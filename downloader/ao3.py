"""AO3（Archive of Our Own）下载模块。

无需登录：公开文 + view_adult=true Cookie 绕过成人提示。
提供官方导出（EPUB/PDF/HTML/MOBI/AZW3）与统一解析管道（MD/TXT/PDF/EPUB）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md

from config import MAX_RETRIES, REQUEST_INTERVAL

logger = logging.getLogger(__name__)

AO3_BASE = "https://archiveofourown.org"
AO3_OFFICIAL_FORMATS = {"epub", "pdf", "html", "mobi", "azw3"}
MAX_RETRY_AFTER = 120.0

# 全局限流：模块级共享，保证任意 client 实例、任意协程的请求间隔
_rate_lock: asyncio.Lock | None = None
_last_request_time: float = 0.0


class AO3Error(Exception):
    """AO3 模块自定义异常。"""


def _get_rate_lock() -> asyncio.Lock:
    """惰性创建全局限流锁，避免 import 时绑定不存在的事件循环。"""
    global _rate_lock
    if _rate_lock is None:
        _rate_lock = asyncio.Lock()
    return _rate_lock


def extract_username(url: str) -> str | None:
    """从作者链接提取用户名。

    支持 /users/xxx、/users/xxx/pseuds/yyy、/users/xxx/works 等形式。
    """
    m = re.search(r"/users/([^/?#]+)", url.strip())
    return m.group(1) if m else None


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """计算 429 退避秒数，Retry-After 钳制到 MAX_RETRY_AFTER 上限。"""
    delay = REQUEST_INTERVAL * (attempt + 1)
    retry_after = resp.headers.get("retry-after")
    if retry_after:
        with contextlib.suppress(ValueError):
            delay = float(retry_after)
    return min(delay, MAX_RETRY_AFTER)


class AO3Client:
    """AO3 HTTP 客户端，带全局限流与 429 退避。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """懒初始化 httpx 客户端。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Cookie": "view_adult=true",
                },
            )
        return self._client

    async def close(self) -> None:
        """关闭 httpx 客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        """全局限流：在锁内完成间隔检查与时间戳更新。"""
        global _last_request_time
        async with _get_rate_lock():
            elapsed = time.monotonic() - _last_request_time
            if elapsed < REQUEST_INTERVAL:
                await asyncio.sleep(REQUEST_INTERVAL - elapsed)
            _last_request_time = time.monotonic()

    async def _request(self, url: str, **kwargs) -> httpx.Response:
        """带间隔与 429 退避的请求。"""
        client = await self._get_client()
        for attempt in range(1, MAX_RETRIES + 1):
            await self._throttle()
            resp = await client.get(url, **kwargs)
            if resp.status_code != 429:
                return resp
            if attempt == MAX_RETRIES:
                break
            delay = _retry_delay(resp, attempt)
            logger.warning(
                "AO3 返回 429，等待 %.1fs 后重试 [%d/%d]",
                delay,
                attempt,
                MAX_RETRIES,
            )
            await asyncio.sleep(delay)

        raise AO3Error(f"AO3 请求频繁，重试耗尽: {url}")

    async def get_page(self, url: str) -> BeautifulSoup:
        """获取页面并解析为 BeautifulSoup。"""
        resp = await self._request(url)
        if resp.status_code == 403 and "Shields are up" in resp.text:
            raise AO3Error(
                "AO3 当前处于 shields up（高负载防护）模式，请稍后重试或登录后访问"
            )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    # ------------------------------------------------------------------
    # URL 归一化
    # ------------------------------------------------------------------

    def normalize_work_url(self, url: str) -> str | None:
        """归一化为 https://archiveofourown.org/works/:id。"""
        url = url.strip()
        m = re.search(r"/works/(\d+)", url)
        if m:
            return f"{AO3_BASE}/works/{m.group(1)}"
        return None

    # ------------------------------------------------------------------
    # 清单解析
    # ------------------------------------------------------------------

    async def list_series(self, series_id: str) -> list[dict]:
        """解析系列页面，返回 [{id, title, author, url}]。"""
        url = f"{AO3_BASE}/series/{series_id}"
        soup = await self.get_page(url)
        items = []
        for li in soup.select("ul.series li.work"):
            link = li.select_one("h4.heading a[href^='/works/']")
            if not link:
                continue
            work_id = _extract_work_id(link.get("href", ""))
            if not work_id:
                continue
            title = link.get_text(strip=True)
            author_el = li.select_one("a[rel='author']")
            author = author_el.get_text(strip=True) if author_el else ""
            items.append(
                {
                    "id": work_id,
                    "title": title,
                    "author": author,
                    "url": f"{AO3_BASE}/works/{work_id}",
                }
            )
        return items

    async def list_author(self, username: str) -> list[dict]:
        """解析作者全部作品，支持分页。"""
        all_items: list[dict] = []
        page = 1
        while True:
            url = f"{AO3_BASE}/users/{username}/works?page={page}"
            soup = await self.get_page(url)
            items = self._parse_work_list(soup)
            if not items:
                break
            all_items.extend(items)
            # 是否有下一页（间隔由 _request 全局限流保证）
            next_link = soup.select_one("ol.pagination li.next a")
            if not next_link:
                break
            page += 1
        return all_items

    async def list_batch(self, urls: list[str]) -> list[dict]:
        """批量链接：逐行归一化并补充标题（可选）。"""
        items = []
        for raw in urls:
            normalized = self.normalize_work_url(raw)
            if not normalized:
                continue
            work_id = _extract_work_id(normalized)
            if not work_id:
                continue
            items.append(
                {
                    "id": work_id,
                    "title": "",
                    "author": "",
                    "url": normalized,
                }
            )
        # 补充标题（每篇取一次，间隔由 _request 全局限流保证）
        for item in items:
            try:
                info = await self.get_work_info(item["id"])
                item["title"] = info.get("title", "")
                item["author"] = info.get("author", "")
            except Exception as exc:
                logger.debug("批量模式获取标题失败 %s: %s", item["url"], exc)
        return items

    def _parse_work_list(self, soup: BeautifulSoup) -> list[dict]:
        """通用作品列表解析。"""
        items = []
        for li in soup.select("ol.work.index li.work"):
            link = li.select_one("h4.heading a[href^='/works/']")
            if not link:
                continue
            work_id = _extract_work_id(link.get("href", ""))
            if not work_id:
                continue
            title = link.get_text(strip=True)
            author_el = li.select_one("a[rel='author']")
            author = author_el.get_text(strip=True) if author_el else ""
            items.append(
                {
                    "id": work_id,
                    "title": title,
                    "author": author,
                    "url": f"{AO3_BASE}/works/{work_id}",
                }
            )
        return items

    async def get_work_info(self, work_id: str) -> dict:
        """获取单篇作品元信息。"""
        url = f"{AO3_BASE}/works/{work_id}"
        soup = await self.get_page(url)
        return _parse_work_meta(soup)

    # ------------------------------------------------------------------
    # 官方导出
    # ------------------------------------------------------------------

    async def get_official_download_url(
        self,
        work_id: str,
        fmt: str,
        page_html: str | None = None,
    ) -> str:
        """从作品页解析官方导出链接。

        传入已抓取的 page_html 时跳过重复请求。
        """
        fmt = fmt.lower()
        if fmt not in AO3_OFFICIAL_FORMATS:
            raise AO3Error(f"不支持的 AO3 官方格式: {fmt}")
        if page_html is not None:
            soup = BeautifulSoup(page_html, "lxml")
        else:
            soup = await self.get_page(f"{AO3_BASE}/works/{work_id}")
        link = soup.select_one(f".download a[href*='.{fmt}']")
        if not link:
            raise AO3Error(f"未找到官方 {fmt} 导出链接")
        href = link.get("href", "")
        return urljoin(AO3_BASE, href)

    async def download_official(
        self,
        work_id: str,
        fmt: str,
        dest_path: Path,
        page_html: str | None = None,
    ) -> None:
        """官方导出流式下载到指定路径，带限流与 429 退避。

        传入已抓取的 page_html 时跳过重复请求作品页。
        """
        download_url = await self.get_official_download_url(
            work_id, fmt, page_html=page_html
        )
        client = await self._get_client()
        for attempt in range(1, MAX_RETRIES + 1):
            await self._throttle()
            async with client.stream(
                "GET", download_url, follow_redirects=True
            ) as resp:
                if resp.status_code == 429:
                    if attempt == MAX_RETRIES:
                        break
                    delay = _retry_delay(resp, attempt)
                    logger.warning(
                        "AO3 下载返回 429，等待 %.1fs 后重试 [%d/%d]",
                        delay,
                        attempt,
                        MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
                logger.info("AO3 官方 %s 已下载: %s", fmt, dest_path)
                return

        raise AO3Error(f"AO3 请求频繁，重试耗尽: {download_url}")

    # ------------------------------------------------------------------
    # 解析管道
    # ------------------------------------------------------------------

    async def parse_work(self, work_id: str) -> dict:
        """解析整篇作品为统一 post_dict。"""
        url = f"{AO3_BASE}/works/{work_id}?view_full_work=true"
        soup = await self.get_page(url)
        meta = _parse_work_meta(soup)

        # 排除 preface/notes 容器内的 .userstuff（章节 Notes、作品 summary）
        chapters = [
            ch
            for ch in soup.select("#chapters .userstuff")
            if not _in_preface_or_notes(ch)
        ]
        if not chapters:
            chapters = [
                ch
                for ch in soup.select(".userstuff")
                if not _in_preface_or_notes(ch)
            ]

        # 图片 src 统一改写为绝对 URL，
        # 保证 content_html、content_markdown、image_urls 三处一致
        for ch in chapters:
            for img in ch.find_all("img"):
                src = img.get("src", "")
                if src:
                    img["src"] = urljoin(AO3_BASE, src)

        # 合并章节 HTML
        content_html = "\n".join(str(ch) for ch in chapters)
        content_markdown = md(content_html, heading_style="ATX") if content_html else ""

        return {
            "url": f"{AO3_BASE}/works/{work_id}",
            "title": meta.get("title", f"work_{work_id}"),
            "author": meta.get("author", "未知作者"),
            "publish_date": meta.get("publish_date", ""),
            "content_html": content_html,
            "content_markdown": content_markdown,
            "image_urls": _extract_images(content_html),
        }


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

_PREFACE_NOTES_RE = re.compile(r"\b(preface|notes)\b")


def _in_preface_or_notes(tag: Tag) -> bool:
    """判断节点是否位于 preface/notes 容器（章节 Notes、作品 summary）内。"""
    return tag.find_parent(class_=_PREFACE_NOTES_RE) is not None


def _extract_work_id(href: str) -> str | None:
    """从 /works/:id 链接提取 ID。"""
    m = re.search(r"/works/(\d+)", href)
    return m.group(1) if m else None


def _parse_work_meta(soup: BeautifulSoup) -> dict:
    """解析作品页元信息：标题、作者、日期。"""
    title_el = soup.select_one("h2.title.heading")
    title = title_el.get_text(strip=True) if title_el else ""

    author_el = soup.select_one("a[rel='author']")
    author = author_el.get_text(strip=True) if author_el else "未知作者"

    date = ""
    for el in soup.select(".stats .published, .preface .datetime"):
        text = el.get_text(strip=True)
        m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if m:
            date = m.group(1)
            break

    return {"title": title, "author": author, "publish_date": date}


def _extract_images(content_html: str) -> list[str]:
    """提取正文中的图片 URL（src 已在 parse_work 中改写为绝对 URL）。"""
    soup = BeautifulSoup(content_html, "lxml")
    urls = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src:
            urls.append(src)
    return urls
