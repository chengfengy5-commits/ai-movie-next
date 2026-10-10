from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest
from sqlalchemy import insert

from chapter_asset_replacement_support import OWNER_TABLES, OwnerDatabase, create_owner_database

NOW = datetime(2026, 10, 10, 12, 0, 0)


@pytest.fixture
def admin_task_database(tmp_path: Path) -> Iterator[OwnerDatabase]:
    database = create_owner_database(tmp_path / "admin-tasks-owner.sqlite", seed=False)
    try:
        yield database
    finally:
        database.close()


def make_task_row(
    task_id: str,
    user_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": task_id,
        "user_id": user_id,
        "type": "image",
        "message_id": f"message-{task_id}",
        "status": "completed",
        "credit_cost": 0,
        "result": None,
        "request_data": None,
        "model_name": None,
        "progress": 100,
        "progress_message": None,
        "external_task_id": None,
        "external_provider": None,
        "claimed_by": None,
        "lease_until": None,
        "execution_generation": 0,
        "claim_token": None,
        "recovery_status": "ready",
        "billing_status": "unbilled",
        "user_cancelled_at": None,
        "cancellation_reason": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def seed_admin_tasks(
    database: OwnerDatabase,
    rows: list[Mapping[str, Any]],
) -> None:
    user_ids = sorted({str(row["user_id"]) for row in rows})
    with database.engine.begin() as connection:
        for index, user_id in enumerate(user_ids):
            connection.execute(
                insert(OWNER_TABLES["users"]).values(
                    id=user_id,
                    username=f"admin-task-user-{index}",
                    email=f"admin-task-user-{index}@example.invalid",
                    hashed_password="fixture-only",
                    is_superuser=user_id == "admin-user",
                    membership_type="free",
                    membership_expires_at=None,
                    avatar_url=None,
                    bio=None,
                    created_at=NOW,
                    password_updated_at=None,
                )
            )
        for row in rows:
            connection.execute(
                insert(OWNER_TABLES["ai_tasks"]).values(**dict(row))
            )


def snapshot_admin_owner(database: OwnerDatabase) -> dict[str, list[dict[str, Any]]]:
    from chapter_asset_replacement_support import CONSERVATION_TABLES

    return database.snapshot(CONSERVATION_TABLES)
