"""日志配置模块。

提供统一的日志记录器，格式为：
  2026-05-24 15:30:00 | INFO     | module:42 | message
"""

from __future__ import annotations

import logging
import sys

from lofter_downloader.config import settings


def setup_logger(name: str = __name__) -> logging.Logger:
    """配置并返回指定名称的日志记录器。

    Parameters
    ----------
    name : str
        日志记录器名称，通常使用 __name__

    Returns
    -------
    logging.Logger
        配置完成的日志记录器实例
    """
    logger = logging.getLogger(name)
    logger.setLevel(settings.log_level.upper())

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ),
        )
        logger.addHandler(handler)

    return logger
