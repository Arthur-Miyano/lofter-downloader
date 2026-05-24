# LOFTER 下载器 — 技术设计文档

> 版本：1.0.1 | 日期：2026-05-24

## 一、技术栈

| 层次 | 选型 | 说明 |
|------|------|------|
| Web 框架 | Flask 3.x | 同步框架，Werkzeug 多线程开发服务器 |
| 前端 | Alpine.js + Tailwind CSS | CDN 引入，零构建 |
| 浏览器自动化 | Playwright (Python async API) | 加载 SPA 页面、执行 JS 提取 |
| HTML 解析 | BeautifulSoup4 + lxml | 从 `page.content()` 解析 DOM |
| 图片下载 | httpx (async) | 携带 session cookie + Referer 头 |
| 登录持久化 | storageState JSON | Playwright 原生格式 |
| 任务追踪 | 内存 dict + threading.Lock | 单用户，无需持久化 |

## 二、项目结构

```
lofter-downloader/
├── app.py                    # Flask 工厂函数 + CLI 入口
├── config.py                 # 配置（手动 .env 解析，含容错）
├── requirements.txt          # 7 个运行时依赖 + 3 个开发依赖
│
├── downloader/
│   ├── browser.py            # BrowserManager（后台线程 + 事件循环）
│   ├── auth.py               # 登录函数（无类）
│   ├── pipeline.py           # DownloadPipeline（统一下载流程）
│   ├── parser.py             # 文章解析（2 JS 策略 + 1 HTML 降级策略含 3 子策略）
│   ├── models.py             # Post / Task 数据类 + TaskManager（线程安全）
│   ├── saver.py              # Markdown + 图片存储
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
│    .result(timeout=15) ◄────┼─────│  执行协程，返回结果           │
│                             │     │                              │
│  browser.submit_async(coro)─┼────►│  执行协程（不等待结果）       │
│    （保存 future 用于取消）  │     │  更新 task_manager 内存状态   │
└─────────────────────────────┘     └──────────────────────────────┘
```

- `submit(coro)` → 阻塞等待结果，用于登录检查等短操作
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
├── launch_headed()      → 临时启动可见浏览器（登录用，独立 Chromium 进程）
└── stop()               → 关闭浏览器 + 停止事件循环（atexit 注册）
```

**设计要点：**
- **双进程架构：** 单一 headless Chromium 用于下载（`self._browser`，持久化）。登录时通过 `launch_headed()` 启动第二个独立的 headed Chromium 进程（临时，登录成功后关闭）。这不是"模式切换"——Playwright 不支持运行时改变 headless 状态，必须启动独立进程
- 下载使用 headless 模式（`self._browser`），通过 BrowserContext 隔离不同下载操作
- 登录 headed 浏览器需 5 分钟超时机制：启动时注册 `asyncio.get_event_loop().call_later(300, close_headed)`，超时自动关闭并清理资源
- 线程安全：`asyncio.run_coroutine_threadsafe` 是 Python 官方提供的线程间协程提交 API。`submit_async()` 返回的 Future 必须被调用方保存，用于实现真正的任务取消

### 4.3 `downloader/auth.py` — 登录

纯函数，无类：

```
load_storage_state(path) → dict | None    # 读取 session 文件
start_login(playwright) → (browser, ctx, page)  # 启动 headed 浏览器登录
check_login(page) → (bool, username, error_type?)  # 检查登录 + 提取用户名
save_session(context, path)               # 持久化 storageState
clear_session(path)                       # 删除 session 文件
```

**check_login 返回值扩展：** 考虑返回三元组 `(bool, username, error_type)`，其中 `error_type` 区分：`None`（成功）、`"not_logged_in"`（未完成登录）、`"network_error"`（网络故障）、`"timeout"`（超时），便于前端展示分类错误信息。

### 4.4 `downloader/pipeline.py` — 统一下载管道

当前实现 API（与早期设计不同，无统一 `run()` 方法）：

```
DownloadPipeline
├── run_post(url) → list[dict]           # 单篇下载
├── collect_blog_links(user_id) → list[str]    # 收集作者全部文章链接
├── collect_collection_links(url) → list[str]  # 收集合集全部文章链接
├── collect_favorites_links() → list[str]      # 收集收藏全部文章链接
│
├── _paginate(base_url) → list[str]      # 通用分页收集
├── _resolve_domain(user_id) → str        # ID → 博客域名
└── _resolve_favorites_url() → str        # 探测收藏页 URL
```

**分页逻辑：** 从 page=1 开始递增，每页提取 `a[href*='/post/']` 链接，去重后追加。当某页无新链接时停止。

**分页参数适配（待验证）：** 当前 `_paginated_url()` 统一使用 `page=N`。不同 LOFTER 页面类型可能使用不同参数名或分页方式（cursor-based、offset-based），需在集成测试中对每种下载类型验证。

**页面加载策略：**
1. `page.goto(url, wait_until="load")` — 等待所有资源加载
2. `page.wait_for_selector("a[href*='/post/'], .post_content, article", timeout=15000)` — 等待 SPA 渲染内容（仅分页遍历使用）
3. `asyncio.sleep(2)` — React hydration 缓冲（仅分页遍历使用）

**注意：** `run_post()` 当前仅执行步骤 1（无 selector 等待和无 hydration 缓冲），在 SPA 渲染较慢时可能导致 Strategy 3（BS4 HTML 降级）拿到 hydration 前的空白 DOM。JS 提取策略（strategy 1/2）不受影响（数据嵌入在 HTML 源码中）。需评估是否为 `run_post()` 添加与分页遍历相同的等待逻辑。

**错误韧性：** 批量下载中单篇文章失败 → 记录日志 → 跳过 → 继续下一篇。只有零文章或浏览器崩溃才标记任务失败。

**请求间隔：**
- 分页遍历：每页之间有 `asyncio.sleep(REQUEST_INTERVAL)` — 已实现
- 页面导航重试：`asyncio.sleep(REQUEST_INTERVAL * attempt)` 退避 — 已实现
- 批量下载逐篇之间：**当前缺失** `asyncio.sleep(REQUEST_INTERVAL)`，需在 `_run_blog` / `_run_collection` / `_run_favorites` 的循环中添加

### 4.5 `downloader/parser.py` — 文章解析

```
extract_post(page, url) → dict | None
├── Strategy 1: page.evaluate("window.__INITIAL_STATE__")
│   └── _parse_initial_state(state, url) → dict | None
├── Strategy 2: page.evaluate("document.getElementById('__NEXT_DATA__')?.textContent")
│   └── _parse_next_data(data, url) → dict | None
└── Strategy 3: _try_html(page.content(), url)
    ├── 3a: _try_ldjson(soup, url) — JSON-LD 结构化数据
    ├── 3b: _try_embedded_json(html, url) — 正则匹配 window.__INITIAL_STATE__ 和 __NEXT_DATA__
    └── 3c: CSS 选择器降级 — .post_title, .author, .date 等
