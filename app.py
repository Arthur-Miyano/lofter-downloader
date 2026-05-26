"""LOFTER 下载器 — Flask 应用入口。

启动 Flask 开发服务器和 Playwright 浏览器后台线程。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import webbrowser
from pathlib import Path

from flask import Flask

from config import DOWNLOAD_DIR, HOST, LOG_LEVEL, PORT, SESSION_PATH


def create_app() -> Flask:
    """创建并配置 Flask 应用。"""
    # 日志配置
    _setup_logging()

    app = Flask(__name__)

    # 初始化浏览器管理器
    from downloader.browser import BrowserManager

    browser_mgr = BrowserManager(headless=True)
    browser_mgr.start()

    # 注入 browser 到 routes 模块
    import web.routes as routes

    routes.browser = browser_mgr

    # 确保下载目录存在
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 注册 API 蓝图
    app.register_blueprint(routes.api)

    # 首页
    @app.route("/")
    def index() -> str:
        template_path = Path(__file__).parent / "web" / "templates" / "index.html"
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")
        return "<h1>LOFTER 下载器</h1><p>前端模板未找到</p>"

    return app


def _setup_logging() -> None:
    """配置统一日志格式。"""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 抑制第三方库的过多日志
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def main() -> None:
    """CLI 入口。"""
    logger = logging.getLogger(__name__)

    def _shutdown(signum: int, _frame: object) -> None:
        logger.info("收到退出信号 (%s)，正在清理...", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("启动 LOFTER 下载器 http://%s:%s", HOST, PORT)

    # 自动打开浏览器
    if os.environ.get("LOFTER_NO_BROWSER") != "1":
        webbrowser.open(f"http://{HOST}:{PORT}")

    app = create_app()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
