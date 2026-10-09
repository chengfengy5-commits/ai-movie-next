"""Minimal SQLAlchemy Core projections of legacy rough-cut tables.

These declarations describe only columns used by this module. They are not a
production schema or migration and must not be used with create_all outside tests.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
)

chapters = Table(
    "chapters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("title", String(255), nullable=False),
    Column("content", Text, nullable=True),
)

storyboard_assets = Table(
    "storyboard_assets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("chapter_id", String(36), nullable=False),
    Column("frame_index", Integer, nullable=False),
    Column("image_url", String(500), nullable=True),
)

rough_cut_drafts = Table(
    "rough_cut_drafts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("frames", JSON, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("chapter_id", "user_id", name="uq_rough_cut_drafts_chapter_user"),
    CheckConstraint("revision > 0", name="ck_rough_cut_drafts_revision_positive"),
    Index("ix_rough_cut_drafts_chapter_id", "chapter_id"),
    Index("ix_rough_cut_drafts_user_id", "user_id"),
)
