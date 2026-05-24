"""收藏文章下载模块。"""

from __future__ import annotations

from lofter_downloader.config import settings
from lofter_downloader.core.api import LofterAPI
from lofter_downloader.core.post import Post, PostDownloader
from lofter_downloader.core.spider import Spider
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class FavoritesDownloader(Spider):
    """收藏文章下载器。

    在已登录状态下，获取用户收藏页面的所有文章并下载。
    优先使用 lofter-phone-login-auth Token 调用 API，降级使用 Cookie + HTML。
    """

    FAVORITE_URLS = [
        "https://www.lofter.com/fav/blog",
        "https://www.lofter.com/user/fav/blog",
        "https://www.lofter.com/my/fav",
    ]

    def __init__(self, cookie: str = "") -> None:
        super().__init__()
        self._cookie = cookie
        if cookie:
            settings.cookie = cookie
        self._post_downloader = PostDownloader()
        self._resolved_url: str | None = None

    async def run(self) -> list[Post]:  # type: ignore[override]
        """下载当前用户的所有收藏文章。

        策略：
        1. lofter-phone-login-auth Token → API batchdata（首选）
        2. Cookie → HTML 解析（降级）
        """
        logger.info("Downloading favorite posts")

        # 策略1: Token API
        if settings.lofter_phone_login_auth:
            posts = await self._try_api_favorites()
            if posts:
                logger.info("Downloaded %d favorite posts via API", len(posts))
                return posts

        # 策略2: Cookie + HTML
        posts = await self._try_html_favorites()
        return posts

    async def _try_api_favorites(self) -> list[Post]:
        """通过 lofter-phone-login-auth Token 调用 API 获取收藏。"""
        posts: list[Post] = []
        api = LofterAPI()
        offset = 0
        try:
            while True:
                items = await api.favorites(offset)
                if not items:
                    break
                for item in items:
                    post = await self._post_downloader.run(
                        self._build_post_url(item)
                    )
                    posts.append(post)
                offset += 18
        except Exception as exc:
            logger.warning("API favorites failed: %s", exc)
        return posts

    async def _try_html_favorites(self) -> list[Post]:
        """通过 Cookie + HTML 解析获取收藏。"""
        logger.info("Falling back to HTML favorites parsing")
        post_links = await self._collect_all_fav_links()
        if not post_links:
            logger.warning("No favorite posts found via HTML")
            return []

        posts: list[Post] = []
        for link in post_links:
            try:
                post = await self._post_downloader.run(link)
                posts.append(post)
            except Exception as exc:
                logger.warning("Failed to download post %s: %s", link, exc)
        return posts

    @staticmethod
    def _build_post_url(item: dict[str, str]) -> str:
        """从 API 返回的收藏项构建文章 URL。"""
        domain = item.get("blogDomain", "")
        post_id = item.get("postId", "")
        if domain and post_id:
            return f"https://{domain}.lofter.com/post/{post_id}"
        return ""

    async def _resolve_fav_url(self) -> str:
        """解析可用的收藏页面 URL。"""
        if self._resolved_url is not None:
            return self._resolved_url
        for url in self.FAVORITE_URLS:
            try:
                logger.debug("Trying favorites URL: %s", url)
                client = await super()._get_client()  # type: ignore[misc]
                resp = await client.get(url, follow_redirects=True)
                if resp.status_code == 200 and "/post/" in resp.text:
                    self._resolved_url = url
                    logger.info("Resolved favorites URL: %s", url)
                    return url
            except Exception:
                continue
        # 使用第一个作为默认
        self._resolved_url = self.FAVORITE_URLS[0]
        return self._resolved_url

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
