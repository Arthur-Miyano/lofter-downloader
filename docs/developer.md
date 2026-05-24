# 开发者指南

## 环境搭建

```bash
git clone https://github.com/user/lofter-downloader.git
cd lofter-downloader
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e ".[dev,docs]"
```

## 代码规范

项目使用以下工具保证代码质量：

```bash
# 代码格式化
black src/ tests/

# 代码检查
ruff check src/ tests/

# 类型检查
mypy src/

# 全部
nox -s lint
```

## 运行测试

```bash
# 运行全部测试
pytest

# 带覆盖率
pytest --cov

# 多版本测试
nox -s tests
```

## 构建文档

```bash
mkdocs serve  # 本地预览
mkdocs build  # 构建静态文件
```

## 项目结构

```
src/lofter_downloader/
├── config.py          # 配置管理
├── core/              # 爬虫核心
│   ├── spider.py      # 爬虫基类
│   ├── post.py        # 单篇文章
│   ├── blog.py        # 作者全部文章
│   ├── collection.py  # 合集下载
│   ├── favorites.py   # 收藏下载
│   ├── auth.py        # 登录模块
│   ├── resolver.py    # ID 解析
│   └── task_manager.py # 任务调度
├── storage/           # 存储模块
│   └── saver.py       # Markdown + 图片
├── models/            # 数据模型
├── web/               # Web 服务
│   ├── server.py      # FastAPI
│   ├── routes/        # API 路由
│   └── templates/     # 前端模板
└── utils/             # 工具模块
    ├── logger.py      # 日志
    └── exceptions.py  # 异常
```
