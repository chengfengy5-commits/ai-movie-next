"""Minimal SQLAlchemy Core projections for the replacement adapter."""

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text

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
)

scenes = Table(
    "scenes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("title", String(255), nullable=False),
)

props = Table(
    "props",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("name", String(255), nullable=False),
)

ASSET_TABLES = {
    "character": characters,
    "scene": scenes,
    "prop": props,
}

ASSET_DISPLAY_COLUMNS = {
    "character": characters.c.name,
    "scene": scenes.c.title,
    "prop": props.c.name,
}
