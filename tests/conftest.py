"""pytest 全局 fixtures 配置。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_post_html() -> str:
    """返回样本文章 HTML。"""
    fixture_path = Path(__file__).parent / "fixtures" / "post_page.html"
    if fixture_path.exists():
        return fixture_path.read_text(encoding="utf-8")
    return _default_post_html()


@pytest.fixture
def sample_blog_html() -> str:
    """返回样本博客首页 HTML。"""
    fixture_path = Path(__file__).parent / "fixtures" / "blog_page.html"
    if fixture_path.exists():
        return fixture_path.read_text(encoding="utf-8")
    return _default_blog_html()


@pytest.fixture
def temp_download_dir(tmp_path: Path) -> Path:
    """返回临时下载目录。"""
    return tmp_path / "downloads"


def _default_post_html() -> str:
    """默认的文章样本 HTML（无 fixture 文件时使用）。"""
    return """<!DOCTYPE html>
<html>
<head><title>旅行的意义</title></head>
<body>
<div class="post">
    <h1 class="post_title">旅行的意义</h1>
    <span class="author">背包客小王</span>
    <span class="date">2024-03-15</span>
    <div class="post_content">
        <p>今天分享一次难忘的云南之旅。</p>
        <img src="https://example.com/dali.jpg" alt="大理">
        <img src="https://example.com/erhai.png" alt="洱海">
    </div>
</div>
</body>
</html>"""


def _default_blog_html() -> str:
    """默认的博客样本 HTML（无 fixture 文件时使用）。"""
    return """<!DOCTYPE html>
<html>
<head><title>背包客小王的博客</title></head>
<body>
<script>
window.globalData = {"userId": 67890, "blogName": "backpacker_wang"};
</script>
<div class="posts">
    <a href="https://backpacker_wang.lofter.com/post/1">旅行的意义</a>
    <a href="https://backpacker_wang.lofter.com/post/2">美食探店</a>
    <a href="https://backpacker_wang.lofter.com/post/3">摄影技巧分享</a>
</div>
</body>
</html>"""
