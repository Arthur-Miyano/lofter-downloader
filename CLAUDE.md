# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库工作时提供指引。

## 项目概览

LOFTER 下载器 — 一个从网易 LOFTER（中文博客平台）下载文章的 Python Web 应用。提供浏览器界面，支持将文章下载为 Markdown（含图片）、TXT 纯文本或 PDF 格式。所有页面访问均通过 **Playwright Chromium**，因为 LOFTER 实施了全站登录墙（所有 `*.lofter.com` 页面在未登录时重定向至 `/front/login`）。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 启动应用（运行于 http://127.0.0.1:8080）
python app.py

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

- **Flask**（Werkzeug 多线程开发服务器）— Web 框架
- **Alpine.js + Tailwind CSS**（CDN 引入，零构建步骤）— 前端单页应用
- **Playwright**（Python 异步 API）— 浏览器自动化加载页面
- **BeautifulSoup4 + lxml** — 从 `page.content()` 解析 HTML
- **httpx**（携带会话 Cookie）— 图片下载
- **storageState**（Playwright 原生格式）— 登录会话持久化
- **fpdf2** — PDF 文件生成（含 CJK 中文字体支持）

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
├── app.py                    # Flask 工厂函数 + CLI 入口
├── config.py                 # 纯配置，手动解析 .env
├── requirements.txt          # 8 个运行时依赖
│
├── downloader/
│   ├── browser.py            # BrowserManager（后台线程 + 事件循环）
│   ├── auth.py               # 登录函数（无类）
│   ├── pipeline.py           # DownloadPipeline（统一下载管道）
│   ├── parser.py             # 文章提取：JS 状态 → __NEXT_DATA__ → BS4
│   ├── models.py             # Post/Task 数据类 + TaskManager
│   ├── saver.py              # Markdown/TXT/PDF 存储 + 图片下载（含 Cookie）
│   └── exceptions.py         # 5 个异常类
│
├── web/
│   ├── routes.py             # Flask Blueprint（全部 API 路由）
│   └── templates/
│       └── index.html        # Alpine.js + Tailwind 单页应用
│
└── tests/
    ├── conftest.py
    ├── test_parser.py
    └── test_routes.py
```

### 数据流

1. 用户点击「下载全部」→ `POST /api/download/blog {user_id}`
2. 路由层在 `TaskManager`（内存字典）中创建 `Task`，调用 `browser.submit_async(_run_blog)`
3. `_run_blog` 协程在浏览器线程中执行：
   - 用会话文件中的 `storage_state` 创建 BrowserContext
   - `DownloadPipeline.collect_blog_links()` → 解析域名 → DWR API 分页 → 收集文章链接
   - 对每篇文章链接：`DownloadPipeline.run_post()` → `parser.extract_post()`（三级策略）
   - `PostSaver.save_dict()` → 按格式写入文件 + 下载图片
   - 每篇文章处理完成后更新 `task_manager`（current++、message=标题）
4. 前端每 2 秒轮询 `GET /api/tasks` 获取进度更新

### DownloadPipeline（统一下载管道）

一个类处理 3 种下载类型：
- `run_post(url)` → 单篇文章下载
- `collect_blog_links(user_id)` → 解析域名 → DWR ArchiveBean API → 收集文章链接
- `collect_likes_links()` → DWR BlogBean.queryLikePosts API → 收集喜欢文章链接

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
| GET | `/api/login/status` | 返回 `{logged_in, user_name}` |
| POST | `/api/login/start` | 启动 headed 浏览器至登录页 |
| POST | `/api/login/check` | 验证登录结果，保存会话 |
| DELETE | `/api/login` | 清除登录会话 |
| POST | `/api/download/post` | `{url}` → `{task_id}` |
| POST | `/api/download/blog` | `{user_id, format, download_dir}` → `{task_id}` |
| POST | `/api/download/likes` | `{format, download_dir}`（需登录）→ `{task_id}` |
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
- **多格式导出** — Markdown（含图片文件夹）、TXT（纯文本单文件）、PDF（含 CJK 字体的格式化单文件）。
- **作者 ID 输入框使用 `type="text" inputmode="numeric"`** — 不用 `type="number"`（在多数系统上会阻止粘贴和文本输入）。
