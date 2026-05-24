"""应用配置管理。

使用 pydantic-settings 加载配置，支持 .env 文件和环境变量覆盖。
所有配置项以 LOFTER_ 为前缀。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。

    从 .env 文件或环境变量加载，环境变量前缀为 LOFTER_。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LOFTER_",
    )

    # 下载存储根目录
    download_dir: Path = Path.home() / "lofter_downloads"

    # HTTP 请求间隔（秒），防止被反爬
    request_interval: float = 1.5

    # 最大重试次数
    max_retries: int = 3

    # 请求超时（秒）
    request_timeout: int = 30

    # 登录 Cookie（敏感信息，不提交到版本控制）
    cookie: str = ""

    # 手机端 API 认证 Token（lofter-phone-login-auth）
    lofter_phone_login_auth: str = ""

    # 数据库路径
    db_path: Path = Path.home() / ".lofter_downloader" / "data.db"

    # 日志级别
    log_level: str = "INFO"

    # 最大并发请求数
    max_concurrency: int = 3


settings = Settings()
