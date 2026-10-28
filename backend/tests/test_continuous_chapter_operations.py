from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from fastapi import FastAPI

from haoai_backend.app import create_app
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from haoai_backend.personal_production.rough_cut.tables import metadata as rough_metadata
from haoai_backend.series_data.continuous_operation_schema import apply_continuous_operation_migration
from haoai_backend.series_data.continuous_operations import execute_continuous_chapter_operation
from haoai_backend.series_data.continuous_operation_receipts import (
    continuous_source_snapshot_binding,
    operation_receipts,
)
from haoai_backend.series_data.tables import (
    chapter_locks,
    chapters,
    characters,
    metadata as series_metadata,
    series,
    team_members,
    teams,
)
from haoai_backend.series_data.http import build_series_data_router
from haoai_backend.series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from haoai_backend.series_data.continuous_operation_schemas import ContinuousChapterOperationRequest
from haoai_backend.shared.identity import TrustedActor


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'continuous-chapter-operations.sqlite'}", future=True)
    auth_metadata.create_all(engine)
    series_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    rough_metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(auth_users).values(
            id="user-a", username="作者", email="author@example.test", hashed_password="x",
            is_superuser=False, membership_type="free", created_at=datetime(2026, 1, 1),
            password_updated_at=None,
        ))
    apply_continuous_operation_migration(engine)
    sessions = sessionmaker(engine, expire_on_commit=False, future=True)
    yield engine, sessions
    engine.dispose()


def _app(sessions: sessionmaker[Session]):
    def resolve_actor(request) -> TrustedActor:
        if request.headers.get("x-test-user") != "user-a":
            raise AuthenticationError(401, "登录状态已失效")
        return TrustedActor(user_id="user-a")

    return create_app(session_factory=sessions, resolve_actor=resolve_actor)


def _request(
    app,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    raise_app_exceptions: bool = True,
):
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
            base_url="http://continuous-operations.test",
            headers={"x-test-user": "user-a"},
        ) as client:
            return await client.request(method, path, json=body)

    return asyncio.run(send())


def _seed_chapter(engine: Engine) -> list[dict[str, Any]]:
    now = datetime(2026, 1, 2, 3, 4, 5)
    original = [{
        "text": "打开时原文",
        "original_text": "打开时原文",
        "character": [],
        "scene": [],
        "prop": [],
        "storyboard": [],
        "unknown": {"keep": True, "ratio": 1e-7, "label": "雪だるま🧊"},
    }]
    with engine.begin() as connection:
        connection.execute(insert(series).values(
            id="series-a", user_id="user-a", name="测试剧集", description=None,
            image_url=None, style_prompt_id=None, team_id=None, claimed_by=None,
            claimed_at=None, created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-a", series_id="series-a", title="第一章",
            content=json.dumps(original, ensure_ascii=False), order=1,
            created_at=now, updated_at=now,
        ))
    return original


def _save_operation(original: list[dict[str, Any]], *, operation_id: str | None = None) -> tuple[str, dict[str, Any]]:
    operation_id = operation_id or str(uuid4())
    new_content = [{**original[0], "text": "保存后的文本"}]
    return operation_id, {
        "protocol_version": 1,
        "editor_session_id": str(uuid4()),
        "command": {
            "kind": "save-chapter",
            "content": new_content,
            "chapter_edit_guard": {
                "version": 1,
                "expected_chapter_id": "chapter-a",
                "expected_series_id": "series-a",
                "expected_order": 1,
                "expected_content": original,
                "expected_storyboards": [],
                "expected_lock_identity": None,
                "frame_sources": [{"kind": "retained", "source_index": 0, "storyboard_id": None}],
                "deleted_source_indexes": [],
            },
            "draft_frame_ids": ["draft-a"],
        },
    }


def test_receipt_capability_is_advertised_only_after_explicit_feature_migration(database) -> None:
    engine, sessions = database
    response = _request(_app(sessions), "GET", "/api/chapters/continuous-edit/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "features": {"chapter_edit_guard": 1, "continuous_edit_operation_receipt": 1},
    }


def test_source_snapshot_binding_matches_the_frontend_protocol_vector() -> None:
    assert continuous_source_snapshot_binding("0" * 64) == (
        "a7f2f0f42d531591f65cf4648baa3811789f950978ec41018c0c1cc8fcfcc948"
    )


