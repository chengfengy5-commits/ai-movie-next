"""Transaction tests for task cancellation against the complete owner schema."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from chapter_asset_replacement_support import (
    CONSERVATION_TABLES,
    OwnerDatabase,
    TrackedSession,
    ai_tasks,
    billing_units,
    credit_logs,
    create_owner_database,
    execution_steps,
    external_submissions,
    result_evidence,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.application import (
    get_batch_optimize_status,
    request_batch_optimize_cancel,
)
from haoai_backend.task_observation.cancellation import SqlTaskCancellationWriter
from haoai_backend.task_observation.persistence import (
    task_observation_unit_of_work_factory,
)
from haoai_backend.task_observation.ports import UnitOfWorkFactory


TASK_ID = "task-a"


@pytest.fixture
def owner_database(tmp_path: Path) -> Iterator[OwnerDatabase]:
    database = create_owner_database(tmp_path / "task-observation-cancellation.sqlite")
    try:
        database.assert_foreign_keys_enabled()
        yield database
    finally:
        database.close()


@contextmanager
def capture_task_updates(engine: Engine) -> Iterator[list[tuple[str, Any]]]:
    """Capture only the task UPDATE statements executed in this context."""
    statements: list[tuple[str, Any]] = []

    def record_update(
        _connection: Connection,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("UPDATE AI_TASKS"):
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", record_update)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record_update)


def assert_status_only_change(
    before: dict[str, list[dict[str, Any]]],
    after: dict[str, list[dict[str, Any]]],
    expected_old_status: str,
    expected_new_status: str,
) -> None:
    expected = deepcopy(before)
    task_rows = [row for row in expected["ai_tasks"] if row["id"] == TASK_ID]
    assert len(task_rows) == 1
    assert task_rows[0]["status"] == expected_old_status
    task_rows[0]["status"] = expected_new_status
    assert after == expected


def assert_full_nonempty_owner_history(
    snapshot: dict[str, list[dict[str, Any]]],
) -> None:
    assert tuple(snapshot) == CONSERVATION_TABLES
    assert all(snapshot[table_name] for table_name in CONSERVATION_TABLES)


def set_task_status(database: OwnerDatabase, status: str) -> None:
    with database.engine.begin() as connection:
        result = connection.execute(
            update(ai_tasks).where(ai_tasks.c.id == TASK_ID).values(status=status)
        )
        assert result.rowcount == 1


def normalized_statement(statement: str) -> str:
    return " ".join(statement.split())


def test_updates_only_status_and_commits_for_queued_and_processing_tasks(
    owner_database: OwnerDatabase,
) -> None:
    for starting_status in ("queued", "processing"):
        set_task_status(owner_database, starting_status)
        before = owner_database.snapshot(CONSERVATION_TABLES)
        assert_full_nonempty_owner_history(before)

        writer = SqlTaskCancellationWriter(owner_database.engine.begin)
        with capture_task_updates(owner_database.engine) as statements:
            assert writer.request_cancel(TASK_ID) is True

        assert len(statements) == 1
        statement, parameters = statements[0]
        assert normalized_statement(statement) == (
            "UPDATE ai_tasks SET status='cancelling' "
            "WHERE id=? AND status IN ('queued','processing')"
        )
        assert parameters == (TASK_ID,)
        assert_status_only_change(
            before,
            owner_database.snapshot(CONSERVATION_TABLES),
            starting_status,
            "cancelling",
        )


def test_row_zero_still_commits_after_a_second_physical_connection_finishes_task(
    owner_database: OwnerDatabase,
) -> None:
    set_task_status(owner_database, "processing")
    before = owner_database.snapshot(CONSERVATION_TABLES)
    assert_full_nonempty_owner_history(before)

    with owner_database.physical_connection_pair() as (business, concurrent):
        business_dbapi = business.connection.dbapi_connection
        concurrent_dbapi = concurrent.connection.dbapi_connection
        assert business_dbapi is not concurrent_dbapi

        # Consume the initial task read, then end its read transaction before
        # the second connection commits a terminal status.
        task_id = business.execute(
            select(ai_tasks.c.id).where(ai_tasks.c.id == TASK_ID)
        ).scalar_one()
        assert task_id == TASK_ID
        business.commit()

        with concurrent.begin():
            changed = concurrent.execute(
                update(ai_tasks)
                .where(ai_tasks.c.id == TASK_ID)
                .values(status="completed")
            )
            assert changed.rowcount == 1

        @contextmanager
        def business_transaction() -> Iterator[Connection]:
            with business.begin():
                yield business

        committed_connections: list[Connection] = []

        def record_commit(connection: Connection) -> None:
            committed_connections.append(connection)

        event.listen(owner_database.engine, "commit", record_commit)
        try:
            writer = SqlTaskCancellationWriter(business_transaction)
            with capture_task_updates(owner_database.engine) as statements:
                assert writer.request_cancel(TASK_ID) is False
        finally:
            event.remove(owner_database.engine, "commit", record_commit)

        assert len(statements) == 1
        assert committed_connections == [business]

    expected = deepcopy(before)
    changed_task = next(
        row for row in expected["ai_tasks"] if row["id"] == TASK_ID
    )
    changed_task["status"] = "completed"
    assert owner_database.snapshot(CONSERVATION_TABLES) == expected


def test_actual_sql_failure_rolls_back_every_owner_row(
    owner_database: OwnerDatabase,
) -> None:
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_task_cancellation
            BEFORE UPDATE OF status ON ai_tasks
            WHEN OLD.id = 'task-a' AND NEW.status = 'cancelling'
            BEGIN
                SELECT RAISE(ABORT, 'injected cancellation SQL failure');
            END
            """
        )

    before = owner_database.snapshot(CONSERVATION_TABLES)
    writer = SqlTaskCancellationWriter(owner_database.engine.begin)
    with pytest.raises(IntegrityError, match="injected cancellation SQL failure"):
        writer.request_cancel(TASK_ID)

    assert owner_database.snapshot(CONSERVATION_TABLES) == before
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER reject_task_cancellation")


