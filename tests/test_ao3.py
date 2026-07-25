"""AO3 模块测试。

使用本地 HTML fixture，不访问真实 AO3。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from downloader.ao3 import (
    MAX_RETRY_AFTER,
    AO3Client,
    AO3Error,
    _extract_images,
    _parse_work_meta,
    extract_username,
    extract_work_id,
)


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def client() -> AO3Client:
    return AO3Client()


class TestUrlNormalization:
    """URL 归一化测试。"""

    def test_normalize_work_url(self, client: AO3Client) -> None:
        assert (
            client.normalize_work_url(
                "https://archiveofourown.org/works/12345",
            )
            == "https://archiveofourown.org/works/12345"
        )
        assert (
            client.normalize_work_url(
                "https://archiveofourown.org/works/12345/chapters/67890",
            )
            == "https://archiveofourown.org/works/12345"
        )
        assert client.normalize_work_url("not a url") is None


class TestExtractUsername:
    """作者链接用户名提取测试。"""

    def test_extract_username(self) -> None:
        base = "https://archiveofourown.org"
        assert extract_username(f"{base}/users/testauthor") == "testauthor"
        assert (
            extract_username(f"{base}/users/testauthor/pseuds/pseud1") == "testauthor"
        )
        assert extract_username(f"{base}/users/testauthor/works") == "testauthor"
        assert extract_username(f"{base}/users/testauthor/works?page=2") == "testauthor"
        assert extract_username(f"{base}/works/12345") is None
        assert extract_username("not a url") is None


class TestExtractWorkId:
    """作品 ID 提取测试。"""

    def testextract_work_id(self) -> None:
        assert extract_work_id("/works/12345") == "12345"
        assert extract_work_id("/works/12345/chapters/678") == "12345"
        assert extract_work_id("/series/999") is None


class TestParseWorkMeta:
    """作品元信息解析测试。"""

    def test_parse_work_meta(self, fixtures_dir: Path) -> None:
        html = (fixtures_dir / "ao3_work.html").read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "lxml")
        meta = _parse_work_meta(soup)
        assert meta["title"] == "Sample AO3 Work"
        assert meta["author"] == "TestAuthor"
        assert meta["publish_date"] == "2024-05-20"


class TestExtractImages:
    """正文图片提取测试。

    src 的绝对 URL 改写由 parse_work 负责，_extract_images 只按原样提取。
    """

    def test_extract_images(self) -> None:
        html = (
            '<img src="https://archiveofourown.org/images/a.png">'
            '<img src="https://example.com/b.jpg">'
        )
        urls = _extract_images(html)
        assert urls == [
            "https://archiveofourown.org/images/a.png",
            "https://example.com/b.jpg",
        ]


class TestParseWork:
    """整篇解析测试（增强 fixture：含 Notes/summary 与相对路径图片）。"""

    @pytest.mark.asyncio
    async def test_parse_work_excludes_notes_and_summary(
        self,
        client: AO3Client,
        fixtures_dir: Path,
    ) -> None:
        html = (fixtures_dir / "ao3_work.html").read_text(encoding="utf-8")

        async def _fake_get_page(_url: str) -> BeautifulSoup:
            return BeautifulSoup(html, "lxml")

        client.get_page = _fake_get_page  # type: ignore[method-assign]
        result = await client.parse_work("12345")

        # 正文包含章节内容
        assert "Chapter one content." in result["content_html"]
        assert "Chapter two content." in result["content_markdown"]
        # 正文不包含章节 Notes、作品 Notes、作品 summary
        for marker in (
            "CHAPTER_NOTES_MARKER",
            "WORK_NOTES_MARKER",
            "WORK_SUMMARY_MARKER",
        ):
            assert marker not in result["content_html"]
            assert marker not in result["content_markdown"]

    @pytest.mark.asyncio
    async def test_parse_work_image_urls_consistent(
        self,
        client: AO3Client,
        fixtures_dir: Path,
    ) -> None:
        html = (fixtures_dir / "ao3_work.html").read_text(encoding="utf-8")

        async def _fake_get_page(_url: str) -> BeautifulSoup:
            return BeautifulSoup(html, "lxml")

        client.get_page = _fake_get_page  # type: ignore[method-assign]
        result = await client.parse_work("12345")

        # 相对 src 统一改写为绝对 URL，三处保持一致
        abs_url = "https://archiveofourown.org/images/chapter1.png"
        assert abs_url in result["content_html"]
        assert abs_url in result["content_markdown"]
        assert result["image_urls"] == [abs_url]


class TestRequestRetry:
    """_request 的 429/Retry-After 退避测试。"""

    def _mock_client(self, client: AO3Client, responses: list) -> AsyncMock:
        fake = AsyncMock()
        fake.get = AsyncMock(side_effect=responses)
        client._client = fake
        return fake

    @pytest.mark.asyncio
    async def test_retry_after_respected(self, client: AO3Client) -> None:
        resp429 = httpx.Response(429, headers={"Retry-After": "5"})
        resp200 = httpx.Response(200, text="ok")
        fake = self._mock_client(client, [resp429, resp200])

        with (
            patch("downloader.ao3.REQUEST_INTERVAL", 0),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            resp = await client._request("https://archiveofourown.org/works/1")

        assert resp.status_code == 200
        assert fake.get.await_count == 2
        mock_sleep.assert_any_await(5.0)

    @pytest.mark.asyncio
    async def test_retry_after_clamped(self, client: AO3Client) -> None:
        resp429 = httpx.Response(429, headers={"Retry-After": "9999"})
        resp200 = httpx.Response(200, text="ok")
        self._mock_client(client, [resp429, resp200])

        with (
            patch("downloader.ao3.REQUEST_INTERVAL", 0),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            resp = await client._request("https://archiveofourown.org/works/1")

        assert resp.status_code == 200
        mock_sleep.assert_any_await(MAX_RETRY_AFTER)

    @pytest.mark.asyncio
    async def test_last_attempt_raises_without_extra_sleep(
        self,
        client: AO3Client,
    ) -> None:
        resp429 = httpx.Response(429)
        self._mock_client(client, [resp429, resp429, resp429])

        with (
            patch("downloader.ao3.REQUEST_INTERVAL", 0),
            patch("downloader.ao3.MAX_RETRIES", 3),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
            pytest.raises(AO3Error, match="重试耗尽"),
        ):
            await client._request("https://archiveofourown.org/works/1")

        # 最后一次 429 直接 raise，不再等待：仅前两次重试有 sleep
        assert mock_sleep.await_count == 2


class TestListParsing:
    """清单解析测试（mock 网络请求）。"""

    @pytest.mark.asyncio
    async def test_list_series(self, client: AO3Client, fixtures_dir: Path) -> None:
        html = (fixtures_dir / "ao3_series.html").read_text(encoding="utf-8")

        async def _fake_get_page(_url: str) -> BeautifulSoup:
            return BeautifulSoup(html, "lxml")

        client.get_page = _fake_get_page  # type: ignore[method-assign]
        items = await client.list_series("999")
        assert len(items) == 2
        assert items[0]["id"] == "111"
        assert items[0]["title"] == "First Work"
        assert items[0]["author"] == "TestAuthor"
        assert items[1]["id"] == "222"

    @pytest.mark.asyncio
    async def test_list_author_pagination(
        self,
        client: AO3Client,
        fixtures_dir: Path,
    ) -> None:
        page1 = (fixtures_dir / "ao3_author.html").read_text(encoding="utf-8")
        page2 = (fixtures_dir / "ao3_author_page2.html").read_text(encoding="utf-8")
        responses = [page1, page2]
        call_count = 0

        async def _fake_get_page(_url: str) -> BeautifulSoup:
            nonlocal call_count
            html = responses[call_count]
            call_count += 1
            return BeautifulSoup(html, "lxml")

        client.get_page = _fake_get_page  # type: ignore[method-assign]
        items = await client.list_author("testauthor")

        assert len(items) == 3
        assert items[0]["id"] == "333"
        assert items[2]["id"] == "555"


class TestOfficialDownload:
    """官方导出链接解析与流式下载测试。"""

    @pytest.mark.asyncio
    async def test_get_official_download_url(
        self,
        client: AO3Client,
        fixtures_dir: Path,
    ) -> None:
        html = (fixtures_dir / "ao3_work.html").read_text(encoding="utf-8")

        async def _fake_get_page(_url: str) -> BeautifulSoup:
            return BeautifulSoup(html, "lxml")

        client.get_page = _fake_get_page  # type: ignore[method-assign]
        url = await client.get_official_download_url("12345", "epub")
        assert "/downloads/12345/work.epub" in url

    @pytest.mark.asyncio
    async def test_get_official_download_url_with_page_html(
        self,
        client: AO3Client,
        fixtures_dir: Path,
    ) -> None:
        html = (fixtures_dir / "ao3_work.html").read_text(encoding="utf-8")

        async def _fail_get_page(_url: str) -> BeautifulSoup:
            raise AssertionError("传入 page_html 时不应再抓取作品页")

        client.get_page = _fail_get_page  # type: ignore[method-assign]
        url = await client.get_official_download_url("12345", "pdf", page_html=html)
        assert "/downloads/12345/work.pdf" in url

    @pytest.mark.asyncio
    async def test_unsupported_format_raises(self, client: AO3Client) -> None:
        with pytest.raises(AO3Error):
            await client.get_official_download_url("12345", "docx")

    @pytest.mark.asyncio
    async def test_download_official_with_page_html_skips_refetch(
        self,
        client: AO3Client,
        fixtures_dir: Path,
        tmp_path: Path,
    ) -> None:
        html = (fixtures_dir / "ao3_work.html").read_text(encoding="utf-8")

        async def _fail_get_page(_url: str) -> BeautifulSoup:
            raise AssertionError("传入 page_html 时不应再抓取作品页")

        client.get_page = _fail_get_page  # type: ignore[method-assign]

        class _FakeStreamResp:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

            async def aiter_bytes(self, **_kwargs):
                yield b"fake-epub-bytes"

        class _FakeStreamCtx:
            async def __aenter__(self) -> _FakeStreamResp:
                return _FakeStreamResp()

            async def __aexit__(self, *args) -> bool:
                return False

        fake = AsyncMock()
        fake.stream = Mock(return_value=_FakeStreamCtx())
        client._client = fake

        dest = tmp_path / "work.epub"
        with patch("downloader.ao3.REQUEST_INTERVAL", 0):
            await client.download_official("12345", "epub", dest, page_html=html)

        assert dest.read_bytes() == b"fake-epub-bytes"
        fake.stream.assert_called_once()


class TestShieldsUp:
    """AO3 shields up（高负载防护）检测。"""

    @pytest.mark.asyncio
    async def test_shields_up_raises_friendly_error(
        self,
        client: AO3Client,
    ) -> None:
        mock_resp = httpx.Response(403, text="<title>Shields are up!</title>")
        with (
            patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)),
            pytest.raises(AO3Error, match="shields up"),
        ):
            await client.get_page("https://archiveofourown.org/works/12345")
