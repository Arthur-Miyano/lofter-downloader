# LOFTER Downloader

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Flask](https://img.shields.io/badge/flask-3.x-black.svg)](https://flask.palletsprojects.com/)

网易 LOFTER 文章下载器 — 通过浏览器 UI 一键下载 LOFTER 文章，支持 **Markdown**（含图片）、**TXT** 纯文本、**PDF** 三种导出格式。

## 项目背景

LOFTER 已于 2026 年实施全站登录墙 — 所有页面未登录时强制跳转到登录页。这意味着：

- 第三方下载工具（如直接 HTTP 请求）无法访问文章内容
- 需要真实的浏览器环境完成登录（含拼图验证码）
- 文章内容由 React SPA 动态渲染，需要等待 JS 执行后才能获取

本工具使用真实的 Chromium 浏览器登录并加载页面，将文章保存为本地文件，方便离线阅读和备份。

## 功能

- **浏览器登录** — 一键启动 Chromium 窗口，手动完成登录（含验证码），登录状态自动持久化
- **单篇下载** — 粘贴文章链接，下载单篇文章
- **批量下载** — 输入作者数字 ID，下载该作者全部公开文章
- **喜欢下载** — 一键下载你标记为"喜欢"的全部文章
- **多格式导出** — Markdown（含图片文件夹）、TXT 纯文本、PDF（含中文字体）
- **自定义目录** — 支持文本输入或文件夹浏览器选择保存位置
- **进度追踪** — 实时显示下载进度，失败文章自动跳过不中断批量任务

## 快速开始

### 系统要求

- Python 3.10+
- Chromium 浏览器（Playwright 会自动安装）

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/lofter-downloader.git
cd lofter-downloader

# 2. 安装依赖
pip install -r requirements.txt

# 3. 安装 Chromium
playwright install chromium

# 4. 启动应用
python app.py
```

浏览器会自动打开 http://127.0.0.1:8080。

### 使用流程

1. **登录** — 点击「启动浏览器登录」，在弹出的 Chromium 窗口中完成 LOFTER 登录（支持手机扫码 / 账号密码）
2. **检查登录** — 登录完成后点击「检查登录」，页面显示你的用户名即表示成功
3. **选择设置** — 选择导出格式（Markdown / TXT / PDF）和保存目录（可选）
4. **开始下载** — 根据需求选择下载方式（见下方）
5. **等待完成** — 在任务列表中查看进度，完成后在保存目录中查看文件

### 下载方式

#### 单篇文章

粘贴文章链接，点击下载。适用于保存特定文章。

```
https://xxxxxx.lofter.com/post/xxxxx_xxxxxxxxx
```

#### 作者全部文章

输入作者的数字 ID（在 LOFTER 个人主页 URL 中可以找到），点击「下载全部」。

> **如何获取作者数字 ID？**
> 打开作者的 LOFTER 主页，URL 格式为 `https://xxxxxx.lofter.com`。在浏览器地址栏输入 `https://www.lofter.com/blog/xxxxxx`，页面跳转后的 URL 中包含数字 ID。

#### 喜欢文章

点击「下载喜欢」按钮，自动下载你标记为"喜欢"的全部文章（需要先登录）。

## 配置

在项目根目录创建 `.env` 文件（可选），支持以下配置项：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LOFTER_DOWNLOAD_DIR` | `~/lofter_downloads` | 下载文件保存目录 |
| `LOFTER_SESSION_PATH` | `~/.lofter_downloader/lofter_auth.json` | 登录会话文件路径 |
| `LOFTER_REQUEST_INTERVAL` | `2.0` | 请求间隔（秒），避免触发反爬 |
| `LOFTER_MAX_RETRIES` | `2` | 下载失败最大重试次数 |
| `LOFTER_REQUEST_TIMEOUT` | `30` | 网络请求超时（秒） |
| `LOFTER_LOG_LEVEL` | `INFO` | 日志级别（DEBUG/INFO/WARNING/ERROR） |
| `LOFTER_HOST` | `127.0.0.1` | 服务器监听地址 |
| `LOFTER_PORT` | `8080` | 服务器监听端口 |

## 输出格式说明

### Markdown（默认）

每篇文章保存为独立文件夹，含 `index.md` 和 `images/` 子目录：

```
lofter_downloads/
└── 单篇下载/
    └── 文章标题/
        ├── index.md      # 文章正文（Markdown 格式）
        └── images/
            ├── 001.jpg   # 文中图片
            └── 002.png
```

### TXT

每篇文章保存为单个 `.txt` 文件，纯文本不含图片：

```
lofter_downloads/
└── 单篇下载/
    └── 文章标题.txt      # 标题 + 元数据 + 正文
```

### PDF

每篇文章保存为单个 `.pdf` 文件，含中文字体渲染，适合打印和分享：

```
lofter_downloads/
└── 单篇下载/
    └── 文章标题.pdf      # 格式化的 PDF 文档
```

## 技术架构

| 层次 | 技术 |
|------|------|
| Web 框架 | Flask（多线程开发服务器） |
| 前端 | Alpine.js + Tailwind CSS（CDN，零构建） |
| 浏览器自动化 | Playwright Chromium（加载 SPA + 执行 JS） |
| HTML 解析 | BeautifulSoup4 + lxml |
| 图片下载 | httpx（携带 Cookie + Referer） |
| PDF 生成 | fpdf2（CJK 字体支持） |

详细设计文档见 [docs/design.md](docs/design.md)，功能需求见 [docs/requirements.md](docs/requirements.md)。

## 常见问题（FAQ）

### 登录后下载仍然失败？

登录会话有时效性。点击「清除登录」后重新登录，然后重试下载。

### 下载的文章内容为空？

部分 LOFTER 文章使用较新的模板，可能无法完全提取。请提交 Issue 并附上文章链接。

### PDF 中文显示为方框？

PDF 导出需要系统中安装 CJK 字体。Windows 系统通常自带微软雅黑，Linux 需安装 `fonts-noto-cjk`：

```bash
# Ubuntu / Debian
sudo apt install fonts-noto-cjk

# CentOS / Fedora
sudo dnf install google-noto-cjk-fonts
```

### 提示「已有下载任务在进行中」？

同一时间只能运行一个下载任务。请等待当前任务完成或取消后再提交新任务。

### 如何取消正在进行的下载？

在任务列表中点击对应任务的「取消」按钮。已下载的文章会保留在保存目录中。

## 开发指南

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 运行特定测试
pytest tests/test_parser.py -k test_parse_basic_post

# 代码检查
ruff check .

# 格式化
ruff format .
```

## 免责声明

本工具仅供个人学习和内容备份使用。下载的文章请勿用于商业用途或二次发布。使用本工具即表示你同意遵守 LOFTER 的服务条款和相关法律法规。

## 开源许可

MIT License
