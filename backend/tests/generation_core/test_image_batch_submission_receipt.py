from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import AITaskRow, TaskSubmissionRow
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.errors import (
    TaskObservationIntegrityError,
    TaskObservationNotFound,
)
from haoai_backend.task_observation.persistence import (
    SqlAlchemyTaskObservationUnitOfWork,
)
from haoai_backend.task_observation.application import get_submission_receipt
from test_batch_execution_snapshot_reader import _authenticated_batch_environment
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_core.admission_receipts import AdmissionStaged


@pytest.fixture
def batch_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
        item_count=2,
    )
    assert isinstance(staged, AdmissionStaged)
    try:
        yield environment, staged
    finally:
        environment.engine.dispose()


def test_batch_submission_receipt_uses_original_parent_order_after_child_progress(
    batch_environment,
):
    environment, staged = batch_environment
    progressed_task_id = staged.ordered_tasks[0].task_id
    with environment.engine.begin() as connection:
        connection.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == progressed_task_id)
            .values(status="processing")
        )

    activity: dict[str, object] = {"statements": [], "commits": 0}

    def observe_statement(_conn, _cursor, statement, _parameters, _context, _many):
        activity["statements"].append(statement)

    def observe_commit(_connection):
        activity["commits"] = int(activity["commits"]) + 1

    event.listen(environment.engine, "before_cursor_execute", observe_statement)
    event.listen(environment.engine, "commit", observe_commit)
    try:
        receipt = get_submission_receipt(
            lambda: SqlAlchemyTaskObservationUnitOfWork(Session(environment.engine)),
            TrustedActor(staged.owner_id),
            "image.batch",
            "batch-reader-system-2",
        )
    finally:
        event.remove(environment.engine, "before_cursor_execute", observe_statement)
        event.remove(environment.engine, "commit", observe_commit)

    expected = []
    for task in staged.ordered_tasks:
        request_data = json.loads(task.request_data)
        expected.append(
            {
                "type": request_data["asset_type"],
                "asset_id": request_data["asset_id"],
                "asset_name": request_data["asset_name"],
                "message_id": task.message_ids[-1],
                "task_id": task.task_id,
            }
        )
    assert receipt == {"tasks": expected}
    assert receipt["tasks"][0]["task_id"] == progressed_task_id
    assert activity["commits"] == 0
    writes = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "TRUNCATE")
    assert not [
        statement
        for statement in activity["statements"]
        if statement.lstrip().upper().startswith(writes)
    ]


def test_batch_submission_receipt_does_not_expose_another_owners_key(
    batch_environment,
):
    environment, _staged = batch_environment
    with pytest.raises(TaskObservationNotFound):
        get_submission_receipt(
            lambda: SqlAlchemyTaskObservationUnitOfWork(Session(environment.engine)),
            TrustedActor("another-owner"),
            "image.batch",
            "batch-reader-system-2",
        )


def test_batch_submission_receipt_rejects_a_missing_original_child_without_writes(
    batch_environment,
):
    environment, staged = batch_environment
    original_ids = [task.task_id for task in staged.ordered_tasks]
    with environment.engine.begin() as connection:
        connection.execute(
            update(TaskSubmissionRow.__table__)
            .where(TaskSubmissionRow.__table__.c.id == staged.submission_id)
            .values(task_ids=[original_ids[0], "missing-batch-child"])
        )

    activity: dict[str, object] = {"statements": [], "commits": 0}

    def observe_statement(_conn, _cursor, statement, _parameters, _context, _many):
        activity["statements"].append(statement)

    def observe_commit(_connection):
        activity["commits"] = int(activity["commits"]) + 1

    event.listen(environment.engine, "before_cursor_execute", observe_statement)
    event.listen(environment.engine, "commit", observe_commit)
    try:
        with pytest.raises(TaskObservationIntegrityError):
            get_submission_receipt(
                lambda: SqlAlchemyTaskObservationUnitOfWork(
                    Session(environment.engine)
                ),
                TrustedActor(staged.owner_id),
                "image.batch",
                "batch-reader-system-2",
            )
    finally:
        event.remove(environment.engine, "before_cursor_execute", observe_statement)
        event.remove(environment.engine, "commit", observe_commit)

    assert activity["commits"] == 0
    writes = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "TRUNCATE")
    assert not [
        statement
        for statement in activity["statements"]
        if statement.lstrip().upper().startswith(writes)
    ]
