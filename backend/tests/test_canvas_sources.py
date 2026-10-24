"""HTTP contract tests for the guarded Canvas source API."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, delete, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)
from haoai_backend.chapter_locks.domain import ChapterLockActor
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_data.tables import (
    chapters,
    characters,
    chapter_locks,
    chat_messages,
    metadata as series_metadata,
    props,
    scenes,
    series,
    storyboard_assets,
    system_configs,
)
from haoai_backend.series_access.tables import (
    metadata as series_access_metadata,
    team_members,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.canvas_sources.persistence import SqlAlchemyCanvasSourceUnitOfWork
from haoai_backend.canvas_sources import persistence as canvas_source_persistence


NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


@pytest.fixture
def source_system(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'canvas-source.sqlite3'}", future=True)
    authentication_metadata.create_all(engine)
    series_metadata.create_all(
        engine,
        tables=[
            series,
            chapters,
            characters,
            scenes,
            props,
            storyboard_assets,
            chapter_locks,
            chat_messages,
            system_configs,
        ],
    )
    series_access_metadata.create_all(engine, tables=[team_members])
    notes_metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id="owner",
                username="Owner",
                email="123456@qq.com",
                hashed_password="unused",
                is_superuser=False,
                membership_type="free",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=NOW,
                password_updated_at=None,
            )
        )
        connection.execute(
            insert(series).values(
                id="series-a",
                user_id="owner",
                team_id="team-a",
                name="Synthetic series",
                description=None,
                image_url=None,
                style_prompt_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-a",
                series_id="series-a",
                title="Synthetic chapter",
                content='[{"text":"第一镜","storyboard":["board-a"],"custom":"保留"}]',
                order=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(storyboard_assets).values(
                id="board-a",
                series_id="series-a",
                chapter_id="chapter-a",
                frame_index=0,
                name="分镜 1",
                description="第一镜",
                image_url=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(chapter_locks).values(
                id="lock-a",
                chapter_id="chapter-a",
                user_id="owner",
                username="Owner",
                acquired_at=NOW,
                last_active_at=NOW,
                expires_at=NOW + timedelta(minutes=30),
            )
        )
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False, future=True)
    app = create_app(
        session_factory=sessions,
        resolve_actor=lambda _request: TrustedActor("owner"),
        resolve_chapter_lock_actor=lambda _request: ChapterLockActor("owner", "Owner"),
        series_access_policy=lambda _session, _actor, _series_id: None,
        authentication_runtime=AuthenticationRuntime(
            session_factory=sessions,
            secret_key="synthetic-canvas-source-secret",
        ),
    )
    yield app, engine
    engine.dispose()


def request(
    app,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://canvas-source.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def test_get_canvas_source_returns_complete_authorized_snapshot(source_system) -> None:
    app, _engine = source_system

    response = request(app, "GET", "/api/chapters/chapter-a/canvas-source")

    assert response.status_code == 200
    payload = response.json()
    example_path = os.environ.get("CANVAS_SOURCE_WIRE_EXAMPLE_PATH")
    if example_path:
        Path(example_path).write_text(
            json.dumps(
                {
                    "request": {
                        "method": "GET",
                        "path": "/api/chapters/chapter-a/canvas-source",
                        "body": None,
                    },
                    "response": {"status": response.status_code, "body": payload},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    assert payload["capability"] == "canvas-source-v1"
    assert payload["series_id"] == "series-a"
    assert payload["chapter_id"] == "chapter-a"
    assert payload["requires_lock"] is True
    assert len(payload["version"]) == 64
    assert payload["chapter"]["content"] == [
        {"text": "第一镜", "storyboard": ["board-a"], "custom": "保留"}
    ]
    assert payload["chapter"]["lock"]["locked"] is True
    assert payload["chapter"]["lock"]["locked_by_username"] == "Owner"
    assert payload["chapter"]["lock"]["is_mine"] is True
    assert payload["chapter"]["lock"]["locked_by_avatar"] == (
        "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100"
    )
    assert [asset["id"] for asset in payload["storyboard_assets"]] == ["board-a"]
    assert payload["characters"] == []
    assert payload["scenes"] == []
    assert payload["props"] == []


def test_personal_chapter_allows_unlocked_write_and_team_transition_invalidates_version(
    source_system,
) -> None:
    app, engine = source_system
    with engine.begin() as connection:
        connection.execute(update(series).where(series.c.id == "series-a").values(team_id=None))
        connection.execute(delete(chapter_locks).where(chapter_locks.c.chapter_id == "chapter-a"))

    personal = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    assert personal["requires_lock"] is False
    assert personal["chapter"]["lock"] is None
    personal_write = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body={
            "capability": "canvas-source-v1",
            "request_id": "1042d4a7-08d8-42bb-9cae-c439e4ef20a1",
            "expected_series_id": "series-a",
            "expected_version": personal["version"],
            "command": {
                "type": "frame.update",
                "target": {"index": 0, "storyboard_id": "board-a"},
                "text": "个人剧集无需加锁",
            },
        },
    )
    assert personal_write.status_code == 200, personal_write.text
    updated = personal_write.json()["snapshot"]
    assert updated["requires_lock"] is False

    with engine.begin() as connection:
        connection.execute(update(series).where(series.c.id == "series-a").values(team_id="team-a"))
    acquired = request(app, "POST", "/api/chapters/chapter-a/lock")
    assert acquired.status_code == 200, acquired.text
    assert acquired.json()["locked"] is True
    current_lock = request(app, "GET", "/api/chapters/chapter-a/lock")
    assert current_lock.status_code == 200
    assert current_lock.json()["locked"] is True

    stale_personal_write = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body={
            "capability": "canvas-source-v1",
            "request_id": "1142d4a7-08d8-42bb-9cae-c439e4ef20a1",
            "expected_series_id": "series-a",
            "expected_version": updated["version"],
            "command": {
                "type": "frame.update",
                "target": {"index": 0, "storyboard_id": "board-a"},
                "text": "不得越过个人快照转团队后的来源版本",
            },
        },
    )
    assert stale_personal_write.status_code == 409
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    assert current["requires_lock"] is True
    assert current["chapter"]["content"][0]["text"] == "个人剧集无需加锁"


def test_team_chapter_write_requires_current_lock_from_real_lock_routes(source_system) -> None:
    app, engine = source_system
    with engine.begin() as connection:
        connection.execute(delete(chapter_locks).where(chapter_locks.c.chapter_id == "chapter-a"))

    initial = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    assert initial["requires_lock"] is True
    command = {
        "type": "frame.update",
        "target": {"index": 0, "storyboard_id": "board-a"},
        "text": "持锁后保存",
    }

    def write(version: str, request_id: str) -> httpx.Response:
        return request(
            app,
            "POST",
            "/api/chapters/chapter-a/canvas-source",
            body={
                "capability": "canvas-source-v1",
                "request_id": request_id,
                "expected_series_id": "series-a",
                "expected_version": version,
                "command": command,
            },
        )

    no_lock = write(initial["version"], "1242d4a7-08d8-42bb-9cae-c439e4ef20a1")
    assert no_lock.status_code == 409

    acquired = request(app, "POST", "/api/chapters/chapter-a/lock")
    assert acquired.status_code == 200, acquired.text
    assert acquired.json()["locked"] is True
    lock_snapshot = request(app, "GET", "/api/chapters/chapter-a/lock")
    assert lock_snapshot.status_code == 200
    assert lock_snapshot.json()["locked"] is True
    assert lock_snapshot.json()["is_mine"] is True

    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    success = write(current["version"], "1342d4a7-08d8-42bb-9cae-c439e4ef20a1")
    assert success.status_code == 200, success.text

    with engine.begin() as connection:
        connection.execute(delete(chapter_locks).where(chapter_locks.c.chapter_id == "chapter-a"))
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    with engine.begin() as connection:
        connection.execute(
            insert(chapter_locks).values(
                id="lock-other",
                chapter_id="chapter-a",
                user_id="other",
                username="Other",
                acquired_at=NOW,
                last_active_at=NOW,
                expires_at=NOW + timedelta(minutes=30),
            )
        )
    other_lock = write(current["version"], "1442d4a7-08d8-42bb-9cae-c439e4ef20a1")
    assert other_lock.status_code == 409

    with engine.begin() as connection:
        connection.execute(
            update(chapter_locks)
            .where(chapter_locks.c.id == "lock-other")
            .values(
                user_id="owner",
                username="Owner",
                expires_at=NOW - timedelta(minutes=1),
            )
        )
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    expired_lock = write(current["version"], "1542d4a7-08d8-42bb-9cae-c439e4ef20a1")
    assert expired_lock.status_code == 409

    with engine.connect() as connection:
        content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-a")
            ).scalar_one()
        )
    assert content[0]["text"] == "持锁后保存"


def test_frame_update_changes_text_through_guarded_source_command(source_system) -> None:
    app, _engine = source_system
    initial = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()

    response = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body={
            "capability": "canvas-source-v1",
            "request_id": "a2b2d4a7-08d8-42bb-9cae-c439e4ef20a1",
            "expected_series_id": "series-a",
            "expected_version": initial["version"],
            "command": {
                "type": "frame.update",
                "target": {"index": 0, "storyboard_id": "board-a"},
                "text": "更新后的镜头",
            },
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["snapshot"]["chapter"]["content"] == [
        {"text": "更新后的镜头", "storyboard": ["board-a"], "custom": "保留"}
    ]
    assert payload["snapshot"]["storyboard_assets"][0]["description"] == "更新后的镜头"
    assert payload["creation_receipt"] is None


def test_source_commands_preserve_orphans_and_legacy_frame_chat_indexes(
    source_system,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, engine = source_system
    initial_content = json.dumps(
        [
            {"text": "第一镜", "storyboard": ["board-a"], "vendor": {"keep": 1}},
            {"text": "旧无ID镜头", "custom": ["保留", 2]},
            {"text": "第三镜", "storyboard": ["board-z"], "another": True},
        ],
        ensure_ascii=False,
    )
    with engine.begin() as connection:
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(content=initial_content)
        )
        connection.execute(
            insert(storyboard_assets).values(
                id="board-z",
                series_id="series-a",
                chapter_id="chapter-a",
                frame_index=2,
                name="分镜 3",
                description="第三镜",
                image_url=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(storyboard_assets).values(
                id="orphan-board",
                series_id="series-a",
                chapter_id="chapter-a",
                frame_index=55,
                name="孤立记录保留",
                description="不属于任何frame",
                image_url="https://example.test/orphan.png",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        for message_id, frame_index in (("chat-legacy", 1), ("chat-third", 2)):
            connection.execute(
                insert(chat_messages).values(
                    id=message_id,
                    chapter_id="chapter-a",
                    frame_index=frame_index,
                    asset_type=None,
                    asset_id=None,
                    chat_mode="storyboard",
                    role="user",
                    content="保留既有聊天",
                    model_name=None,
                    created_at=NOW,
                )
            )
        connection.execute(
            insert(storyboard_media_states).values(
                id="orphan-media-state",
                chapter_id="chapter-a",
                storyboard_asset_id="orphan-board",
                media_revision=7,
                asset_image_digest="a" * 64,
                preview_digest="b" * 64,
                source_valid=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    wire: list[dict[str, object]] = []

    def post(expected_version: str, command: dict, request_id: str) -> httpx.Response:
        body = {
            "capability": "canvas-source-v1",
            "request_id": request_id,
            "expected_series_id": "series-a",
            "expected_version": expected_version,
            "command": command,
        }
        response = request(app, "POST", "/api/chapters/chapter-a/canvas-source", body=body)
        wire.append(
            {
                "request": {"method": "POST", "path": "/api/chapters/chapter-a/canvas-source", "body": body},
                "response": {"status": response.status_code, "body": response.json()},
            }
        )
        return response

    before = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    text_response = post(
        before["version"],
        {
            "type": "frame.update",
            "target": {"index": 0, "storyboard_id": "board-a"},
            "text": "第一镜已改",
        },
        "7db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert text_response.status_code == 200, text_response.text
    text_snapshot = text_response.json()["snapshot"]
    assert text_snapshot["chapter"]["content"][1] == {
        "text": "旧无ID镜头",
        "custom": ["保留", 2],
    }

    media_response = post(
        text_snapshot["version"],
        {
            "type": "media.update",
            "target": {"index": 0, "storyboard_id": "board-a"},
            "field": "preview",
            "slot": None,
            "value": "https://example.test/preview.jpg",
        },
        "8db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert media_response.status_code == 200, media_response.text
    media_snapshot = media_response.json()["snapshot"]

    insert_response = post(
        media_snapshot["version"],
        {
            "type": "frame.insert",
            "after": {"index": 0, "storyboard_id": "board-a"},
            "frame": {"text": "新插入镜头", "vendor": {"keep": True}},
        },
        "9db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert insert_response.status_code == 200, insert_response.text
    inserted = insert_response.json()
    receipt = inserted["creation_receipt"]
    assert receipt["kind"] == "frame"
    created_id = receipt["target"]["storyboard_id"]
    inserted_frames = inserted["snapshot"]["chapter"]["content"]
    assert inserted_frames[1]["storyboard"] == [created_id]
    assert inserted_frames[2] == {"text": "旧无ID镜头", "custom": ["保留", 2]}
    assert inserted_frames[3]["storyboard"] == ["board-z"]

    undo_response = post(
        inserted["snapshot"]["version"],
        {"type": "creation.undo", "receipt": receipt["token"]},
        "ad42d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert undo_response.status_code == 200, undo_response.text
    restored = undo_response.json()["snapshot"]
    assert restored["chapter"]["content"] == [
        {"text": "第一镜已改", "storyboard": ["board-a"], "vendor": {"keep": 1}, "preview": "https://example.test/preview.jpg"},
        {"text": "旧无ID镜头", "custom": ["保留", 2]},
        {"text": "第三镜", "storyboard": ["board-z"], "another": True},
    ]

    with engine.connect() as connection:
        board_rows = {
            row["id"]: dict(row)
            for row in connection.execute(
                select(storyboard_assets).where(storyboard_assets.c.chapter_id == "chapter-a")
            ).mappings()
        }
        chat_rows = {
            row["id"]: row["frame_index"]
            for row in connection.execute(
                select(chat_messages.c.id, chat_messages.c.frame_index).where(
                    chat_messages.c.chapter_id == "chapter-a"
                )
            ).mappings()
        }
        orphan_state = connection.execute(
            select(storyboard_media_states).where(storyboard_media_states.c.id == "orphan-media-state")
        ).mappings().one()
    assert created_id not in board_rows
    assert board_rows["board-a"]["frame_index"] == 0
    assert board_rows["board-z"]["frame_index"] == 2
    assert board_rows["board-z"]["name"] == "分镜 3"
    assert board_rows["orphan-board"]["frame_index"] == 55
    assert board_rows["orphan-board"]["name"] == "孤立记录保留"
    assert chat_rows == {"chat-legacy": 1, "chat-third": 2}
    assert orphan_state["media_revision"] == 7
    assert orphan_state["source_valid"] is True

    character_response = post(
        restored["version"],
        {
            "type": "asset.create",
            "kind": "character",
            "values": {
                "name": "小满",
                "role": "主人公",
                "image_url": "https://example.test/xiaoman.png",
            },
        },
        "af42d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert character_response.status_code == 200, character_response.text
    character_result = character_response.json()
    created_character = character_result["creation_receipt"]["target"]["id"]
    assert character_result["snapshot"]["characters"][0]["id"] == created_character
    assert character_result["snapshot"]["characters"][0]["image_url"] == (
        "https://example.test/xiaoman.png"
    )

    appended_video = post(
        character_result["snapshot"]["version"],
        {
            "type": "media.append",
            "target": {"index": 0, "storyboard_id": "board-a"},
            "value": "https://example.test/trailer.mp4",
        },
        "bf42d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert appended_video.status_code == 200, appended_video.text
    video_result = appended_video.json()
    assert video_result["snapshot"]["chapter"]["content"][0]["videos"] == [
        "https://example.test/trailer.mp4"
    ]
    assert video_result["creation_receipt"]["kind"] == "video"
    assert video_result["creation_receipt"]["target"]["slot"] == 0

    video_undo = post(
        video_result["snapshot"]["version"],
        {"type": "creation.undo", "receipt": video_result["creation_receipt"]["token"]},
        "cf42d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert video_undo.status_code == 200, video_undo.text

    wire_path = os.environ.get("CANVAS_SOURCE_POST_WIRE_EXAMPLE_PATH")
    if wire_path:
        Path(wire_path).write_text(
            json.dumps(wire, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def test_media_reconciliation_preserves_only_requested_orphan_states(source_system) -> None:
    _app, engine = source_system
    with engine.begin() as connection:
        connection.execute(
            insert(storyboard_assets).values(
                id="orphan-for-coordinator",
                series_id="series-a",
                chapter_id="chapter-a",
                frame_index=77,
                name="orphan",
                description=None,
                image_url=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        for state_id, asset_id, revision, digest in (
            ("state-target", "board-a", 1, None),
            ("state-orphan", "orphan-for-coordinator", 7, "a" * 64),
        ):
            connection.execute(
                insert(storyboard_media_states).values(
                    id=state_id,
                    chapter_id="chapter-a",
                    storyboard_asset_id=asset_id,
                    media_revision=revision,
                    asset_image_digest=digest,
                    preview_digest=None,
                    source_valid=True,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        connection.execute(
            insert(personal_production_notes).values(
                id="notes-owner",
                chapter_id="chapter-a",
                user_id="owner",
                revision=4,
                frame_notes={
                    "board-a": {"status": "approved", "approved_media_revision": 1},
                    "orphan-for-coordinator": {
                        "status": "approved",
                        "approved_media_revision": 7,
                    },
                },
                resume_frame_id=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    with Session(engine, future=True) as session:
        uow = SqlAlchemyCanvasSourceUnitOfWork(session)
        uow.reconcile_media(
            "chapter-a",
            content='[{"text":"第一镜","storyboard":["board-a"]}]',
            deleted_asset_ids={"board-a"},
            preserve_unreferenced_state_ids=["board-a", "orphan-for-coordinator"],
        )
        session.commit()

    with engine.connect() as connection:
        states = {
            row["storyboard_asset_id"]: row
            for row in connection.execute(
                select(storyboard_media_states).where(
                    storyboard_media_states.c.chapter_id == "chapter-a"
                )
            ).mappings()
        }
        note = connection.execute(
            select(personal_production_notes).where(
                personal_production_notes.c.id == "notes-owner"
            )
        ).mappings().one()
    assert states["board-a"]["source_valid"] is False
    assert states["board-a"]["media_revision"] == 2
    assert states["orphan-for-coordinator"]["source_valid"] is True
    assert states["orphan-for-coordinator"]["media_revision"] == 7
    assert note["frame_notes"]["board-a"]["status"] == "unmarked"
    assert note["frame_notes"]["board-a"]["needs_reconfirmation"] is True
    assert note["frame_notes"]["orphan-for-coordinator"]["status"] == "approved"

    with Session(engine, future=True) as session:
        uow = SqlAlchemyCanvasSourceUnitOfWork(session)
        uow.reconcile_media(
            "chapter-a",
            content='[{"text":"第一镜","storyboard":["board-a"]}]',
            deleted_asset_ids={"board-a"},
        )
        session.commit()
    with engine.connect() as connection:
        orphan_state = connection.execute(
            select(storyboard_media_states).where(
                storyboard_media_states.c.id == "state-orphan"
            )
        ).mappings().one()
        note = connection.execute(
            select(personal_production_notes).where(
                personal_production_notes.c.id == "notes-owner"
            )
        ).mappings().one()
    assert orphan_state["source_valid"] is False
    assert orphan_state["media_revision"] == 8
    assert note["frame_notes"]["orphan-for-coordinator"]["status"] == "unmarked"


def test_asset_create_update_and_receipt_undo_use_actual_ids(source_system) -> None:
    app, engine = source_system

    def post(version: str, command: dict, request_id: str) -> httpx.Response:
        return request(
            app,
            "POST",
            "/api/chapters/chapter-a/canvas-source",
            body={
                "capability": "canvas-source-v1",
                "request_id": request_id,
                "expected_series_id": "series-a",
                "expected_version": version,
                "command": command,
            },
        )

    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    character_create = post(
        current["version"],
        {"type": "asset.create", "kind": "character", "values": {"name": "小满", "role": "主角"}},
        "1db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert character_create.status_code == 200, character_create.text
    created_character = character_create.json()
    character_id = created_character["creation_receipt"]["target"]["id"]
    assert created_character["snapshot"]["characters"][0]["id"] == character_id
    assert created_character["creation_receipt"]["kind"] == "character"

    invalid_reference = post(
        created_character["snapshot"]["version"],
        {
            "type": "frame.insert",
            "after": {"index": 0, "storyboard_id": "board-a"},
            "frame": {"text": "不得引用未知角色", "character": ["missing-character"]},
        },
        "1042d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert invalid_reference.status_code == 409
    valid_reference = post(
        created_character["snapshot"]["version"],
        {
            "type": "frame.insert",
            "after": {"index": 0, "storyboard_id": "board-a"},
            "frame": {"text": "可以引用本剧角色", "character": [character_id]},
        },
        "1142d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert valid_reference.status_code == 200, valid_reference.text
    inserted_frame = valid_reference.json()
    assert inserted_frame["snapshot"]["chapter"]["content"][1]["character"] == [character_id]
    inserted_undo = post(
        inserted_frame["snapshot"]["version"],
        {"type": "creation.undo", "receipt": inserted_frame["creation_receipt"]["token"]},
        "1242d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert inserted_undo.status_code == 200, inserted_undo.text

    tampered_token = created_character["creation_receipt"]["token"][:-1] + "x"
    tampered = post(
        inserted_undo.json()["snapshot"]["version"],
        {"type": "creation.undo", "receipt": tampered_token},
        "2db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert tampered.status_code == 409

    character_update = post(
        inserted_undo.json()["snapshot"]["version"],
        {
            "type": "asset.update",
            "kind": "character",
            "id": character_id,
            "values": {"name": "小满·更新", "aliases": ["小满", "小满"], "audio_url": "voice://new"},
        },
        "3db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert character_update.status_code == 200, character_update.text
    updated_character = character_update.json()["snapshot"]["characters"][0]
    assert updated_character["name"] == "小满·更新"
    assert updated_character["aliases"] == ["小满"]
    assert updated_character["audio_url"] == "voice://new"
    stale_receipt = post(
        character_update.json()["snapshot"]["version"],
        {"type": "creation.undo", "receipt": created_character["creation_receipt"]["token"]},
        "4db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert stale_receipt.status_code == 409

    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    scene_create = post(
        current["version"],
        {"type": "asset.create", "kind": "scene", "values": {"title": "仓库·夜晚"}},
        "5db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert scene_create.status_code == 200, scene_create.text
    scene_result = scene_create.json()
    scene_id = scene_result["creation_receipt"]["target"]["id"]
    current = scene_result["snapshot"]
    prop_create = post(
        current["version"],
        {"type": "asset.create", "kind": "prop", "values": {"name": "旧钥匙"}},
        "6db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert prop_create.status_code == 200, prop_create.text
    prop_result = prop_create.json()
    prop_id = prop_result["creation_receipt"]["target"]["id"]
    assert [asset["id"] for asset in prop_result["snapshot"]["props"]] == [prop_id]

    # An unrelated source write does not invalidate the row-scoped scene receipt.
    scene_undo = post(
        prop_result["snapshot"]["version"],
        {"type": "creation.undo", "receipt": scene_result["creation_receipt"]["token"]},
        "7db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert scene_undo.status_code == 200, scene_undo.text
    assert all(asset["id"] != scene_id for asset in scene_undo.json()["snapshot"]["scenes"])

    with engine.begin() as connection:
        content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-a")
            ).scalar_one()
        )
        content[0]["prop"] = [prop_id]
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(
                content=json.dumps(content, ensure_ascii=False)
            )
        )
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    referenced_undo = post(
        current["version"],
        {"type": "creation.undo", "receipt": prop_result["creation_receipt"]["token"]},
        "8db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert referenced_undo.status_code == 409
    with engine.connect() as connection:
        assert connection.execute(
            select(props.c.id).where(props.c.id == prop_id)
        ).scalar_one_or_none() == prop_id

    with engine.begin() as connection:
        content[0]["prop"] = []
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(
                content=json.dumps(content, ensure_ascii=False)
            )
        )
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    prop_undo = post(
        current["version"],
        {"type": "creation.undo", "receipt": prop_result["creation_receipt"]["token"]},
        "9db2d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert prop_undo.status_code == 200, prop_undo.text
    assert all(asset["id"] != prop_id for asset in prop_undo.json()["snapshot"]["props"])
    with engine.connect() as connection:
        assert connection.execute(
            select(characters.c.id).where(characters.c.id == character_id)
        ).scalar_one_or_none() == character_id


def test_video_append_and_update_use_exact_slots_for_duplicate_urls(source_system) -> None:
    app, engine = source_system
    initial_frames = [{"text": "第一镜", "storyboard": ["board-a"], "videos": ["same", "same"]}]
    with engine.begin() as connection:
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(
                content=json.dumps(initial_frames, ensure_ascii=False)
            )
        )

    def post(version: str, command: dict, request_id: str) -> httpx.Response:
        return request(
            app,
            "POST",
            "/api/chapters/chapter-a/canvas-source",
            body={
                "capability": "canvas-source-v1",
                "request_id": request_id,
                "expected_series_id": "series-a",
                "expected_version": version,
                "command": command,
            },
        )

    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    appended = post(
        current["version"],
        {
            "type": "media.append",
            "target": {"index": 0, "storyboard_id": "board-a"},
            "value": "same",
        },
        "ab42d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert appended.status_code == 200, appended.text
    appended_result = appended.json()
    assert appended_result["creation_receipt"]["kind"] == "video"
    assert appended_result["creation_receipt"]["target"]["slot"] == 2
    assert appended_result["snapshot"]["chapter"]["content"][0]["videos"] == [
        "same",
        "same",
        "same",
    ]

    undo = post(
        appended_result["snapshot"]["version"],
        {"type": "creation.undo", "receipt": appended_result["creation_receipt"]["token"]},
        "bc42d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert undo.status_code == 200, undo.text
    current = undo.json()["snapshot"]
    assert current["chapter"]["content"][0]["videos"] == ["same", "same"]

    changed = post(
        current["version"],
        {
            "type": "media.update",
            "target": {"index": 0, "storyboard_id": "board-a"},
            "field": "video",
            "slot": 1,
            "value": "replacement",
        },
        "cd42d4a7-08d8-42bb-9cae-c439e4ef20a1",
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["snapshot"]["chapter"]["content"][0]["videos"] == [
        "same",
        "replacement",
    ]
    assert changed.json()["creation_receipt"] is None


def test_frame_delete_only_removes_target_and_reindexes_known_rows(source_system) -> None:
    app, engine = source_system
    frames = [
        {"text": "target", "storyboard": ["board-a"]},
        {"text": "no stable identity", "unknown": "keep"},
    ]
    with engine.begin() as connection:
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(
                content=json.dumps(frames, ensure_ascii=False)
            )
        )
        connection.execute(
            insert(storyboard_assets).values(
                id="orphan-delete",
                series_id="series-a",
                chapter_id="chapter-a",
                frame_index=0,
                name="orphan at target index",
                description="keep",
                image_url=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    before = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    response = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body={
            "capability": "canvas-source-v1",
            "request_id": "de42d4a7-08d8-42bb-9cae-c439e4ef20a1",
            "expected_series_id": "series-a",
            "expected_version": before["version"],
            "command": {
                "type": "frame.delete",
                "target": {"index": 0, "storyboard_id": "board-a"},
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["chapter"]["content"] == [
        {"text": "no stable identity", "unknown": "keep"}
    ]
    with engine.connect() as connection:
        remaining_ids = set(
            connection.execute(
                select(storyboard_assets.c.id).where(
                    storyboard_assets.c.chapter_id == "chapter-a"
                )
            ).scalars()
        )
    assert remaining_ids == {"orphan-delete"}


def test_source_guard_orders_access_series_lock_and_signature_checks(source_system) -> None:
    app, engine = source_system
    with engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id="outsider",
                username="Outsider",
                email="outsider@example.test",
                hashed_password="unused",
                is_superuser=False,
                membership_type="free",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=NOW,
                password_updated_at=None,
            )
        )
    original = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    with engine.begin() as connection:
        connection.execute(
            update(chapter_locks).where(chapter_locks.c.id == "lock-a").values(
                user_id="outsider",
                username="Outsider",
                last_active_at=NOW + timedelta(minutes=1),
                expires_at=NOW + timedelta(minutes=45),
            )
        )
    other_lock = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    assert other_lock["version"] == original["version"]
    assert other_lock["chapter"]["lock"]["locked_by_username"] == "Outsider"
    assert other_lock["chapter"]["lock"]["is_mine"] is False

    frame_update = {
        "type": "frame.update",
        "target": {"index": 0, "storyboard_id": "board-a"},
        "text": "不能越过他人锁",
    }
    body = {
        "capability": "canvas-source-v1",
        "request_id": "e142d4a7-08d8-42bb-9cae-c439e4ef20a1",
        "expected_series_id": "series-a",
        "expected_version": original["version"],
        "command": frame_update,
    }
    locked = request(app, "POST", "/api/chapters/chapter-a/canvas-source", body=body)
    assert locked.status_code == 409

    with engine.begin() as connection:
        connection.execute(
            update(chapter_locks).where(chapter_locks.c.id == "lock-a").values(
                user_id="owner",
                username="Owner",
                expires_at=NOW + timedelta(minutes=30),
            )
        )
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    wrong_series = dict(body, expected_version=current["version"], expected_series_id="series-b")
    mismatch = request(app, "POST", "/api/chapters/chapter-a/canvas-source", body=wrong_series)
    assert mismatch.status_code == 409

    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False, future=True)
    outsider_app = create_app(
        session_factory=sessions,
        resolve_actor=lambda _request: TrustedActor("outsider"),
        series_access_policy=lambda _session, _actor, _series_id: None,
        authentication_runtime=AuthenticationRuntime(
            session_factory=sessions,
            secret_key="synthetic-canvas-source-secret",
        ),
    )
    unauthorized = request(
        outsider_app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=dict(wrong_series, expected_version=current["version"]),
    )
    assert unauthorized.status_code == 403

    with engine.begin() as connection:
        connection.execute(
            update(chapter_locks).where(chapter_locks.c.id == "lock-a").values(
                expires_at=NOW - timedelta(minutes=1)
            )
        )
    expired = request(app, "POST", "/api/chapters/chapter-a/canvas-source", body=body)
    assert expired.status_code == 409

    no_signer_app = create_app(
        session_factory=sessions,
        resolve_actor=lambda _request: TrustedActor("owner"),
        series_access_policy=lambda _session, _actor, _series_id: None,
    )
    missing_capability = request(
        no_signer_app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=body,
    )
    assert missing_capability.status_code == 503

    with engine.connect() as connection:
        content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one()
    assert json.loads(content)[0]["text"] == "第一镜"


def test_guard_rejects_stale_writer_but_runs_same_version_no_op(source_system) -> None:
    app, engine = source_system
    before = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    no_op = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body={
            "capability": "canvas-source-v1",
            "request_id": "f142d4a7-08d8-42bb-9cae-c439e4ef20a1",
            "expected_series_id": "series-a",
            "expected_version": before["version"],
            "command": {
                "type": "frame.update",
                "target": {"index": 0, "storyboard_id": "board-a"},
                "text": "第一镜",
            },
        },
    )
    assert no_op.status_code == 200, no_op.text
    assert no_op.json()["snapshot"]["version"] == before["version"]
    with engine.connect() as connection:
        active_at = connection.execute(
            select(chapter_locks.c.last_active_at).where(chapter_locks.c.id == "lock-a")
        ).scalar_one()
    assert active_at >= NOW

    def write(text: str, request_id: str) -> httpx.Response:
        return request(
            app,
            "POST",
            "/api/chapters/chapter-a/canvas-source",
            body={
                "capability": "canvas-source-v1",
                "request_id": request_id,
                "expected_series_id": "series-a",
                "expected_version": before["version"],
                "command": {
                    "type": "frame.update",
                    "target": {"index": 0, "storyboard_id": "board-a"},
                    "text": text,
                },
            },
        )

    first = write("先提交的值", "0142d4a7-08d8-42bb-9cae-c439e4ef20a1")
    stale = write("旧快照不得覆盖", "0242d4a7-08d8-42bb-9cae-c439e4ef20a1")
    assert first.status_code == 200, first.text
    assert stale.status_code == 409
    assert first.json()["snapshot"]["chapter"]["content"][0]["text"] == "先提交的值"


def test_media_failure_rolls_back_chapter_content_and_lock_refresh(
    source_system,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, engine = source_system
    before = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    with engine.connect() as connection:
        before_lock = connection.execute(
            select(chapter_locks.c.last_active_at, chapter_locks.c.expires_at).where(
                chapter_locks.c.id == "lock-a"
            )
        ).one()

    def fail_media(*_args, **_kwargs):
        raise RuntimeError("synthetic media reconciliation failure")

    monkeypatch.setattr(canvas_source_persistence, "stage_media_reconciliation", fail_media)
    response = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body={
            "capability": "canvas-source-v1",
            "request_id": "0342d4a7-08d8-42bb-9cae-c439e4ef20a1",
            "expected_series_id": "series-a",
            "expected_version": before["version"],
            "command": {
                "type": "media.update",
                "target": {"index": 0, "storyboard_id": "board-a"},
                "field": "preview",
                "slot": None,
                "value": "https://example.test/failure.jpg",
            },
        },
    )
    assert response.status_code == 500
    with engine.connect() as connection:
        content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one()
        after_lock = connection.execute(
            select(chapter_locks.c.last_active_at, chapter_locks.c.expires_at).where(
                chapter_locks.c.id == "lock-a"
            )
        ).one()
        states = connection.execute(
            select(storyboard_media_states.c.id).where(
                storyboard_media_states.c.chapter_id == "chapter-a"
            )
        ).all()
    assert content == '[{"text":"第一镜","storyboard":["board-a"],"custom":"保留"}]'
    assert tuple(after_lock) == tuple(before_lock)
    assert states == []


@pytest.mark.parametrize(
    ("content", "storyboard_index"),
    [
        ("{", 0),
        ('{"text":"not an array"}', 0),
        ('[{"text":{"nested":[NaN]},"storyboard":["board-a"]}]', 0),
        ('[{"text":"第一镜","storyboard":["board-a"]},{"text":"重复","storyboard":["board-a"]}]', 0),
        ('[{"text":"第一镜","storyboard":["board-a"]}]', 5),
    ],
)
def test_guarded_source_rejects_malformed_content_and_storyboard_identity_before_writes(
    source_system,
    content: str,
    storyboard_index: int,
) -> None:
    app, engine = source_system
    with engine.begin() as connection:
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(content=content)
        )
        connection.execute(
            update(storyboard_assets)
            .where(storyboard_assets.c.id == "board-a")
            .values(frame_index=storyboard_index)
        )

    source_uow = SqlAlchemyCanvasSourceUnitOfWork(Session(engine, future=True))
    try:
        expected_version = source_uow.load_source(
            "chapter-a", TrustedActor("owner")
        ).version
    finally:
        source_uow.rollback()
        source_uow.close()

    before_lock = _read_lock(engine)
    before_characters = _read_rows(engine, characters)
    before_notes = _read_rows(engine, personal_production_notes)
    before_media = _read_rows(engine, storyboard_media_states)
    read_response = request(app, "GET", "/api/chapters/chapter-a/canvas-source")
    write_response = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            expected_version,
            "asset.create",
            {"type": "asset.create", "kind": "character", "values": {"name": "不得写入"}},
        ),
    )

    with engine.connect() as connection:
        assert connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one() == content
        assert connection.execute(
            select(storyboard_assets.c.frame_index).where(storyboard_assets.c.id == "board-a")
        ).scalar_one() == storyboard_index
    assert _read_lock(engine) == before_lock
    assert _read_rows(engine, characters) == before_characters
    assert _read_rows(engine, personal_production_notes) == before_notes
    assert _read_rows(engine, storyboard_media_states) == before_media
    assert (read_response.status_code, write_response.status_code) == (409, 409), (
        read_response.text,
        write_response.text,
    )


def test_guarded_frame_update_can_target_a_legacy_frame_without_stable_id(source_system) -> None:
    app, engine = source_system
    legacy_content = '[{"text":"无稳定ID","vendor":{"keep":true}}]'
    with engine.begin() as connection:
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(content=legacy_content)
        )
    before = request(app, "GET", "/api/chapters/chapter-a/canvas-source")
    assert before.status_code == 200, before.text

    response = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            before.json()["version"],
            "frame.update",
            {
                "type": "frame.update",
                "target": {"index": 0, "storyboard_id": None},
                "text": "修改无ID旧帧",
            },
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["chapter"]["content"] == [
        {"text": "修改无ID旧帧", "vendor": {"keep": True}}
    ]
    with engine.connect() as connection:
        board = connection.execute(
            select(storyboard_assets).where(storyboard_assets.c.id == "board-a")
        ).mappings().one()
    assert board["frame_index"] == 0
    assert board["name"] == "分镜 1"
    assert board["description"] == "第一镜"


def test_null_source_can_be_created_and_undone_but_last_frame_cannot_be_deleted(
    source_system,
) -> None:
    app, engine = source_system
    with engine.begin() as connection:
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(content=None)
        )
    empty_snapshot = request(app, "GET", "/api/chapters/chapter-a/canvas-source")
    assert empty_snapshot.status_code == 200, empty_snapshot.text
    assert empty_snapshot.json()["chapter"]["content"] == []

    inserted = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            empty_snapshot.json()["version"],
            "frame.insert",
            {"type": "frame.insert", "after": None, "frame": {"text": "从NULL创建"}},
        ),
    )
    assert inserted.status_code == 200, inserted.text
    created = inserted.json()
    storyboard_id = created["creation_receipt"]["target"]["storyboard_id"]
    last_delete = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            created["snapshot"]["version"],
            "frame.delete",
            {
                "type": "frame.delete",
                "target": {"index": 0, "storyboard_id": storyboard_id},
            },
        ),
    )
    assert last_delete.status_code == 409

    undone = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            created["snapshot"]["version"],
            "creation.undo",
            {"type": "creation.undo", "receipt": created["creation_receipt"]["token"]},
        ),
    )
    assert undone.status_code == 200, undone.text
    with engine.connect() as connection:
        assert connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one() is None
    with engine.begin() as connection:
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(content="[]")
        )
    empty_array = request(app, "GET", "/api/chapters/chapter-a/canvas-source")
    assert empty_array.status_code == 200, empty_array.text
    assert empty_array.json()["chapter"]["content"] == []


@pytest.mark.parametrize("drift", ["chat", "notes"])
def test_frame_creation_receipt_rejects_post_creation_chat_or_notes_drift(
    source_system,
    drift: str,
) -> None:
    app, engine = source_system
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    inserted = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            current["version"],
            "frame.insert",
            {
                "type": "frame.insert",
                "after": {"index": 0, "storyboard_id": "board-a"},
                "frame": {"text": "带回执的新分镜"},
            },
        ),
    )
    assert inserted.status_code == 200, inserted.text
    created = inserted.json()
    created_board_id = created["creation_receipt"]["target"]["storyboard_id"]

    if drift == "chat":
        chat = request(
            app,
            "POST",
            "/api/chapters/chapter-a/asset-chat-messages",
            body={
                "chapter_id": "chapter-a",
                "asset_type": "storyboard",
                "asset_id": created_board_id,
                "role": "user",
                "content": "创建后新增的对话",
            },
        )
        assert chat.status_code == 201, chat.text
    else:
        notes = request(app, "GET", "/api/chapters/chapter-a/personal-production-notes")
        assert notes.status_code == 200, notes.text
        saved = request(
            app,
            "PUT",
            "/api/chapters/chapter-a/personal-production-notes",
            body={
                "expected_revision": notes.json()["revision"],
                "frames": [
                    {
                        "storyboard_asset_id": created_board_id,
                        "expected_media_revision": 1,
                        "status": "needs_revision",
                        "note": "创建后新增的便签",
                    }
                ],
            },
        )
        assert saved.status_code == 200, saved.text

    current_after_drift = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    undo = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            current_after_drift["version"],
            "creation.undo",
            {"type": "creation.undo", "receipt": created["creation_receipt"]["token"]},
        ),
    )
    assert undo.status_code == 409
    remaining = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    assert any(row["id"] == created_board_id for row in remaining["storyboard_assets"])


def test_asset_creation_undo_checks_direct_chat_references_in_every_chapter(source_system) -> None:
    app, engine = source_system
    with engine.begin() as connection:
        connection.execute(
            insert(series).values(
                id="series-b",
                user_id="owner",
                team_id=None,
                name="Second accessible series",
                description=None,
                image_url=None,
                style_prompt_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-b",
                series_id="series-b",
                title="Second chapter",
                content="[]",
                order=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    initial = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    created = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            initial["version"],
            "asset.create",
            {"type": "asset.create", "kind": "character", "values": {"name": "撤销保护"}},
        ),
    )
    assert created.status_code == 200, created.text
    receipt = created.json()["creation_receipt"]
    asset_id = receipt["target"]["id"]

    same_series_chat = request(
        app,
        "POST",
        "/api/chapters/chapter-a/asset-chat-messages",
        body={
            "chapter_id": "chapter-a",
            "asset_type": "character",
            "asset_id": asset_id,
            "role": "user",
            "content": "同剧集对话引用",
        },
    )
    assert same_series_chat.status_code == 201, same_series_chat.text
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    same_series_undo = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            current["version"],
            "creation.undo",
            {"type": "creation.undo", "receipt": receipt["token"]},
        ),
    )
    assert same_series_undo.status_code == 409
    deleted = request(
        app,
        "DELETE",
        f"/api/chapters/chapter-a/asset-chat-messages/single/{same_series_chat.json()['id']}",
    )
    assert deleted.status_code == 204

    other_series_chat = request(
        app,
        "POST",
        "/api/chapters/chapter-b/asset-chat-messages",
        body={
            "chapter_id": "chapter-b",
            "asset_type": "character",
            "asset_id": asset_id,
            "role": "user",
            "content": "另一可访问剧集的对话引用",
        },
    )
    assert other_series_chat.status_code == 201, other_series_chat.text
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    cross_series_undo = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            current["version"],
            "creation.undo",
            {"type": "creation.undo", "receipt": receipt["token"]},
        ),
    )

    assert cross_series_undo.status_code == 409
    assert "chapter-b" not in cross_series_undo.text
    assert "另一可访问剧集" not in cross_series_undo.text
    with engine.connect() as connection:
        assert connection.execute(
            select(characters.c.id).where(characters.c.id == asset_id)
        ).scalar_one() == asset_id
        assert connection.execute(
            select(chat_messages.c.id).where(
                chat_messages.c.id == other_series_chat.json()["id"],
                chat_messages.c.asset_type == "character",
                chat_messages.c.asset_id == asset_id,
            )
        ).scalar_one() == other_series_chat.json()["id"]


def test_authenticated_canvas_source_uses_actual_jwt_resolver_composition(source_system) -> None:
    _app, engine = source_system
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False, future=True)
    secret = "canvas-source-auth-composition-secret"
    runtime = AuthenticationRuntime(session_factory=sessions, secret_key=secret)
    token_codec = runtime.get_token_codec()
    assert token_codec is not None
    token = token_codec.encode(
        {
            "sub": "owner",
            "jti": "canvas-source-jwt-session",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
    )
    with engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.id == "owner")
            .values(membership_type="premium", membership_expires_at=None)
        )
        connection.execute(
            insert(user_sessions).values(
                id="canvas-source-jwt-session-row",
                user_id="owner",
                jti="canvas-source-jwt-session",
                user_agent="canvas source integration test",
                ip_address="127.0.0.1",
                device_name="test",
                is_active=True,
                created_at=NOW,
                last_seen_at=NOW,
                revoked_at=None,
            )
        )
    app = create_app(
        session_factory=sessions,
        authentication_runtime=runtime,
    )
    headers = {"Authorization": f"Bearer {token}"}

    snapshot = request(
        app,
        "GET",
        "/api/chapters/chapter-a/canvas-source",
        headers=headers,
    )
    assert snapshot.status_code == 200, snapshot.text
    updated = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        headers=headers,
        body=_source_command_body(
            snapshot.json()["version"],
            "frame.update",
            {
                "type": "frame.update",
                "target": {"index": 0, "storyboard_id": "board-a"},
                "text": "由真实JWT resolver识别的用户提交",
            },
        ),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["snapshot"]["chapter"]["content"][0]["text"] == (
        "由真实JWT resolver识别的用户提交"
    )


def test_precommit_failure_after_lock_refresh_rolls_back_source_lock_media_and_notes(
    source_system,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, engine = source_system
    with engine.begin() as connection:
        connection.execute(
            insert(storyboard_media_states).values(
                id="rollback-media-state",
                chapter_id="chapter-a",
                storyboard_asset_id="board-a",
                media_revision=1,
                asset_image_digest=None,
                preview_digest=None,
                source_valid=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(personal_production_notes).values(
                id="rollback-notes",
                chapter_id="chapter-a",
                user_id="owner",
                revision=1,
                frame_notes={"board-a": {"status": "approved", "approved_media_revision": 1}},
                resume_frame_id="board-a",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    before_content = _read_chapter_content(engine)
    before_lock = _read_lock(engine)
    before_media = _read_rows(engine, storyboard_media_states)
    before_notes = _read_rows(engine, personal_production_notes)
    observed: dict[str, object] = {}

    def fail_after_refresh(uow: SqlAlchemyCanvasSourceUnitOfWork) -> None:
        observed["content"] = uow.session.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one()
        lock = uow.session.execute(
            select(chapter_locks.c.last_active_at, chapter_locks.c.expires_at).where(
                chapter_locks.c.id == "lock-a"
            )
        ).one()
        observed["lock"] = tuple(lock)
        observed["media"] = _read_rows_from_session(uow.session, storyboard_media_states)
        observed["notes"] = _read_rows_from_session(uow.session, personal_production_notes)
        raise RuntimeError("synthetic failure after lock refresh")

    monkeypatch.setattr(SqlAlchemyCanvasSourceUnitOfWork, "commit", fail_after_refresh)
    current = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    response = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            current["version"],
            "media.update",
            {
                "type": "media.update",
                "target": {"index": 0, "storyboard_id": "board-a"},
                "field": "preview",
                "slot": None,
                "value": "https://example.test/rollback-preview.jpg",
            },
        ),
    )

    assert response.status_code == 500
    assert observed["content"] != before_content
    assert tuple(observed["lock"]) != tuple(before_lock)
    assert observed["media"] != before_media
    assert observed["notes"] != before_notes
    assert _read_chapter_content(engine) == before_content
    assert _read_lock(engine) == before_lock
    assert _read_rows(engine, storyboard_media_states) == before_media
    assert _read_rows(engine, personal_production_notes) == before_notes


def test_commit_ack_unknown_keeps_committed_truth_without_automatic_retry(
    source_system,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, engine = source_system
    original_commit = SqlAlchemyCanvasSourceUnitOfWork.commit
    commit_calls = 0

    def commit_then_lose_ack(uow: SqlAlchemyCanvasSourceUnitOfWork) -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit(uow)
        raise RuntimeError("synthetic commit acknowledgement lost")

    monkeypatch.setattr(SqlAlchemyCanvasSourceUnitOfWork, "commit", commit_then_lose_ack)
    initial = request(app, "GET", "/api/chapters/chapter-a/canvas-source").json()
    response = request(
        app,
        "POST",
        "/api/chapters/chapter-a/canvas-source",
        body=_source_command_body(
            initial["version"],
            "media.append",
            {
                "type": "media.append",
                "target": {"index": 0, "storyboard_id": "board-a"},
                "value": "https://example.test/one-committed-video.mp4",
            },
        ),
    )

    assert response.status_code == 500
    assert commit_calls == 1
    independent_read = request(app, "GET", "/api/chapters/chapter-a/canvas-source")
    assert independent_read.status_code == 200, independent_read.text
    assert independent_read.json()["chapter"]["content"][0]["videos"] == [
        "https://example.test/one-committed-video.mp4"
    ]


def _source_command_body(version: str, label: str, command: dict) -> dict:
    # The command label documents the case at call sites and is not sent on the wire.
    assert label == command["type"]
    request_ids = {
        "asset.create": "21000000-0000-4000-8000-000000000001",
        "frame.update": "21000000-0000-4000-8000-000000000002",
        "frame.insert": "21000000-0000-4000-8000-000000000003",
        "frame.delete": "21000000-0000-4000-8000-000000000004",
        "creation.undo": "21000000-0000-4000-8000-000000000005",
        "media.update": "21000000-0000-4000-8000-000000000006",
        "media.append": "21000000-0000-4000-8000-000000000007",
    }
    return {
        "capability": "canvas-source-v1",
        "request_id": request_ids[label],
        "expected_series_id": "series-a",
        "expected_version": version,
        "command": command,
    }


def _read_chapter_content(engine) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one()


def _read_lock(engine) -> tuple[object, ...]:
    with engine.connect() as connection:
        return tuple(
            connection.execute(
                select(chapter_locks.c.last_active_at, chapter_locks.c.expires_at).where(
                    chapter_locks.c.id == "lock-a"
                )
            ).one()
        )


def _read_rows(engine, table) -> list[dict[str, object]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(table)).mappings()]


def _read_rows_from_session(session: Session, table) -> list[dict[str, object]]:
    return [dict(row) for row in session.execute(select(table)).mappings()]
