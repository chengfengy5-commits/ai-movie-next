"""Private SQLAlchemy Core declaration for the complete legacy SMTP row."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table

metadata = MetaData()

smtp_accounts = Table(
    "smtp_accounts",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("host", String(200), nullable=False, default="smtp.qq.com"),
    Column("port", Integer, nullable=False, default=465),
    Column("user", String(200), nullable=False),
    Column("password", String(500), nullable=False),
    Column("daily_sent", Integer, nullable=False, default=0),
    Column("is_active", Integer, nullable=False, default=1),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow),
)
