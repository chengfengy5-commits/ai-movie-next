from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    storyboard_media_states,
)
from haoai_backend.personal_production.rough_cut.tables import metadata as rough_metadata
from haoai_backend.series_data.tables import (
    chapter_locks,
    chapters,
    chat_messages,
    team_members,
    teams,
    metadata as series_metadata,
    series,
    storyboard_assets,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.series_data.chapter_edit_guard import (
    ChapterEditGuardConflict,
    ChapterEditGuardInvalid,
    decode_chapter_content,
    strict_json_equal,
    validate_frame_sources,
)
from haoai_backend.series_data.errors import SeriesDataConflict
from haoai_backend.series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from haoai_backend.series_data.schemas import ChapterEditGuard
from haoai_backend.shared.identity import TrustedActor


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'continuous-chapter-edit.sqlite'}", future=True)
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
    sessions = sessionmaker(engine, expire_on_commit=False, future=True)
    yield engine, sessions
    engine.dispose()


def _app(sessions: sessionmaker[Session]):
    def resolve_actor(request) -> TrustedActor:
        if request.headers.get("x-test-user") != "user-a":
            raise AuthenticationError(401, "登录状态已失效")
        return TrustedActor(user_id="user-a")

    return create_app(session_factory=sessions, resolve_actor=resolve_actor)


def _request(app, method: str, path: str, body: dict[str, Any] | None = None, *, authenticated: bool = True):
    async def send() -> httpx.Response:
        headers = {"x-test-user": "user-a"} if authenticated else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://continuous-edit.test",
            headers=headers,
        ) as client:
            return await client.request(method, path, json=body)

    return asyncio.run(send())


def test_continuous_chapter_edit_capability_is_explicit_and_authenticated(database) -> None:
    _engine, sessions = database
    app = _app(sessions)

    supported = _request(app, "GET", "/api/chapters/continuous-edit/capabilities")
    unsupported_actor = _request(
        app, "GET", "/api/chapters/continuous-edit/capabilities", authenticated=False,
    )

    assert supported.status_code == 200
    assert supported.json() == {"features": {"chapter_edit_guard": 1}}
    assert unsupported_actor.status_code == 401


def test_source_map_keeps_identity_across_reorder_and_reports_explicit_deletion() -> None:
    old = [
        {"text": "旧甲", "storyboard": ["shot-a"], "original_text": "甲原文", "custom": {"keep": True}},
        {"text": "旧乙", "storyboard": ["shot-b"], "preview": None},
    ]
    new = [
        {"text": "乙已编辑", "storyboard": ["shot-b"], "preview": None},
        {"text": "全新", "character": [], "scene": [], "prop": [], "storyboard": [], "preview": None},
    ]

    mapping = validate_frame_sources(
        old,
        new,
        [
            {"kind": "retained", "source_index": 1, "storyboard_id": "shot-b"},
            {"kind": "new"},
        ],
        [0],
    )

    assert mapping == {0: None, 1: 0}


def test_json_numbers_use_browser_number_semantics_but_booleans_remain_distinct() -> None:
    assert strict_json_equal(1, 1.0)
    assert strict_json_equal({"unknown": [1]}, {"unknown": [1.0]})
    assert not strict_json_equal(True, 1)
    assert not strict_json_equal(9007199254740993, 9007199254740992)
    assert not strict_json_equal(10**25 + 1, 10**25)
    assert not strict_json_equal(10**25 + 1, float(10**25))
    assert not strict_json_equal(float("inf"), float("inf"))


