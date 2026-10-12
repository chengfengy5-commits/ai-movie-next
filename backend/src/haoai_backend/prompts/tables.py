"""Private SQLAlchemy mappings for the legacy prompt tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text
from sqlalchemy.orm import registry

metadata = MetaData()
users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
)
prompt_configs = Table(
    "prompt_configs",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("user_id", String(36), ForeignKey(users.c.id), nullable=False, index=True),
    Column("category", String(50), nullable=False, index=True),
    Column("name", String(255), nullable=False, default="提示词"),
    Column("system_prompt", Text, nullable=True),
    Column("user_prompt", Text, nullable=True),
    Column("sort_order", Integer, default=0, nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False),
)
system_prompts = Table(
    "system_prompts",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("category", String(50), nullable=False, index=True),
    Column("name", String(255), nullable=False, default="系统提示词"),
    Column("system_prompt", Text, nullable=True),
    Column("user_prompt", Text, nullable=True),
    Column("sort_order", Integer, default=0, nullable=False),
    Column("is_active", Boolean, default=True, nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False),
)
user_prompt_orders = Table(
    "user_prompt_orders",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("user_id", String(36), ForeignKey(users.c.id), nullable=False, index=True),
    Column("prompt_id", String(36), nullable=False),
    Column("category", String(50), nullable=False),
    Column("sort_order", Integer, nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False),
)

_mapper_registry = registry(metadata=metadata)


class PromptConfigRow:
    """Mapped legacy personal prompt row; is_active intentionally is not mapped."""


class SystemPromptRow:
    """Mapped legacy system prompt row."""


class UserPromptOrderRow:
    """Mapped legacy per-user prompt ordering row."""


_mapper_registry.map_imperatively(PromptConfigRow, prompt_configs)
_mapper_registry.map_imperatively(SystemPromptRow, system_prompts)
_mapper_registry.map_imperatively(UserPromptOrderRow, user_prompt_orders)
