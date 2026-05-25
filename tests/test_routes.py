"""Flask API 路由集成测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from downloader.models import TaskManager, TaskStatus
from web.routes import api


@pytest.fixture
def app():
    """创建测试 Flask 应用。"""
    app = Flask(__name__)
    app.register_blueprint(api)
    app.config["TESTING"] = True

    # 注入 mock browser
    import web.routes as routes

    mock_bm = MagicMock()
    mock_bm.submit = MagicMock(return_value={"logged_in": False})
    mock_bm.submit_async = MagicMock()
    routes.browser = mock_bm

    # 重置模块级状态
    routes._login_page = None  # noqa: SLF001
    routes._login_context = None  # noqa: SLF001
    routes._login_browser = None  # noqa: SLF001
    routes._login_in_progress = False  # noqa: SLF001
    routes._user_name = ""  # noqa: SLF001
    routes._running_task_id = None  # noqa: SLF001
    routes.task_manager = TaskManager()

    return app


@pytest.fixture
def client(app):
    return app.test_client()


class TestLogin:
    """登录相关端点测试。"""

    def test_login_status_not_logged_in(self, client) -> None:  # noqa: ANN001
        with patch("web.routes.SESSION_PATH") as mock_path:
            mock_path.exists.return_value = False
            resp = client.get("/api/login/status")
            data = json.loads(resp.data)
            assert data["logged_in"] is False

    def test_start_login(self, client) -> None:  # noqa: ANN001
        import web.routes as routes

        routes.browser.submit = MagicMock(
            return_value={
                "status": "ready",
                "message": "请在浏览器中完成登录",
            }
        )
        routes.browser._playwright = MagicMock()  # noqa: SLF001
        resp = client.post("/api/login/start")
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["status"] == "ready"

    def test_check_login_not_started(self, client) -> None:  # noqa: ANN001
        resp = client.post("/api/login/check")
        data = json.loads(resp.data)
        assert data["ok"] is False  # 尚未启动登录

    def test_logout(self, client) -> None:  # noqa: ANN001
        resp = client.delete("/api/login")
        data = json.loads(resp.data)
        assert data["ok"] is True


class TestDownloads:
    """下载端点测试。"""

    def test_download_post_missing_url(self, client) -> None:  # noqa: ANN001
        resp = client.post("/api/download/post", json={})
        data = json.loads(resp.data)
        assert data["ok"] is False

    def test_download_post_creates_task(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/post",
            json={"url": "https://test.lofter.com/post/1"},
        )
        data = json.loads(resp.data)
        assert "task_id" in data

    def test_download_blog_missing_id(self, client) -> None:  # noqa: ANN001
        resp = client.post("/api/download/blog", json={})
        data = json.loads(resp.data)
        assert data["ok"] is False

    def test_download_blog_creates_task(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/blog",
            json={"user_id": "12345678"},
        )
        data = json.loads(resp.data)
        assert "task_id" in data

    def test_download_likes_without_session(self, client) -> None:  # noqa: ANN001
        with patch("web.routes.SESSION_PATH") as mock_path:
            mock_path.exists.return_value = False
            resp = client.post("/api/download/likes")
            assert resp.status_code == 403


class TestTasks:
    """任务管理端点测试。"""

    def test_list_tasks_empty(self, client) -> None:  # noqa: ANN001
        resp = client.get("/api/tasks")
        data = json.loads(resp.data)
        assert data == []

    def test_get_task_not_found(self, client) -> None:  # noqa: ANN001
        resp = client.get("/api/tasks/nonexistent")
        assert resp.status_code == 404

    def test_cancel_nonexistent_task(self, client) -> None:  # noqa: ANN001
        resp = client.post("/api/tasks/nonexistent/cancel")
        data = json.loads(resp.data)
        assert data["ok"] is False

    def test_create_and_get_task(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/post",
            json={"url": "https://test.lofter.com/post/1"},
        )
        data = json.loads(resp.data)
        task_id = data["task_id"]

        resp = client.get(f"/api/tasks/{task_id}")
        data = json.loads(resp.data)
        assert data["task_id"] == task_id
        assert data["type"] == "post"
        assert data["status"] == "pending"

    def test_concurrent_download_rejected(self, client) -> None:  # noqa: ANN001
        """同时提交两个下载任务，第二个应被拒绝。"""
        import web.routes as routes

        routes._running_task_id = "fake_running"  # noqa: SLF001
        task = routes.task_manager.create("post")
        routes.task_manager.update(task, status=TaskStatus.RUNNING)
        routes._running_task_id = task  # noqa: SLF001

        resp = client.post(
            "/api/download/post",
            json={"url": "https://test.lofter.com/post/1"},
        )
        data = json.loads(resp.data)
        assert resp.status_code == 409
        assert data["ok"] is False

    def test_download_various_types(self, client) -> None:  # noqa: ANN001
        """验证三种下载类型均可创建任务。"""
        import web.routes as routes

        # 喜欢（需模拟已登录）
        with patch("web.routes.SESSION_PATH") as mock_path:
            mock_path.exists.return_value = True
            resp = client.post("/api/download/likes")
            assert json.loads(resp.data)["task_id"]

        # 重置并发控制，模拟第一个任务已完成
        routes._running_task_id = None  # noqa: SLF001

        # 作者
        resp = client.post(
            "/api/download/blog",
            json={"user_id": "12345678"},
        )
        assert json.loads(resp.data)["task_id"]

    def test_cancel_task_invalid_status(self, client) -> None:  # noqa: ANN001
        """已完成的任务不能被取消。"""
        import web.routes as routes

        tid = routes.task_manager.create("post")
        routes.task_manager.update(tid, status=TaskStatus.COMPLETED)

        resp = client.post(f"/api/tasks/{tid}/cancel")
        data = json.loads(resp.data)
        assert data["ok"] is False

    def test_download_likes_requires_login(self, client) -> None:  # noqa: ANN001
        """喜欢下载需要登录。"""
        with patch("web.routes.SESSION_PATH") as mock_path:
            mock_path.exists.return_value = False
            resp = client.post("/api/download/likes")
            assert resp.status_code == 403
            data = json.loads(resp.data)
            assert "登录" in data["error"]