def test_guarded_http_rejects_rounded_large_integer_baselines_and_retained_fields(database) -> None:
    engine, sessions = database
    now = datetime(2026, 1, 2, 3, 4, 5)
    large_integer = 9007199254740993
    old_frame = {
        "text": "原文段落",
        "character": [],
        "scene": [],
        "prop": [],
        "storyboard": ["shot-large"],
        "unknown": {"score": large_integer},
    }
    raw_content = json.dumps([old_frame], ensure_ascii=False)
    with engine.begin() as connection:
        connection.execute(insert(series).values(
            id="series-a", user_id="user-a", name="测试剧集", description=None,
            image_url=None, style_prompt_id=None, team_id=None, claimed_by=None,
            claimed_at=None, created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-a", series_id="series-a", title="第一章", content=raw_content,
            order=1, created_at=now, updated_at=now,
        ))
        connection.execute(insert(storyboard_assets).values(
            id="shot-large", series_id="series-a", chapter_id="chapter-a", frame_index=0,
            name="原分镜", description="原分镜", image_url=None, created_at=now, updated_at=now,
        ))

    def request_body(expected_content: list[dict[str, Any]], content: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "content": content,
            "chapter_edit_guard": {
                "version": 1,
                "expected_chapter_id": "chapter-a",
                "expected_series_id": "series-a",
                "expected_order": 1,
                "expected_content": expected_content,
                "expected_storyboards": [
                    {"id": "shot-large", "series_id": "series-a", "chapter_id": "chapter-a", "frame_index": 0},
                ],
                "expected_lock_identity": None,
                "frame_sources": [{"kind": "retained", "source_index": 0, "storyboard_id": "shot-large"}],
                "deleted_source_indexes": [],
            },
        }

    rounded = [{**old_frame, "unknown": {"score": 9007199254740992}}]
    stale_baseline = _request(
        _app(sessions), "PUT", "/api/chapters/chapter-a",
        request_body(rounded, [old_frame]),
    )
    changed_unknown = [{**old_frame, "text": "改写", "unknown": {"score": 9007199254740992}}]
    invalid_retained_value = _request(
        _app(sessions), "PUT", "/api/chapters/chapter-a",
        request_body([old_frame], changed_unknown),
    )
    with sessions() as session:
        current_raw = session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one()

    assert stale_baseline.status_code == 409
    assert invalid_retained_value.status_code == 400
    assert current_raw == raw_content


def test_source_map_allows_explicit_delete_of_every_frame() -> None:
    mapping = validate_frame_sources(
        [{"text": "甲", "storyboard": ["shot-a"]}],
        [],
        [],
        [0],
    )
    assert mapping == {0: None}


def test_guarded_http_update_reorders_deletes_and_remaps_atomically(database) -> None:
    engine, sessions = database
    now = datetime(2026, 1, 2, 3, 4, 5)
    old_frames = [
        {
            "text": "甲",
            "character": [],
            "scene": [],
            "prop": [],
            "storyboard": ["shot-a"],
            "preview": None,
            "original_text": "甲原文",
            "unknown": {"rating": 1.0},
        },
        {
            "text": "乙",
            "character": [],
            "scene": [],
            "prop": [],
            "storyboard": ["shot-b"],
            "preview": None,
            "original_text": "乙原文",
            "unknown": {"rating": 1.0},
            "extra": "保留",
        },
    ]
    raw_content = json.dumps(old_frames, ensure_ascii=False)
    with engine.begin() as connection:
        connection.execute(insert(series).values(
            id="series-a", user_id="user-a", name="测试剧集", description=None,
            image_url=None, style_prompt_id=None, team_id=None, claimed_by=None,
            claimed_at=None, created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-a", series_id="series-a", title="第一章", content=raw_content,
            order=1, created_at=now, updated_at=now,
        ))
        connection.execute(insert(storyboard_assets), [
            {"id": "shot-a", "series_id": "series-a", "chapter_id": "chapter-a", "frame_index": 0,
             "name": "甲", "description": "甲", "image_url": None, "created_at": now, "updated_at": now},
            {"id": "shot-b", "series_id": "series-a", "chapter_id": "chapter-a", "frame_index": 1,
             "name": "乙", "description": "乙", "image_url": None, "created_at": now, "updated_at": now},
            {"id": "orphan", "series_id": "series-a", "chapter_id": "chapter-a", "frame_index": 9,
             "name": "遗留游离行", "description": None, "image_url": None, "created_at": now, "updated_at": now},
        ])
        connection.execute(insert(storyboard_media_states).values(
            id="orphan-media-state", chapter_id="chapter-a", storyboard_asset_id="orphan",
            media_revision=4, asset_image_digest="a" * 64, preview_digest="b" * 64,
            source_valid=True, created_at=now, updated_at=now,
        ))
        connection.execute(insert(chat_messages), [
            {"id": "chat-a", "chapter_id": "chapter-a", "frame_index": 0, "asset_type": None,
             "asset_id": None, "chat_mode": "text", "role": "user", "content": "旧甲会话",
             "model_name": None, "created_at": now},
            {"id": "chat-b", "chapter_id": "chapter-a", "frame_index": 1, "asset_type": None,
             "asset_id": None, "chat_mode": "text", "role": "user", "content": "旧乙会话",
             "model_name": None, "created_at": now},
        ])

    next_content = [
        {**old_frames[1], "text": "乙的新文本", "unknown": {"rating": 1}},
        {"text": "丙", "character": [], "scene": [], "prop": [], "storyboard": [], "preview": None},
    ]
    expected_content = json.loads(json.dumps(old_frames, ensure_ascii=False))
    expected_content[0]["unknown"]["rating"] = 1
    body = {
        "content": next_content,
        "chapter_edit_guard": {
            "version": 1,
            "expected_chapter_id": "chapter-a",
            "expected_series_id": "series-a",
            "expected_order": 1,
            "expected_content": expected_content,
            "expected_storyboards": [
                {"id": "shot-a", "series_id": "series-a", "chapter_id": "chapter-a", "frame_index": 0},
                {"id": "shot-b", "series_id": "series-a", "chapter_id": "chapter-a", "frame_index": 1},
                {"id": "orphan", "series_id": "series-a", "chapter_id": "chapter-a", "frame_index": 9},
            ],
            "expected_lock_identity": None,
            "frame_sources": [
                {"kind": "retained", "source_index": 1, "storyboard_id": "shot-b"},
                {"kind": "new"},
            ],
            "deleted_source_indexes": [0],
        },
    }
    response = _request(_app(sessions), "PUT", "/api/chapters/chapter-a", body)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["content"][0]["storyboard"] == ["shot-b"]
    assert result["content"][0]["original_text"] == "乙原文"
    assert result["content"][0]["extra"] == "保留"
    assert result["content"][1]["storyboard"]
    with sessions() as session:
        assets = session.execute(
            select(storyboard_assets.c.id, storyboard_assets.c.frame_index)
            .where(storyboard_assets.c.chapter_id == "chapter-a")
        ).all()
        chats = session.execute(
            select(chat_messages.c.id, chat_messages.c.frame_index)
            .where(chat_messages.c.chapter_id == "chapter-a")
        ).all()
        orphan_state = session.execute(
            select(storyboard_media_states).where(
                storyboard_media_states.c.storyboard_asset_id == "orphan"
            )
        ).mappings().one()
    asset_map = {item.id: item.frame_index for item in assets}
    assert asset_map["shot-b"] == 0
    assert asset_map["orphan"] == 9
    assert "shot-a" not in asset_map
    assert {item.id: item.frame_index for item in chats} == {"chat-b": 0}
    assert orphan_state["source_valid"] is True
    assert orphan_state["media_revision"] == 4


