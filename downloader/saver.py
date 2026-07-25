"""文章存储模块。

将文章保存为 Markdown / TXT / PDF / EPUB，异步下载文中图片。
含重试（指数退避）、Referer 头、非法字符清洗、同名冲突自动编号。
"""

from __future__ import annotations

import asyncio
import html
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

    将文章写入 Markdown 文件，带 cookie 和 Referer 下载图片；
    同时支持 TXT / PDF / EPUB 导出。
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
        self,
        post_dict: dict,
        sub_dir: str = "",
        fmt: str = "md",
    ) -> Path:
        """保存文章（从 dict 格式）。fmt: "md" | "txt" | "pdf" | "epub"。"""
        if fmt == "txt":
            return await self._save_txt(post_dict, sub_dir)
        if fmt == "pdf":
            return await self._save_pdf(post_dict, sub_dir)
        if fmt == "epub":
            return await self._save_epub(post_dict, sub_dir)
        # 默认 markdown：先下载图片（获取实际文件名），再写 MD（替换路径）
        title = post_dict.get("title", "untitled")
        post_dir = self._make_post_dir(sub_dir, title)
        image_urls = post_dict.get("image_urls", [])
        url_to_local: dict[str, str] = {}
        if image_urls:
            url_to_local = await self._save_images(post_dir / "images", image_urls)
        await self._save_markdown(post_dir / "index.md", post_dict, url_to_local)
        return post_dir

    def _make_post_dir(self, sub_dir: str, post_title: str) -> Path:
        """创建文章子目录，处理特殊字符标题和同名冲突。"""
        safe_title = _sanitize_filename(post_title)
        if not safe_title:
            safe_title = f"untitled_{int(time.time())}"
        path = _unique_path(self._base_dir / sub_dir, safe_title, is_dir=True)
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _save_markdown(
        self, path: Path, post: dict, url_to_local: dict[str, str] | None = None
    ) -> None:
        """写入 Markdown 文件，将远程图片 URL 替换为本地相对路径。"""
        content_md = post.get("content_markdown", "")
        if url_to_local:
            for remote_url, local_name in url_to_local.items():
                content_md = content_md.replace(remote_url, f"images/{local_name}")
        content = self.MARKDOWN_TEMPLATE.format(
            title=post.get("title", ""),
            author=post.get("author", ""),
            publish_date=post.get("publish_date", ""),
            url=post.get("url", ""),
            content=content_md,
        )
        async with aiofiles.open(str(path), "w", encoding="utf-8") as f:
            await f.write(content)
        logger.info("Markdown 已保存: %s", path)

    async def _save_txt(self, post: dict, sub_dir: str) -> Path:
        """保存为纯文本 TXT 文件（单文件，无目录）。"""
        title = post.get("title", "untitled")
        safe_title = _sanitize_filename(title) or f"untitled_{int(time.time())}"
        output_dir = self._base_dir / sub_dir if sub_dir else self._base_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        path = _unique_path(output_dir, safe_title, ext=".txt")

        body = _markdown_to_plain_text(post.get("content_markdown", ""))

        # parser 回退可能把标题混入正文首行，此时头部不再重复标题
        body_first_line = body.splitlines()[0].strip() if body.strip() else ""
        title_in_body = body_first_line == title.strip()

        lines = [
            "" if title_in_body else post.get("title", ""),
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
        path = _unique_path(output_dir, safe_title, ext=".pdf")

        pdf = FPDF()
        pdf.add_page()
        # 使用内置中文字体（fpdf2 支持 UTF-8）
        pdf.add_font("NotoSansCJK", "", str(_font_path()), uni=True)
        pdf.add_font("NotoSansCJK", "B", str(_font_path_bold()), uni=True)
        pdf.add_font("NotoSansCJK", "I", str(_font_path()), uni=True)

        pdf.set_font("NotoSansCJK", "B", 16)
        # 长标题需换行，避免 cell 不换行导致溢出裁剪
        pdf.multi_cell(0, 10, title, align="C")
        pdf.ln(2)
        pdf.set_font("NotoSansCJK", "", 9)
        pdf.cell(0, 6, f"作者: {post.get('author', '')}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(
            0, 6, f"日期: {post.get('publish_date', '')}", new_x="LMARGIN", new_y="NEXT"
        )
        pdf.cell(0, 6, f"链接: {post.get('url', '')}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)

        content_md = post.get("content_markdown", "")
        # 预下载正文引用的图片（失败不阻塞，渲染时降级为占位文本）
        image_urls = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content_md)
        images = await self._download_pdf_images(image_urls)
        _render_markdown_to_pdf(pdf, content_md, images=images)

        pdf.output(str(path))
        logger.info("PDF 已保存: %s", path)
        return path

    async def _download_pdf_images(
        self, urls: list[str],
    ) -> dict[str, tuple]:
        """下载图片并转为 PNG 字节供 PDF 嵌入。

        返回 {url: (png_buffer, width_px, height_px)}。
        借助 Pillow 统一转码（webp/gif 首帧 → PNG），透明底合成到白色。
        单张失败跳过（渲染时显示 [图片] 占位）。
        """
        if not urls:
            return {}
        import io

        try:
            from PIL import Image
        except ImportError:
            logger.warning("未安装 Pillow，PDF 将不嵌入图片")
            return {}

        client = await self._get_client()
        images: dict[str, tuple] = {}
        for url in dict.fromkeys(urls):  # 去重保序
            data = await _fetch_image_bytes(client, url)
            if data is None:
                continue
            try:
                img = Image.open(io.BytesIO(data))
                img.load()
                w, h = img.size
                if img.mode in ("RGBA", "LA", "P"):
                    # 透明底合成到白色，避免转 RGB 后变黑
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    rgba = img.convert("RGBA")
                    bg.paste(rgba, mask=rgba.split()[-1])
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                images[url] = (buf, w, h)
            except Exception:
                logger.warning("PDF 图片解码失败: %s", url)
        return images

    async def _save_epub(self, post: dict, sub_dir: str) -> Path:
        """保存为 EPUB 电子书，嵌入图片，保留 HTML 排版。

        仅包含标题、正文和图片，不含网页导航/侧栏等信息。
        """
        from ebooklib import epub

        title = post.get("title", "untitled")
        safe_title = _sanitize_filename(title) or f"untitled_{int(time.time())}"
        output_dir = self._base_dir / sub_dir if sub_dir else self._base_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        path = _unique_path(output_dir, safe_title, ext=".epub")

        book = epub.EpubBook()
        book.set_identifier(f"lofter_{int(time.time())}")
        book.set_title(title)
        book.set_language("zh-CN")
        book.add_author(post.get("author", "未知作者"))

        # 下载图片到内存
        image_urls = post.get("image_urls", [])
        epub_images: list[tuple] = []  # (url, filename)
        if image_urls:
            client = await self._get_client()
            for idx, url in enumerate(image_urls):
                success = False
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        ct = resp.headers.get("content-type", "")
                        if ct and not ct.startswith("image/"):
                            logger.warning(
                                "EPUB 非图片响应，跳过: %s (Content-Type: %s)",
                                url,
                                ct,
                            )
                            break
                        ext = _infer_extension(
                            url, resp.headers.get("content-type", "")
                        )
                        filename = f"img_{idx:03d}{ext}"
                        img = epub.EpubImage()
                        img.file_name = f"images/{filename}"
                        img.media_type = resp.headers.get("content-type", "image/jpeg")
                        img.content = resp.content
                        book.add_item(img)
                        epub_images.append((url, filename))
                        success = True
                        break
                    except Exception:
                        delay = 2 ** (attempt - 1)
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(delay)
                if not success:
                    logger.warning("EPUB 图片下载失败: %s", url)

        # 构建正文 HTML：仅保留标题、正文、图片
        content_html = post.get("content_html", "")
        if content_html:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(content_html, "lxml")
            # 替换图片 src 为 epub 内嵌路径
            for img_tag in soup.find_all("img"):
                src = img_tag.get("src", "")
                for orig_url, filename in epub_images:
                    if src == orig_url:
                        img_tag["src"] = f"images/{filename}"
                        break
                # 移除内联样式以外可能残留的 class/id
                img_tag.attrs = {
                    k: v
                    for k, v in img_tag.attrs.items()
                    if k in ("src", "alt", "style")
                }
            body_html = soup.body.decode_contents() if soup.body else str(soup)
        else:
            body_html = f"<p>{post.get('content_markdown', '')}</p>"

        chapter = epub.EpubHtml(
            title="正文",
            file_name="chap_01.xhtml",
            lang="zh-CN",
        )
        chapter.content = (
            f"<h1>{html.escape(title)}</h1>"
            f"<p style='color:#666;font-size:0.9em;margin-bottom:1em'>"
            f"作者: {html.escape(post.get('author', ''))}<br/>"
            f"日期: {html.escape(post.get('publish_date', ''))}</p>"
            f"<hr/>"
            f"{body_html}"
        )
        book.add_item(chapter)

        book.toc = [epub.Link("chap_01.xhtml", "正文", "chap_01")]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav", chapter]

        epub.write_epub(str(path), book)
        logger.info("EPUB 已保存: %s", path)
        return path

    async def _save_images(self, image_dir: Path, urls: list[str]) -> dict[str, str]:
        """下载图片到指定目录，失败不中断整体保存。

        含重试（最多 MAX_RETRIES 次），指数退避（1s → 2s → 4s）。
        返回 {remote_url: local_filename} 映射，供 MD 路径重写使用。
        """
        image_dir.mkdir(parents=True, exist_ok=True)
        client = await self._get_client()
        url_to_local: dict[str, str] = {}

        for idx, url in enumerate(urls, start=1):
            success = False
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    ct = resp.headers.get("content-type", "")
                    if ct and not ct.startswith("image/"):
                        logger.warning(
                            "非图片响应，跳过: %s (Content-Type: %s)",
                            url,
                            ct,
                        )
                        break
                    ext = _infer_extension(url, resp.headers.get("content-type", ""))
                    img_path = image_dir / f"{idx:03d}{ext}"
                    async with aiofiles.open(str(img_path), "wb") as f:
                        await f.write(resp.content)
                    logger.debug("图片已保存: %s", img_path)
                    url_to_local[url] = f"{idx:03d}{ext}"
                    success = True
                    break
                except Exception as exc:
                    delay = 2 ** (attempt - 1)  # 1s, 2s, 4s
                    if attempt < MAX_RETRIES:
                        logger.debug(
                            "图片下载重试 [%d/%d]: %s (等待 %ss)",
                            attempt,
                            MAX_RETRIES,
                            url,
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            "图片下载失败 [%d/%d]: %s - %s",
                            attempt,
                            MAX_RETRIES,
                            url,
                            exc,
                        )
            if not success:
                # 非致命：图片失败不影响文章保存
                logger.warning("已跳过图片: %s", url)
        return url_to_local

    async def close(self) -> None:
        """关闭 httpx 客户端。"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None


async def _fetch_image_bytes(client: httpx.AsyncClient, url: str) -> bytes | None:
    """下载单张图片字节，含重试（指数退避）。非图片响应或失败返回 None。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if ct and not ct.startswith("image/"):
                logger.warning("非图片响应，跳过: %s (Content-Type: %s)", url, ct)
                return None
            return resp.content
        except Exception as exc:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
            else:
                logger.warning("图片下载失败: %s - %s", url, exc)
    return None


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
    """查找系统 CJK 常规字体路径（PDF 导出需要）。"""
    return _find_font(bold=False)


def _font_path_bold() -> str:
    """查找系统 CJK 粗体字体路径（PDF 导出需要）。"""
    return _find_font(bold=True)


def _find_font(bold: bool = False) -> str:
    """按平台查找可用的中文字体文件。"""
    import os as _os
    import platform

    _non_cjk = {
        "arial",
        "times",
        "courier",
        "helvetica",
        "verdana",
        "georgia",
        "trebuchet",
        "comic",
        "impact",
        "palatino",
        "garamond",
        "segoe",
        "tahoma",
        "calibri",
        "cambria",
        "symbol",
        "wingdings",
        "webdings",
    }
    candidates = []
    system = platform.system()
    if system == "Windows":
        if bold:
            candidates = [
                "C:/Windows/Fonts/msyhbd.ttc",
                "C:/Windows/Fonts/msyh.ttc",
                "C:/Windows/Fonts/simhei.ttf",
                "C:/Windows/Fonts/Dengb.ttf",
                "C:/Windows/Fonts/simsun.ttc",
            ]
        else:
            candidates = [
                "C:/Windows/Fonts/msyh.ttc",
                "C:/Windows/Fonts/simsun.ttc",
                "C:/Windows/Fonts/simhei.ttf",
                "C:/Windows/Fonts/Deng.ttf",
                "C:/Windows/Fonts/msgothic.ttc",
            ]
    elif system == "Darwin":
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ]
    else:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ]
    for path in candidates:
        if Path(path).exists() and _os.access(path, _os.R_OK):
            return path
    # 降级：尝试任意 ttf/ttc，过滤已知非 CJK 字体
    for d in [Path("C:/Windows/Fonts"), Path("/usr/share/fonts")]:
        if d.exists():
            for f in d.rglob("*.ttf"):
                if f.stem.lower() not in _non_cjk:
                    return str(f)
            for f in d.rglob("*.ttc"):
                if f.stem.lower() not in _non_cjk:
                    return str(f)
    raise FileNotFoundError(
        "未找到中文字体文件，"
        "PDF 导出需要系统安装 CJK 字体（如微软雅黑或 Noto Sans CJK）"
    )


