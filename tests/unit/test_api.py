"""API 模块单元测试。"""

from __future__ import annotations

from lofter_downloader.core.api import LofterAPI, _extract_img_urls


class TestLofterAPI:
    """LofterAPI 单元测试。"""

    def test_extract_img_urls_from_content(self):
        """应从 postContent 的 HTML 中提取图片 URL。"""
        data = {
            "postContent": (
                '<p>正文</p>'
                '<img src="https://example.com/img1.jpg">'
                '<img src="https://example.com/img2.png">'
            ),
        }
        urls = _extract_img_urls(data)
        assert len(urls) == 2
        assert "https://example.com/img1.jpg" in urls
        assert "https://example.com/img2.png" in urls

    def test_extract_img_urls_from_list(self):
        """应从 imgUrls 字段中提取图片 URL。"""
        data = {
            "imgUrls": ["https://example.com/a.jpg", "https://example.com/b.jpg"],
            "postContent": "",
        }
        urls = _extract_img_urls(data)
        assert len(urls) == 2

    def test_extract_img_urls_deduplicates(self):
        """重复的图片 URL 应被去重。"""
        data = {
            "postContent": '<img src="https://example.com/img.jpg">',
            "imgUrls": ["https://example.com/img.jpg"],
        }
        urls = _extract_img_urls(data)
        assert len(urls) == 1

    def test_extract_img_urls_empty(self):
        """无图片时应返回空列表。"""
        data = {"postContent": "<p>无图片</p>"}
        urls = _extract_img_urls(data)
        assert urls == []

    def test_extract_post_detail_success(self):
        """应从 API 响应中提取文章关键字段。"""
        result = {
            "posts": [
                {
                    "postTitle": "测试文章",
                    "postContent": "<p>内容</p>",
                    "blogName": "测试博客",
                    "postTime": "2024-03-15",
                    "tag": "摄影",
                },
            ],
        }
        data = LofterAPI._extract_post_detail(result)
        assert data is not None
        assert data["postTitle"] == "测试文章"
        assert data["blogName"] == "测试博客"
        assert data["postTime"] == "2024-03-15"

    def test_extract_post_detail_no_posts(self):
        """无 posts 字段时应返回 None。"""
        assert LofterAPI._extract_post_detail({}) is None
        assert LofterAPI._extract_post_detail({"posts": []}) is None

    def test_extract_favorites_list(self):
        """应从 batchdata 响应中提取收藏列表。"""
        result = {
            "data": [
                {
                    "postId": "123",
                    "postTitle": "收藏1",
                    "blogName": "博客A",
                    "blogDomain": "blog_a",
                },
                {
                    "postId": "456",
                    "postTitle": "收藏2",
                    "blogName": "博客B",
                    "blogDomain": "blog_b",
                },
            ],
        }
        items = LofterAPI._extract_favorites_list(result)
        assert len(items) == 2
        assert items[0]["postTitle"] == "收藏1"
        assert items[1]["blogDomain"] == "blog_b"

    def test_extract_favorites_list_empty(self):
        """无收藏时应返回空列表。"""
        assert LofterAPI._extract_favorites_list({}) == []
        assert LofterAPI._extract_favorites_list({"data": []}) == []

    def test_parse_dwr_response_invalid(self):
        """无效的 DWR 响应应返回空列表。"""
        posts = LofterAPI._parse_dwr_response("invalid data")
        assert posts == []
