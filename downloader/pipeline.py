"""统一下载管道。

DownloadPipeline 提供链接收集、单篇下载三大功能。

链接收集策略（经实际验证）：
  博客全部文章 — DWR ArchiveBean.getArchivePostByTime（主）
                 备选 ?page=N SSR（仅旧模板有效）
  喜欢 — DWR BlogBean.queryLikePosts

window.__INITIAL_STATE__ 在实际 LOFTER 页面中不存在，不可用。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import MAX_RETRIES, REQUEST_INTERVAL, SESSION_PATH
from downloader.exceptions import LoginRequiredError, NetworkError
from downloader.parser import extract_post

logger = logging.getLogger(__name__)

# DWR API 端点
DWR_LIKES_URL = "https://www.lofter.com/dwr/call/plaincall/BlogBean.queryLikePosts.dwr"


class DownloadPipeline:
    """统一下载管道。"""

    def __init__(self, browser) -> None:  # noqa: ANN001
        self._browser = browser

    # ------------------------------------------------------------------
    # 单篇文章
    # ------------------------------------------------------------------

    async def run_post(self, url: str, context=None) -> list[dict]:  # noqa: ANN001
        """下载单篇文章，返回 [post_dict] 或空列表。

        context 可选，传入时复用现有 BrowserContext（不自动关闭）。
        """
        own_context = context is None
        if own_context:
            storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
            context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            try:
                await _navigate(page, url)
                await _wait_for_article_ready(page)
                result = await extract_post(page, url)
                return [result] if result else []
            finally:
                await page.close()
        finally:
            if own_context:
                await context.close()

    # ------------------------------------------------------------------
    # 链接收集
    # ------------------------------------------------------------------

    async def collect_blog_items(
        self,
        user_id: str,
        context=None,  # noqa: ANN001
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[list[dict], str]:
        """收集作者全部文章，返回 (items, blog_name)。

        每个 item 包含 {url, title}。
        主策略：DWR ArchiveBean API（同时提取 permalink + title）。
        备选：?page=N SSR 分页（仅旧模板有效，标题取链接文本）。

        context 可选，传入时复用现有 BrowserContext（不自动关闭）。
        should_cancel 可选，返回 True 时抛出 CancelledError 中断收集。
        """
        own_context = context is None
        if own_context:
            storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
            context = await self._browser.new_context(storage_state=storage)
        try:
            domain = await self._resolve_domain(user_id, context)
            if not domain:
                raise LoginRequiredError(f"无法解析用户 ID: {user_id}")
            logger.info("博客域名: %s (user_id=%s)", domain, user_id)

            blog_url = f"https://{domain}.lofter.com"

            # 主策略: DWR ArchiveBean API
            author_id, blog_name = await self._get_author_info(domain, context)
            if author_id:
                logger.info("作者: %s (id=%s)", blog_name or domain, author_id)
                try:
                    items = await self._collect_blog_items_via_dwr(
                        author_id,
                        blog_url,
                        context,
                        should_cancel=should_cancel,
                    )
                    if items:
                        logger.info("DWR 获取到 %d 篇文章", len(items))
                        return items, blog_name or domain
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("DWR 链接收集失败，回退到 SSR 分页: %s", exc)

            # 备选: ?page=N SSR 分页（仅旧模板有效）
            logger.info("尝试 ?page=N SSR 分页")
            items = await self._paginate_items(
                blog_url, context=context, should_cancel=should_cancel
            )
            return items, domain
        finally:
            if own_context:
                await context.close()

    async def collect_blog_links(
        self,
        user_id: str,
        context=None,  # noqa: ANN001
    ) -> tuple[list[str], str]:  # noqa: E501
        """收集作者全部文章链接（兼容旧接口）。

        返回 (links, blog_name)。
        """
        items, blog_name = await self.collect_blog_items(user_id, context=context)
        return [item["url"] for item in items], blog_name

    async def collect_likes_links(self, context=None) -> list[str]:  # noqa: ANN001
        """收集喜欢全部文章链接。

        通过 DWR BlogBean.queryLikePosts API 分页获取。

        context 可选，传入时复用现有 BrowserContext（不自动关闭）。
        """
        own_context = context is None
        if own_context:
            storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
            context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            try:
                await _navigate(page, "https://www.lofter.com/")
                if "/front/login" in page.url:
                    raise LoginRequiredError("登录会话已过期，请重新登录")

                user_id = await page.evaluate("""() => {
                    const u = window.userSignedIn;
                    if (u && u.blogId) return String(u.blogId);
                    return '';
                }""")

                if not user_id:
                    raise LoginRequiredError("无法获取用户 ID，请确认已登录")

                logger.info("通过 DWR API 获取喜欢，userId=%s", user_id)
                links = await self._call_dwr_likes(page, user_id)
                if not links:
                    raise LoginRequiredError(
                        "未找到任何喜欢文章，请确认已登录并有喜欢内容"
                    )
                return links
            finally:
                await page.close()
        finally:
            if own_context:
                await context.close()

    # ------------------------------------------------------------------
    # 域名解析
    # ------------------------------------------------------------------

    async def _resolve_domain(self, user_id: str, context=None) -> str | None:  # noqa: ANN001
        """根据用户 ID 解析博客域名。"""
        if not user_id.isdigit():
            logger.info("用户输入非数字，直接作为博客名: %s", user_id)
            return user_id

        url = f"https://www.lofter.com/blog/{user_id}"
        own_context = context is None
        if own_context:
            storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
            context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            try:
                await _navigate(page, url)
                final_url = page.url
                logger.info("_resolve_domain: %s → %s", url, final_url)

                m = re.match(r"https?://([^.]+)\.lofter\.com", final_url)
                if m and m.group(1) not in ("www",):
                    return m.group(1)
                return user_id
            finally:
                await page.close()
        finally:
            if own_context:
                await context.close()

    # ------------------------------------------------------------------
    # 作者信息获取
    # ------------------------------------------------------------------

    async def _get_author_info(
        self,
        domain: str,
        context=None,  # noqa: ANN001
    ) -> tuple[str | None, str | None]:  # noqa: E501
        """从 /view 页面获取 author_id 和 blog_name。"""
        url = f"https://{domain}.lofter.com/view"
        own_context = context is None
        if own_context:
            storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
            context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            try:
                await _navigate(page, url)

                author_id = await page.evaluate("""() => {
                    const iframe = document.querySelector('iframe#control_frame');
                    if (!iframe) return '';
                    const m = iframe.src.match(/blogId=(\\d+)/);
                    return m ? m[1] : '';
                }""")

                blog_name = await page.evaluate("""() => {
                    const t = document.title;
                    if (!t) return '';
                    const m = t.match(/^归档\\s*-\\s*(.+)$/);
                    return m ? m[1] : '';
                }""")
                if not blog_name:
                    blog_name = await page.evaluate("""() => {
                        const el = document.querySelector('h1 a');
                        return el ? el.textContent.trim() : '';
                    }""")

                logger.info("作者信息: id=%s, name=%s", author_id, blog_name)
                return author_id or None, blog_name or None
            finally:
                await page.close()
        except Exception as exc:
            logger.warning("获取作者信息失败: %s", exc)
            return None, None
        finally:
            if own_context:
                await context.close()

    # ------------------------------------------------------------------
    # DWR: 博客文章列表
    # ------------------------------------------------------------------

    async def _collect_blog_items_via_dwr(
        self,
        author_id: str,
        blog_url: str,
        context=None,  # noqa: ANN001
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[dict]:
        """通过 DWR ArchiveBean API 分页获取全部文章（含标题）。

        should_cancel 可选，每个分页前检查，返回 True 时抛出 CancelledError。
        """
        all_items: list[dict] = []
        timestamp = str(round(time.time() * 1000))
        batch_size = 50
        max_pages = 200  # 防空转上限

        dwr_url = f"{blog_url}/dwr/call/plaincall/ArchiveBean.getArchivePostByTime.dwr"

        own_context = context is None
        if own_context:
            storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
            context = await self._browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            try:
                await _navigate(page, blog_url)

                for _ in range(max_pages):
                    _raise_if_cancelled(should_cancel)
                    body = _build_dwr_body(
                        "ArchiveBean",
                        "getArchivePostByTime",
                        (
                            "boolean:false",
                            f"number:{author_id}",
                            f"number:{timestamp}",
                            f"number:{batch_size}",
                            "boolean:false",
                        ),
                    )
                    raw = await page.evaluate(
                        """
                        async ([dwrUrl, body]) => {
                            const r = await fetch(dwrUrl, {
                                method: 'POST',
                                headers: {'Content-Type': 'text/plain'},
                                body: body,
                            });
                            return await r.text();
                        }
                    """,
                        [dwr_url, body],
                    )

                    # 按 s<N>. 记录分组解析，permalink 与 title 组内配对
                    records = [r for r in _parse_dwr_records(raw) if r.get("permalink")]
                    batch_count = len(records)
                    if batch_count == 0:
                        break

                    new_in_batch = 0
                    for rec in records:
                        link = f"{blog_url}/post/{rec['permalink']}"
                        title = next(
                            (rec[f] for f in _DWR_TITLE_FIELDS if rec.get(f)),
                            "",
                        )
                        if not any(item["url"] == link for item in all_items):
                            all_items.append({"url": link, "title": title})
                            new_in_batch += 1

                    # 游标按本批原始记录数推进（去重不影响分页位置）
                    last_timestamp = records[-1].get("time") or _extract_last_timestamp(
                        raw, batch_count
                    )

                    logger.debug(
                        "DWR archive [ts=%s]: +%d 篇 (累计 %d)",
                        timestamp[:10],
                        new_in_batch,
                        len(all_items),
                    )

                    if not last_timestamp or last_timestamp == timestamp:
                        # 游标无法推进，继续只会空转
                        break

                    timestamp = last_timestamp
                    await asyncio.sleep(REQUEST_INTERVAL)
            finally:
                await page.close()
        finally:
            if own_context:
                await context.close()

        return all_items

    # ------------------------------------------------------------------
    # DWR: 喜欢
    # ------------------------------------------------------------------

    async def _call_dwr_likes(
        self,
        page: Any,
        user_id: str,
        batch_size: int = 100,
    ) -> list[str]:
        """调用 DWR API 分页获取全部喜欢文章链接。"""
        all_links: list[str] = []
        got_num = 0

        while True:
            body = _build_dwr_body(
                "BlogBean",
                "queryLikePosts",
                (
                    f"number:{user_id}",
                    f"number:{batch_size}",
                    f"number:{got_num}",
                    "string:",
                ),
            )
            raw = await page.evaluate(
                """
                async ([dwrUrl, body]) => {
                    const r = await fetch(dwrUrl, {
                        method: 'POST',
                        headers: {'Content-Type': 'text/plain'},
                        body: body,
                    });
                    return await r.text();
                }
            """,
                [DWR_LIKES_URL, body],
            )

            found = set(
                re.findall(
                    r'blogPageUrl="(https?://[^"]+/post/[^"]+)"',
                    raw,
                )
            )
            if not found:
                break
            new_links = [u for u in found if u not in all_links]
            if not new_links:
                break
            all_links.extend(new_links)
            got_num += batch_size
            logger.debug(
                "DWR 喜欢 [offset=%d]: +%d 篇 (累计 %d)",
                got_num,
                len(new_links),
                len(all_links),
            )
            await asyncio.sleep(REQUEST_INTERVAL)

        logger.info("DWR 喜欢完成，共 %d 篇", len(all_links))
        return all_links

    # ------------------------------------------------------------------
    # 分页遍历（SSR）
    # ------------------------------------------------------------------

    async def _paginate_items(
        self,
        base_url: str,
        context=None,  # noqa: ANN001
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[dict]:
        """SSR ?page=N 分页（返回 items: {url, title}）。

        should_cancel 可选，每个分页前检查，返回 True 时抛出 CancelledError。
        """
        all_items: list[dict] = []
        page_num = 1

        own_context = context is None
        if own_context:
            storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
            context = await self._browser.new_context(storage_state=storage)
        try:
            while True:
                _raise_if_cancelled(should_cancel)

                url = _paginated_url(base_url, page_num)
                logger.info("分页 [%d]: %s", page_num, url)

                page = await context.new_page()
                try:
                    await _navigate(page, url)
                    await _wait_for_links(page)
                    html = await page.content()
                    items = _extract_items_from_html(html, base_url)
                finally:
                    await page.close()

                new_items: list[dict] = []
                for item in items:
                    if item["url"] not in {i["url"] for i in all_items}:
                        new_items.append(item)

                if not new_items:
                    logger.info("第 %d 页无新链接，停止分页", page_num)
                    break

                all_items.extend(new_items)
                logger.info(
                    "第 %d 页找到 %d 个新链接（累计 %d）",
                    page_num,
                    len(new_items),
                    len(all_items),
                )
                page_num += 1
                await asyncio.sleep(REQUEST_INTERVAL)
        finally:
            if own_context:
                await context.close()

        logger.info("分页完成，共收集 %d 个链接", len(all_items))
        return all_items


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


# DWR 记录字段正则：s0.permalink="..";s0.time=123;...
# 字符串值支持 JS 转义（\"、\\ 等），避免遇转义引号截断
_DWR_FIELD_RE = re.compile(r's(\d+)\.([\w$]+)=("(?:\\.|[^"\\])*"|[^;\n]*);')

# 标题字段白名单（按优先级），不使用易误命中的 text 字段
_DWR_TITLE_FIELDS = ("title", "caption", "blogTitle")

_JS_ESCAPES = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    '"': '"',
    "'": "'",
    "\\": "\\",
    "/": "/",
}


def _js_unescape(value: str) -> str:
    """对 DWR/JS 字符串做反转义（\\n、\\"、\\\\、\\uXXXX 等）。"""
    if "\\" not in value:
        return value

    def _replace(m: re.Match) -> str:
        seq = m.group(1)
        if seq.startswith("u") and len(seq) == 5:
            try:
                return chr(int(seq[1:], 16))
            except ValueError:
                return m.group(0)
        return _JS_ESCAPES.get(seq, seq)

    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", _replace, value)


