"""Minimal Core projections for the series source-data adapter."""

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text,
    UniqueConstraint,
)

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(100), nullable=False),
    Column("email", String(255), nullable=True),
    Column("is_superuser", Boolean, nullable=False, default=False),
)
teams = Table(
    "teams", metadata,
    Column("id", String(36), primary_key=True), Column("name", String(255), nullable=False),
    Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
)
team_members = Table(
    "team_members", metadata,
    Column("id", String(36), primary_key=True), Column("team_id", String(36), ForeignKey("teams.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False), Column("role", String(20), nullable=False),
    Column("permissions", Text, nullable=True),
    UniqueConstraint("team_id", "user_id", name="uq_series_data_team_member"),
)
series = Table(
    "series", metadata,
    Column("id", String(36), primary_key=True), Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("name", String(255), nullable=False), Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True), Column("style_prompt_id", String(36), nullable=True),
    Column("team_id", String(36), nullable=True), Column("claimed_by", String(36), nullable=True),
    Column("claimed_at", DateTime, nullable=True), Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
chapters = Table(
    "chapters", metadata,
    Column("id", String(36), primary_key=True), Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("title", String(255), nullable=False), Column("content", Text, nullable=True),
    Column("order", Integer, nullable=False, default=0), Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
characters = Table(
    "characters", metadata,
    Column("id", String(36), primary_key=True), Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("name", String(255), nullable=False), Column("aliases", Text, nullable=True),
    Column("canonical_key", String(255), nullable=True), Column("gender", String(20), nullable=True),
    Column("age", String(100), nullable=True), Column("role", String(50), nullable=True),
    Column("appearance", Text, nullable=True), Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True), Column("audio_url", String(500), nullable=True),
    Column("voice_ref", Text, nullable=True), Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
scenes = Table(
    "scenes", metadata,
    Column("id", String(36), primary_key=True), Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("title", String(255), nullable=False), Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True), Column("aliases", Text, nullable=True),
    Column("canonical_key", String(255), nullable=True), Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
props = Table(
    "props", metadata,
    Column("id", String(36), primary_key=True), Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("name", String(255), nullable=False), Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True), Column("aliases", Text, nullable=True),
    Column("canonical_key", String(255), nullable=True), Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
storyboard_assets = Table(
    "storyboard_assets", metadata,
    Column("id", String(36), primary_key=True), Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False), Column("frame_index", Integer, nullable=False),
    Column("name", String(255), nullable=False), Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True), Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
fused_assets = Table(
    "fused_assets", metadata,
    Column("id", String(36), primary_key=True), Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
)
asset_duplicate_exclusions = Table(
    "asset_duplicate_exclusions", metadata,
    Column("id", String(36), primary_key=True), Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
)
chat_messages = Table(
    "chat_messages", metadata,
    Column("id", String(36), primary_key=True), Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("frame_index", Integer, nullable=True), Column("asset_type", String(20), nullable=True),
    Column("asset_id", String(36), nullable=True), Column("chat_mode", String(20), nullable=False),
    Column("role", String(20), nullable=False), Column("content", Text, nullable=False),
    Column("model_name", String(255), nullable=True), Column("created_at", DateTime, nullable=False),
)
ai_tasks = Table(
    "ai_tasks", metadata,
    Column("id", String(36), primary_key=True), Column("message_id", String(36), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
)
rough_cut_drafts = Table(
    "rough_cut_drafts", metadata,
    Column("id", String(36), primary_key=True), Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
)
canvas_documents = Table(
    "canvas_documents", metadata,
    Column("id", String(36), primary_key=True), Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
)
chapter_locks = Table(
    "chapter_locks", metadata,
    Column("id", String(36), primary_key=True), Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False), Column("username", String(100), nullable=False),
    Column("acquired_at", DateTime, nullable=False), Column("last_active_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)
prompt_configs = Table(
    "prompt_configs", metadata,
    Column("id", String(36), primary_key=True), Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("name", String(255), nullable=False), Column("system_prompt", Text, nullable=True),
    Column("user_prompt", Text, nullable=True),
)
system_prompts = Table(
    "system_prompts", metadata,
    Column("id", String(36), primary_key=True), Column("name", String(255), nullable=False),
    Column("system_prompt", Text, nullable=True), Column("user_prompt", Text, nullable=True),
    Column("is_active", Boolean, nullable=False, default=True),
)
system_configs = Table(
    "system_configs", metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_lock_idle_minutes", Integer, nullable=False, default=15),
)
