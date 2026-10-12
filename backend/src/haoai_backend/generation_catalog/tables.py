"""Inert Core projections for legacy admission tables not already shared."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text

from haoai_backend.authentication.tables import user_credits, users
from haoai_backend.series_access.tables import series, team_members

metadata = MetaData()

chapters = Table(
    "chapters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("title", String(255), nullable=False),
    Column("content", Text, nullable=True),
    Column("order", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

characters = Table(
    "characters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("updated_at", DateTime, nullable=False),
)

scenes = Table(
    "scenes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("title", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("updated_at", DateTime, nullable=False),
)

props = Table(
    "props",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("updated_at", DateTime, nullable=False),
)

storyboard_assets = Table(
    "storyboard_assets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("chapter_id", String(36), nullable=False),
    Column("frame_index", Integer, nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
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
    Column("cost_per_call", Integer, nullable=False),
    Column("description", Text, nullable=True),
    Column("params", Text, nullable=True),
    Column("is_active", Boolean, nullable=False),
    Column("sort_order", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

model_configs = Table(
    "model_configs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("category", String(20), nullable=False),
    Column("name", String(255), nullable=False),
    Column("provider", String(50), nullable=False),
    Column("api_key", String(500), nullable=False),
    Column("model", String(255), nullable=True),
    Column("base_url", String(500), nullable=True),
    Column("sort_order", Integer, nullable=False),
    Column("params", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

system_prompts = Table(
    "system_prompts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("category", String(50), nullable=False),
    Column("name", String(255), nullable=False),
    Column("system_prompt", Text, nullable=True),
    Column("user_prompt", Text, nullable=True),
    Column("is_active", Boolean, nullable=False),
    Column("sort_order", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

prompt_configs = Table(
    "prompt_configs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("category", String(50), nullable=False),
    Column("name", String(255), nullable=False),
    Column("system_prompt", Text, nullable=True),
    Column("user_prompt", Text, nullable=True),
    Column("sort_order", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

user_model_orders = Table(
    "user_model_orders",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("model_id", String(36), nullable=False),
    Column("category", String(20), nullable=False),
    Column("sort_order", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

ASSET_TABLES = {
    "character": characters,
    "scene": scenes,
    "prop": props,
    "storyboard": storyboard_assets,
}

CANVAS_ASSET_TABLES = {
    "character": characters,
    "scene": scenes,
    "prop": props,
}
