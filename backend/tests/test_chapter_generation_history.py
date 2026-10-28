"""Focused HTTP and transaction coverage for chapter generation history."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.chapter_generation_history import persistence
from haoai_backend.chapter_generation_history.domain import safe_generation_media_url
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_data.http import build_series_data_router
from haoai_backend.series_data.tables import (
    chapters,
    chat_messages,
    chapter_locks,
    metadata as series_metadata,
    series,
    storyboard_assets,
    system_configs,
    users,
)
from haoai_backend.shared.identity import TrustedActor


_NOW = datetime(2026, 10, 28, 1, 0, 0)
_ACTOR = TrustedActor("user-a")
_CHAPTER_ID = "chapter-a"
_SERIES_ID = "series-a"
_STORYBOARD_ID = "board-a"


class HistoryDatabase:
    def __init__(self, path: Path, actor: TrustedActor = _ACTOR) -> None:
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
        )
        series_metadata.create_all(
            self.engine,
            tables=[
                users,
                series,
                chapters,
                storyboard_assets,
                chat_messages,
                chapter_locks,
                system_configs,
            ],
        )
        notes_metadata.create_all(
            self.engine,
            tables=[storyboard_media_states, personal_production_notes],
        )
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        self.actor = actor
        self._seed()
        self.app = FastAPI()
        self.app.include_router(
            build_series_data_router(
                uow_factory=None,
                resolve_actor=lambda _request: self.actor,
                generation_session_factory=self.session_factory,
            )
        )

    def _seed(self) -> None:
        frames = [
            {
                "storyboard": [_STORYBOARD_ID, "secondary-board"],
                "text": "保留的镜头字段",
                "preview": "https://media.example.test/old-preview.mp4?sig=old",
            }
        ]
        with self.engine.begin() as connection:
            connection.execute(
                insert(users),
                [
                    {"id": "user-a", "username": "测试用户"},
                    {"id": "user-b", "username": "其他用户"},
                ],
            )
            connection.execute(
                insert(series).values(
                    id=_SERIES_ID,
                    user_id="user-a",
                    name="测试剧集",
                    description=None,
                    image_url=None,
                    style_prompt_id=None,
                    team_id=None,
                    claimed_by=None,
                    claimed_at=None,
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
            connection.execute(
                insert(chapters).values(
                    id=_CHAPTER_ID,
                    series_id=_SERIES_ID,
                    title="测试章节",
                    content=json.dumps(frames, ensure_ascii=False),
                    order=0,
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
            connection.execute(
                insert(storyboard_assets).values(
                    id=_STORYBOARD_ID,
                    series_id=_SERIES_ID,
                    chapter_id=_CHAPTER_ID,
                    frame_index=0,
                    name="测试分镜",
                    description="原描述",
                    image_url="https://media.example.test/old-image.png?sig=old",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
            messages = []
            for mode, suffix in (("image", "image"), ("video", "video")):
                messages.append(
                    {
                        "id": f"frame-{mode}",
                        "chapter_id": _CHAPTER_ID,
                        "frame_index": 0,
                        "asset_type": None,
                        "asset_id": None,
                        "chat_mode": mode,
                        "role": "assistant",
                        "content": f"https://media.example.test/{suffix}.mp4?sig=frame",
                        "model_name": "fixture-model",
                        "created_at": _NOW + timedelta(seconds=len(messages)),
                    }
                )
                messages.append(
                    {
                        "id": f"storyboard-{mode}",
                        "chapter_id": _CHAPTER_ID,
                        "frame_index": None,
                        "asset_type": "storyboard",
                        "asset_id": _STORYBOARD_ID,
                        "chat_mode": mode,
                        "role": "assistant",
                        "content": f"https://media.example.test/{suffix}.png?sig=board",
                        "model_name": "fixture-model",
                        "created_at": _NOW + timedelta(seconds=len(messages)),
                    }
                )
            connection.execute(insert(chat_messages), messages)

    def close(self) -> None:
        self.engine.dispose()


@pytest.fixture
def database(tmp_path: Path):
    current = HistoryDatabase(tmp_path / "history.sqlite")
    yield current
    current.close()


def _get_history(database: HistoryDatabase, target_kind: str, mode: str):
    params: dict[str, Any] = {
        "target_kind": target_kind,
        "frame_index": 0,
        "chat_mode": mode,
    }
    if target_kind == "storyboard-image":
        params["storyboard_asset_id"] = _STORYBOARD_ID
    return _request(
        database,
        "GET",
        f"/api/chapters/{_CHAPTER_ID}/generation-history",
        params=params,
    )


def _request(database: HistoryDatabase, method: str, path: str, **kwargs):
    async def send():
        async with AsyncClient(
            transport=ASGITransport(app=database.app),
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


@pytest.mark.parametrize("target_kind", ["frame-preview", "storyboard-image"])
@pytest.mark.parametrize("mode", ["image", "video"])
def test_history_reads_exact_source_tuple_and_mode(database, target_kind, mode):
    response = _get_history(database, target_kind, mode)

    assert response.status_code == 200
    payload = response.json()
    assert payload["series_id"] == _SERIES_ID
    assert payload["target_kind"] == target_kind
    assert payload["chat_mode"] == mode
    assert payload["writable"] is True
    assert payload["target_proof"]["mapping_digest"]
    assert payload["target_proof"]["media_url"].startswith("https://media.example.test/old-")
    assert [item["id"] for item in payload["messages"]] == [
        f"frame-{mode}" if target_kind == "frame-preview" else f"storyboard-{mode}"
    ]


def test_get_is_read_only_and_missing_stable_mapping_remains_browseable(database):
    before_states = _count(database, storyboard_media_states)
    before_notes = _count(database, personal_production_notes)
    with database.engine.begin() as connection:
        connection.execute(
            update(chapters)
            .where(chapters.c.id == _CHAPTER_ID)
            .values(content=json.dumps([{"storyboard": [], "preview": None}]))
        )

    response = _get_history(database, "frame-preview", "image")

    assert response.status_code == 200
    payload = response.json()
    assert payload["writable"] is False
    assert payload["read_only_reason"] == "missing_storyboard_id"
    assert payload["target_proof"] is not None
    assert [item["id"] for item in payload["messages"]] == ["frame-image"]
    assert _count(database, storyboard_media_states) == before_states == 0
    assert _count(database, personal_production_notes) == before_notes == 0


def test_read_rejects_an_actor_without_series_access(tmp_path):
    database = HistoryDatabase(tmp_path / "denied.sqlite", TrustedActor("user-b"))
    try:
        response = _get_history(database, "frame-preview", "image")

        assert response.status_code == 403
        assert response.json()["detail"] == "series_access_denied"
    finally:
        database.close()


def test_get_and_post_keep_native_validation_errors(database):
    invalid_query = _request(
        database,
        "GET",
        f"/api/chapters/{_CHAPTER_ID}/generation-history",
        params={"target_kind": "chapter", "frame_index": -1, "chat_mode": "audio"},
    )
    assert invalid_query.status_code == 422
    assert isinstance(invalid_query.json()["detail"], list)

    history = _get_history(database, "frame-preview", "image").json()
    body = _adopt_body("frame-preview", "image", history)
    body["replacement_url"] = "https://media.example.test/forbidden.mp4"
    invalid_body = _request(
        database,
        "POST",
        f"/api/chapters/{_CHAPTER_ID}/generation-history/adopt",
        json=body,
    )
    assert invalid_body.status_code == 422
    assert isinstance(invalid_body.json()["detail"], list)
    assert _count(database, storyboard_media_states) == 0


def test_stale_message_is_rejected_before_media_reconciliation(database):
    history = _get_history(database, "storyboard-image", "video").json()
    proof = history["target_proof"]
    body = _adopt_body("storyboard-image", "video", history)
    with database.engine.begin() as connection:
        connection.execute(
            update(chat_messages)
            .where(chat_messages.c.id == history["messages"][0]["id"])
            .values(content="https://media.example.test/changed.png")
        )

    response = _request(
        database,
        "POST",
        f"/api/chapters/{_CHAPTER_ID}/generation-history/adopt",
        json=body,
    )

    assert response.status_code == 409
    assert _count(database, storyboard_media_states) == 0
    with database.engine.connect() as connection:
        board_image = connection.execute(
            select(storyboard_assets.c.image_url).where(
                storyboard_assets.c.id == _STORYBOARD_ID
            )
        ).scalar_one()
    assert board_image == proof["media_url"]


@pytest.mark.parametrize("matches_current_lease", [True, False])
def test_team_adoption_requires_and_refreshes_the_exact_lock(matches_current_lease, database):
    with database.engine.begin() as connection:
        connection.execute(
            update(series).where(series.c.id == _SERIES_ID).values(team_id="team-a")
        )
        connection.execute(
            insert(chapter_locks).values(
                id="lock-a",
                chapter_id=_CHAPTER_ID,
                user_id="user-a",
                username="测试用户",
                acquired_at=_NOW,
                last_active_at=_NOW,
                expires_at=datetime(2099, 1, 1),
            )
        )

    history = _get_history(database, "frame-preview", "video").json()
    body = _adopt_body("frame-preview", "video", history)
    body["expected_lock_identity"] = {
        "user_id": "user-a",
        "acquired_at": (
            "2026-10-28T01:00:00Z"
            if matches_current_lease
            else "2026-10-28T01:00:01Z"
        ),
    }
    response = _request(
        database,
        "POST",
        f"/api/chapters/{_CHAPTER_ID}/generation-history/adopt",
        json=body,
    )

    if not matches_current_lease:
        assert response.status_code == 409
        assert _count(database, storyboard_media_states) == 0
        return

    assert response.status_code == 200, response.text
    with database.engine.connect() as connection:
        lock = connection.execute(
            select(chapter_locks).where(chapter_locks.c.id == "lock-a")
        ).mappings().one()
    assert lock["user_id"] == "user-a"
    assert lock["last_active_at"] > _NOW
    assert lock["expires_at"] > datetime.now(timezone.utc).replace(tzinfo=None)
    assert lock["expires_at"] != datetime(2099, 1, 1)


@pytest.mark.parametrize("expiration_point", ["lock-read", "final-message-read"])
def test_expiring_team_lease_is_not_refreshed_after_database_waits(
    expiration_point, database, monkeypatch
):
    deadline = _NOW + timedelta(minutes=1)
    before_expiry = deadline - timedelta(seconds=1)
    after_expiry = deadline + timedelta(seconds=1)
    with database.engine.begin() as connection:
        connection.execute(
            update(series).where(series.c.id == _SERIES_ID).values(team_id="team-a")
        )
        connection.execute(
            insert(chapter_locks).values(
                id="lock-a",
                chapter_id=_CHAPTER_ID,
                user_id="user-a",
                username="测试用户",
                acquired_at=_NOW,
                last_active_at=_NOW,
                expires_at=deadline,
            )
        )
        if expiration_point == "final-message-read":
            frame_content = json.loads(
                connection.execute(
                    select(chapters.c.content).where(chapters.c.id == _CHAPTER_ID)
                ).scalar_one()
            )
            frame_content[0]["preview"] = "https://media.example.test/image.mp4?sig=frame"
            connection.execute(
                update(chapters)
                .where(chapters.c.id == _CHAPTER_ID)
                .values(content=json.dumps(frame_content, ensure_ascii=False))
            )

    history = _get_history(database, "frame-preview", "image").json()
    body = _adopt_body("frame-preview", "image", history)
    body["expected_lock_identity"] = {
        "user_id": "user-a",
        "acquired_at": "2026-10-28T01:00:00Z",
    }
    clock_state = {"now": before_expiry, "message_selects": 0}
    statements: list[str] = []
    stage_calls: list[bool] = []
    original_stage = persistence.stage_media_reconciliation

    def advance_clock_after_lock_wait(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        statements.append(statement)
        if expiration_point == "lock-read" and "FROM chapter_locks" in statement:
            clock_state["now"] = after_expiry
        if expiration_point == "final-message-read" and "FROM chat_messages" in statement:
            clock_state["message_selects"] += 1
            if clock_state["message_selects"] == 2:
                clock_state["now"] = after_expiry

    def controlled_clock(_clock=None):
        return clock_state["now"]

    def track_media_stage(session: Session, chapter_id: str, **kwargs):
        stage_calls.append(True)
        return original_stage(session, chapter_id, **kwargs)

    monkeypatch.setattr(persistence, "_utcnow", controlled_clock)
    monkeypatch.setattr(persistence, "stage_media_reconciliation", track_media_stage)
    event.listen(database.engine, "before_cursor_execute", advance_clock_after_lock_wait)
    try:
        response = _request(
            database,
            "POST",
            f"/api/chapters/{_CHAPTER_ID}/generation-history/adopt",
            json=body,
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", advance_clock_after_lock_wait)

    assert response.status_code == 409
    assert response.json()["detail"] == "generation_history_conflict"
    assert clock_state["now"] == after_expiry
    assert stage_calls == []
    assert _count(database, storyboard_media_states) == 0
    assert any("FROM chapter_locks" in statement for statement in statements)
    if expiration_point == "final-message-read":
        assert clock_state["message_selects"] == 2
    with database.engine.connect() as connection:
        lock = connection.execute(
            select(chapter_locks).where(chapter_locks.c.id == "lock-a")
        ).mappings().one()
    assert lock["expires_at"] == deadline
    assert lock["last_active_at"] == _NOW


def test_storyboard_index_move_invalidates_a_previously_read_history(database):
    history = _get_history(database, "storyboard-image", "image").json()
    body = _adopt_body("storyboard-image", "image", history)
    with database.engine.begin() as connection:
        connection.execute(
            update(storyboard_assets)
            .where(storyboard_assets.c.id == _STORYBOARD_ID)
            .values(frame_index=1)
        )

    response = _request(
        database,
        "POST",
        f"/api/chapters/{_CHAPTER_ID}/generation-history/adopt",
        json=body,
    )

    assert response.status_code == 409
    assert _count(database, storyboard_media_states) == 0


@pytest.mark.parametrize(
    ("target_kind", "message_id", "expected_status"),
    [
        ("frame-preview", "frame-video", 200),
        ("storyboard-image", "storyboard-video", 422),
    ],
)
def test_storyboard_url_limit_is_explicit_and_does_not_truncate(
    database, target_kind, message_id, expected_status
):
    candidate = "https://cdn.example.test/" + "a" * 600
    with database.engine.begin() as connection:
        connection.execute(
            update(chat_messages)
            .where(chat_messages.c.id == message_id)
            .values(content=candidate)
        )
    history = _get_history(database, target_kind, "video").json()

    response = _request(
        database,
        "POST",
        f"/api/chapters/{_CHAPTER_ID}/generation-history/adopt",
        json=_adopt_body(target_kind, "video", history),
    )

    assert response.status_code == expected_status
    if target_kind == "storyboard-image":
        assert response.json()["detail"] == "storyboard_media_url_too_long"
        assert _count(database, storyboard_media_states) == 0
        with database.engine.connect() as connection:
            board_image = connection.execute(
                select(storyboard_assets.c.image_url).where(
                    storyboard_assets.c.id == _STORYBOARD_ID
                )
            ).scalar_one()
        assert board_image == "https://media.example.test/old-image.png?sig=old"
    else:
        assert response.json()["outcome"] == "applied"
        with database.engine.connect() as connection:
            content = connection.execute(
                select(chapters.c.content).where(chapters.c.id == _CHAPTER_ID)
            ).scalar_one()
        assert json.loads(content)[0]["preview"] == candidate


@pytest.mark.parametrize("target_kind", ["frame-preview", "storyboard-image"])
def test_adoption_updates_only_the_selected_media_field(database, target_kind):
    history = _get_history(database, target_kind, "video").json()
    message = history["messages"][0]
    proof = history["target_proof"]
    _seed_approved_note(database)
    request = {
        "protocol_version": 1,
        "expected_series_id": _SERIES_ID,
        "target_kind": target_kind,
        "storyboard_asset_id": _STORYBOARD_ID,
        "expected_frame_index": 0,
        "chat_mode": "video",
        "message_id": message["id"],
        "expected_message_digest": message["message_digest"],
        "expected_mapping_digest": proof["mapping_digest"],
        "expected_target_digest": proof["target_digest"],
        "expected_media_url": proof["media_url"],
        "expected_lock_identity": None,
    }
    response = _request(
        database,
        "POST",
        f"/api/chapters/{_CHAPTER_ID}/generation-history/adopt",
        json=request,
    )

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "applied"
    with database.engine.connect() as connection:
        chapter = connection.execute(
            select(chapters.c.content).where(chapters.c.id == _CHAPTER_ID)
        ).scalar_one()
        board_image = connection.execute(
            select(storyboard_assets.c.image_url).where(
                storyboard_assets.c.id == _STORYBOARD_ID
            )
        ).scalar_one()
    frame = json.loads(chapter)[0]
    if target_kind == "frame-preview":
        assert frame["preview"] == message["content"]
        assert frame["storyboard"] == [_STORYBOARD_ID, "secondary-board"]
        assert frame["text"] == "保留的镜头字段"
        assert board_image == "https://media.example.test/old-image.png?sig=old"
    else:
        assert frame["preview"] == "https://media.example.test/old-preview.mp4?sig=old"
        assert board_image == message["content"]
    with database.engine.connect() as connection:
        media_state = connection.execute(
            select(storyboard_media_states).where(
                storyboard_media_states.c.storyboard_asset_id == _STORYBOARD_ID
            )
        ).mappings().one()
        notes = connection.execute(
            select(personal_production_notes).where(
                personal_production_notes.c.chapter_id == _CHAPTER_ID
            )
        ).mappings().one()
    assert media_state["source_valid"] is True
    assert media_state["media_revision"] == 2
    assert notes["revision"] == 2
    assert notes["frame_notes"][_STORYBOARD_ID]["status"] == "unmarked"
    assert notes["frame_notes"][_STORYBOARD_ID]["needs_reconfirmation"] is True


def test_adoption_reports_already_current_without_initializing_media_state(database):
    history = _get_history(database, "frame-preview", "image").json()
    message = history["messages"][0]
    with database.engine.begin() as connection:
        current_frames = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == _CHAPTER_ID)
            ).scalar_one()
        )
        current_frames[0]["preview"] = message["content"]
        connection.execute(
            update(chapters)
            .where(chapters.c.id == _CHAPTER_ID)
            .values(content=json.dumps(current_frames, ensure_ascii=False))
        )

    latest = _get_history(database, "frame-preview", "image").json()
    response = _request(
        database,
        "POST",
        f"/api/chapters/{_CHAPTER_ID}/generation-history/adopt",
        json=_adopt_body("frame-preview", "image", latest),
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "already_current"
    assert _count(database, storyboard_media_states) == 0


def test_final_message_recheck_failure_rolls_back_media_staging(database, monkeypatch):
    history = _get_history(database, "frame-preview", "image").json()
    message = history["messages"][0]
    proof = history["target_proof"]
    _seed_approved_note(database)
    request = {
        "protocol_version": 1,
        "expected_series_id": _SERIES_ID,
        "target_kind": "frame-preview",
        "storyboard_asset_id": _STORYBOARD_ID,
        "expected_frame_index": 0,
        "chat_mode": "image",
        "message_id": message["id"],
        "expected_message_digest": message["message_digest"],
        "expected_mapping_digest": proof["mapping_digest"],
        "expected_target_digest": proof["target_digest"],
        "expected_media_url": proof["media_url"],
        "expected_lock_identity": None,
    }
    original_stage = persistence.stage_media_reconciliation

    def change_message_after_media_stage(session: Session, chapter_id: str, **kwargs):
        result = original_stage(session, chapter_id, **kwargs)
        session.execute(
            update(chat_messages)
            .where(chat_messages.c.id == message["id"])
            .values(content="https://media.example.test/changed.mp4")
        )
        return result

    monkeypatch.setattr(persistence, "stage_media_reconciliation", change_message_after_media_stage)
    response = _request(
        database,
        "POST",
        f"/api/chapters/{_CHAPTER_ID}/generation-history/adopt",
        json=request,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "generation_history_conflict"
    assert _count(database, storyboard_media_states) == 0
    with database.engine.connect() as connection:
        content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == _CHAPTER_ID)
        ).scalar_one()
        stored_message = connection.execute(
            select(chat_messages.c.content).where(chat_messages.c.id == message["id"])
        ).scalar_one()
    assert json.loads(content)[0]["preview"] == proof["media_url"]
    assert stored_message == message["content"]
    with database.engine.connect() as connection:
        notes = connection.execute(
            select(personal_production_notes).where(
                personal_production_notes.c.chapter_id == _CHAPTER_ID
            )
        ).mappings().one()
    assert notes["revision"] == 1
    assert notes["frame_notes"][_STORYBOARD_ID]["status"] == "approved"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://cdn.example.test/a.mp4?signature=one", True),
        ("HTTPS://cdn.example.test/a.mp4?signature=one", True),
        ("/uploads/user/media/video.mp4", True),
        ("uploads/user/media/video.mp4", True),
        ("//cdn.example.test/a.mp4", False),
        ("https://user:pass@cdn.example.test/a.mp4", False),
        ("/uploads/a/../b.mp4", False),
        ("/uploads/a%2Fb.mp4", False),
        ("/uploads/a%20b.mp4", False),
        ("/uploads/a.mp4?download=1", False),
    ],
)
def test_generation_media_source_validation(value, expected):
    assert safe_generation_media_url(value) is expected


def _count(database: HistoryDatabase, table) -> int:
    with database.engine.connect() as connection:
        return int(connection.execute(select(func.count()).select_from(table)).scalar_one())


def _adopt_body(target_kind: str, mode: str, history: dict[str, Any]) -> dict[str, Any]:
    message = history["messages"][0]
    proof = history["target_proof"]
    return {
        "protocol_version": 1,
        "expected_series_id": _SERIES_ID,
        "target_kind": target_kind,
        "storyboard_asset_id": _STORYBOARD_ID,
        "expected_frame_index": 0,
        "chat_mode": mode,
        "message_id": message["id"],
        "expected_message_digest": message["message_digest"],
        "expected_mapping_digest": proof["mapping_digest"],
        "expected_target_digest": proof["target_digest"],
        "expected_media_url": proof["media_url"],
        "expected_lock_identity": None,
    }


def _seed_approved_note(database: HistoryDatabase) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            insert(personal_production_notes).values(
                id="notes-a",
                chapter_id=_CHAPTER_ID,
                user_id="user-a",
                revision=1,
                frame_notes={
                    _STORYBOARD_ID: {
                        "status": "approved",
                        "approved_media_revision": 1,
                        "note": "保留确认状态信息",
                    }
                },
                resume_frame_id=None,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
