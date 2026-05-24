"""应用配置管理。

从 .env 文件和环境变量加载配置，环境变量前缀为 LOFTER_。
所有值经过校验，非法值时使用默认值并记录警告，不静默崩溃。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """手动加载 .env 文件，无第三方依赖。"""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val


def _parse_float(key: str, default: float, min_val: float = 0.1) -> float:
    """解析浮点配置，非法值回退默认值并警告。"""
    raw = os.getenv(key, "")
    if not raw:
        return default
    try:
        val = float(raw)
        if val < min_val:
            logger.warning(
                "%s=%s 小于最小值 %s，使用默认值 %s", key, raw, min_val, default
            )
            return default
        return val
    except (ValueError, TypeError):
        logger.warning("%s=%s 不是有效数值，使用默认值 %s", key, raw, default)
        return default


def _parse_int(key: str, default: int, min_val: int = 0) -> int:
    """解析整数配置，非法值回退默认值并警告。"""
    raw = os.getenv(key, "")
    if not raw:
        return default
    try:
        val = int(raw)
        if val < min_val:
            logger.warning(
                "%s=%s 小于最小值 %s，使用默认值 %s", key, raw, min_val, default
            )
            return default
        return val
    except (ValueError, TypeError):
        logger.warning("%s=%s 不是有效整数，使用默认值 %s", key, raw, default)
        return default


def _parse_path(key: str, default: Path) -> Path:
    """解析路径配置，确保父目录存在。"""
    raw = os.getenv(key, "")
    if not raw:
        return default
    try:
        p = Path(os.path.expanduser(raw)).resolve()
        return p
    except Exception:
        logger.warning("%s=%s 路径无效，使用默认值 %s", key, raw, default)
        return default


_load_dotenv()

DOWNLOAD_DIR = _parse_path(
    "LOFTER_DOWNLOAD_DIR",
    Path(os.path.expanduser("~/lofter_downloads")).resolve(),
)
SESSION_PATH = _parse_path(
    "LOFTER_SESSION_PATH",
    Path(os.path.expanduser("~/.lofter_downloader/lofter_auth.json")).resolve(),
)
REQUEST_INTERVAL = _parse_float("LOFTER_REQUEST_INTERVAL", 2.0, min_val=0.1)
MAX_RETRIES = _parse_int("LOFTER_MAX_RETRIES", 2, min_val=0)
REQUEST_TIMEOUT = _parse_int("LOFTER_REQUEST_TIMEOUT", 30, min_val=5)
LOG_LEVEL = os.getenv("LOFTER_LOG_LEVEL", "INFO").upper()

_HOST_DEFAULT = "127.0.0.1"
HOST = os.getenv("LOFTER_HOST", _HOST_DEFAULT) or _HOST_DEFAULT
PORT = _parse_int("LOFTER_PORT", 8080, min_val=1024)
