"""单篇文章下载模块。

提供 Post 数据模型和 PostDownloader 爬虫，负责解析单篇 LOFTER 文章页面。
LOFTER 使用 SPA 架构，优先尝试 API 接口获取 JSON 数据，失败后降级到 HTML 解析。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from markdownify import markdownify as md

from lofter_downloader.core.api import LofterAPI
from lofter_downloader.core.spider import Spider
from lofter_downloader.utils.exceptions import ParseError
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class Post:
    """文章数据模型。

    Attributes
    ----------
    url : str
        原文链接
    title : str
        文章标题
    author : str
        作者名称
    publish_date : str
        发布日期
    content_html : str
        原始 HTML 正文
    content_markdown : str
        转换后的 Markdown 正文
    image_urls : list[str]
        文中图片 URL 列表
    """

    url: str
    title: str
    author: str
    publish_date: str
    content_html: str
    content_markdown: str = ""
    image_urls: list[str] = field(default_factory=list)


class PostDownloader(Spider):
    """单篇文章下载器。

    解析 LOFTER 文章页面，提取标题、作者、日期、正文和图片链接。

    由于 LOFTER 采用 SPA 架构，实际数据可能通过 API 返回。
    run() 方法会自动尝试以下策略：
    1. 调用 LOFTER API 获取 JSON 数据
    2. 降级为 HTML 页面解析
    """

    async def run(self, url: str) -> Post:  # type: ignore[override]
        """下载并解析单篇文章。

        策略：
        1. LofterAPI.post_detail() — JSON API（首选）
        2. HTML 页面嵌入式 JSON 提取
        3. 传统 HTML 选择器解析（降级）

        Parameters
        ----------
        url : str
            文章完整 URL，格式如 https://xxx.lofter.com/post/xxx_xxx

        Returns
        -------
        Post
            解析后的文章数据

        Raises
        ------
        ParseError
            无法找到标题或正文等关键元素时抛出
        """
        logger.info("Downloading post: %s", url)

        # 策略1: JSON API
        post = await self._try_api(url)
        if post is not None:
            return post

        # 策略2: HTML 页面解析
        logger.info("Falling back to HTML parsing for: %s", url)
        html = await self.fetch(url)
        soup = self.parse_html(html)

        # 策略2a: 嵌入式 JSON
        post = self._try_embedded_json(html, url)
        if post is not None:
            return post

        # 策略2b: 传统 HTML 选择器
        title = self._extract_title(soup, url)
        author = self._extract_author(soup)
        publish_date = self._extract_date(soup)
        content_html = self._extract_content(soup, url)

        post = Post(
            url=url,
            title=title,
            author=author,
            publish_date=publish_date,
            content_html=content_html,
            content_markdown=md(content_html, heading_style="ATX"),
            image_urls=self._extract_images(content_html),
        )
        logger.info("Post downloaded via HTML: %s - %s", title, url)
        return post

    async def _try_api(self, url: str) -> Post | None:
        """通过 api.lofter.com JSON API 获取文章数据。"""
        post_id = self._extract_post_id(url)
        blog_domain = self._extract_blog_domain(url)
        if not post_id or not blog_domain:
            return None
        try:
            logger.debug("Trying API post_detail: %s blog=%s", post_id, blog_domain)
            api = LofterAPI()
            data = await api.post_detail(blog_domain, post_id)
            if data and data.get("postTitle"):
                return self._parse_api_response(data, url)
            return None
        except Exception as exc:
            logger.debug("API post_detail failed: %s", exc)
            return None

    @staticmethod
    def _extract_post_id(url: str) -> str | None:
        """从文章 URL 中提取 postId。"""
        match = re.search(r"/post/([^/?]+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _extract_blog_domain(url: str) -> str | None:
        """从文章 URL 中提取博客域名前缀。"""
        match = re.search(r"https?://([^.]+)\.lofter\.com", url)
        return match.group(1) if match else None

    @staticmethod
    def _parse_api_response(data: dict[str, Any], url: str) -> Post:
        """将 API JSON 响应解析为 Post 对象。"""
        title = data.get("postTitle", "")
        content = data.get("postContent", "")
        author = data.get("blogName", "")
        date = data.get("postTime", "")
        images = data.get("imgUrls", [])
        return Post(
            url=url,
            title=title,
            author=author,
            publish_date=str(date),
            content_html=content,
            content_markdown=md(content, heading_style="ATX"),
            image_urls=images,
        )

    def _try_embedded_json(self, html: str, url: str) -> Post | None:
        """尝试从页面中提取嵌入式 JSON 数据。"""
        # 尝试 window.__INITIAL_STATE__
        match = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*({.*?});",
            html,
            re.DOTALL,
        )
        if match:
            try:
                data: dict[str, Any] = json.loads(match.group(1))
                return self._parse_from_state(data, url)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.debug("Failed to parse __INITIAL_STATE__: %s", exc)

        # 尝试 <script id="__NEXT_DATA__">
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>({.*?})</script>',
            html,
            re.DOTALL,
        )
        if match:
            try:
                data = json.loads(match.group(1))
                return self._parse_from_next(data, url)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.debug("Failed to parse __NEXT_DATA__: %s", exc)

        # 尝试 application/ld+json
        for match in re.finditer(
            r'<script[^>]+type="application/ld\+json"[^>]*>({.*?})</script>',
            html,
            re.DOTALL,
        ):
            try:
                data = json.loads(match.group(1))
                return self._parse_from_ldjson(data, url)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.debug("Failed to parse ld+json: %s", exc)

        return None

    @staticmethod
    def _parse_from_state(data: dict[str, Any], url: str) -> Post | None:
        """从 __INITIAL_STATE__ 数据中提取 Post。"""
        post_data = data.get("post") or data.get("article") or data.get("blog")
        if not post_data:
            return None
        title = post_data.get("title", "")
        content = post_data.get("content", "") or post_data.get("htmlContent", "")
        author = post_data.get("author", {}).get("name", "") or post_data.get(
            "blogName", ""
        )
        date = post_data.get("date", "") or post_data.get("publishTime", "")
        images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content)
        return Post(
            url=url,
            title=title,
            author=author,
            publish_date=str(date),
            content_html=content,
            content_markdown=md(content, heading_style="ATX"),
            image_urls=images,
        )

    @staticmethod
    def _parse_from_next(data: dict[str, Any], url: str) -> Post | None:
        """从 __NEXT_DATA__ 数据中提取 Post。"""
        props = data.get("props", {})
        page_props = props.get("pageProps", {})
        post_data = page_props.get("post") or page_props.get("article") or page_props
        if not post_data or not post_data.get("title"):
            return None
        title = post_data.get("title", "")
        content = post_data.get("content", "") or post_data.get("htmlContent", "")
        author = post_data.get("author", {}).get("name", "") or post_data.get(
            "blogName", ""
        )
        date = post_data.get("date", "") or post_data.get("publishTime", "")
        images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content)
        return Post(
            url=url,
            title=title,
            author=author,
            publish_date=str(date),
            content_html=content,
            content_markdown=md(content, heading_style="ATX"),
            image_urls=images,
        )

    @staticmethod
    def _parse_from_ldjson(data: dict[str, Any], url: str) -> Post | None:
        """从 JSON-LD 结构化数据中提取 Post。"""
        if not data.get("name") and not data.get("headline"):
            return None
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
        return Post(
            url=url,
            title=title,
            author=author,
            publish_date=str(date),
            content_html=content,
            content_markdown=md(content, heading_style="ATX"),
            image_urls=images,
        )

    @staticmethod
    def _extract_title(soup: Any, url: str) -> str:
        """从页面中提取文章标题。"""
        selectors = ["h1.post_title", ".title", "h1", "[class*='title']"]
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag is not None:
                return tag.get_text(strip=True)
        raise ParseError(f"Cannot find title element in page: {url}")

    @staticmethod
    def _extract_author(soup: Any) -> str:
        """从页面中提取作者名称。"""
        selectors = [".author", ".blogname", "[data-blogname]", "[class*='author']"]
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag is not None:
                return tag.get_text(strip=True)
        return "未知作者"

    @staticmethod
    def _extract_date(soup: Any) -> str:
        """从页面中提取发布日期。"""
        selectors = [
            ".date", ".time", "[datetime]",
            "[class*='date']", "[class*='time']",
        ]
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag is not None:
                dt = tag.get("datetime", "")
                if dt:
                    return dt
                return tag.get_text(strip=True)
        return ""

    @staticmethod
    def _extract_content(soup: Any, url: str) -> str:
        """从页面中提取文章正文 HTML。"""
        selectors = [".post_content", ".content", "article", "[class*='content']"]
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag is not None:
                return str(tag)
        raise ParseError(f"Cannot find content element in page: {url}")

    @staticmethod
    def _extract_images(html: str) -> list[str]:
        """从 HTML 中提取所有图片 URL。"""
        pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']')
        return [m.group(1) for m in pattern.finditer(html)]
