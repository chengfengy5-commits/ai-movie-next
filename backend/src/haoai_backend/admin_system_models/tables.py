"""Private SQLAlchemy mapping for the fixed legacy system_models table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text
from sqlalchemy.orm import registry

metadata = MetaData()

system_models = Table(
    "system_models",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("category", String(20), nullable=False, index=True),
    Column("name", String(255), nullable=False),
    Column("provider", String(50), nullable=False),
    Column("api_key", String(500), nullable=False),
    Column("model", String(255), nullable=True),
    Column("base_url", String(500), nullable=True),
    Column("cost_per_call", Integer, default=0, nullable=False),
    Column("description", Text, nullable=True),
    Column("is_active", Boolean, default=True, nullable=False),
    Column("sort_order", Integer, default=0, nullable=False),
    Column("params", Text, nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False),
)

_mapper_registry = registry(metadata=metadata)


class SystemModelRow:
    """Mapped row corresponding only to the legacy system_models table."""


_mapper_registry.map_imperatively(SystemModelRow, system_models)
