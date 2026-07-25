"""存储模块测试。

覆盖文件名冲突处理、Markdown 转纯文本、子目录生成等。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from downloader.saver import (
    PostSaver,
    _markdown_to_plain_text,
    _sanitize_filename,
    _strip_inline_markdown,
    _unique_path,
)


class TestUniquePath:
    """唯一路径生成测试。"""

    def test_first_file_uses_clean_name(self, tmp_path: Path) -> None:
        path = _unique_path(tmp_path, "旅行", ext=".txt")
        assert path == tmp_path / "旅行.txt"

    def test_second_file_gets_numbered_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "旅行.txt").write_text("existing")
        path = _unique_path(tmp_path, "旅行", ext=".txt")
        assert path == tmp_path / "旅行 (2).txt"

    def test_third_file_gets_number_three(self, tmp_path: Path) -> None:
        (tmp_path / "旅行.txt").write_text("a")
        (tmp_path / "旅行 (2).txt").write_text("b")
        path = _unique_path(tmp_path, "旅行", ext=".txt")
        assert path == tmp_path / "旅行 (3).txt"

    def test_first_directory_uses_clean_name(self, tmp_path: Path) -> None:
        path = _unique_path(tmp_path, "旅行", is_dir=True)
        assert path == tmp_path / "旅行"

    def test_second_directory_gets_numbered_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "旅行").mkdir()
        path = _unique_path(tmp_path, "旅行", is_dir=True)
        assert path == tmp_path / "旅行 (2)"


class TestSanitizeFilename:
    """文件名清洗测试。"""

    def test_replaces_windows_illegal_chars(self) -> None:
        result = _sanitize_filename('hello<world>:test"file?')
        assert result == "hello_world__test_file_"

    def test_strips_leading_trailing_dots(self) -> None:
        result = _sanitize_filename("...hidden...")
        assert result == "hidden"

    def test_all_special_chars_sanitized_to_underscores(self) -> None:
        result = _sanitize_filename('<>?:"/\\|?*')
        assert result == "__________"

    def test_truncates_long_name(self) -> None:
        long_name = "A" * 250
        result = _sanitize_filename(long_name)
        assert len(result) <= 200


class TestMarkdownToPlainText:
    """Markdown 转纯文本测试。"""

    def test_removes_image_tags(self) -> None:
        md = "正文\n![alt](https://img.com/a.jpg)\n结尾"
        text = _markdown_to_plain_text(md)
        assert "![alt]" not in text
        assert "https://img.com" not in text
        assert "正文" in text
        assert "结尾" in text

    def test_keeps_link_text_only(self) -> None:
        md = "点击[这里](https://example.com)查看"
        text = _markdown_to_plain_text(md)
        assert text == "点击这里查看"

    def test_removes_bold_italic(self) -> None:
        md = "**粗体** 和 *斜体* 和 __粗体2__ 和 _斜体2_"
        text = _markdown_to_plain_text(md)
        assert text == "粗体 和 斜体 和 粗体2 和 斜体2"

    def test_removes_inline_code(self) -> None:
        md = "使用 `code` 示例"
        text = _markdown_to_plain_text(md)
        assert text == "使用 code 示例"

    def test_no_duplicate_text_from_nested_tags(self) -> None:
        """BS4 时代会因嵌套标签重复文本；新实现不应重复。"""
        md = "# 标题\n\n第一段内容。\n\n第二段内容。"
        text = _markdown_to_plain_text(md)
        assert text.count("第一段内容") == 1
        assert text.count("第二段内容") == 1

    def test_preserves_snake_case_identifiers(self) -> None:
        """下划线斜体不应误删 snake_case 中的下划线。"""
        md = "变量 snake_case_name 保持不变"
        text = _markdown_to_plain_text(md)
        assert "snake_case_name" in text

    def test_keeps_underscore_italic_at_word_boundaries(self) -> None:
        md = "这是 _斜体_ 文本"
        text = _markdown_to_plain_text(md)
        assert text == "这是 斜体 文本"

    def test_fenced_code_block_content_kept_fence_removed(self) -> None:
        """围栏代码块：去掉 ``` 围栏行，块内内容原样保留。"""
        md = "前文\n\n```python\nprint('hello')\nprint('world')\n```\n\n后文"
        text = _markdown_to_plain_text(md)
        assert "```" not in text
        assert "print('hello')" in text
        assert "print('world')" in text
        assert "前文" in text
        assert "后文" in text

    def test_ordered_list_numbers_preserved(self) -> None:
        """有序列表保留序号，不丢失枚举信息。"""
        md = "1. 第一项\n2. 第二项\n3. 第三项"
        text = _markdown_to_plain_text(md)
        assert "1. 第一项" in text
        assert "2. 第二项" in text
        assert "3. 第三项" in text

    def test_horizontal_rule_becomes_blank(self) -> None:
        """分隔线 --- / *** 不应字面残留。"""
        md = "上文\n\n---\n\n中缝\n\n***\n\n下文"
        text = _markdown_to_plain_text(md)
        assert "---" not in text
        assert "***" not in text
        assert "上文" in text
        assert "下文" in text

    def test_collapses_multiple_empty_lines(self) -> None:
        md = "a\n\n\n\nb"
        text = _markdown_to_plain_text(md)
        assert "\n\n\n" not in text


