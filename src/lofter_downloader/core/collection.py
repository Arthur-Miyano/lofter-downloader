"""合集下载模块。"""

from __future__ import annotations

from lofter_downloader.core.post import Post, PostDownloader
from lofter_downloader.core.spider import Spider
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class CollectionDownloader(Spider):
    """合集下载器。

    解析 LOFTER 合集页面，获取合集内所有文章并下载。
    """

    def __init__(self) -> None:
        super().__init__()
        self._post_downloader = PostDownloader()

    async def run(self, collection_url: str) -> list[Post]:  # type: ignore[override]
        """下载指定合集内的全部文章。

        Parameters
        ----------
        collection_url : str
            合集页面 URL，格式如 https://xxx.lofter.com/view/collection/xxx

        Returns
        -------
        list[Post]
            合集内所有文章的列表
        """
        logger.info("Downloading collection: %s", collection_url)

        post_links = await self._collect_all_post_links(collection_url)
        logger.info("Found %d posts in collection", len(post_links))

        posts: list[Post] = []
        for link in post_links:
            post = await self._post_downloader.run(link)
            posts.append(post)

        logger.info("Completed downloading collection: %s (%d posts)", collection_url, len(posts))
        return posts

    async def _collect_all_post_links(self, collection_url: str) -> list[str]:
        """收集合集中所有文章的链接。"""
        all_links: list[str] = []
        page = 1

        while True:
            links = await self._get_post_links_from_page(collection_url, page)
            if not links:
                break
            all_links.extend(links)
            page += 1

        return all_links

    async def _get_post_links_from_page(self, collection_url: str, page: int) -> list[str]:
        """从合集页面的指定页码提取文章链接。"""
        paginated_url = f"{collection_url}?page={page}"
        html = await self.fetch(paginated_url)
        soup = self.parse_html(html)

        links: list[str] = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)

        return links

    async def close(self) -> None:
        """关闭子爬虫连接。"""
        await self._post_downloader.close()
        await super().close()
