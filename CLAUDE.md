# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库工作时提供指引。

## 项目概览

LOFTER / AO3 下载器 — 一个从网易 LOFTER 与 AO3（Archive of Our Own）下载文章的 Python 应用。默认以 pywebview 无边框桌面窗口运行（`LOFTER_NO_GUI=1` 时为纯服务器 + 浏览器界面），支持将文章下载为 Markdown（含图片）、TXT 纯文本、PDF 或 EPUB 格式。LOFTER 所有页面访问均通过 **Playwright Chromium**，因为 LOFTER 实施了全站登录墙（所有 `*.lofter.com` 页面在未登录时重定向至 `/front/login`）。AO3 公开文可通过 httpx 直接下载。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 启动应用（默认：pywebview 无边框桌面窗口，Flask 跑在后台线程）
python app.py

# 纯服务器模式（开发/测试），浏览器访问 http://127.0.0.1:8080
LOFTER_NO_GUI=1 python app.py

# 运行全部测试
pytest

# 运行部分测试
pytest tests/test_parser.py -k test_parse_basic_post

# 代码检查
ruff check .

# 代码格式化
ruff format .
```

## 架构

### 技术栈

- **pywebview**（Edge WebView2）— 桌面 App 窗口，Flask 跑在后台线程
- **Flask**（Werkzeug 多线程开发服务器）— Web 框架
- **Alpine.js + Tailwind CSS**（CDN 引入，零构建步骤）— 前端单页应用
- **Playwright**（Python 异步 API）— 浏览器自动化加载页面
- **BeautifulSoup4 + lxml** — 从 `page.content()` 解析 HTML
- **markdownify** — HTML → Markdown（LOFTER 正文与 AO3 解析管道共用）
- **aiofiles** — 异步写文件（saver 存储层）
- **httpx**（携带会话 Cookie）— 图片下载、AO3 页面与官方导出下载
- **storageState**（Playwright 原生格式）— LOFTER 登录会话持久化
- **fpdf2** — PDF 文件生成（含 CJK 中文字体支持）
- **ebooklib** — EPUB 生成

### 核心设计：Flask 同步 + Playwright 异步

Flask 是同步框架，Playwright 是异步 API。桥接方案：**后台守护线程**运行独立的 asyncio 事件循环，通过 `asyncio.run_coroutine_threadsafe` 提交异步操作：

- **`BrowserManager.submit(coro)`** — 阻塞等待 `.result(timeout=60)`，用于短操作（如登录检查）
- **`BrowserManager.submit_async(coro)`** — 即发即忘，用于长时间下载任务

```
Flask 请求线程（线程池）                 浏览器事件循环线程（守护线程）
        │                                        │
        ├── submit(coro) ──────────────────────► │  登录检查
        │   .result(timeout=60) ◄─────────────── │
        │                                        │
        ├── submit_async(coro) ─────────────────► │  下载任务
        │   （立即返回）                          │  更新内存中的 TaskManager
```

### 项目结构（扁平化，~12 个源文件）

```
lofter-downloader/
├── app.py                    # 应用入口：pywebview 桌面窗口 + Flask 后台线程
├── config.py                 # 纯配置，手动解析 .env
├── requirements.txt          # 运行时依赖
│
├── downloader/
│   ├── browser.py            # BrowserManager（后台线程 + 事件循环）
│   ├── auth.py               # LOFTER 登录函数（无类）
│   ├── pipeline.py           # DownloadPipeline（LOFTER 统一下载管道）
│   ├── ao3.py                # AO3Client（清单/官方导出/解析管道）
│   ├── parser.py             # 文章提取：JS 状态 → __NEXT_DATA__ → BS4
│   ├── models.py             # Post/Task 数据类 + TaskManager
│   ├── saver.py              # Markdown/TXT/PDF/EPUB 存储 + 图片下载（含 Cookie）
│   └── exceptions.py         # 4 个异常类（AO3Error 定义在 ao3.py）
│
├── web/
│   ├── routes.py             # Flask Blueprint（全部 API 路由）
│   └── templates/
│       └── index.html        # Alpine.js + Tailwind 单页应用（macOS 风格 UI）
│
├── scripts/
│   ├── make_icon.py          # 生成 macOS 风格应用图标（assets/icon.*）
│   └── 创建桌面快捷方式.ps1   # 在桌面创建一键启动快捷方式（Windows）
├── assets/                   # 应用图标（icon.ico / icon.png）
├── 启动 LOFTER 下载器.bat     # Windows 一键启动脚本
├── diagnose.py               # 作者本地调试用脚本（已 gitignore，非测试体系）
│
└── tests/
    ├── conftest.py
    ├── test_parser.py
    ├── test_models.py
    ├── test_routes.py
    ├── test_saver.py
    ├── test_ao3.py
    └── fixtures/
