"""登录管理。

在 Playwright 浏览器中完成 LOFTER 手动登录，持久化 storageState。
含：会话有效性校验、登录超时计时、退出清理。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page

from config import SESSION_PATH

logger = logging.getLogger(__name__)

URL_LOGIN = "https://www.lofter.com/front/login"
URL_HOME = "https://www.lofter.com/"
LOGIN_TIMEOUT = 300  # 登录窗口 5 分钟超时


def load_storage_state(path: str | None = None) -> dict | None:
    """读取已保存的 storageState 文件。"""
    p = path or str(SESSION_PATH)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


async def start_login(
    playwright: Any,
    loop: asyncio.AbstractEventLoop,
    on_timeout: object = None,
) -> tuple[Browser, BrowserContext, Page]:
    """启动 headed 浏览器并导航到 LOFTER 登录页。

    返回 (browser, context, page)。登录超时后自动关闭浏览器。

    参数:
        playwright: Playwright 实例。
        loop: 浏览器事件循环，用于注册超时回调。
        on_timeout: 超时后调用的回调（在事件循环线程中执行）。
    """
    browser = await playwright.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(locale="zh-CN")
    page = await context.new_page()
    await page.goto(URL_LOGIN, wait_until="domcontentloaded")
    logger.info("登录页面已在可见浏览器中打开")

    # 注册 5 分钟超时回调，自动关闭 headed 浏览器
    if on_timeout:
        loop.call_later(LOGIN_TIMEOUT, on_timeout)

    return browser, context, page


async def check_login(page: Page) -> tuple[bool, str]:
    """检查用户是否已完成登录，登录成功时提取用户名。

    返回 (is_logged_in, username)。
    """
    await page.goto(URL_HOME, wait_until="domcontentloaded", timeout=15000)
    current_url = page.url
    logger.info("登录检查 URL: %s", current_url)

    if "/front/login" in current_url:
        return False, ""

    username = await _extract_username(page)
    logger.info("登录成功，用户名: %s", username)
    return True, username


async def verify_session(context: BrowserContext) -> bool:
    """验证 storageState 是否仍然有效。

    访问 LOFTER 首页，检测是否被重定向到登录页。
    """
    try:
        page = await context.new_page()
        try:
            await page.goto(URL_HOME, wait_until="domcontentloaded", timeout=15000)
            return "/front/login" not in page.url
        finally:
            await page.close()
    except Exception as exc:
        logger.warning("会话验证失败: %s", exc)
        return False


async def _extract_username(page: Page) -> str:
    """从登录后的页面提取用户名。"""
    try:
        result: Any = await page.evaluate(
            "() => {"
            "  const s = window.__INITIAL_STATE__;"
            "  if (s && s.user)"
            "    return s.user.blogName || s.user.nickName || '';"
            "  const g = window.globalData;"
            "  if (g) return g.blogName || g.nickName || '';"
            "  const el = document.querySelector("
            "    '.blogname,.username,.nickname,[class*=blogname]');"
            "  return el ? el.textContent.trim() : '';"
            "}"
        )
        if result and isinstance(result, str):
            return result.strip()
    except Exception as exc:
        logger.debug("提取用户名失败: %s", exc)
    return ""


async def save_session(context: BrowserContext, path: str | None = None) -> None:
    """持久化 BrowserContext 的 storageState 到文件。"""
    p = path or str(SESSION_PATH)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    state = await context.storage_state()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f)
    logger.info("登录会话已保存到 %s", p)


def clear_session(path: str | None = None) -> None:
    """删除登录会话文件。"""
    p = path or str(SESSION_PATH)
    if os.path.exists(p):
        os.remove(p)
        logger.info("登录会话已清除")
