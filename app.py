"""LOFTER 下载器 — 应用入口。

默认以桌面 App 模式运行：Flask 在后台线程提供 API，pywebview 创建原生
窗口加载界面（无浏览器外壳）。设置环境变量 LOFTER_NO_GUI=1 可回退为
纯服务器模式（开发/测试用），通过浏览器访问 http://127.0.0.1:8080。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask

from config import DOWNLOAD_DIR, HOST, LOG_LEVEL, PORT, SESSION_PATH

WINDOW_TITLE = "LOFTER 下载器"

logger = logging.getLogger(__name__)


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
    """配置统一日志格式。

    始终写入日志文件（pythonw 无控制台时也可排查问题）；
    有控制台时同时输出到 stderr。
    """
    log_dir = SESSION_PATH.parent
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_dir / "app.log", encoding="utf-8"),
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    # 抑制第三方库的过多日志
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _run_server(app: Flask) -> None:
    """在后台线程运行 Flask（供桌面窗口模式使用）。"""
    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)


def _wait_server_ready(timeout: float = 15.0) -> bool:
    """等待 Flask 就绪，避免窗口打开时连接被拒绝。"""
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://{HOST}:{PORT}/api/login/status",
                timeout=2,
            ):
                return True
        except Exception:
            time.sleep(0.3)
    return False


def _find_main_window() -> int:
    """枚举窗口，找到可见且面积最大的同名主窗口（仅 Windows）。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    best_hwnd = 0
    best_area = 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd: int, _l_param: int) -> bool:
        nonlocal best_hwnd, best_area
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value != WINDOW_TITLE:
            return True
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        # 排除 WebView2 隐藏的极小窗口（实测有 202×56 的隐藏窗口）
        if area > best_area and area > 10000:
            best_area = area
            best_hwnd = hwnd
        return True

    user32.EnumWindows(_enum_proc, 0)
    return best_hwnd


def _set_window_icon() -> None:
    """尽力为桌面窗口设置图标（仅 Windows，失败不影响运行）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ico = Path(__file__).parent / "assets" / "icon.ico"
        if not ico.exists():
            return
        hwnd = _find_main_window()
        if not hwnd:
            logger.debug("未找到主窗口，跳过图标设置")
            return
        user32 = ctypes.windll.user32
        image_icon = 1
        lr_loadfromfile = 0x10
        hicon = user32.LoadImageW(
            None,
            str(ico),
            image_icon,
            0,
            0,
            lr_loadfromfile,
        )
        if not hicon:
            return
        wm_seticon = 0x80
        user32.SendMessageW(hwnd, wm_seticon, 0, hicon)  # ICON_SMALL
        user32.SendMessageW(hwnd, wm_seticon, 1, hicon)  # ICON_BIG
    except Exception:
        logger.debug("设置窗口图标失败", exc_info=True)


class _WindowApi:
    """暴露给前端 JS 的窗口控制接口（无边框窗口的红绿灯按钮使用）。

    前端通过 window.pywebview.api.close() / minimize() / maximize() /
    restore() 调用。
    """

    def __init__(self) -> None:
        self._window = None

    def attach(self, window: object) -> None:
        self._window = window

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def maximize(self) -> None:
        if self._window is not None:
            self._window.maximize()

    def restore(self) -> None:
        if self._window is not None:
            self._window.restore()

    def select_folder(self) -> str | None:
        """打开系统文件夹选择器，返回选中的路径（仅桌面 App 模式）。"""
        if self._window is None:
            return None
        try:
            import webview

            # 兼容新版 pywebview（FileDialog 枚举；旧版回退到 FOLDER_DIALOG）
            dialog_type = (
                getattr(webview.FileDialog, "FOLDER", None) or webview.FOLDER_DIALOG
            )
            result = self._window.create_file_dialog(
                dialog_type=dialog_type,
                allow_multiple=False,
            )
            if isinstance(result, (list, tuple)) and result:
                return result[0]
            if isinstance(result, str):
                return result
        except Exception:
            logger.debug("打开文件夹选择器失败", exc_info=True)
        return None

    def open_folder(self, path: str) -> None:
        """在系统文件管理器中打开指定文件夹（仅桌面 App 模式）。"""
        if not path:
            return
        try:
            os.startfile(path)
        except Exception:
            logger.debug("打开文件夹失败: %s", path, exc_info=True)


def _run_desktop(app: Flask) -> None:
    """桌面 App 模式：后台 Flask + pywebview 无边框原生窗口。"""
    import webview

    threading.Thread(target=_run_server, args=(app,), daemon=True).start()
    if not _wait_server_ready():
        logger.warning("等待 Flask 就绪超时，窗口可能短暂显示加载失败")

    # 任务栏图标正确分组：必须在创建窗口前设置
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "lofter.downloader",
            )
        except Exception:
            logger.debug("设置 AppUserModelID 失败", exc_info=True)

    api = _WindowApi()
    window = webview.create_window(
        WINDOW_TITLE,
        f"http://{HOST}:{PORT}",
        width=1160,
        height=820,
        min_size=(900, 640),
        frameless=True,
        easy_drag=True,
        js_api=api,
    )
    api.attach(window)
    window.events.shown += _set_window_icon
    webview.start()


def _run_browser_fallback(app: Flask) -> None:
    """浏览器回退模式：启动服务器并自动打开系统浏览器。"""

    def _open_browser() -> None:
        time.sleep(1.5)
        webbrowser.open(f"http://{HOST}:{PORT}")

    threading.Thread(target=_open_browser, daemon=True).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


def main() -> None:
    """应用入口。"""

    def _shutdown(signum: int, _frame: object) -> None:
        logger.info("收到退出信号 (%s)，正在清理...", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    app = create_app()

    # 纯服务器模式（开发/测试）
    if os.environ.get("LOFTER_NO_GUI") == "1":
        logger.info("服务器模式 http://%s:%s", HOST, PORT)
        app.run(host=HOST, port=PORT, debug=False, threaded=True)
        return

    try:
        logger.info("以桌面 App 模式启动")
        _run_desktop(app)
    except ImportError:
        logger.warning("pywebview 未安装，回退到浏览器模式")
        _run_browser_fallback(app)
    except Exception:
        logger.exception("桌面窗口启动失败，回退到浏览器模式")
        _run_browser_fallback(app)


if __name__ == "__main__":
    main()
