"""Minimal SQLAlchemy Core projections for the notes adapter.

These declarations are query projections, not a complete production schema or
migration. Only tests and isolated labs may create these tables.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
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
    Column("content", Text, nullable=True),
)

storyboard_assets = Table(
    "storyboard_assets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), nullable=False),
    Column("image_url", String(500), nullable=True),
    Index("ix_storyboard_assets_chapter_id", "chapter_id"),
)

storyboard_media_states = Table(
    "storyboard_media_states",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
    Column("storyboard_asset_id", String(36), nullable=False),
    Column("media_revision", Integer, nullable=False, server_default=text("1")),
    Column("asset_image_digest", String(64), nullable=True),
    Column("preview_digest", String(64), nullable=True),
    Column("source_valid", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint(
        "chapter_id",
        "storyboard_asset_id",
        name="uq_storyboard_media_states_chapter_asset",
    ),
    CheckConstraint("media_revision > 0", name="ck_storyboard_media_states_revision_positive"),
    Index("ix_storyboard_media_states_chapter_id", "chapter_id"),
)

personal_production_notes = Table(
    "personal_production_notes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    Column("frame_notes", JSON, nullable=False, server_default=text("'{}'")),
    Column("resume_frame_id", String(36), nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint(
        "chapter_id",
        "user_id",
        name="uq_personal_production_notes_chapter_user",
    ),
    CheckConstraint("revision > 0", name="ck_personal_production_notes_revision_positive"),
    Index("ix_personal_production_notes_chapter_id", "chapter_id"),
    Index("ix_personal_production_notes_user_id", "user_id"),
)
