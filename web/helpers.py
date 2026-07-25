"""任务辅助函数：取消检查、完成标记、错误分类与用户可读错误。"""

from __future__ import annotations

import asyncio

from downloader.ao3 import AO3Error
from downloader.models import Task, TaskStatus
from web import state


def _is_cancelled(task_id: str) -> bool:
    """任务是否已被用户取消。"""
    task = state.task_manager.get(task_id)
    return task is not None and task.status == TaskStatus.CANCELED


def _check_cancelled(task_id: str) -> None:
    """检查任务是否被取消，若是则抛出 CancelledError。"""
    if _is_cancelled(task_id):
        raise asyncio.CancelledError()


def _complete_task(task_id: str, **kwargs: object) -> None:
    """将任务标记为完成；若已被用户取消则不覆盖 CANCELED 状态。"""
    if _is_cancelled(task_id):
        return
    state.task_manager.update(task_id, status=TaskStatus.COMPLETED, **kwargs)


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


def _task_summary_dict(task: Task) -> dict:
    """任务字典的列表摘要版：剔除 result.items 大字段，保留其余小字段。"""
    d = task.to_dict()
    result = d.get("result") or {}
    if "items" in result:
        d["result"] = {k: v for k, v in result.items() if k != "items"}
        d["result"]["items_count"] = len(result["items"])
    return d