def _parse_dwr_records(raw: str) -> list[dict[str, str]]:
    """按 s<N>. 前缀分组解析 DWR 响应，返回按序号排列的字段字典列表。

    同一条记录的字段（permalink/title/time 等）归入同一字典，
    避免跨字段 findall 后 zip 导致的缺字段错配。
    """
    grouped: dict[int, dict[str, str]] = {}
    for m in _DWR_FIELD_RE.finditer(raw):
        value = m.group(3)
        if value.startswith('"'):
            value = _js_unescape(value[1:-1])
        grouped.setdefault(int(m.group(1)), {})[m.group(2)] = value
    return [grouped[k] for k in sorted(grouped)]


def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    """取消断点：回调返回 True 时抛出 CancelledError。"""
    if should_cancel is not None and should_cancel():
        raise asyncio.CancelledError()


def _extract_last_timestamp(raw: str, batch_count: int) -> str | None:
    """从 DWR 响应中提取最后一条记录的时间戳作为下一页游标。

    DWR 响应为分号分隔格式：
        s0.blogId=...;s0.time=12345;...s1.time=67890;...

    batch_count 为本批原始记录数（未去重），取第 (batch_count - 1) 条的时间戳。
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
    script_name: str,
    method_name: str,
    params: tuple[str, ...],
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


async def _wait_for_article_ready(page: Any, timeout: float = 15.0) -> bool:
    """等待文章页面内容渲染完成，返回是否就绪。

    四阶段渐进策略：
    1. networkidle — 等待异步 API 请求（最多 8s）
    2. 内容文本轮询 — 检查正文元素 textLength > 200（指数退避，最多 12s）
    3. 滚动高度稳定 — 检测渐进式渲染完成（最多 3s）
    4. 超时日志 — 记录原因，返回 False 让调用方降级
    """
    start = time.monotonic()

    # 阶段1: 网络静默
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
        logger.debug("networkidle reached after %.1fs", time.monotonic() - start)
    except Exception:
        logger.debug("networkidle timeout after %.1fs", time.monotonic() - start)

    # 阶段2: 内容文本轮询
    deadline = start + min(timeout, 12.0)
    interval = 0.3

    while time.monotonic() < deadline:
        try:
            ready = await page.evaluate("""() => {
                const selectors = [
                    ".m-postdtl",
                    "[class*='postdtl']",
                    ".m-post",
                    ".postinner",
                    "article .text",
                    "[class*='article']",
                    "[class*='txt']",
                    "[class*='cnt']",
                    "[class*='content']",
                ];
                for (let i = 0; i < selectors.length; i++) {
                    const el = document.querySelector(selectors[i]);
                    if (el && el.textContent.trim().length > 200) return true;
                }
                const main = document.querySelector('main, [role="main"], #content');
                return main && main.textContent.trim().length > 200;
            }""")
            if ready:
                elapsed = time.monotonic() - start
                logger.debug("文章内容就绪 (%.1fs)", elapsed)
                return True
        except Exception:
            logger.debug("内容检测 JS 执行异常，继续轮询", exc_info=True)

        await asyncio.sleep(interval)
        interval = min(interval * 1.5, 2.0)

    # 阶段3: 滚动高度稳定检测（捕获延迟渲染）
    stable_count = 0
    prev_height = None
    scroll_deadline = start + timeout
    while time.monotonic() < scroll_deadline and stable_count < 2:
        try:
            height = await page.evaluate("() => document.body.scrollHeight")
            if height == prev_height:
                stable_count += 1
            else:
                stable_count = 0
            prev_height = height
        except Exception:
            stable_count += 1
        if stable_count < 2:
            await asyncio.sleep(1.0)

    # 阶段4: 超时
    elapsed = time.monotonic() - start
    logger.warning("文章内容等待超时 (%.0fs)，使用当前 DOM 继续", elapsed)
    return False


async def _navigate(page: Any, url: str) -> None:
    """导航到 URL，使用 domcontentloaded 策略适配 SPA。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return
        except (ValueError, TypeError) as exc:
            raise NetworkError(f"无效的 URL: {url}") from exc
        except Exception as exc:
            if attempt == MAX_RETRIES:
                raise NetworkError(
                    f"页面导航失败 [{MAX_RETRIES}/{MAX_RETRIES}]: {url}"
                ) from exc
            logger.warning("导航失败 [%d/%d]: %s - %s", attempt, MAX_RETRIES, url, exc)
            await asyncio.sleep(REQUEST_INTERVAL * attempt)


async def _wait_for_links(page: Any) -> None:
    """等待分页列表中的文章链接渲染完成。

    仅匹配链接选择器（去掉宽泛的 content/article 防止假成功）。
    分页场景无异步 API 依赖，用 selector + 固定缓冲即可。
    """
    try:
        await page.wait_for_selector("a[href*='/post/']", timeout=10000)
        await asyncio.sleep(1)
    except Exception:
        logger.warning("分页链接等待超时，使用当前 DOM 继续")


def _extract_items_from_html(html: str, base_url: str) -> list[dict]:
    """从 HTML 中提取文章链接与标题，去重保序。"""
    soup = BeautifulSoup(html, "lxml")
    items = []
    seen = set()
    for tag in soup.select("a[href*='/post/']"):
        href = tag.get("href", "")
        if not href:
            continue
        full = urljoin(base_url, href)
        if full in seen:
            continue
        seen.add(full)
        title = tag.get_text(strip=True)
        items.append({"url": full, "title": title})
    return items


def _paginated_url(base_url: str, page_num: int) -> str:
    """构造分页 URL。"""
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}page={page_num}"
