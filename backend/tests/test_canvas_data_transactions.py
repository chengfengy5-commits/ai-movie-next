from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import delete, event, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.canvas_data.application import get_chapter_canvas, save_chapter_canvas
from haoai_backend.canvas_data.persistence import SqlAlchemyCanvasDataUnitOfWork
from haoai_backend.canvas_data.tables import canvas_documents
from haoai_backend.shared.identity import TrustedActor
from test_canvas_data_persistence import CanvasDatabase, canvas_db, chapter_locks


def seed_document(db: CanvasDatabase, *, version: int = 1, document_json: str = '{"value":"stored"}') -> None:
    now = datetime(2026, 1, 2)
    with db.engine.begin() as connection:
        connection.execute(
            insert(canvas_documents).values(
                id="doc-a", series_id="series-a", chapter_id="chapter-a", version=version,
                document_json=document_json, created_by="user-a", updated_by="user-a",
                created_at=now, updated_at=now,
            )
        )


def test_two_physical_connections_reject_second_initial_document(canvas_db: CanvasDatabase) -> None:
    first_connection = canvas_db.engine.connect()
    second_connection = canvas_db.engine.connect()
    assert first_connection.connection.driver_connection is not second_connection.connection.driver_connection
    first = Session(bind=first_connection, expire_on_commit=False)
    second = Session(bind=second_connection, expire_on_commit=False)
    first_uow = SqlAlchemyCanvasDataUnitOfWork(first, new_id=lambda: "doc-first")
    second_uow = SqlAlchemyCanvasDataUnitOfWork(second, new_id=lambda: "doc-second")
    try:
        assert first_uow.load_canvas_document("chapter-a") is None
        assert second_uow.load_canvas_document("chapter-a") is None
        first.rollback()
        second.rollback()

        first_uow.insert_canvas_document(
            series_id="series-a", chapter_id="chapter-a", user_id="user-a", document_json='{"owner":"first"}'
        )
        first.commit()
        with pytest.raises(IntegrityError):
            second_uow.insert_canvas_document(
                series_id="series-a", chapter_id="chapter-a", user_id="user-b",
                document_json='{"owner":"second"}',
            )
        second.rollback()
        row = first.execute(select(canvas_documents)).mappings().one()
        assert row["id"] == "doc-first"
        assert row["document_json"] == '{"owner":"first"}'
    finally:
        first.close()
        second.close()
        first_connection.close()
        second_connection.close()


def test_real_update_zero_rows_is_not_reported_as_success(canvas_db: CanvasDatabase) -> None:
    seed_document(canvas_db)
    first_connection = canvas_db.engine.connect()
    second_connection = canvas_db.engine.connect()
    assert first_connection.connection.driver_connection is not second_connection.connection.driver_connection
    stale_session = Session(bind=first_connection, expire_on_commit=False)
    concurrent = Session(bind=second_connection, expire_on_commit=False)
    uow = SqlAlchemyCanvasDataUnitOfWork(stale_session)
    try:
        assert uow.load_canvas_document("chapter-a") is not None
        stale_session.rollback()
        concurrent.execute(delete(canvas_documents).where(canvas_documents.c.id == "doc-a"))
        concurrent.commit()
        with pytest.raises(RuntimeError, match="exactly one row"):
            uow.update_canvas_document(
                "doc-a",
                version=2,
                user_id="user-a",
                document_json='{"lost":true}',
                update_document_json=True,
                update_updated_by=True,
            )
    finally:
        stale_session.rollback()
        concurrent.close()
        stale_session.close()
        first_connection.close()
        second_connection.close()


def test_commit_ack_failure_is_unknown_and_explicit_get_confirms_one_write(canvas_db: CanvasDatabase) -> None:
    calls = {"commits": 0}

    preserved_before = canvas_db.snapshot_preserved_rows()

    class LostAck(SqlAlchemyCanvasDataUnitOfWork):
        def commit(self) -> None:
            calls["commits"] += 1
            super().commit()
            raise RuntimeError("commit acknowledgement lost")

    def factory():
        return LostAck(canvas_db.session_factory())

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        save_chapter_canvas(factory, TrustedActor("user-a"), "chapter-a", {"saved": True})
    assert calls["commits"] == 1
    assert canvas_db.snapshot_preserved_rows() == preserved_before
    stored = get_chapter_canvas(
        lambda: SqlAlchemyCanvasDataUnitOfWork(canvas_db.session_factory()),
        TrustedActor("user-a"),
        "chapter-a",
    )
    assert stored["version"] == 1
    assert stored["document_json"] == {"saved": True}


