"""登录管理。

在 Playwright 浏览器中完成 LOFTER 手动登录，持久化 storageState。
含：会话有效性校验、登录超时计时、退出清理。

LOFTER 用户信息来源：window.userSignedIn（www.lofter.com 首页）。
window.__INITIAL_STATE__ 在实际页面中不存在，不可用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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
    """
    browser = await playwright.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(locale="zh-CN")
    page = await context.new_page()
    await page.goto(URL_LOGIN, wait_until="domcontentloaded")
    logger.info("登录页面已在可见浏览器中打开")

    if on_timeout:
        loop.call_later(LOGIN_TIMEOUT, on_timeout)

    return browser, context, page


async def check_login(page: Page) -> tuple[bool, str]:
    """检查用户是否已完成登录，登录成功时提取用户名。

    返回 (is_logged_in, username)。
    主策略：window.userSignedIn（LOFTER 首页实际存在的全局变量）。
    备用：URL 提取 + 设置页提取。
    """
    await page.goto(URL_HOME, wait_until="domcontentloaded", timeout=15000)
    current_url = page.url
    logger.info("登录检查 URL: %s", current_url)

    if "/front/login" in current_url:
        return False, ""

    # 策略 1: window.userSignedIn（实际存在的数据源）
    username = await _extract_from_user_signed_in(page)
    if username:
        logger.info("从 userSignedIn 提取用户名: %s", username)
        return True, username

    # 策略 2: URL 提取
    username = _extract_username_from_url(current_url)
    if username:
        logger.info("从 URL 提取用户名: %s", username)
        return True, username

    # 策略 3: 设置页
    logger.debug("尝试从设置页提取用户名")
    try:
        await page.goto(
            "https://www.lofter.com/settings",
            wait_until="domcontentloaded", timeout=10000,
        )
        if "/front/login" not in page.url:
            username = (
                await _extract_from_user_signed_in(page)
                or _extract_username_from_url(page.url)
                or await _extract_from_settings_page(page)
            )
    except Exception as exc:
        logger.debug("设置页提取用户名失败: %s", exc)

    logger.info("登录成功，用户名: %s", username or "(空)")
    return True, username


async def verify_session(context: BrowserContext) -> bool:
    """验证 storageState 是否仍然有效。"""
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


async def _extract_from_user_signed_in(page: Page) -> str:
    """从 window.userSignedIn 提取用户名（实际存在的 LOFTER 前端变量）。"""
    try:
        result = await page.evaluate("""() => {
            const u = window.userSignedIn;
            if (!u) return '';
            return u.blogName || u.blogNickName || '';
        }""")
        if result and isinstance(result, str) and result.strip():
            return result.strip()
    except Exception as exc:
        logger.debug("userSignedIn 提取失败: %s", exc)
    return ""


async def _extract_from_settings_page(page: Page) -> str:
    """从设置页提取用户名（DOM 文本匹配）。"""
    try:
        # 设置页上有用户昵称
        result = await page.evaluate("""() => {
            const s = '[class*=name],[class*=nick],[class*=user]';
            const els = document.querySelectorAll(s);
            for (const el of els) {
                const t = el.textContent.trim();
                if (t && t.length > 1 && t.length < 50 &&
                    !/登录|注册|LOFTER|乐乎|设置|修改|保存|退出/.test(t)) {
                    return t;
                }
            }
            return '';
        }""")
        if result and isinstance(result, str) and result.strip():
            return result.strip()
    except Exception as exc:
        logger.debug("设置页提取失败: %s", exc)
    return ""


def _extract_username_from_url(url: str) -> str:
    """从 URL 提取用户名（https://{name}.lofter.com/...）。"""
    m = re.match(r"https?://([^.]+)\.lofter\.com", url)
    if m and m.group(1) not in ("www", "tag", "collection", "post", "front"):
        return m.group(1)
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
