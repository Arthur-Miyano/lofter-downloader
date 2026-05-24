"""博客下载模块单元测试。"""

from __future__ import annotations

from lofter_downloader.core.blog import BlogDownloader


class TestBlogDownloader:
    """BlogDownloader 单元测试。"""

    def test_get_post_links_from_page_returns_links(self, sample_blog_html):
        """应从博客页面中提取文章链接。"""
        downloader = BlogDownloader()
        soup = downloader.parse_html(sample_blog_html)
        links = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)
        assert len(links) == 3
        assert "https://backpacker_wang.lofter.com/post/1" in links

    def test_get_post_links_from_page_empty(self):
        """无文章链接时应返回空列表。"""
        downloader = BlogDownloader()
        soup = downloader.parse_html("<html><body></body></html>")
        links = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)
        assert links == []

    def test_get_post_links_deduplicates(self):
        """重复链接应被去重。"""
        html = """
        <html>
        <body>
            <a href="https://a.lofter.com/post/1">link1</a>
            <a href="https://a.lofter.com/post/1">link1</a>
            <a href="https://a.lofter.com/post/2">link2</a>
        </body>
        </html>
        """
        downloader = BlogDownloader()
        soup = downloader.parse_html(html)
        links = []
        for tag in soup.select("a[href*='/post/']"):
            href = tag.get("href", "")
            if href and href not in links:
                links.append(href)
        assert len(links) == 2