def test_guarded_http_update_can_delete_all_frames_and_their_chat_indexes(database) -> None:
    engine, sessions = database
    now = datetime(2026, 1, 2, 3, 4, 5)
    old_frames = [{"text": "仅有镜头", "character": [], "scene": [], "prop": [], "storyboard": ["only-shot"]}]
    with engine.begin() as connection:
        connection.execute(insert(series).values(
            id="series-a", user_id="user-a", name="测试剧集", description=None,
            image_url=None, style_prompt_id=None, team_id=None, claimed_by=None,
            claimed_at=None, created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-a", series_id="series-a", title="第一章",
            content=json.dumps(old_frames, ensure_ascii=False), order=1,
            created_at=now, updated_at=now,
        ))
        connection.execute(insert(storyboard_assets).values(
            id="only-shot", series_id="series-a", chapter_id="chapter-a", frame_index=0,
            name="仅有镜头", description=None, image_url=None, created_at=now, updated_at=now,
        ))
        connection.execute(insert(chat_messages).values(
            id="chat-a", chapter_id="chapter-a", frame_index=0, asset_type=None,
            asset_id=None, chat_mode="text", role="user", content="原会话",
            model_name=None, created_at=now,
        ))
    body = {
        "content": [],
        "chapter_edit_guard": {
            "version": 1,
            "expected_chapter_id": "chapter-a",
            "expected_series_id": "series-a",
            "expected_order": 1,
            "expected_content": old_frames,
            "expected_storyboards": [
                {"id": "only-shot", "series_id": "series-a", "chapter_id": "chapter-a", "frame_index": 0},
            ],
            "expected_lock_identity": None,
            "frame_sources": [],
            "deleted_source_indexes": [0],
        },
    }
    response = _request(_app(sessions), "PUT", "/api/chapters/chapter-a", body)

    assert response.status_code == 200, response.text
    assert response.json()["content"] == []
    with sessions() as session:
        assert session.execute(select(storyboard_assets.c.id)).all() == []
        assert session.execute(select(chat_messages.c.id)).all() == []