def test_save_and_original_receipt_commit_together_and_same_operation_replays(database) -> None:
    engine, sessions = database
    original = _seed_chapter(engine)
    operation_id, body = _save_operation(original)
    path = f"/api/chapters/chapter-a/continuous-edit/operations/{operation_id}"
    app = _app(sessions)

    first = _request(app, "POST", path, body)
    replay = _request(app, "POST", path, body)

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json() == replay.json()
    assert first.json()["receipt"]["outcome"] == "committed"
    assert first.json()["receipt"]["command_kind"] == "save-chapter"
    assert first.json()["receipt"]["request_body_sha256"]
    exact_request_body = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    request_body_sha256 = hashlib.sha256(exact_request_body).hexdigest()
    assert first.json()["receipt"]["request_body_sha256"] == request_body_sha256
    assert first.json()["receipt"]["source_snapshot_digest"] == continuous_source_snapshot_binding(request_body_sha256)
    canonical_response_json = first.json()["receipt"]["canonical_response_json"]
    assert hashlib.sha256(canonical_response_json.encode("utf-8")).hexdigest() == first.json()["receipt"]["result_digest"]
    assert first.json()["result"]["content"][0]["text"] == "保存后的文本"
    assert first.json()["result"]["content"][0]["unknown"] == {
        "keep": True,
        "ratio": 1e-7,
        "label": "雪だるま🧊",
    }
    with sessions() as session:
        stored_content = session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one()
        receipts = session.execute(select(operation_receipts)).mappings().all()
    assert json.loads(stored_content)[0]["text"] == "保存后的文本"
    assert len(receipts) == 1


def test_operation_uuid_cannot_be_reused_for_a_different_exact_request(database) -> None:
    engine, sessions = database
    original = _seed_chapter(engine)
    operation_id, body = _save_operation(original)
    path = f"/api/chapters/chapter-a/continuous-edit/operations/{operation_id}"
    app = _app(sessions)
    first = _request(app, "POST", path, body)
    changed = json.loads(json.dumps(body))
    changed["command"]["content"][0]["text"] = "另一个请求"
    second = _request(app, "POST", path, changed)

    assert first.status_code == 200
    assert second.status_code == 409
    with sessions() as session:
        assert session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one()
        assert len(session.execute(select(operation_receipts)).all()) == 1


def test_asset_create_stores_exact_id_with_receipt_without_linking_the_chapter(database) -> None:
    engine, sessions = database
    original = _seed_chapter(engine)
    operation_id = str(uuid4())
    editor_session_id = str(uuid4())
    body = {
        "protocol_version": 1,
        "editor_session_id": editor_session_id,
        "command": {
            "kind": "create-character",
            "input": {"name": "夏明"},
            "target_draft_id": "draft-a",
            "draft_frame_ids": ["draft-a"],
            "source_context": {
                "version": 1,
                "expected_chapter_id": "chapter-a",
                "expected_series_id": "series-a",
                "expected_order": 1,
                "expected_content": original,
                "expected_storyboards": [],
                "expected_lock_identity": None,
                "frame_sources": [{"kind": "retained", "source_index": 0, "storyboard_id": None}],
                "deleted_source_indexes": [],
            },
        },
    }
    path = f"/api/chapters/chapter-a/continuous-edit/operations/{operation_id}"
    response = _request(_app(sessions), "POST", path, body)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    receipt = response.json()["receipt"]

    with sessions() as session:
        asset = session.execute(select(characters).where(characters.c.id == result["id"])).mappings().one()
        chapter_content = session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one()
        stored_receipt = session.execute(select(operation_receipts)).mappings().one()

    assert result["series_id"] == "series-a"
    assert receipt["outcome"] == "committed"
    assert receipt["target_draft_id"] == "draft-a"
    assert receipt["result_identity"]["asset_id"] == asset["id"]
    assert json.loads(chapter_content) == original
    assert stored_receipt["command_kind"] == "create-character"


def test_readonly_lookup_returns_original_rejected_receipt_without_replaying_write(database) -> None:
    engine, sessions = database
    original = _seed_chapter(engine)
    operation_id, body = _save_operation(original)
    body["command"]["chapter_edit_guard"]["expected_order"] = 7
    app = _app(sessions)
    path = f"/api/chapters/chapter-a/continuous-edit/operations/{operation_id}"

    rejected = _request(app, "POST", path, body)
    looked_up = _request(app, "GET", path)

    assert rejected.status_code == 409
    assert rejected.json()["receipt"]["outcome"] == "rejected"
    assert looked_up.status_code == 200
    assert looked_up.json()["receipt"]["operation_id"] == operation_id
    assert looked_up.json()["receipt"]["outcome"] == "rejected"
    with sessions() as session:
        assert json.loads(session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one()) == original
        assert len(session.execute(select(operation_receipts)).all()) == 1


def test_receipt_insert_failure_rolls_back_guarded_chapter_effect(database) -> None:
    engine, sessions = database
    original = _seed_chapter(engine)
    operation_id, body = _save_operation(original)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TRIGGER reject_operation_receipt BEFORE INSERT ON continuous_chapter_operation_receipts "
            "BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END"
        ))

    response = _request(
        _app(sessions), "POST",
        f"/api/chapters/chapter-a/continuous-edit/operations/{operation_id}", body,
        raise_app_exceptions=False,
    )

    assert response.status_code == 500
    with sessions() as session:
        stored_content = session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one()
        receipts = session.execute(select(operation_receipts)).all()
    assert json.loads(stored_content) == original
    assert receipts == []


