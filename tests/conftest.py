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
<head><title>测试文章</title></head>
<body>
<div class="post">
    <h1 class="post_title">我的旅行日记</h1>
    <span class="author">旅行者小明</span>
    <span class="date">2024-01-15</span>
    <div class="post_content">
        <p>今天去了一个美丽的地方。</p>
        <img src="http://example.com/image1.jpg" alt="photo1">
        <img src="http://example.com/image2.png" alt="photo2">
    </div>
</div>
</body>
</html>"""


def _default_blog_html() -> str:
    """默认的博客样本 HTML（无 fixture 文件时使用）。"""
    return """<!DOCTYPE html>
<html>
<head><title>旅行者小明的博客</title></head>
<body>
<script>
window.globalData = {"userId": 12345, "blogName": "traveler_xiao"};
</script>
<div class="posts">
    <a href="https://traveler.lofter.com/post/1">第一篇文章</a>
    <a href="https://traveler.lofter.com/post/2">第二篇文章</a>
</div>
</body>
</html>"""
