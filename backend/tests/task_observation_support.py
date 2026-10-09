"""Small adapter around the shared full-owner SQLite test fixture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chapter_asset_replacement_support import (
    CONSERVATION_TABLES,
    OWNER_METADATA,
    OwnerDatabase,
    create_owner_database,
)


def create_empty_task_observation_database(path: Path) -> OwnerDatabase:
    """Create every real owner table without borrowing another module's seed data."""
    return create_owner_database(path, seed=False)


def snapshot_task_observation_owner(database: OwnerDatabase) -> dict[str, list[dict[str, Any]]]:
    return database.snapshot(CONSERVATION_TABLES)


def task_observation_owner_table_names() -> tuple[str, ...]:
    return tuple(table.name for table in OWNER_METADATA.sorted_tables)
