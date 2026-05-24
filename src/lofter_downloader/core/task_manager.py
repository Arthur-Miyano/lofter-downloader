"""异步任务调度模块。"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from lofter_downloader.utils.exceptions import TaskCanceledError
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class TaskStatus(str, Enum):
    """任务状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class Progress:
    """任务进度信息。"""

    current: int = 0
    total: int = 0
    message: str = ""

    @property
    def percentage(self) -> float:
        """计算进度百分比。"""
        if self.total == 0:
            return 0.0
        return min(self.current / self.total, 1.0)


@dataclass
class Task:
    """异步任务数据模型。"""

    task_id: str
    type: str
    params: dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    progress: Progress = field(default_factory=Progress)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class TaskManager:
    """异步任务管理器。

    负责创建、追踪、取消和查询异步下载任务。
    支持通过 callback 函数实时推送进度。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._progress_callbacks: dict[str, list[Any]] = {}

    def create_task(
        self,
        task_type: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """创建并注册一个新任务。

        Parameters
        ----------
        task_type : str
            任务类型（post / blog / collection / favorites）
        params : dict or None
            任务参数字典

        Returns
        -------
        str
            任务 ID
        """
        task_id = str(uuid.uuid4())
        task = Task(
            task_id=task_id,
            type=task_type,
            params=params or {},
        )
        self._tasks[task_id] = task
        logger.info("Task created: %s (%s)", task_id, task_type)
        return task_id

    def get_task(self, task_id: str) -> Task | None:
        """根据 ID 获取任务。"""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[Task]:
        """获取所有任务列表（按创建时间倒序）。"""
        return sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )

    def update_progress(
        self,
        task_id: str,
        current: int,
        total: int,
        message: str = "",
    ) -> None:
        """更新任务进度并通知所有监听器。

        Parameters
        ----------
        task_id : str
            任务 ID
        current : int
            当前进度
        total : int
            总进度
        message : str
            进度消息
        """
        task = self._tasks.get(task_id)
        if task is None:
            return

        task.progress = Progress(current=current, total=total, message=message)
        self._notify_progress(task_id)

    def set_status(self, task_id: str, status: TaskStatus) -> None:
        """设置任务状态。"""
        task = self._tasks.get(task_id)
        if task is not None:
            task.status = status
            self._notify_progress(task_id)

    def set_result(self, task_id: str, result: dict[str, Any]) -> None:
        """设置任务结果。"""
        task = self._tasks.get(task_id)
        if task is not None:
            task.result = result
            task.status = TaskStatus.COMPLETED
            self._notify_progress(task_id)

    def set_error(self, task_id: str, error: str) -> None:
        """设置任务错误。"""
        task = self._tasks.get(task_id)
        if task is not None:
            task.error = error
            task.status = TaskStatus.FAILED
            self._notify_progress(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """取消一个正在运行的任务。

        Parameters
        ----------
        task_id : str
            任务 ID

        Returns
        -------
        bool
            是否成功取消
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False

        task.status = TaskStatus.CANCELED
        task._cancel_event.set()
        self._notify_progress(task_id)
        logger.info("Task canceled: %s", task_id)
        return True

    async def wait_for_cancel(self, task_id: str) -> None:
        """检查任务是否被取消，如果被取消则抛出异常。

        Raises
        ------
        TaskCanceledError
            任务被取消时抛出
        """
        task = self._tasks.get(task_id)
        if task is not None and task._cancel_event.is_set():
            raise TaskCanceledError(f"Task {task_id} was canceled")

    def subscribe(self, task_id: str, callback: Any) -> None:
        """订阅任务进度更新。"""
        if task_id not in self._progress_callbacks:
            self._progress_callbacks[task_id] = []
        self._progress_callbacks[task_id].append(callback)

    def unsubscribe(self, task_id: str, callback: Any) -> None:
        """取消订阅任务进度更新。"""
        callbacks = self._progress_callbacks.get(task_id, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def _notify_progress(self, task_id: str) -> None:
        """通知所有进度监听器。"""
        callbacks = self._progress_callbacks.get(task_id, [])
        task = self._tasks.get(task_id)
        if task is None:
            return
        for callback in callbacks:
            callback(task)


# 全局任务管理器实例
task_manager = TaskManager()
