"""任务管理端点：任务列表、单任务状态查询、取消运行中任务。"""

from __future__ import annotations

import logging

from flask import jsonify
from flask.typing import ResponseReturnValue

from downloader.models import TaskStatus
from web import state
from web.helpers import _task_summary_dict

logger = logging.getLogger(__name__)


@state.api.route("/tasks")
def list_tasks() -> ResponseReturnValue:
    """所有任务列表（按创建时间倒序）。

    清单任务的 result.items 可能含数百条记录，轮询开销大，
    列表响应中替换为 items_count 摘要；完整 result 由 /api/tasks/<id> 提供。
    """
    return jsonify([_task_summary_dict(t) for t in state.task_manager.list_all()])


@state.api.route("/tasks/<task_id>")
def get_task(task_id: str) -> ResponseReturnValue:
    """单个任务状态。"""
    task = state.task_manager.get(task_id)
    if task is None:
        return jsonify(error="任务不存在"), 404
    return jsonify(task.to_dict())


@state.api.route("/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task(task_id: str) -> ResponseReturnValue:
    """取消运行中的任务（设置标志位 + 触发 CancelledError）。

    注意：取消后立即释放并发槽位（_running_task_id / _running_list_kind），
    旧协程会运行到下一个取消断点（_check_cancelled / should_cancel）
    才真正停止，期间新任务可能与旧协程短暂并存。这是可接受的取舍：
    等待旧协程完全退出会阻塞取消请求的响应。
    """
    task = state.task_manager.get(task_id)
    if task is None:
        return jsonify(ok=False, error="任务不存在"), 404
    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        cancelled = state.task_manager.cancel(task_id)
        if task_id == state._running_task_id:
            state._running_task_id = None
        if task.type == state._running_list_kind:
            with state._list_task_lock:
                if state._running_list_kind == task.type:
                    state._running_list_kind = None
        if not cancelled:
            state.task_manager.update(task_id, status=TaskStatus.CANCELED)
        return jsonify(ok=True)
    return jsonify(ok=False, error="任务不在运行中，无法取消")
