"""端到端测试——完整下载流水线。

注意：这些测试会发起真实网络请求，默认被跳过。
使用 `pytest --run-e2e` 运行。
"""

from __future__ import annotations

import pytest

from lofter_downloader.core.post import PostDownloader
from lofter_downloader.storage.saver import PostSaver


@pytest.mark.e2e
@pytest.mark.skip(reason="需要真实网络连接，手动运行")
class TestFullPipeline:
    """完整下载流水线 E2E 测试。"""

    async def test_download_single_post(self, temp_download_dir):
        """应能下载并保存一篇真实文章。"""
        url = "https://example.lofter.com/post/1_1"
        downloader = PostDownloader()
        saver = PostSaver(base_dir=temp_download_dir)

        post = await downloader.run(url)
        assert post.title
        assert post.author
        assert post.content_markdown

        post_dir = await saver.save(post, sub_dir="e2e_test")
        md_file = post_dir / "index.md"
        assert md_file.exists()
        assert md_file.read_text(encoding="utf-8")
