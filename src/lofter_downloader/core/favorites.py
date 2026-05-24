"""收藏文章下载模块。"""

from __future__ import annotations

from lofter_downloader.config import settings
from lofter_downloader.core.post import Post, PostDownloader
from lofter_downloader.core.spider import Spider
from lofter_downloader.utils.exceptions import NetworkError
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class FavoritesDownloader(Spider):
    """收藏文章下载器。

    在已登录状态下，获取用户收藏页面的所有文章并下载。

    LOFTER 收藏页面使用 SPA 客户端路由，实际文章列表通过 API 获取。
    优先尝试 API 接口，失败后降级到 HTML 页面解析。
    """

    FAVORITE_URLS = [
        "https://www.lofter.com/fav/blog",
        "https://www.lofter.com/user/fav/blog",
        "https://www.lofter.com/my/fav",
    ]
    API_FAV_LIST = "https://www.lofter.com/next/api/fav/list"

    def __init__(self, cookie: str) -> None:
        super().__init__()
        self._cookie = cookie
        if cookie:
            settings.cookie = cookie
        self._post_downloader = PostDownloader()
        self._resolved_url: str | None = None

    async def run(self) -> list[Post]:  # type: ignore[override]
        """下载当前用户的所有收藏文章。

        Returns
        -------
        list[Post]
            收藏文章的列表
        """
        logger.info("Downloading favorite posts")

        post_links = await self._collect_all_fav_links()
        if not post_links:
            logger.warning("No favorite posts found (cookie may be invalid)")
            return []

        logger.info("Found %d favorite posts", len(post_links))

        posts: list[Post] = []
        for link in post_links:
            try:
                post = await self._post_downloader.run(link)
                posts.append(post)
            except Exception as exc:
                logger.warning("Failed to download post %s: %s", link, exc)

        logger.info("Completed downloading %d favorite posts", len(posts))
        return posts

    async def _resolve_fav_url(self) -> str:
        """解析可用的收藏页面 URL。"""
        if self._resolved_url is not None:
            return self._resolved_url

        for url in self.FAVORITE_URLS:
            try:
                logger.debug("Trying favorites URL: %s", url)
                resp_text = await self.fetch(url)
                soup = self.parse_html(resp_text)
                links = soup.select("a[href*='/post/']")
                if links:
                    self._resolved_url = url
                    logger.info("Resolved favorites URL: %s", url)
                    return url
            except Exception as exc:
                logger.debug("Favorites URL %s failed: %s", url, exc)
                continue

        raise NetworkError(
            "Cannot resolve any favorites URL. LOFTER structure may have changed."
        )

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
        fav_url = await self._resolve_fav_url()
        url = f"{fav_url}?page={page}"
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
