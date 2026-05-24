"""作者全部文章下载模块。"""

from __future__ import annotations

import re

from lofter_downloader.core.post import Post, PostDownloader
from lofter_downloader.core.resolver import UserResolver
from lofter_downloader.core.spider import Spider
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class BlogDownloader(Spider):
    """作者全部文章下载器。

    根据用户数字 ID 解析博客域名，遍历全部分页下载所有文章。
    """

    def __init__(self) -> None:
        super().__init__()
        self._post_downloader = PostDownloader()
        self._resolver = UserResolver()

    async def run(self, user_id: int) -> list[Post]:  # type: ignore[override]
        """下载指定作者的全部文章。

        Parameters
        ----------
        user_id : int
            LOFTER 用户数字 ID

        Returns
        -------
        list[Post]
            所有文章的列表
        """
        domain = await self._resolver.resolve_domain(user_id)
        logger.info("Downloading all posts from blog: %s (user_id: %d)", domain, user_id)

        post_links = await self._collect_all_post_links(domain)
        logger.info("Found %d posts in blog: %s", len(post_links), domain)

        posts: list[Post] = []
        for link in post_links:
            post = await self._post_downloader.run(link)
            posts.append(post)

        logger.info("Completed downloading %d posts from: %s", len(posts), domain)
        return posts

    async def _collect_all_post_links(self, domain: str) -> list[str]:
        """收集博客中所有文章的链接。"""
        all_links: list[str] = []
        page = 1

        while True:
            links = await self._get_post_links_from_page(domain, page)
            if not links:
                break
            all_links.extend(links)
            page += 1

        return all_links

    async def _get_post_links_from_page(self, domain: str, page: int) -> list[str]:
        """从指定页码提取所有文章链接。"""
        url = f"https://{domain}.lofter.com/?page={page}"
        html = await self.fetch(url)
        soup = self.parse_html(html)

        links: list[str] = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)

        return links

    async def close(self) -> None:
        """关闭所有子爬虫的连接。"""
        await self._post_downloader.close()
        await self._resolver.close()
        await super().close()