```

三种顶层策略按优先级依次降级。Strategy 3 内部含 3 个子策略。

提取字段：`title`, `author`, `publish_date`, `content_html`, `content_markdown`, `image_urls`

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
    _lock: threading.Lock              # 保护 _tasks 的并发访问
    create(), get(), list_all(), update()
    cleanup(max_items=100)             # 淘汰最旧的非运行中任务
    cancel(task_id) → bool             # 设置 CANCELED 状态 + 触发 CancelledError
```

**线程安全要求：** `create()`、`update()`、`get()`、`list_all()` 均需持有 `_lock`。`update()` 的 get + setattr 循环必须在锁内完成。

**任务取消机制（需重构）：**
1. `submit_async()` 返回的 Future 对象保存在 Task 上（`task._future`）
2. `cancel()` 同时执行：(a) `task_manager.update(status=CANCELED)` + (b) `task._future.cancel()`
3. 后台协程在 `await` 点收到 `CancelledError` 后执行清理（关闭 BrowserContext、删除部分下载文件）
4. 协程内部的取消标志检查作为补充（处理 `CancelledError` 被抑制的边缘情况）

### 4.7 `downloader/saver.py` — 存储

```
PostSaver(base_dir, cookies=None)
├── save_dict(post_dict, sub_dir) → Path
│   ├── _make_post_dir(sub_dir, title) → Path
│   ├── _save_markdown(path, post)       # 使用 MARKDOWN_TEMPLATE
│   └── _save_images(images_dir, urls)   # 含重试 + 指数退避
├── _infer_extension(url, content_type) → str
└── close()                              # 关闭 httpx 客户端
```

**Cookie 转换：** Playwright storageState 的 cookies 格式为 `[{name, value, domain, path, ...}]`。`PostSaver.__init__()` 提取 `{name: value}` 键值对传给 httpx。当前实现丢弃 domain/path/sameSite 属性，对于 LOFTER CDN 的图片下载场景可正常工作，但跨域图片请求可能需完整 cookie 属性。

**图片下载要求：**
- 设置 `Referer` 请求头为 LOFTER 域名（反盗链）
- 重试最多 2 次，含指数退避（1s → 2s）
- 失败时记录警告日志，包含 URL 和 HTTP 状态码，不终止文章保存

**文件名安全：** `_sanitize_filename()` 替换 `< > : " / \ | ? *` 为 `_`，去除首尾空格和点，截断到 200 字符。全特殊字符标题降级为 `untitled_{timestamp}`。

