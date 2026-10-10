from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import event, insert
from sqlalchemy.exc import MultipleResultsFound

from chapter_asset_replacement_support import (
    OWNER_TABLES,
    OwnerDatabase,
    create_owner_database,
)
from haoai_backend.task_observation.errors import TaskObservationBadRequest
from haoai_backend.task_observation.persistence import task_observation_unit_of_work_factory
from haoai_backend.task_observation.receipts import (
    build_submission_receipt,
    build_task_receipt,
)


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "task-observation-receipts.sqlite")
    try:
        yield database
    finally:
        database.close()


def _task_values(
    task_id: str,
    *,
    user_id: str = "user-a",
    message_id: str,
    credit_cost: int = 3,
    billing_status: str = "unbilled",
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "user_id": user_id,
        "type": "image-single",
        "message_id": message_id,
        "status": "processing",
        "credit_cost": credit_cost,
        "result": None,
        "request_data": None,
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
        "billing_status": billing_status,
        "user_cancelled_at": None,
        "cancellation_reason": None,
        "created_at": created_at,
        "updated_at": created_at,
    }


def _billing_unit_values(
    unit_id: str,
    task_id: str,
    *,
    item_key: str,
    quoted_amount: int,
    billing_status: str,
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "id": unit_id,
        "task_id": task_id,
        "item_key": item_key,
        "operation": "image.single",
        "step_graph_version": "image-single-v1",
        "input_digest": f"digest-{unit_id}",
        "quoted_amount": quoted_amount,
        "debited_amount": 0,
        "refunded_amount": 0,
        "billing_status": billing_status,
        "result_status": "pending",
        "first_intent_at": None,
        "deadline_at": None,
        "success_confirmed_at": None,
        "user_cancelled_at": None,
        "cancellation_reason": None,
        "quote_id": None,
        "quote_unit_key": None,
        "candidate_digest": None,
        "created_at": created_at,
        "updated_at": created_at,
    }


def _submission_values(task_id: str, *, raw_key: str = " receipt-key ") -> dict[str, Any]:
    return {
        "id": f"submission-{task_id}",
        "user_id": "user-a",
        "operation": "image.single",
        "idempotency_key": raw_key,
        "request_digest": "receipt-digest",
        "task_ids": [task_id],
        "task_quote_id": None,
        "status": "accepted",
        "created_at": datetime(2026, 10, 9, 12, 0, 0),
    }


def _observe_reads(database: OwnerDatabase):
    activity: dict[str, Any] = {"statements": [], "commits": 0}

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        activity["statements"].append(statement)

    def on_commit(_connection):
        activity["commits"] += 1

    event.listen(database.engine, "before_cursor_execute", before_cursor_execute)
    event.listen(database.engine, "commit", on_commit)
    return activity, before_cursor_execute, on_commit


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


def _insert_task(database: OwnerDatabase, task_id: str) -> None:
    now = datetime(2026, 10, 9, 12, 0, 0)
    with database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["ai_tasks"]),
            _task_values(task_id, message_id=f"message-{task_id}", created_at=now),
        )


def test_task_receipt_uses_first_billing_unit_and_task_fallback(owner_database):
    now = datetime(2026, 10, 9, 12, 0, 0)
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["billing_units"]),
            [
                _billing_unit_values(
                    "billing-first",
                    "task-a",
                    item_key="first",
                    quoted_amount=0,
                    billing_status="no_charge",
                    created_at=datetime(2026, 10, 9, 11, 0, 0),
                ),
                _billing_unit_values(
                    "billing-last",
                    "task-a",
                    item_key="last",
                    quoted_amount=99,
                    billing_status="charged",
                    created_at=datetime(2026, 10, 9, 13, 0, 0),
                ),
            ],
        )
        connection.execute(
            insert(OWNER_TABLES["ai_tasks"]),
            _task_values(
                "task-no-unit",
                message_id="message-no-unit",
                credit_cost=7,
                billing_status="charged",
                created_at=now,
            ),
        )

    before = owner_database.snapshot()
    activity, before_cursor_execute, on_commit = _observe_reads(owner_database)
    unit = task_observation_unit_of_work_factory(owner_database.session_factory)()
    try:
        task = unit.load_owned_task("user-a", "task-a")
        no_unit_task = unit.load_owned_task("user-a", "task-no-unit")
        first_receipt = build_task_receipt(task, unit)
        fallback_receipt = build_task_receipt(no_unit_task, unit)
    finally:
        _close_uow(unit)
        _stop_observing(owner_database, before_cursor_execute, on_commit)

    assert first_receipt["quoted_amount"] == 0
    assert first_receipt["billing_status"] == "no_charge"
    assert fallback_receipt["quoted_amount"] == 7
    assert fallback_receipt["billing_status"] == "charged"
    assert owner_database.session_factory.created[-1].close_calls == 1
    _assert_reads_only(activity)
    assert owner_database.snapshot() == before


