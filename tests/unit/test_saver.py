"""存储模块单元测试。"""

from __future__ import annotations

import pytest

from lofter_downloader.core.post import Post
from lofter_downloader.storage.saver import PostSaver


class TestPostSaver:
    """PostSaver 单元测试。"""

    async def test_save_creates_markdown_file(self, temp_download_dir):
        """保存文章时应创建 Markdown 文件。"""
        saver = PostSaver(base_dir=temp_download_dir)
        post = Post(
            url="http://example.com/post/1",
            title="测试文章",
            author="测试作者",
            publish_date="2024-01-15",
            content_html="<p>Hello</p>",
            content_markdown="Hello",
            image_urls=[],
        )
        post_dir = await saver.save(post, sub_dir="test_author")
        md_file = post_dir / "index.md"
        assert md_file.exists()
        content = md_file.read_text(encoding="utf-8")
        assert "# 测试文章" in content
        assert "测试作者" in content
        assert "Hello" in content

    async def test_save_creates_images_directory(self, temp_download_dir):
        """包含图片时应创建 images 目录。"""
        saver = PostSaver(base_dir=temp_download_dir)
        post = Post(
            url="http://example.com/post/1",
            title="带图片的文章",
            author="作者",
            publish_date="2024-01-15",
            content_html="<p>Content</p>",
            content_markdown="Content",
            image_urls=["http://example.com/img.jpg"],
        )
        post_dir = await saver.save(post, sub_dir="test")
        img_dir = post_dir / "images"
        assert img_dir.exists()

    async def test_sanitize_filename(self):
        """应清理文件名中的非法字符。"""
        assert PostSaver._sanitize_filename('file:name/test"') == "file_name_test_"
        assert PostSaver._sanitize_filename("normal.txt") == "normal.txt"

    @pytest.mark.parametrize(
        ("url", "content_type", "expected"),
        [
            ("http://a.com/img.jpg", "image/jpeg", ".jpg"),
            ("http://a.com/img", "image/png", ".png"),
            ("http://a.com/img.gif", "image/gif", ".gif"),
            ("http://a.com/img", "application/octet-stream", ".jpg"),
            ("http://a.com/img", "", ".jpg"),
        ],
    )
    def test_infer_extension(self, url, content_type, expected):
        """应正确推断文件扩展名。"""
        saver = PostSaver()
        assert saver._infer_extension(url, content_type) == expected
