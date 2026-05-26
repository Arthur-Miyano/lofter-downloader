"""Flask API 路由。

所有 REST 端点：登录管理、下载任务创建/查询/取消。
含会话校验、登录超时、退出清理、并发控制。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

from flask import Blueprint, jsonify, request

from config import DOWNLOAD_DIR, SESSION_PATH
from downloader.auth import (
    LOGIN_TIMEOUT,
    check_login,
    clear_session,
    load_storage_state,
    save_session,
    start_login,
    verify_session,
)
from downloader.models import TaskManager, TaskStatus

logger = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")

# ------------------------------------------------------------------
# 模块级共享状态（单用户模式）
# ------------------------------------------------------------------

task_manager = TaskManager()
browser = None  # 由 app.py 注入 BrowserManager 实例

# 登录状态
_login_browser = None   # headed 浏览器实例
_login_context = None   # headed BrowserContext
_login_page = None      # headed Page
_login_in_progress = False  # 防止重复启动登录
_user_name = ""

# 下载并发控制
_running_task_id: str | None = None  # 当前运行中的任务 ID
_task_lock = threading.Lock()


# ------------------------------------------------------------------
# 登录
# ------------------------------------------------------------------


@api.route("/login/status")
def login_status():
    """检查登录状态（含会话有效性校验）。"""
    return jsonify(
        logged_in=SESSION_PATH.exists(),
        user_name=_user_name,
    )


@api.route("/login/start", methods=["POST"])
def login_start():
    """启动浏览器登录流程，含 5 分钟超时自动关闭。"""
    global _login_browser, _login_context, _login_page, _login_in_progress

    if browser is None:
        return jsonify(ok=False, error="浏览器未初始化，请稍后重试"), 500

    if _login_in_progress:
        # 如果 headed 浏览器已经不在了，重置标志位
        if _login_browser is None or not _login_browser.is_connected():
            logger.warning("headed 浏览器已关闭，重置 _login_in_progress")
            _login_in_progress = False
        else:
            return jsonify(ok=False, error="登录流程已在进行中，请完成当前登录"), 409

    _login_in_progress = True

    def _on_login_timeout():
        """超时回调：在浏览器事件循环线程中执行，安排异步清理。"""
        logger.warning("登录超时（%s 秒），自动关闭 headed 浏览器", LOGIN_TIMEOUT)
        asyncio.ensure_future(_close_login_browser())

    async def _cleanup_timeout():
        """超时后的异步清理。"""
        global _login_in_progress, _login_browser, _login_context, _login_page
        await _close_login_browser()
        _login_in_progress = False

    async def _start():
        global _login_browser, _login_context, _login_page
        pw = browser._playwright  # noqa: SLF001
        if pw is None:
            return {"ok": False, "error": "Playwright 未初始化"}
        loop = asyncio.get_event_loop()
        _login_browser, _login_context, _login_page = await start_login(
            pw, loop, on_timeout=_on_login_timeout
        )
        return {
            "status": "ready",
            "message": "请在浏览器中完成登录（含拼图验证码），完成后点击「检查登录」",
        }

    try:
        result = browser.submit(_start(), timeout=60)
        if not result.get("ok", True):
            _login_in_progress = False
            return jsonify(result), 500
        return jsonify(ok=True, **result)
    except Exception:
        _login_in_progress = False
        logger.exception("启动登录失败")
        err = "启动浏览器失败，请检查 Playwright/Chromium 安装"
        return jsonify(ok=False, error=err), 500


@api.route("/login/check", methods=["POST"])
def login_check():
    """检查登录是否完成并保存会话。成功时取消超时计时。"""
    global _user_name, _login_in_progress

    if _login_page is None:
        err = "尚未启动登录流程，请先点击「启动浏览器登录」"
        return jsonify(ok=False, error=err), 400

    async def _check():
        global _user_name
        logged_in, username = await check_login(_login_page)
        if logged_in:
            await save_session(_login_context)
            _user_name = username
            await _close_login_browser()
            return {"logged_in": True, "user_name": username}
        return {"logged_in": False}

    try:
        result = browser.submit(_check(), timeout=30)
        if result["logged_in"]:
            _login_in_progress = False
        user_name_val = result.get("user_name", "")
        resp: dict = {"ok": True, "logged_in": result["logged_in"]}
        if user_name_val:
            resp["user_name"] = user_name_val
        return jsonify(resp)
    except Exception:
        logger.exception("登录检查失败")
        return jsonify(ok=False, error="登录检查失败，请重试"), 500


@api.route("/login", methods=["DELETE"])
def logout():
    """清除登录会话：关闭浏览器、清空上下文、取消运行中任务。"""
    global _user_name, _login_browser, _login_context, _login_page
    global _login_in_progress, _running_task_id

    _user_name = ""
    _login_in_progress = False

    # 取消所有运行中的任务并清理历史
    for task in task_manager.list_all():
        if task.status == TaskStatus.RUNNING:
            task_manager.cancel(task.task_id)
    task_manager.clear_finished()

    # 异步清理 headed 浏览器（通过 browser 线程）
    if _login_browser is not None:
        with contextlib.suppress(Exception):
            browser.submit(_close_login_browser(), timeout=10)

    # 同步清理模块级引用
    _login_browser = None
    _login_context = None
    _login_page = None
    _running_task_id = None

    clear_session()
    return jsonify(ok=True)


async def _close_login_browser() -> None:
    """关闭登录用的 headed 浏览器及上下文（在事件循环线程中调用）。"""
    global _login_browser, _login_context, _login_page
    try:
        if _login_page is not None:
            await _login_page.close()
    except Exception:
        pass
    try:
        if _login_context is not None:
            await _login_context.close()
    except Exception:
        pass
    try:
        if _login_browser is not None:
            await _login_browser.close()
    except Exception:
        pass
    _login_browser = None
    _login_context = None
    _login_page = None
    logger.info("Headed 浏览器已关闭")


# ------------------------------------------------------------------
# 下载
# ------------------------------------------------------------------


@api.route("/download/post", methods=["POST"])
def download_post():
    """下载单篇文章。"""
    data = request.get_json(silent=True) or {}
    url = data.get("url", "")
    if not url:
        return jsonify(ok=False, error="请输入文章链接"), 400
    fmt = data.get("format", "md")
    dl_dir = data.get("download_dir", "")

    return _create_download_task("post", _run_post, url, fmt, dl_dir)


@api.route("/download/blog", methods=["POST"])
def download_blog():
    """下载作者全部文章。"""
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "")
    if not user_id:
        return jsonify(ok=False, error="请输入作者数字 ID"), 400
    fmt = data.get("format", "md")
    dl_dir = data.get("download_dir", "")

    return _create_download_task("blog", _run_blog, str(user_id), fmt, dl_dir)


@api.route("/download/likes", methods=["POST"])
def download_likes():
    """下载喜欢文章（需要登录）。"""
    if not SESSION_PATH.exists():
        return jsonify(ok=False, error="喜欢下载需要先登录，请先完成登录"), 403
    data = request.get_json(silent=True) or {}
    fmt = data.get("format", "md")
    dl_dir = data.get("download_dir", "")

    return _create_download_task("likes", _run_likes, fmt, dl_dir)


def _create_download_task(task_type: str, coro_func, *args) -> tuple:
    """创建下载任务，含并发控制和会话校验。"""
    global _running_task_id

    # 自动清理旧任务
    task_manager.cleanup()

    # 并发控制：同一时间只允许一个下载任务
    with _task_lock:
        if _running_task_id is not None:
            running_task = task_manager.get(_running_task_id)
            if running_task and running_task.status in (
                TaskStatus.PENDING, TaskStatus.RUNNING,
            ):
                return jsonify(
                    ok=False,
                    error="已有下载任务在进行中，请等待完成后再提交新任务",
                ), 409

        task_id = task_manager.create(task_type)
        _running_task_id = task_id

    async def _wrapped():
        global _running_task_id
        try:
            # 会话有效性校验：下载前验证 storageState 是否仍有效
            if SESSION_PATH.exists():
                storage = str(SESSION_PATH)
                ctx = await browser.new_context(storage_state=storage)
                try:
                    valid = await verify_session(ctx)
                finally:
                    await ctx.close()
                if not valid:
                    task_manager.update(
                        task_id,
                        status=TaskStatus.FAILED,
                        error="登录会话已过期，请重新登录后再下载",
                    )
                    global _running_task_id
                    if _running_task_id == task_id:
                        _running_task_id = None
                    return
            await coro_func(task_id, *args)
        finally:
            if _running_task_id == task_id:
                _running_task_id = None

    future = browser.submit_async(_wrapped())
    task_manager.set_future(task_id, future)
    return jsonify(task_id=task_id)


# ------------------------------------------------------------------
# 任务管理
# ------------------------------------------------------------------


@api.route("/tasks")
def list_tasks():
    """所有任务列表（按创建时间倒序）。"""
    return jsonify([t.to_dict() for t in task_manager.list_all()])


@api.route("/tasks/<task_id>")
def get_task(task_id: str):
    """单个任务状态。"""
    task = task_manager.get(task_id)
    if task is None:
        return jsonify(error="任务不存在"), 404
    return jsonify(task.to_dict())


@api.route("/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task(task_id: str):
    """取消运行中的任务（设置标志位 + 触发 CancelledError）。"""
    global _running_task_id

    task = task_manager.get(task_id)
    if task is None:
        return jsonify(ok=False, error="任务不存在"), 404
    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        cancelled = task_manager.cancel(task_id)
        if task_id == _running_task_id:
            _running_task_id = None
        if not cancelled:
            task_manager.update(task_id, status=TaskStatus.CANCELED)
        return jsonify(ok=True)
    return jsonify(ok=False, error="任务不在运行中，无法取消")


# ------------------------------------------------------------------
# 后台下载协程（在浏览器事件循环线程中执行）
# ------------------------------------------------------------------


async def _run_post(task_id: str, url: str, fmt: str = "md",
                    dl_dir: str = "") -> None:
    """后台执行单篇文章下载。"""
    from downloader.pipeline import DownloadPipeline

    task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(browser)
    try:
        _check_cancelled(task_id)
        results = await pipeline.run_post(url)
        if results:
            await _save_results(results, sub_dir="单篇下载", fmt=fmt,
                                dl_dir=dl_dir)
            task_manager.update(
                task_id,
                status=TaskStatus.COMPLETED,
                total=len(results),
                current=len(results),
                message=results[0].get("title", ""),
            )
        else:
            task_manager.update(
                task_id, status=TaskStatus.FAILED,
                error="未能提取文章内容，请检查链接是否正确",
            )
    except asyncio.CancelledError:
        task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("下载文章失败")
        task_manager.update(task_id, status=TaskStatus.FAILED, error=_user_error(exc))


async def _run_blog(task_id: str, user_id: str, fmt: str = "md",
                   dl_dir: str = "") -> None:
    """后台执行作者全部文章下载。"""
    from downloader.pipeline import DownloadPipeline

    task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(browser)
    _check_cancelled(task_id)
    storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
    ctx = await browser.new_context(storage_state=storage)
    try:
        links, blog_name = await pipeline.collect_blog_links(user_id, context=ctx)
        if not links:
            task_manager.update(
                task_id, status=TaskStatus.FAILED,
                error="未找到任何文章，请检查作者 ID 是否正确",
            )
            return

        task_manager.update(
            task_id, total=len(links), message=f"作者: {blog_name}",
        )
        for idx, link in enumerate(links):
            _check_cancelled(task_id)
            try:
                results = await pipeline.run_post(link, context=ctx)
                if results:
                    await _save_results(results, sub_dir=user_id, fmt=fmt,
                                        dl_dir=dl_dir)
                task_manager.update(
                    task_id,
                    current=idx + 1,
                    message=(
                        results[0].get("title", "")
                        if results else f"空内容: {link[:40]}"
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = _classify_error(exc)
                logger.warning("跳过失败博客文章 [%s]: %s", reason, link)
                task_manager.update(
                    task_id, current=idx + 1,
                    message=f"跳过({reason}): {link[:40]}",
                )
            from config import REQUEST_INTERVAL
            await asyncio.sleep(REQUEST_INTERVAL)

        task_manager.update(task_id, status=TaskStatus.COMPLETED)
    except asyncio.CancelledError:
        task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("下载博客失败")
        task_manager.update(task_id, status=TaskStatus.FAILED, error=_user_error(exc))
    finally:
        await ctx.close()


async def _run_likes(task_id: str, fmt: str = "md",
                    dl_dir: str = "") -> None:
    """后台执行喜欢文章下载。"""
    from downloader.pipeline import DownloadPipeline

    task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(browser)
    _check_cancelled(task_id)
    storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
    ctx = await browser.new_context(storage_state=storage)
    try:
        links = await pipeline.collect_likes_links(context=ctx)
        if not links:
            task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error="未找到任何喜欢文章，请确认已登录并有喜欢内容",
            )
            return

        task_manager.update(task_id, total=len(links))
        for idx, link in enumerate(links):
            _check_cancelled(task_id)
            try:
                results = await pipeline.run_post(link, context=ctx)
                if results:
                    await _save_results(results, sub_dir="喜欢文章", fmt=fmt,
                                        dl_dir=dl_dir)
                task_manager.update(
                    task_id,
                    current=idx + 1,
                    message=(
                        results[0].get("title", "")
                        if results else f"空内容: {link[:40]}"
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = _classify_error(exc)
                logger.warning("跳过失败喜欢文章 [%s]: %s", reason, link)
                task_manager.update(
                    task_id, current=idx + 1,
                    message=f"跳过({reason}): {link[:40]}",
                )
            from config import REQUEST_INTERVAL
            await asyncio.sleep(REQUEST_INTERVAL)

        task_manager.update(task_id, status=TaskStatus.COMPLETED)
    except asyncio.CancelledError:
        task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("下载喜欢失败")
        task_manager.update(task_id, status=TaskStatus.FAILED, error=_user_error(exc))
    finally:
        await ctx.close()


async def _save_results(
    post_dicts: list[dict], sub_dir: str, fmt: str = "md",
    dl_dir: str = "",
) -> None:
    """保存文章到文件系统。dl_dir 为空则使用默认下载目录。"""
    from pathlib import Path

    from downloader.saver import PostSaver

    if dl_dir:
        dl_path = Path(dl_dir)
        base_dir = dl_path.resolve() if dl_path.is_absolute() else DOWNLOAD_DIR / dl_dir
    else:
        base_dir = DOWNLOAD_DIR
    storage = load_storage_state()
    cookies = []
    if storage and "cookies" in storage:
        cookies = storage["cookies"]

    saver = PostSaver(base_dir, cookies=cookies)
    try:
        for post_dict in post_dicts:
            await saver.save_dict(post_dict, sub_dir=sub_dir, fmt=fmt)
    finally:
        await saver.close()


# ------------------------------------------------------------------
# 辅助
# ------------------------------------------------------------------


def _check_cancelled(task_id: str) -> None:
    """检查任务是否被取消，若是则抛出 CancelledError。"""
    task = task_manager.get(task_id)
    if task and task.status == TaskStatus.CANCELED:
        raise asyncio.CancelledError()


def _classify_error(exc: Exception) -> str:
    """将异常分类为短中文标签，供前端 task.message 展示。"""
    from downloader.exceptions import LoginRequiredError, NetworkError, ParseError

    msg = str(exc)
    if isinstance(exc, ParseError):
        return "解析失败"
    if isinstance(exc, NetworkError):
        return "网络错误"
    if isinstance(exc, LoginRequiredError):
        return "需登录"
    if "timeout" in msg.lower() or "超时" in msg:
        return "超时"
    if "login" in msg.lower() or "session" in msg.lower():
        return "需登录"
    # 截断技术异常，只给用户看前 10 个字符
    return msg[:10] if msg else "未知错误"


def _user_error(exc: Exception) -> str:
    """将异常转为用户可读的中文错误信息。"""
    msg = str(exc)
    # 隐藏技术细节，返回友好提示
    if "timeout" in msg.lower() or "超时" in msg:
        return "网络请求超时，请检查网络连接后重试"
    if "connection" in msg.lower() or "拒绝" in msg:
        return "网络连接失败，请检查网络设置"
    if "session" in msg.lower() or "cookie" in msg.lower() or "登录" in msg:
        return "登录会话已过期，请重新登录"
    return "下载过程出错，请重试"
