"""Flask API 路由集成测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

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

    def _submit_async(coro):
        # 测试里不实际执行后台协程，但需关闭以避免「never awaited」警告
        coro.close()
        return MagicMock()

    mock_bm.submit_async = MagicMock(side_effect=_submit_async)
    routes.browser = mock_bm

    # 重置模块级状态
    routes._login_page = None  # noqa: SLF001
    routes._login_context = None  # noqa: SLF001
    routes._login_browser = None  # noqa: SLF001
    routes._login_in_progress = False  # noqa: SLF001
    routes._user_name = ""  # noqa: SLF001
    routes._running_task_id = None  # noqa: SLF001
    routes._running_list_kind = None  # noqa: SLF001
    routes.task_manager = TaskManager()

    return app


def _close_coro(coro) -> None:
    """测试替身：不实际执行后台协程，关闭以避免「never awaited」警告。"""
    coro.close()


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

        routes.browser._playwright = MagicMock()  # noqa: SLF001
        resp = client.post("/api/login/start")
        data = json.loads(resp.data)
        # 非阻塞设计：立即返回 starting，headed 浏览器在后台启动
        assert data["ok"] is True
        assert data["status"] == "starting"
        routes.browser.submit_async.assert_called_once()
        # 测试产生的后台协程需要关闭，避免未等待警告
        coro = routes.browser.submit_async.call_args.args[0]
        coro.close()

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

    def test_download_blog_with_urls_creates_task(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/blog",
            json={"user_id": "12345678", "urls": ["https://test.lofter.com/post/1"]},
        )
        data = json.loads(resp.data)
        assert "task_id" in data

    def test_download_ao3_creates_task(self, client) -> None:  # noqa: ANN001
        import web.routes as routes

        routes._running_task_id = None  # noqa: SLF001
        resp = client.post(
            "/api/download/ao3",
            json={"urls": ["https://archiveofourown.org/works/12345"]},
        )
        data = json.loads(resp.data)
        assert "task_id" in data


class TestListTasks:
    """清单任务端点测试。"""

    def test_list_blog_creates_task(self, client) -> None:  # noqa: ANN001
        resp = client.post("/api/list/blog", json={"user_id": "12345678"})
        data = json.loads(resp.data)
        assert "task_id" in data

    def test_list_ao3_requires_valid_kind(self, client) -> None:  # noqa: ANN001
        resp = client.post("/api/list/ao3", json={"kind": "invalid", "query": "x"})
        assert resp.status_code == 400

    def test_list_ao3_creates_task(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/list/ao3",
            json={"kind": "series", "query": "https://archiveofourown.org/series/123"},
        )
        data = json.loads(resp.data)
        assert "task_id" in data


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


class TestResolveBaseDir:
    """下载目录解析规则。"""

    def test_empty_returns_default_dir(self) -> None:
        from web.routes import DOWNLOAD_DIR, _resolve_base_dir

        assert _resolve_base_dir("") == DOWNLOAD_DIR

    def test_absolute_path_returned_resolved(self, tmp_path) -> None:  # noqa: ANN001
        from web.routes import _resolve_base_dir

        target = tmp_path / "downloads"
        target.mkdir()
        assert _resolve_base_dir(str(target)) == target.resolve()

    def test_relative_path_joins_default_dir(self) -> None:
        from web.routes import DOWNLOAD_DIR, _resolve_base_dir

        assert _resolve_base_dir("my/sub") == DOWNLOAD_DIR / "my/sub"


class TestRunDownloadAo3:
    """AO3 下載後台任務狀態。"""

    @pytest.mark.asyncio
    async def test_all_failed_marks_task_failed(self) -> None:
        import web.routes as routes
        from downloader.ao3 import AO3Client, AO3Error
        from downloader.models import TaskManager, TaskStatus

        routes.task_manager = TaskManager()
        tid = routes.task_manager.create("ao3")
        with patch.object(
            AO3Client, "get_work_info", side_effect=AO3Error("shields up")
        ):
            await routes._run_download_ao3(
                tid,
                ["https://archiveofourown.org/works/12345"],
                "epub",
                "official",
                "",
            )
        task = routes.task_manager.get(tid)
        assert task.status == TaskStatus.FAILED
        assert "shields up" in task.error


class TestDownloadBlogUrls:
    """download/blog 的 urls 参数语义。"""

    def test_empty_urls_rejected(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/blog",
            json={"user_id": "12345678", "urls": []},
        )
        data = json.loads(resp.data)
        assert resp.status_code == 400
        assert "未选择" in data["error"]

    def test_urls_not_list_rejected(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/blog",
            json={"user_id": "12345678", "urls": "https://x/post/1"},
        )
        assert resp.status_code == 400

    def test_urls_non_string_element_rejected(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/blog",
            json={"user_id": "12345678", "urls": [123]},
        )
        assert resp.status_code == 400

    def test_valid_urls_with_blog_name_creates_task(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/blog",
            json={
                "user_id": "12345678",
                "urls": ["https://test.lofter.com/post/1"],
                "blog_name": "测试博客",
            },
        )
        assert "task_id" in json.loads(resp.data)


class TestSubmitAsyncFailure:
    """submit_async 抛 LofterError 时的并发槽位恢复。"""

    def test_lofter_error_resets_running_task(self, client) -> None:  # noqa: ANN001
        import web.routes as routes
        from downloader.exceptions import LofterError

        routes.browser.submit_async = MagicMock(
            side_effect=LofterError("浏览器事件循环未运行")
        )
        resp = client.post(
            "/api/download/post",
            json={"url": "https://test.lofter.com/post/1"},
        )
        assert resp.status_code == 500
        assert routes._running_task_id is None  # noqa: SLF001
        # 任务被标记为 FAILED 而不是永卡 PENDING
        task = routes.task_manager.list_all()[0]
        assert task.status == TaskStatus.FAILED

        # 并发槽位已释放：后续任务不再 409
        routes.browser.submit_async = MagicMock(side_effect=_close_coro)
        resp = client.post(
            "/api/download/post",
            json={"url": "https://test.lofter.com/post/1"},
        )
        assert resp.status_code == 200
        assert "task_id" in json.loads(resp.data)


class TestListTaskMutex:
    """同类清单任务互斥。"""

    def test_same_kind_list_rejected(self, client) -> None:  # noqa: ANN001
        resp1 = client.post("/api/list/blog", json={"user_id": "12345678"})
        assert "task_id" in json.loads(resp1.data)

        resp2 = client.post("/api/list/blog", json={"user_id": "87654321"})
        data = json.loads(resp2.data)
        assert resp2.status_code == 409
        assert data["ok"] is False

    def test_different_kind_list_allowed(self, client) -> None:  # noqa: ANN001
        client.post("/api/list/blog", json={"user_id": "12345678"})
        resp = client.post(
            "/api/list/ao3",
            json={"kind": "series", "query": "https://archiveofourown.org/series/1"},
        )
        assert "task_id" in json.loads(resp.data)


class TestDownloadAo3Validation:
    """AO3 端点参数校验。"""

    def test_invalid_official_format_rejected(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/ao3",
            json={
                "urls": ["https://archiveofourown.org/works/12345"],
                "source": "official",
                "format": "txt",
            },
        )
        assert resp.status_code == 400

    def test_parsed_source_allows_md(self, client) -> None:  # noqa: ANN001
        resp = client.post(
            "/api/download/ao3",
            json={
                "urls": ["https://archiveofourown.org/works/12345"],
                "source": "parsed",
                "format": "md",
            },
        )
        assert "task_id" in json.loads(resp.data)


class TestTasksSummary:
    """/api/tasks 列表摘要与单任务完整 result。"""

    def _create_list_task_with_items(self) -> str:
        import web.routes as routes

        tid = routes.task_manager.create("list_blog")
        routes.task_manager.update(
            tid,
            status=TaskStatus.COMPLETED,
            result={
                "items": [{"url": "https://x/post/1", "title": "t"}],
                "blog_name": "b",
            },
        )
        return tid

    def test_list_strips_items(self, client) -> None:  # noqa: ANN001
        self._create_list_task_with_items()
        data = json.loads(client.get("/api/tasks").data)
        assert "items" not in data[0]["result"]
        assert data[0]["result"]["items_count"] == 1
        assert data[0]["result"]["blog_name"] == "b"

    def test_single_task_keeps_full_result(self, client) -> None:  # noqa: ANN001
        tid = self._create_list_task_with_items()
        data = json.loads(client.get(f"/api/tasks/{tid}").data)
        assert data["result"]["items"] == [{"url": "https://x/post/1", "title": "t"}]


class TestSubDirNaming:
    """路由层子目录命名规则回归保护（断言 save_dict 的 sub_dir）。"""

    @staticmethod
    def _make_ctx() -> MagicMock:
        ctx = MagicMock()
        ctx.close = AsyncMock()
        return ctx

    @staticmethod
    def _make_saver() -> MagicMock:
        saver = MagicMock()
        saver.save_dict = AsyncMock()
        saver.close = AsyncMock()
        return saver

    async def test_run_post_uses_author(self) -> None:
        import web.routes as routes

        routes.task_manager = TaskManager()
        tid = routes.task_manager.create("post")
        pipeline = MagicMock()
        pipeline.run_post = AsyncMock(
            return_value=[{"author": "张三", "title": "标题"}]
        )
        saver = self._make_saver()
        with (
            patch("downloader.pipeline.DownloadPipeline", return_value=pipeline),
            patch("downloader.saver.PostSaver", return_value=saver),
            patch("web.routes.load_storage_state", return_value=None),
        ):
            await routes._run_post(tid, "https://test.lofter.com/post/1")
        assert saver.save_dict.call_args.kwargs["sub_dir"] == "张三"

    async def test_run_blog_uses_blog_name(self) -> None:
        import web.routes as routes

        routes.task_manager = TaskManager()
        routes.browser.new_context = AsyncMock(return_value=self._make_ctx())
        tid = routes.task_manager.create("blog")
        pipeline = MagicMock()
        pipeline.collect_blog_links = AsyncMock(
            return_value=(["https://test.lofter.com/post/1"], "测试博客")
        )
        pipeline.run_post = AsyncMock(return_value=[{"title": "t", "author": "a"}])
        saver = self._make_saver()
        with (
            patch("downloader.pipeline.DownloadPipeline", return_value=pipeline),
            patch("downloader.saver.PostSaver", return_value=saver),
            patch("web.routes.load_storage_state", return_value=None),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await routes._run_blog(tid, "12345678")
        assert saver.save_dict.call_args.kwargs["sub_dir"] == "测试博客"

    async def test_run_blog_selected_uses_passed_blog_name(self) -> None:
        """选中下载（urls 模式）使用前端回传的 blog_name 而非数字 ID。"""
        import web.routes as routes

        routes.task_manager = TaskManager()
        routes.browser.new_context = AsyncMock(return_value=self._make_ctx())
        tid = routes.task_manager.create("blog")
        pipeline = MagicMock()
        pipeline.run_post = AsyncMock(return_value=[{"title": "t", "author": "a"}])
        saver = self._make_saver()
        with (
            patch("downloader.pipeline.DownloadPipeline", return_value=pipeline),
            patch("downloader.saver.PostSaver", return_value=saver),
            patch("web.routes.load_storage_state", return_value=None),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await routes._run_blog(
                tid,
                "12345678",
                urls=["https://test.lofter.com/post/1"],
                blog_name="测试博客",
            )
        assert saver.save_dict.call_args.kwargs["sub_dir"] == "测试博客"

    async def test_run_likes_uses_fixed_dir(self) -> None:
        import web.routes as routes

        routes.task_manager = TaskManager()
        routes.browser.new_context = AsyncMock(return_value=self._make_ctx())
        tid = routes.task_manager.create("likes")
        pipeline = MagicMock()
        pipeline.collect_likes_links = AsyncMock(
            return_value=["https://test.lofter.com/post/1"]
        )
        pipeline.run_post = AsyncMock(return_value=[{"title": "t", "author": "a"}])
        saver = self._make_saver()
        with (
            patch("downloader.pipeline.DownloadPipeline", return_value=pipeline),
            patch("downloader.saver.PostSaver", return_value=saver),
            patch("web.routes.load_storage_state", return_value=None),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await routes._run_likes(tid)
        assert saver.save_dict.call_args.kwargs["sub_dir"] == "喜欢文章"

    async def test_run_download_ao3_uses_author_dir(self) -> None:
        import web.routes as routes

        routes.task_manager = TaskManager()
        tid = routes.task_manager.create("ao3")
        ao3_client = MagicMock()
        ao3_client.get_work_info = AsyncMock(
            return_value={"author": "AO3作者", "title": "t"}
        )
        ao3_client.parse_work = AsyncMock(
            return_value={"title": "t", "author": "AO3作者"}
        )
        ao3_client.close = AsyncMock()
        saver = self._make_saver()
        with (
            patch("web.routes.AO3Client", return_value=ao3_client),
            patch("downloader.saver.PostSaver", return_value=saver),
            patch("web.routes.load_storage_state", return_value=None),
        ):
            await routes._run_download_ao3(
                tid,
                ["https://archiveofourown.org/works/12345"],
                "md",
                "parsed",
                "",
            )
        assert saver.save_dict.call_args.kwargs["sub_dir"] == "AO3/AO3作者"