class TestStripInlineMarkdown:
    """行内 Markdown 语法移除测试。"""

    def test_bold_inside_italic(self) -> None:
        text = _strip_inline_markdown("***bold italic***")
        assert text == "bold italic"

    def test_strikethrough(self) -> None:
        text = _strip_inline_markdown("~~deleted~~")
        assert text == "deleted"

    def test_bare_url_angle_brackets(self) -> None:
        text = _strip_inline_markdown("访问 <https://example.com>")
        assert text == "访问 https://example.com"


class TestPostSaver:
    """PostSaver 集成测试（使用临时目录，不访问网络）。"""

    @pytest.mark.asyncio
    async def test_save_txt_uses_markdown_content(self, tmp_path: Path) -> None:
        saver = PostSaver(tmp_path)
        post = {
            "title": "测试文章",
            "author": "作者A",
            "publish_date": "2024-01-01",
            "url": "https://example.com/post/1",
            "content_markdown": "# 标题\n\n正文段落。",
            "content_html": "<h1>标题</h1><p>正文段落。</p>",
            "image_urls": [],
        }
        path = await saver.save_dict(post, fmt="txt")
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "测试文章" in content
        assert "正文段落" in content
        # 不应因 HTML 嵌套而重复
        assert content.count("正文段落") == 1
        await saver.close()

    @pytest.mark.asyncio
    async def test_save_markdown_creates_images_subdir(self, tmp_path: Path) -> None:
        saver = PostSaver(tmp_path)
        post = {
            "title": "图集",
            "author": "作者B",
            "publish_date": "2024-01-01",
            "url": "https://example.com/post/2",
            "content_markdown": "![alt](https://img.com/x.png)",
            "content_html": '<p><img src="https://img.com/x.png"></p>',
            "image_urls": [],  # 不触发网络下载
        }
        post_dir = await saver.save_dict(post, fmt="md")
        assert post_dir.exists()
        assert (post_dir / "index.md").exists()
        await saver.close()

    @pytest.mark.asyncio
    async def test_save_txt_no_duplicate_title(self, tmp_path: Path) -> None:
        """parser 回退会把标题混入 content_markdown，TXT 头部不应再重复。"""
        saver = PostSaver(tmp_path)
        post = {
            "title": "旅行的意义",
            "author": "背包客小王",
            "publish_date": "2024-03-15",
            "url": "https://example.com/post/3",
            # 模拟 parser 回退结果：正文首行已含标题
            "content_markdown": "# 旅行的意义\n\n正文第一段。",
            "content_html": "",
            "image_urls": [],
        }
        path = await saver.save_dict(post, fmt="txt")
        content = path.read_text(encoding="utf-8")
        assert content.count("旅行的意义") == 1
        assert "正文第一段" in content
        await saver.close()

    @pytest.mark.asyncio
    async def test_save_epub_produces_file_with_title(
        self, tmp_path: Path
    ) -> None:
        """EPUB 导出：mock 图片下载，断言产出 .epub 且标题正确。"""
        from ebooklib import epub

        saver = PostSaver(tmp_path)
        # mock 图片下载，避免真实网络请求
        resp = MagicMock()
        resp.headers = {"content-type": "image/png"}
        resp.content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        resp.raise_for_status = MagicMock()
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        saver._http_client = client

        post = {
            "title": "EPUB 测试文章",
            "author": "作者C",
            "publish_date": "2024-01-01",
            "url": "https://example.com/post/4",
            "content_markdown": "正文内容",
            "content_html": (
                '<p>正文内容</p><p><img src="https://img.com/a.png"></p>'
            ),
            "image_urls": ["https://img.com/a.png"],
        }
        path = await saver.save_dict(post, fmt="epub")
        assert path.exists()
        assert path.suffix == ".epub"
        assert path.stat().st_size > 0

        book = epub.read_epub(str(path))
        titles = book.get_metadata("DC", "title")
        assert titles[0][0] == "EPUB 测试文章"
        await saver.close()

    @pytest.mark.asyncio
    async def test_save_pdf_produces_nonempty_file(self, tmp_path: Path) -> None:
        """PDF 导出冒烟测试：产出非空 .pdf 文件。"""
        saver = PostSaver(tmp_path)
        post = {
            "title": "PDF 冒烟测试",
            "author": "作者D",
            "publish_date": "2024-01-01",
            "url": "https://example.com/post/5",
            "content_markdown": "# 章节\n\n正文段落。\n\n1. 第一项\n2. 第二项",
            "content_html": "",
            "image_urls": [],
        }
        path = await saver.save_dict(post, fmt="pdf")
        assert path.exists()
        assert path.suffix == ".pdf"
        assert path.stat().st_size > 0
        await saver.close()

    @pytest.mark.asyncio
    async def test_save_pdf_embeds_image(self, tmp_path: Path) -> None:
        """PDF 导出：独占一行的图片被下载并嵌入（产出含 Image XObject）。"""
        import io

        from PIL import Image

        # 生成一张真实 PNG 供 mock 下载
        buf = io.BytesIO()
        Image.new("RGB", (40, 30), (200, 100, 50)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        saver = PostSaver(tmp_path)
        resp = MagicMock()
        resp.headers = {"content-type": "image/png"}
        resp.content = png_bytes
        resp.raise_for_status = MagicMock()
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        saver._http_client = client

        post = {
            "title": "带图文章",
            "author": "作者E",
            "publish_date": "2024-01-01",
            "url": "https://example.com/post/6",
            "content_markdown": "前文段落。\n\n![插图](https://img.com/p.png)\n\n后文段落。",
            "content_html": "",
            "image_urls": ["https://img.com/p.png"],
        }
        path = await saver.save_dict(post, fmt="pdf")
        assert path.exists()
        data = path.read_bytes()
        assert b"/Subtype /Image" in data
        client.get.assert_called_once_with("https://img.com/p.png")
        await saver.close()

    @pytest.mark.asyncio
    async def test_save_pdf_image_failure_uses_placeholder(
        self, tmp_path: Path
    ) -> None:
        """PDF 导出：图片下载失败时降级为 [图片] 占位，不阻塞保存。"""
        saver = PostSaver(tmp_path)
        client = AsyncMock()
        client.get = AsyncMock(side_effect=RuntimeError("网络错误"))
        saver._http_client = client

        post = {
            "title": "裂图文章",
            "author": "作者F",
            "publish_date": "2024-01-01",
            "url": "https://example.com/post/7",
            "content_markdown": "前文。\n\n![插图](https://img.com/broken.png)\n\n后文。",
            "content_html": "",
            "image_urls": ["https://img.com/broken.png"],
        }
        path = await saver.save_dict(post, fmt="pdf")
        assert path.exists()
        assert path.stat().st_size > 0
        assert b"/Subtype /Image" not in path.read_bytes()
        await saver.close()
