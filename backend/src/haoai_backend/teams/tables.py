"""Minimal SQLAlchemy Core projections for the shared teams adapters.

Importing this module declares metadata only. It does not create an Engine,
inspect a database, or initialize a schema.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
    Column("username", String(100), nullable=False, unique=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("is_superuser", Boolean, nullable=False, default=False),
    Column("membership_type", String(20), nullable=False, default="free"),
    Column("membership_expires_at", DateTime, nullable=True),
    Column("avatar_url", String(500), nullable=True),
)

teams = Table(
    "teams",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Index("ix_teams_owner_id", "owner_id"),
)

team_members = Table(
    "team_members",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), ForeignKey("teams.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("role", String(20), nullable=False, default="member"),
    Column("permissions", Text, nullable=True),
    Column("joined_at", DateTime, nullable=False),
    UniqueConstraint("team_id", "user_id", name="uq_team_member"),
    Index("ix_team_members_team_id", "team_id"),
    Index("ix_team_members_user_id", "user_id"),
)

team_invites = Table(
    "team_invites",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), ForeignKey("teams.id"), nullable=False),
    Column("code", String(20), nullable=False),
    Column("created_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("used_by", String(36), nullable=True),
    Column("used_at", DateTime, nullable=True),
    Column("status", String(20), nullable=False, default="active"),
    Index("ix_team_invites_team_id", "team_id"),
    Index("ix_team_invites_code", "code"),
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
    UniqueConstraint("chapter_id", name="uq_chapter_lock_chapter_id"),
)

system_configs = Table(
    "system_configs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("team_created_limit", Integer, nullable=False, default=3),
    Column("team_joined_limit", Integer, nullable=False, default=3),
    Column("team_member_limit", Integer, nullable=False, default=20),
    Column("invite_code_ttl_hours", Integer, nullable=False, default=24),
    Column("chapter_lock_idle_minutes", Integer, nullable=False, default=15),
)

series = Table(
    "series",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("style_prompt_id", String(36), nullable=True),
    Column("team_id", String(36), ForeignKey("teams.id"), nullable=True),
    Column("claimed_by", String(36), nullable=True),
    Column("claimed_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_series_team_id", "team_id"),
    Index("ix_series_user_id", "user_id"),
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
    Index("ix_chapters_series_id", "series_id"),
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
    Index("ix_characters_series_id", "series_id"),
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
    Index("ix_scenes_series_id", "series_id"),
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
    Index("ix_props_series_id", "series_id"),
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
    Index("ix_storyboard_assets_series_id", "series_id"),
    Index("ix_storyboard_assets_chapter_id", "chapter_id"),
)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("frame_index", Integer, nullable=True),
    Column("asset_type", String(20), nullable=True),
    Column("asset_id", String(36), nullable=True),
    Column("chat_mode", String(20), nullable=False, default="chat"),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("model_name", String(255), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Index("ix_chat_messages_chapter_id", "chapter_id"),
)

ai_tasks = Table(
    "ai_tasks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("type", String(20), nullable=False),
    Column("message_id", String(36), nullable=False),
    Column("status", String(20), nullable=False, default="processing"),
    Column("credit_cost", Integer, nullable=False, default=0),
    Column("result", Text, nullable=True),
    Column("request_data", Text, nullable=True),
    Column("model_name", String(255), nullable=True),
    Column("progress", Integer, nullable=False, default=0),
    Column("progress_message", Text, nullable=True),
    Column("external_task_id", String(255), nullable=True),
    Column("external_provider", String(50), nullable=True),
    Column("claimed_by", String(64), nullable=True),
    Column("lease_until", DateTime, nullable=True),
    Column("execution_generation", Integer, nullable=False, default=0, server_default="0"),
    Column("claim_token", String(64), nullable=True),
    Column("recovery_status", String(32), nullable=False, default="ready", server_default="ready"),
    Column("billing_status", String(32), nullable=False, default="unbilled", server_default="unbilled"),
    Column("user_cancelled_at", DateTime(timezone=True), nullable=True),
    Column("cancellation_reason", String(64), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    CheckConstraint(
        "billing_status IN ('legacy_unreconciled', 'unbilled', 'charged', "
        "'partially_refunded', 'refunded', 'no_charge')",
        name="ck_ai_tasks_billing_status",
    ),
    Index("ix_ai_tasks_user_id", "user_id"),
    Index("ix_ai_tasks_message_id", "message_id"),
    Index("ix_ai_tasks_status", "status"),
)

user_credits = Table(
    "user_credits",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, unique=True),
    Column("credits", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

system_models = Table(
    "system_models",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("category", String(20), nullable=False),
    Column("name", String(255), nullable=False),
    Column("provider", String(50), nullable=False),
    Column("api_key", String(500), nullable=False),
    Column("model", String(255), nullable=True),
    Column("base_url", String(500), nullable=True),
    Column("cost_per_call", Integer, nullable=False, default=0),
    Column("description", Text, nullable=True),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("params", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
