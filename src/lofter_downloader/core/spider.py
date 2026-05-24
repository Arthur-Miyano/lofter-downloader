"""爬虫基类。

封装统一的异步 HTTP 请求、重试、限速和 HTML 解析逻辑。
所有具体爬虫（文章、博客、合集等）继承此类。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

import httpx
from bs4 import BeautifulSoup

from lofter_downloader.config import settings
from lofter_downloader.utils.exceptions import (
    LoginRequiredError,
    NetworkError,
    ParseError,
)
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class Spider(ABC):
    """爬虫基类。

    提供带重试和限速的异步 HTTP 请求，以及 HTML 解析工具方法。
    子类需实现 :meth:`run` 方法。
    """

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建异步 HTTP 客户端（含 Cookie 支持）。"""
        if self._client is None:
            headers: dict[str, str] = {
                "User-Agent": self.USER_AGENT,
                "Referer": "https://www.lofter.com/",
            }
            if settings.cookie:
                logger.debug(
                    "Using cookie (%d chars): %s...",
                    len(settings.cookie),
                    settings.cookie[:80],
                )
                headers["Cookie"] = settings.cookie
            self._client = httpx.AsyncClient(
                timeout=settings.request_timeout,
                headers=headers,
            )
        return self._client

    async def fetch(self, url: str) -> str:
        """发送 GET 请求，带重试、限速和登录检测。

        Parameters
        ----------
        url : str
            目标 URL

        Returns
        -------
        str
            响应文本

        Raises
        ------
        NetworkError
            超过最大重试次数后仍失败
        LoginRequiredError
            检测到被重定向到登录页时抛出
        """
        client = await self._get_client()
        last_exc: Exception | None = None

        for attempt in range(1, settings.max_retries + 1):
            try:
                async with self._semaphore:
                    logger.debug(
                        "Fetching [%d/%d]: %s", attempt, settings.max_retries, url
                    )
                    resp = await client.get(url, follow_redirects=True)
                    resp.raise_for_status()
                    self._check_login_page(resp, url)
                    return resp.text

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.warning("HTTP %d: %s", exc.response.status_code, url)
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning("Request failed: %s - %s", url, exc)

            if attempt < settings.max_retries:
                await asyncio.sleep(settings.request_interval * attempt)

        raise NetworkError(
            f"Failed after {settings.max_retries} attempts: {url}",
        ) from last_exc

    @staticmethod
    def _check_login_page(resp: httpx.Response, url: str) -> None:
        """检测是否被重定向到 LOFTER 登录页面。

        通过检查响应 URL 判断，避免误判 SPA 页面。

        Raises
        ------
        LoginRequiredError
            检测到被重定向到登录页时抛出
        """
        final_url = str(resp.url)
        if "/front/login" in final_url:
            logger.warning(
                "Request to %s was redirected to login page. Cookie may be invalid.",
                url,
            )
            raise LoginRequiredError(
                "LOFTER 需要登录才能访问，请先导入有效的 Cookie"
            )

    @staticmethod
    def parse_html(html: str) -> BeautifulSoup:
        """将 HTML 字符串解析为 BeautifulSoup 对象。

        Parameters
        ----------
        html : str
            HTML 文本

        Returns
        -------
        BeautifulSoup
            解析后的对象
        """
        return BeautifulSoup(html, "lxml")

    @staticmethod
    def _raise_if_none(tag: Any, name: str, url: str) -> None:
        """如果标签为 None，抛出 ParseError。"""
        if tag is None:
            raise ParseError(f"Cannot find {name} in page: {url}")

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """子类实现具体的爬取逻辑。"""

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
