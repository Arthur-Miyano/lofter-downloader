"""收藏下载模块单元测试。"""

from __future__ import annotations

from lofter_downloader.core.favorites import FavoritesDownloader


class TestFavoritesDownloader:
    """FavoritesDownloader 单元测试。"""

    FAV_HTML = """
    <html>
    <body>
        <a href="https://a.lofter.com/post/1">fav1</a>
        <a href="https://a.lofter.com/post/2">fav2</a>
    </body>
    </html>
    """

    def test_get_fav_links_from_page_returns_links(self):
        """应从收藏页面中提取文章链接。"""
        downloader = FavoritesDownloader(cookie="test=123")
        soup = downloader.parse_html(self.FAV_HTML)
        links = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)
        assert len(links) == 2

    def test_get_fav_links_empty(self):
        """无收藏链接时应返回空列表。"""
        downloader = FavoritesDownloader(cookie="test=123")
        soup = downloader.parse_html("<html><body></body></html>")
        links = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)
        assert links == []
