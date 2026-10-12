"""Minimal SQLAlchemy Core projections for the legacy user-card tables."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, String, Table

metadata = MetaData()

teams = Table(
    "teams",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
)

team_members = Table(
    "team_members",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), nullable=False),
    Column("user_id", String(36), nullable=False),
    Column("role", String(20), nullable=False),
)

series = Table(
    "series",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("name", String(255), nullable=False),
    Column("team_id", String(36), nullable=True),
)

characters = Table(
    "characters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
)

scenes = Table(
    "scenes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
)

props = Table(
    "props",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
)

storyboard_assets = Table(
    "storyboard_assets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
)
