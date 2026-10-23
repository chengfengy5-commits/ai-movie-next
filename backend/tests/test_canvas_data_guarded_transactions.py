from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import insert, select

from haoai_backend.canvas_data.application import save_chapter_canvas
from haoai_backend.canvas_data.domain import CanvasSaveGuard
from haoai_backend.canvas_data.errors import CanvasDataConflict
from haoai_backend.canvas_data.persistence import SqlAlchemyCanvasDataUnitOfWork
from haoai_backend.canvas_data.tables import canvas_documents
from haoai_backend.shared.identity import TrustedActor
from test_canvas_data_persistence import CanvasDatabase, canvas_db


def test_two_physical_connections_accept_only_one_expected_version(canvas_db: CanvasDatabase) -> None:
    now = datetime(2026, 1, 2)
    with canvas_db.engine.begin() as connection:
        connection.execute(insert(canvas_documents).values(
            id="canvas-a", series_id="series-a", chapter_id="chapter-a", version=3,
            document_json='{"value":"base"}', created_by="user-a", updated_by="user-a",
            created_at=now, updated_at=now,
        ))

    first_connection = canvas_db.engine.connect()
    second_connection = canvas_db.engine.connect()
    assert first_connection.connection.driver_connection is not second_connection.connection.driver_connection
    first_session = canvas_db.session_factory(bind=first_connection)
    second_session = canvas_db.session_factory(bind=second_connection)
    try:
        first = save_chapter_canvas(
            lambda: SqlAlchemyCanvasDataUnitOfWork(first_session), TrustedActor("user-a"),
            "chapter-a", {"value": "winner"}, 3, CanvasSaveGuard("guarded-v1", "canvas-a", 3),
        )
        assert first["version"] == 4
        with pytest.raises(CanvasDataConflict):
            save_chapter_canvas(
                lambda: SqlAlchemyCanvasDataUnitOfWork(second_session), TrustedActor("user-b"),
                "chapter-a", {"value": "loser"}, 3, CanvasSaveGuard("guarded-v1", "canvas-a", 3),
            )
    finally:
        first_session.close()
        second_session.close()
        first_connection.close()
        second_connection.close()

    with canvas_db.session_factory() as session:
        rows = session.execute(select(canvas_documents)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["version"] == 4
    assert rows[0]["document_json"] == '{"value": "winner"}'


def test_guarded_write_failure_before_commit_rolls_back_document(canvas_db: CanvasDatabase) -> None:
    class CommitUnavailable(SqlAlchemyCanvasDataUnitOfWork):
        def commit(self) -> None:
            raise RuntimeError("commit unavailable")

    with pytest.raises(RuntimeError, match="commit unavailable"):
        save_chapter_canvas(
            lambda: CommitUnavailable(canvas_db.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
            {"value": "not committed"},
            1,
            CanvasSaveGuard("guarded-v1", None, 1),
        )

    with canvas_db.session_factory() as session:
        assert session.execute(select(canvas_documents)).mappings().all() == []


def test_guarded_commit_acknowledgement_loss_is_unknown_without_retry(canvas_db: CanvasDatabase) -> None:
    calls = {"commits": 0}

    class LostCommitAck(SqlAlchemyCanvasDataUnitOfWork):
        def commit(self) -> None:
            calls["commits"] += 1
            super().commit()
            raise RuntimeError("guarded commit acknowledgement lost")

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        save_chapter_canvas(
            lambda: LostCommitAck(canvas_db.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
            {"value": "committed once"},
            1,
            CanvasSaveGuard("guarded-v1", None, 1),
        )

    assert calls["commits"] == 1
    with canvas_db.session_factory() as session:
        row = session.execute(select(canvas_documents)).mappings().one()
    assert row["version"] == 1
    assert row["document_json"] == '{"value": "committed once"}'
