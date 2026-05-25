# LOFTER 下载器 — 技术设计文档

> 版本：1.0.2 | 日期：2026-05-25

## 一、技术栈

| 层次 | 选型 | 说明 |
|------|------|------|
| Web 框架 | Flask 3.x | 同步框架，Werkzeug 多线程开发服务器 |
| 前端 | Alpine.js + Tailwind CSS | CDN 引入，零构建 |
| 浏览器自动化 | Playwright (Python async API) | 加载 SPA 页面、执行 JS 提取 |
| HTML 解析 | BeautifulSoup4 + lxml | 从 `page.content()` 解析 DOM |
| 图片下载 | httpx (async) | 携带 session cookie + Referer 头 |
| PDF 生成 | fpdf2 | 纯 Python，支持 CJK 字体 |
| 登录持久化 | storageState JSON | Playwright 原生格式 |
| 任务追踪 | 内存 dict + threading.Lock | 单用户，无需持久化 |

## 二、项目结构

```
lofter-downloader/
├── app.py                    # Flask 工厂函数 + CLI 入口
├── config.py                 # 配置（手动 .env 解析，含容错）
├── requirements.txt          # 运行时依赖
├── pyproject.toml            # 项目元数据 + 工具配置
│
├── downloader/
│   ├── browser.py            # BrowserManager（后台线程 + 事件循环）
│   ├── auth.py               # 登录函数（无类）
│   ├── pipeline.py           # DownloadPipeline（统一下载流程）
│   ├── parser.py             # 文章解析（3 策略降级）
│   ├── models.py             # Post / Task 数据类 + TaskManager（线程安全）
│   ├── saver.py              # Markdown / TXT / PDF 存储 + 图片下载
│   └── exceptions.py         # 5 个自定义异常
│
├── web/
│   ├── routes.py             # Flask Blueprint（全部 API 路由）
│   └── templates/
│       └── index.html        # SPA 前端
│
├── docs/
│   ├── requirements.md       # 功能需求
│   └── design.md             # 本文档
│
└── tests/
    ├── conftest.py
    ├── test_parser.py        # 解析器单元测试
    └── test_routes.py        # API 路由集成测试
```

## 三、核心架构：Flask 同步 + Playwright 异步

**问题：** Flask 是同步框架，Playwright 是异步 API。

**方案：** 后台守护线程运行独立的 asyncio 事件循环，Flask 请求线程通过 `asyncio.run_coroutine_threadsafe()` 提交任务。

```
┌─────────────────────────────┐     ┌──────────────────────────────┐
│  Flask 请求线程（线程池）     │     │  浏览器事件循环线程（守护）    │
│                             │     │                              │
│  browser.submit(coro) ──────┼────►│  asyncio 事件循环            │
│    .result(timeout=60) ◄────┼─────│  执行协程，返回结果           │
│                             │     │                              │
│  browser.submit_async(coro)─┼────►│  执行协程（不等待结果）       │
│    （保存 future 用于取消）  │     │  更新 task_manager 内存状态   │
└─────────────────────────────┘     └──────────────────────────────┘
```

- `submit(coro)` → 阻塞等待结果，用于登录检查等短操作（timeout=60s）
- `submit_async(coro)` → 返回 `concurrent.futures.Future`，调用方保存引用用于后续取消操作

**并发控制：** Flask 请求线程在创建新下载任务前检查是否有运行中任务。同一时间最多 1 个下载任务执行。

## 四、模块设计

### 4.1 `config.py` — 配置

手动解析 `.env` 文件，无第三方依赖。暴露 8 个模块级变量：

`DOWNLOAD_DIR`, `SESSION_PATH`, `REQUEST_INTERVAL`, `MAX_RETRIES`, `REQUEST_TIMEOUT`, `LOG_LEVEL`, `HOST`, `PORT`

**容错要求：** `float()` / `int()` 转换需 try/except，非法值时回退到默认值并记录警告日志，不可崩溃。

### 4.2 `downloader/browser.py` — 浏览器管理

```
BrowserManager
├── start()              → 后台线程启动事件循环 + 启动 headless Chromium
├── submit(coro)         → 提交协程，阻塞等待结果（线程安全）
├── submit_async(coro)   → 提交协程，返回 Future 对象（线程安全）
├── new_context(storage) → 创建 BrowserContext（加载 storageState）
└── stop()               → 关闭浏览器 + 停止事件循环（atexit 注册）
```

