"""收藏文章下载模块。"""

from __future__ import annotations

from lofter_downloader.core.post import Post, PostDownloader
from lofter_downloader.core.spider import Spider
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class FavoritesDownloader(Spider):
    """收藏文章下载器。

    在已登录状态下，获取用户收藏页面的所有文章并下载。
    """

    FAVORITES_URL = "https://www.lofter.com/fav/blog"

    def __init__(self, cookie: str) -> None:
        super().__init__()
        self._cookie = cookie
        self._post_downloader = PostDownloader()

    async def run(self) -> list[Post]:  # type: ignore[override]
        """下载当前用户的所有收藏文章。

        Returns
        -------
        list[Post]
            收藏文章的列表
        """
        logger.info("Downloading favorite posts")

        post_links = await self._collect_all_fav_links()
        logger.info("Found %d favorite posts", len(post_links))

        posts: list[Post] = []
        for link in post_links:
            post = await self._post_downloader.run(link)
            posts.append(post)

        logger.info("Completed downloading %d favorite posts", len(posts))
        return posts

    async def _collect_all_fav_links(self) -> list[str]:
        """收集所有收藏文章的链接。"""
        all_links: list[str] = []
        page = 1

        while True:
            links = await self._get_fav_links_from_page(page)
            if not links:
                break
            all_links.extend(links)
            page += 1

        return all_links

    async def _get_fav_links_from_page(self, page: int) -> list[str]:
        """从收藏页面的指定页码提取文章链接。"""
        url = f"{self.FAVORITES_URL}?page={page}"
        html = await self.fetch(url)
        soup = self.parse_html(html)

        links: list[str] = []
        for tag in soup.select("a[href*='/post/']"):
            href = str(tag.get("href", ""))
            if href and href not in links:
                links.append(href)

        return links

    async def close(self) -> None:
        """关闭子爬虫连接。"""
        await self._post_downloader.close()
        await super().close()
