"""Playwright 浏览器管理器。

后台守护线程运行独立的 asyncio 事件循环，Flask 请求线程通过
asyncio.run_coroutine_threadsafe 提交协程。
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from downloader.exceptions import LofterError

logger = logging.getLogger(__name__)


class BrowserManager:
    """单 Chromium 实例管理器。

    后台线程运行 asyncio 事件循环。通过 submit() 提交短操作（阻塞等待结果），
    通过 submit_async() 提交长任务（立即返回）。
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._ready = threading.Event()
        self._atexit_registered = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动浏览器事件循环线程。"""

        def _run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._init_browser())
            self._ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=30):
            self.stop()
            raise LofterError(
                "BrowserManager 启动超时，请检查 Playwright/Chromium 是否安装"
            )

        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True
        logger.info("BrowserManager started (headless=%s)", self._headless)

    def stop(self) -> None:
        """关闭浏览器和事件循环（atexit 自动调用）。"""
        if self._loop is None or not self._loop.is_running():
            return

        async def _shutdown() -> None:
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=10)
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("BrowserManager shut down")

    async def _init_browser(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        logger.info("Chromium launched (headless=%s)", self._headless)

    # ------------------------------------------------------------------
    # 线程安全提交
    # ------------------------------------------------------------------

    def submit(self, coro: Any, timeout: int = 60) -> Any:
        """提交协程到浏览器线程，阻塞等待结果。

        用于登录检查等短操作。
        """
        if self._loop is None or not self._loop.is_running():
            raise LofterError("浏览器事件循环未运行")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def submit_async(self, coro: Any) -> asyncio.Future:
        """提交协程到浏览器线程，返回 Future（不阻塞）。

        用于下载等长时间任务。调用方可通过 Future 取消任务。
        """
        if self._loop is None or not self._loop.is_running():
            raise LofterError("浏览器事件循环未运行")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ------------------------------------------------------------------
    # BrowserContext 管理
    # ------------------------------------------------------------------

    async def new_context(self, storage_state: str | None = None) -> BrowserContext:
        """创建 BrowserContext，可选加载 storageState。"""
        if self._browser is None:
            raise LofterError("浏览器未初始化")
        context = await self._browser.new_context(
            storage_state=storage_state,
            locale="zh-CN",
        )
        return context

    async def launch_headed(self) -> tuple[Browser, BrowserContext, Page]:
        """临时启动 headed 浏览器用于登录。

        返回 (browser, context, page)，调用者负责在用完后
        关闭 browser。
        """
        if self._playwright is None:
            raise LofterError("Playwright 未初始化")
        headed = await self._playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await headed.new_context(locale="zh-CN")
        page = await context.new_page()
        logger.info("Headed browser launched for login")
        return headed, context, page