def test_real_deferred_constraint_failure_rolls_back_before_commit(
    owner_database: OwnerDatabase,
) -> None:
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE cancel_commit_parent (id TEXT PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE cancel_commit_child (
                parent_id TEXT NOT NULL,
                FOREIGN KEY(parent_id) REFERENCES cancel_commit_parent(id)
                    DEFERRABLE INITIALLY DEFERRED
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_task_cancellation_at_commit
            AFTER UPDATE OF status ON ai_tasks
            WHEN OLD.id = 'task-a' AND NEW.status = 'cancelling'
            BEGIN
                INSERT INTO cancel_commit_child(parent_id) VALUES ('missing-parent');
            END
            """
        )

    before = owner_database.snapshot(CONSERVATION_TABLES)
    writer = SqlTaskCancellationWriter(owner_database.engine.begin)
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        writer.request_cancel(TASK_ID)

    assert owner_database.snapshot(CONSERVATION_TABLES) == before
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER reject_task_cancellation_at_commit"
        )
        connection.exec_driver_sql("DROP TABLE cancel_commit_child")
        connection.exec_driver_sql("DROP TABLE cancel_commit_parent")


class CommitAcknowledgementLost(RuntimeError):
    pass


@contextmanager
def commit_then_lose_acknowledgement(engine: Engine) -> Iterator[Connection]:
    with engine.begin() as connection:
        yield connection
    raise CommitAcknowledgementLost("commit completed but acknowledgement was lost")


def test_commit_acknowledgement_loss_is_durable_and_never_replayed(
    owner_database: OwnerDatabase,
) -> None:
    before = owner_database.snapshot(CONSERVATION_TABLES)
    assert_full_nonempty_owner_history(before)
    writer = SqlTaskCancellationWriter(
        lambda: commit_then_lose_acknowledgement(owner_database.engine)
    )

    with capture_task_updates(owner_database.engine) as statements:
        with pytest.raises(CommitAcknowledgementLost):
            writer.request_cancel(TASK_ID)

    assert len(statements) == 1
    assert_status_only_change(
        before,
        owner_database.snapshot(CONSERVATION_TABLES),
        "processing",
        "cancelling",
    )

    # A later independent read observes the durable write. The adapter did
    # not retry after the context manager reported an unknown commit outcome.
    with owner_database.engine.connect() as connection:
        status = connection.execute(
            select(ai_tasks.c.status).where(ai_tasks.c.id == TASK_ID)
        ).scalar_one()
    assert status == "cancelling"

def test_row_zero_still_commits_after_a_second_physical_connection_deletes_task(
    owner_database: OwnerDatabase,
) -> None:
    set_task_status(owner_database, "processing")
    before = owner_database.snapshot(CONSERVATION_TABLES)
    assert_full_nonempty_owner_history(before)

    with owner_database.physical_connection_pair() as (business, concurrent):
        assert (
            business.connection.dbapi_connection
            is not concurrent.connection.dbapi_connection
        )

        # Consume the task observation and close its read transaction before B
        # deletes the task and the rows whose foreign keys reference it.
        task_id = business.execute(
            select(ai_tasks.c.id).where(ai_tasks.c.id == TASK_ID)
        ).scalar_one()
        assert task_id == TASK_ID
        business.commit()

        with concurrent.begin():
            for table, column, row_id in (
                (result_evidence, result_evidence.c.id, "result-evidence-a"),
                (
                    external_submissions,
                    external_submissions.c.id,
                    "external-submission-a",
                ),
                (execution_steps, execution_steps.c.id, "step-a"),
                (credit_logs, credit_logs.c.id, "credit-log-a"),
                (billing_units, billing_units.c.id, "billing-a"),
                (ai_tasks, ai_tasks.c.id, TASK_ID),
            ):
                deleted = concurrent.execute(delete(table).where(column == row_id))
                assert deleted.rowcount == 1

        @contextmanager
        def business_transaction() -> Iterator[Connection]:
            with business.begin():
                yield business

        committed_connections: list[Connection] = []

        def record_commit(connection: Connection) -> None:
            committed_connections.append(connection)

        event.listen(owner_database.engine, "commit", record_commit)
        try:
            writer = SqlTaskCancellationWriter(business_transaction)
            with capture_task_updates(owner_database.engine) as statements:
                assert writer.request_cancel(TASK_ID) is False
        finally:
            event.remove(owner_database.engine, "commit", record_commit)

        assert len(statements) == 1
        assert committed_connections == [business]

    expected = deepcopy(before)
    rows_removed_by_b = {
        "result_evidence": {"result-evidence-a"},
        "external_submissions": {"external-submission-a"},
        "execution_steps": {"step-a"},
        "credit_logs": {"credit-log-a"},
        "billing_units": {"billing-a"},
        "ai_tasks": {TASK_ID},
    }
    for table_name, removed_ids in rows_removed_by_b.items():
        expected[table_name] = [
            row for row in expected[table_name] if row["id"] not in removed_ids
        ]
    assert owner_database.snapshot(CONSERVATION_TABLES) == expected


@contextmanager
def capture_application_task_sql(engine: Engine) -> Iterator[dict[str, list[Any]]]:
    """Track the owned-task connection, cancel UPDATE result, and commits."""
    trace: dict[str, list[Any]] = {
        "owned_read_connections": [],
        "cancel_update_connections": [],
        "cancel_rowcounts": [],
        "commit_connections": [],
    }

    def before_cursor_execute(
        connection: Connection,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = normalized_statement(statement).upper()
        connection_id = id(connection.connection.dbapi_connection)
        if normalized.startswith("SELECT ") and " FROM AI_TASKS " in f" {normalized} ":
            trace["owned_read_connections"].append(connection_id)
        if normalized.startswith("UPDATE AI_TASKS SET STATUS='CANCELLING'"):
            trace["cancel_update_connections"].append(connection_id)

    def after_cursor_execute(
        _connection: Connection,
        cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if normalized_statement(statement).upper().startswith(
            "UPDATE AI_TASKS SET STATUS='CANCELLING'"
        ):
            trace["cancel_rowcounts"].append(cursor.rowcount)

    def record_commit(connection: Connection) -> None:
        trace["commit_connections"].append(
            id(connection.connection.dbapi_connection)
        )

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    event.listen(engine, "commit", record_commit)
    try:
        yield trace
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
        event.remove(engine, "after_cursor_execute", after_cursor_execute)
        event.remove(engine, "commit", record_commit)


def task_factory(database: OwnerDatabase) -> UnitOfWorkFactory:
    return task_observation_unit_of_work_factory(database.session_factory)


def assert_application_session_cleanup(
    database: OwnerDatabase,
    previous_session_count: int,
    expected_new_sessions: int,
) -> None:
    created = database.session_factory.created[previous_session_count:]
    assert len(created) == expected_new_sessions
    assert all(isinstance(session, TrackedSession) for session in created)
    assert all(session.close_calls == 1 for session in created)


class RecordingSignal:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def set(self) -> None:
        self.events.append("signal.set")


class RecordingRegistry:
    def __init__(self, events: list[str], signal: RecordingSignal) -> None:
        self.events = events
        self.signal = signal

    def get(self, task_id: str) -> RecordingSignal:
        assert task_id == TASK_ID
        self.events.append("signal.get")
        return self.signal


def test_real_application_signals_then_commits_zero_row_after_concurrent_delete(
    owner_database: OwnerDatabase,
) -> None:
    with owner_database.engine.connect() as connection:
        journal_mode = connection.exec_driver_sql(
            "PRAGMA journal_mode=WAL"
        ).scalar_one()
        connection.commit()
    assert journal_mode.lower() == "wal"

    before = owner_database.snapshot(CONSERVATION_TABLES)
    assert_full_nonempty_owner_history(before)
    events: list[str] = []
    signal = RecordingSignal(events)
    uow_factory = task_factory(owner_database)
    previous_session_count = len(owner_database.session_factory.created)
    concurrent_connection_ids: list[int] = []

    class DeleteTaskAfterObservedRead:
        def get(self, task_id: str) -> RecordingSignal:
            assert task_id == TASK_ID
            assert len(sql_trace["owned_read_connections"]) == 1
            business_connection_id = sql_trace["owned_read_connections"][0]
            events.append("signal.get")
            with owner_database.engine.begin() as concurrent:
                concurrent_connection_id = id(
                    concurrent.connection.dbapi_connection
                )
                concurrent_connection_ids.append(concurrent_connection_id)
                assert concurrent_connection_id != business_connection_id
                for table, column, row_id in (
                    (result_evidence, result_evidence.c.id, "result-evidence-a"),
                    (
                        external_submissions,
                        external_submissions.c.id,
                        "external-submission-a",
                    ),
                    (execution_steps, execution_steps.c.id, "step-a"),
                    (credit_logs, credit_logs.c.id, "credit-log-a"),
                    (billing_units, billing_units.c.id, "billing-a"),
                    (ai_tasks, ai_tasks.c.id, TASK_ID),
                ):
                    deleted = concurrent.execute(delete(table).where(column == row_id))
                    assert deleted.rowcount == 1
            events.append("b.commit.completed")
            return signal

    @contextmanager
    def cancellation_transaction() -> Iterator[Connection]:
        events.append("writer.enter")
        with owner_database.engine.begin() as connection:
            yield connection
        events.append("writer.commit.completed")

    writer = SqlTaskCancellationWriter(cancellation_transaction)
    with capture_application_task_sql(owner_database.engine) as sql_trace:
        response = request_batch_optimize_cancel(
            uow_factory,
            TrustedActor("user-a"),
            TASK_ID,
            DeleteTaskAfterObservedRead(),
            writer,
        )

    assert response == {"task_id": TASK_ID, "status": "cancelling"}
    assert events == [
        "signal.get",
        "b.commit.completed",
        "signal.set",
        "writer.enter",
        "writer.commit.completed",
    ]
    assert len(sql_trace["owned_read_connections"]) == 1
    assert len(concurrent_connection_ids) == 1
    assert concurrent_connection_ids[0] != sql_trace["owned_read_connections"][0]
    assert len(sql_trace["cancel_update_connections"]) == 1
    assert (
        sql_trace["cancel_update_connections"][0]
        != sql_trace["owned_read_connections"][0]
    )
    assert sql_trace["cancel_rowcounts"] == [0]
    assert sql_trace["commit_connections"] == [
        concurrent_connection_ids[0],
        sql_trace["cancel_update_connections"][0],
    ]
    assert_application_session_cleanup(
        owner_database,
        previous_session_count,
        expected_new_sessions=1,
    )

    expected = deepcopy(before)
    rows_removed_by_b = {
        "result_evidence": {"result-evidence-a"},
        "external_submissions": {"external-submission-a"},
        "execution_steps": {"step-a"},
        "credit_logs": {"credit-log-a"},
        "billing_units": {"billing-a"},
        "ai_tasks": {TASK_ID},
    }
    for table_name, removed_ids in rows_removed_by_b.items():
        expected[table_name] = [
            row for row in expected[table_name] if row["id"] not in removed_ids
        ]
    assert owner_database.snapshot(CONSERVATION_TABLES) == expected


def test_real_application_returns_cancelling_for_terminal_task_without_signal_or_writer(
    owner_database: OwnerDatabase,
) -> None:
    set_task_status(owner_database, "completed")
    before = owner_database.snapshot(CONSERVATION_TABLES)
    previous_session_count = len(owner_database.session_factory.created)
    writer_entries: list[str] = []

    @contextmanager
    def cancellation_transaction() -> Iterator[Connection]:
        writer_entries.append("entered")
        with owner_database.engine.begin() as connection:
            yield connection

    class UnexpectedSignalRegistry:
        def get(self, _task_id: str) -> None:
            raise AssertionError("terminal task must not query the signal registry")

    with capture_application_task_sql(owner_database.engine) as sql_trace:
        response = request_batch_optimize_cancel(
            task_factory(owner_database),
            TrustedActor("user-a"),
            TASK_ID,
            UnexpectedSignalRegistry(),
            SqlTaskCancellationWriter(cancellation_transaction),
        )

    assert response == {"task_id": TASK_ID, "status": "cancelling"}
    assert writer_entries == []
    assert sql_trace["cancel_update_connections"] == []
    assert owner_database.snapshot(CONSERVATION_TABLES) == before
    assert_application_session_cleanup(
        owner_database,
        previous_session_count,
        expected_new_sessions=1,
    )


def test_real_application_unknown_commit_is_seen_by_later_get_without_replay(
    owner_database: OwnerDatabase,
) -> None:
    before = owner_database.snapshot(CONSERVATION_TABLES)
    assert_full_nonempty_owner_history(before)
    events: list[str] = []
    signal = RecordingSignal(events)
    uow_factory = task_factory(owner_database)
    previous_session_count = len(owner_database.session_factory.created)

    @contextmanager
    def commit_then_lose_acknowledgement() -> Iterator[Connection]:
        events.append("writer.enter")
        with owner_database.engine.begin() as connection:
            yield connection
        events.append("writer.commit.completed")
        raise CommitAcknowledgementLost(
            "commit completed but acknowledgement was lost"
        )

    with capture_application_task_sql(owner_database.engine) as sql_trace:
        with pytest.raises(CommitAcknowledgementLost):
            request_batch_optimize_cancel(
                uow_factory,
                TrustedActor("user-a"),
                TASK_ID,
                RecordingRegistry(events, signal),
                SqlTaskCancellationWriter(commit_then_lose_acknowledgement),
            )

        assert events == [
            "signal.get",
            "signal.set",
            "writer.enter",
            "writer.commit.completed",
        ]
        assert len(sql_trace["cancel_update_connections"]) == 1
        assert sql_trace["cancel_rowcounts"] == [1]

        later_get = get_batch_optimize_status(
            uow_factory,
            TrustedActor("user-a"),
            TASK_ID,
        )

        assert later_get["status"] == "cancelling"
        assert len(sql_trace["cancel_update_connections"]) == 1

    assert_application_session_cleanup(
        owner_database,
        previous_session_count,
        expected_new_sessions=2,
    )
    assert_status_only_change(
        before,
        owner_database.snapshot(CONSERVATION_TABLES),
        "processing",
        "cancelling",
    )
