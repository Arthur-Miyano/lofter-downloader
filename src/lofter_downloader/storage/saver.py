"""文章存储模块。

负责将 Post 对象保存为 Markdown 文件，并异步下载文中图片。
"""

from __future__ import annotations

import re
from pathlib import Path

import aiofiles
import httpx

from lofter_downloader.config import settings
from lofter_downloader.core.post import Post
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class PostSaver:
    """文章保存器。

    将文章内容以 Markdown 格式写入文件系统，同时下载文中图片至子目录。

    Parameters
    ----------
    base_dir : Path or None
        下载存储根目录，默认使用配置中的 download_dir
    """

    MARKDOWN_TEMPLATE = """# {title}

- **作者**: {author}
- **发布日期**: {publish_date}
- **原文链接**: {url}

---

{content}
"""

    IMAGE_CONTENT_TYPES = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or settings.download_dir
        self._http_client = httpx.AsyncClient(timeout=30)

    async def save(self, post: Post, sub_dir: str = "") -> Path:
        """保存单篇文章到文件系统。

        Parameters
        ----------
        post : Post
            待保存的文章数据
        sub_dir : str
            子目录名称（如作者名、合集名）

        Returns
        -------
        Path
            文章所在目录的路径
        """
        post_dir = self._make_post_dir(sub_dir, post.title)
        await self._save_markdown(post_dir / "index.md", post)
        await self._save_images(post_dir / "images", post.image_urls)
        return post_dir

    def _make_post_dir(self, sub_dir: str, post_title: str) -> Path:
        """创建文章目录。"""
        safe_title = self._sanitize_filename(post_title)
        path = self._base_dir / sub_dir / safe_title
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _save_markdown(self, path: Path, post: Post) -> None:
        """将文章内容写入 Markdown 文件。"""
        content = self.MARKDOWN_TEMPLATE.format(
            title=post.title,
            author=post.author,
            publish_date=post.publish_date,
            url=post.url,
            content=post.content_markdown,
        )
        async with aiofiles.open(str(path), "w", encoding="utf-8") as f:
            await f.write(content)
        logger.info("Markdown saved: %s", path)

    async def _save_images(self, image_dir: Path, urls: list[str]) -> None:
        """异步下载并保存图片。"""
        if not urls:
            return
        image_dir.mkdir(parents=True, exist_ok=True)

        for idx, url in enumerate(urls, start=1):
            try:
                resp = await self._http_client.get(url)
                resp.raise_for_status()

                ext = self._infer_extension(url, resp.headers.get("content-type", ""))
                img_path = image_dir / f"{idx:03d}{ext}"

                async with aiofiles.open(str(img_path), "wb") as f:
                    await f.write(resp.content)
                logger.debug("Image saved: %s", img_path)

            except Exception:
                logger.warning("Failed to download image: %s", url)

    def _infer_extension(self, url: str, content_type: str) -> str:
        """根据 Content-Type 或 URL 后缀推断图片扩展名。"""
        ext = self.IMAGE_CONTENT_TYPES.get(content_type)
        if ext is not None:
            return ext
        suffix = Path(url).suffix
        if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            return suffix
        return ".jpg"

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """清理文件名中的非法字符。"""
        return re.sub(r'[<>:"/\\|?*]', "_", name).strip(". ")[:200]

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._http_client.aclose()
