"""文章解析模块。

从 Playwright Page 中提取文章数据，按优先级依次尝试：
1. JS 全局状态 (window.__INITIAL_STATE__)
2. Next.js 数据 (__NEXT_DATA__)
3. HTML 降级（嵌入式 JSON → JSON-LD → CSS 选择器）

全部策略失败时抛出 ParseError。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from playwright.async_api import Page

from downloader.exceptions import ParseError

logger = logging.getLogger(__name__)


async def extract_post(page: Page, url: str) -> dict | None:
    """从页面中提取文章数据，返回 dict 或 None。

    三种策略按优先级降级。全部失败时抛出 ParseError。
    """
    # 策略 1: window.__INITIAL_STATE__
    result = await _try_initial_state(page, url)
    if result:
        return result

    # 策略 2: __NEXT_DATA__
    result = await _try_next_data(page, url)
    if result:
        return result

    # 策略 3: HTML 降级提取（嵌入式 JSON → JSON-LD → CSS 选择器）
    html = await page.content()
    result = _try_html(html, url)
    if result:
        return result

    raise ParseError(f"文章解析失败，所有策略均未提取到内容: {url}")


# ------------------------------------------------------------------
# 策略 1: __INITIAL_STATE__
# ------------------------------------------------------------------


async def _try_initial_state(page: Page, url: str) -> dict | None:
    try:
        state: Any = await page.evaluate("() => window.__INITIAL_STATE__")
        if state and isinstance(state, dict):
            return _parse_initial_state(state, url)
    except Exception as exc:
        logger.debug("__INITIAL_STATE__ 提取失败: %s", exc)
    return None


def _parse_initial_state(data: dict, url: str) -> dict | None:
    post_data = data.get("post") or data.get("article") or data.get("blog")
    if not post_data or not isinstance(post_data, dict):
        return None
    title = post_data.get("title", "")
    if not title:
        return None
    content = post_data.get("content", "") or post_data.get("htmlContent", "") or ""
    author_val = post_data.get("author", {})
    author = (
        author_val.get("name", "")
        if isinstance(author_val, dict)
        else str(author_val)
    ) or post_data.get("blogName", "")
    date = post_data.get("date", "") or post_data.get("publishTime", "")
    images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content)
    return {
        "url": url,
        "title": title,
        "author": author,
        "publish_date": str(date),
        "content_html": content,
        "content_markdown": md(content, heading_style="ATX") if content else "",
        "image_urls": images,
    }


# ------------------------------------------------------------------
# 策略 2: __NEXT_DATA__
# ------------------------------------------------------------------


async def _try_next_data(page: Page, url: str) -> dict | None:
    try:
        raw: Any = await page.evaluate(
            "() => document.getElementById('__NEXT_DATA__')?.textContent"
        )
        if raw and isinstance(raw, str):
            data = json.loads(raw)
            return _parse_next_data(data, url)
    except Exception as exc:
        logger.debug("__NEXT_DATA__ 提取失败: %s", exc)
    return None


def _parse_next_data(data: dict, url: str) -> dict | None:
    props = data.get("props", {})
    page_props = props.get("pageProps", {})
    post_data = page_props.get("post") or page_props.get("article") or page_props
    if not post_data or not isinstance(post_data, dict):
        return None
    title = post_data.get("title", "")
    if not title:
        return None
    content = post_data.get("content", "") or post_data.get("htmlContent", "") or ""
    author_val = post_data.get("author", {})
    author = (
        author_val.get("name", "")
        if isinstance(author_val, dict)
        else str(author_val)
    ) or post_data.get("blogName", "")
    date = post_data.get("date", "") or post_data.get("publishTime", "")
    images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content)
    return {
        "url": url,
        "title": title,
        "author": author,
        "publish_date": str(date),
        "content_html": content,
        "content_markdown": md(content, heading_style="ATX") if content else "",
        "image_urls": images,
    }


# ------------------------------------------------------------------
# 策略 3: BS4 HTML 选择器
# ------------------------------------------------------------------


def _try_html(html: str, url: str) -> dict | None:
    """使用 BeautifulSoup 从 HTML 中提取文章数据。"""
    soup = BeautifulSoup(html, "lxml")

    # JSON-LD 结构化数据
    ld_json = _try_ldjson(soup, url)
    if ld_json:
        return ld_json

    # 嵌入式 JS 状态（正则匹配 HTML 源码）
    emb = _try_embedded_json(html, url)
    if emb:
        return emb

    # CSS 选择器降级
    title = _extract_text(soup, ["h1.post_title", ".title", "h1", "[class*='title']"])
    if not title:
        return None

    author = _extract_text(
        soup, [".author", ".blogname", "[data-blogname]", "[class*='author']"]
    ) or "未知作者"
    date = _extract_date(soup)
    content_el = soup.select_one(
        ".post_content, .content, article, [class*='content']"
    )
    content_html = str(content_el) if content_el else ""
    images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content_html)
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


def _try_ldjson(soup: BeautifulSoup, url: str) -> dict | None:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
            if not data.get("name") and not data.get("headline"):
                continue
            title = data.get("name", "") or data.get("headline", "")
            content = data.get("description", "") or data.get("articleBody", "")
            author_data = data.get("author", {})
            author = (
                author_data.get("name", "")
                if isinstance(author_data, dict)
                else str(author_data)
            )
            date = data.get("datePublished", "") or data.get("dateCreated", "")
            images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content)
            return {
                "url": url,
                "title": title,
                "author": author,
                "publish_date": str(date),
                "content_html": content,
                "content_markdown": md(content, heading_style="ATX") if content else "",
                "image_urls": images,
            }
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _try_embedded_json(html: str, url: str) -> dict | None:
    # __INITIAL_STATE__ 正则
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.*?});", html, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            result = _parse_initial_state(data, url)
            if result:
                return result
        except (json.JSONDecodeError, TypeError):
            pass

    # __NEXT_DATA__ 正则
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>({.*?})</script>', html, re.DOTALL
    )
    if match:
        try:
            data = json.loads(match.group(1))
            result = _parse_next_data(data, url)
            if result:
                return result
        except (json.JSONDecodeError, TypeError):
            pass

    return None


# ------------------------------------------------------------------
# DOM 工具函数
# ------------------------------------------------------------------


def _extract_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag is not None:
            return str(tag.get_text(strip=True))
    return ""


def _extract_date(soup: BeautifulSoup) -> str:
    date_selectors = [
        ".date", ".time", "[datetime]", "[class*='date']", "[class*='time']",
    ]
    for selector in date_selectors:
        tag = soup.select_one(selector)
        if tag is not None:
            dt = tag.get("datetime", "")
            if dt:
                return str(dt)
            return str(tag.get_text(strip=True))
    return ""
