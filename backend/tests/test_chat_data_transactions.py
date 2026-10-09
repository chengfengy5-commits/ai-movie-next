from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from haoai_backend.chat_data import application
from haoai_backend.chat_data.domain import NewChatMessage
from haoai_backend.chat_data.persistence import SqlAlchemyChatDataUnitOfWork
from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.shared.identity import TrustedActor
from test_chat_data_persistence import ChatDatabase, create_database


@pytest.fixture
def database(tmp_path: Path):
    database = create_database(tmp_path / "chat-transactions.sqlite")
    yield database
    database.engine.dispose()


def _driver_connection(session: Session) -> int:
    return id(session.connection().connection.driver_connection)


def test_update_after_concurrent_delete_fails_on_real_zero_row_update(database: ChatDatabase) -> None:
    database.seed_message("message-race", content="before")
    first = database.session_factory()
    second = database.session_factory()
    first_connection = _driver_connection(first)
    second_connection = _driver_connection(second)
    assert first_connection != second_connection

    uow = SqlAlchemyChatDataUnitOfWork(first)
    loaded = uow.load_chat_message("message-race", "chapter-a")
    assert loaded is not None
    second.execute(delete(chat_messages).where(chat_messages.c.id == "message-race"))
    second.commit()

    with pytest.raises(StaleDataError):
        uow.update_chat_message_content(
            "message-race",
            "chapter-a",
            "replacement",
        )

    uow.rollback()
    uow.close()
    second.close()
    with database.session_factory() as session:
        assert session.execute(select(chat_messages)).all() == []


def test_loaded_single_delete_that_later_matches_zero_still_commits_204_behavior(
    database: ChatDatabase,
) -> None:
    database.seed_message("message-single-delete")
    state: dict[str, Any] = {"connections": [], "commits": 0}

    class DeleteBetweenReadAndWrite(SqlAlchemyChatDataUnitOfWork):
        def load_chat_message(self, message_id: str, chapter_id: str):
            record = super().load_chat_message(message_id, chapter_id)
            if record is not None and not state["connections"]:
                state["connections"].append(_driver_connection(self._session))
                with database.session_factory() as other:
                    state["connections"].append(_driver_connection(other))
                    other.execute(
                        delete(chat_messages).where(chat_messages.c.id == message_id)
                    )
                    other.commit()
            return record

        def commit(self) -> None:
            state["commits"] += 1
            super().commit()

    application.delete_single_chat_message(
        lambda: DeleteBetweenReadAndWrite(database.session_factory()),
        TrustedActor("user-a"),
        "chapter-a",
        "message-single-delete",
    )

    assert len(state["connections"]) == 2
    assert state["connections"][0] != state["connections"][1]
    assert state["commits"] == 1
    with database.session_factory() as session:
        assert session.execute(select(chat_messages)).all() == []