def test_commit_before_write_failure_rolls_back_canvas_and_lock(canvas_db: CanvasDatabase) -> None:
    now = datetime(2026, 3, 1)
    with canvas_db.engine.begin() as connection:
        connection.execute(
            insert(chapter_locks).values(
                id="lock-a", chapter_id="chapter-a", user_id="user-a", username="A",
                acquired_at=now, last_active_at=now, expires_at=now,
            )
        )

    preserved_before = canvas_db.snapshot_preserved_rows()

    class FailedBeforeCommit(SqlAlchemyCanvasDataUnitOfWork):
        def commit(self) -> None:
            raise RuntimeError("commit not reached")

    with pytest.raises(RuntimeError, match="commit not reached"):
        save_chapter_canvas(
            lambda: FailedBeforeCommit(canvas_db.session_factory(), now=lambda: datetime(2026, 4, 1)),
            TrustedActor("user-a"),
            "chapter-a",
            {"saved": True},
        )
    assert canvas_db.snapshot_preserved_rows() == preserved_before
    with canvas_db.session_factory() as session:
        assert session.execute(select(canvas_documents)).first() is None
        lock = session.execute(select(chapter_locks)).mappings().one()
        assert lock["last_active_at"] == now
        assert lock["expires_at"] == now


def test_refresh_uses_actual_postcommit_row_and_put_echoes_input(canvas_db: CanvasDatabase) -> None:
    seed_document(canvas_db, document_json='{"value":"old"}')
    other_connections: list[object] = []

    class ConcurrentUpdate(SqlAlchemyCanvasDataUnitOfWork):
        def commit(self) -> None:
            super().commit()
            with canvas_db.engine.begin() as connection:
                other_connections.append(connection.connection.driver_connection)
                connection.execute(
                    update(canvas_documents)
                    .where(canvas_documents.c.id == "doc-a")
                    .values(version=8, document_json='{"value":"other"}', updated_by="user-b")
                )

    result = save_chapter_canvas(
        lambda: ConcurrentUpdate(canvas_db.session_factory()),
        TrustedActor("user-a"),
        "chapter-a",
        {"value": "request"},
    )
    assert result["version"] == 8
    assert result["updated_by"] == "user-b"
    assert result["document_json"] == {"value": "request"}
    assert other_connections
    stored = get_chapter_canvas(
        lambda: SqlAlchemyCanvasDataUnitOfWork(canvas_db.session_factory()),
        TrustedActor("user-a"),
        "chapter-a",
    )
    assert stored["document_json"] == {"value": "other"}


