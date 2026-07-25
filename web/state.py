"""API 共享状态：蓝图与单用户模式的模块级状态。

包含 task_manager、browser 注入点、登录流程引用、下载/清单并发槽位与锁。
各路由模块统一经此读写，避免循环导入。
"""

from __future__ import annotations

import threading

from flask import Blueprint

from downloader.models import TaskManager

api = Blueprint("api", __name__, url_prefix="/api")

task_manager = TaskManager()
browser = None  # 由 app.py 注入 BrowserManager 实例

# 登录状态
_login_browser = None  # headed 浏览器实例
_login_context = None  # headed BrowserContext
_login_page = None  # headed Page
_login_in_progress = False  # 防止重复启动登录
_login_start_error = ""  # 启动阶段的错误信息
_user_name = ""

# 下载并发控制
_running_task_id: str | None = None  # 当前运行中的任务 ID
_task_lock = threading.Lock()

# 清单并发控制（与下载任务互不阻塞，同类清单同时只允许一个）
_running_list_kind: str | None = None  # 当前运行中的清单任务类型
_list_task_lock = threading.Lock()
