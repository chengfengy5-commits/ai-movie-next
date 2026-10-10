from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import event, insert, update

from chapter_asset_replacement_support import (
    CONSERVATION_TABLES,
    OWNER_METADATA,
    OWNER_TABLES,
    OwnerDatabase,
    create_owner_database,
)
from haoai_backend.task_observation.domain import TeamMembershipRecord
from haoai_backend.task_observation.persistence import (
    SqlAlchemyTaskObservationUnitOfWork,
    task_observation_unit_of_work_factory,
)
from haoai_backend.task_observation.tables import ai_tasks


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "task-observation.sqlite")
    try:
        yield database
    finally:
        database.close()


def _task_values(
    task_id: str,
    *,
    user_id: str = "user-a",
    task_type: str = "image",
    status: str = "queued",
    message_id: str,
    request_data: str | None = None,
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "user_id": user_id,
        "type": task_type,
        "message_id": message_id,
        "status": status,
        "credit_cost": 3,
        "result": None,
        "request_data": request_data,
        "model_name": "test-model",
        "progress": 0,
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
        "created_at": created_at,
        "updated_at": created_at,
    }


def _observe_sql(database: OwnerDatabase):
    activity: dict[str, Any] = {"statements": [], "commits": 0}
    tracking = {"enabled": True}

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        if tracking["enabled"]:
            activity["statements"].append(statement)

    def on_commit(_connection):
        if tracking["enabled"]:
            activity["commits"] += 1

    event.listen(database.engine, "before_cursor_execute", before_cursor_execute)
    event.listen(database.engine, "commit", on_commit)
    return activity, tracking, before_cursor_execute, on_commit


def _stop_observing(database, before_cursor_execute, on_commit) -> None:
    event.remove(database.engine, "before_cursor_execute", before_cursor_execute)
    event.remove(database.engine, "commit", on_commit)


def _assert_reads_only(activity: dict[str, Any]) -> None:
    writes = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "TRUNCATE")
    assert activity["commits"] == 0
    assert not [
        statement
        for statement in activity["statements"]
        if statement.lstrip().upper().startswith(writes)
    ]


def _close_uow(unit_of_work) -> None:
    try:
        unit_of_work.rollback()
    finally:
        unit_of_work.close()


