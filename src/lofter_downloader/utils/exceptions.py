"""自定义异常层次。

所有异常继承自 LofterError，便于上层统一捕获。
"""

from __future__ import annotations


class LofterError(Exception):
    """基础异常，所有自定义异常的基类。"""


class NetworkError(LofterError):
    """网络请求异常（连接失败、超时等）。"""


class ParseError(LofterError):
    """HTML 解析异常（元素未找到、结构变更等）。"""


class LoginRequiredError(LofterError):
    """需要先登录才能执行的操作。"""


class LoginFailedError(LofterError):
    """登录失败（Cookie 无效、密码错误等）。"""


class ResolveError(LofterError):
    """用户 ID 解析异常。"""


class TaskCanceledError(LofterError):
    """任务被用户取消。"""


class StorageError(LofterError):
    """文件存储异常。"""