**设计要点：**
- **双进程架构：** 单一 headless Chromium 用于下载（`self._browser`，持久化）。登录时启动第二个独立的 headed Chromium 进程（临时，登录成功后关闭）
- 登录 headed 浏览器需 5 分钟超时机制，超时自动关闭并清理资源
- 线程安全：`asyncio.run_coroutine_threadsafe` 是 Python 官方提供的线程间协程提交 API

### 4.3 `downloader/auth.py` — 登录

纯函数，无类：

```
load_storage_state(path) → dict | None    # 读取 session 文件
start_login(playwright, loop, on_timeout) → (browser, ctx, page)  # 启动 headed 浏览器
check_login(page) → (bool, username)      # 检查登录 + 提取用户名
save_session(context, path)               # 持久化 storageState
clear_session(path)                       # 删除 session 文件
verify_session(context) → bool            # 验证会话有效性
```

### 4.4 `downloader/pipeline.py` — 统一下载管道

```
DownloadPipeline
├── run_post(url) → list[dict]                    # 单篇下载
├── collect_blog_links(user_id) → (list[str], str) # 收集作者全部文章链接
├── collect_likes_links() → list[str]              # 收集喜欢文章链接
│
├── _resolve_domain(user_id) → str | None  # ID → 博客域名
├── _get_author_info(domain) → (str, str)  # 获取 authorId + blogName
├── _collect_blog_via_dwr(author_id, url)  # DWR ArchiveBean API 分页
├── _call_dwr_likes(page, user_id)         # DWR BlogBean API 分页
└── _paginate(base_url) → list[str]        # SSR ?page=N 备选分页
```

**博客文章收集策略：**
1. 主策略：DWR `ArchiveBean.getArchivePostByTime` — 时间戳游标分页，每批 50 篇
2. 备选策略：`?page=N` SSR 分页 — 仅旧版 LOFTER 模板有效

**喜欢文章收集策略：**
- DWR `BlogBean.queryLikePosts` — offset 分页，每批 100 篇
- 首次调用需从 `window.userSignedIn` 获取当前用户 blogId

**页面加载策略：**
1. `page.goto(url, wait_until="load")` — 等待所有资源加载
2. `page.wait_for_selector()` — 等待 SPA 渲染内容
3. `asyncio.sleep(2)` — React hydration 缓冲

**错误韧性：** 批量下载中单篇文章失败 → 记录日志 → 跳过 → 继续下一篇。只有零文章或浏览器崩溃才标记任务失败。

### 4.5 `downloader/parser.py` — 文章解析

```
extract_post(page, url) → dict | None
├── Strategy 1: page.evaluate("window.__INITIAL_STATE__")
├── Strategy 2: page.evaluate("document.getElementById('__NEXT_DATA__')?.textContent")
└── Strategy 3: _try_html(page.content(), url)
    ├── 3a: CSS 选择器降级 — .m-postdtl, article 等
    ├── 3b: 日期提取 — .date 元素 + 正则 YYYY.MM.DD / YYYY-MM-DD
    └── 3c: 图片收集 — img[src] 遍历
```

提取字段：`title`, `author`, `publish_date`, `content_html`, `content_markdown`, `image_urls`

**CSS 选择器注意事项：** `select_one` 按 DOM 顺序返回首个匹配。需避免宽泛选择器（如 `[class*='detail']`）匹配到 `body.p-detailpage` 而非实际文章容器。当前优先使用 `.m-postdtl`（LOFTER 实测 ~6700 字符清洁内容）。

### 4.6 `downloader/models.py` — 数据模型

```python
@dataclass
class Post:
    url, title, author, publish_date, content_html, content_markdown, image_urls

class TaskStatus(Enum): PENDING, RUNNING, COMPLETED, FAILED, CANCELED

@dataclass
class Task:
    task_id, type, status, current, total, message, error, result, created_at
    progress: float  # computed property

class TaskManager:
    _tasks: dict[str, Task]
    _lock: threading.Lock
    create(), get(), list_all(), update()
    cleanup(max_items=100)   # 淘汰最旧的非运行中任务
    cancel(task_id) → bool   # 设置 CANCELED + 触发 CancelledError
    set_future(task_id, fut) # 关联 Future 用于取消
    clear_finished() → int   # 登出时清除历史
```

