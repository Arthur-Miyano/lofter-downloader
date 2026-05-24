"""API 端点集成测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lofter_downloader.web.server import app

client = TestClient(app)


class TestAPIEndpoints:
    """FastAPI 端点集成测试。"""

    def _login(self):
        """辅助方法：导入测试 Cookie。"""
        client.post("/api/login/cookie", json={"cookie": "test_session=abc"})

    def _logout(self):
        """辅助方法：清除登录。"""
        client.delete("/api/login")

    def test_index_returns_html(self):
        """首页应返回 HTML。"""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_login_status(self):
        """登录状态接口应返回 JSON。"""
        resp = client.get("/api/login/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "has_token" in data
        assert "has_cookie" in data

    def test_login_with_cookie(self):
        """Cookie 登录接口应返回 ok。"""
        self._logout()
        resp = client.post("/api/login/cookie", json={"cookie": "test=123"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_login_with_empty_cookie(self):
        """空 Cookie 应返回错误。"""
        resp = client.post("/api/login/cookie", json={"cookie": ""})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_login_with_token(self):
        """Token 登录接口应返回 ok。"""
        self._logout()
        resp = client.post("/api/login/token", json={"token": "test_token_123"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_login_with_empty_token(self):
        """空 Token 应返回错误。"""
        resp = client.post("/api/login/token", json={"token": ""})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_login_verify_without_auth(self):
        """未认证时验证应返回错误。"""
        self._logout()
        resp = client.post("/api/login/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is False

    def test_login_verify_with_token(self):
        """有 Token 时验证应尝试连接。"""
        self._logout()
        client.post("/api/login/token", json={"token": "dummy_token"})
        resp = client.post("/api/login/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        self._logout()

    def test_login_status_shows_auth_mode(self):
        """登录状态应返回 auth_mode。"""
        self._logout()
        resp = client.get("/api/login/status")
        data = resp.json()
        assert "auth_mode" in data

    def test_logout(self):
        """登出接口应返回 ok。"""
        resp = client.delete("/api/login")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_download_post_without_login(self):
        """公开内容下载不需要登录。"""
        self._logout()
        resp = client.post(
            "/api/download/post",
            json={"url": "http://example.lofter.com/post/1_1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # 即使未登录也会创建任务（下载会失败但流程正常）
        assert "task_id" in data

    def test_download_blog_without_login(self):
        """公开博客下载不需要登录。"""
        self._logout()
        resp = client.post("/api/download/blog", json={"user_id": 12345})
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data

    def test_download_post_returns_task_id(self):
        """合法请求应返回 task_id。"""
        self._login()
        resp = client.post(
            "/api/download/post",
            json={"url": "http://example.lofter.com/post/1_1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        self._logout()

    def test_download_favorites_without_auth(self):
        """未认证时下载收藏应返回错误。"""
        self._logout()
        resp = client.post("/api/download/favorites", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is False
        assert "Token" in data.get("error", "") or "Cookie" in data.get("error", "")

    def test_get_nonexistent_task_returns_error(self):
        """不存在的任务应返回错误。"""
        resp = client.get("/api/tasks/nonexistent")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_list_tasks(self):
        """任务列表接口应返回数组。"""
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_stats(self):
        """统计接口应返回数字。"""
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert isinstance(data["total"], int)
