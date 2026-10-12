"""Standalone ORM projection for the existing user preference table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, MetaData, String, Table, Text
from sqlalchemy.orm import registry


metadata = MetaData()
users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
)
user_preferences = Table(
    "user_preferences",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column(
        "user_id",
        String(36),
        ForeignKey(users.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("key", String(100), nullable=False, index=True),
    Column("value", Text, nullable=False),
    Column(
        "updated_at",
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    ),
)

_mapper_registry = registry(metadata=metadata)


class UserPreference:
    """Mapped row used with a caller-owned SQLAlchemy Session."""


_mapper_registry.map_imperatively(UserPreference, user_preferences)
