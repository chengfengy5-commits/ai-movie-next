from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest
from sqlalchemy import insert, select, update

from haoai_backend.app import create_app
from haoai_backend.canvas_data.tables import canvas_documents
from haoai_backend.shared.identity import TrustedActor
from test_canvas_data_persistence import CanvasDatabase, canvas_db


def make_app(db: CanvasDatabase):
    return create_app(
        session_factory=db.session_factory,
        resolve_actor=lambda request: TrustedActor(request.headers.get("x-test-user", "")),
        series_access_policy=lambda session, actor, series_id: None,
    )


def send(app, method: str, path: str, *, user: str = "user-a", body=None):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://canvas.test",
        ) as client:
            return await client.request(method, path, headers={"x-test-user": user}, json=body)
    return asyncio.run(request())


def test_http_get_put_conflict_denial_and_persisted_truth(canvas_db: CanvasDatabase) -> None:
    app = make_app(canvas_db)
    path = "/api/chapters/chapter-a/canvas"

    initial = send(app, "GET", path)
    assert initial.status_code == 200
    assert initial.json()["document_json"] == {
        "version": 1,
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "nodes": [], "edges": [], "notes": [],
    }
    assert initial.json()["id"] is None

    payload = {"document_json": {"nodes": [{"id": "n1"}]}, "version": "99", "extra": True}
    saved = send(app, "PUT", path, body=payload)
    assert saved.status_code == 200
    assert saved.json()["version"] == 1
    assert saved.json()["document_json"] == payload["document_json"]

    conflict = send(app, "PUT", path, body={"document_json": {}, "version": 99})
    assert conflict.status_code == 409
    denied = send(app, "GET", path, user="outsider")
    assert denied.status_code == 403
    missing = send(app, "GET", "/api/chapters/missing/canvas")
    assert missing.status_code == 404
    stored = send(app, "GET", path)
    assert stored.status_code == 200
    assert stored.json()["version"] == 1


def test_http_validation_limit_and_unwired_factory(canvas_db: CanvasDatabase) -> None:
    app = make_app(canvas_db)
    path = "/api/chapters/chapter-a/canvas"
    invalid = send(app, "PUT", path, body={"document_json": []})
    assert invalid.status_code == 422
    too_large = send(app, "PUT", path, body={"document_json": {"text": "界" * 1_000_001}})
    assert too_large.status_code == 400
    assert send(app, "GET", path).json()["id"] is None

    unwired = create_app()
    assert send(unwired, "GET", path).status_code == 503


def test_get_does_not_normalize_valid_non_object_stored_json(canvas_db: CanvasDatabase) -> None:
    now = datetime(2026, 1, 1)
    with canvas_db.engine.begin() as connection:
        connection.execute(
            insert(canvas_documents).values(
                id="doc-null", series_id="series-a", chapter_id="chapter-a", version=1,
                document_json="null", created_by="user-a", updated_by="user-a",
                created_at=now, updated_at=now,
            )
        )
    response = send(make_app(canvas_db), "GET", "/api/chapters/chapter-a/canvas")
    assert response.status_code == 500
