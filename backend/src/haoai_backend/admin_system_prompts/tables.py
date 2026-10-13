"""Private SQLAlchemy mapping for the legacy system_prompts table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text
from sqlalchemy.orm import registry

metadata = MetaData()

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

_mapper_registry = registry(metadata=metadata)


class SystemPromptRow:
    """Mapped row matching the fixed legacy SystemPrompt model."""


_mapper_registry.map_imperatively(SystemPromptRow, system_prompts)