def test_precommit_failure_rolls_back_real_insert_and_postcommit_ack_failure_is_unknown(
    database: ChatDatabase,
) -> None:
    from datetime import datetime, timedelta
    from sqlalchemy import insert, select
    from haoai_backend.chat_data.tables import chapter_locks

    now = datetime(2026, 10, 9, 12, 0)
    expired = now - timedelta(days=1)
    with database.engine.begin() as connection:
        connection.execute(
            insert(chapter_locks).values(
                id="fault-lock",
                chapter_id="chapter-a",
                user_id="user-a",
                username="Alice",
                acquired_at=expired,
                last_active_at=expired,
                expires_at=expired,
            )
        )
    with database.session_factory() as session:
        lock_before = session.execute(
            select(chapter_locks).where(chapter_locks.c.id == "fault-lock")
        ).mappings().one()

    request = NewChatMessage(
        chapter_id="chapter-a",
        frame_index=None,
        asset_type=None,
        asset_id=None,
        chat_mode="chat",
        role="user",
        content="durable candidate",
        model_name=None,
    )
    statements: list[str] = []
    event.listen(
        database.engine,
        "before_cursor_execute",
        lambda connection, cursor, statement, parameters, context, executemany: statements.append(statement.lower()),
    )

    class FailBeforeCommit(SqlAlchemyChatDataUnitOfWork):
        def commit(self) -> None:
            state["before_attempts"] += 1
            raise RuntimeError("commit rejected before durability")

    state = {"before_attempts": 0}
    with pytest.raises(RuntimeError, match="before durability"):
        application.create_chat_message(
            lambda: FailBeforeCommit(
                database.session_factory(),
                now=lambda: now,
                new_id=lambda: "message-before",
            ),
            TrustedActor("user-a"),
            "chapter-a",
            request,
        )
    assert state["before_attempts"] == 1
    assert any(statement.lstrip().startswith("insert into chat_messages") for statement in statements)
    with database.session_factory() as session:
        assert session.execute(select(chat_messages)).all() == []
        lock_after_rollback = session.execute(
            select(chapter_locks).where(chapter_locks.c.id == "fault-lock")
        ).mappings().one()
    assert dict(lock_after_rollback) == dict(lock_before)
    assert any(statement.lstrip().startswith("update chapter_locks") for statement in statements)

    class CommitThenLoseAck(SqlAlchemyChatDataUnitOfWork):
        def commit(self) -> None:
            state["after_attempts"] += 1
            super().commit()
            raise RuntimeError("commit acknowledgement lost")

    state["after_attempts"] = 0
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        application.create_chat_message(
            lambda: CommitThenLoseAck(
                database.session_factory(),
                now=lambda: now,
                new_id=lambda: "message-after",
            ),
            TrustedActor("user-a"),
            "chapter-a",
            request,
        )
    assert state["after_attempts"] == 1

    confirmed = application.get_chat_messages(
        lambda: SqlAlchemyChatDataUnitOfWork(database.session_factory()),
        TrustedActor("user-a"),
        "chapter-a",
    )
    assert [item["id"] for item in confirmed] == ["message-after"]
    with database.session_factory() as session:
        lock_after_commit = session.execute(
            select(chapter_locks).where(chapter_locks.c.id == "fault-lock")
        ).mappings().one()
    assert lock_after_commit["last_active_at"] == now
    assert lock_after_commit["expires_at"] == now + timedelta(minutes=15)



