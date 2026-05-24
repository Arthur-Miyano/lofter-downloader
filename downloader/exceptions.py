"""自定义异常层次。"""

from __future__ import annotations


class LofterError(Exception):
    """所有自定义异常的基类。"""


class LoginRequiredError(LofterError):
    """需要登录才能访问。"""


class ParseError(LofterError):
    """页面解析失败（元素缺失、结构变更等）。"""


class NetworkError(LofterError):
    """网络请求失败。"""


class TaskCanceledError(LofterError):
    """任务被用户取消。"""
