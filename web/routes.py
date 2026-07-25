"""Flask API 路由聚合层。

原单文件路由已按域拆分：
- web.state：api 蓝图与模块级共享状态（task_manager、browser、登录/并发状态）
- web.helpers：任务辅助函数（取消检查、错误分类、列表摘要等）
- web.login：登录相关端点（/login/status、/login/start、/login/check、DELETE /login）
- web.downloads：下载与清单端点、任务创建、后台下载协程
- web.tasks：任务查询/取消端点（/tasks、/tasks/<id>、/tasks/<id>/cancel）

本模块导入各域模块以完成路由注册，并 re-export 对外需要的名字。
"""

from __future__ import annotations

from config import DOWNLOAD_DIR
from web import downloads, login, tasks  # noqa: F401  # 导入即注册路由
from web.downloads import (
    _resolve_base_dir,
    _run_blog,
    _run_download_ao3,
    _run_likes,
    _run_list_ao3,
    _run_list_blog,
    _run_post,
)
from web.state import api

__all__ = [
    "DOWNLOAD_DIR",
    "_resolve_base_dir",
    "_run_blog",
    "_run_download_ao3",
    "_run_likes",
    "_run_list_ao3",
    "_run_list_blog",
    "_run_post",
    "api",
]
