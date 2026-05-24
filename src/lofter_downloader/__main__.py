"""CLI 入口，启动 Web 服务。"""

from __future__ import annotations

import webbrowser

import uvicorn

from lofter_downloader.config import settings
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


def main() -> None:
    """启动 FastAPI Web 服务并打开浏览器。"""
    host = "127.0.0.1"
    port = 8080

    logger.info("Starting LOFTER Downloader Web UI...")
    logger.info("Open http://%s:%d in your browser", host, port)

    webbrowser.open(f"http://{host}:{port}")

    uvicorn.run(
        "lofter_downloader.web.server:app",
        host=host,
        port=port,
        reload=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