def test_guarded_http_update_rejects_stale_content_without_mutation(database) -> None:
    engine, sessions = database
    now = datetime(2026, 1, 2, 3, 4, 5)
    current = [{"text": "已变更", "storyboard": []}]
    with engine.begin() as connection:
        connection.execute(insert(series).values(
            id="series-a", user_id="user-a", name="测试剧集", description=None,
            image_url=None, style_prompt_id=None, team_id=None, claimed_by=None,
            claimed_at=None, created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-a", series_id="series-a", title="第一章",
            content=json.dumps(current, ensure_ascii=False), order=1,
            created_at=now, updated_at=now,
        ))
    body = {
        "content": [{"text": "写入不应发生", "storyboard": []}],
        "chapter_edit_guard": {
            "version": 1,
            "expected_chapter_id": "chapter-a",
            "expected_series_id": "series-a",
            "expected_order": 1,
            "expected_content": [{"text": "旧内容", "storyboard": []}],
            "expected_storyboards": [],
            "expected_lock_identity": None,
            "frame_sources": [{"kind": "retained", "source_index": 0, "storyboard_id": None}],
            "deleted_source_indexes": [],
        },
    }
    response = _request(_app(sessions), "PUT", "/api/chapters/chapter-a", body)
    assert response.status_code == 409
    with sessions() as session:
        stored = session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one()
    assert json.loads(stored) == current


def test_guarded_update_requires_and_refreshes_the_original_team_lock(database) -> None:
    engine, sessions = database
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    expires = now + timedelta(days=1)
    old_frames = [{"text": "原文", "storyboard": []}]
    with engine.begin() as connection:
        connection.execute(insert(teams).values(id="team-a", name="团队", owner_id="user-a"))
        connection.execute(insert(team_members).values(
            id="member-a", team_id="team-a", user_id="user-a", role="owner", permissions="[]",
        ))
        connection.execute(insert(series).values(
            id="series-a", user_id="user-a", name="测试剧集", description=None,
            image_url=None, style_prompt_id=None, team_id="team-a", claimed_by=None,
            claimed_at=None, created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-a", series_id="series-a", title="第一章",
            content=json.dumps(old_frames, ensure_ascii=False), order=1,
            created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapter_locks).values(
            id="lock-a", chapter_id="chapter-a", user_id="user-a", username="作者",
            acquired_at=now, last_active_at=now, expires_at=expires,
        ))

    def request_with_lock_timestamp(acquired_at: str):
        return _request(_app(sessions), "PUT", "/api/chapters/chapter-a", {
            "content": [{"text": "新文本", "storyboard": []}],
            "chapter_edit_guard": {
                "version": 1,
                "expected_chapter_id": "chapter-a",
                "expected_series_id": "series-a",
                "expected_order": 1,
                "expected_content": old_frames,
                "expected_storyboards": [],
                "expected_lock_identity": {"user_id": "user-a", "acquired_at": acquired_at},
                "frame_sources": [{"kind": "retained", "source_index": 0, "storyboard_id": None}],
                "deleted_source_indexes": [],
            },
        })

    stale = request_with_lock_timestamp((now + timedelta(seconds=1)).isoformat())
    accepted = request_with_lock_timestamp(now.isoformat())
    assert stale.status_code == 409
    assert accepted.status_code == 200, accepted.text
    with sessions() as session:
        lock = session.execute(select(chapter_locks).where(chapter_locks.c.id == "lock-a")).mappings().one()
        stored_chapter = session.execute(select(chapters).where(chapters.c.id == "chapter-a")).mappings().one()
    assert lock["user_id"] == "user-a"
    assert lock["acquired_at"] == now
    assert lock["expires_at"] > now
    assert json.loads(stored_chapter["content"])[0]["text"] == "新文本"


