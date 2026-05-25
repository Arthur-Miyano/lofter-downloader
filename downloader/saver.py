"""文章存储模块。

将文章保存为 Markdown 文件，异步下载文中图片。
含重试（指数退避）、Referer 头、非法字符清洗。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

import aiofiles
import httpx

from config import MAX_RETRIES

logger = logging.getLogger(__name__)

IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

LOFTER_REFERER = "https://www.lofter.com/"
MAX_TITLE_LEN = 200


class PostSaver:
    """文章保存器。

    将文章写入 Markdown 文件，带 cookie 和 Referer 下载图片。
    """

    MARKDOWN_TEMPLATE = """# {title}

- **作者**: {author}
- **发布日期**: {publish_date}
- **原文链接**: {url}

---

{content}
"""

    def __init__(
        self,
        base_dir: Path,
        cookies: list[dict] | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._cookies: dict[str, str] = {}
        if cookies:
            for c in cookies:
                name = c.get("name", "")
                if name:
                    self._cookies[name] = c.get("value", "")
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """懒初始化 httpx 客户端（携带 cookie 和 Referer）。"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                cookies=self._cookies,
                timeout=30,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Referer": LOFTER_REFERER,
                },
            )
        return self._http_client

    async def save_dict(
        self, post_dict: dict, sub_dir: str = "", fmt: str = "md",
    ) -> Path:
        """保存文章（从 dict 格式）。fmt: "md" | "txt" | "pdf"。"""
        if fmt == "txt":
            return await self._save_txt(post_dict, sub_dir)
        if fmt == "pdf":
            return await self._save_pdf(post_dict, sub_dir)
        # 默认 markdown：文件夹 + index.md + images/
        title = post_dict.get("title", "untitled")
        post_dir = self._make_post_dir(sub_dir, title)
        await self._save_markdown(post_dir / "index.md", post_dict)
        image_urls = post_dict.get("image_urls", [])
        if image_urls:
            await self._save_images(post_dir / "images", image_urls)
        return post_dir

    def _make_post_dir(self, sub_dir: str, post_title: str) -> Path:
        """创建文章子目录，处理特殊字符标题。"""
        safe_title = _sanitize_filename(post_title)
        if not safe_title:
            safe_title = f"untitled_{int(time.time())}"
        path = self._base_dir / sub_dir / safe_title
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _save_markdown(self, path: Path, post: dict) -> None:
        """写入 Markdown 文件。"""
        content = self.MARKDOWN_TEMPLATE.format(
            title=post.get("title", ""),
            author=post.get("author", ""),
            publish_date=post.get("publish_date", ""),
            url=post.get("url", ""),
            content=post.get("content_markdown", ""),
        )
        async with aiofiles.open(str(path), "w", encoding="utf-8") as f:
            await f.write(content)
        logger.info("Markdown 已保存: %s", path)

    async def _save_txt(self, post: dict, sub_dir: str) -> Path:
        """保存为纯文本 TXT 文件（单文件，无目录）。"""
        from bs4 import BeautifulSoup

        title = post.get("title", "untitled")
        safe_title = _sanitize_filename(title) or f"untitled_{int(time.time())}"
        output_dir = self._base_dir / sub_dir if sub_dir else self._base_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{safe_title}.txt"

        # 从 HTML 提取纯文本
        html = post.get("content_html", "")
        if html:
            soup = BeautifulSoup(html, "lxml")
            body = "\n".join(
                p.get_text(strip=True)
                for p in soup.select("p, br, div, h1, h2, h3, h4, h5, h6, li")
            )
            if not body:
                body = soup.get_text(separator="\n", strip=True)
        else:
            body = post.get("content_markdown", "")

        lines = [
            post.get("title", ""),
            f"作者: {post.get('author', '')}",
            f"日期: {post.get('publish_date', '')}",
            f"链接: {post.get('url', '')}",
            "",
            body,
        ]
        async with aiofiles.open(str(path), "w", encoding="utf-8") as f:
            await f.write("\n".join(lines))
        logger.info("TXT 已保存: %s", path)
        return path

    async def _save_pdf(self, post: dict, sub_dir: str) -> Path:
        """保存为 PDF 文件（单文件，无目录）。"""
        from fpdf import FPDF

        title = post.get("title", "untitled")
        safe_title = _sanitize_filename(title) or f"untitled_{int(time.time())}"
        output_dir = self._base_dir / sub_dir if sub_dir else self._base_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{safe_title}.pdf"

        pdf = FPDF()
        pdf.add_page()
        # 使用内置中文字体（fpdf2 支持 UTF-8）
        pdf.add_font("NotoSansCJK", "", str(_font_path()), uni=True)
        pdf.set_font("NotoSansCJK", "", 14)
        pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(4)
        pdf.set_font("NotoSansCJK", "", 9)
        pdf.cell(0, 6, f"作者: {post.get('author', '')}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"日期: {post.get('publish_date', '')}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"链接: {post.get('url', '')}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)

        # 正文
        from bs4 import BeautifulSoup
        html = post.get("content_html", "")
        if html:
            soup = BeautifulSoup(html, "lxml")
            paragraphs = []
            for el in soup.select("p, br, div, h2, h3, h4, li"):
                t = el.get_text(strip=True)
                if t and len(t) > 1:
                    paragraphs.append(t)
            if not paragraphs:
                paragraphs = [
                    l for l in soup.get_text(separator="\n").split("\n")
                    if l.strip()
                ]
        else:
            paragraphs = [
                l for l in post.get("content_markdown", "").split("\n")
                if l.strip() and not l.startswith("#") and not l.startswith("![]")
            ]

        pdf.set_font("NotoSansCJK", "", 10)
        for p_text in paragraphs:
            if len(p_text) > 200:
                p_text = p_text[:200] + "..."
            pdf.multi_cell(0, 5, p_text)
            pdf.ln(1)

        pdf.output(str(path))
        logger.info("PDF 已保存: %s", path)
        return path

    async def _save_images(self, image_dir: Path, urls: list[str]) -> None:
        """下载图片到指定目录，失败不中断整体保存。

        含重试（最多 MAX_RETRIES 次），指数退避（1s → 2s → 4s）。
        """
        image_dir.mkdir(parents=True, exist_ok=True)
        client = await self._get_client()

        for idx, url in enumerate(urls, start=1):
            success = False
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    ext = _infer_extension(
                        url, resp.headers.get("content-type", "")
                    )
                    img_path = image_dir / f"{idx:03d}{ext}"
                    async with aiofiles.open(str(img_path), "wb") as f:
                        await f.write(resp.content)
                    logger.debug("图片已保存: %s", img_path)
                    success = True
                    break
                except Exception as exc:
                    delay = 2 ** (attempt - 1)  # 1s, 2s, 4s
                    if attempt < MAX_RETRIES:
                        logger.debug(
                            "图片下载重试 [%d/%d]: %s (等待 %ss)",
                            attempt, MAX_RETRIES, url, delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            "图片下载失败 [%d/%d]: %s - %s",
                            attempt, MAX_RETRIES, url, exc,
                        )
            if not success:
                # 非致命：图片失败不影响文章保存
                logger.warning("已跳过图片: %s", url)

    async def close(self) -> None:
        """关闭 httpx 客户端。"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None


def _infer_extension(url: str, content_type: str) -> str:
    """根据 Content-Type 或 URL 推断文件扩展名。"""
    ext = IMAGE_CONTENT_TYPES.get(content_type)
    if ext:
        return ext
    suffix = Path(url).suffix
    if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return suffix
    return ".jpg"


def _font_path() -> str:
    """查找系统 CJK 字体路径（PDF 导出需要）。"""
    import platform
    candidates = []
    if platform.system() == "Windows":
        candidates = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/msgothic.ttc",
        ]
    else:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ]
    for path in candidates:
        if Path(path).exists():
            return path
    # 降级：尝试任意 ttf/ttc
    for d in [Path("C:/Windows/Fonts"), Path("/usr/share/fonts")]:
        if d.exists():
            for f in d.rglob("*.ttf"):
                return str(f)
            for f in d.rglob("*.ttc"):
                return str(f)
    raise FileNotFoundError(
        "未找到中文字体文件，PDF 导出需要系统安装 CJK 字体（如微软雅黑）"
    )


def _sanitize_filename(name: str) -> str:
    r"""清洗文件名中的非法字符。

    Windows 不允许: \ / : * ? " < > |
    同时处理首尾空格/点，截断过长的名称。
    """
    # 替换 Windows 非法字符
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name)
    # 去除首尾空格和点（Windows 不允许以空格或点结尾）
    cleaned = cleaned.strip(". ")
    # 截断到最大长度
    if len(cleaned) > MAX_TITLE_LEN:
        cleaned = cleaned[:MAX_TITLE_LEN].rstrip(". ")
    return cleaned