### 4.8 `downloader/exceptions.py` — 异常

```
LofterError (base)           — 已在 browser.py 中使用
├── LoginRequiredError       — 会话过期时抛出（pipeline.py）
├── ParseError               — 所有提取策略失败时抛出（parser.py）
├── NetworkError             — 页面导航/图片下载重试耗尽时抛出
└── TaskCanceledError        — 任务被取消时抛出（用于协程内部传播）
```

**当前状态：** 仅 `LofterError` 在 browser.py 中被实际使用。其余 4 个异常已定义但未被 raise。需在 parser.py（ParseError）、pipeline.py（LoginRequiredError、NetworkError）、routes.py（TaskCanceledError）中接入。

### 4.9 `web/routes.py` — API 路由

Flask Blueprint。模块级变量维护登录状态（单用户模式）：
- `_login_browser` / `_login_context` / `_login_page` — 登录中的 headed 浏览器资源
- `_user_name` — 已登录用户名
- `task_manager` — 全局 TaskManager 实例（线程安全）
- `browser` — 由 `app.py` 注入的 BrowserManager
- `_running_task_id` — 当前运行中的任务 ID（并发控制）

**登录超时实现：** `login_start()` 中通过 `asyncio.get_event_loop().call_later(300, _close_login_browser)` 注册 5 分钟后自动关闭 headed 浏览器的回调。

**会话过期检测：** `_save_results()` 在保存文章前验证 storageState 有效性（访问 LOFTER 首页检查是否被重定向到 `/front/login`）。若失效，标记任务 FAILED 并设置 error="登录会话已过期，请重新登录"。

**并发控制：** 下载端点创建任务前检查 `_running_task_id`，若已有运行中任务则返回 `{ok: false, error: "已有下载任务在进行中"}`。

**退出登录清理：** `logout()` 遍历所有任务，取消运行中的任务（调用 `task_manager.cancel()`），关闭 headed 浏览器（如存在），清除 `_login_*` 引用。

### 4.10 `web/templates/index.html` — 前端

Alpine.js 单页应用，2 秒轮询 `GET /api/tasks` 更新进度。

**已实现特性：** 暗黑模式（系统跟随 + 手动切换）、登录状态显示（@username + 头像首字母）、任务进度条、错误信息展示、已完成任务独立区域。

## 五、API 接口

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/` | — | HTML | 前端页面 |
| GET | `/api/login/status` | — | `{logged_in, user_name}` | 登录状态 |
| POST | `/api/login/start` | — | `{ok, status, message}` | 启动浏览器登录 |
| POST | `/api/login/check` | — | `{ok, logged_in, user_name}` | 检查登录结果 |
| DELETE | `/api/login` | — | `{ok}` | 清除登录（含取消运行中任务） |
| POST | `/api/download/post` | `{url}` | `{task_id}` 或 `{ok:false, error}` | 下载单篇 |
| POST | `/api/download/blog` | `{user_id}` | `{task_id}` 或 `{ok:false, error}` | 下载作者全部 |
| POST | `/api/download/collection` | `{url}` | `{task_id}` 或 `{ok:false, error}` | 下载合集 |
| POST | `/api/download/favorites` | — | `{task_id}` 或 `{ok:false, error}` | 下载收藏 |
| GET | `/api/tasks` | — | `[{task}]` | 任务列表 |
| GET | `/api/tasks/<id>` | — | `{task}` 或 404 | 单个任务 |
| POST | `/api/tasks/<id>/cancel` | — | `{ok}` | 取消任务（真正中断协程） |

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
  "created_at": "2026-05-24T12:00:00+00:00"
}
```

## 六、数据流

```
用户点击「下载全部」
  → POST /api/download/blog {user_id: "12345678"}
  → routes.download_blog()
      1. 检查 _running_task_id（并发控制）
      2. 创建 task，submit_async(_run_blog)，保存 future 到 task._future
      3. 设置 _running_task_id
  → _run_blog 协程在浏览器线程执行：
      1. 验证 storageState 有效性（会话过期检测）
      2. 创建 BrowserContext（加载 storageState）
      3. 解析 user_id → 博客域名（访问 lofter.com/blog/{id}）
      4. 分页收集文章链接（每页间隔 REQUEST_INTERVAL 秒）
      5. 逐篇下载（每篇间隔 REQUEST_INTERVAL 秒）：
         goto(文章URL) → 等待 SPA 渲染 → parser.extract_post() → saver.save_dict()
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
markdownify>=0.12    # HTML → Markdown
```

开发依赖：`pytest>=8.0`, `pytest-cov>=5.0`, `ruff>=0.8`

**版本策略：** 使用 `>=` 下限约束（非 `==` 精确锁定），在 `pyproject.toml` 中声明依赖以支持 lock 文件生成。
