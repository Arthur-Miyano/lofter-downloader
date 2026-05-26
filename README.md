# LOFTER Downloader

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Flask](https://img.shields.io/badge/flask-3.x-black.svg?logo=flask)](https://flask.palletsprojects.com/)
[![Playwright](https://img.shields.io/badge/playwright-1.x-2EAD33.svg?logo=playwright)](https://playwright.dev/)

从网易 LOFTER 一键备份文章到本地。通过浏览器 UI 操作，支持 Markdown（含图片）、TXT 纯文本、PDF 三种导出格式。

## Table of Contents

- [Background](#background)
- [Features](#features)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Configuration](#configuration)
- [Output Formats](#output-formats)
- [Tech Stack](#tech-stack)
- [FAQ](#faq)
- [Development](#development)
- [Disclaimer](#disclaimer)

## Background

LOFTER 自 2026 年起实施了全站登录墙 — 所有页面在未登录状态下强制跳转到 `/front/login`。这意味着：

- 传统 HTTP 请求爬虫无法获取任何文章内容
- 需要真实浏览器环境完成拼图验证码登录
- 文章由 React SPA 动态渲染，必须等待 JavaScript 执行

本工具通过 Playwright 驱动 Chromium 完成登录和页面加载，将文章保存为本地文件，方便离线阅读和数据备份。

## Features

**登录与认证**
- 一键启动 Chromium 窗口，手动完成 LOFTER 登录（含拼图验证码）
- 登录状态自动持久化，下次启动无需重新登录
- 支持 Token 认证方式访问 API 接口

**下载模式**
- **单篇文章** — 粘贴链接，下载单篇
- **作者全部文章** — 输入作者数字 ID，批量下载所有公开文章
- **喜欢的文章** — 一键下载已标记为"喜欢"的全部文章（需登录）

**导出格式**
- **Markdown** — 文章正文 + 独立图片文件夹，适合归档和二次编辑
- **TXT** — 纯文本单文件，不含图片，适合文本分析
- **PDF** — 含 CJK 中文字体渲染的格式化文档，适合打印和分享

**用户体验**
- 自定义保存目录，支持文本输入或系统文件夹选择器
- 实时显示下载进度，批量任务中单篇失败自动跳过继续
- 支持任务取消，已下载内容会被保留

## Quick Start

### Prerequisites

- Python 3.10 or later
- Chromium browser (auto-installed by Playwright)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/Arthur-Miyano/lofter-downloader.git
cd lofter-downloader

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install Chromium
playwright install chromium

# 4. Start the application
python app.py
```

Open http://127.0.0.1:8080 in your browser.

## Usage

### Step 1: Login

Click "Start Browser Login" — a Chromium window will open with the LOFTER login page. Complete the login manually (QR code scan or username/password).

Click "Check Login" to verify. Once your username appears, you're ready.

### Step 2: Choose Settings

Select your preferred export format (Markdown / TXT / PDF) and optionally set a custom download directory.

### Step 3: Download

Choose one of three download methods:

| Method | What You Need | What It Does |
|--------|--------------|--------------|
| **Single Post** | Post URL (e.g. `https://xxxxxx.lofter.com/post/xxxxx_xxxxxxxxx`) | Downloads one specific article |
| **Author Blog** | Author's numeric ID (find via `https://www.lofter.com/blog/xxxxxx`) | Downloads all public posts by an author |
| **Liked Posts** | Must be logged in | Downloads all posts you've liked |

### Finding an Author's Numeric ID

Open the author's LOFTER page (`https://xxxxxx.lofter.com`), then visit `https://www.lofter.com/blog/xxxxxx` in your browser. The redirected URL will contain a numeric ID.

### Step 4: Wait and Check

Monitor progress in the task list. Files are saved to your chosen download directory once complete.

## Configuration

Create a `.env` file in the project root (optional). All variables are prefixed with `LOFTER_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `LOFTER_DOWNLOAD_DIR` | `~/lofter_downloads` | Output directory for downloaded files |
| `LOFTER_SESSION_PATH` | `~/.lofter_downloader/lofter_auth.json` | Session storage path |
| `LOFTER_REQUEST_INTERVAL` | `2.0` | Delay between requests (seconds), for rate limiting |
| `LOFTER_MAX_RETRIES` | `2` | Max retries on download failure |
| `LOFTER_REQUEST_TIMEOUT` | `30` | Network request timeout (seconds) |
| `LOFTER_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `LOFTER_HOST` | `127.0.0.1` | Server listen address |
| `LOFTER_PORT` | `8080` | Server listen port |

## Output Formats

### Markdown (default)

Each post saved as a folder with `index.md` and an `images/` subdirectory:

```
lofter_downloads/
└── Single Posts/
    └── Post Title/
        ├── index.md      # Post content in Markdown
        └── images/
            ├── 001.jpg
            └── 002.png
```

### TXT

Each post saved as a single `.txt` file, plain text without images:

```
lofter_downloads/
└── Single Posts/
    └── Post Title.txt    # Title + metadata + body
```

### PDF

Each post saved as a single `.pdf` file with CJK font rendering:

```
lofter_downloads/
└── Single Posts/
    └── Post Title.pdf    # Formatted PDF document
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | Flask (multi-threaded dev server) |
| Frontend | Alpine.js + Tailwind CSS (CDN, zero build step) |
| Browser Automation | Playwright Chromium (SPA rendering + JS execution) |
| HTML Parsing | BeautifulSoup4 + lxml |
| Image Download | httpx (with Cookie + Referer headers) |
| PDF Generation | fpdf2 (with CJK font support) |

For detailed architecture documentation, see [docs/design.md](docs/design.md).

## FAQ

<details>
<summary><strong>Login succeeded but downloads fail?</strong></summary>
The session may have expired. Use "Clear Login" and re-authenticate, then retry.
</details>

<details>
<summary><strong>Downloaded articles have empty content?</strong></summary>
Some LOFTER articles use newer templates that may not parse correctly. Please file an issue with the article URL.
</details>

<details>
<summary><strong>PDF shows boxes instead of Chinese characters?</strong></summary>
CJK fonts are required for PDF export. Windows typically includes Microsoft YaHei by default. On Linux:

```bash
# Ubuntu / Debian
sudo apt install fonts-noto-cjk

# CentOS / Fedora
sudo dnf install google-noto-cjk-fonts
```
</details>

<details>
<summary><strong>"A download task is already in progress"?</strong></summary>
Only one download task can run at a time. Wait for the current task to finish or cancel it first.
</details>

<details>
<summary><strong>How do I cancel a running download?</strong></summary>
Click the "Cancel" button on the task in the task list. Already-downloaded articles are kept.
</details>

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run specific tests
pytest tests/test_parser.py -k test_parse_basic_post

# Lint
ruff check .

# Format
ruff format .
```

## Disclaimer

This tool is for personal learning and content backup only. Downloaded articles may not be used for commercial purposes or redistribution. By using this tool, you agree to comply with LOFTER's Terms of Service and applicable laws.

## License

[MIT](LICENSE)
