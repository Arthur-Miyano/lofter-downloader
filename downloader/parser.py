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
        result.get("title", ""),
        len(result.get("content_html", "")),
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
        ".m-postdtl, [class*='postdtl'], .m-post, .postinner, article, .m-detail"
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

    # 日期：优先 DOM/meta 提取，其次从正文匹配 YYYY-MM-DD / YYYY.MM.DD 等
    body_text = soup.body.get_text(separator=" ", strip=True) if soup.body else ""
    date = _extract_date_from_soup(soup) or _extract_date_from_text(
        body_text, content_html
    )

    # 图片：内容区域中的 img（排除头像和小图标）
    images = _extract_images(content_html or html)

    # 清洗正文 Markdown：剔除模板带入的非文章内容（博客头部、
    # 热度/评论、标签栏、上一篇/下一篇、版权页脚等）
    content_md = md(content_html, heading_style="ATX") if content_html else ""
    content_md = _clean_content_markdown(content_md, title)

    return {
        "url": url,
        "title": title,
        "author": author,
        "publish_date": date,
        "content_html": content_html,
        "content_markdown": content_md,
        "image_urls": images,
    }


# ------------------------------------------------------------------
# 正文清洗：剔除页面模板带入的非文章内容
# ------------------------------------------------------------------

# 真实段落判定：去除 Markdown 语法后纯文本不少于该长度
_REAL_PARAGRAPH_MIN = 40

# 头部杂质（仅作用于首个真实段落之前）
_HEAD_JUNK_RES = [
    # 链接包裹的图片（博客头像的典型形态，正文首图通常是裸图，不受影响）
    re.compile(r"^\[\s*!\[[^\]]*\]\([^)]*\)\s*\]\([^)]*\)\s*$"),
    # URL 含头像/图标特征的裸图
    re.compile(r"^!\[[^\]]*\]\([^)]*(?:avatar|avaimg|icon)[^)]*\)\s*$"),
    re.compile(r"^\[[^\]]*\]\([^)]*\)\s*$"),  # 纯链接行
    re.compile(r"^#{1,6}\s*\[[^\]]*\]\([^)]*\)"),  # 链接标题（博客名/日期等）
    re.compile(r"^[*\-+]\s*\[[^\]]*\]\([^)]*\)\s*$"),  # 链接列表项（博客导航）
]

# 热度/评论/收藏区块特征（尾部出现后才启用序号/图标清理，
# 避免把作者自己的编号脚注、文末插图当杂质删掉）
_TAIL_LIKER_RES = [
    re.compile(r"^\[(热度|评论)[^\]]*\]\("),  # 热度/评论入口
    re.compile(r"^#{1,6}\s*(评论|热度)"),  # 评论/热度区块标题
    re.compile(r"(共\d+人收藏了此|很喜欢此)"),  # 收藏/喜欢列表
]

# 尾部杂质（仅作用于最后一个真实段落之后）
_TAIL_JUNK_RES = [
    # LOFTER 标签行
    re.compile(r"^(\[(?:#|＃)[^\]]*\]\([^)]*/tag/[^)]*\)\s*)+$"),
    *_TAIL_LIKER_RES,
    # URL 含图标/头像/小缩略图特征的图片（正文尾图不受影响）
    re.compile(
        r"^!?\[?\s*!\[[^\]]*\]\([^)]*"
        r"(?:icon|avatar|avaimg|thumbnail=(?:16|32))[^)]*\)"
    ),
    re.compile(r"^\[[^\]]*(上一篇|下一篇|查看更多|返回首页)[^\]]*\]\("),
    re.compile(r"^加载中"),  # 加载占位
    re.compile(r"^只展示最近"),  # 数据说明
    re.compile(r"^[©&]|Powered by", re.IGNORECASE),  # 版权页脚
]

# 热度区块里的序号项（需 liker 上下文确认）
_NUMBERED_RE = re.compile(r"^\d+\.\s")


