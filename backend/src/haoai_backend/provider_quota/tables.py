"""Private SQLAlchemy Core declaration matching the complete legacy row."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text

metadata = MetaData()

provider_quota_accounts = Table(
    "provider_quota_accounts",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("name", String(100), nullable=False, default=""),
    Column("provider_type", String(50), nullable=False, default="new_api"),
    Column("base_url", String(500), nullable=False, default=""),
    Column("username", String(500), nullable=False, default=""),
    Column("password", Text, nullable=False, default=""),
    Column("is_active", Integer, nullable=False, default=1),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column(
        "updated_at",
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    ),
)
