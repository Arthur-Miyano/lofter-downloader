"""单篇文章下载模块。

提供 Post 数据模型和 PostDownloader 爬虫，负责解析单篇 LOFTER 文章页面。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from markdownify import markdownify as md

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
    """

    async def run(self, url: str) -> Post:  # type: ignore[override]
        """下载并解析单篇文章。

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
        html = await self.fetch(url)
        soup = self.parse_html(html)

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
        logger.info("Post downloaded: %s - %s", title, url)
        return post

    def _extract_title(self, soup: Any, url: str) -> str:
        """从页面中提取文章标题。"""
        selectors = ["h1.post_title", ".title", "h1"]
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag is not None:
                return tag.get_text(strip=True)
        raise ParseError(f"Cannot find title element in page: {url}")

    @staticmethod
    def _extract_author(soup: Any) -> str:
        """从页面中提取作者名称。"""
        selectors = [".author", ".blogname", "[data-blogname]"]
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag is not None:
                return tag.get_text(strip=True)
        return "未知作者"

    @staticmethod
    def _extract_date(soup: Any) -> str:
        """从页面中提取发布日期。"""
        selectors = [".date", ".time", "[datetime]"]
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
        selectors = [".post_content", ".content", "article"]
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
