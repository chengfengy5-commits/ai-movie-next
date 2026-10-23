from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import event, insert, select, update

from chapter_asset_replacement_support import (
    CONSERVATION_TABLES,
    OWNER_METADATA,
    OWNER_TABLES,
    OwnerDatabase,
    create_owner_database,
)
from haoai_backend.task_observation.domain import TeamMembershipRecord
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.application import get_running_batch_optimize
from haoai_backend.task_observation.application import request_batch_optimize_cancel
from haoai_backend.task_observation.errors import TaskObservationUnavailable
from haoai_backend.task_observation.persistence import (
    SqlAlchemyTaskObservationUnitOfWork,
    task_observation_unit_of_work_factory,
)
from haoai_backend.task_observation.tables import ai_tasks
from haoai_backend.generation_core.base_schema import AITaskRow, BillingUnitRow
from haoai_backend.generation_core.frame_optimize_cancellation import (
    SqlAlchemyFrameOptimizeCancellation,
)
from generation_execution.frame_optimize_execution_helpers import admit_batch


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


def test_running_optimize_candidates_keep_legacy_states_and_only_registered_reconciling(
    owner_database,
):
    stamp = datetime(2026, 10, 9, 13, 0, 0)
    task_specs = (
        ("legacy-processing", "user-a", "processing", '{"chapter_id": "chapter-a"}', 18),
        (
            "registered-request",
            "user-a",
            "reconciling",
            json.dumps({"chapter_id": "chapter-a", "operation": "frame.optimize"}),
            16,
        ),
        ("registered-unit", "user-a", "reconciling", '{"chapter_id": "chapter-a"}', 17),
        ("legacy-reconciling", "user-a", "reconciling", '{"chapter_id": "chapter-a"}', 21),
        ("damaged-hint", "user-a", "reconciling", '{broken json', 20),
        (
            "other-owner",
            "user-b",
            "reconciling",
            json.dumps({"chapter_id": "chapter-a", "operation": "frame.optimize"}),
            22,
        ),
        ("legacy-queued", "user-a", "queued", '{"chapter_id": "chapter-a"}', 15),
        ("legacy-cancelling", "user-a", "cancelling", '{"chapter_id": "chapter-a"}', 14),
    )
    task_rows = [
        _task_values(
            task_id,
            user_id=user_id,
            task_type="batch-optimize",
            status=status,
            message_id=f"message-{task_id}",
            request_data=request_data,
            created_at=stamp.replace(minute=minute),
        )
        for task_id, user_id, status, request_data, minute in task_specs
    ]
    with owner_database.engine.begin() as connection:
        connection.execute(insert(OWNER_TABLES["ai_tasks"]), task_rows)
        connection.execute(
            insert(OWNER_TABLES["billing_units"]).values(
                id="unit-registered",
                task_id="registered-unit",
                item_key="frame-0",
                operation="frame.optimize",
                step_graph_version="frame-optimize-v1",
                input_digest="synthetic-digest",
                quoted_amount=3,
                created_at=stamp,
                updated_at=stamp,
            )
        )

    factory = task_observation_unit_of_work_factory(owner_database.session_factory)
    unit = factory()
    try:
        candidates = unit.list_running_batch_optimize_candidates("user-a")
        assert [row["id"] for row in candidates] == [
            "legacy-processing",
            "registered-unit",
            "registered-request",
            "legacy-queued",
            "legacy-cancelling",
        ]
    finally:
        _close_uow(unit)

    public_result = get_running_batch_optimize(
        factory,
        TrustedActor(user_id="user-a"),
        "chapter-a",
    )
    assert public_result["task"]["task_id"] == "legacy-processing"
    assert public_result["task"]["status"] == "processing"


def test_unit_only_registered_cancel_refuses_bad_graph_without_legacy_fallback(tmp_path):
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        with case.sessions.begin() as session:
            task = session.get(AITaskRow, case.task_id)
            assert task is not None
            request_data = json.loads(task.request_data)
            for name in ("operation", "mode", "graph_version", "step_graph_version"):
                request_data.pop(name, None)
            task.request_data = json.dumps(request_data, separators=(",", ":"))
            unit = session.execute(
                select(BillingUnitRow).where(BillingUnitRow.task_id == case.task_id)
            ).scalar_one()
            assert unit.operation == "frame.optimize"

        events: list[str] = []

        class LegacyWriter:
            def request_cancel(self, task_id: str) -> bool:
                events.append(f"legacy:{task_id}")
                return True

        with pytest.raises(TaskObservationUnavailable):
            request_batch_optimize_cancel(
                task_observation_unit_of_work_factory(case.sessions),
                TrustedActor(user_id=case.actor_id),
                case.task_id,
                signal_registry=None,
                cancellation_writer=LegacyWriter(),
                frame_optimize_cancellation=SqlAlchemyFrameOptimizeCancellation(
                    session_factory=case.sessions,
                    db_clock=lambda: case.clock[0],
                    id_factory=lambda: "unit-only-cancel-marker",
                    crypto=case.capabilities.crypto,
                    proof_authority=case.capabilities.proof_authority,
                ),
            )

        assert events == []
        with case.sessions() as session:
            task = session.get(AITaskRow, case.task_id)
            assert task is not None and task.status == "queued"
            assert task.user_cancelled_at is None
    finally:
        case.engine.dispose()


def test_public_registered_cancel_closes_read_session_and_commits_before_signal(tmp_path):
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        events: list[str] = []
        caller_sessions = 0

        class ObservedUnitOfWork(SqlAlchemyTaskObservationUnitOfWork):
            def close(self) -> None:
                super().close()
                events.append("caller-session-closed")

        def unit_of_work_factory():
            nonlocal caller_sessions
            caller_sessions += 1
            return ObservedUnitOfWork(case.sessions())

        class LegacyWriter:
            def request_cancel(self, task_id: str) -> bool:
                raise AssertionError(f"registered task fell through to legacy writer: {task_id}")

        class Signal:
            def set(self) -> None:
                assert events[-1] == "cancellation-committed"
                events.append("signal-set")

        class SignalRegistry:
            def get(self, task_id: str):
                assert task_id == case.task_id
                return Signal()

        cancellation = SqlAlchemyFrameOptimizeCancellation(
            session_factory=case.sessions,
            db_clock=lambda: case.clock[0],
            id_factory=lambda: "public-cancel-marker",
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
        )

        class ObservedCancellation:
            def cancel_owned(self, actor, task_id: str) -> str:
                assert events == ["caller-session-closed"]
                outcome = cancellation.cancel_owned(actor, task_id)
                assert outcome == "settled"
                with case.sessions() as session:
                    task = session.get(AITaskRow, task_id)
                    assert task is not None
                    assert task.user_cancelled_at is not None
                    assert task.cancellation_reason is not None
                events.append("cancellation-committed")
                return outcome

        response = request_batch_optimize_cancel(
            unit_of_work_factory,
            TrustedActor(user_id=case.actor_id),
            case.task_id,
            SignalRegistry(),
            LegacyWriter(),
            frame_optimize_cancellation=ObservedCancellation(),
        )

        assert caller_sessions == 1
        assert response == {"task_id": case.task_id, "status": "cancelling"}
        assert events == ["caller-session-closed", "cancellation-committed", "signal-set"]
    finally:
        case.engine.dispose()


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
