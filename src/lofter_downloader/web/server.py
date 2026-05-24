"""FastAPI 应用入口。

定义所有 REST API 路由和 WebSocket 端点。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from lofter_downloader.config import settings
from lofter_downloader.core.task_manager import Task, TaskStatus, task_manager
from lofter_downloader.utils.exceptions import TaskCanceledError
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)

app = FastAPI(title="LOFTER 下载器", version="0.1.0")

# 挂载静态文件
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """首页，返回 Web UI。"""
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return "<h1>LOFTER 下载器</h1><p>前端模板未找到</p>"


@app.get("/api/login/status")
async def login_status():
    """检查登录状态。"""
    has_cookie = bool(settings.cookie)
    return {"logged_in": has_cookie}


@app.post("/api/login/cookie")
async def login_with_cookie(data: dict):
    """导入 Cookie。"""
    cookie = data.get("cookie", "")
    if not cookie:
        return {"ok": False, "error": "Cookie 不能为空"}
    settings.cookie = cookie
    logger.info("Cookie updated via API")
    return {"ok": True}


@app.delete("/api/login")
async def logout():
    """清除登录信息。"""
    settings.cookie = ""
    logger.info("Cookie cleared via API")
    return {"ok": True}


def _require_login() -> dict | None:
    """检查是否已导入 Cookie，未登录时返回错误响应。"""
    if not settings.cookie:
        return {"ok": False, "error": "请先导入 Cookie 登录后才能使用下载功能"}
    return None


@app.post("/api/download/post")
async def download_post(data: dict):
    """下载单篇文章。"""
    err = _require_login()
    if err:
        return err
    url = data.get("url", "")
    if not url:
        return {"ok": False, "error": "URL 不能为空"}
    task_id = task_manager.create_task("post", {"url": url})
    asyncio.create_task(_run_download_post(task_id, url))
    return {"task_id": task_id}


@app.post("/api/download/blog")
async def download_blog(data: dict):
    """下载作者全部文章。"""
    err = _require_login()
    if err:
        return err
    user_id = data.get("user_id", 0)
    if not user_id:
        return {"ok": False, "error": "user_id 不能为空"}
    task_id = task_manager.create_task("blog", {"user_id": user_id})
    asyncio.create_task(_run_download_blog(task_id, user_id))
    return {"task_id": task_id}


@app.post("/api/download/collection")
async def download_collection(data: dict):
    """下载合集。"""
    err = _require_login()
    if err:
        return err
    url = data.get("url", "")
    if not url:
        return {"ok": False, "error": "URL 不能为空"}
    task_id = task_manager.create_task("collection", {"url": url})
    asyncio.create_task(_run_download_collection(task_id, url))
    return {"task_id": task_id}


@app.post("/api/download/favorites")
async def download_favorites():
    """下载收藏文章。"""
    err = _require_login()
    if err:
        return err
    task_id = task_manager.create_task("favorites", {})
    asyncio.create_task(_run_download_favorites(task_id))
    return {"task_id": task_id}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """查询任务状态。"""
    task = task_manager.get_task(task_id)
    if task is None:
        return {"error": "任务不存在"}
    return _task_to_response(task)


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消任务。"""
    ok = await task_manager.cancel_task(task_id)
    return {"ok": ok}


@app.get("/api/tasks")
async def list_tasks():
    """任务历史列表。"""
    tasks = task_manager.get_all_tasks()
    return [_task_to_response(t) for t in tasks]


@app.get("/api/stats")
async def stats():
    """下载统计。"""
    tasks = task_manager.get_all_tasks()
    return {
        "total": len(tasks),
        "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
        "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
        "failed": sum(1 for t in tasks if t.status == TaskStatus.FAILED),
        "canceled": sum(1 for t in tasks if t.status == TaskStatus.CANCELED),
    }


