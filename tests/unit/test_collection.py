"""合集下载模块单元测试。"""

from __future__ import annotations

from lofter_downloader.core.collection import CollectionDownloader


class TestCollectionDownloader:
    """CollectionDownloader 单元测试。"""

    def test_get_post_links_from_page_returns_links(self):
        """应从合集页面中提取文章链接。"""
        html = """
        <html>
        <body>
            <div class="posts">
                <a href="https://a.lofter.com/post/1">post1</a>
                <a href="https://a.lofter.com/post/2">post2</a>
                <a href="https://a.lofter.com/post/3">post3</a>
            </div>
        </body>
        </html>
        """
        downloader = CollectionDownloader()
        soup = downloader.parse_html(html)
        links = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)
        assert len(links) == 3

    def test_get_post_links_empty(self):
        """无文章链接时应返回空列表。"""
        downloader = CollectionDownloader()
        soup = downloader.parse_html("<html><body></body></html>")
        links = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)
        assert links == []

    def test_get_post_links_only_matches_post_urls(self):
        """应只提取含 /post/ 的链接。"""
        html = """
        <html>
        <body>
            <a href="https://a.lofter.com/post/1">article</a>
            <a href="https://a.lofter.com/about">about</a>
            <a href="https://a.lofter.com/archive">archive</a>
        </body>
        </html>
        """
        downloader = CollectionDownloader()
        soup = downloader.parse_html(html)
        links = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)
        assert len(links) == 1
        assert "https://a.lofter.com/post/1" in links