### 4.7 `downloader/saver.py` — 存储

```
PostSaver(base_dir, cookies=None)
├── save_dict(post_dict, sub_dir, fmt) → Path
│   ├── fmt="md": _make_post_dir() + _save_markdown() + _save_images()
│   ├── fmt="txt": _save_txt()  # 单文件，纯文本提取
│   └── fmt="pdf": _save_pdf()  # 单文件，fpdf2 + CJK 字体
├── _save_markdown(path, post)        # MARKDOWN_TEMPLATE
├── _save_txt(post, sub_dir)          # BS4 文本提取 + 元数据头
├── _save_pdf(post, sub_dir)          # fpdf2 PDF 生成
├── _save_images(images_dir, urls)    # 重试 + 指数退避
├── _font_path() → str                # 系统 CJK 字体查找
└── close()                           # 关闭 httpx 客户端
```

**Cookie 转换：** Playwright storageState 的 cookies 格式为 `[{name, value, domain, path, ...}]`。`PostSaver.__init__()` 提取 `{name: value}` 键值对传给 httpx，用于图片下载携带认证 cookie。

**图片下载要求：**
- 设置 `Referer` 请求头为 LOFTER 域名（反盗链）
- 重试最多 2 次，含指数退避（1s → 2s → 4s）
- 失败时记录警告日志，不终止文章保存

**PDF 字体：** 按系统自动查找 — Windows 微软雅黑 / 宋体、macOS 苹方、Linux Noto Sans CJK。查找失败时抛出异常提示用户安装 CJK 字体。

**文件名安全：** `_sanitize_filename()` 替换 `<>:"/\|?*` 为 `_`，去除首尾空格和点，截断到 200 字符。全特殊字符标题降级为 `untitled_{timestamp}`。

### 4.8 `downloader/exceptions.py` — 异常

```
LofterError (base)
├── LoginRequiredError    — 会话过期 / 未登录
├── ParseError            — 所有提取策略失败
├── NetworkError          — 页面导航重试耗尽
└── TaskCanceledError     — 任务被用户取消
```

### 4.9 `web/routes.py` — API 路由

Flask Blueprint。模块级变量维护登录状态（单用户模式）：
- `_login_browser` / `_login_context` / `_login_page` — 登录中的 headed 浏览器资源
- `_user_name` — 已登录用户名
- `task_manager` — 全局 TaskManager 实例（线程安全）
- `browser` — 由 `app.py` 注入的 BrowserManager
- `_running_task_id` — 当前运行中的任务 ID（并发控制）

**登录超时实现：** `login_start()` 中通过 `call_later(LOGIN_TIMEOUT, callback)` 注册超时自动关闭 headed 浏览器。

**会话过期检测：** `_create_download_task()` 在启动下载协程前验证 storageState 有效性。若失效，标记任务 FAILED 并设置 error="登录会话已过期，请重新登录后再下载"。

**并发控制：** 下载端点创建任务前检查 `_running_task_id`，若已有运行中任务则返回 409。

**退出登录清理：** `logout()` 取消所有运行中任务，关闭 headed 浏览器，清除会话文件和模块级引用。

**后台下载协程：**
- `_run_post(task_id, url, fmt, dl_dir)` — 单篇下载
- `_run_blog(task_id, user_id, fmt, dl_dir)` — 批量下载作者文章
- `_run_likes(task_id, fmt, dl_dir)` — 批量下载喜欢文章
- `_save_results(post_dicts, sub_dir, fmt, dl_dir)` — 通用保存入口

### 4.10 `web/templates/index.html` — 前端

Alpine.js 单页应用，2 秒轮询 `GET /api/tasks` 更新进度。

**主要区域：**
- 登录区域：状态显示（@username + 首字母头像）、登录/检查/清除按钮
- 下载设置：格式单选（Markdown / TXT / PDF）、保存目录输入 + 文件夹浏览器
- 下载操作：单篇 URL 输入、作者 ID 输入、喜欢下载按钮
- 任务列表：进度条、状态标签、错误信息、取消按钮
- 已完成列表：历史任务状态 + 时间戳

