from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import insert, select, update

from haoai_backend.canvas_data.application import save_chapter_canvas
from haoai_backend.canvas_data.domain import CanvasSaveGuard
from haoai_backend.canvas_data.errors import CanvasDataConflict
from haoai_backend.canvas_data.persistence import SqlAlchemyCanvasDataUnitOfWork
from haoai_backend.canvas_data.tables import canvas_documents
from haoai_backend.shared.identity import TrustedActor
from test_canvas_data_persistence import CanvasDatabase, canvas_db


def seed_document(db: CanvasDatabase, *, document_id: str = "canvas-a", version: int = 3) -> None:
    now = datetime(2026, 1, 2)
    with db.engine.begin() as connection:
        connection.execute(insert(canvas_documents).values(
            id=document_id, series_id="series-a", chapter_id="chapter-a", version=version,
            document_json='{"value":"base"}', created_by="user-a", updated_by="user-a",
            created_at=now, updated_at=now,
        ))


def factory(db: CanvasDatabase):
    return lambda: SqlAlchemyCanvasDataUnitOfWork(db.session_factory())


def test_guarded_update_is_atomic_and_stale_writer_conflicts(canvas_db: CanvasDatabase) -> None:
    seed_document(canvas_db)
    first = save_chapter_canvas(
        factory(canvas_db), TrustedActor("user-a"), "chapter-a", {"value": "first"}, 3,
        CanvasSaveGuard("guarded-v1", "canvas-a", 3),
    )
    assert first["id"] == "canvas-a"
    assert first["version"] == 4
    assert first["document_json"] == {"value": "first"}
    assert first["updated_by"] == "user-a"
    assert first["save_receipt"] == {"mode": "guarded-v1", "id": "canvas-a", "version": 4}

    with pytest.raises(CanvasDataConflict):
        save_chapter_canvas(
            factory(canvas_db), TrustedActor("user-b"), "chapter-a", {"value": "stale"}, 3,
            CanvasSaveGuard("guarded-v1", "canvas-a", 3),
        )
    with canvas_db.session_factory() as session:
        stored = session.execute(select(canvas_documents)).mappings().one()
    assert stored["version"] == 4
    assert stored["document_json"] == '{"value": "first"}'


def test_guarded_absent_insert_and_recreated_document_identity(canvas_db: CanvasDatabase) -> None:
    created = save_chapter_canvas(
        factory(canvas_db), TrustedActor("user-a"), "chapter-a", {"value": 1}, 1,
        CanvasSaveGuard("guarded-v1", None, 1),
    )
    assert created["version"] == 1
    with pytest.raises(CanvasDataConflict):
        save_chapter_canvas(
            factory(canvas_db), TrustedActor("user-b"), "chapter-a", {"value": 2}, 1,
            CanvasSaveGuard("guarded-v1", None, 1),
        )

    with canvas_db.engine.begin() as connection:
        connection.execute(canvas_documents.delete().where(canvas_documents.c.chapter_id == "chapter-a"))
        connection.execute(insert(canvas_documents).values(
            id="canvas-rebuilt", series_id="series-a", chapter_id="chapter-a", version=1,
            document_json='{"value":"rebuilt"}', created_by="user-a", updated_by="user-a",
            created_at=datetime(2026, 1, 3), updated_at=datetime(2026, 1, 3),
        ))
    with pytest.raises(CanvasDataConflict):
        save_chapter_canvas(
            factory(canvas_db), TrustedActor("user-a"), "chapter-a", {"value": "stale"}, 1,
            CanvasSaveGuard("guarded-v1", created["id"], 1),
        )
    with canvas_db.session_factory() as session:
        stored = session.execute(select(canvas_documents)).mappings().one()
    assert stored["id"] == "canvas-rebuilt"
    assert stored["document_json"] == '{"value":"rebuilt"}'


def test_guarded_receipt_uses_returning_row_not_peer_postcommit_update(canvas_db: CanvasDatabase) -> None:
    seed_document(canvas_db)

    class PeerWritesAfterCommit(SqlAlchemyCanvasDataUnitOfWork):
        def commit(self) -> None:
            super().commit()
            with canvas_db.engine.begin() as connection:
                connection.execute(update(canvas_documents).where(
                    canvas_documents.c.id == "canvas-a"
                ).values(version=8, document_json='{"value":"peer"}', updated_by="user-b"))

    result = save_chapter_canvas(
        lambda: PeerWritesAfterCommit(canvas_db.session_factory()),
        TrustedActor("user-a"), "chapter-a", {"value": "mine"}, 3,
        CanvasSaveGuard("guarded-v1", "canvas-a", 3),
    )
    assert result["version"] == 4
    assert result["updated_by"] == "user-a"
    assert result["document_json"] == {"value": "mine"}
    assert result["save_receipt"]["version"] == 4

    with canvas_db.session_factory() as session:
        stored = session.execute(select(canvas_documents)).mappings().one()
    assert stored["version"] == 8
    assert stored["document_json"] == '{"value":"peer"}'
