"""数据模型：Post、Task、TaskManager。

TaskManager 含线程安全保护（threading.Lock），
支持并发安全的任务创建、更新、取消和内存清理。
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)

MAX_TASK_HISTORY = 100  # 最多保留的已完成/失败/取消任务数


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class Post:
    """一篇下载完成的文章。"""

    url: str
    title: str
    author: str = ""
    publish_date: str = ""
    content_html: str = ""
    content_markdown: str = ""
    image_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "publish_date": self.publish_date,
            "content_html": self.content_html,
            "content_markdown": self.content_markdown,
            "image_urls": self.image_urls,
        }


@dataclass
class Task:
    """下载任务追踪。"""

    task_id: str
    type: str  # "post" | "blog" | "collection" | "favorites"
    status: TaskStatus = TaskStatus.PENDING
    current: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    result: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _future: object | None = field(default=None, repr=False)

    @property
    def progress(self) -> float:
        if self.total == 0:
            return 0.0
        return min(self.current / self.total, 1.0)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "type": self.type,
            "status": self.status.value,
            "progress": self.progress,
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
        }


class TaskManager:
    """线程安全的内存任务管理器。

    在 Flask 请求线程与浏览器事件循环线程间共享，所有操作加锁。
    自动清理旧任务（保留最近 MAX_TASK_HISTORY 条）。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, task_type: str, _params: dict | None = None) -> str:
        """创建新任务，返回 task_id。"""
        task_id = str(uuid.uuid4())[:8]
        task = Task(task_id=task_id, type=task_type)
        with self._lock:
            self._tasks[task_id] = task
        return task_id

    def get(self, task_id: str) -> Task | None:
        """获取单个任务。"""
        with self._lock:
            return self._tasks.get(task_id)

    def list_all(self) -> list[Task]:
        """列出所有任务（按创建时间倒序）。"""
        with self._lock:
            return sorted(
                self._tasks.values(), key=lambda t: t.created_at, reverse=True
            )

    def update(self, task_id: str, **kwargs: object) -> None:
        """更新任务字段（线程安全）。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                for k, v in kwargs.items():
                    if hasattr(task, k):
                        setattr(task, k, v)

    def cancel(self, task_id: str) -> bool:
        """取消任务：设置 CANCELED 状态 + 向协程发送 CancelledError。

        返回 True 表示成功发送取消信号，False 表示仅有标志位被设置。
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.status = TaskStatus.CANCELED
            future = task._future
            task._future = None
        # 在锁外取消 Future，避免死锁
        if future is not None:
            try:
                return future.cancel()
            except Exception:
                pass
        return False

    def set_future(self, task_id: str, future: object) -> None:
        """关联 Future 对象到任务（用于取消操作）。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task._future = future

    def cleanup(self) -> int:
        """裁剪旧任务至 MAX_TASK_HISTORY 条（保留运行中的任务）。

        返回裁剪的任务数。
        """
        with self._lock:
            running = {
                tid: t
                for tid, t in self._tasks.items()
                if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
            }
            finished = {
                tid: t
                for tid, t in self._tasks.items()
                if t.status not in (TaskStatus.PENDING, TaskStatus.RUNNING)
            }
            kept = dict(
                sorted(
                    finished.items(),
                    key=lambda kv: kv[1].created_at,
                    reverse=True,
                )[:MAX_TASK_HISTORY]
            )
            removed = len(finished) - len(kept)
            self._tasks = {**running, **kept}
        if removed > 0:
            logger.info("裁剪了 %d 条旧任务记录", removed)
        return removed

    def clear_finished(self) -> int:
        """清除所有已完成/失败/取消的任务（登出时调用）。

        返回清除的任务数。
        """
        with self._lock:
            running = {
                tid: t
                for tid, t in self._tasks.items()
                if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
            }
            removed = len(self._tasks) - len(running)
            self._tasks = running
        if removed > 0:
            logger.info("登出清理了 %d 条任务记录", removed)
        return removed
