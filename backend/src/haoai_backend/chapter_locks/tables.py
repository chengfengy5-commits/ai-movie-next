"""Local SQLAlchemy projections for chapter lock persistence."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table

metadata = MetaData()

chapters = Table(
    "chapters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
)

chapter_locks = Table(
    "chapter_locks",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("chapter_id", String(36), nullable=False, unique=True, index=True),
    Column("user_id", String(36), nullable=False),
    Column("username", String(100), nullable=False),
    Column("acquired_at", DateTime, nullable=False),
    Column("last_active_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)

system_configs = Table(
    "system_configs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_lock_idle_minutes", Integer, nullable=False, default=15),
)
