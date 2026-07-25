"""下载与清单端点：任务创建、并发控制、后台下载协程。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from flask import jsonify, request
from flask.typing import ResponseReturnValue

from config import DOWNLOAD_DIR, SESSION_PATH
from downloader.ao3 import (
    AO3Client,
    extract_series_id,
    extract_username,
    extract_work_id,
)
from downloader.auth import load_storage_state, verify_session
from downloader.exceptions import LofterError
from downloader.models import TaskStatus
from downloader.saver import _sanitize_filename
from web import state
from web.helpers import (
    _check_cancelled,
    _classify_error,
    _complete_task,
    _is_cancelled,
    _user_error,
)

logger = logging.getLogger(__name__)

# AO3 官方导出支持的格式
AO3_OFFICIAL_FORMATS = {"epub", "pdf", "html", "mobi", "azw3"}


@state.api.route("/download/post", methods=["POST"])
def download_post() -> ResponseReturnValue:
    """下载单篇文章。"""
    data = request.get_json(silent=True) or {}
    url = data.get("url", "")
    if not url:
        return jsonify(ok=False, error="请输入文章链接"), 400
    fmt = data.get("format", "md")
    dl_dir = data.get("download_dir", "")

    return _create_download_task("post", _run_post, url, fmt, dl_dir)


@state.api.route("/list/blog", methods=["POST"])
def list_blog() -> ResponseReturnValue:
    """列出作者全部文章（用于选择下载）。"""
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "")
    if not user_id:
        return jsonify(ok=False, error="请输入作者数字 ID"), 400

    return _create_list_task("list_blog", _run_list_blog, str(user_id))


@state.api.route("/download/blog", methods=["POST"])
def download_blog() -> ResponseReturnValue:
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


@state.api.route("/download/likes", methods=["POST"])
def download_likes() -> ResponseReturnValue:
    """下载喜欢文章（需要登录）。"""
    if not SESSION_PATH.exists():
        return jsonify(ok=False, error="喜欢下载需要先登录，请先完成登录"), 403
    data = request.get_json(silent=True) or {}
    fmt = data.get("format", "md")
    dl_dir = data.get("download_dir", "")

    return _create_download_task("likes", _run_likes, fmt, dl_dir)


@state.api.route("/list/ao3", methods=["POST"])
def list_ao3() -> ResponseReturnValue:
    """列出 AO3 系列/作者/批量链接文章清单。"""
    data = request.get_json(silent=True) or {}
    kind = data.get("kind", "")
    query = data.get("query", "")
    if kind not in ("series", "author", "batch"):
        return jsonify(ok=False, error="kind 必须是 series/author/batch"), 400
    if not query:
        return jsonify(ok=False, error="请输入查询内容"), 400

    return _create_list_task("list_ao3", _run_list_ao3, kind, query)


@state.api.route("/download/ao3", methods=["POST"])
def download_ao3() -> ResponseReturnValue:
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


def _create_download_task(
    task_type: str, coro_func: Callable[..., Awaitable[None]], *args: object
) -> tuple:
    """创建下载任务，含并发控制和会话校验。"""
    # 自动清理旧任务
    state.task_manager.cleanup()

    # 并发控制：同一时间只允许一个下载任务
    with state._task_lock:
        if state._running_task_id is not None:
            running_task = state.task_manager.get(state._running_task_id)
            if running_task and running_task.status in (
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
            ):
                return jsonify(
                    ok=False,
                    error="已有下载任务在进行中，请等待完成后再提交新任务",
                ), 409

        task_id = state.task_manager.create(task_type)
        state._running_task_id = task_id

    async def _wrapped() -> None:
        try:
            # 会话有效性校验：仅 LOFTER 相关下载需要验证登录会话
            if task_type != "ao3" and SESSION_PATH.exists():
                storage = str(SESSION_PATH)
                ctx = await state.browser.new_context(storage_state=storage)
                try:
                    valid = await verify_session(ctx)
                finally:
                    await ctx.close()
                if not valid:
                    state.task_manager.update(
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
            state.task_manager.update(
                task_id, status=TaskStatus.FAILED, error=_user_error(exc)
            )
        finally:
            if state._running_task_id == task_id:
                state._running_task_id = None

    coro = _wrapped()
    try:
        future = state.browser.submit_async(coro)
    except LofterError as exc:
        # 事件循环未运行：重置并发槽位并标记失败，避免任务永卡 PENDING
        coro.close()
        with state._task_lock:
            if state._running_task_id == task_id:
                state._running_task_id = None
        state.task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )
        return jsonify(ok=False, error=_user_error(exc)), 500
    state.task_manager.set_future(task_id, future)
    return jsonify(task_id=task_id)


def _create_list_task(
    task_type: str, coro_func: Callable[..., Awaitable[None]], *args: object
) -> tuple:
    """创建清单任务；同类清单同时只允许一个（与下载任务互不阻塞）。"""
    state.task_manager.cleanup()

    with state._list_task_lock:
        if state._running_list_kind == task_type:
            return jsonify(
                ok=False,
                error="同类清单任务已在进行中，请等待完成后再提交",
            ), 409
        task_id = state.task_manager.create(task_type)
        state._running_list_kind = task_type

    async def _wrapped() -> None:
        try:
            await coro_func(task_id, *args)
        except asyncio.CancelledError:
            state.task_manager.update(task_id, status=TaskStatus.CANCELED)
        except Exception as exc:
            logger.exception("清单任务失败")
            state.task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error=_user_error(exc),
            )
        finally:
            with state._list_task_lock:
                if state._running_list_kind == task_type:
                    state._running_list_kind = None

    coro = _wrapped()
    try:
        future = state.browser.submit_async(coro)
    except LofterError as exc:
        coro.close()
        with state._list_task_lock:
            if state._running_list_kind == task_type:
                state._running_list_kind = None
        state.task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )
        return jsonify(ok=False, error=_user_error(exc)), 500
    state.task_manager.set_future(task_id, future)
    return jsonify(task_id=task_id)


# ------------------------------------------------------------------
# 后台下载协程（在浏览器事件循环线程中执行）
# ------------------------------------------------------------------


async def _run_post(task_id: str, url: str, fmt: str = "md", dl_dir: str = "") -> None:
    """后台执行单篇文章下载。"""
    from downloader.pipeline import DownloadPipeline

    state.task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(state.browser)
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
            state.task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error="未能提取文章内容，请检查链接是否正确",
            )
    except asyncio.CancelledError:
        state.task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("下载文章失败")
        state.task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )


async def _run_list_blog(task_id: str, user_id: str) -> None:
    """后台执行作者文章清单收集。"""
    from downloader.pipeline import DownloadPipeline

    state.task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(state.browser)
    storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
    ctx = await state.browser.new_context(storage_state=storage)
    try:
        _check_cancelled(task_id)
        # 与下载路径一致：收集前先校验会话，过期给出明确提示而非「未找到文章」
        if SESSION_PATH.exists():
            valid = await verify_session(ctx)
            if not valid:
                state.task_manager.update(
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
            state.task_manager.update(
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
        state.task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("收集清单失败")
        state.task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )
    finally:
        await ctx.close()


async def _run_list_ao3(task_id: str, kind: str, query: str) -> None:
    """后台执行 AO3 清单收集。"""
    client = AO3Client()
    try:
        state.task_manager.update(task_id, status=TaskStatus.RUNNING)
        _check_cancelled(task_id)
        items: list[dict] = []
        name = ""

        if kind == "series":
            series_id = extract_series_id(query)
            if not series_id:
                state.task_manager.update(
                    task_id,
                    status=TaskStatus.FAILED,
                    error="无法解析 AO3 系列链接",
                )
                return
            items = await client.list_series(series_id)
            name = f"series_{series_id}"
        elif kind == "author":
            username = extract_username(query)
            if not username:
                state.task_manager.update(
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
            state.task_manager.update(
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
        state.task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("AO3 清单收集失败")
        state.task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )
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

    state.task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(state.browser)
    _check_cancelled(task_id)
    storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
    ctx = await state.browser.new_context(storage_state=storage)
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
            state.task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error="未找到任何文章，请检查作者 ID 是否正确",
            )
            return

        blog_sub_dir = _sanitize_filename(blog_name or user_id)
        base_dir = _resolve_base_dir(dl_dir)
        state.task_manager.update(
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
                state.task_manager.update(
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
                state.task_manager.update(
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
        state.task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("下载博客失败")
        state.task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )
    finally:
        await ctx.close()


async def _run_likes(task_id: str, fmt: str = "md", dl_dir: str = "") -> None:
    """后台执行喜欢文章下载。"""
    from downloader.pipeline import DownloadPipeline

    state.task_manager.update(task_id, status=TaskStatus.RUNNING)
    pipeline = DownloadPipeline(state.browser)
    _check_cancelled(task_id)
    storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
    ctx = await state.browser.new_context(storage_state=storage)
    try:
        links = await pipeline.collect_likes_links(context=ctx)
        if not links:
            state.task_manager.update(
                task_id,
                status=TaskStatus.FAILED,
                error="未找到任何喜欢文章，请确认已登录并有喜欢内容",
            )
            return

        base_dir = _resolve_base_dir(dl_dir)
        state.task_manager.update(task_id, total=len(links))
        for idx, link in enumerate(links):
            _check_cancelled(task_id)
            try:
                results = await pipeline.run_post(link, context=ctx)
                if results:
                    await _save_results(
                        results, sub_dir="喜欢文章", fmt=fmt, dl_dir=dl_dir
                    )
                state.task_manager.update(
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
                state.task_manager.update(
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
        state.task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("下载喜欢失败")
        state.task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )
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
        state.task_manager.update(task_id, status=TaskStatus.RUNNING)
        state.task_manager.update(task_id, total=len(urls))

        # 任务内作品页缓存：get_work_info 结果复用，
        # 避免「取作者名 + 逐篇取标题」重复抓取同一页面
        info_cache: dict[str, dict] = {}

        async def _get_info(work_id: str) -> dict:
            if work_id not in info_cache:
                info_cache[work_id] = await client.get_work_info(work_id)
            return info_cache[work_id]

        # 确定作者名（用于子目录），取第一篇
        first_author = ""
        first_id = extract_work_id(urls[0]) if urls else None
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
                work_id = extract_work_id(url)
                if not work_id:
                    failure_count += 1
                    state.task_manager.update(
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
                    state.task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=title,
                    )
                except Exception as exc:
                    failure_count += 1
                    reason = _classify_error(exc)
                    logger.warning("AO3 官方下载失败 [%s]: %s", reason, url)
                    state.task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=f"跳过({reason}): {url[:40]}",
                    )
        else:
            # 解析管道
            saver = None
            for idx, url in enumerate(urls):
                _check_cancelled(task_id)
                work_id = extract_work_id(url)
                if not work_id:
                    failure_count += 1
                    state.task_manager.update(
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
                    state.task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=post_dict.get("title", ""),
                    )
                except Exception as exc:
                    failure_count += 1
                    reason = _classify_error(exc)
                    logger.warning("AO3 解析下载失败 [%s]: %s", reason, url)
                    state.task_manager.update(
                        task_id,
                        current=idx + 1,
                        message=f"跳过({reason}): {url[:40]}",
                    )
            if saver is not None:
                await saver.close()

        total = len(urls)
        if success_count == 0 and failure_count > 0:
            state.task_manager.update(
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
        state.task_manager.update(task_id, status=TaskStatus.CANCELED)
    except Exception as exc:
        logger.exception("AO3 下载失败")
        state.task_manager.update(
            task_id, status=TaskStatus.FAILED, error=_user_error(exc)
        )
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
