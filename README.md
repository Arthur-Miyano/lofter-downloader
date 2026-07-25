# lofter-downloader

把自己的 LOFTER 和 AO3 文章备份到本地的小工具。

写这个是因为 LOFTER 从 2026 年开始全站强制登录，未登录访问任何页面都会被踢到 `/front/login`，普通爬虫直接报废。所以这里用 Playwright 开了个真的 Chromium 来过登录墙，登录一次后会话存在本地，之后都是自动的。AO3 那边简单一些，公开文章不需要登录，httpx 直接抓。

界面是个桌面窗口（pywebview 包了一层 WebView2），不是命令行工具，双击就能用。

## 能干什么

- LOFTER 单篇文章：粘贴链接即可
- LOFTER 作者全部文章：输入作者数字 ID，可以先列出清单勾选再下载
- LOFTER 喜欢的文章：一键全拉下来（需要登录）
- AO3：单篇、系列（Series）、作者全部作品、一次粘贴多行链接都行
- 导出格式：Markdown（带图片文件夹）、TXT、PDF（正文图片会嵌进去）、EPUB（也是嵌图的）
- AO3 额外提供官方导出（EPUB/PDF/HTML/MOBI/AZW3），排版和网站上一模一样

## 跑起来

```bash
git clone https://github.com/Arthur-Miyano/lofter-downloader.git
cd lofter-downloader
pip install -r requirements.txt
playwright install chromium
python app.py
```

Windows 用户可以跑一次 `scripts/创建桌面快捷方式.ps1`，之后双击桌面图标就行，不会有黑窗口。日志写在 `~/.lofter_downloader/app.log`。

开发或调试时用 `LOFTER_NO_GUI=1 python app.py`，就是个普通的 Flask 服务，浏览器访问 http://127.0.0.1:8080。

## 用法

第一次用先登录：点界面上的「登录 LOFTER」，会弹出一个 Chromium 窗口，在里面正常登录（扫码或账号密码，可能要过拼图验证码），**不要手动关这个窗口**，回到 App 点「检查登录」，看到用户名就可以了。会话会存下来，重启不用再来一遍。

然后选格式、选目录，粘贴链接开下。批量任务里某一篇失败了会跳过继续，不会整个挂掉；任务随时可以取消，已经下完的部分会保留。

找作者数字 ID 的办法：打开作者的 LOFTER 主页（`https://xxx.lofter.com`），再访问 `https://www.lofter.com/blog/xxx`，跳转后的 URL 里那串数字就是。

## 下载下来的东西长这样

```
lofter_downloads/
├── 作者名/
│   ├── 某篇文章/           # Markdown：一个文章一个文件夹
│   │   ├── index.md
│   │   └── images/
│   ├── 另一篇.txt          # TXT / PDF / EPUB 都是单文件
│   └── 另一篇.pdf
├── 喜欢文章/
└── AO3/
    └── 作者名/
```

文件名就是文章标题，重名了会自动加 `(2)`、`(3)`，不会出现乱码后缀。正文里 LOFTER 页面自带的导航、热度、评论区这些杂质会在下载时洗掉，不会混进文件。

## 配置

一般不用管。要改的话在项目根目录建个 `.env`：

| 变量 | 默认 | 说明 |
|------|------|------|
| `LOFTER_DOWNLOAD_DIR` | `~/lofter_downloads` | 下载目录 |
| `LOFTER_SESSION_PATH` | `~/.lofter_downloader/lofter_auth.json` | 登录会话文件 |
| `LOFTER_REQUEST_INTERVAL` | `2.0` | 请求间隔（秒）。AO3 限流比较敏感，遇到 429 就调大到 3 以上 |
| `LOFTER_MAX_RETRIES` | `2` | 失败重试次数 |
| `LOFTER_REQUEST_TIMEOUT` | `30` | 请求超时（秒） |
| `LOFTER_LOG_LEVEL` | `INFO` | 日志级别 |
| `LOFTER_HOST` / `LOFTER_PORT` | `127.0.0.1:8080` | 服务监听地址 |

## 一些已知的事

- PDF 里中文变成方块：缺中文字体。Windows 自带微软雅黑不会有这个问题；Linux 装一下 `fonts-noto-cjk`。
- 同一时间只跑一个下载任务，免得被网站风控。想下别的先等当前的跑完或取消。
- AO3 官方导出的文件是 AO3 服务器生成的，正文里的外链图片它不会打包进去，离线看会裂——要图片完整就用解析格式（Markdown 或 EPUB）。
- 文章解析失败一般是 LOFTER 又改模板了，开个 issue 把链接贴上来。

## 开发

```bash
pip install -e ".[dev]"
pytest          # 116 个测试
ruff check .
```

技术栈大概是这样：Flask 提供 API，Playwright 在后台线程跑 asyncio 事件循环处理页面加载，解析靠 BeautifulSoup + markdownify，PDF 用 fpdf2 + Pillow，EPUB 用 ebooklib。更细的看 [docs/design.md](docs/design.md)。

## 声明

仅限个人学习和备份用途。下载的内容不要二次传播，不要商用，请遵守 LOFTER 和 AO3 各自的服务条款。

[MIT](LICENSE)
