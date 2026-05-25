"""文章解析器测试。

window.__INITIAL_STATE__ 和 __NEXT_DATA__ 经 Playwright 诊断确认
在实际 LOFTER 页面中不存在，相关策略和测试已移除。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from downloader.parser import _try_html


class TestTryHtml:
    """BS4 HTML 解析测试。"""

    def test_extract_from_html(self, sample_post_html: str) -> None:
        result = _try_html(sample_post_html, "https://test.lofter.com/post/1")
        assert result is not None
        assert result["title"] == "旅行的意义"
        assert result["author"] == "背包客小王"
        assert result["publish_date"] == "2024-03-15"
        assert "云南之旅" in result["content_html"]

    def test_extract_images_from_html(self, sample_post_html: str) -> None:
        result = _try_html(sample_post_html, "https://test.lofter.com/post/1")
        assert result is not None
        assert len(result["image_urls"]) == 1
        assert "dali.jpg" in result["image_urls"][0]

    def test_empty_html_returns_none(self) -> None:
        result = _try_html("<html><body></body></html>", "url")
        assert result is None


@pytest.mark.asyncio
class TestExtractPost:
    """extract_post 集成测试。"""

    async def test_html_fallback(self, mock_page: AsyncMock) -> None:
        from downloader.parser import extract_post

        mock_page.content = AsyncMock(
            return_value="""<html><body>
                <h1>标题</h1>
                <span class="author">作者名</span>
                <span class="date">2025-04-01</span>
                <article><p>正文内容</p></article>
            </body></html>"""
        )

        result = await extract_post(mock_page, "https://test.lofter.com/post/1")
        assert result is not None
        assert result["title"] == "标题"
        assert result["author"] == "作者名"

    async def test_all_strategies_fail_raises_parse_error(
        self, mock_page: AsyncMock,
    ) -> None:
        """无法提取内容时抛出 ParseError。"""
        from downloader.exceptions import ParseError
        from downloader.parser import extract_post

        mock_page.content = AsyncMock(return_value="<html><body></body></html>")

        with pytest.raises(ParseError):
            await extract_post(mock_page, "https://test.lofter.com/post/1")

    async def test_no_image_article(self, mock_page: AsyncMock) -> None:
        """无图片文章正常返回，image_urls 为空列表。"""
        from downloader.parser import extract_post

        mock_page.content = AsyncMock(
            return_value="""<html><body>
                <h1>纯文字文章</h1>
                <article><p>只有文字，没有图片</p></article>
            </body></html>"""
        )

        result = await extract_post(mock_page, "https://test.lofter.com/post/1")
        assert result is not None
        assert result["image_urls"] == []

    async def test_multi_image_article(self, mock_page: AsyncMock) -> None:
        """多图片文章提取所有图片 URL。"""
        from downloader.parser import extract_post

        content = (
            '<img src="https://img.com/a.jpg">'
            '<img src="https://img.com/b.jpg">'
            '<img src="https://img.com/c.png">'
        )
        mock_page.content = AsyncMock(
            return_value=f"""<html><body>
                <h1>多图文章</h1>
                <article>{content}</article>
            </body></html>"""
        )

        result = await extract_post(mock_page, "https://test.lofter.com/post/1")
        assert result is not None
        assert len(result["image_urls"]) == 3


class TestSanitizeFilename:
    """文件名清洗测试。"""

    def test_replaces_windows_illegal_chars(self) -> None:
        from downloader.saver import _sanitize_filename

        result = _sanitize_filename('hello<world>:test"file?')
        assert result == "hello_world__test_file_"

    def test_strips_leading_trailing_dots(self) -> None:
        from downloader.saver import _sanitize_filename

        result = _sanitize_filename("...hidden...")
        assert result == "hidden"

    def test_all_special_chars_gives_untitled(self) -> None:
        from downloader.saver import _sanitize_filename

        result = _sanitize_filename('<>:"/\\|?*')
        assert result == "_________"

    def test_truncates_long_name(self) -> None:
        from downloader.saver import _sanitize_filename

        long_name = "A" * 250
        result = _sanitize_filename(long_name)
        assert len(result) <= 200
