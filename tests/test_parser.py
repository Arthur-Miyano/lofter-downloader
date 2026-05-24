"""文章解析器测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from downloader.parser import (
    _parse_initial_state,
    _parse_next_data,
    _try_html,
)


class TestParseInitialState:
    """__INITIAL_STATE__ 解析测试。"""

    def test_parse_basic_post(self) -> None:
        data = {
            "post": {
                "title": "测试文章",
                "content": "<p>正文</p>",
                "blogName": "test_author",
                "date": "2025-01-01",
                "author": {"name": "作者名"},
            }
        }
        result = _parse_initial_state(data, "https://test.lofter.com/post/test")
        assert result is not None
        assert result["title"] == "测试文章"
        assert result["author"] == "作者名"
        assert result["publish_date"] == "2025-01-01"
        assert result["content_html"] == "<p>正文</p>"

    def test_parse_with_article_key(self) -> None:
        data = {"article": {"title": "文章", "content": "<p>内容</p>"}}
        result = _parse_initial_state(data, "https://test.lofter.com/post/test")
        assert result is not None
        assert result["title"] == "文章"

    def test_parse_extracts_images(self) -> None:
        data = {
            "post": {
                "title": "图文",
                "content": '<img src="https://img.com/a.jpg"><img src="https://img.com/b.png">',
            }
        }
        result = _parse_initial_state(data, "https://test.lofter.com/post/test")
        assert result is not None
        assert len(result["image_urls"]) == 2
        assert "https://img.com/a.jpg" in result["image_urls"]

    def test_parse_returns_none_for_empty(self) -> None:
        assert _parse_initial_state({}, "url") is None
        assert _parse_initial_state({"post": {}}, "url") is None


class TestParseNextData:
    """__NEXT_DATA__ 解析测试。"""

    def test_parse_basic(self) -> None:
        data = {
            "props": {
                "pageProps": {
                    "post": {
                        "title": "Next 文章",
                        "content": "<p>内容</p>",
                        "blogName": "author1",
                        "date": "2025-02-01",
                    }
                }
            }
        }
        result = _parse_next_data(data, "url")
        assert result is not None
        assert result["title"] == "Next 文章"
        assert result["author"] == "author1"


class TestTryHtml:
    """BS4 HTML 降级解析测试。"""

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

    async def test_strategy1_initial_state(self, mock_page: AsyncMock) -> None:
        from downloader.parser import extract_post

        state_data = {
            "post": {
                "title": "JS 文章",
                "content": "<p>test</p>",
                "blogName": "js_author",
                "date": "2025-03-01",
            }
        }
        mock_page.evaluate = AsyncMock(return_value=state_data)

        result = await extract_post(mock_page, "https://test.lofter.com/post/1")
        assert result is not None
        assert result["title"] == "JS 文章"

    async def test_strategy3_fallback(self, mock_page: AsyncMock) -> None:
        from downloader.parser import extract_post

        mock_page.evaluate = AsyncMock(return_value=None)
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
        """所有策略失败时抛出 ParseError。"""
        from downloader.exceptions import ParseError
        from downloader.parser import extract_post

        mock_page.evaluate = AsyncMock(return_value=None)
        mock_page.content = AsyncMock(return_value="<html><body></body></html>")

        with pytest.raises(ParseError):
            await extract_post(mock_page, "https://test.lofter.com/post/1")

    async def test_no_image_article(self, mock_page: AsyncMock) -> None:
        """无图片文章正常返回，image_urls 为空列表。"""
        from downloader.parser import extract_post

        state_data = {
            "post": {
                "title": "纯文字文章",
                "content": "<p>只有文字，没有图片</p>",
                "blogName": "author1",
                "date": "2025-05-01",
            }
        }
        mock_page.evaluate = AsyncMock(return_value=state_data)

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
        state_data = {
            "post": {
                "title": "多图文章",
                "content": content,
                "blogName": "photographer",
                "date": "2025-05-01",
            }
        }
        mock_page.evaluate = AsyncMock(return_value=state_data)

        result = await extract_post(mock_page, "https://test.lofter.com/post/1")
        assert result is not None
        assert len(result["image_urls"]) == 3


class TestSanitizeFilename:
    """文件名清洗测试。"""

    def test_replaces_windows_illegal_chars(self) -> None:
        from downloader.saver import _sanitize_filename

        result = _sanitize_filename('hello<world>:test"file?')
        # > 和 : 相邻，各替换为 _
        assert result == "hello_world__test_file_"

    def test_strips_leading_trailing_dots(self) -> None:
        from downloader.saver import _sanitize_filename

        result = _sanitize_filename("...hidden...")
        assert result == "hidden"

    def test_all_special_chars_gives_untitled(self) -> None:
        from downloader.saver import _sanitize_filename

        result = _sanitize_filename('<>:"/\\|?*')
        # 全特殊字符被替换为下划线，_make_post_dir 会检测空名并降级为 untitled
        assert result == "_________"

    def test_truncates_long_name(self) -> None:
        from downloader.saver import _sanitize_filename

        long_name = "A" * 250
        result = _sanitize_filename(long_name)
        assert len(result) <= 200
