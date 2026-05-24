"""LOFTER 文章下载器。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("lofter-downloader")
except PackageNotFoundError:
    __version__ = "0.1.0.dev0"