```

### 数据流

1. 用户点击「下载全部」或「选择文章」
   - 「选择文章」→ `POST /api/list/blog {user_id}` → 创建清单任务 → 前端轮询 → 弹出选择面板 → 用户勾选 → `POST /api/download/blog {user_id, urls}`
   - 「下载全部」→ `POST /api/download/blog {user_id}`
2. 路由层在 `TaskManager`（内存字典）中创建 `Task`，调用 `browser.submit_async(_run_blog)`
3. `_run_blog` 协程在浏览器线程中执行：
   - 用会话文件中的 `storage_state` 创建 BrowserContext
   - `DownloadPipeline.collect_blog_links()` → 解析域名 → DWR API 分页 → 收集文章链接与标题
   - 对每篇文章链接：`DownloadPipeline.run_post()` → `parser.extract_post()`（BS4 兜底）
   - `PostSaver.save_dict()` → 按格式写入文件 + 下载图片；文件/目录名冲突时追加 `(2)`、`(3)`
   - 每篇文章处理完成后更新 `task_manager`（current++、message=标题）
4. AO3 下载走 `AO3Client`：清单任务 → 选择面板 → 官方导出（EPUB/PDF/HTML/MOBI/AZW3）或解析为 MD/TXT/PDF/EPUB
5. 前端每 2 秒轮询 `GET /api/tasks` 获取进度更新

### DownloadPipeline（LOFTER 统一下载管道）

一个类处理 3 种下载类型：
- `run_post(url)` → 单篇文章下载
- `collect_blog_items(user_id)` / `collect_blog_links(user_id)` → 解析域名 → DWR ArchiveBean API → 收集文章链接与标题
- `collect_likes_links()` → DWR BlogBean.queryLikePosts API → 收集喜欢文章链接

### AO3Client

处理 AO3 公开文：
- `list_series(id)` / `list_author(name)` / `list_batch(urls)` → 作品清单
- `get_official_download_url(id, fmt)` / `download_official(id, fmt, dest)` → EPUB/PDF/HTML/MOBI/AZW3
- `parse_work(id)` → 解析为统一 post_dict，复用 `PostSaver` 输出 MD/TXT/PDF/EPUB

### 文章提取（3 级策略，按优先级降级）

在 `downloader/parser.py` 中：
1. `page.evaluate("window.__INITIAL_STATE__")` — 直接读取 JS 状态（最快）
2. `page.evaluate("document.getElementById('__NEXT_DATA__')?.textContent")` — Next.js SSR 数据
3. BS4 解析 `page.content()` — CSS 选择器作为最后兜底

**页面加载策略：** `wait_until="load"` + `page.wait_for_selector()` + 2 秒 React hydration 缓冲。不用 `"domcontentloaded"`（SPA 渲染过早）也不用 `"networkidle"`（分析脚本可能导致永不触发）。

### 登录流程

`web/routes.py` 中的模块级状态（单用户工具）：
1. `POST /api/login/start` → 启动临时 headed Chromium → 打开 LOFTER 登录页
2. 用户在 headed 浏览器中手动完成登录（含拼图验证码）
3. `POST /api/login/check` → 导航到 LOFTER 首页 → 检查 URL 是否含 `/front/login` → 从 `window.userSignedIn` 提取用户名 → 保存 `storage_state()` 到 JSON 文件 → 关闭 headed 浏览器
4. 所有下载操作：`new_context(storage_state=session_path)` 加载已保存的会话

### API 路由

| 方法 | 路径 | 用途 |
|--------|------|---------|
| GET | `/` | 提供前端页面 index.html |
| GET | `/api/login/status` | 返回 `{logged_in, user_name, login_starting, login_ready, login_start_error}` |
| POST | `/api/login/start` | 启动 headed 浏览器至登录页 |
| POST | `/api/login/check` | 验证登录结果，保存会话 |
| DELETE | `/api/login` | 清除登录会话 |
| POST | `/api/download/post` | `{url, format, download_dir}` → `{task_id}` |
| POST | `/api/download/blog` | `{user_id, format, download_dir, urls?}` → `{task_id}` |
| POST | `/api/download/likes` | `{format, download_dir}`（需登录）→ `{task_id}` |
| POST | `/api/download/ao3` | `{urls, source, format, download_dir}` → `{task_id}` |
| POST | `/api/list/blog` | `{user_id}` → `{task_id}`，结果在 `task.result` |
| POST | `/api/list/ao3` | `{kind, query}` → `{task_id}`，结果在 `task.result` |
| GET | `/api/tasks` | 列出全部任务 |
| GET | `/api/tasks/<id>` | 查询单个任务 |
| POST | `/api/tasks/<id>/cancel` | 取消任务 |

无 WebSocket，无 `/api/stats`，无数据库。任务仅存在于内存中。

### 配置（config.py，~100 行）

手动解析 `.env` 文件（无 pydantic 依赖）。8 个配置项：`DOWNLOAD_DIR`、`SESSION_PATH`、`REQUEST_INTERVAL`、`MAX_RETRIES`、`REQUEST_TIMEOUT`、`LOG_LEVEL`、`HOST`、`PORT`。环境变量前缀：`LOFTER_`。

### 关键设计决策

- **无数据库** — 任务追踪使用内存字典（单用户工具）。无 SQLAlchemy。
- **无 Pydantic** — Post/Task 使用纯 Python 数据类。路由中手动 JSON 反序列化。
- **无 WebSocket** — 前端每 2 秒轮询 `GET /api/tasks`。更简单可靠。
- **逐篇错误容忍** — 博客/喜欢批量下载时，单篇文章失败会跳过并继续。只有零篇文章或浏览器崩溃才标记整个任务失败。
- **Cookie 携带图片下载** — `PostSaver` 从会话文件中提取 Cookie，传递给 httpx 用于下载登录墙后的图片。
- **多格式导出** — Markdown（含图片文件夹）、TXT（纯文本单文件）、PDF（含 CJK 字体的格式化单文件）、EPUB。
- **文件/目录命名** — 去掉 UUID 随机后缀，冲突时追加 `(2)`、`(3)`；子目录按作者/博客名/AO3 作者组织。
- **文章选择下载** — 作者全部 / AO3 系列/作者/批量链接先列出清单，用户勾选后再下载。
- **作者 ID 输入框使用 `type="text" inputmode="numeric"`** — 不用 `type="number"`（在多数系统上会阻止粘贴和文本输入）。