@app.websocket("/api/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """WebSocket 实时进度推送。"""
    await websocket.accept()

    def callback(task: Task) -> None:
        asyncio.ensure_future(websocket.send_json(_task_to_response(task)))

    task_manager.subscribe(task_id, callback)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        task_manager.unsubscribe(task_id, callback)


def _task_to_response(task: Task) -> dict:
    """将 Task 对象转换为 API 响应字典。"""
    return {
        "task_id": task.task_id,
        "type": task.type,
        "status": task.status.value,
        "progress": task.progress.percentage,
        "message": task.progress.message,
        "error": task.error,
        "created_at": task.created_at.isoformat(),
    }


async def _run_download_post(task_id: str, url: str) -> None:
    """后台执行单篇文章下载。"""
    from lofter_downloader.core.post import PostDownloader
    from lofter_downloader.storage.saver import PostSaver

    task_manager.set_status(task_id, TaskStatus.RUNNING)
    downloader: PostDownloader | None = None
    saver: PostSaver | None = None
    try:
        await task_manager.wait_for_cancel(task_id)
        downloader = PostDownloader()
        saver = PostSaver()
        post = await downloader.run(url)
        await saver.save(post, sub_dir="单篇下载")
        task_manager.set_result(task_id, {"title": post.title, "url": post.url})
    except TaskCanceledError:
        pass
    except Exception as exc:
        logger.exception("Download post failed")
        task_manager.set_error(task_id, str(exc))
    finally:
        if downloader is not None:
            await downloader.close()
        if saver is not None:
            await saver.close()


async def _run_download_blog(task_id: str, user_id: int) -> None:
    """后台执行作者全部文章下载。"""
    from lofter_downloader.core.blog import BlogDownloader
    from lofter_downloader.storage.saver import PostSaver

    task_manager.set_status(task_id, TaskStatus.RUNNING)
    downloader: BlogDownloader | None = None
    saver: PostSaver | None = None
    try:
        await task_manager.wait_for_cancel(task_id)
        downloader = BlogDownloader()
        saver = PostSaver()
        posts = await downloader.run(user_id)
        for idx, post in enumerate(posts):
            await task_manager.wait_for_cancel(task_id)
            task_manager.update_progress(task_id, idx + 1, len(posts), post.title)
            await saver.save(post, sub_dir=str(user_id))
        task_manager.set_result(task_id, {"total": len(posts)})
    except TaskCanceledError:
        pass
    except Exception as exc:
        logger.exception("Download blog failed")
        task_manager.set_error(task_id, str(exc))
    finally:
        if downloader is not None:
            await downloader.close()
        if saver is not None:
            await saver.close()


async def _run_download_collection(task_id: str, url: str) -> None:
    """后台执行合集下载。"""
    from lofter_downloader.core.collection import CollectionDownloader
    from lofter_downloader.storage.saver import PostSaver

    task_manager.set_status(task_id, TaskStatus.RUNNING)
    downloader: CollectionDownloader | None = None
    saver: PostSaver | None = None
    try:
        await task_manager.wait_for_cancel(task_id)
        downloader = CollectionDownloader()
        saver = PostSaver()
        posts = await downloader.run(url)
        for idx, post in enumerate(posts):
            await task_manager.wait_for_cancel(task_id)
            task_manager.update_progress(task_id, idx + 1, len(posts), post.title)
            await saver.save(post, sub_dir="合集")
        task_manager.set_result(task_id, {"total": len(posts)})
    except TaskCanceledError:
        pass
    except Exception as exc:
        logger.exception("Download collection failed")
        task_manager.set_error(task_id, str(exc))
    finally:
        if downloader is not None:
            await downloader.close()
        if saver is not None:
            await saver.close()


async def _run_download_favorites(task_id: str) -> None:
    """后台执行收藏下载。"""
    from lofter_downloader.core.favorites import FavoritesDownloader
    from lofter_downloader.storage.saver import PostSaver

    task_manager.set_status(task_id, TaskStatus.RUNNING)
    downloader: FavoritesDownloader | None = None
    saver: PostSaver | None = None
    try:
        await task_manager.wait_for_cancel(task_id)
        downloader = FavoritesDownloader(cookie=settings.cookie)
        saver = PostSaver()
        posts = await downloader.run()
        for idx, post in enumerate(posts):
            await task_manager.wait_for_cancel(task_id)
            task_manager.update_progress(task_id, idx + 1, len(posts), post.title)
            await saver.save(post, sub_dir="收藏文章")
        task_manager.set_result(task_id, {"total": len(posts)})
    except TaskCanceledError:
        pass
    except Exception as exc:
        logger.exception("Download favorites failed")
        task_manager.set_error(task_id, str(exc))
    finally:
        if downloader is not None:
            await downloader.close()
        if saver is not None:
            await saver.close()
