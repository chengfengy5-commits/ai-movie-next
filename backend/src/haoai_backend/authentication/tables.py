"""Minimal SQLAlchemy Core projections for authentication-owned tables."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("username", String(100), nullable=False, unique=True, index=True),
    Column("email", String(255), nullable=False, unique=True, index=True),
    Column("hashed_password", String(255), nullable=False),
    Column("is_superuser", Boolean, nullable=False, default=False),
    Column("membership_type", String(20), nullable=False, default="free"),
    Column("membership_expires_at", DateTime, nullable=True),
    Column("avatar_url", String(500), nullable=True),
    Column("bio", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("password_updated_at", DateTime, nullable=True),
)

user_sessions = Table(
    "user_sessions",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("jti", String(128), nullable=False, unique=True, index=True),
    Column("user_agent", Text, nullable=True),
    Column("ip_address", String(64), nullable=True),
    Column("device_name", String(200), nullable=True),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),
    Column("revoked_at", DateTime, nullable=True),
)

user_credits = Table(
    "user_credits",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True),
    Column("credits", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
