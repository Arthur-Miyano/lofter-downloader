"""统一下载管道。

单一 DownloadPipeline 类替代原先 4 个独立下载器。
提供链接收集、单篇下载、分页遍历等通用方法。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import MAX_RETRIES, REQUEST_INTERVAL, SESSION_PATH
from downloader.exceptions import LoginRequiredError, NetworkError
from downloader.parser import extract_post

logger = logging.getLogger(__name__)

# 收藏页候选 URL
FAVORITE_URLS = [
    "https://www.lofter.com/fav/blog",
    "https://www.lofter.com/user/fav/blog",
    "https://www.lofter.com/my/fav",
]


class DownloadPipeline:
    """统一下载管道。

    所有操作接收 browser 参数（BrowserManager），
    由调用方通过 submit/submit_async 调度到浏览器线程。
    """

    def __init__(self, browser) -> None:  # noqa: ANN001
        self._browser = browser

    # ------------------------------------------------------------------
    # 单篇文章
    # ------------------------------------------------------------------

    async def run_post(self, url: str) -> list[dict]:
        """下载单篇文章，返回 [post_dict] 或空列表。"""
        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
        context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            await _navigate(page, url)
            result = await extract_post(page, url)
            return [result] if result else []
        finally:
            await context.close()

    # ------------------------------------------------------------------
    # 链接收集
    # ------------------------------------------------------------------

    async def collect_blog_links(self, user_id: str) -> list[str]:
        """收集作者全部文章链接。"""
        domain = await self._resolve_domain(user_id)
        if not domain:
            raise LoginRequiredError(f"无法解析用户 ID: {user_id}")
        logger.info("博客域名: %s (user_id=%s)", domain, user_id)
        return await self._paginate(f"https://{domain}.lofter.com")

    async def collect_collection_links(self, url: str) -> list[str]:
        """收集合集全部文章链接。"""
        return await self._paginate(url)

    async def collect_favorites_links(self) -> list[str]:
        """收集收藏全部文章链接。"""
        fav_url = await self._resolve_favorites_url()
        if not fav_url:
            raise LoginRequiredError("无法找到收藏页面 URL，请确认已登录并有收藏内容")
        return await self._paginate(fav_url)

    # ------------------------------------------------------------------
    # 分页遍历
    # ------------------------------------------------------------------

    async def _paginate(self, base_url: str) -> list[str]:
        """通用分页：从 page=1 开始，无新链接时停止。"""
        all_links: list[str] = []
        page_num = 1
        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None

        context = await self._browser.new_context(storage_state=storage)
        try:
            while True:
                url = _paginated_url(base_url, page_num)
                logger.debug("分页 [%d]: %s", page_num, url)

                page = await context.new_page()
                try:
                    await _navigate(page, url)
                    await _wait_for_links(page)
                    html = await page.content()
                finally:
                    await page.close()

                links = _extract_links(html, base_url)
                new_links = [link for link in links if link not in all_links]

                if not new_links:
                    break

                all_links.extend(new_links)
                logger.debug(
                    "第 %d 页找到 %d 个新链接（累计 %d）",
                    page_num, len(new_links), len(all_links),
                )
                page_num += 1

                await asyncio.sleep(REQUEST_INTERVAL)
        finally:
            await context.close()

        logger.info("分页完成，共收集 %d 个链接", len(all_links))
        return all_links

    # ------------------------------------------------------------------
    # 域名解析
    # ------------------------------------------------------------------

    async def _resolve_domain(self, user_id: str) -> str | None:
        """根据用户 ID 解析博客域名。"""
        url = f"https://www.lofter.com/blog/{user_id}"
        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
        context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            await _navigate(page, url)
            html = await page.content()
            match = re.search(r'"blogName"\s*:\s*"([^"]+)"', html)
            if match:
                return match.group(1)
            match = re.search(r"window\.globalData\s*=\s*({.*?});", html, re.DOTALL)
            if match:
                import json
                data = json.loads(match.group(1))
                return data.get("blogName")
            return None
        finally:
            await context.close()

    async def _resolve_favorites_url(self) -> str | None:
        """探测可用的收藏页 URL。"""
        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
        context = await self._browser.new_context(storage_state=storage)
        try:
            for url in FAVORITE_URLS:
                try:
                    page = await context.new_page()
                    await _navigate(page, url)
                    html = await page.content()
                    await page.close()
                    if "/post/" in html:
                        logger.info("收藏页 URL: %s", url)
                        return url
                except Exception as exc:
                    logger.debug("收藏 URL %s 失败: %s", url, exc)
            # 全部探测失败
            logger.warning("所有收藏页 URL 均探测失败")
            return None
        finally:
            await context.close()


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


async def _navigate(page: Any, url: str) -> None:
    """导航到 URL，等待内容渲染。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await page.goto(url, wait_until="load", timeout=30000)
            return
        except Exception as exc:
            if attempt == MAX_RETRIES:
                raise NetworkError(
                    f"页面导航失败 [{MAX_RETRIES}/{MAX_RETRIES}]: {url}"
                ) from exc
            logger.warning("导航失败 [%d/%d]: %s - %s", attempt, MAX_RETRIES, url, exc)
            await asyncio.sleep(REQUEST_INTERVAL * attempt)


async def _wait_for_links(page: Any) -> None:
    """等待页面中的文章链接渲染完成。"""
    with contextlib.suppress(Exception):
        await page.wait_for_selector(
            "a[href*='/post/'], .post_content, article, [class*='content']",
            timeout=15000,
        )
    await asyncio.sleep(2)  # React hydration 缓冲


def _extract_links(html: str, base_url: str) -> list[str]:
    """从 HTML 中提取文章链接，去重保序。"""
    soup = BeautifulSoup(html, "lxml")
    links = []
    for tag in soup.select("a[href*='/post/']"):
        href = tag.get("href", "")
        if href:
            full = urljoin(base_url, href)
            if full not in links:
                links.append(full)
    return links


def _paginated_url(base_url: str, page_num: int) -> str:
    """构造分页 URL。"""
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}page={page_num}"
