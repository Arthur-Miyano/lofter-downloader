"""登录相关端点：会话状态查询、浏览器登录流程、退出清理。"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from flask import jsonify
from flask.typing import ResponseReturnValue

from config import SESSION_PATH
from downloader.auth import (
    LOGIN_TIMEOUT,
    check_login,
    clear_session,
    save_session,
    start_login,
)
from downloader.models import TaskStatus
from web import state

logger = logging.getLogger(__name__)


@state.api.route("/login/status")
def login_status() -> ResponseReturnValue:
    """检查登录状态（含会话有效性校验和登录启动进度）。"""
    return jsonify(
        logged_in=SESSION_PATH.exists(),
        user_name=state._user_name,
        login_starting=state._login_in_progress and state._login_page is None,
        login_ready=state._login_page is not None,
        login_start_error=state._login_start_error,
    )


@state.api.route("/login/start", methods=["POST"])
def login_start() -> ResponseReturnValue:
    """启动浏览器登录流程（非阻塞，立即返回）。"""
    if state.browser is None:
        return jsonify(ok=False, error="浏览器未初始化，请稍后重试"), 500

    if state._login_in_progress:
        if state._login_browser is None or not state._login_browser.is_connected():
            logger.warning("headed 浏览器已关闭，重置 _login_in_progress")
            state._login_in_progress = False
        else:
            return jsonify(ok=False, error="登录流程已在进行中，请完成当前登录"), 409

    state._login_in_progress = True
    state._login_start_error = ""

    def _on_login_timeout() -> None:
        logger.warning("登录超时（%s 秒），自动关闭 headed 浏览器", LOGIN_TIMEOUT)
        asyncio.ensure_future(_close_login_browser())

    async def _start_and_set_state() -> None:
        try:
            pw = state.browser._playwright  # noqa: SLF001
            if pw is None:
                state._login_start_error = "Playwright 未初始化"
                state._login_in_progress = False
                return
            loop = asyncio.get_event_loop()
            (
                state._login_browser,
                state._login_context,
                state._login_page,
            ) = await start_login(pw, loop, on_timeout=_on_login_timeout)
        except Exception as exc:
            state._login_start_error = str(exc) or type(exc).__name__
            state._login_in_progress = False
            logger.exception("启动登录失败")

    state.browser.submit_async(_start_and_set_state())
    return jsonify(ok=True, status="starting", message="正在启动浏览器，请稍候...")


@state.api.route("/login/check", methods=["POST"])
def login_check() -> ResponseReturnValue:
    """检查登录是否完成并保存会话。成功时取消超时计时。"""
    if state._login_page is None:
        err = "尚未启动登录流程，请先点击「启动浏览器登录」"
        return jsonify(ok=False, error=err), 400

    # 登录窗口已被用户关闭（或浏览器崩溃）：重置状态，允许重新发起登录
    if state._login_page.is_closed() or not state._login_browser.is_connected():
        _reset_login_state()
        return jsonify(ok=False, error="登录窗口已关闭，请重新点击登录"), 400

    async def _check() -> dict:
        logged_in, username = await check_login(state._login_page)
        if logged_in:
            await save_session(state._login_context)
            state._user_name = username
            await _close_login_browser()
            return {"logged_in": True, "user_name": username}
        return {"logged_in": False}

    try:
        result = state.browser.submit(_check(), timeout=30)
        if result["logged_in"]:
            state._login_in_progress = False
        user_name_val = result.get("user_name", "")
        resp: dict = {"ok": True, "logged_in": result["logged_in"]}
        if user_name_val:
            resp["user_name"] = user_name_val
        return jsonify(resp)
    except Exception:
        logger.exception("登录检查失败")
        # 检查过程中页面被关闭（如用户中途关窗）：同样重置，避免状态卡死
        if state._login_page is None or state._login_page.is_closed():
            _reset_login_state()
            return jsonify(ok=False, error="登录窗口已关闭，请重新点击登录"), 400
        return jsonify(ok=False, error="登录检查失败，请重试"), 500


def _reset_login_state() -> None:
    """重置登录流程的模块级状态（登录窗口失效时调用）。"""
    state._login_browser = None
    state._login_context = None
    state._login_page = None
    state._login_in_progress = False
    state._login_start_error = ""


@state.api.route("/login", methods=["DELETE"])
def logout() -> ResponseReturnValue:
    """清除登录会话：关闭浏览器、清空上下文、取消运行中任务。"""
    state._user_name = ""
    state._login_in_progress = False
    state._login_start_error = ""

    # 取消所有运行中的任务并清理历史
    for task in state.task_manager.list_all():
        if task.status == TaskStatus.RUNNING:
            state.task_manager.cancel(task.task_id)
    state.task_manager.clear_finished()

    # 异步清理 headed 浏览器（通过 browser 线程）
    if state._login_browser is not None:
        with contextlib.suppress(Exception):
            state.browser.submit(_close_login_browser(), timeout=10)

    # 同步清理模块级引用
    state._login_browser = None
    state._login_context = None
    state._login_page = None
    state._running_task_id = None
    state._running_list_kind = None

    clear_session()
    return jsonify(ok=True)


async def _close_login_browser() -> None:
    """关闭登录用的 headed 浏览器及上下文（在事件循环线程中调用）。"""
    try:
        if state._login_page is not None:
            await state._login_page.close()
    except Exception:
        pass
    try:
        if state._login_context is not None:
            await state._login_context.close()
    except Exception:
        pass
    try:
        if state._login_browser is not None:
            await state._login_browser.close()
    except Exception:
        pass
    state._login_browser = None
    state._login_context = None
    state._login_page = None
    logger.info("Headed 浏览器已关闭")
