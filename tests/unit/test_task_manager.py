"""任务调度模块单元测试。"""

from __future__ import annotations

import pytest

from lofter_downloader.core.task_manager import TaskManager, TaskStatus


class TestTaskManager:
    """TaskManager 单元测试。"""

    def setup_method(self):
        self.manager = TaskManager()

    def test_create_task_returns_id(self):
        """创建任务应返回非空 ID。"""
        task_id = self.manager.create_task("post", {"url": "http://example.com"})
        assert task_id
        assert isinstance(task_id, str)
        assert len(task_id) > 0

    def test_get_task_returns_none_for_missing(self):
        """不存在的任务应返回 None。"""
        assert self.manager.get_task("nonexistent") is None

    def test_create_and_get_task(self):
        """创建后应能通过 ID 获取任务。"""
        task_id = self.manager.create_task("post", {"url": "http://example.com"})
        task = self.manager.get_task(task_id)
        assert task is not None
        assert task.task_id == task_id
        assert task.type == "post"
        assert task.status == TaskStatus.PENDING

    def test_set_status(self):
        """应能修改任务状态。"""
        task_id = self.manager.create_task("post")
        self.manager.set_status(task_id, TaskStatus.RUNNING)
        task = self.manager.get_task(task_id)
        assert task.status == TaskStatus.RUNNING

    def test_update_progress(self):
        """应能更新任务进度。"""
        task_id = self.manager.create_task("post")
        self.manager.update_progress(task_id, 3, 10, "下载中")
        task = self.manager.get_task(task_id)
        assert task.progress.current == 3
        assert task.progress.total == 10
        assert task.progress.percentage == 0.3

    def test_get_all_tasks_returns_recent_first(self):
        """get_all_tasks 应按创建时间倒序排列。"""
        id1 = self.manager.create_task("post")
        id2 = self.manager.create_task("blog")
        tasks = self.manager.get_all_tasks()
        assert len(tasks) == 2
        assert tasks[0].task_id == id2
        assert tasks[1].task_id == id1

    async def test_cancel_task(self):
        """取消任务后状态应为 CANCELED。"""
        task_id = self.manager.create_task("post")
        ok = await self.manager.cancel_task(task_id)
        assert ok
        task = self.manager.get_task(task_id)
        assert task.status == TaskStatus.CANCELED

    async def test_cancel_nonexistent_task(self):
        """取消不存在的任务应返回 False。"""
        ok = await self.manager.cancel_task("nonexistent")
        assert not ok

    def test_set_result(self):
        """设置结果后状态应为 COMPLETED。"""
        task_id = self.manager.create_task("post")
        self.manager.set_result(task_id, {"total": 5})
        task = self.manager.get_task(task_id)
        assert task.status == TaskStatus.COMPLETED
        assert task.result == {"total": 5}

    def test_set_error(self):
        """设置错误后状态应为 FAILED。"""
        task_id = self.manager.create_task("post")
        self.manager.set_error(task_id, "网络错误")
        task = self.manager.get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.error == "网络错误"