**设置持久化：** 输出格式和下载目录保存至 `localStorage`，页面刷新后自动恢复。

## 五、API 接口

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/` | — | HTML | 前端页面 |
| GET | `/api/login/status` | — | `{logged_in, user_name}` | 登录状态 |
| POST | `/api/login/start` | — | `{ok, message}` | 启动浏览器登录 |
| POST | `/api/login/check` | — | `{ok, logged_in, user_name}` | 检查登录结果 |
| DELETE | `/api/login` | — | `{ok}` | 清除登录（含取消运行中任务） |
| POST | `/api/download/post` | `{url, format?, download_dir?}` | `{task_id}` 或 `{ok:false, error}` | 下载单篇 |
| POST | `/api/download/blog` | `{user_id, format?, download_dir?}` | `{task_id}` 或 `{ok:false, error}` | 下载作者全部 |
| POST | `/api/download/likes` | `{format?, download_dir?}` | `{task_id}` 或 `{ok:false, error}` | 下载喜欢（需登录） |
| GET | `/api/tasks` | — | `[{task}]` | 任务列表 |
| GET | `/api/tasks/<id>` | — | `{task}` 或 404 | 单个任务 |
| POST | `/api/tasks/<id>/cancel` | — | `{ok}` | 取消任务 |

`{task}` 格式：
```json
{
  "task_id": "abc12345",
  "type": "blog",
  "status": "running",
  "progress": 0.35,
  "current": 7,
  "total": 20,
  "message": "文章标题",
  "error": "",
  "created_at": "2026-05-25T12:00:00+00:00"
}
```

## 六、数据流

```
用户点击「下载全部」
  → POST /api/download/blog {user_id: "12345678", format: "md", download_dir: ""}
  → routes.download_blog()
      1. 检查 _running_task_id（并发控制）
      2. 创建 task，submit_async(_run_blog)
      3. 设置 _running_task_id
  → _run_blog 协程在浏览器线程执行：
      1. 验证 storageState 有效性（会话过期检测）
      2. 创建 BrowserContext（加载 storageState）
      3. 解析 user_id → 博客域名（访问 lofter.com/blog/{id}）
      4. DWR API 收集文章链接（时间戳游标分页）
      5. 逐篇下载（每篇间隔 REQUEST_INTERVAL 秒）：
         goto(文章URL) → 等待 SPA 渲染 → parser.extract_post() → saver.save_dict(fmt)
      6. 每篇更新 task_manager（current++, message=标题）
      7. 响应 CancelledError → 清理资源 → 更新状态
  → 前端每 2s 轮询 GET /api/tasks 更新进度条
```

## 七、配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LOFTER_DOWNLOAD_DIR` | `~/lofter_downloads` | 下载根目录 |
| `LOFTER_SESSION_PATH` | `~/.lofter_downloader/lofter_auth.json` | 登录会话文件 |
| `LOFTER_REQUEST_INTERVAL` | `2.0` | 请求间隔（秒） |
| `LOFTER_MAX_RETRIES` | `2` | 最大重试次数 |
| `LOFTER_REQUEST_TIMEOUT` | `30` | 请求超时（秒） |
| `LOFTER_LOG_LEVEL` | `INFO` | 日志级别 |
| `LOFTER_HOST` | `127.0.0.1` | 监听地址 |
| `LOFTER_PORT` | `8080` | 监听端口 |

**配置容错：** 数值类型配置使用 `try/except ValueError` 解析，非法值时回退默认值并记录警告。

## 八、依赖

```
flask>=3.0           # Web 框架
playwright>=1.45     # 浏览器自动化
beautifulsoup4>=4.12 # HTML 解析
lxml>=5.0            # BS4 后端
httpx>=0.27          # 图片下载
aiofiles>=23.2       # 异步文件写入
markdownify>=0.12    # HTML → Markdown 转换
fpdf2>=2.8           # PDF 生成（纯 Python，CJK 支持）
```

开发依赖：`pytest>=8.0`, `pytest-cov>=5.0`, `ruff>=0.8`

## 九、线框截图页

（预留位置：后续可补充 UI 线框图或实际运行截图）
