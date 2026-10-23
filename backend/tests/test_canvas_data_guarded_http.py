from __future__ import annotations

import asyncio

import httpx

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


def send(app, method: str, path: str, *, body=None):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://canvas.test",
        ) as client:
            return await client.request(method, path, headers={"x-test-user": "user-a"}, json=body)
    return asyncio.run(request())


def test_guarded_http_negotiates_receipt_and_preserves_legacy_wire(canvas_db: CanvasDatabase) -> None:
    app = make_app(canvas_db)
    path = "/api/chapters/chapter-a/canvas"
    legacy = send(app, "GET", path).json()
    assert "save_capability" not in legacy

    negotiated = send(app, "GET", path + "?save_mode=guarded-v1")
    assert negotiated.status_code == 200
    assert negotiated.json()["save_capability"] == "guarded-v1"
    assert negotiated.json()["id"] is None
    assert negotiated.json()["version"] == 1
    assert negotiated.json()["document_json"] == legacy["document_json"]

    saved = send(app, "PUT", path, body={
        "document_json": {"nodeLayouts": {"frame:0": {"x": 0, "y": 9}}, "unknown": {"kept": True}},
        "version": 1,
        "save_guard": {"mode": "guarded-v1", "id": None, "version": 1},
    })
    assert saved.status_code == 200
    assert saved.json()["version"] == saved.json()["save_receipt"]["version"] == 1
    assert saved.json()["id"] == saved.json()["save_receipt"]["id"]
    assert saved.json()["document_json"]["unknown"] == {"kept": True}

    invalid = send(app, "PUT", path, body={
        "document_json": {}, "version": "2",
        "save_guard": {"mode": "guarded-v1", "id": saved.json()["id"], "version": 1},
    })
    assert invalid.status_code == 422
    invalid = send(app, "PUT", path, body={
        "document_json": {},
        "save_guard": {"mode": "guarded-v1", "id": saved.json()["id"], "version": "1"},
    })
    assert invalid.status_code == 422
    invalid = send(app, "PUT", path, body={
        "document_json": {},
        "save_guard": {"mode": "guarded-v1", "id": saved.json()["id"], "version": 0},
    })
    assert invalid.status_code == 422
    invalid_first_version = send(app, "PUT", path, body={
        "document_json": {},
        "save_guard": {"mode": "guarded-v1", "id": None, "version": 7},
    })
    assert invalid_first_version.status_code == 422


def test_guarded_conflict_returns_409_without_changing_document(canvas_db: CanvasDatabase) -> None:
    app = make_app(canvas_db)
    path = "/api/chapters/chapter-a/canvas"
    first = send(app, "PUT", path, body={
        "document_json": {"value": "first"},
        "save_guard": {"mode": "guarded-v1", "id": None, "version": 1},
    })
    assert first.status_code == 200
    stale = send(app, "PUT", path, body={
        "document_json": {"value": "stale"},
        "save_guard": {"mode": "guarded-v1", "id": None, "version": 1},
    })
    assert stale.status_code == 409
    with canvas_db.session_factory() as session:
        row = session.execute(canvas_documents.select()).mappings().one()
    assert row["version"] == 1
    assert row["document_json"] == '{"value": "first"}'