def test_submission_receipt_uses_raw_key_and_one_billing_unit(owner_database):
    task_id = "receipt-task"
    raw_key = " receipt-key "
    _insert_task(owner_database, task_id)
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["task_submissions"]).values(
                **_submission_values(task_id, raw_key=raw_key)
            )
        )
        connection.execute(
            insert(OWNER_TABLES["billing_units"]).values(
                **_billing_unit_values(
                    "receipt-unit",
                    task_id,
                    item_key="receipt-item",
                    quoted_amount=0,
                    billing_status="no_charge",
                    created_at=datetime(2026, 10, 9, 12, 0, 0),
                )
            )
        )

    before = owner_database.snapshot()
    activity, before_cursor_execute, on_commit = _observe_reads(owner_database)
    unit = task_observation_unit_of_work_factory(owner_database.session_factory)()
    try:
        receipt = build_submission_receipt(
            "user-a",
            "image.single",
            raw_key,
            unit,
            unit,
        )
        assert unit.find_submission("user-a", "image.single", "receipt-key") is None
        with pytest.raises(TaskObservationBadRequest, match="不支持的任务操作"):
            build_submission_receipt(
                "user-a",
                "image.generate",
                "",
                unit,
                unit,
            )
    finally:
        _close_uow(unit)
        _stop_observing(owner_database, before_cursor_execute, on_commit)

    assert receipt == {
        "submission_id": f"submission-{task_id}",
        "operation": "image.single",
        "task_id": task_id,
        "message_id": f"message-{task_id}",
        "status": "processing",
        "quoted_amount": 0,
        "billing_status": "no_charge",
    }
    assert owner_database.session_factory.created[-1].close_calls == 1
    _assert_reads_only(activity)
    assert owner_database.snapshot() == before


def test_submission_receipt_preserves_multiple_unit_error(owner_database):
    task_id = "ambiguous-receipt-task"
    _insert_task(owner_database, task_id)
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["task_submissions"]).values(
                **_submission_values(task_id)
            )
        )
        connection.execute(
            insert(OWNER_TABLES["billing_units"]),
            [
                _billing_unit_values(
                    "ambiguous-unit-a",
                    task_id,
                    item_key="item-a",
                    quoted_amount=2,
                    billing_status="charged",
                    created_at=datetime(2026, 10, 9, 12, 0, 0),
                ),
                _billing_unit_values(
                    "ambiguous-unit-b",
                    task_id,
                    item_key="item-b",
                    quoted_amount=4,
                    billing_status="charged",
                    created_at=datetime(2026, 10, 9, 12, 1, 0),
                ),
            ],
        )

    before = owner_database.snapshot()
    activity, before_cursor_execute, on_commit = _observe_reads(owner_database)
    unit = task_observation_unit_of_work_factory(owner_database.session_factory)()
    try:
        with pytest.raises(MultipleResultsFound):
            build_submission_receipt(
                "user-a",
                "image.single",
                " receipt-key ",
                unit,
                unit,
            )
    finally:
        _close_uow(unit)
        _stop_observing(owner_database, before_cursor_execute, on_commit)

    assert owner_database.session_factory.created[-1].close_calls == 1
    _assert_reads_only(activity)
    assert owner_database.snapshot() == before