def test_guarded_update_rolls_back_if_original_lock_expires_before_commit(database) -> None:
    engine, sessions = database
    initial_now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    expires = initial_now + timedelta(minutes=1)
    old_content = [{"text": "原文", "storyboard": []}]
    with engine.begin() as connection:
        connection.execute(insert(teams).values(id="team-late", name="测试团队", owner_id="user-a"))
        connection.execute(insert(team_members).values(
            id="member-late", team_id="team-late", user_id="user-a", role="owner", permissions="[]",
        ))
        connection.execute(insert(series).values(
            id="series-late", user_id="user-a", name="延迟锁测试", description=None,
            image_url=None, style_prompt_id=None, team_id="team-late", claimed_by=None,
            claimed_at=None, created_at=initial_now, updated_at=initial_now,
        ))
        connection.execute(insert(chapters).values(
            id="chapter-late", series_id="series-late", title="第一章",
            content=json.dumps(old_content, ensure_ascii=False), order=1,
            created_at=initial_now, updated_at=initial_now,
        ))
        connection.execute(insert(chapter_locks).values(
            id="lock-late", chapter_id="chapter-late", user_id="user-a", username="作者",
            acquired_at=initial_now, last_active_at=initial_now, expires_at=expires,
        ))
    body_guard = ChapterEditGuard.model_validate({
        "version": 1,
        "expected_chapter_id": "chapter-late",
        "expected_series_id": "series-late",
        "expected_order": 1,
        "expected_content": old_content,
        "expected_storyboards": [],
        "expected_lock_identity": {"user_id": "user-a", "acquired_at": initial_now.isoformat()},
        "frame_sources": [{"kind": "retained", "source_index": 0, "storyboard_id": None}],
        "deleted_source_indexes": [],
    })
    commit_time = expires + timedelta(seconds=1)
    with sessions() as session:
        uow = SqlAlchemySeriesDataUnitOfWork(session, now=lambda: commit_time)
        with pytest.raises(SeriesDataConflict, match="提交前已过期"):
            uow.update_chapter_guarded(
                TrustedActor(user_id="user-a"),
                "chapter-late",
                [{"text": "不能写入", "storyboard": []}],
                body_guard,
                initial_now,
            )
    with sessions() as session:
        stored_content = session.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-late")
        ).scalar_one()
        lock = session.execute(
            select(chapter_locks).where(chapter_locks.c.id == "lock-late")
        ).mappings().one()
    assert json.loads(stored_content) == old_content
    assert lock["expires_at"] == expires
    assert lock["acquired_at"] == initial_now


def test_guarded_mode_rejects_mixed_legacy_fields(database) -> None:
    _engine, sessions = database
    response = _request(_app(sessions), "PUT", "/api/chapters/chapter-a", {
        "content": [],
        "title": "不允许混写",
        "chapter_edit_guard": {},
    })
    assert response.status_code == 422


def test_source_map_rejects_duplicate_sources_and_mutated_preserved_fields() -> None:
    old = [{"text": "甲", "storyboard": ["shot-a"], "original_text": "原文"}]
    duplicate = [
        {"text": "甲", "storyboard": ["shot-a"], "original_text": "原文"},
        {"text": "甲副本", "storyboard": ["shot-a"], "original_text": "原文"},
    ]
    with pytest.raises(ChapterEditGuardInvalid, match="保留多次"):
        validate_frame_sources(
            old,
            duplicate,
            [
                {"kind": "retained", "source_index": 0, "storyboard_id": "shot-a"},
                {"kind": "retained", "source_index": 0, "storyboard_id": "shot-a"},
            ],
            [],
        )

    with pytest.raises(ChapterEditGuardInvalid, match="未知字段"):
        validate_frame_sources(
            old,
            [{"text": "甲", "storyboard": ["shot-a"], "original_text": "篡改"}],
            [{"kind": "retained", "source_index": 0, "storyboard_id": "shot-a"}],
            [],
        )


@pytest.mark.parametrize(
    "raw_content",
    [
        '[{"text":"甲","text":"乙"}]',
        '[{"text":NaN}]',
        '[{"text":"甲","value":Infinity}]',
        '["不是对象分镜"]',
    ],
)
def test_decoder_rejects_ambiguous_or_non_json_chapter_content(raw_content: str) -> None:
    with pytest.raises(ChapterEditGuardConflict):
        decode_chapter_content(raw_content)


def test_source_map_rejects_unlisted_deleted_frames_and_new_identity_leaks() -> None:
    with pytest.raises(ChapterEditGuardInvalid, match="删除来源"):
        validate_frame_sources(
            [{"text": "甲", "storyboard": []}],
            [{"text": "新"}],
            [{"kind": "new"}],
            [],
        )

    with pytest.raises(ChapterEditGuardInvalid, match="旧原文"):
        validate_frame_sources(
            [],
            [{"text": "新", "original_text": "旧原文"}],
            [{"kind": "new"}],
            [],
        )