def _plain_len(line: str) -> int:
    """去除 Markdown 语法后的纯文本长度（用于判定真实段落）。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)  # 图片
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # 链接保留文字
    text = re.sub(r"[#>*_`~\-]+", "", text)  # 结构符号
    return len(text.strip())


def _clean_content_markdown(md_text: str, title: str = "") -> str:
    """清洗正文 Markdown 首尾的模板杂质。

    LOFTER 部分模板（尤其旧版博客主题）会把博客头部（头像/博客名/导航）
    和页脚（标签/热度/评论/上下篇/版权）包进正文容器。这些杂质的特征
    是几乎全由链接构成且聚集在首尾，因此只在「首个真实段落之前」和
    「最后一个真实段落之后」两个区域内按模式删除，正文中间一律不动。
    纯图集文章（无真实段落）不清洗，避免误删正文图片。
    """
    if not md_text:
        return md_text
    lines = md_text.splitlines()
    real_idx = [
        i for i, ln in enumerate(lines) if _plain_len(ln) >= _REAL_PARAGRAPH_MIN
    ]
    if not real_idx:
        return md_text
    first, last = real_idx[0], real_idx[-1]

    title_norm = title.replace("\\", "").strip()

    def _is_head_junk(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        if any(p.search(s) for p in _HEAD_JUNK_RES):
            return True
        # 与文章标题重复的纯文本标题行
        return bool(
            title_norm and s.lstrip("# ").replace("\\", "").strip() == title_norm
        )

    tail_lines = lines[last + 1 :]
    # 尾部出现热度/评论/收藏区块特征后，序号项才判定为杂质
    has_liker = any(p.search(ln.strip()) for ln in tail_lines for p in _TAIL_LIKER_RES)

    def _is_tail_junk(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        if any(p.search(s) for p in _TAIL_JUNK_RES):
            return True
        return bool(has_liker and _NUMBERED_RE.match(s))

    kept = (
        [ln for ln in lines[:first] if not _is_head_junk(ln)]
        + lines[first : last + 1]
        + [ln for ln in tail_lines if not _is_tail_junk(ln)]
    )
    cleaned = "\n".join(kept)
    # 压缩删除后产生的连续空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip("\n")
    return cleaned


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
        if tokens & {
            "nav",
            "footer",
            "header",
            "sidebar",
            "menu",
            "script",
            "recommend",
            "ad",
            "banner",
            "comment",
            "tag",
        }:
            continue
        text = el.get_text(strip=True)
        if len(text) > best_len:
            best_len = len(text)
            best_el = el
    return best_el


def _extract_author(soup: BeautifulSoup) -> str:
    """从 DOM 提取作者名。"""
    selectors = [
        "[class*='author']",
        "[class*='blogname']",
        "[data-blogname]",
        ".blogname",
    ]
    for sel in selectors:
        tag = soup.select_one(sel)
        if tag:
            return tag.get_text(strip=True)
    return ""


def _extract_date_from_text(body_text: str, content_html: str) -> str:
    """从文本中匹配日期：YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD / YYYY年MM月DD日。

    优先在 content_html 中搜索，仅未找到时才回退到全页 body_text。
    """
    for source in (content_html, body_text):
        if not source:
            continue
        for pattern in (
            r"(\d{4}-\d{1,2}-\d{1,2})",
            r"(\d{4}\.\d{1,2}\.\d{1,2})",
            r"(\d{4}/\d{1,2}/\d{1,2})",
            r"(\d{4}年\d{1,2}月\d{1,2}日)",
        ):
            m = re.search(pattern, source)
            if m:
                return (
                    m.group(1)
                    .replace(".", "-")
                    .replace("/", "-")
                    .replace("年", "-")
                    .replace("月", "-")
                    .replace("日", "")
                )
    return ""


def _extract_date_from_soup(soup: BeautifulSoup) -> str:
    """从 DOM 元素或 meta 标签提取日期。

    覆盖 LOFTER 常见结构：.date / time[datetime] / .publishDate / .post-date，
    以及 <meta property="article:published_time"> 等。
    """
    # 优先 meta 标签（通常最准确）
    for meta in soup.select(
        "meta[property='article:published_time'], "
        "meta[name='publishdate'], meta[name='date'], meta[itemprop='datePublished']"
    ):
        content = meta.get("content", "")
        m = re.search(r"(\d{4}[./-]\d{1,2}[./-]\d{1,2})", content)
        if m:
            return m.group(1).replace(".", "-").replace("/", "-")

    # DOM 元素
    date_el = soup.select_one(
        ".date, [class*='date'], time, [datetime], "
        ".publishDate, .post-date, .time, .meta-date"
    )
    if not date_el:
        return ""
    dt = date_el.get("datetime", "") or date_el.get_text(strip=True)
    for pattern in (
        r"(\d{4}[./-]\d{1,2}[./-]\d{1,2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
    ):
        m = re.search(pattern, dt)
        if m:
            return (
                m.group(1)
                .replace(".", "-")
                .replace("/", "-")
                .replace("年", "-")
                .replace("月", "-")
                .replace("日", "")
            )
    return ""


def _extract_images(content_html: str) -> list[str]:
    """提取内容中的图片 URL，排除头像、图标、缩略图等。"""
    srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content_html)
    # 过滤掉头像、图标、太小的图片等
    result = []
    for src in srcs:
        if any(
            kw in src.lower()
            for kw in (
                "avatar",
                "icon",
                "logo",
                "favicon",
                "thumbnail=16",
                "thumbnail=32",
                "thumbnail=48",
                "thumbnail=64",
            )
        ):
            continue
        result.append(src)
    return result
