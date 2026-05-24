"""单篇文章下载器单元测试。"""

from __future__ import annotations

import pytest

from lofter_downloader.core.post import ParseError, PostDownloader


class TestPostDownloader:
    """PostDownloader 单元测试。"""

    async def test_extract_title(self, sample_post_html):
        """应从 HTML 中正确提取文章标题。"""
        downloader = PostDownloader()
        soup = downloader.parse_html(sample_post_html)
        title = downloader._extract_title(soup, "http://example.com")
        assert title == "旅行的意义"
        assert len(title) > 0

    async def test_extract_title_raises_when_missing(self):
        """缺少标题时应抛 ParseError。"""
        downloader = PostDownloader()
        soup = downloader.parse_html("<html><body></body></html>")
        with pytest.raises(ParseError, match="Cannot find title"):
            downloader._extract_title(soup, "http://example.com")

    async def test_extract_author(self, sample_post_html):
        """应正确提取作者名称。"""
        downloader = PostDownloader()
        soup = downloader.parse_html(sample_post_html)
        author = downloader._extract_author(soup)
        assert author == "背包客小王"

    async def test_extract_author_fallback(self):
        """缺少作者时应返回默认值。"""
        downloader = PostDownloader()
        soup = downloader.parse_html("<html><body></body></html>")
        author = downloader._extract_author(soup)
        assert author == "未知作者"

    async def test_extract_date(self, sample_post_html):
        """应正确提取发布日期。"""
        downloader = PostDownloader()
        soup = downloader.parse_html(sample_post_html)
        date = downloader._extract_date(soup)
        assert date == "2024-03-15"

    async def test_extract_content(self, sample_post_html):
        """应正确提取文章正文 HTML。"""
        downloader = PostDownloader()
        soup = downloader.parse_html(sample_post_html)
        content = downloader._extract_content(soup, "http://example.com")
        assert "<p>今天分享一次难忘的云南之旅。</p>" in content

    async def test_extract_content_raises_when_missing(self):
        """缺少正文时应抛 ParseError。"""
        downloader = PostDownloader()
        soup = downloader.parse_html("<html><body></body></html>")
        with pytest.raises(ParseError, match="Cannot find content"):
            downloader._extract_content(soup, "http://example.com")

    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            ('<img src="http://a.com/1.jpg">', ["http://a.com/1.jpg"]),
            ('<img src="http://a.com/1.jpg"><img src="http://a.com/2.png">',
             ["http://a.com/1.jpg", "http://a.com/2.png"]),
            ("<p>no image</p>", []),
            ("", []),
        ],
    )
    def test_extract_images(self, html, expected):
        """应正确提取文章中的图片 URL。"""
        assert PostDownloader._extract_images(html) == expected