def test_supplied_session_reads_full_owner_rows_without_business_writes(owner_database):
    owner_database.assert_foreign_keys_enabled()
    assert len(OWNER_METADATA.sorted_tables) == 24
    assert set(OWNER_TABLES) == set(CONSERVATION_TABLES)
    before = owner_database.snapshot()
    assert set(before) == set(CONSERVATION_TABLES)
    assert all(before[table_name] for table_name in CONSERVATION_TABLES)
    assert not ai_tasks.c.message_id.foreign_keys

    stamp = datetime(2026, 10, 9, 13, 0, 0)
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["ai_tasks"]),
            [
                _task_values(
                    "review-spaced",
                    task_type="ai-review",
                    status="queued",
                    message_id="review-message",
                    request_data='{"source_message_id": "review-message", "prompt_id": "p1"}',
                    created_at=stamp,
                ),
                _task_values(
                    "review-compact",
                    task_type="ai-review",
                    status="processing",
                    message_id="review-message-compact",
                    request_data='{"source_message_id":"review-message","prompt_id":"p1"}',
                    created_at=datetime(2026, 10, 9, 14, 0, 0),
                ),
                _task_values(
                    "batch-cancelling",
                    task_type="batch-optimize",
                    status="cancelling",
                    message_id="batch-message",
                    request_data=json.dumps({"chapter_id": "chapter-a"}),
                    created_at=datetime(2026, 10, 9, 15, 0, 0),
                ),
            ],
        )
        connection.execute(
            update(OWNER_TABLES["users"])
            .where(OWNER_TABLES["users"].c.id == "user-b")
            .values(is_superuser=True)
        )

    before_reads = owner_database.snapshot()
    activity, tracking, before_cursor_execute, on_commit = _observe_sql(owner_database)
    unit = task_observation_unit_of_work_factory(owner_database.session_factory)()
    try:
        owned_task = unit.load_owned_task("user-a", "task-a")
        assert owned_task is not None
        assert owned_task["message_id"] == "chat-a"
        assert unit.load_owned_task("user-b", "task-a") is None
        assert unit.load_task("task-a")["id"] == "task-a"
        assert [row["id"] for row in unit.find_owned_by_message("user-a", "chat-a")] == ["task-a"]
        assert unit.count_owned_tasks("user-a") == 4
        assert len(unit.list_owned_tasks("user-a", offset=-1, limit=-1)) == 4

        messages = unit.load_messages(["chat-a"])
        assert messages["chat-a"]["chapter_id"] == "chapter-a"
        assert messages["chat-a"]["frame_index"] == 0
        assert unit.load_messages([]) == {}
        assert unit.load_chapter_titles({"chapter-a"}) == {"chapter-a": "待替换章节"}
        assert unit.load_chapter_titles(set()) == {}
        names = unit.load_asset_names(
            {
                "character": ["old-character"],
                "scene": ["old-scene"],
                "prop": ["old-prop"],
                "storyboard": ["storyboard-a"],
            }
        )
        assert names["character"]["old-character"] == "旧角色"
        assert names["scene"]["old-scene"] == "旧场景"
        assert names["prop"]["old-prop"] == "旧道具"
        assert names["storyboard"]["storyboard-a"] == "镜头 A"

        review_rows = unit.list_ai_review_count_candidates("user-a", "review-message")
        assert [row["id"] for row in review_rows] == ["review-spaced"]
        running_rows = unit.list_running_batch_optimize_candidates("user-a")
        assert [row["id"] for row in running_rows] == ["batch-cancelling"]
        assert unit.load_sql_superuser("user-b") is True
        assert unit.load_sql_superuser("missing-user") is False
        assert unit.load_membership("team-a", "user-b") == TeamMembershipRecord(
            team_id="team-a",
            user_id="user-b",
            role="member",
            permissions=None,
        )
    finally:
        _close_uow(unit)
        _stop_observing(owner_database, before_cursor_execute, on_commit)

    assert owner_database.session_factory.created[-1].close_calls == 1
    _assert_reads_only(activity)
    assert owner_database.snapshot() == before_reads


def test_membership_cache_uses_primary_key_and_executes_each_select(owner_database):
    activity, tracking, before_cursor_execute, on_commit = _observe_sql(owner_database)
    session = owner_database.session_factory()
    unit = SqlAlchemyTaskObservationUnitOfWork(session)
    membership_table = OWNER_TABLES["team_members"]
    stamp = datetime(2026, 10, 9, 13, 0, 0)

    try:
        first = unit.load_membership("team-a", "user-b")
        assert first is not None
        assert first.role == "member"
        assert first.permissions is None

        unit.rollback()
        tracking["enabled"] = False
        with owner_database.engine.begin() as connection:
            connection.execute(
                update(membership_table)
                .where(membership_table.c.id == "team-member-b")
                .values(role="admin", permissions='["view_tasks"]')
            )
        tracking["enabled"] = True

        same_primary_key = unit.load_membership("team-a", "user-b")
        assert same_primary_key is first
        assert same_primary_key.role == "member"
        assert same_primary_key.permissions is None

        unit.rollback()
        tracking["enabled"] = False
        with owner_database.engine.begin() as connection:
            connection.execute(
                membership_table.delete().where(membership_table.c.id == "team-member-b")
            )
            connection.execute(
                insert(membership_table).values(
                    id="team-member-b-replacement",
                    team_id="team-a",
                    user_id="user-b",
                    role="admin",
                    permissions='["view_tasks"]',
                    joined_at=stamp,
                )
            )
        tracking["enabled"] = True

        new_primary_key = unit.load_membership("team-a", "user-b")
        assert new_primary_key is not None
        assert new_primary_key is not first
        assert new_primary_key.role == "admin"
        assert new_primary_key.permissions == '["view_tasks"]'
    finally:
        _close_uow(unit)
        _stop_observing(owner_database, before_cursor_execute, on_commit)

    team_member_selects = [
        statement
        for statement in activity["statements"]
        if statement.lstrip().upper().startswith("SELECT")
        and "FROM team_members" in statement
    ]
    assert len(team_member_selects) == 3
    assert session.close_calls == 1
    _assert_reads_only(activity)
