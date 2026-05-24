"""数据库模块。

使用 SQLite + SQLAlchemy 存储任务历史记录和登录会话。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from lofter_downloader.config import settings


class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类。"""


class TaskRecord(Base):
    """任务记录表。"""

    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), unique=True, nullable=False, index=True)
    type = Column(String(32), nullable=False)
    params = Column(JSON, default=dict)
    status = Column(String(16), nullable=False, default="pending")
    progress = Column(Float, default=0.0)
    result = Column(JSON, default=dict)
    error = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LoginSession(Base):
    """登录会话表。"""

    __tablename__ = "login_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cookie_encrypted = Column(Text, default="")
    is_valid = Column(Integer, default=0)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def get_engine(db_path: Path | None = None) -> object:
    """获取数据库引擎。

    Parameters
    ----------
    db_path : Path or None
        数据库文件路径，默认使用配置中的 db_path

    Returns
    -------
    Engine
        SQLAlchemy 引擎
    """
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", echo=False)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine: object) -> Session:
    """获取数据库会话。"""
    return Session(engine)