def test_same_content_update_commits_once_and_reads_the_committed_row(
    database: ChatDatabase,
) -> None:
    database.seed_message("message-noop", content="unchanged")
    statements: list[str] = []
    state = {"commits": 0}

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    class CountingUnitOfWork(SqlAlchemyChatDataUnitOfWork):
        def commit(self) -> None:
            state["commits"] += 1
            super().commit()

    event.listen(database.engine, "before_cursor_execute", observe_sql)
    try:
        response = application.update_chat_message(
            lambda: CountingUnitOfWork(database.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
            "message-noop",
            "unchanged",
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", observe_sql)

    assert response["content"] == "unchanged"
    assert state["commits"] == 1
    assert not any(statement.lstrip().startswith("update chat_messages") for statement in statements)
    assert sum(statement.lstrip().startswith("select chat_messages") for statement in statements) == 2


def test_changed_content_updates_only_the_content_column(database: ChatDatabase) -> None:
    database.seed_message("message-content", content="before")
    statements: list[tuple[str, Any]] = []

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith("update chat_messages"):
            statements.append((statement.lower(), parameters))

    event.listen(database.engine, "before_cursor_execute", observe_sql)
    try:
        result = application.update_chat_message(
            lambda: SqlAlchemyChatDataUnitOfWork(database.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
            "message-content",
            "after",
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", observe_sql)

    assert result["content"] == "after"
    assert len(statements) == 1
    statement, parameters = statements[0]
    set_clause = statement.split(" set ", 1)[1].split(" where ", 1)[0]
    assert set_clause == "content=?"
    assert "after" in str(parameters)


def test_create_inserts_before_refreshing_only_an_existing_owned_lock(
    database: ChatDatabase,
) -> None:
    from datetime import datetime, timedelta
    from sqlalchemy import insert, select
    from haoai_backend.chat_data.tables import chapter_locks

    now = datetime(2026, 10, 9, 12, 0)
    expired = now - timedelta(days=1)
    with database.engine.begin() as connection:
        connection.execute(
            insert(chapter_locks).values(
                id="owned-lock",
                chapter_id="chapter-a",
                user_id="user-a",
                username="Alice",
                acquired_at=expired,
                last_active_at=expired,
                expires_at=expired,
            )
        )

    statements: list[str] = []
    state = {"commits": 0}

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    class CountingUnitOfWork(SqlAlchemyChatDataUnitOfWork):
        def commit(self) -> None:
            state["commits"] += 1
            super().commit()

    request = NewChatMessage(
        chapter_id="chapter-a",
        frame_index=None,
        asset_type=None,
        asset_id=None,
        chat_mode="chat",
        role="assistant",
        content="created",
        model_name=None,
    )
    event.listen(database.engine, "before_cursor_execute", observe_sql)
    try:
        created = application.create_chat_message(
            lambda: CountingUnitOfWork(
                database.session_factory(),
                now=lambda: now,
                new_id=lambda: "message-created",
            ),
            TrustedActor("user-a"),
            "chapter-a",
            request,
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", observe_sql)

    insert_index = next(
        index for index, statement in enumerate(statements)
        if statement.lstrip().startswith("insert into chat_messages")
    )
    lock_update_index = next(
        index for index, statement in enumerate(statements)
        if statement.lstrip().startswith("update chapter_locks")
    )
    assert insert_index < lock_update_index
    assert state["commits"] == 1
    assert created["id"] == "message-created"
    with database.session_factory() as session:
        lock = session.execute(
            select(chapter_locks).where(chapter_locks.c.id == "owned-lock")
        ).mappings().one()
    assert lock["acquired_at"] == expired
    assert lock["last_active_at"] == now



def test_deleted_lock_between_read_and_update_fails_on_real_zero_row_update(
    database: ChatDatabase,
) -> None:
    from datetime import datetime, timedelta
    from sqlalchemy import insert
    from sqlalchemy.orm import sessionmaker
    from haoai_backend.chat_data.tables import chapter_locks

    now = datetime(2026, 10, 9, 12, 0)
    expired = now - timedelta(days=1)
    with database.engine.begin() as connection:
        connection.execute(
            insert(chapter_locks).values(
                id="lock-race",
                chapter_id="chapter-a",
                user_id="user-a",
                username="Alice",
                acquired_at=expired,
                last_active_at=expired,
                expires_at=expired,
            )
        )

    state: dict[str, Any] = {"connection_ids": [], "deleted": False}
    competing_factory = sessionmaker(bind=database.engine, future=True)

    class FetchedResult:
        def __init__(self, row: Any) -> None:
            self._row = row

        def mappings(self):
            return self

        def first(self):
            return self._row

    class DeleteLockAfterRead(Session):
        def execute(self, statement, *args, **kwargs):
            result = super().execute(statement, *args, **kwargs)
            normalized = str(statement).lower()
            if (
                not state["deleted"]
                and normalized.lstrip().startswith("select")
                and "chapter_locks" in normalized
            ):
                row = result.mappings().first()
                state["connection_ids"].append(_driver_connection(self))
                state["deleted"] = True
                with competing_factory() as other:
                    state["connection_ids"].append(_driver_connection(other))
                    other.execute(delete(chapter_locks).where(chapter_locks.c.id == "lock-race"))
                    other.commit()
                return FetchedResult(row)
            return result

    race_factory = sessionmaker(
        bind=database.engine,
        class_=DeleteLockAfterRead,
        future=True,
    )
    session = race_factory()
    try:
        uow = SqlAlchemyChatDataUnitOfWork(session, now=lambda: now)
        with pytest.raises(StaleDataError, match="chapter lock disappeared"):
            uow.refresh_chapter_lock("chapter-a", "user-a")
        uow.rollback()
        uow.close()
    finally:
        session.close()

    assert state["deleted"] is True
    assert len(state["connection_ids"]) == 2
    assert state["connection_ids"][0] != state["connection_ids"][1]



def test_create_rolls_back_when_owned_lock_disappears_after_read(
    database: ChatDatabase,
) -> None:
    from datetime import datetime, timedelta
    from sqlalchemy import insert
    from sqlalchemy.orm import sessionmaker
    from haoai_backend.chat_data.tables import chapter_locks

    now = datetime(2026, 10, 9, 12, 0)
    expired = now - timedelta(days=1)
    with database.engine.begin() as connection:
        connection.execute(
            insert(chapter_locks).values(
                id="create-lock-race",
                chapter_id="chapter-a",
                user_id="user-a",
                username="Alice",
                acquired_at=expired,
                last_active_at=expired,
                expires_at=expired,
            )
        )

    state = {"deleted": False}
    factory = sessionmaker(bind=database.engine, future=True)

    class FetchedResult:
        def __init__(self, row: Any) -> None:
            self._row = row

        def mappings(self):
            return self

        def first(self):
            return self._row

    class DeleteLockInCreateSession(Session):
        def execute(self, statement, *args, **kwargs):
            result = super().execute(statement, *args, **kwargs)
            normalized = str(statement).lower()
            if (
                not state["deleted"]
                and normalized.lstrip().startswith("select")
                and "chapter_locks" in normalized
            ):
                row = result.mappings().first()
                state["deleted"] = True
                super().execute(
                    delete(chapter_locks).where(
                        chapter_locks.c.id == "create-lock-race"
                    )
                )
                return FetchedResult(row)
            return result

    race_factory = sessionmaker(
        bind=database.engine,
        class_=DeleteLockInCreateSession,
        future=True,
    )
    request = NewChatMessage(
        chapter_id="chapter-a",
        frame_index=None,
        asset_type=None,
        asset_id=None,
        chat_mode="chat",
        role="user",
        content="must roll back",
        model_name=None,
    )
    with pytest.raises(StaleDataError, match="chapter lock disappeared"):
        application.create_chat_message(
            lambda: SqlAlchemyChatDataUnitOfWork(
                race_factory(),
                now=lambda: now,
                new_id=lambda: "message-lock-race",
            ),
            TrustedActor("user-a"),
            "chapter-a",
            request,
        )

    assert state["deleted"] is True
    with factory() as session:
        assert session.execute(select(chat_messages)).all() == []
        lock = session.execute(
            select(chapter_locks).where(chapter_locks.c.id == "create-lock-race")
        ).mappings().one()
    assert lock["last_active_at"] == expired
    assert lock["expires_at"] == expired


def test_same_content_readback_uses_latest_row_from_a_second_physical_connection(
    database: ChatDatabase,
) -> None:
    database.seed_message("message-latest", content="unchanged")
    other_connection = database.engine.connect()
    other_connection_id = id(other_connection.connection.driver_connection)
    state: dict[str, Any] = {
        "request_connection_id": None,
        "events": [],
        "unit_commits": 0,
        "other_commits": 0,
    }

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.lower().split())
        connection_id = id(connection.connection.driver_connection)
        if (
            state["request_connection_id"] is None
            and connection_id != other_connection_id
        ):
            state["request_connection_id"] = connection_id
        if connection_id in {other_connection_id, state["request_connection_id"]}:
            state["events"].append(("sql", connection_id, normalized))

    class ChangeAfterCommit(SqlAlchemyChatDataUnitOfWork):
        def commit(self) -> None:
            state["unit_commits"] += 1
            super().commit()
            state["events"].append(("unit-commit", None, ""))
            other_connection.execute(
                update(chat_messages)
                .where(chat_messages.c.id == "message-latest")
                .values(content="written-by-second-connection")
            )
            other_connection.commit()
            state["other_commits"] += 1

    event.listen(database.engine, "before_cursor_execute", observe_sql)
    try:
        response = application.update_chat_message(
            lambda: ChangeAfterCommit(database.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
            "message-latest",
            "unchanged",
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", observe_sql)
        other_connection.close()

    assert state["request_connection_id"] is not None
    assert state["request_connection_id"] != other_connection_id
    assert state["unit_commits"] == 1
    assert state["other_commits"] == 1
    assert response["content"] == "written-by-second-connection"

    request_events = [
        event_row
        for event_row in state["events"]
        if event_row[0] == "sql" and event_row[1] == state["request_connection_id"]
    ]
    message_selects = [
        event_row for event_row in request_events
        if event_row[2].startswith("select chat_messages")
    ]
    message_updates = [
        event_row for event_row in request_events
        if event_row[2].startswith("update chat_messages")
    ]
    assert len(message_selects) == 2
    assert message_updates == []

    commit_index = next(
        index for index, event_row in enumerate(state["events"])
        if event_row[0] == "unit-commit"
    )
    other_update_index = next(
        index for index, event_row in enumerate(state["events"])
        if event_row[0] == "sql"
        and event_row[1] == other_connection_id
        and event_row[2].startswith("update chat_messages")
    )
    readback_index = max(
        index for index, event_row in enumerate(state["events"])
        if event_row[0] == "sql"
        and event_row[1] == state["request_connection_id"]
        and event_row[2].startswith("select chat_messages")
    )
    assert commit_index < other_update_index < readback_index


def test_real_postcommit_readback_sql_error_is_unknown_and_not_replayed(
    database: ChatDatabase,
) -> None:
    from datetime import datetime

    state: dict[str, Any] = {
        "committed": False,
        "commit_attempts": 0,
        "readback_attempts": 0,
        "denied_reads": 0,
        "driver_connection": None,
        "authorizer_installed": False,
        "message_inserts": 0,
        "message_selects": [],
    }

    def authorizer(action, arg1, arg2, database_name, source):
        if (
            state["committed"]
            and action == sqlite3.SQLITE_READ
            and arg1 == "chat_messages"
        ):
            state["denied_reads"] += 1
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    class DenyCommittedReadback(Session):
        def execute(self, statement, *args, **kwargs):
            normalized = " ".join(str(statement).lower().split())
            if state["committed"] and "from chat_messages" in normalized:
                state["readback_attempts"] += 1
                driver = self.connection().connection.driver_connection
                driver.set_authorizer(authorizer)
                state["driver_connection"] = driver
                state["authorizer_installed"] = True
            result = super().execute(statement, *args, **kwargs)
            if not state["authorizer_installed"]:
                driver = self.connection().connection.driver_connection
                driver.set_authorizer(authorizer)
                state["driver_connection"] = driver
                state["authorizer_installed"] = True
            return result

        def commit(self) -> None:
            super().commit()
            state["commit_attempts"] += 1
            state["committed"] = True

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("insert into chat_messages"):
            state["message_inserts"] += 1
        if normalized.startswith("select chat_messages"):
            state["message_selects"].append(normalized)

    factory = sessionmaker(
        bind=database.engine,
        class_=DenyCommittedReadback,
        expire_on_commit=False,
        future=True,
    )
    request = NewChatMessage(
        chapter_id="chapter-a",
        frame_index=None,
        asset_type=None,
        asset_id=None,
        chat_mode="chat",
        role="user",
        content="committed before readback error",
        model_name=None,
    )

    event.listen(database.engine, "before_cursor_execute", observe_sql)
    try:
        with pytest.raises(DatabaseError):
            application.create_chat_message(
                lambda: SqlAlchemyChatDataUnitOfWork(
                    factory(),
                    now=lambda: datetime(2026, 10, 9, 12, 0),
                    new_id=lambda: "message-readback-fault",
                ),
                TrustedActor("user-a"),
                "chapter-a",
                request,
            )
    finally:
        event.remove(database.engine, "before_cursor_execute", observe_sql)
        if state["driver_connection"] is not None:
            state["driver_connection"].set_authorizer(None)

    assert state["commit_attempts"] == 1
    assert state["message_inserts"] == 1
    assert state["readback_attempts"] == 1
    assert state["denied_reads"] > 0
    assert len(state["message_selects"]) == 1

    confirmed = application.get_chat_messages(
        lambda: SqlAlchemyChatDataUnitOfWork(database.session_factory()),
        TrustedActor("user-a"),
        "chapter-a",
    )
    assert [row["id"] for row in confirmed] == ["message-readback-fault"]
    assert confirmed[0]["content"] == "committed before readback error"