def test_postcommit_readback_failure_is_unknown_and_does_not_replay(canvas_db: CanvasDatabase) -> None:
    calls = {"commits": 0, "refreshes": 0}

    preserved_before = canvas_db.snapshot_preserved_rows()

    class FailedReadback(SqlAlchemyCanvasDataUnitOfWork):
        def commit(self) -> None:
            calls["commits"] += 1
            super().commit()

        def refresh_canvas_document(self, document_id: str):
            calls["refreshes"] += 1
            super().refresh_canvas_document(document_id)
            raise RuntimeError("post-commit readback failed")

    with pytest.raises(RuntimeError, match="post-commit readback failed"):
        save_chapter_canvas(
            lambda: FailedReadback(canvas_db.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
            {"write": 1},
        )
    assert calls == {"commits": 1, "refreshes": 1}
    assert canvas_db.snapshot_preserved_rows() == preserved_before

    confirmed = get_chapter_canvas(
        lambda: SqlAlchemyCanvasDataUnitOfWork(canvas_db.session_factory()),
        TrustedActor("user-a"),
        "chapter-a",
    )
    assert confirmed["version"] == 1
    assert confirmed["document_json"] == {"write": 1}


def test_existing_same_content_put_still_updates_version_and_timestamp(canvas_db: CanvasDatabase) -> None:
    seed_document(canvas_db, version=4, document_json='{"same":true}')
    now = datetime(2026, 5, 1)
    result = save_chapter_canvas(
        lambda: SqlAlchemyCanvasDataUnitOfWork(canvas_db.session_factory(), now=lambda: now),
        TrustedActor("user-b"),
        "chapter-a",
        {"same": True},
        version=4,
    )
    assert result["version"] == 5
    assert result["updated_by"] == "user-b"
    assert result["updated_at"] == now
    with canvas_db.session_factory() as session:
        stored = session.execute(select(canvas_documents)).mappings().one()
        assert stored["version"] == 5
        assert stored["document_json"] == '{"same": true}'



def test_same_raw_document_snapshot_preserves_concurrent_document_and_actor(canvas_db: CanvasDatabase) -> None:
    seed_document(canvas_db, version=1, document_json='{"winner": "A"}')
    connection_a = canvas_db.engine.connect()
    connection_b = canvas_db.engine.connect()
    driver_a = connection_a.connection.driver_connection
    driver_b = connection_b.connection.driver_connection
    assert driver_a is not driver_b
    session_a = Session(bind=connection_a, expire_on_commit=False)
    session_b = Session(bind=connection_b, expire_on_commit=False)
    preserved_before = canvas_db.snapshot_preserved_rows()
    concurrent_update = {"completed": False}

    class SnapshotThenConcurrentUpdate(SqlAlchemyCanvasDataUnitOfWork):
        def load_canvas_document(self, chapter_id: str):
            record = super().load_canvas_document(chapter_id)
            if record is not None and not concurrent_update["completed"]:
                concurrent_update["completed"] = True
                SqlAlchemyCanvasDataUnitOfWork(session_b).update_canvas_document(
                    record.id,
                    version=2,
                    user_id="user-b",
                    document_json='{"winner":"B"}',
                    update_document_json=True,
                    update_updated_by=True,
                )
                session_b.commit()
            return record

    captured_updates: list[str] = []

    def capture_update(connection, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().upper().startswith("UPDATE CANVAS_DOCUMENTS"):
            captured_updates.append(statement)

    event.listen(canvas_db.engine, "before_cursor_execute", capture_update)
    try:
        result = save_chapter_canvas(
            lambda: SnapshotThenConcurrentUpdate(session_a),
            TrustedActor("user-a"),
            "chapter-a",
            {"winner": "A"},
            version=1,
        )
        assert concurrent_update["completed"] is True
        assert result["version"] == 2
        assert result["updated_by"] == "user-b"
        assert result["document_json"] == {"winner": "A"}
        assert captured_updates
        set_clause = captured_updates[-1].split(" WHERE ", 1)[0].split(" SET ", 1)[1]
        set_columns = {assignment.split("=", 1)[0].strip() for assignment in set_clause.split(",")}
        assert set_columns == {"version", "updated_at"}
        assert "version = ?" not in captured_updates[-1].split(" WHERE ", 1)[1]
        assert canvas_db.snapshot_preserved_rows() == preserved_before

        stored = get_chapter_canvas(
            lambda: SqlAlchemyCanvasDataUnitOfWork(canvas_db.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
        )
        assert stored["version"] == 2
        assert stored["updated_by"] == "user-b"
        assert stored["document_json"] == {"winner": "B"}
    finally:
        event.remove(canvas_db.engine, "before_cursor_execute", capture_update)
        session_a.close()
        session_b.close()
        connection_a.close()
        connection_b.close()


def test_two_connections_preserve_snapshot_based_overwrite_without_database_cas(canvas_db: CanvasDatabase) -> None:
    seed_document(canvas_db, version=1, document_json='{"winner":"initial"}')
    connection_a = canvas_db.engine.connect()
    connection_b = canvas_db.engine.connect()
    driver_a = connection_a.connection.driver_connection
    driver_b = connection_b.connection.driver_connection
    assert driver_a is not driver_b
    session_a = Session(bind=connection_a, expire_on_commit=False)
    session_b = Session(bind=connection_b, expire_on_commit=False)
    preserved_before = canvas_db.snapshot_preserved_rows()
    concurrent_update = {"completed": False}

    class SnapshotThenConcurrentUpdate(SqlAlchemyCanvasDataUnitOfWork):
        def load_canvas_document(self, chapter_id: str):
            record = super().load_canvas_document(chapter_id)
            if record is not None and not concurrent_update["completed"]:
                concurrent_update["completed"] = True
                second_uow = SqlAlchemyCanvasDataUnitOfWork(session_b)
                second_uow.update_canvas_document(
                    record.id,
                    version=2,
                    user_id="user-b",
                    document_json='{"winner":"B"}',
                    update_document_json=True,
                    update_updated_by=True,
                )
                session_b.commit()
            return record

    captured_updates: list[str] = []

    def capture_update(connection, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().upper().startswith("UPDATE CANVAS_DOCUMENTS"):
            captured_updates.append(statement)

    event.listen(canvas_db.engine, "before_cursor_execute", capture_update)
    try:
        result = save_chapter_canvas(
            lambda: SnapshotThenConcurrentUpdate(session_a),
            TrustedActor("user-a"),
            "chapter-a",
            {"winner": "A"},
            version=1,
        )
        assert concurrent_update["completed"] is True
        assert result["version"] == 2
        assert result["updated_by"] == "user-b"
        assert result["document_json"] == {"winner": "A"}
        assert captured_updates
        final_set_clause = captured_updates[-1].split(" WHERE ", 1)[0].split(" SET ", 1)[1]
        final_set_columns = {assignment.split("=", 1)[0].strip() for assignment in final_set_clause.split(",")}
        assert final_set_columns == {"version", "updated_at", "document_json"}
        assert "version = ?" not in captured_updates[-1].split(" WHERE ", 1)[1]
        assert canvas_db.snapshot_preserved_rows() == preserved_before

        stored = get_chapter_canvas(
            lambda: SqlAlchemyCanvasDataUnitOfWork(canvas_db.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
        )
        assert stored["version"] == 2
        assert stored["updated_by"] == "user-b"
        assert stored["document_json"] == {"winner": "A"}
    finally:
        event.remove(canvas_db.engine, "before_cursor_execute", capture_update)
        session_a.close()
        session_b.close()
        connection_a.close()
        connection_b.close()
