"""Minimal SQLAlchemy Core projections owned by the chat-data adapter."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

chapters = Table(
    "chapters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("frame_index", Integer, nullable=True),
    Column("asset_type", String(20), nullable=True),
    Column("asset_id", String(36), nullable=True),
    Column("chat_mode", String(20), nullable=False),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("model_name", String(255), nullable=True),
    Column("created_at", DateTime, nullable=False),
)

chapter_locks = Table(
    "chapter_locks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
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
    Column("chapter_lock_idle_minutes", Integer, nullable=True),
)

ai_tasks = Table(
    "ai_tasks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("message_id", String(36), nullable=False),
    Column("status", String(50), nullable=False),
    Column("credit_cost", Integer, nullable=False),
    Column("model_name", String(255), nullable=True),
)
