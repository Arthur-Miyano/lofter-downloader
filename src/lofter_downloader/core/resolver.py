"""用户 ID 解析模块。

实现 LOFTER 数字用户 ID 与博客域名之间的双向转换。
"""

from __future__ import annotations

import json
import re
from typing import Any

from lofter_downloader.core.spider import Spider
from lofter_downloader.utils.exceptions import ResolveError
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class UserResolver(Spider):
    """用户 ID 解析器。

    通过解析 LOFTER 博客页面中的 JavaScript 数据，实现数字 ID 与域名的转换。
    """

    async def run(self, user_id: int) -> str:  # type: ignore[override]
        """解析用户数字 ID 为博客域名。

        Parameters
        ----------
        user_id : int
            LOFTER 用户数字 ID

        Returns
        -------
        str
            博客域名
        """
        return await self.resolve_domain(user_id)

    async def resolve_domain(self, user_id: int) -> str:
        """根据数字用户 ID 获取博客域名。

        Parameters
        ----------
        user_id : int
            LOFTER 用户数字 ID

        Returns
        -------
        str
            博客域名（不含 .lofter.com 后缀）

        Raises
        ------
        ResolveError
            无法解析时抛出
        """
        url = f"https://www.lofter.com/blog/{user_id}"
        html = await self.fetch(url)
        domain = self._extract_domain_from_html(html)

        if domain is None:
            raise ResolveError(f"Cannot resolve domain for user_id: {user_id}")
        logger.info("Resolved user_id %d → domain: %s", user_id, domain)
        return domain

    async def resolve_user_id(self, domain: str) -> int:
        """根据博客域名获取数字用户 ID。

        Parameters
        ----------
        domain : str
            博客域名（xxx.lofter.com 中的 xxx）

        Returns
        -------
        int
            用户数字 ID

        Raises
        ------
        ResolveError
            无法解析时抛出
        """
        url = f"https://{domain}.lofter.com/"
        html = await self.fetch(url)
        user_id = self._extract_user_id_from_html(html)

        if user_id is None:
            raise ResolveError(f"Cannot resolve user_id for domain: {domain}")
        logger.info("Resolved domain %s → user_id: %d", domain, user_id)
        return user_id

    @staticmethod
    def _extract_domain_from_html(html: str) -> str | None:
        """从页面 HTML 中提取博客域名。"""
        pattern = re.compile(r'"blogName"\s*:\s*"([^"]+)"')
        match = pattern.search(html)
        return match.group(1) if match else None

    @staticmethod
    def _extract_user_id_from_html(html: str) -> int | None:
        """从页面 HTML 中提取用户数字 ID。"""
        pattern = re.compile(r'"userId"\s*:\s*(\d+)')
        match = pattern.search(html)
        if match is None:
            # 尝试从全局数据中提取
            data_pattern = re.compile(r"window\.globalData\s*=\s*({.*?});")
            data_match = data_pattern.search(html)
            if data_match is not None:
                try:
                    data: dict[str, Any] = json.loads(data_match.group(1))
                    return int(data.get("userId", 0)) or None
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
            return None
        return int(match.group(1))
