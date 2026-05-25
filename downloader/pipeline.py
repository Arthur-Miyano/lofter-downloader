"""统一下载管道。

DownloadPipeline 提供链接收集、单篇下载两大功能。

链接收集策略（经实际验证）：
  博客全部文章 — DWR ArchiveBean.getArchivePostByTime（主）
                 备选 ?page=N SSR（仅旧模板有效）
  合集 — SPA 页面：等待渲染 + 滚动加载 + DOM 提取
  收藏 — DWR BlogBean.queryLikePosts（主）
         备选收藏页 SPA 提取

window.__INITIAL_STATE__ 在实际 LOFTER 页面中不存在，不可用。
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import logging
import re
import time
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
    "https://www.lofter.com/like",
]

# DWR API 端点
DWR_FAV_URL = (
    "https://www.lofter.com/dwr/call/plaincall/BlogBean.queryLikePosts.dwr"
)


class DownloadPipeline:
    """统一下载管道。"""

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

    async def collect_blog_links(self, user_id: str) -> tuple[list[str], str]:
        """收集作者全部文章链接。

        返回 (links, blog_name)。
        主策略：DWR ArchiveBean API（经 lofterSpider 验证）。
        备选：?page=N SSR 分页（仅旧模板有效）。
        """
        domain = await self._resolve_domain(user_id)
        if not domain:
            raise LoginRequiredError(f"无法解析用户 ID: {user_id}")
        logger.info("博客域名: %s (user_id=%s)", domain, user_id)

        blog_url = f"https://{domain}.lofter.com"

        # 主策略: DWR ArchiveBean API
        author_id, blog_name = await self._get_author_info(domain)
        if author_id:
            logger.info("作者: %s (id=%s)", blog_name or domain, author_id)
            links = await self._collect_blog_via_dwr(author_id, blog_url)
            if links:
                logger.info("DWR 获取到 %d 篇文章", len(links))
                return links, blog_name or domain

        # 备选: ?page=N SSR 分页（仅旧模板有效）
        logger.info("DWR 未获取到文章，尝试 ?page=N SSR 分页")
        links = await self._paginate(blog_url)
        return links, domain

    async def collect_collection_links(self, url: str) -> list[str]:
        """收集合集全部文章链接（SPA 页面）。"""
        return await self._paginate(url, use_spa=True)

    async def collect_favorites_links(self) -> list[str]:
        """收集收藏全部文章链接。

        优先 DWR API（已验证有效），URL 探测失败则降级为 SPA。
        """
        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
        context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            # 先获取用户 ID
            await _navigate(page, "https://www.lofter.com/")
            if "/front/login" in page.url:
                raise LoginRequiredError("登录会话已过期，请重新登录")

            user_id = await page.evaluate("""() => {
                const u = window.userSignedIn;
                if (u && u.blogId) return String(u.blogId);
                return '';
            }""")

            if user_id:
                logger.info("通过 DWR API 获取收藏，userId=%s", user_id)
                links = await self._call_dwr_favorites(page, user_id)
                if links:
                    return links

            # 备选：SPA 收藏页
            logger.info("DWR 收藏失败，尝试收藏页 SPA 提取")
            fav_url = await self._resolve_favorites_url(context)
            if fav_url:
                return await self._paginate(fav_url, use_spa=True)

            raise LoginRequiredError(
                "无法获取收藏列表，请确认已登录并有收藏内容"
            )
        finally:
            await context.close()

    # ------------------------------------------------------------------
    # 域名解析
    # ------------------------------------------------------------------

    async def _resolve_domain(self, user_id: str) -> str | None:
        """根据用户 ID 解析博客域名。

        策略：
        1. 如果 user_id 不是纯数字，直接用 {user_id}.lofter.com
        2. 导航 blog/{id} → URL 重定向
        3. 直接用 user_id 作为域名
        """
        # 非纯数字 → 直接作为博客名
        if not user_id.isdigit():
            logger.info("用户输入非数字，直接作为博客名: %s", user_id)
            return user_id

        url = f"https://www.lofter.com/blog/{user_id}"
        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
        context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            await _navigate(page, url)
            final_url = page.url
            logger.info("_resolve_domain: %s → %s", url, final_url)

            m = re.match(r"https?://([^.]+)\.lofter\.com", final_url)
            if m and m.group(1) not in ("www",):
                return m.group(1)

            # 最终兜底：直接用数字 ID 作为域名
            return user_id
        finally:
            await context.close()

    # ------------------------------------------------------------------
    # 作者信息获取
    # ------------------------------------------------------------------

    async def _get_author_info(self, domain: str) -> tuple[str | None, str | None]:
        """从 /view 页面获取 author_id 和 blog_name。

        返回 (author_id, blog_name)。author_id 是纯数字 ID。
        """
        url = f"https://{domain}.lofter.com/view"
        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
        context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            await _navigate(page, url)

            # 提取 author_id（从 iframe src）
            author_id = await page.evaluate("""() => {
                const iframe = document.querySelector('iframe[id=\"control_frame\"]');
                if (!iframe) return '';
                const m = iframe.src.match(/blogId=(\\d+)/);
                return m ? m[1] : '';
            }""")

            # 提取 blog_name（从 title）
            blog_name = await page.evaluate("""() => {
                const t = document.title;
                if (!t) return '';
                const m = t.match(/^归档\\s*-\\s*(.+)$/);
                return m ? m[1] : '';
            }""")
            if not blog_name:
                # 从 h1/a 提取
                blog_name = await page.evaluate("""() => {
                    const el = document.querySelector('h1 a');
                    return el ? el.textContent.trim() : '';
                }""")

            logger.info("作者信息: id=%s, name=%s", author_id, blog_name)
            return author_id or None, blog_name or None
        except Exception as exc:
            logger.warning("获取作者信息失败: %s", exc)
            return None, None
        finally:
            await context.close()

    # ------------------------------------------------------------------
    # DWR: 博客文章列表
    # ------------------------------------------------------------------

    async def _collect_blog_via_dwr(
        self, author_id: str, blog_url: str,
    ) -> list[str]:
        """通过 DWR ArchiveBean API 分页获取全部文章链接。

        分页使用时间戳游标：每批返回 50 篇，用最后一条的时间戳
        作为下一批请求的 c0-param2。
        """
        all_links: list[str] = []
        timestamp = str(round(time.time() * 1000))
        batch_size = 50

        dwr_url = f"{blog_url}/dwr/call/plaincall/ArchiveBean.getArchivePostByTime.dwr"

        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
        context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            await _navigate(page, blog_url)

            while True:
                body = _build_dwr_body(
                    "ArchiveBean", "getArchivePostByTime",
                    ("boolean:false", f"number:{author_id}",
                     f"number:{timestamp}", f"number:{batch_size}",
                     "boolean:false"),
                )
                raw = await page.evaluate(
                    f"""async () => {{
                        const r = await fetch('{dwr_url}', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'text/plain'}},
                            body: {_json.dumps(body)},
                        }});
                        return await r.text();
                    }}"""
                )

                # DWR 响应为分号分隔的单行格式:
                #   var s0={};...s0.blogId=123;s0.time=456;s0.values=s10;...
                #   s10.permalink="abc";s10.title="...";
                permalinks = re.findall(r'permalink="([^"]+)"', raw)
                if not permalinks:
                    break

                new_in_batch = 0
                for pl in permalinks:
                    link = f"{blog_url}/post/{pl}"
                    if link not in all_links:
                        all_links.append(link)
                        new_in_batch += 1

                # 用最后一条的时间戳作为下一页游标
                last_timestamp = _extract_last_timestamp(raw, new_in_batch)

                logger.debug(
                    "DWR archive [ts=%s]: +%d 篇 (累计 %d)",
                    timestamp[:10], new_in_batch, len(all_links),
                )

                if new_in_batch < batch_size:
                    break

                timestamp = last_timestamp or timestamp
                await asyncio.sleep(REQUEST_INTERVAL)

            await page.close()
        finally:
            await context.close()

        return all_links

    # ------------------------------------------------------------------
    # DWR: 收藏
    # ------------------------------------------------------------------

    async def _call_dwr_favorites(
        self, page: Any, user_id: str, batch_size: int = 100,
    ) -> list[str]:
        """调用 DWR API 分页获取全部收藏文章链接。"""
        all_links: list[str] = []
        got_num = 0

        while True:
            body = _build_dwr_body(
                "BlogBean", "queryLikePosts",
                (f"number:{user_id}", f"number:{batch_size}",
                 f"number:{got_num}", "string:"),
            )
            raw = await page.evaluate(
                f"""async () => {{
                    const r = await fetch('{DWR_FAV_URL}', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'text/plain'}},
                        body: {_json.dumps(body)},
                    }});
                    return await r.text();
                }}"""
            )

            found = set(re.findall(
                r'blogPageUrl="(https?://[^"]+/post/[^"]+)"', raw,
            ))
            if not found:
                break
            new_links = [u for u in found if u not in all_links]
            if not new_links:
                break
            all_links.extend(new_links)
            got_num += batch_size
            logger.debug(
                "DWR 收藏 [offset=%d]: +%d 篇 (累计 %d)",
                got_num, len(new_links), len(all_links),
            )
            await asyncio.sleep(REQUEST_INTERVAL)

        logger.info("DWR 收藏完成，共 %d 篇", len(all_links))
        return all_links

    # ------------------------------------------------------------------
    # 收藏 URL 探测
    # ------------------------------------------------------------------

    async def _resolve_favorites_url(self, context: Any) -> str | None:
        """探测可用的收藏页 URL。"""
        for url in FAVORITE_URLS:
            try:
                page = await context.new_page()
                await _navigate(page, url)
                links = await _extract_links_spa(page, url)
                await page.close()
                if links:
                    logger.info("收藏页 URL 可用: %s (links=%d)", url, len(links))
                    return url
            except Exception as exc:
                logger.debug("收藏 URL %s 失败: %s", url, exc)
        logger.warning("所有收藏页 URL 均探测失败")
        return None

    # ------------------------------------------------------------------
    # 分页遍历（SSR + SPA）
    # ------------------------------------------------------------------

    async def _paginate(
        self, base_url: str, use_spa: bool = False,
    ) -> list[str]:
        """通用分页：SSR 用 ?page=N，SPA 用滚动加载。"""
        all_links: list[str] = []
        page_num = 1
        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None

        context = await self._browser.new_context(storage_state=storage)
        try:
            while True:
                if use_spa and page_num > 1:
                    # SPA 页面只有一页，滚动加载足够了
                    break

                url = _paginated_url(base_url, page_num)
                logger.info("分页 [%d]: %s", page_num, url)

                page = await context.new_page()
                try:
                    await _navigate(page, url)

                    if use_spa:
                        links = await _extract_links_spa(page, base_url)
                    else:
                        await _wait_for_links(page)
                        html = await page.content()
                        links = _extract_links(html, base_url)
                finally:
                    await page.close()

                new_links: list[str] = []
                for link in links:
                    if link not in all_links and link not in new_links:
                        new_links.append(link)

                if not new_links:
                    if use_spa:
                        logger.info("SPA 页面无新链接，停止")
                    else:
                        logger.info("第 %d 页无新链接，停止分页", page_num)
                    break

                all_links.extend(new_links)
                logger.info(
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
# 辅助函数
# ------------------------------------------------------------------


def _extract_last_timestamp(raw: str, batch_count: int) -> str | None:
    """从 DWR 响应中提取最后一条博客的时间戳作为下一页游标。

    DWR 响应为分号分隔格式：
        s0.blogId=...;s0.time=12345;...s1.time=67890;...

    取第 (batch_count - 1) 条主记录的时间戳。
    """
    if batch_count <= 0:
        return None
    last_idx = batch_count - 1
    m = re.search(rf"s{last_idx}\.time=(\d+);", raw)
    if m:
        return m.group(1)
    # 降级：取任意 time 值
    times = re.findall(r"s\d+\.time=(\d+);", raw)
    return times[-1] if times else None


def _build_dwr_body(
    script_name: str, method_name: str, params: tuple[str, ...],
) -> str:
    """构建 DWR POST 请求体。"""
    lines = [
        "callCount=1",
        "scriptSessionId=${scriptSessionId}187",
        "httpSessionId=",
        f"c0-scriptName={script_name}",
        f"c0-methodName={method_name}",
        "c0-id=0",
    ]
    for i, p in enumerate(params):
        lines.append(f"c0-param{i}={p}")
    lines.append("batchId=472352")
    return "\n".join(lines)


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
    """等待页面中的文章链接渲染完成（SSR 页面用）。"""
    base = "a[href*='/post/'], .post_content, article, [class*='content']"
    with contextlib.suppress(Exception):
        await page.wait_for_selector(base, timeout=15000)
    await asyncio.sleep(2)


async def _extract_links_spa(page: Any, base_url: str) -> list[str]:
    """SPA 页面链接提取：等待渲染 + 滚动加载 + DOM 查询。"""
    await asyncio.sleep(2)

    with contextlib.suppress(Exception):
        await page.wait_for_selector(
            "a[href*='/post/'], article, [class*='content'], [class*='post']",
            timeout=15000,
        )

    # 滚动加载
    all_links: list[str] = []
    for _ in range(5):
        prev_count = len(all_links)
        try:
            dom_links: list[str] = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll("a[href*='/post/']"))
                    .map(a => a.href);
            }""")
            for u in dom_links:
                if u not in all_links:
                    all_links.append(u)
        except Exception:
            pass

        if len(all_links) == prev_count and prev_count > 0:
            break  # 无新链接，停止滚动

        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await asyncio.sleep(1)

    # HTML 兜底
    try:
        html = await page.content()
        for u in _extract_links(html, base_url):
            if u not in all_links:
                all_links.append(u)
    except Exception:
        pass

    return all_links


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
