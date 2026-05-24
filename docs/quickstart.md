# 快速开始

## 安装

### 从 PyPI 安装

```bash
pip install lofter-downloader
```

### 从源码安装

```bash
git clone https://github.com/user/lofter-downloader.git
cd lofter-downloader
pip install -e ".[dev,docs]"
```

## 启动

```bash
python -m lofter_downloader
```

浏览器将自动打开 `http://127.0.0.1:8080`。

## 配置

复制 `.env.example` 为 `.env` 并按需修改：

```bash
cp .env.example .env
```

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `LOFTER_DOWNLOAD_DIR` | `~/lofter_downloads` | 下载保存目录 |
| `LOFTER_REQUEST_INTERVAL` | `1.5` | 请求间隔（秒） |
| `LOFTER_LOG_LEVEL` | `INFO` | 日志级别 |

## 使用方法

1. 打开浏览器访问 `http://127.0.0.1:8080`
2. 粘贴文章链接进行单篇下载
3. 输入作者数字 ID 下载全部文章
4. 输入合集链接下载合集
5. 导入 Cookie 后可下载收藏文章
