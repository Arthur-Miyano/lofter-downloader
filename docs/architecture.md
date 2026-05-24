# 架构设计

## 系统架构图

```mermaid
flowchart TB
    User["用户浏览器<br/>(Tailwind CSS + Alpine.js)"]
    API["FastAPI Web 服务<br/>(REST + WebSocket)"]
    TM["任务调度器<br/>(AsyncIO Background Tasks)"]
    Spider["爬虫引擎<br/>(httpx + BeautifulSoup4 + lxml)"]
    Store["存储层<br/>(aiofiles + SQLite)"]

    User <-->|HTTP / WebSocket| API
    API -->|创建/追踪任务| TM
    TM -->|调用爬虫| Spider
    Spider -->|文章 + 图片| Store
    TM -->|进度推送| API
    API -->|实时更新| User
```

## 模块依赖关系

```mermaid
flowchart LR
    subgraph Web["Web 层"]
        Server["server.py<br/>FastAPI 路由"]
        Template["templates/index.html<br/>前端 UI"]
    end

    subgraph Core["爬虫核心"]
        Spider["spider.py<br/>爬虫基类"]
        Post["post.py<br/>单篇文章"]
        Blog["blog.py<br/>作者全部"]
        Collection["collection.py<br/>合集"]
        Favorites["favorites.py<br/>收藏"]
        Auth["auth.py<br/>登录"]
        Resolver["resolver.py<br/>ID解析"]
        TMgr["task_manager.py<br/>任务调度"]
    end

    subgraph Storage["存储"]
        Saver["saver.py<br/>Markdown + 图片"]
    end

    subgraph Models["数据"]
        Schemas["schemas.py<br/>Pydantic 模型"]
        DB["database.py<br/>SQLAlchemy ORM"]
    end

    subgraph Utils["工具"]
        Config["config.py<br/>配置管理"]
        Logger["logger.py<br/>日志"]
        Exceptions["exceptions.py<br/>异常"]
    end

    Server --> Core
    Server --> Models
    Core --> Utils
    Core --> Storage
    Storage --> Utils
    Blog --> Post
    Blog --> Resolver
    Collection --> Post
    Favorites --> Post
    Spider --> httpx
    Spider --> BeautifulSoup
```

## 数据流（作者全部文章下载）

```mermaid
sequenceDiagram
    participant User as 用户浏览器
    participant API as FastAPI
    participant TM as 任务调度
    participant Spider as 爬虫引擎
    participant Store as 文件系统

    User->>API: POST /api/download/blog {user_id: 67890}
    API->>TM: 创建后台任务
    API-->>User: {task_id: "uuid", status: "pending"}

    TM->>Spider: resolve_domain(67890)
    Spider-->>TM: "backpacker_wang"

    loop page=1..N
        TM->>Spider: get_post_links("backpacker_wang", page)
        Spider-->>TM: [url1, url2, url3]

        loop each url
            TM->>Spider: download_post(url)
            Spider-->>TM: Post(title, content, images)
            TM->>Store: saver.save(post)
            TM-->>API: WebSocket 推送进度
        end
    end

    TM-->>API: 任务完成
    API-->>User: 结果统计
```

## 文件存储结构

```
downloads/
├── {author_name}/
│   ├── {publish_date}_{post_title}/
│   │   ├── index.md
│   │   └── images/
│   │       ├── 001.jpg
│   │       ├── 002.png
│   │       └── ...
│   └── ...
├── 收藏文章/
│   └── ...
└── 单篇下载/
    └── ...
```

## 技术栈

| 层次 | 技术 | 用途 |
|---|---|---|
| 前端 | Tailwind CSS + Alpine.js | 美观零构建 UI |
| 后端 | FastAPI + Uvicorn | 异步 Web 服务 |
| 爬虫 | httpx + BeautifulSoup4 + lxml | HTML 解析 |
| 存储 | aiofiles + SQLite (SQLAlchemy) | 文件 + 数据库 |
| 格式 | Markdown (markdownify) | 文章输出 |
| 测试 | pytest + pytest-cov + pytest-mock | 质量保障 |
| 文档 | MkDocs + mkdocstrings | 自动生成 API 文档 |
| CI/CD | GitHub Actions | 自动化质量检查与发布 |
