"""实站测试：用有真实文章的博客验证 DWR + 文章提取。"""
import asyncio
import json
import logging
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from downloader.parser import extract_post
from downloader.pipeline import DownloadPipeline

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

SESSION_PATH = Path.home() / ".lofter_downloader" / "lofter_auth.json"
TEST_BLOG = "loftercreator"  # LOFTER 官方博客，有文章


class FakeBrowser:
    def __init__(self, pw, headless=True):
        self._playwright = pw
        self._headless = headless
        self._browser = None

    async def _ensure_browser(self):
        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless
            )

    async def new_context(self, storage_state=None):
        await self._ensure_browser()
        return await self._browser.new_context(
            storage_state=storage_state, locale="zh-CN"
        )

    async def close(self):
        if self._browser:
            await self._browser.close()


async def main():
    async with async_playwright() as pw:
        browser = FakeBrowser(pw, headless=True)
        pipeline = DownloadPipeline(browser)

        # ── Test 1: Blog link collection ──
        print(f"{'='*60}")
        print(f"Test 1: Blog links - {TEST_BLOG}")
        print("=" * 60)
        try:
            links, blog_name = await pipeline.collect_blog_links(TEST_BLOG)
            print(f"Result: blog_name={blog_name}, post_count={len(links)}")
            for l in links[:5]:
                print(f"  {l}")
        except Exception as e:
            print(f"FAILED: {e}")
            import traceback
            traceback.print_exc()
            links = []

        # ── Test 2: Post extraction ──
        if links:
            test_url = links[0]
        else:
            test_url = f"https://{TEST_BLOG}.lofter.com/post/4d1d129b_34e0114d3"
        print(f"\n{'='*60}")
        print(f"Test 2: Post extraction - {test_url}")
        print("=" * 60)

        storage = str(SESSION_PATH) if SESSION_PATH.exists() else None
        context = await browser.new_context(storage_state=storage)
        try:
            page = await context.new_page()
            await page.goto(test_url, wait_until="load", timeout=30000)
            await asyncio.sleep(3)
            result = await extract_post(page, test_url)
            if result:
                print(f"SUCCESS!")
                print(f"  title: {result.get('title', 'N/A')[:100]}")
                print(f"  author: {result.get('author', 'N/A')}")
                print(f"  date: {result.get('publish_date', 'N/A')}")
                content = result.get("content_html", "")
                print(f"  content_html: {len(content)} chars")
                text = BeautifulSoup(content, 'lxml').get_text()[:200] if content else ''
                print(f"  content text (first 200): {text}")
                imgs = result.get("image_urls", [])
                print(f"  images: {len(imgs)}")
            else:
                print("FAILED: extract_post returned None")
        except Exception as e:
            print(f"FAILED: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await context.close()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