def _unique_path(
    base_dir: Path,
    safe_name: str,
    ext: str = "",
    is_dir: bool = False,
) -> Path:
    """生成唯一文件或目录路径，同名时追加序号 (2)、(3)…，不再使用 UUID。"""
    candidate = base_dir / safe_name
    if not is_dir:
        candidate = candidate.with_suffix(ext)
    if not candidate.exists():
        return candidate

    i = 2
    while True:
        numbered = f"{safe_name} ({i})"
        candidate = base_dir / numbered
        if not is_dir:
            candidate = candidate.with_suffix(ext)
        if not candidate.exists():
            return candidate
        i += 1


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


def _markdown_to_plain_text(md: str) -> str:
    """把 Markdown 转为易读纯文本，保留段落与列表结构，移除语法标记。"""
    # 先做块级处理：围栏代码块整体提取，块内内容原样保留、去掉围栏行，
    # 避免逐行处理匹配不上跨行 ``` 对导致围栏字面残留
    code_blocks: list[str] = []

    def _stash_code_block(match: re.Match) -> str:
        code_blocks.append(match.group(1))
        return f"\n\x00CODE{len(code_blocks) - 1}\x00\n"

    text = re.sub(r"```[^\n]*\n([\s\S]*?)```", _stash_code_block, md)

    # 转义 HTML 实体并移除 HTML 标签（代码块已提取，不受影响）
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)

    lines = text.splitlines()
    result: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            result.append("")
            continue

        stripped = line
        # 代码块占位符：原样保留，待最后还原
        if re.fullmatch(r"\x00CODE\d+\x00", stripped.strip()):
            result.append(stripped.strip())
            continue
        # 分隔线：替换为空行
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", stripped):
            result.append("")
            continue
        # 标题：去掉前导 # 并保留文字
        if re.match(r"^#{1,6}\s+", stripped):
            stripped = re.sub(r"^#{1,6}\s+", "", stripped)
        # 列表标记（有序列表保留序号，避免丢失枚举信息）
        stripped = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", stripped)
        # 引用
        stripped = re.sub(r"^>\s*", "", stripped)
        # 行内格式
        stripped = _strip_inline_markdown(stripped)

        result.append(stripped)

    # 合并连续空行，保留段落间距
    cleaned: list[str] = []
    prev_empty = False
    for line in result:
        is_empty = line.strip() == ""
        if is_empty and prev_empty:
            continue
        cleaned.append(line)
        prev_empty = is_empty

    joined = "\n".join(cleaned).strip()

    # 还原代码块内容
    def _restore_code_block(match: re.Match) -> str:
        return code_blocks[int(match.group(1))].strip("\n")

    return re.sub(r"\x00CODE(\d+)\x00", _restore_code_block, joined)


