"""ID 解析模块单元测试。"""

from __future__ import annotations

from lofter_downloader.core.resolver import UserResolver


class TestUserResolver:
    """UserResolver 单元测试。"""

    def test_extract_domain_from_html(self):
        """应从 HTML 中正确提取博客域名。"""
        html = '"blogName": "traveler_xiao"'
        domain = UserResolver._extract_domain_from_html(html)
        assert domain == "traveler_xiao"

    def test_extract_domain_from_html_not_found(self):
        """HTML 中无域名信息时应返回 None。"""
        domain = UserResolver._extract_domain_from_html("<html></html>")
        assert domain is None

    def test_extract_user_id_from_html(self):
        """应从 HTML 中正确提取用户 ID。"""
        html = '"userId": 12345'
        user_id = UserResolver._extract_user_id_from_html(html)
        assert user_id == 12345

    def test_extract_user_id_from_global_data(self):
        """应从 window.globalData 中正确提取用户 ID。"""
        html = 'window.globalData = {"userId": 67890, "blogName": "test"};'
        user_id = UserResolver._extract_user_id_from_html(html)
        assert user_id == 67890

    def test_extract_user_id_not_found(self):
        """HTML 中无用户 ID 时应返回 None。"""
        user_id = UserResolver._extract_user_id_from_html("<html></html>")
        assert user_id is None
