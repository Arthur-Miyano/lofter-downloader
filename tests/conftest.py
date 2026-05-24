"""pytest 全局 fixtures。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from downloader.models import TaskManager


@pytest.fixture
def sample_post_html() -> str:
    """返回样本文章 HTML。"""
    fixture_path = Path(__file__).parent / "fixtures" / "post_page.html"
    if fixture_path.exists():
        return fixture_path.read_text(encoding="utf-8")
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
    </div>
</div>
</body>
</html>"""


@pytest.fixture
def mock_page() -> AsyncMock:
    """返回 mock 的 Playwright Page 对象。"""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html><body></body></html>")
    page.url = "https://example.lofter.com/post/test"
    page.evaluate = AsyncMock(return_value=None)
    page.wait_for_selector = AsyncMock()
    return page


@pytest.fixture
def mock_browser() -> MagicMock:
    """Mock BrowserManager。"""
    bm = MagicMock()
    bm.new_context = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.new_page = AsyncMock()
    mock_ctx.close = AsyncMock()
    bm.new_context.return_value = mock_ctx
    return bm


@pytest.fixture
def task_manager() -> TaskManager:
    """返回空 TaskManager。"""
    return TaskManager()
