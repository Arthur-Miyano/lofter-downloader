# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

LOFTER 下载器 — a Python web app that downloads articles from NetEase LOFTER (a Chinese blogging platform). Provides a browser-based UI and downloads articles as Markdown with inline images. Uses **Playwright Chromium** for all page access because LOFTER enforces a full-site login wall (all `*.lofter.com` HTML pages redirect to `/front/login` when unauthenticated).

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run the app (starts on http://127.0.0.1:8080)
python app.py

# Run tests
pytest

# Run a subset of tests
pytest tests/test_parser.py -k test_parse_basic_post

# Lint
ruff check .

# Format
ruff format .
```

## Architecture

### Tech stack

- **Flask** (threaded Werkzeug dev server) — web framework
- **Alpine.js + Tailwind CSS** (CDN, no build step) — frontend SPA
- **Playwright** (Python async API) — browser automation for page loading
- **BeautifulSoup4 + lxml** — HTML parsing from `page.content()`
- **httpx** (with session cookies) — image downloads
- **storageState** (Playwright native format) — login session persistence

### Core design: Flask sync + Playwright async

Flask is synchronous; Playwright is async. The bridge: a **background daemon thread** running its own asyncio event loop. Operations are submitted via `asyncio.run_coroutine_threadsafe`:

- **`BrowserManager.submit(coro)`** — blocks with `.result(timeout=60)`, for quick ops (login check)
- **`BrowserManager.submit_async(coro)`** — fire-and-forget, for long downloads

```
Flask request thread (ThreadPool)      Browser Event Loop Thread (daemon)
        │                                        │
        ├── submit(coro) ──────────────────────► │  login check
        │   .result(timeout=60) ◄─────────────── │
        │                                        │
        ├── submit_async(coro) ─────────────────► │  downloads
        │   (returns immediately)                │  updates TaskManager in-memory
```

### Project structure (flat, ~12 source files)

```
lofter-downloader/
├── app.py                    # Flask factory + CLI entry
├── config.py                 # Plain config, manual .env loading
├── requirements.txt          # 7 runtime deps
│
├── downloader/
│   ├── browser.py            # BrowserManager (bg thread + event loop)
│   ├── auth.py               # Login functions (no class)
│   ├── pipeline.py           # DownloadPipeline (unified, replaces 4 downloaders)
│   ├── parser.py             # Post extraction: JS state → __NEXT_DATA__ → BS4
│   ├── models.py             # Post/Task dataclasses + TaskManager
│   ├── saver.py              # Markdown + image download (with cookies)
│   └── exceptions.py         # 5 exception classes
│
├── web/
│   ├── routes.py             # Flask Blueprint (all API routes)
│   └── templates/
│       └── index.html        # Alpine.js + Tailwind SPA
│
└── tests/
    ├── conftest.py
    ├── test_parser.py
    └── test_routes.py
```

### Data flow

1. User clicks "下载全部" → `POST /api/download/blog {user_id}`
2. Route creates `Task` in `TaskManager` (in-memory dict), calls `browser.submit_async(_run_blog)`
3. `_run_blog` coroutine runs in browser thread:
   - Creates BrowserContext with `storage_state` from session file
   - `DownloadPipeline.collect_blog_links()` → resolves domain → paginates → collects post URLs
   - For each URL: `DownloadPipeline.run_post()` → `parser.extract_post()` (3-strategy)
   - `PostSaver.save_dict()` → writes Markdown + downloads images with cookies
   - Updates `task_manager` (current++, message=title) after each post
4. Frontend polls `GET /api/tasks` every 2s for progress updates

### DownloadPipeline (unified, replaces 4 downloader classes)

Single class handles all 4 download types:
- `run_post(url)` → single post download
- `collect_blog_links(user_id)` → resolve domain → paginate blog
- `collect_collection_links(url)` → paginate collection
- `collect_favorites_links()` → resolve fav URL → paginate

Shared `_paginate(base_url)` method for all list types.

### Post extraction (3 strategies, in priority order)

In `downloader/parser.py`:
1. `page.evaluate("window.__INITIAL_STATE__")` — direct JS state read (fastest)
2. `page.evaluate("document.getElementById('__NEXT_DATA__')?.textContent")` — Next.js SSR data
3. BS4 on `page.content()` — CSS selectors as last resort

**Page load strategy:** `wait_until="load"` + `page.wait_for_selector()` + 2s sleep for React hydration. Not `"domcontentloaded"` (too early for SPA) and not `"networkidle"` (may never fire).

### Auth flow

Module-level state in `web/routes.py` (single-user tool):
1. `POST /api/login/start` → temp headed Chromium → LOFTER login page
2. User manually logs in (with captcha) in the headed browser
3. `POST /api/login/check` → navigate to LOFTER home → check URL for `/front/login` → extract username from `window.__INITIAL_STATE__` → save `storage_state()` to JSON → close headed browser
4. All downloads: `new_context(storage_state=session_path)` loads the saved session

### API routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Serve index.html |
| GET | `/api/login/status` | `{logged_in, user_name}` |
| POST | `/api/login/start` | Launch headed browser |
| POST | `/api/login/check` | Verify login, save session |
| DELETE | `/api/login` | Clear session |
| POST | `/api/download/post` | `{url}` → `{task_id}` |
| POST | `/api/download/blog` | `{user_id}` → `{task_id}` |
| POST | `/api/download/collection` | `{url}` → `{task_id}` |
| POST | `/api/download/favorites` | (auth required) → `{task_id}` |
| GET | `/api/tasks` | List all tasks |
| GET | `/api/tasks/<id>` | Single task |
| POST | `/api/tasks/<id>/cancel` | Cancel task |

No WebSocket, no `/api/stats`, no database. Tasks are in-memory only.

### Configuration (config.py, ~40 lines)

Manual `.env` parsing (no pydantic). 8 settings: `DOWNLOAD_DIR`, `SESSION_PATH`, `REQUEST_INTERVAL`, `MAX_RETRIES`, `REQUEST_TIMEOUT`, `LOG_LEVEL`, `HOST`, `PORT`. Env var prefix: `LOFTER_`.

### Key design decisions

- **No database** — Task tracking is in-memory (single-user tool). No SQLAlchemy.
- **No Pydantic** — Plain dataclasses for Post/Task. Manual JSON deserialization in routes.
- **No WebSocket** — Frontend polls `GET /api/tasks` every 2s. Simpler, more reliable.
- **Per-post error resilience** — Blog/collection/favorites skip failed posts, continue with others. Only fatal errors fail the whole task.
- **Cookie-bearing image downloads** — `PostSaver` extracts cookies from session file, passes to httpx for downloading images behind the login wall.
- **`type="text"` on author ID input** — NOT `type="number"` (blocks paste and text input on many systems).
