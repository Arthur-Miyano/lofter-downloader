"""LOFTER JSON API 调用器。

封装 api.lofter.com 和 DWR 接口，提供文章内容、列表等数据获取。
手机端 API 无需登录即可访问公开内容（经社区验证）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from lofter_downloader.config import settings
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class LofterAPIError(Exception):
    """API 调用异常。"""


class LofterAPI:
    """LOFTER 数据 API 封装。

    提供对 api.lofter.com 各端点的 HTTP 调用。
    通过 settings.lofter_phone_login_auth 配置认证 Token。
    """

    BASE = "https://api.lofter.com"
    HEADERS = {
        "User-Agent": (
            "LOFTER-Android 8.2.34 (RMX3888; Android 12; null) WIFI"
        ),
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "lofproduct": "lofter-android-8.2.34",
        "x-device": "",
    }

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建异步 HTTP 客户端（含 Token 认证头）。"""
        if self._client is None:
            headers = dict(self.HEADERS)
            token = settings.lofter_phone_login_auth
            if token:
                headers["lofter-phone-login-auth"] = token
                logger.debug("API client using token (%d chars)", len(token))
            self._client = httpx.AsyncClient(
                timeout=settings.request_timeout,
                headers=headers,
            )
        return self._client

    async def _post(
        self,
        path: str,
        data: dict[str, str] | None = None,
        full_url: str | None = None,
    ) -> dict[str, Any]:
        """发送 POST 请求到 API 端点。

        Parameters
        ----------
        path : str
            API 路径（如 /oldapi/post/detail.api）
        data : dict or None
            URL 编码的表单数据
        full_url : str or None
            完整 URL（用于 DWR 等非标准端点）

        Returns
        -------
        dict
            解析后的 JSON 响应
        """
        client = await self._get_client()
        url = full_url or f"{self.BASE}{path}"

        for attempt in range(1, settings.max_retries + 1):
            try:
                async with self._semaphore:
                    logger.debug(
                        "API POST [%d/%d]: %s", attempt, settings.max_retries, url
                    )
                    resp = await client.post(url, data=data)
                    resp.raise_for_status()
                    result: dict[str, Any] = resp.json()
                    return result

            except httpx.HTTPStatusError as exc:
                logger.warning("API HTTP %d: %s", exc.response.status_code, url)
                if attempt < settings.max_retries:
                    await asyncio.sleep(settings.request_interval * attempt)
            except Exception as exc:
                logger.warning("API request failed: %s - %s", url, exc)
                if attempt < settings.max_retries:
                    await asyncio.sleep(settings.request_interval * attempt)

        raise LofterAPIError(
            f"API request failed after {settings.max_retries} attempts: {url}"
        )

    async def post_detail(
        self,
        blog_domain: str,
        post_id: str,
    ) -> dict[str, Any] | None:
        """获取单篇文章详情。

        POST /oldapi/post/detail.api

        Parameters
        ----------
        blog_domain : str
            博客域名（不含 .lofter.com）
        post_id : str
            文章 ID（如 465cc3_2b69ab705）

        Returns
        -------
        dict or None
            文章详情数据，包含 postTitle / postContent / blogName / postTime / imgUrls
        """
        data = {
            "supportposttypes": "1,2,3,4,5,6",
            "blogdomain": f"{blog_domain}.lofter.com",
            "offset": "0",
            "requestType": "0",
            "postdigestnew": "1",
            "postid": post_id,
            "checkpwd": "1",
            "needgetpoststat": "1",
        }
        result = await self._post("/oldapi/post/detail.api", data=data)
        return self._extract_post_detail(result)

    async def blog_posts(
        self, blog_domain: str, offset: int = 0
    ) -> list[dict[str, Any]]:
        """获取博客文章列表。

        使用 DWR 协议调用 ArchiveBean.getArchivePostByTime。
        该接口无需认证，返回公开的文章列表。

        Parameters
        ----------
        blog_domain : str
            博客域名
        offset : int
            分页偏移量

        Returns
        -------
        list[dict]
            文章摘要列表
        """
        dwr_body = (
            f"callCount=1\n"
            f"scriptSessionId=${{scriptSessionId}}187\n"
            f"httpSessionId=\n"
            f"c0-scriptName=ArchiveBean\n"
            f"c0-methodName=getArchivePostByTime\n"
            f"c0-id=0\n"
            f"c0-param0=number:0\n"
            f"c0-param1=number:{offset}\n"
            f"c0-param2=number:20\n"
            f"c0-param3=number:0\n"
            f"batchId=1\n"
        )
        url = f"https://{blog_domain}.lofter.com/dwr/call/plaincall/ArchiveBean.getArchivePostByTime.dwr"
        client = await self._get_client()
        try:
            async with self._semaphore:
                resp = await client.post(
                    url,
                    content=dwr_body,
                    headers={
                        "Content-Type": "text/plain",
                        "Origin": f"https://{blog_domain}.lofter.com",
                        "Referer": f"https://{blog_domain}.lofter.com/",
                    },
                )
                resp.raise_for_status()
                return self._parse_dwr_response(resp.text)
        except Exception as exc:
            logger.warning("DWR blog_posts failed for %s: %s", blog_domain, exc)
            return []

    async def favorites(self, offset: int = 0) -> list[dict[str, Any]]:
        """获取当前用户收藏的文章列表。

        POST /v1.1/batchdata.api
        需要设置 lofter-phone-login-auth Token。

        Parameters
        ----------
        offset : int
            分页偏移量

        Returns
        -------
        list[dict]
            收藏的文章摘要列表
        """
        data = {
            "supportposttypes": "1,2,3,4,5,6",
            "offset": str(offset),
            "method": "favorites",
            "postdigestnew": "1",
            "returnData": "1",
            "limit": "18",
        }
        result = await self._post("/v1.1/batchdata.api", data=data)
        return self._extract_favorites_list(result)

    async def verify_token(self) -> dict[str, Any] | None:
        """验证当前 Token 有效性，返回用户基本信息。

        通过请求收藏列表接口判断 Token 是否有效。
        LOFTER API 在未认证或 Token 无效时返回特定错误格式。
        """
        try:
            result = await self._post(
                "/v1.1/batchdata.api",
                data={
                    "method": "favorites",
                    "offset": "0",
                    "limit": "1",
                    "returnData": "1",
                },
            )
            # 成功响应：result == "success" 且有数据
            if result.get("result") in ("success", True, "ok"):
                blog_name = (
                    result.get("blogName", "")
                    or result.get("userName", "")
                    or result.get("user", {}).get("blogName", "")
                )
                if blog_name:
                    return {
                        "blogName": blog_name,
                        "userId": result.get("userId", "")
                        or result.get("user", {}).get("userId", ""),
                        "avatar": result.get("avatar", "")
                        or result.get("user", {}).get("avatar", ""),
                    }
            # 部分成功但有数据
            data = result.get("data", [])
            if data and isinstance(data, list) and len(data) > 0:
                first = data[0]
                blog_name = first.get("blogName", "")
                if blog_name:
                    return {
                        "blogName": blog_name,
                        "userId": first.get("userId", ""),
                        "avatar": first.get("avatar", ""),
                    }
            return None
        except Exception as exc:
            logger.debug("Token verification failed: %s", exc)
            return None

    @staticmethod
    def _extract_post_detail(result: dict[str, Any]) -> dict[str, Any] | None:
        """从 post_detail 响应中提取关键字段。"""
        if not result:
            return None
        posts = result.get("posts", [])
        if not posts:
            return None
        post = posts[0] if isinstance(posts, list) else posts
        return {
            "postTitle": post.get("postTitle", "") or post.get("title", ""),
            "postContent": post.get("postContent", "") or post.get("content", ""),
            "blogName": post.get("blogName", "") or result.get("blogName", ""),
            "postTime": post.get("postTime", "") or post.get("date", ""),
            "tag": post.get("tag", ""),
            "imgUrls": _extract_img_urls(post),
        }

    @staticmethod
    def _extract_favorites_list(result: dict[str, Any]) -> list[dict[str, Any]]:
        """从 batchdata 响应中提取收藏列表。"""
        items = (
            result.get("data", [])
            or result.get("posts", [])
            or result.get("list", [])
        )
        if not items:
            return []
        return [
            {
                "postTitle": item.get("postTitle", "") or item.get("title", ""),
                "postContent": item.get("postContent", "") or item.get("content", ""),
                "blogName": item.get("blogName", ""),
                "postTime": item.get("postTime", "") or item.get("date", ""),
                "postId": str(item.get("postId", "")),
                "blogDomain": item.get("blogDomain", ""),
                "imgUrls": _extract_img_urls(item),
            }
            for item in items
            if item.get("postId")
        ]

    @staticmethod
    def _parse_dwr_response(text: str) -> list[dict[str, Any]]:
        """解析 DWR 文本协议响应为结构化数据。"""
        posts: list[dict[str, Any]] = []
        try:
            for line in text.split("\n"):
                if line.startswith("s2="):
                    import json
                    data = json.loads(line[3:])
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get("postId"):
                                posts.append({
                                    "postTitle": item.get("postTitle", ""),
                                    "postId": str(item.get("postId", "")),
                                    "blogName": item.get("blogName", ""),
                                    "postTime": item.get("postTime", ""),
                                })
        except Exception as exc:
            logger.debug("Failed to parse DWR response: %s", exc)
        return posts

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _extract_img_urls(data: dict[str, Any]) -> list[str]:
    """从文章数据中提取图片 URL 列表。"""
    content = data.get("postContent", "") or data.get("content", "") or ""
    import re
    pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']')
    urls = [m.group(1) for m in pattern.finditer(content)]
    # 也检查单独的 imgUrls 字段
    img_list = data.get("imgUrls", []) or data.get("images", []) or data.get("imgs", [])
    if isinstance(img_list, list):
        urls.extend(img_list)
    return list(dict.fromkeys(urls))  # 去重
