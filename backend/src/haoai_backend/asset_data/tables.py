"""Minimal SQLAlchemy Core projections used by the asset-data adapter."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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
    Column("username", String(100), nullable=False),
)

teams = Table(
    "teams",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
)

team_members = Table(
    "team_members",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), ForeignKey("teams.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("permissions", Text, nullable=True),
    UniqueConstraint("team_id", "user_id", name="uq_asset_data_team_member"),
)

series = Table(
    "series",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("team_id", String(36), nullable=True),
    Column("claimed_by", String(36), nullable=True),
)

chapters = Table(
    "chapters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("content", Text, nullable=True),
    Column("order", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_asset_data_chapters_series_id", "series_id"),
)

characters = Table(
    "characters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("name", String(255), nullable=False),
    Column("gender", String(20), nullable=True),
    Column("age", String(100), nullable=True),
    Column("role", String(50), nullable=True),
    Column("appearance", Text, nullable=True),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("audio_url", String(500), nullable=True),
    Column("voice_ref", Text, nullable=True),
    Column("aliases", Text, nullable=True),
    Column("canonical_key", String(255), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_asset_data_characters_series_id", "series_id"),
    Index("ix_asset_data_characters_canonical_key", "canonical_key"),
)

scenes = Table(
    "scenes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("aliases", Text, nullable=True),
    Column("canonical_key", String(255), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_asset_data_scenes_series_id", "series_id"),
    Index("ix_asset_data_scenes_canonical_key", "canonical_key"),
)

props = Table(
    "props",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("aliases", Text, nullable=True),
    Column("canonical_key", String(255), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_asset_data_props_series_id", "series_id"),
    Index("ix_asset_data_props_canonical_key", "canonical_key"),
)

storyboard_assets = Table(
    "storyboard_assets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("frame_index", Integer, nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_asset_data_storyboard_series_id", "series_id"),
    Index("ix_asset_data_storyboard_chapter_id", "chapter_id"),
)

chapter_locks = Table(
    "chapter_locks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("username", String(100), nullable=False),
    Column("acquired_at", DateTime, nullable=False),
    Column("last_active_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    UniqueConstraint("chapter_id", name="uq_asset_data_chapter_lock"),
)

system_configs = Table(
    "system_configs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_lock_idle_minutes", Integer, nullable=False, default=15),
)

ASSET_TABLES = {
    "character": characters,
    "scene": scenes,
    "prop": props,
}
