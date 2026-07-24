"""Flask API 路由。

所有 REST 端点：登录管理、下载任务创建/查询/取消。
含会话校验、登录超时、退出清理、并发控制。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request

from config import DOWNLOAD_DIR, SESSION_PATH
from downloader.ao3 import AO3Client, AO3Error
from downloader.auth import (
    LOGIN_TIMEOUT,
    check_login,
    clear_session,
    load_storage_state,
    save_session,
    start_login,
    verify_session,
)
from downloader.exceptions import LofterError
from downloader.models import TaskManager, TaskStatus
from downloader.saver import _sanitize_filename

logger = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")

# AO3 官方导出支持的格式
AO3_OFFICIAL_FORMATS = {"epub", "pdf", "html", "mobi", "azw3"}

# ------------------------------------------------------------------
# 模块级共享状态（单用户模式）
# ------------------------------------------------------------------

task_manager = TaskManager()
browser = None  # 由 app.py 注入 BrowserManager 实例

# 登录状态
_login_browser = None  # headed 浏览器实例
_login_context = None  # headed BrowserContext
_login_page = None  # headed Page
_login_in_progress = False  # 防止重复启动登录
_login_start_error = ""  # 启动阶段的错误信息
_user_name = ""

# 下载并发控制
_running_task_id: str | None = None  # 当前运行中的任务 ID
_task_lock = threading.Lock()

# 清单并发控制（与下载任务互不阻塞，同类清单同时只允许一个）
_running_list_kind: str | None = None  # 当前运行中的清单任务类型
_list_task_lock = threading.Lock()


# ------------------------------------------------------------------
# 登录
# ------------------------------------------------------------------


@api.route("/login/status")
def login_status():
    """检查登录状态（含会话有效性校验和登录启动进度）。"""
    return jsonify(
        logged_in=SESSION_PATH.exists(),
        user_name=_user_name,
        login_starting=_login_in_progress and _login_page is None,
        login_ready=_login_page is not None,
        login_start_error=_login_start_error,
    )


@api.route("/login/start", methods=["POST"])
def login_start():
    """启动浏览器登录流程（非阻塞，立即返回）。"""
    global _login_in_progress, _login_start_error
    global _login_browser, _login_context, _login_page

    if browser is None:
        return jsonify(ok=False, error="浏览器未初始化，请稍后重试"), 500

    if _login_in_progress:
        if _login_browser is None or not _login_browser.is_connected():
            logger.warning("headed 浏览器已关闭，重置 _login_in_progress")
            _login_in_progress = False
        else:
            return jsonify(ok=False, error="登录流程已在进行中，请完成当前登录"), 409

    _login_in_progress = True
    _login_start_error = ""

    def _on_login_timeout():
        logger.warning("登录超时（%s 秒），自动关闭 headed 浏览器", LOGIN_TIMEOUT)
        asyncio.ensure_future(_close_login_browser())

    async def _start_and_set_state():
        global _login_in_progress, _login_start_error
        global _login_browser, _login_context, _login_page
        try:
            pw = browser._playwright  # noqa: SLF001
            if pw is None:
                _login_start_error = "Playwright 未初始化"
                _login_in_progress = False
                return
            loop = asyncio.get_event_loop()
            _login_browser, _login_context, _login_page = await start_login(
                pw, loop, on_timeout=_on_login_timeout
            )
        except Exception as exc:
            _login_start_error = str(exc) or type(exc).__name__
            _login_in_progress = False
            logger.exception("启动登录失败")

    browser.submit_async(_start_and_set_state())
    return jsonify(ok=True, status="starting", message="正在启动浏览器，请稍候...")


@api.route("/login/check", methods=["POST"])
def login_check():
    """检查登录是否完成并保存会话。成功时取消超时计时。"""
    global _user_name, _login_in_progress

    if _login_page is None:
        err = "尚未启动登录流程，请先点击「启动浏览器登录」"
        return jsonify(ok=False, error=err), 400

    # 登录窗口已被用户关闭（或浏览器崩溃）：重置状态，允许重新发起登录
    if _login_page.is_closed() or not _login_browser.is_connected():
        _reset_login_state()
        return jsonify(ok=False, error="登录窗口已关闭，请重新点击登录"), 400

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
        # 检查过程中页面被关闭（如用户中途关窗）：同样重置，避免状态卡死
        if _login_page is None or _login_page.is_closed():
            _reset_login_state()
            return jsonify(ok=False, error="登录窗口已关闭，请重新点击登录"), 400
        return jsonify(ok=False, error="登录检查失败，请重试"), 500


def _reset_login_state() -> None:
    """重置登录流程的模块级状态（登录窗口失效时调用）。"""
    global _login_browser, _login_context, _login_page
    global _login_in_progress, _login_start_error
    _login_browser = None
    _login_context = None
    _login_page = None
    _login_in_progress = False
    _login_start_error = ""


@api.route("/login", methods=["DELETE"])
def logout():
    """清除登录会话：关闭浏览器、清空上下文、取消运行中任务。"""
    global _user_name, _login_browser, _login_context, _login_page
    global _login_in_progress, _login_start_error
    global _running_task_id, _running_list_kind

    _user_name = ""
    _login_in_progress = False
    _login_start_error = ""

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
    _running_list_kind = None

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


@api.route("/list/blog", methods=["POST"])
def list_blog():
    """列出作者全部文章（用于选择下载）。"""
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "")
    if not user_id:
        return jsonify(ok=False, error="请输入作者数字 ID"), 400

    return _create_list_task("list_blog", _run_list_blog, str(user_id))


@api.route("/download/blog", methods=["POST"])
def download_blog():
    """下载作者全部文章，或下载选中的文章。"""
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "")
    if not user_id:
        return jsonify(ok=False, error="请输入作者数字 ID"), 400
    fmt = data.get("format", "md")
    dl_dir = data.get("download_dir", "")
    # urls 区分 None（未传，全量下载）与空列表（用户未选择任何文章）
    urls = data.get("urls")
    if urls is not None:
        if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
            return jsonify(ok=False, error="urls 必须是字符串列表"), 400
        if not urls:
            return jsonify(ok=False, error="未选择任何文章"), 400
    # 选中下载时前端随清单结果回传博客名，用于子目录命名
    blog_name = data.get("blog_name", "")

    return _create_download_task(
        "blog",
        _run_blog,
        str(user_id),
        fmt,
        dl_dir,
        urls,
        blog_name,
    )


@api.route("/download/likes", methods=["POST"])
def download_likes():
    """下载喜欢文章（需要登录）。"""
    if not SESSION_PATH.exists():
        return jsonify(ok=False, error="喜欢下载需要先登录，请先完成登录"), 403
    data = request.get_json(silent=True) or {}
    fmt = data.get("format", "md")
    dl_dir = data.get("download_dir", "")

    return _create_download_task("likes", _run_likes, fmt, dl_dir)


@api.route("/list/ao3", methods=["POST"])
def list_ao3():
    """列出 AO3 系列/作者/批量链接文章清单。"""
    data = request.get_json(silent=True) or {}
    kind = data.get("kind", "")
    query = data.get("query", "")
    if kind not in ("series", "author", "batch"):
        return jsonify(ok=False, error="kind 必须是 series/author/batch"), 400
    if not query:
        return jsonify(ok=False, error="请输入查询内容"), 400

    return _create_list_task("list_ao3", _run_list_ao3, kind, query)


@api.route("/download/ao3", methods=["POST"])
def download_ao3():
    """下载 AO3 文章（官方导出或统一解析）。"""
    data = request.get_json(silent=True) or {}
    urls = data.get("urls", [])
    if not urls:
        return jsonify(ok=False, error="请选择要下载的文章"), 400
    fmt = data.get("format", "epub")
    source = data.get("source", "official")
    dl_dir = data.get("download_dir", "")

    if source == "official" and fmt.lower() not in AO3_OFFICIAL_FORMATS:
        return jsonify(ok=False, error=f"不支持的 AO3 官方格式: {fmt}"), 400

    return _create_download_task(
        "ao3",
        _run_download_ao3,
        urls,
        fmt,
        source,
        dl_dir,
    )


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
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
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
            # 会话有效性校验：仅 LOFTER 相关下载需要验证登录会话
            if task_type != "ao3" and SESSION_PATH.exists():
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
                    return
            await coro_func(task_id, *args)
        except Exception as exc:
            # 启动阶段（new_context/verify_session）异常：必须标记 FAILED，
            # 否则任务永远停留在 PENDING
            logger.exception("下载任务失败")
            task_manager.update(
                task_id, status=TaskStatus.FAILED, error=_user_error(exc)
            )
        finally:
            if _running_task_id == task_id:
                _running_task_id = None

    coro = _wrapped()
    try:
        future = browser.submit_async(coro)
    except LofterError as exc:
        # 事件循环未运行：重置并发槽位并标记失败，避免任务永卡 PENDING
        coro.close()
        with _task_lock:
            if _running_task_id == task_id:
                _running_task_id = None
        task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )
        return jsonify(ok=False, error=_user_error(exc)), 500
    task_manager.set_future(task_id, future)
    return jsonify(task_id=task_id)


def _create_list_task(task_type: str, coro_func, *args) -> tuple:
    """创建清单任务；同类清单同时只允许一个（与下载任务互不阻塞）。"""
    global _running_list_kind

    task_manager.cleanup()

    with _list_task_lock:
        if _running_list_kind == task_type:
            return jsonify(
                ok=False,
                error="同类清单任务已在进行中，请等待完成后再提交",
            ), 409
        task_id = task_manager.create(task_type)
        _running_list_kind = task_type

    async def _wrapped():
        global _running_list_kind
        try:
            await coro_func(task_id, *args)
        except asyncio.CancelledError:
            task_manager.update(task_id, status=TaskStatus.CANCELED)
        except Exception as exc:
            logger.exception("清单任务失败")
            task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error=_user_error(exc),
            )
        finally:
            with _list_task_lock:
                if _running_list_kind == task_type:
                    _running_list_kind = None

    coro = _wrapped()
    try:
        future = browser.submit_async(coro)
    except LofterError as exc:
        coro.close()
        with _list_task_lock:
            if _running_list_kind == task_type:
                _running_list_kind = None
        task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )
        return jsonify(ok=False, error=_user_error(exc)), 500
    task_manager.set_future(task_id, future)
    return jsonify(task_id=task_id)


# ------------------------------------------------------------------
# 任务管理
# ------------------------------------------------------------------


@api.route("/tasks")
def list_tasks():
    """所有任务列表（按创建时间倒序）。

    清单任务的 result.items 可能含数百条记录，轮询开销大，
    列表响应中替换为 items_count 摘要；完整 result 由 /api/tasks/<id> 提供。
    """
    return jsonify([_task_summary_dict(t) for t in task_manager.list_all()])


def _task_summary_dict(task) -> dict:
    """任务字典的列表摘要版：剔除 result.items 大字段，保留其余小字段。"""
    d = task.to_dict()
    result = d.get("result") or {}
    if "items" in result:
        d["result"] = {k: v for k, v in result.items() if k != "items"}
        d["result"]["items_count"] = len(result["items"])
    return d


@api.route("/tasks/<task_id>")
def get_task(task_id: str):
    """单个任务状态。"""
    task = task_manager.get(task_id)
    if task is None:
        return jsonify(error="任务不存在"), 404
    return jsonify(task.to_dict())


@api.route("/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task(task_id: str):
    """取消运行中的任务（设置标志位 + 触发 CancelledError）。

    注意：取消后立即释放并发槽位（_running_task_id / _running_list_kind），
    旧协程会运行到下一个取消断点（_check_cancelled / should_cancel）
    才真正停止，期间新任务可能与旧协程短暂并存。这是可接受的取舍：
    等待旧协程完全退出会阻塞取消请求的响应。
    """
    global _running_task_id, _running_list_kind

    task = task_manager.get(task_id)
    if task is None:
        return jsonify(ok=False, error="任务不存在"), 404
    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        cancelled = task_manager.cancel(task_id)
        if task_id == _running_task_id:
            _running_task_id = None
        if task.type == _running_list_kind:
            with _list_task_lock:
                if _running_list_kind == task.type:
                    _running_list_kind = None
        if not cancelled:
            task_manager.update(task_id, status=TaskStatus.CANCELED)
        return jsonify(ok=True)
    return jsonify(ok=False, error="任务不在运行中，无法取消")


# ------------------------------------------------------------------
# 后台下载协程（在浏览器事件循环线程中执行）
# ------------------------------------------------------------------


async def _run_post(task_id: str, url: str, fmt: str = "md", dl_dir: str = "") -> None:
    """后台执行单篇文章下载。"""
    from downloader.pipeline import DownloadPipeline

    task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(browser)
    try:
        _check_cancelled(task_id)
        results = await pipeline.run_post(url)
        if results:
            # parser 对作者有「未知作者」兜底，author 恒非空
            sub_dir = _sanitize_filename(results[0].get("author", ""))
            saved_base = await _save_results(
                results, sub_dir=sub_dir, fmt=fmt, dl_dir=dl_dir
            )
            _complete_task(
                task_id,
                total=len(results),
                current=len(results),
                message=results[0].get("title", ""),
                result={"saved_path": str(saved_base)},
            )
        else:
            task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error="未能提取文章内容，请检查链接是否正确",
            )
    except asyncio.CancelledError:
        task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("下载文章失败")
        task_manager.update(task_id, status=TaskStatus.FAILED, error=_user_error(exc))


async def _run_list_blog(task_id: str, user_id: str) -> None:
    """后台执行作者文章清单收集。"""
    from downloader.pipeline import DownloadPipeline

    task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(browser)
    storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
    ctx = await browser.new_context(storage_state=storage)
    try:
        _check_cancelled(task_id)
        # 与下载路径一致：收集前先校验会话，过期给出明确提示而非「未找到文章」
        if SESSION_PATH.exists():
            valid = await verify_session(ctx)
            if not valid:
                task_manager.update(
                    task_id,
                    status=TaskStatus.FAILED,
                    error="登录会话已过期，请重新登录后再获取清单",
                )
                return
        items, blog_name = await pipeline.collect_blog_items(
            user_id,
            context=ctx,
            should_cancel=lambda: _is_cancelled(task_id),
        )
        _check_cancelled(task_id)
        if not items:
            task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error="未找到任何文章，请检查作者 ID 是否正确",
            )
            return

        _complete_task(
            task_id,
            total=len(items),
            current=len(items),
            message=f"作者: {blog_name}",
            result={"items": items, "blog_name": blog_name},
        )
    except asyncio.CancelledError:
        task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("收集清单失败")
        task_manager.update(task_id, status=TaskStatus.FAILED, error=_user_error(exc))
    finally:
        await ctx.close()


async def _run_list_ao3(task_id: str, kind: str, query: str) -> None:
    """后台执行 AO3 清单收集。"""
    client = AO3Client()
    try:
        task_manager.update(task_id, status=TaskStatus.RUNNING)
        _check_cancelled(task_id)
        items: list[dict] = []
        name = ""

        if kind == "series":
            series_id = _extract_ao3_series_id(query)
            if not series_id:
                task_manager.update(
                    task_id,
                    status=TaskStatus.FAILED,
                    error="无法解析 AO3 系列链接",
                )
                return
            items = await client.list_series(series_id)
            name = f"series_{series_id}"
        elif kind == "author":
            username = _extract_ao3_username(query)
            if not username:
                task_manager.update(
                    task_id,
                    status=TaskStatus.FAILED,
                    error="无法解析 AO3 作者链接",
                )
                return
            items = await client.list_author(username)
            name = username
        else:  # batch
            urls = [u.strip() for u in query.splitlines() if u.strip()]
            items = await client.list_batch(urls)
            name = "批量链接"

        _check_cancelled(task_id)
        if not items:
            task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error="未找到任何 AO3 文章",
            )
            return

        _complete_task(
            task_id,
            total=len(items),
            current=len(items),
            message=f"AO3 {name}",
            result={"items": items, "name": name, "kind": kind},
        )
    except asyncio.CancelledError:
        task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("AO3 清单收集失败")
        task_manager.update(task_id, status=TaskStatus.FAILED, error=_user_error(exc))
    finally:
        await client.close()


async def _run_blog(
    task_id: str,
    user_id: str,
    fmt: str = "md",
    dl_dir: str = "",
    urls: list[str] | None = None,
    blog_name: str = "",
) -> None:
    """后台执行作者全部文章下载（urls 非空时只下载指定链接）。

    blog_name 由前端随清单结果回传，选中下载时用于子目录命名，
    缺省回退为 user_id。
    """
    from downloader.pipeline import DownloadPipeline

    task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(browser)
    _check_cancelled(task_id)
    storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
    ctx = await browser.new_context(storage_state=storage)
    try:
        if urls:
            links = urls
            blog_name = blog_name or user_id
        else:
            links, blog_name = await pipeline.collect_blog_links(
                user_id,
                context=ctx,
            )
        if not links:
            task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error="未找到任何文章，请检查作者 ID 是否正确",
            )
            return

        blog_sub_dir = _sanitize_filename(blog_name or user_id)
        base_dir = _resolve_base_dir(dl_dir)
        task_manager.update(
            task_id,
            total=len(links),
            message=f"作者: {blog_name}",
        )
        for idx, link in enumerate(links):
            _check_cancelled(task_id)
            try:
                results = await pipeline.run_post(link, context=ctx)
                if results:
                    await _save_results(
                        results, sub_dir=blog_sub_dir, fmt=fmt, dl_dir=dl_dir
                    )
                task_manager.update(
                    task_id,
                    current=idx + 1,
                    message=(
                        results[0].get("title", "")
                        if results
                        else f"空内容: {link[:40]}"
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = _classify_error(exc)
                logger.warning("跳过失败博客文章 [%s]: %s", reason, link)
                task_manager.update(
                    task_id,
                    current=idx + 1,
                    message=f"跳过({reason}): {link[:40]}",
                )
            from config import REQUEST_INTERVAL

            await asyncio.sleep(REQUEST_INTERVAL)

        _complete_task(
            task_id,
            result={"saved_path": str(base_dir)},
        )
    except asyncio.CancelledError:
        task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("下载博客失败")
        task_manager.update(task_id, status=TaskStatus.FAILED, error=_user_error(exc))
    finally:
        await ctx.close()


async def _run_likes(task_id: str, fmt: str = "md", dl_dir: str = "") -> None:
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

        base_dir = _resolve_base_dir(dl_dir)
        task_manager.update(task_id, total=len(links))
        for idx, link in enumerate(links):
            _check_cancelled(task_id)
            try:
                results = await pipeline.run_post(link, context=ctx)
                if results:
                    await _save_results(
                        results, sub_dir="喜欢文章", fmt=fmt, dl_dir=dl_dir
                    )
                task_manager.update(
                    task_id,
                    current=idx + 1,
                    message=(
                        results[0].get("title", "")
                        if results
                        else f"空内容: {link[:40]}"
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = _classify_error(exc)
                logger.warning("跳过失败喜欢文章 [%s]: %s", reason, link)
                task_manager.update(
                    task_id,
                    current=idx + 1,
                    message=f"跳过({reason}): {link[:40]}",
                )
            from config import REQUEST_INTERVAL

            await asyncio.sleep(REQUEST_INTERVAL)

        _complete_task(
            task_id,
            result={"saved_path": str(base_dir)},
        )
    except asyncio.CancelledError:
        task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("下载喜欢失败")
        task_manager.update(task_id, status=TaskStatus.FAILED, error=_user_error(exc))
    finally:
        await ctx.close()


async def _run_download_ao3(
    task_id: str,
    urls: list[str],
    fmt: str,
    source: str,
    dl_dir: str,
) -> None:
    """后台执行 AO3 下载（官方导出或解析）。"""
    from pathlib import Path

    from downloader.saver import PostSaver, _sanitize_filename, _unique_path

    client = AO3Client()
    try:
        task_manager.update(task_id, status=TaskStatus.RUNNING)
        task_manager.update(task_id, total=len(urls))

        # 任务内作品页缓存：get_work_info 结果复用，
        # 避免「取作者名 + 逐篇取标题」重复抓取同一页面
        info_cache: dict[str, dict] = {}

        async def _get_info(work_id: str) -> dict:
            if work_id not in info_cache:
                info_cache[work_id] = await client.get_work_info(work_id)
            return info_cache[work_id]

        # 确定作者名（用于子目录），取第一篇
        first_author = ""
        first_id = _extract_ao3_work_id(urls[0]) if urls else None
        if first_id:
            try:
                info = await _get_info(first_id)
                first_author = info.get("author", "")
            except Exception:
                pass
        author_dir = _sanitize_filename(first_author) or "AO3"
        sub_dir = f"AO3/{author_dir}"

        # 基础目录
        if dl_dir:
            dl_path = Path(dl_dir)
            base_dir = (
                dl_path.resolve() if dl_path.is_absolute() else DOWNLOAD_DIR / dl_dir
            )
        else:
            base_dir = DOWNLOAD_DIR

        success_count = 0
        failure_count = 0

        if source == "official":
            for idx, url in enumerate(urls):
                _check_cancelled(task_id)
                work_id = _extract_ao3_work_id(url)
                if not work_id:
                    failure_count += 1
                    task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=f"链接无效: {url[:40]}",
                    )
                    continue
                try:
                    info = await _get_info(work_id)
                    title = info.get("title") or f"work_{work_id}"
                    safe_title = _sanitize_filename(title) or f"work_{work_id}"
                    ext = f".{fmt.lower()}"
                    output_dir = base_dir / sub_dir
                    output_dir.mkdir(parents=True, exist_ok=True)
                    dest = _unique_path(output_dir, f"{safe_title}_{work_id}", ext=ext)
                    await client.download_official(work_id, fmt, dest)
                    success_count += 1
                    task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=title,
                    )
                except Exception as exc:
                    failure_count += 1
                    reason = _classify_error(exc)
                    logger.warning("AO3 官方下载失败 [%s]: %s", reason, url)
                    task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=f"跳过({reason}): {url[:40]}",
                    )
        else:
            # 解析管道
            saver = None
            for idx, url in enumerate(urls):
                _check_cancelled(task_id)
                work_id = _extract_ao3_work_id(url)
                if not work_id:
                    failure_count += 1
                    task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=f"链接无效: {url[:40]}",
                    )
                    continue
                try:
                    post_dict = await client.parse_work(work_id)
                    if saver is None:
                        storage = load_storage_state()
                        cookies = storage.get("cookies", []) if storage else []
                        saver = PostSaver(base_dir, cookies=cookies)
                    await saver.save_dict(post_dict, sub_dir=sub_dir, fmt=fmt)
                    success_count += 1
                    task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=post_dict.get("title", ""),
                    )
                except Exception as exc:
                    failure_count += 1
                    reason = _classify_error(exc)
                    logger.warning("AO3 解析下载失败 [%s]: %s", reason, url)
                    task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=f"跳过({reason}): {url[:40]}",
                    )
            if saver is not None:
                await saver.close()

        total = len(urls)
        if success_count == 0 and failure_count > 0:
            task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error=(
                    "AO3 全部下载失败，当前可能处于 shields up"
                    "（高负载防护）模式，请稍后重试"
                ),
            )
        else:
            message = f"成功 {success_count}/{total}"
            _complete_task(
                task_id,
                message=message,
                result={"saved_path": str(base_dir)},
            )
    except asyncio.CancelledError:
        task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("AO3 下载失败")
        task_manager.update(task_id, status=TaskStatus.FAILED, error=_user_error(exc))
    finally:
        await client.close()


def _resolve_base_dir(dl_dir: str) -> Path:
    """根据用户输入解析最终下载根目录。"""
    if dl_dir:
        dl_path = Path(dl_dir)
        return dl_path.resolve() if dl_path.is_absolute() else DOWNLOAD_DIR / dl_dir
    return DOWNLOAD_DIR


async def _save_results(
    post_dicts: list[dict],
    sub_dir: str,
    fmt: str = "md",
    dl_dir: str = "",
) -> Path:
    """保存文章到文件系统。dl_dir 为空则使用默认下载目录。

    返回实际写入的根目录，便于前端展示「打开文件夹」。
    """
    from downloader.saver import PostSaver

    base_dir = _resolve_base_dir(dl_dir)
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
    return base_dir


# ------------------------------------------------------------------
# 辅助
# ------------------------------------------------------------------


def _extract_ao3_work_id(url: str) -> str | None:
    """从 AO3 作品链接提取 ID。"""
    import re

    m = re.search(r"/works/(\d+)", url)
    return m.group(1) if m else None


def _extract_ao3_series_id(url: str) -> str | None:
    """从 AO3 系列链接提取 ID。"""
    import re

    m = re.search(r"/series/(\d+)", url)
    return m.group(1) if m else None


def _extract_ao3_username(url: str) -> str | None:
    """从 AO3 作者链接提取用户名（委托 ao3 模块的统一实现）。

    支持 /users/xxx、/users/xxx/works、/users/xxx/pseuds/yyy 等形式。
    """
    from downloader.ao3 import extract_username

    return extract_username(url)


def _is_cancelled(task_id: str) -> bool:
    """任务是否已被用户取消。"""
    task = task_manager.get(task_id)
    return task is not None and task.status == TaskStatus.CANCELED


def _check_cancelled(task_id: str) -> None:
    """检查任务是否被取消，若是则抛出 CancelledError。"""
    if _is_cancelled(task_id):
        raise asyncio.CancelledError()


def _complete_task(task_id: str, **kwargs: object) -> None:
    """将任务标记为完成；若已被用户取消则不覆盖 CANCELED 状态。"""
    if _is_cancelled(task_id):
        return
    task_manager.update(task_id, status=TaskStatus.COMPLETED, **kwargs)


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
    if "shields up" in msg.lower():
        return "AO3 受限"
    if isinstance(exc, AO3Error):
        if "频繁" in msg or "限流" in msg or "429" in msg:
            return "被限流"
        return "AO3 错误"
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
    if "shields up" in msg.lower():
        return "AO3 当前处于高负载防护模式，请稍后重试"
    if "timeout" in msg.lower() or "超时" in msg:
        return "网络请求超时，请检查网络连接后重试"
    if "connection" in msg.lower() or "拒绝" in msg:
        return "网络连接失败，请检查网络设置"
    if "session" in msg.lower() or "cookie" in msg.lower() or "登录" in msg:
        return "登录会话已过期，请重新登录"
    return "下载过程出错，请重试"