def test_asset_stage_rejects_a_lock_that_expires_during_asset_creation(database) -> None:
    engine, sessions = database
    now = datetime(2026, 1, 2, 3, 4, 5)
    expires = now + timedelta(minutes=1)
    original = [{
        "text": "基线镜头", "original_text": "打开时原文", "character": [], "scene": [], "prop": [],
        "storyboard": [], "preview": None,
    }]
    with engine.begin() as connection:
        connection.execute(insert(teams).values(id="team-op", name="测试团队", owner_id="user-a"))
        connection.execute(insert(team_members).values(
            id="member-op", team_id="team-op", user_id="user-a", role="owner", permissions="[]",
        ))
        connection.execute(insert(series).values(
            id="series-op", user_id="user-a", name="团队剧集", description=None, image_url=None,
            style_prompt_id=None, team_id="team-op", claimed_by=None, claimed_at=None,
            created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-op", series_id="series-op", title="第一章", content=json.dumps(original), order=1,
            created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapter_locks).values(
            id="lock-op", chapter_id="chapter-op", user_id="user-a", username="作者",
            acquired_at=now, last_active_at=now, expires_at=expires,
        ))

    current_time = {"value": now}

    def new_asset_id() -> str:
        current_time["value"] = expires + timedelta(seconds=1)
        return "character-op-new"

    def factory():
        return SqlAlchemySeriesDataUnitOfWork(
            sessions(), now=lambda: current_time["value"], new_id=new_asset_id,
        )

    operation_id = "9a3a4482-14bc-45dd-9758-cff7982a15ae"
    editor_session_id = "f73afee7-80ce-49fc-9a1e-394b402c8a53"
    body = {
        "protocol_version": 1,
        "editor_session_id": editor_session_id,
        "command": {
            "kind": "create-character",
            "input": {"name": "过期时不得创建"},
            "target_draft_id": "draft-a",
            "draft_frame_ids": ["draft-a"],
            "source_context": {
                "version": 1,
                "expected_chapter_id": "chapter-op",
                "expected_series_id": "series-op",
                "expected_order": 1,
                "expected_content": original,
                "expected_storyboards": [],
                "expected_lock_identity": {"user_id": "user-a", "acquired_at": now.isoformat()},
                "frame_sources": [{"kind": "retained", "source_index": 0, "storyboard_id": None}],
                "deleted_source_indexes": [],
            },
        },
    }
    raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    request = ContinuousChapterOperationRequest.model_validate(body)

    result = execute_continuous_chapter_operation(
        factory,
        TrustedActor(user_id="user-a"),
        "chapter-op",
        operation_id,
        request,
        hashlib.sha256(raw_body.encode()).hexdigest(),
    )

    assert result.status_code == 409
    assert result.payload["receipt"]["outcome"] == "rejected"
    with sessions() as session:
        assert session.execute(select(characters.c.id).where(characters.c.series_id == "series-op")).all() == []
        lock = session.execute(select(chapter_locks).where(chapter_locks.c.id == "lock-op")).mappings().one()
        assert lock["expires_at"] == expires


def test_blocking_operation_uow_does_not_block_the_asgi_event_loop(database) -> None:
    engine, sessions = database
    original = _seed_chapter(engine)
    operation_id, body = _save_operation(original)
    entered = threading.Event()
    release = threading.Event()
    released_at: list[float] = []

    class BlockingUnitOfWork(SqlAlchemySeriesDataUnitOfWork):
        def begin_continuous_operation(self) -> None:
            entered.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test release was not signalled")
            super().begin_continuous_operation()

    def factory():
        return BlockingUnitOfWork(sessions())

    def resolve_actor(request) -> TrustedActor:
        if request.headers.get("x-test-user") != "user-a":
            raise AuthenticationError(401, "登录状态已失效")
        return TrustedActor(user_id="user-a")

    app = FastAPI()
    app.include_router(build_series_data_router(uow_factory=factory, resolve_actor=resolve_actor))

    def release_uow() -> None:
        if entered.wait(timeout=2):
            time.sleep(0.15)
            released_at.append(time.monotonic())
            release.set()

    releaser = threading.Thread(target=release_uow, daemon=True)
    releaser.start()

    async def send_and_beat() -> tuple[httpx.Response, float]:
        heartbeat_at: list[float] = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://continuous-operations.test",
            headers={"x-test-user": "user-a"},
        ) as client:
            request_task = asyncio.create_task(client.post(
                f"/api/chapters/chapter-a/continuous-edit/operations/{operation_id}", json=body,
            ))
            asyncio.get_running_loop().call_later(0.02, lambda: heartbeat_at.append(time.monotonic()))
            response = await request_task
            return response, heartbeat_at[0]

    response, heartbeat_at = asyncio.run(send_and_beat())
    releaser.join(timeout=2)

    assert response.status_code == 200, response.text
    assert entered.is_set()
    assert released_at
    assert heartbeat_at < released_at[0]
