"""文章解析模块。

从 Playwright Page 中提取文章数据，使用 CSS 选择器 DOM 提取。

LOFTER 2026 实测结构：
- <title> 含 "{文章标题}-{作者名}"
- .m-postdtl 含文章正文（6743 chars 实测）
- .date 含发布日期（格式 YYYY.MM.DD）
- body.p-detailpage 为页面容器（包含导航头，不应直接使用）
- 无 __INITIAL_STATE__ / __NEXT_DATA__ / ld+json
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from playwright.async_api import Page

from downloader.exceptions import ParseError

logger = logging.getLogger(__name__)


async def extract_post(page: Page, url: str) -> dict | None:
    """从页面中提取文章数据，返回 dict 或 None。"""
    html = await page.content()
    result = _try_html(html, url)
    if not result:
        raise ParseError(f"文章解析失败，未提取到内容: {url}")

    logger.info(
        "文章解析成功 (title=%s, content_len=%d)",
        result.get("title", ""), len(result.get("content_html", "")),
    )
    return result


def _try_html(html: str, url: str) -> dict | None:
    """使用 BeautifulSoup 从 HTML 中提取文章数据。"""
    soup = BeautifulSoup(html, "lxml")

    # 标题：<title> 格式为 "{文章标题}-{作者名}"
    title_tag = soup.select_one("title")
    raw_title = title_tag.get_text(strip=True) if title_tag else ""
    title, author_from_title = _parse_title_tag(raw_title)
    # 降级：h1
    if not title:
        h1_tag = soup.select_one("h1")
        if h1_tag:
            title = h1_tag.get_text(strip=True)

    # 内容容器
    # 注意：[class*='detail'] 会误匹配 body.p-detailpage，
    # [class*='content'] 在 LOFTER 实测无匹配，均移除。
    content_el = soup.select_one(
        ".m-postdtl, [class*='postdtl'], .m-post, "
        ".postinner, article, .m-detail"
    )
    # 排除 body/html 标签（某些宽泛选择器可能误匹配）
    if content_el is not None and content_el.name in ("body", "html"):
        inner = soup.select_one(".m-postdtl, .m-post, article")
        content_el = inner if inner else content_el
    content_html = str(content_el) if content_el else ""

    # 降级：取 body 中最长的文本块
    if not content_html or len(content_html) < 500:
        content_el = _find_largest_text_block(soup)
        content_html = str(content_el) if content_el else ""

    if not title and not content_html:
        return None

    # 作者：优先从 title 提取，否则查找 DOM
    author = author_from_title or _extract_author(soup) or "未知作者"

    # 日期：优先查找 .date 元素，其次从正文匹配 YYYY-MM-DD / YYYY.MM.DD
    body_text = soup.body.get_text(separator=" ", strip=True) if soup.body else ""
    date = _extract_date_from_text(body_text, content_html) or _extract_date_from_soup(soup)

    # 图片：内容区域中的 img（排除头像和小图标）
    images = _extract_images(content_html or html)

    return {
        "url": url,
        "title": title,
        "author": author,
        "publish_date": date,
        "content_html": content_html,
        "content_markdown": (
            md(content_html, heading_style="ATX") if content_html else ""
        ),
        "image_urls": images,
    }


def _parse_title_tag(raw_title: str) -> tuple[str, str]:
    """从 <title> 解析文章标题和作者。

    LOFTER 格式: "{文章标题}-{作者名}"
    按最后一个分隔符拆分，避免标题中多个连字符的影响。
    返回 (title, author)。
    """
    if not raw_title:
        return "", ""
    m = re.search(r"^(.*)[-–—]([^-–—]+)$", raw_title.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return raw_title.strip(), ""


def _find_largest_text_block(soup: BeautifulSoup) -> object | None:
    """找到 body 中包含最多文本的 div/article。"""
    best_el = None
    best_len = 0
    for el in soup.select("div, article, section, main"):
        # 跳过导航、页脚、脚本等（使用 token 精确匹配而非子串包含）
        cls = (el.get("class") or [""])[0] if el.get("class") else ""
        cls_str = str(cls).lower()
        tokens = set(re.split(r"[\s\-_]+", cls_str))
        if tokens & {"nav", "footer", "header", "sidebar", "menu", "script",
                       "recommend", "ad", "banner", "comment", "tag"}:
            continue
        text = el.get_text(strip=True)
        if len(text) > best_len:
            best_len = len(text)
            best_el = el
    return best_el


def _extract_author(soup: BeautifulSoup) -> str:
    """从 DOM 提取作者名。"""
    selectors = [
        "[class*='author']", "[class*='blogname']",
        "[data-blogname]", ".blogname",
    ]
    for sel in selectors:
        tag = soup.select_one(sel)
        if tag:
            return tag.get_text(strip=True)
    return ""


def _extract_date_from_text(body_text: str, content_html: str) -> str:
    """从文本中匹配日期：YYYY-MM-DD 或 YYYY.MM.DD 或 YYYY年MM月DD日。

    优先在 content_html 中搜索，仅未找到时才回退到全页 body_text。
    """
    for source in (content_html, body_text):
        if not source:
            continue
        for pattern in (r"(\d{4}-\d{2}-\d{2})", r"(\d{4}\.\d{2}\.\d{2})",
                        r"(\d{4}年\d{1,2}月\d{1,2}日)"):
            m = re.search(pattern, source)
            if m:
                return m.group(1).replace(".", "-").replace("年", "-").replace("月", "-").replace("日", "")
    return ""


def _extract_date_from_soup(soup: BeautifulSoup) -> str:
    """从 DOM 元素提取日期（如 <a class=\"date\">2019.11.24</a>）。"""
    date_el = soup.select_one(".date, [class*='date'], time, [datetime]")
    if not date_el:
        return ""
    dt = date_el.get("datetime", "") or date_el.get_text(strip=True)
    m = re.search(r"(\d{4}[.-]\d{2}[.-]\d{2})", dt)
    if m:
        return m.group(1).replace(".", "-")
    return ""


def _extract_images(content_html: str) -> list[str]:
    """提取内容中的图片 URL，排除头像、图标、缩略图等。"""
    srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content_html)
    # 过滤掉头像、图标、太小的图片等
    result = []
    for src in srcs:
        if any(kw in src.lower() for kw in
               ("avatar", "icon", "logo", "favicon", "thumbnail=16",
                "thumbnail=32", "thumbnail=48", "thumbnail=64")):
            continue
        result.append(src)
    return result
