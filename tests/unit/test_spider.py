"""爬虫基类单元测试。"""

from __future__ import annotations

import pytest
from lofter_downloader.core.spider import Spider
from lofter_downloader.utils.exceptions import NetworkError


class ConcreteSpider(Spider):
    """测试用具体爬虫实现。"""

    async def run(self, url: str) -> str:  # type: ignore[override]
        return await self.fetch(url)


class TestSpider:
    """Spider 基类测试。"""

    async def test_parse_html_returns_soup(self):
        """parse_html 应返回 BeautifulSoup 对象。"""
        spider = ConcreteSpider()
        soup = spider.parse_html("<html><body><p>Hello</p></body></html>")
        assert soup is not None
        assert soup.select_one("p").get_text() == "Hello"

    async def test_fetch_raises_on_invalid_url(self):
        """无效 URL 应抛出 NetworkError。"""
        spider = ConcreteSpider()
        with pytest.raises(NetworkError):
            await spider.fetch("http://invalid-url-12345.com/")
        await spider.close()

    async def test_parse_html_empty_string(self):
        """空字符串也应正常解析。"""
        spider = ConcreteSpider()
        soup = spider.parse_html("")
        assert soup is not None

    async def test_parse_html_malformed(self):
        """非标准 HTML 不应抛出异常。"""
        spider = ConcreteSpider()
        soup = spider.parse_html("<p>unclosed")
        assert soup is not None
        assert soup.select_one("p").get_text() == "unclosed"
