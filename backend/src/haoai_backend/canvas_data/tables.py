"""Minimal SQLAlchemy Core projections for canvas persistence."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

# These small owner references let SQLAlchemy resolve the foreign keys without
# claiming that this module owns the application's complete schema.
users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(100), nullable=False),
)
series = Table(
    "series",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("team_id", String(36), nullable=True),
    Column("claimed_by", String(36), nullable=True),
)
chapters = Table(
    "chapters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
)
chapter_locks = Table(
    "chapter_locks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("last_active_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)
system_configs = Table(
    "system_configs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_lock_idle_minutes", Integer, nullable=False),
)
canvas_documents = Table(
    "canvas_documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("version", Integer, nullable=False, default=1),
    Column("document_json", Text, nullable=False),
    Column("created_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("updated_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("chapter_id", name="uq_canvas_documents_chapter_id"),
)
