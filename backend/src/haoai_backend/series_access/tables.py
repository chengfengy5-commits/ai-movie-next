"""Minimal SQLAlchemy Core projections used only for read-only access checks."""

from sqlalchemy import Column, MetaData, String, Table, Text

metadata = MetaData()

series = Table(
    "series",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("team_id", String(36), nullable=True),
    Column("claimed_by", String(36), nullable=True),
)

team_members = Table(
    "team_members",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), nullable=False),
    Column("user_id", String(36), nullable=False),
    Column("role", String(20), nullable=False),
    Column("permissions", Text, nullable=True),
)

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(100), nullable=False),
)