def _strip_inline_markdown(text: str) -> str:
    """移除行内 Markdown 语法（加粗、斜体、链接、代码、删除线），保留文字。"""
    # 行内代码
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # 图片 → 去掉
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    # 链接 → 保留文本
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 裸链接 <url>
    text = re.sub(r"<([^>]+)>", r"\1", text)
    # 加粗/斜体（按长度优先，避免 ** 被 * 先匹配）
    text = re.sub(r"\*\*\*(.*?)\*\*\*", r"\1", text)
    text = re.sub(r"___(.*?)___", r"\1", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    # 下划线斜体仅在两侧是单词边界时应用，避免误删 snake_case 中的下划线
    text = re.sub(r"(?<![\w_])_([^_]+?)_(?![\w_])", r"\1", text)
    # 删除线
    text = re.sub(r"~~(.*?)~~", r"\1", text)
    return text


def _render_markdown_to_pdf(
    pdf: object, md: str, images: dict[str, tuple] | None = None,
) -> None:
    """按 Markdown 结构渲染 PDF：标题、段落、列表、代码块、缩进、图片。

    images: {url: (png_buffer, width_px, height_px)}，独占一行的图片
    会被嵌入；未下载成功或行内图片降级为 [图片] 占位文本。
    """
    lines = md.splitlines()
    i = 0
    in_code_block = False
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip():
            pdf.ln(2)
            i += 1
            continue

        # 围栏代码块：去掉围栏行，块内内容原样保留
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            i += 1
            continue
        if in_code_block:
            pdf.set_font("NotoSansCJK", "", 10)
            pdf.multi_cell(0, 5, line)
            i += 1
            continue

        # 独占一行的图片：嵌入已下载的图片，失败则占位文本
        img_match = re.fullmatch(r"!\[[^\]]*\]\(([^)]+)\)", line.strip())
        if img_match:
            url = img_match.group(1)
            if images and url in images:
                _embed_pdf_image(pdf, *images[url])
            else:
                pdf.set_font("NotoSansCJK", "I", 9)
                pdf.multi_cell(0, 5, "[图片]")
            i += 1
            continue

        # 行内图片替换为占位文本，避免纯图集文章产出空文
        line = re.sub(r"!\[[^\]]*\]\([^)]+\)", "[图片]", line)

        # 标题
        heading_match = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading_match:
            level = len(heading_match.group(1))
            text = _strip_inline_markdown(heading_match.group(2))
            sizes = {1: 16, 2: 14, 3: 12, 4: 11, 5: 10, 6: 10}
            pdf.set_font("NotoSansCJK", "B", sizes.get(level, 10))
            pdf.multi_cell(0, 6, text)
            pdf.ln(2)
            i += 1
            continue

        # 无序列表
        bullet_match = re.match(r"^(\s*)[-*+]\s+(.*)", line)
        if bullet_match:
            indent = len(bullet_match.group(1))
            text = _strip_inline_markdown(bullet_match.group(2))
            pdf.set_font("NotoSansCJK", "", 10)
            pdf.set_x(pdf.l_margin + min(indent, 8) * 3)
            pdf.cell(4, 5, "•")
            pdf.multi_cell(0, 5, text)
            i += 1
            continue

        # 有序列表
        number_match = re.match(r"^(\s*)\d+\.\s+(.*)", line)
        if number_match:
            indent = len(number_match.group(1))
            text = _strip_inline_markdown(number_match.group(2))
            pdf.set_font("NotoSansCJK", "", 10)
            pdf.set_x(pdf.l_margin + min(indent, 8) * 3)
            pdf.multi_cell(0, 5, line.strip())
            i += 1
            continue

        # 引用
        if line.lstrip().startswith("> "):
            text = _strip_inline_markdown(line.lstrip()[2:])
            pdf.set_font("NotoSansCJK", "I", 10)
            pdf.set_x(pdf.l_margin + 4)
            pdf.multi_cell(0, 5, text)
            i += 1
            continue

        # 普通段落
        pdf.set_font("NotoSansCJK", "", 10)
        pdf.multi_cell(0, 5, _strip_inline_markdown(line))
        pdf.ln(1)
        i += 1


def _embed_pdf_image(pdf: object, buf: object, w_px: int, h_px: int) -> None:
    """将图片按版宽嵌入 PDF（居中、保持比例、必要时分页）。

    按 96 DPI 换算物理宽度，超宽缩到版宽，超高缩到整页高。
    """
    max_w = pdf.w - pdf.l_margin - pdf.r_margin
    w_mm = min(max_w, w_px * 25.4 / 96)
    h_mm = w_mm * h_px / w_px
    # 超高图片（长截图）缩放到一页能放下
    max_h = pdf.h - pdf.t_margin - pdf.b_margin
    if h_mm > max_h:
        h_mm = max_h
        w_mm = h_mm * w_px / h_px
    # 剩余空间不足时先分页，避免图片溢出页脚
    if pdf.get_y() + h_mm > pdf.page_break_trigger:
        pdf.add_page()
    x = pdf.l_margin + (max_w - w_mm) / 2
    pdf.image(buf, x=x, y=pdf.get_y(), w=w_mm)
    pdf.set_y(pdf.get_y() + h_mm + 2)
