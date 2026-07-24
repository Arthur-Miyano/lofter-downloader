"""TaskManager 线程安全与任务管理测试。"""

from __future__ import annotations

import threading
import time

from downloader.models import MAX_TASK_HISTORY, TaskManager, TaskStatus


class TestTaskManager:
    def test_create_task(self, task_manager: TaskManager) -> None:
        tid = task_manager.create("post")
        assert len(tid) == 8
        task = task_manager.get(tid)
        assert task is not None
        assert task.type == "post"
        assert task.status == TaskStatus.PENDING

    def test_update_task(self, task_manager: TaskManager) -> None:
        tid = task_manager.create("blog")
        task_manager.update(tid, status=TaskStatus.RUNNING, current=5, total=10)
        task = task_manager.get(tid)
        assert task is not None
        assert task.status == TaskStatus.RUNNING
        assert task.current == 5
        assert task.total == 10

    def test_list_all_sorted(self, task_manager: TaskManager) -> None:
        tid1 = task_manager.create("post")
        time.sleep(0.01)
        tid2 = task_manager.create("blog")

        tasks = task_manager.list_all()
        assert len(tasks) == 2
        # 倒序：最新创建的在前
        assert tasks[0].task_id == tid2
        assert tasks[1].task_id == tid1

    def test_cancel_running_task(self, task_manager: TaskManager) -> None:
        tid = task_manager.create("blog")
        task_manager.update(tid, status=TaskStatus.RUNNING)

        # 无 Future 时 cancel 设置状态但返回 False
        result = task_manager.cancel(tid)
        assert result is False
        task = task_manager.get(tid)
        assert task is not None
        assert task.status == TaskStatus.CANCELED

    def test_cancel_with_future(self, task_manager: TaskManager) -> None:
        from unittest.mock import MagicMock

        tid = task_manager.create("blog")
        task_manager.update(tid, status=TaskStatus.RUNNING)

        mock_future = MagicMock()
        mock_future.cancel.return_value = True
        task_manager.set_future(tid, mock_future)

        result = task_manager.cancel(tid)
        assert result is True
        mock_future.cancel.assert_called_once()

    def test_cleanup_removes_old_tasks(self, task_manager: TaskManager) -> None:
        # 创建超出 MAX_TASK_HISTORY 的任务
        for _ in range(MAX_TASK_HISTORY + 10):
            task_manager.create("post")
        # 标记所有为完成
        for task in task_manager.list_all():
            task_manager.update(task.task_id, status=TaskStatus.COMPLETED)

        removed = task_manager.cleanup()
        assert removed == 10
        tasks = task_manager.list_all()
        assert len(tasks) == MAX_TASK_HISTORY

    def test_cleanup_preserves_running(self, task_manager: TaskManager) -> None:
        # 运行中的任务不会被清理
        running_id = task_manager.create("blog")
        task_manager.update(running_id, status=TaskStatus.RUNNING)

        for _ in range(MAX_TASK_HISTORY + 5):
            tid = task_manager.create("post")
            task_manager.update(tid, status=TaskStatus.COMPLETED)

        removed = task_manager.cleanup()
        assert removed == 5

        # 运行中的任务仍在
        running_task = task_manager.get(running_id)
        assert running_task is not None
        assert running_task.status == TaskStatus.RUNNING

    def test_thread_safety(self) -> None:
        """多线程并发创建/更新任务不崩溃。"""
        mgr = TaskManager()
        errors = []

        def _worker() -> None:
            try:
                for _ in range(50):
                    tid = mgr.create("post")
                    mgr.update(tid, status=TaskStatus.COMPLETED, current=1, total=1)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 无异常
        assert not errors
        # 所有任务被正确追踪
        tasks = mgr.list_all()
        assert len(tasks) == 200
