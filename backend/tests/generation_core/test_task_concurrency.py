from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import AITaskRow, UserRow
from haoai_backend.generation_core.schema import FoundationBase
from haoai_backend.generation_runtime.registry import (
    IN_FLIGHT_STATES,
    SOURCE_WORKER_TYPES,
)
from haoai_backend.generation_task_concurrency import (
    count_user_in_flight_tasks,
    graph_tasks_match_shape,
    session_uses_fresh_postgres_statements,
    task_shape_for_operation,
)


@pytest.mark.parametrize("task_type", SOURCE_WORKER_TYPES)
@pytest.mark.parametrize("status", IN_FLIGHT_STATES)
def test_all_registered_task_types_and_in_flight_states_share_user_capacity(
    tmp_path: Path,
    task_type: str,
    status: str,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.sqlite'}")
    FoundationBase.metadata.create_all(engine)
    session = Session(engine)
    try:
        session.add_all((UserRow(id="owner"), UserRow(id="other")))
        session.flush()
        session.execute(
            insert(AITaskRow.__table__),
            [
                {
                    "id": "owned-task",
                    "user_id": "owner",
                    "type": task_type,
                    "status": status,
                    "billing_status": "partially_refunded",
                    "message_id": "message-owner",
                },
                {
                    "id": "other-task",
                    "user_id": "other",
                    "type": "image-single",
                    "status": "processing",
                    "billing_status": "unbilled",
                    "message_id": "message-other",
                },
            ],
        )
        assert count_user_in_flight_tasks(session, "owner") == 1
        assert count_user_in_flight_tasks(session, "other") == 1
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("billing_status", "expected"),
    [
        ("legacy_unreconciled", 1),
        ("unbilled", 1),
        ("charged", 1),
        ("partially_refunded", 1),
        ("no_charge", 1),
        ("refunded", 0),
    ],
)
def test_only_exact_refunded_task_rows_are_excluded(
    tmp_path: Path,
    billing_status: str,
    expected: int,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'billing.sqlite'}")
    FoundationBase.metadata.create_all(engine)
    session = Session(engine)
    try:
        session.add(UserRow(id="owner"))
        session.flush()
        session.execute(
            insert(AITaskRow.__table__),
            {
                "id": "task",
                "user_id": "owner",
                "type": "image-single",
                "status": "processing",
                "billing_status": billing_status,
                "message_id": "message",
            },
        )
        assert count_user_in_flight_tasks(session, "owner") == expected
    finally:
        session.close()
        engine.dispose()


def test_task_status_outside_the_four_in_flight_states_is_not_counted(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'unknown-status.sqlite'}")
    FoundationBase.metadata.create_all(engine)
    session = Session(engine)
    try:
        session.add(UserRow(id="owner"))
        session.flush()
        session.execute(
            insert(AITaskRow.__table__).values(
                id="unknown-state-task",
                user_id="owner",
                type="image-single",
                status="unexpected-state",
                billing_status="unbilled",
                message_id="message",
            )
        )
        assert count_user_in_flight_tasks(session, "owner") == 0
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("operation", "item_count", "task_type", "task_count"),
    [
        ("image.single", 1, "image-single", 1),
        ("video.single", 1, "video-single", 1),
        ("image.batch", 4, "batch-image", 4),
        ("frame.review", 1, "ai-review", 1),
        ("frame.optimize", 7, "batch-optimize", 1),
    ],
)
def test_server_task_shape_binds_operation_to_type_and_cardinality(
    operation: str,
    item_count: int,
    task_type: str,
    task_count: int,
) -> None:
    shape = task_shape_for_operation(operation, item_count)
    assert shape is not None
    assert (shape.task_type, shape.task_count) == (task_type, task_count)
    tasks = tuple(SimpleNamespace(task_type=task_type) for _ in range(task_count))
    assert graph_tasks_match_shape(operation, item_count, tasks)
    assert not graph_tasks_match_shape(operation, item_count, tasks[:-1])


def test_taskless_and_unbound_operations_have_no_positive_limit_shape() -> None:
    assert task_shape_for_operation("text.stream", 1) is None
    assert task_shape_for_operation("future.operation", 1) is None
    assert task_shape_for_operation("image.single", 2) is None
    assert task_shape_for_operation("image.batch", 0) is None
    assert task_shape_for_operation("frame.optimize", 0) is None


def test_non_read_committed_postgres_isolation_fails_closed() -> None:
    class FakeSession:
        def __init__(self, isolation: str) -> None:
            self.isolation = isolation

        def get_bind(self) -> object:
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def connection(self) -> object:
            return SimpleNamespace(get_isolation_level=lambda: self.isolation)

    assert session_uses_fresh_postgres_statements(FakeSession("READ COMMITTED"))
    assert session_uses_fresh_postgres_statements(FakeSession("read_committed"))
    assert not session_uses_fresh_postgres_statements(FakeSession("REPEATABLE READ"))
    assert not session_uses_fresh_postgres_statements(FakeSession("SERIALIZABLE"))


def test_sqlite_does_not_add_postgres_isolation_requirement(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sqlite.sqlite'}")
    session = Session(engine)
    try:
        assert session_uses_fresh_postgres_statements(session)
    finally:
        session.close()
        engine.dispose()
