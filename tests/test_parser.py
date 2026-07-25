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
        self,
        mock_page: AsyncMock,
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


class TestCleanContentMarkdown:
    """正文清洗：剔除 LOFTER 模板带入的首尾非文章内容。"""

    def test_strips_blog_header_and_footer_junk(self) -> None:
        """还原真实污染样本（旧版博客主题）：头像/博客名/导航/日期/
        重复标题在头部，标签/热度/评论/上下篇/版权在尾部。"""
        from downloader.parser import _clean_content_markdown

        polluted = (
            "[![](https://avaimg.lf127.net/img/abc.jpg?thumbnail=128)]"
            "(/)\n\n"
            "# [临渊不可退\\_](/)\n\n"
            "信口开河。\n\n"
            "* [Elegna](https://elegna.lofter.com/mylofteruapp)\n"
            "* [我们的小秘密](https://www.lofter.com/message/elegna)\n"
            "* [归档](https://elegna.lofter.com/view)\n\n"
            "## [2022.08.26](https://elegna.lofter.com/post/465cc3)\n\n"
            "## [【新志】无人街区](https://elegna.lofter.com/post/465cc3)\n\n"
            "> 《逆式童话》解禁文NO.1。\n\n"
            "「1」\n\n"
            + ("夏季铺天盖地袭来的热浪带来的不止是青春的气息，"
               "还有无可抑制的烦躁情绪，故事从这里正式开始。")
            + "\n\n"
            + ("他冲回了自己的卧室，上锁，又抵在门上。他哈哈大笑。"
               "为自己，为她。可总还有时间，就算可能正处于倒计时。")
            + "\n\n-FIN-\n\n"
            "[#柯哀](https://elegna.lofter.com/tag/柯哀)"
            "[#新志](https://elegna.lofter.com/tag/新志)\n\n"
            "[热度 500](https://elegna.lofter.com/post/465cc3)\n"
            "[评论 13](https://elegna.lofter.com/post/465cc3)\n\n"
            "### 评论(13)\n\n### 热度(500)\n\n"
            "1. ![](//l.bst.126.net/rsc/img/icon_collection.png)\n\n"
            "   共34人收藏了此文字\n"
            "2. [怀仙](//yunjing43518.lofter.com/) 很喜欢此文字\n\n"
            "3. 加载中...\n4. [查看更多](#)\n\n"
            "只展示最近三个月数据\n\n"
            "[«**上一篇**](https://elegna.lofter.com/post/aaa)\n"
            "[**下一篇»**](https://elegna.lofter.com/post/bbb)\n\n"
            "©[临渊不可退\\_](https://elegna.lofter.com/) | "
            "Powered by [LOFTER](//www.lofter.com)"
        )
        cleaned = _clean_content_markdown(polluted, "【新志】无人街区")

        # 头部杂质全部剔除
        assert "avaimg" not in cleaned
        assert "mylofteruapp" not in cleaned
        assert "2022.08.26" not in cleaned
        # 尾部杂质全部剔除
        assert "/tag/" not in cleaned
        assert "热度" not in cleaned
        assert "评论(13)" not in cleaned
        assert "共34人收藏" not in cleaned
        assert "加载中" not in cleaned
        assert "查看更多" not in cleaned
        assert "只展示最近" not in cleaned
        assert "上一篇" not in cleaned
        assert "下一篇" not in cleaned
        assert "Powered by" not in cleaned
        # 正文完整保留（含首尾与章节标记、作者题记）
        assert "夏季铺天盖地" in cleaned
        assert "他冲回了自己的卧室" in cleaned
        assert "「1」" in cleaned
        assert "-FIN-" in cleaned
        assert "逆式童话" in cleaned

    def test_photo_only_post_not_touched(self) -> None:
        """纯图集文章（无真实段落）不清洗，避免误删正文图片。"""
        from downloader.parser import _clean_content_markdown

        md = "![图1](https://img.com/1.jpg)\n\n![图2](https://img.com/2.jpg)"
        assert _clean_content_markdown(md, "图集") == md

    def test_clean_article_unchanged(self) -> None:
        """干净文章幂等：标题、正文、列表、引用均不受影响。"""
        from downloader.parser import _clean_content_markdown

        md = (
            "# 第一章 开端\n\n"
            "这是一段足够长的正文段落，用来模拟真实文章的开头部分，"
            "包含完整的一句话和丰富的细节描述。\n\n"
            "- 无序列表项一\n- 无序列表项二\n\n"
            "1. 有序列表项一\n2. 有序列表项二\n\n"
            "> 引用的一段话。\n\n"
            "结尾段落同样足够长，确保最后一个真实段落在文章末尾，"
            "这样尾部区域为空，不应触发任何删除行为。"
        )
        assert _clean_content_markdown(md, "某篇无关标题") == md

    def test_duplicate_plain_title_heading_removed(self) -> None:
        """正文中与文章标题重复的纯文本标题行被剔除。"""
        from downloader.parser import _clean_content_markdown

        md = (
            "# 无人街区\n\n"
            "正文从这里开始，这是一段足够长的真实段落，"
            "包含完整的故事内容和丰富的细节描述信息。"
        )
        cleaned = _clean_content_markdown(md, "无人街区")
        assert cleaned.count("无人街区") == 0
        assert "正文从这里开始" in cleaned

    def test_content_link_paragraph_preserved(self) -> None:
        """正文中间出现的链接/标签样式行不动（只清洗首尾区域）。"""
        from downloader.parser import _clean_content_markdown

        md = (
            "第一段足够长的正文内容，用来占位成为首个真实段落，"
            "包含完整的一句话和丰富的细节描述。\n\n"
            "作者自己写的 [参考链接](https://example.com) 在中间。\n\n"
            "第二段足够长的正文内容，同样占位成为真实段落，"
            "确保中间区域的链接行不会被误删，清洗只作用于首尾。"
        )
        cleaned = _clean_content_markdown(md, "标题")
        assert "参考链接" in cleaned

    def test_leading_content_image_preserved(self) -> None:
        """正文首图（裸图、无头像特征）不能被当头像删掉。"""
        from downloader.parser import _clean_content_markdown

        md = (
            "![正文首图](https://img.com/photo1.jpg)\n\n"
            "图片说明文字足够长，构成首个真实段落，"
            "讲述这张照片背后的故事和拍摄时的种种细节。"
        )
        cleaned = _clean_content_markdown(md, "游记")
        assert "photo1.jpg" in cleaned

    def test_trailing_content_image_preserved(self) -> None:
        """文末插图（无图标特征）不能被当页脚图标删掉。"""
        from downloader.parser import _clean_content_markdown

        md = (
            "文章最后一段足够长的正文内容，作为收尾总结，"
            "包含完整的一句话和丰富的细节描述信息。\n\n"
            "![文末插图](https://img.com/final_photo.jpg)"
        )
        cleaned = _clean_content_markdown(md, "游记")
        assert "final_photo.jpg" in cleaned

    def test_author_footnotes_preserved(self) -> None:
        """作者文末的编号脚注（无热度/评论区上下文）不能被删。"""
        from downloader.parser import _clean_content_markdown

        md = (
            "正文最后一段足够长的内容，作为文章收尾，"
            "包含完整的一句话和丰富的细节描述信息。\n\n"
            "1. 此处引用自某某文献\n"
            "2. 作者补充说明"
        )
        cleaned = _clean_content_markdown(md, "论文")
        assert "此处引用自某某文献" in cleaned
        assert "作者补充说明" in cleaned
