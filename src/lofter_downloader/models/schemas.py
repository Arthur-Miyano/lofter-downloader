"""Pydantic 数据模型，用于 API 请求/响应校验。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, HttpUrl


class DownloadPostRequest(BaseModel):
    """单篇文章下载请求。"""

    url: HttpUrl


class DownloadBlogRequest(BaseModel):
    """作者全部文章下载请求。"""

    user_id: int


class DownloadCollectionRequest(BaseModel):
    """合集下载请求。"""

    url: HttpUrl


class LoginCookieRequest(BaseModel):
    """Cookie 登录请求。"""

    cookie: str


class LoginAccountRequest(BaseModel):
    """账号密码登录请求。"""

    username: str
    password: str


class TaskResponse(BaseModel):
    """任务状态响应。"""

    task_id: str
    type: str
    status: str
    progress: float
    message: str
    error: str
    created_at: datetime


class LoginStatusResponse(BaseModel):
    """登录状态响应。"""

    logged_in: bool


class StatsResponse(BaseModel):
    """下载统计响应。"""

    total: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    canceled: int = 0
