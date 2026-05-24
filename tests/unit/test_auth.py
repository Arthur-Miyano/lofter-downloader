"""登录模块单元测试。"""

from __future__ import annotations

from lofter_downloader.core.auth import AuthManager


class TestAuthManager:
    """AuthManager 单元测试。"""

    async def test_save_and_load_cookie(self, tmp_path):
        """保存后的 Cookie 应能正确加载解密。"""
        auth = AuthManager(key_file=tmp_path)
        auth.save_cookie("test_cookie_value")
        loaded = auth.load_cookie()
        assert loaded == "test_cookie_value"

    async def test_load_cookie_returns_none_when_no_file(self, tmp_path):
        """无保存的会话文件时应返回 None。"""
        auth = AuthManager(key_file=tmp_path)
        assert auth.load_cookie() is None

    async def test_has_session(self, tmp_path):
        """应正确判断会话是否存在。"""
        auth = AuthManager(key_file=tmp_path)
        assert not auth.has_session()
        auth.save_cookie("test")
        assert auth.has_session()

    async def test_clear_session(self, tmp_path):
        """清除会话后应不再存在。"""
        auth = AuthManager(key_file=tmp_path)
        auth.save_cookie("test")
        assert auth.has_session()
        auth.clear_session()
        assert not auth.has_session()
