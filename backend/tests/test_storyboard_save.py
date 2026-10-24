from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.asset_data.naming import make_key
from haoai_backend.asset_data.tables import (
    characters,
    metadata as asset_metadata,
    props,
    scenes,
    storyboard_assets,
)
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_access.tables import metadata as access_metadata
from haoai_backend.series_data.tables import (
    chapters,
    metadata as series_metadata,
    series,
    team_members,
    teams,
    users,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_save.application import (
    save_characters_stream,
    save_props_stream,
    save_scenes_stream,
)
from haoai_backend.storyboard_save import http as storyboard_save_http
from haoai_backend.storyboard_save import persistence as storyboard_save_persistence
from haoai_backend.storyboard_save.http import build_storyboard_save_router
from haoai_backend.storyboard_save.persistence import SqlAlchemyStoryboardSaveStore
from haoai_backend.personal_production.notes.facade import (
    stage_media_reconciliation as public_stage_media_reconciliation,
)

NOW = datetime(2026, 10, 14)


class UnknownCommitAcknowledgement(RuntimeError):
    """The database committed, but the caller did not receive its acknowledgement."""


class TrackingSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False
        self.info["events"].append(("open", threading.get_ident()))

    def execute(self, *args, **kwargs):
        self.info["events"].append(("execute", threading.get_ident()))
        return super().execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_count += 1
        self.info["events"].append(("commit", threading.get_ident()))
        super().commit()
        if self.info.get("unknown_commit_ack_at") == self.commit_count:
            raise UnknownCommitAcknowledgement(
                f"commit acknowledgement unknown at phase {self.commit_count}"
            )

    def rollback(self) -> None:
        self.rollback_count += 1
        self.info["events"].append(("rollback", threading.get_ident()))
        super().rollback()

    def close(self) -> None:
        self.closed = True
        self.info["events"].append(("close", threading.get_ident()))
        super().close()


@dataclass
class StoryboardDatabase:
    engine: Any
    sessions: sessionmaker[TrackingSession]
    events: list[tuple[str, int]]
    opened_sessions: list[TrackingSession]
    unknown_commit_ack_at: int | None = None

    def business_session_factory(self) -> TrackingSession:
        self.events.append(("business_factory", threading.get_ident()))
        session = self.sessions()
        session.info["unknown_commit_ack_at"] = self.unknown_commit_ack_at
        self.opened_sessions.append(session)
        return session


@pytest.fixture
def storyboard_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'storyboard-save.sqlite'}", future=True)
    series_metadata.create_all(engine)
    asset_metadata.create_all(engine)
    access_metadata.create_all(engine)
    notes_metadata.create_all(engine)

    events: list[tuple[str, int]] = []
    opened_sessions: list[TrackingSession] = []
    sessions = sessionmaker(
        engine,
        class_=TrackingSession,
        expire_on_commit=False,
        future=True,
        info={"events": events},
    )
    database = StoryboardDatabase(engine, sessions, events, opened_sessions)
    _seed_access_fixtures(engine)
    try:
        yield database
    finally:
        engine.dispose()


def _seed_access_fixtures(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {"id": user_id, "username": user_id}
                for user_id in ("owner", "member", "viewer", "other")
            ],
        )
        connection.execute(
            insert(teams).values(id="team-a", name="测试团队", owner_id="owner")
        )
        connection.execute(
            insert(team_members),
            [
                {
                    "id": "member-access",
                    "team_id": "team-a",
                    "user_id": "member",
                    "role": "viewer",
                    "permissions": '["enter_claimed_series"]',
                },
                {
                    "id": "viewer-access",
                    "team_id": "team-a",
                    "user_id": "viewer",
                    "role": "viewer",
                    "permissions": None,
                },
            ],
        )
        connection.execute(
            insert(series),
            [
                _series_values("series-private", "owner"),
                _series_values("series-foreign", "owner"),
                _series_values("series-team", "owner", team_id="team-a"),
                _series_values(
                    "series-claimed",
                    "owner",
                    team_id="team-a",
                    claimed_by="owner",
                ),
            ],
        )
        connection.execute(
            insert(chapters),
            [
                _chapter_values("chapter-private", "series-private", "[]"),
                _chapter_values("chapter-team", "series-team", "[]"),
                _chapter_values("chapter-claimed", "series-claimed", "[]"),
            ],
        )


def _series_values(
    series_id: str,
    user_id: str,
    *,
    team_id: str | None = None,
    claimed_by: str | None = None,
) -> dict[str, Any]:
    return {
        "id": series_id,
        "user_id": user_id,
        "name": series_id,
        "description": None,
        "image_url": None,
        "style_prompt_id": None,
        "team_id": team_id,
        "claimed_by": claimed_by,
        "claimed_at": NOW if claimed_by else None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _chapter_values(chapter_id: str, series_id: str, content: str) -> dict[str, Any]:
    return {
        "id": chapter_id,
        "series_id": series_id,
        "title": chapter_id,
        "content": content,
        "order": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _insert_storyboard_assets(engine, chapter_id: str, series_id: str, frame_count: int) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(storyboard_assets),
            [
                {
                    "id": f"story-{index}",
                    "series_id": series_id,
                    "chapter_id": chapter_id,
                    "frame_index": index,
                    "name": f"分镜素材{index}",
                    "description": None,
                    "image_url": f"fixture://storyboard/{index}.png",
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for index in range(frame_count)
            ],
        )


def _replace_chapter_content(engine, chapter_id: str, content: object) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(chapters)
            .where(chapters.c.id == chapter_id)
            .values(content=json.dumps(content, ensure_ascii=False)),
        )


def _actor_resolver(events: list[tuple[str, int]] | None = None):
    def resolve_actor(request: Request) -> TrustedActor:
        if events is not None:
            events.append(("actor_resolved", threading.get_ident()))
        return TrustedActor(request.headers.get("x-actor", "owner"))

    return resolve_actor


def _app(database: StoryboardDatabase, *, resolve_actor=None) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_storyboard_save_router(
            session_factory=database.business_session_factory,
            resolve_actor=resolve_actor or _actor_resolver(database.events),
        )
    )
    return app


def _request(app: FastAPI, path: str, *, json_body: object = None, headers=None, content=None):
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://storyboard-save.test",
        ) as client:
            return await client.post(
                path,
                json=json_body,
                headers=headers,
                content=content,
            )

    return asyncio.run(send())


def _events(response: httpx.Response) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_character_save_learns_alias_updates_only_absolute_target_and_protects_media_assets(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    content = [
        {
            "text": "未选中的分镜",
            "storyboard": ["story-0"],
            "extra": {"keep": True},
            "character": ["char-used"],
            "scene": ["scene-keep"],
        },
        {
            "text": "本次选中的分镜",
            "storyboard": ["story-1"],
            "character": ["stale-id"],
            "prop": ["prop-keep"],
        },
    ]
    _replace_chapter_content(db.engine, "chapter-private", content)
    _insert_storyboard_assets(db.engine, "chapter-private", "series-private", 2)
    with db.engine.begin() as connection:
        connection.execute(
            insert(characters),
            [
                _character_values("char-used", "周晴"),
                _character_values("char-image", "有图片", image_url=" "),
                _character_values("char-audio", "有音频", audio_url="fixture://audio"),
                _character_values("char-voice", "有音色", voice_ref="voice-profile"),
                _character_values("char-delete", "未引用"),
            ],
        )

    session = db.sessions()
    events = list(
        save_characters_stream(
            SqlAlchemyStoryboardSaveStore(session),
            TrustedActor("owner"),
            "chapter-private",
            [
                {
                    "name": "周晴",
                    "is_existing": True,
                    "existing_id": "char-used",
                    "original_name": "小晴",
                }
            ],
            [["小晴"]],
            target_frames=[1],
        )
    )

    status_messages = [
        event["message"] for event in events if event.get("type") == "status"
    ]
    assert "正在收集实际出场的角色..." in status_messages
    assert "正在分类角色..." in status_messages
    assert status_messages.index("正在收集实际出场的角色...") < status_messages.index(
        "正在分类角色..."
    )
    assert events[-1]["type"] == "complete"
    assert events[-1]["cleaned_up_count"] == 1
    assert session.commit_count == 4
    session.close()

    with db.engine.connect() as connection:
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
        surviving_characters = set(
            connection.execute(select(characters.c.id)).scalars().all()
        )
        learned_aliases = connection.execute(
            select(characters.c.aliases).where(characters.c.id == "char-used")
        ).scalar_one()
        media_state_ids = set(
            connection.execute(
                select(storyboard_media_states.c.storyboard_asset_id).where(
                    storyboard_media_states.c.chapter_id == "chapter-private"
                )
            ).scalars().all()
        )

    assert saved_content[0] == content[0]
    assert saved_content[1]["character"] == ["char-used"]
    assert saved_content[1]["storyboard"] == ["story-1"]
    assert saved_content[1]["prop"] == ["prop-keep"]
    assert surviving_characters == {
        "char-used",
        "char-image",
        "char-audio",
        "char-voice",
    }
    assert "小晴" in json.loads(learned_aliases)
    assert media_state_ids == {"story-0", "story-1"}


def test_scene_candidate_canonical_match_does_not_expand_library_fuzzy_reuse(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    content = [
        {"text": "第一帧", "scene": ["scene-original"], "storyboard": ["story-0"]},
        {
            "text": "第三帧",
            "scene": ["old-binding"],
            "storyboard": ["story-1"],
            "custom": {"camera": "slow pan"},
        },
    ]
    _replace_chapter_content(db.engine, "chapter-private", content)
    _insert_storyboard_assets(db.engine, "chapter-private", "series-private", 2)
    with db.engine.begin() as connection:
        connection.execute(
            insert(scenes).values(
                id="scene-existing",
                series_id="series-private",
                title="盐宴厅·夜晚",
                description="旧描述",
                image_url=None,
                aliases='["大厅"]',
                canonical_key=make_key("scene", "盐宴厅·夜晚"),
                created_at=NOW,
                updated_at=NOW,
            )
        )

    session = db.sessions()
    events = list(
        save_scenes_stream(
            SqlAlchemyStoryboardSaveStore(session),
            TrustedActor("owner"),
            "chapter-private",
            [
                {
                    "title": "盐宴厅_夜",
                    "description": "确认后的描述",
                    "original_name": "旧宴厅",
                }
            ],
            [["盐宴厅·夜晚"]],
            target_frames=[1],
        )
    )

    assert events[-1]["type"] == "complete"
    assert events[-1]["created_count"] == 1
    assert events[-1]["reused_count"] == 0
    assert session.commit_count == 3
    session.close()

    with db.engine.connect() as connection:
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
        scene_rows = connection.execute(
            select(scenes.c.id, scenes.c.title, scenes.c.description, scenes.c.aliases)
            .where(scenes.c.series_id == "series-private")
        ).mappings().all()

    assert saved_content[0] == content[0]
    bound_scene_id = saved_content[1]["scene"][0]
    assert bound_scene_id != "scene-existing"
    assert saved_content[1]["storyboard"] == ["story-1"]
    assert saved_content[1]["custom"] == {"camera": "slow pan"}
    rows_by_id = {row["id"]: row for row in scene_rows}
    assert rows_by_id["scene-existing"]["title"] == "盐宴厅·夜晚"
    assert rows_by_id["scene-existing"]["description"] == "旧描述"
    assert json.loads(rows_by_id["scene-existing"]["aliases"]) == ["大厅"]
    assert rows_by_id[bound_scene_id]["title"] == "盐宴厅_夜"
    assert rows_by_id[bound_scene_id]["description"] == "确认后的描述"
    assert "旧宴厅" in json.loads(rows_by_id[bound_scene_id]["aliases"])


@pytest.mark.parametrize("clear_selection", [[], "  "])
def test_scene_explicit_empty_selection_clears_only_selected_frame_and_dry_run_is_read_only(
    storyboard_database: StoryboardDatabase,
    clear_selection: object,
) -> None:
    db = storyboard_database
    wire_fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "frontend/src/testing/fixtures/storyboardAssetWire.json")
        .read_text(encoding="utf-8")
    )
    original_content = [
        {"text": "清空场景", "scene": ["old-clear"], "storyboard": ["story-0"]},
        {
            "text": "绑定有效候选",
            "scene": ["old-valid"],
            "storyboard": ["story-1"],
            "camera": {"move": "dolly"},
        },
        {"text": "未返回判定", "scene": ["keep-null"], "storyboard": ["story-2"]},
        {"text": "无法匹配", "scene": ["keep-unmatched"], "storyboard": ["story-3"]},
        {"text": "未选择", "scene": ["keep-unselected"], "storyboard": ["story-4"]},
    ]
    _replace_chapter_content(db.engine, "chapter-private", original_content)
    _insert_storyboard_assets(db.engine, "chapter-private", "series-private", 5)
    body = {
        "chapter_id": "chapter-private",
        "scenes": [{"title": "阁楼", "description": "新确认的阁楼"}],
        "appearances": [clear_selection, ["阁楼"], None, ["不存在的场景"]],
        "target_frames": [0, 1, 2, 3],
    }

    dry_run_response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-scenes-stream",
        json_body={**body, "dry_run": True},
    )

    assert dry_run_response.status_code == 200
    dry_run_events = _events(dry_run_response)
    assert dry_run_events[-1] == {"type": "done"}
    dry_run_plan = next(event for event in dry_run_events if event["type"] == "plan")
    assert dry_run_plan == {"type": "plan", **wire_fixture["save_plan"]}
    assert next(event for event in dry_run_events if event["type"] == "complete") == wire_fixture["save_dry_run_complete"]
    assert db.opened_sessions[-1].commit_count == 0
    with db.engine.connect() as connection:
        after_dry_run = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
    assert after_dry_run == original_content

    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-scenes-stream",
        json_body={**body, "dry_run": False},
    )

    assert response.status_code == 200
    events = _events(response)
    assert events[-1] == {"type": "done"}
    complete = next(event for event in events if event["type"] == "complete")
    assert complete["unmatched_frames"] == [4]
    with db.engine.connect() as connection:
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
        attic_scene_id = connection.execute(
            select(scenes.c.id).where(
                scenes.c.series_id == "series-private",
                scenes.c.title == "阁楼",
            )
        ).scalar_one()

    assert saved_content[0]["scene"] == []
    assert saved_content[1]["scene"] == [attic_scene_id]
    assert saved_content[1]["camera"] == {"move": "dolly"}
    assert saved_content[2] == original_content[2]
    assert saved_content[3] == original_content[3]
    assert saved_content[4] == original_content[4]


def test_props_empty_target_list_means_all_frames_and_duplicate_candidates_keep_legacy_inserts(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    content = [
        {"prop": ["old-a"], "storyboard": ["story-0"]},
        {"prop": ["preserve-me"], "storyboard": ["story-1"]},
        {"prop": ["clear-me"], "storyboard": ["story-2"]},
    ]
    _replace_chapter_content(db.engine, "chapter-private", content)
    _insert_storyboard_assets(db.engine, "chapter-private", "series-private", 3)

    session = db.sessions()
    events = list(
        save_props_stream(
            SqlAlchemyStoryboardSaveStore(session),
            TrustedActor("owner"),
            "chapter-private",
            [{"name": "铜镜"}, {"name": "铜镜"}],
            [["铜镜"], None, []],
            target_frames=[],
        )
    )

    assert events[-1]["type"] == "complete"
    assert events[-1]["created_count"] == 2
    assert events[-1]["total_count"] == 1
    session.close()

    with db.engine.connect() as connection:
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
        prop_ids = set(
            connection.execute(select(props.c.id).where(props.c.series_id == "series-private"))
            .scalars()
            .all()
        )

    assert len(prop_ids) == 2
    assert len(saved_content[0]["prop"]) == 1
    assert saved_content[0]["prop"][0] in prop_ids
    assert saved_content[1]["prop"] == ["preserve-me"]
    assert saved_content[2]["prop"] == []
    assert saved_content[0]["storyboard"] == ["story-0"]


def test_prop_fuzzy_reuse_learns_original_name_and_preserves_missing_appearance_rows(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    content = [
        {"prop": ["old-prop"], "storyboard": ["story-0"], "extra": "keep"},
        {"prop": ["preserve-me"], "storyboard": ["story-1"]},
    ]
    _replace_chapter_content(db.engine, "chapter-private", content)
    _insert_storyboard_assets(db.engine, "chapter-private", "series-private", 2)
    with db.engine.begin() as connection:
        connection.execute(
            insert(props).values(
                **_prop_values("prop-existing", "青铜枝纹长柄镜")
            )
        )

    session = db.sessions()
    events = list(
        save_props_stream(
            SqlAlchemyStoryboardSaveStore(session),
            TrustedActor("owner"),
            "chapter-private",
            [
                {
                    "name": "青铜枝纹长柄镜子",
                    "original_name": "铜镜",
                    "description": "确认后的描述",
                }
            ],
            [["铜镜"], None],
        )
    )

    assert events[-1]["type"] == "complete"
    assert events[-1]["created_count"] == 0
    assert events[-1]["reused_count"] == 1
    session.close()

    with db.engine.connect() as connection:
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
        learned_aliases = connection.execute(
            select(props.c.aliases).where(props.c.id == "prop-existing")
        ).scalar_one()

    assert saved_content[0]["prop"] == ["prop-existing"]
    assert saved_content[0]["extra"] == "keep"
    assert saved_content[1] == content[1]
    assert "铜镜" in json.loads(learned_aliases)


def test_character_dry_run_maps_aliases_and_out_of_range_absolute_indices_without_writes(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    content = [
        {"character": ["original-0"]},
        {"character": ["original-1"]},
        {"character": ["original-2"]},
    ]
    _replace_chapter_content(db.engine, "chapter-private", content)
    _insert_storyboard_assets(db.engine, "chapter-private", "series-private", 3)
    with db.engine.begin() as connection:
        connection.execute(
            insert(characters).values(
                **_character_values("char-existing", "霜月", aliases='["旧月"]')
            )
        )

    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-characters-stream",
        json_body={
            "chapter_id": "chapter-private",
            "merged_characters": [
                {
                    "name": "霜月",
                    "is_existing": True,
                    "existing_id": "char-existing",
                    "aliases": ["旧月"],
                }
            ],
            "appearances": [["旧月"], None, [], ["霜月"]],
            "target_frames": [2, 5, 0, 7],
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    events = _events(response)
    plan = next(event for event in events if event["type"] == "plan")
    assert plan["items"][0]["frames"] == [3, 8]
    assert plan["frame_map"] == [
        {"frame": 1, "candidates": []},
        {"frame": 3, "candidates": ["霜月"]},
        {"frame": 8, "candidates": ["霜月"]},
    ]
    assert events[-1]["type"] == "done"
    assert db.opened_sessions[-1].commit_count == 0
    assert db.opened_sessions[-1].closed is True

    with db.engine.connect() as connection:
        saved_content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-private")
        ).scalar_one()
        count = connection.execute(
            select(characters.c.id).where(characters.c.id == "char-existing")
        ).scalar_one_or_none()
        media_state_count = connection.execute(
            select(storyboard_media_states.c.id).where(
                storyboard_media_states.c.chapter_id == "chapter-private"
            )
        ).scalars().all()
    assert json.loads(saved_content) == content
    assert count == "char-existing"
    assert media_state_count == []

    lifecycle = [event for event in db.events if event[0] in {"actor_resolved", "business_factory"}]
    assert lifecycle[0][0] == "actor_resolved"
    assert lifecycle[1][0] == "business_factory"
    sql_threads = {thread_id for event, thread_id in db.events if event in {"execute", "commit", "rollback", "close"}}
    assert len(sql_threads) == 1
    assert threading.get_ident() not in sql_threads


def test_boolean_target_frame_keeps_legacy_integer_index_semantics(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    _replace_chapter_content(db.engine, "chapter-private", [{}, {}])
    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-props-stream",
        json_body={
            "chapter_id": "chapter-private",
            "props": [{"name": "铜镜"}],
            "appearances": [["铜镜"]],
            "target_frames": [True],
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    plan = next(event for event in _events(response) if event["type"] == "plan")
    assert plan["frame_map"] == [{"frame": 2, "candidates": ["铜镜"]}]
    assert plan["items"][0]["frames"] == [2]


@pytest.mark.parametrize(
    ("path", "candidate_field", "candidate", "local_candidate", "local_name", "foreign_name"),
    [
        (
            "/api/storyboard/filter-and-save-characters-stream",
            "merged_characters",
            {
                "name": "外部角色",
                "is_existing": True,
                "existing_id": "foreign-character",
                "original_name": "外部旧名",
            },
            {"name": "本地候选角色"},
            "本地候选角色",
            "外部角色",
        ),
        (
            "/api/storyboard/filter-and-save-scenes-stream",
            "scenes",
            {"title": "外部场景", "existing_id": "foreign-scene", "original_name": "外部旧景"},
            {"title": "本地候选场景"},
            "本地候选场景",
            "外部场景",
        ),
        (
            "/api/storyboard/filter-and-save-props-stream",
            "props",
            {"name": "外部道具", "existing_id": "foreign-prop", "original_name": "外部旧物"},
            {"name": "本地候选道具"},
            "本地候选道具",
            "外部道具",
        ),
    ],
)
def test_foreign_existing_id_is_rejected_before_any_save_phase(
    storyboard_database: StoryboardDatabase,
    path: str,
    candidate_field: str,
    candidate: dict[str, Any],
    local_candidate: dict[str, Any],
    local_name: str,
    foreign_name: str,
) -> None:
    db = storyboard_database
    _replace_chapter_content(db.engine, "chapter-private", [{"text": "unchanged"}])
    with db.engine.begin() as connection:
        if candidate_field == "merged_characters":
            asset_table = characters
            values = _character_values("foreign-character", "外部角色")
            values["series_id"] = "series-foreign"
            connection.execute(insert(characters).values(**values))
        elif candidate_field == "scenes":
            asset_table = scenes
            values = {
                "id": "foreign-scene",
                "series_id": "series-foreign",
                "title": "外部场景",
                "description": None,
                "image_url": None,
                "aliases": "[]",
                "canonical_key": make_key("scene", "外部场景"),
                "created_at": NOW,
                "updated_at": NOW,
            }
            connection.execute(insert(scenes).values(**values))
        else:
            asset_table = props
            values = _prop_values("foreign-prop", "外部道具")
            values["series_id"] = "series-foreign"
            connection.execute(insert(props).values(**values))

    response = _request(
        _app(db),
        path,
        json_body={
            "chapter_id": "chapter-private",
            candidate_field: [local_candidate, candidate],
            "appearances": [[local_name, foreign_name]],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert events[-2] == {"type": "error", "message": "素材引用不属于当前剧集"}
    assert events[-1] == {"type": "done"}
    assert db.opened_sessions[-1].commit_count == 0

    with db.engine.connect() as connection:
        unchanged = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-private")
        ).scalar_one()
        target_asset_ids = connection.execute(
            select(asset_table.c.id).where(asset_table.c.series_id == "series-private")
        ).scalars().all()
        if candidate_field == "merged_characters":
            aliases = connection.execute(
                select(characters.c.aliases).where(characters.c.id == "foreign-character")
            ).scalar_one()
        elif candidate_field == "scenes":
            aliases = connection.execute(
                select(scenes.c.aliases).where(scenes.c.id == "foreign-scene")
            ).scalar_one()
        else:
            aliases = connection.execute(
                select(props.c.aliases).where(props.c.id == "foreign-prop")
            ).scalar_one()
    assert json.loads(unchanged) == [{"text": "unchanged"}]
    assert target_asset_ids == []
    assert json.loads(aliases) == []


@pytest.mark.parametrize(
    ("kind", "candidate", "appearance", "asset_name"),
    [
        ("character", {"name": "新角色"}, ["新角色"], "新角色"),
        ("scene", {"title": "新场景"}, ["新场景"], "新场景"),
        ("prop", {"name": "新道具"}, ["新道具"], "新道具"),
    ],
)
def test_new_assets_start_with_empty_aliases_and_canonical_key(
    storyboard_database: StoryboardDatabase,
    kind: str,
    candidate: dict[str, Any],
    appearance: list[str],
    asset_name: str,
) -> None:
    db = storyboard_database
    _replace_chapter_content(db.engine, "chapter-private", [{}])
    candidates = [candidate]
    if kind == "character":
        candidates[0]["is_existing"] = False
    events, _session = _run_direct_save(db, kind, candidates, [appearance])
    assert events[-1]["type"] == "complete"

    table = {"character": characters, "scene": scenes, "prop": props}[kind]
    name_column = "title" if kind == "scene" else "name"
    with db.engine.connect() as connection:
        saved = connection.execute(
            select(table.c.id, table.c.aliases, table.c.canonical_key)
            .where(table.c.series_id == "series-private", getattr(table.c, name_column) == asset_name)
        ).mappings().one()

    assert saved["aliases"] == "[]"
    assert saved["canonical_key"] == make_key(kind, asset_name)


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
def test_alias_dirty_refreshes_canonical_key_for_all_asset_kinds(
    storyboard_database: StoryboardDatabase,
    kind: str,
) -> None:
    db = storyboard_database
    _replace_chapter_content(db.engine, "chapter-private", [{}])
    asset_id = f"existing-{kind}"
    name = {"character": "周晴", "scene": "宴会厅", "prop": "铜镜"}[kind]
    original_name = {"character": "小晴", "scene": "旧宴厅", "prop": "古镜"}[kind]
    table = {"character": characters, "scene": scenes, "prop": props}[kind]
    name_column = "title" if kind == "scene" else "name"

    with db.engine.begin() as connection:
        if kind == "character":
            values = _character_values(asset_id, name, aliases="[]")
            values["canonical_key"] = "stale-key"
            connection.execute(insert(characters).values(**values))
            candidate = {
                "name": name,
                "is_existing": True,
                "existing_id": asset_id,
                "original_name": original_name,
            }
        elif kind == "scene":
            connection.execute(
                insert(scenes).values(
                    id=asset_id,
                    series_id="series-private",
                    title=name,
                    description=None,
                    image_url=None,
                    aliases="[]",
                    canonical_key="stale-key",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            candidate = {"title": name, "original_name": original_name}
        else:
            values = _prop_values(asset_id, name)
            values["aliases"] = "[]"
            values["canonical_key"] = "stale-key"
            connection.execute(insert(props).values(**values))
            candidate = {"name": name, "original_name": original_name}

    events, _session = _run_direct_save(db, kind, [candidate], [[name]])
    assert events[-1]["type"] == "complete"

    with db.engine.connect() as connection:
        saved = connection.execute(
            select(table.c.aliases, table.c.canonical_key).where(table.c.id == asset_id)
        ).mappings().one()
    assert original_name in json.loads(saved["aliases"])
    assert saved["canonical_key"] == make_key(kind, name)


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
def test_same_alias_does_not_dirty_stored_canonical_key(
    storyboard_database: StoryboardDatabase,
    kind: str,
) -> None:
    db = storyboard_database
    _replace_chapter_content(db.engine, "chapter-private", [{}])
    asset_id = f"same-alias-{kind}"
    name = {"character": "周晴", "scene": "宴会厅", "prop": "铜镜"}[kind]
    original_name = {"character": "小晴", "scene": "旧宴厅", "prop": "古镜"}[kind]
    table = {"character": characters, "scene": scenes, "prop": props}[kind]
    with db.engine.begin() as connection:
        if kind == "character":
            values = _character_values(asset_id, name, aliases=json.dumps([original_name]))
            values["canonical_key"] = "stale-key"
            connection.execute(insert(characters).values(**values))
            candidate = {
                "name": name,
                "is_existing": True,
                "existing_id": asset_id,
                "original_name": original_name,
            }
        elif kind == "scene":
            connection.execute(
                insert(scenes).values(
                    id=asset_id,
                    series_id="series-private",
                    title=name,
                    description=None,
                    image_url=None,
                    aliases=json.dumps([original_name], ensure_ascii=False),
                    canonical_key="stale-key",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            candidate = {"title": name, "original_name": original_name}
        else:
            values = _prop_values(asset_id, name)
            values["aliases"] = json.dumps([original_name], ensure_ascii=False)
            values["canonical_key"] = "stale-key"
            connection.execute(insert(props).values(**values))
            candidate = {"name": name, "original_name": original_name}

    events, _session = _run_direct_save(db, kind, [candidate], [[name]])
    assert events[-1]["type"] == "complete"
    with db.engine.connect() as connection:
        saved = connection.execute(
            select(table.c.aliases, table.c.canonical_key).where(table.c.id == asset_id)
        ).mappings().one()
    assert original_name in json.loads(saved["aliases"])
    assert saved["canonical_key"] == "stale-key"


def test_auth_error_precedes_json_parsing_and_keeps_bearer_challenge(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    app = _app(
        db,
        resolve_actor=lambda _request: (_ for _ in ()).throw(
            AuthenticationError(401, "无法验证凭据", authenticate=True)
        ),
    )

    response = _request(
        app,
        "/api/storyboard/filter-and-save-scenes-stream",
        content=b"{malformed",
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"] == "无法验证凭据"
    assert db.opened_sessions == []


@pytest.mark.parametrize(
    ("ports", "resolve_actor"),
    [
        (None, None),
        ("business", None),
        ("business", lambda _request: None),
        ("business", lambda _request: TrustedActor("")),
    ],
)
def test_missing_ports_and_invalid_actor_return_503_without_business_session(
    storyboard_database: StoryboardDatabase,
    ports: str | None,
    resolve_actor,
) -> None:
    db = storyboard_database
    app = FastAPI()
    app.include_router(
        build_storyboard_save_router(
            session_factory=db.business_session_factory if ports else None,
            resolve_actor=resolve_actor,
        )
    )

    response = _request(
        app,
        "/api/storyboard/filter-and-save-scenes-stream",
        content=b"{malformed",
    )

    assert response.status_code == 503
    assert response.json()["detail"]
    assert db.opened_sessions == []


def test_sync_actor_resolver_runs_off_loop_and_exits_before_business_session(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    event_loop_thread_id = threading.get_ident()

    def resolve_actor(_request: Request) -> TrustedActor:
        db.events.append(("auth_context_open", threading.get_ident()))
        try:
            return TrustedActor("owner")
        finally:
            db.events.append(("auth_context_close", threading.get_ident()))

    response = _request(
        _app(db, resolve_actor=resolve_actor),
        "/api/storyboard/filter-and-save-scenes-stream",
        json_body={
            "chapter_id": "chapter-private",
            "scenes": [{"title": "宴会厅"}],
            "appearances": [["宴会厅"]],
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    auth_events = [
        event for event in db.events if event[0] in {"auth_context_open", "auth_context_close"}
    ]
    assert len(auth_events) == 2
    assert auth_events[0][0] == "auth_context_open"
    assert auth_events[1][0] == "auth_context_close"
    assert auth_events[0][1] != event_loop_thread_id
    assert auth_events[1][1] == auth_events[0][1]
    assert db.events.index(auth_events[1]) < next(
        index for index, event in enumerate(db.events) if event[0] == "business_factory"
    )


@pytest.mark.parametrize("phase", ["open", "authorize", "initialize"])
def test_pre_response_cancellation_closes_session_on_worker_and_shuts_executor(
    storyboard_database: StoryboardDatabase,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    db = storyboard_database
    worker_entered = threading.Event()
    release_worker = threading.Event()
    worker_thread_ids: list[int] = []
    executors: list[ThreadPoolExecutor] = []

    class RecordingExecutor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.futures: list[Future[Any]] = []
            self.shutdown_calls = 0
            executors.append(self)

        def submit(self, fn, /, *args, **kwargs) -> Future[Any]:
            future = super().submit(fn, *args, **kwargs)
            self.futures.append(future)
            return future

        def shutdown(self, *args, **kwargs) -> None:
            self.shutdown_calls += 1
            super().shutdown(*args, **kwargs)

    monkeypatch.setattr(storyboard_save_http, "ThreadPoolExecutor", RecordingExecutor)

    def gate_worker() -> None:
        worker_thread_ids.append(threading.get_ident())
        worker_entered.set()
        if not release_worker.wait(timeout=3):
            raise TimeoutError("test did not release the gated worker")

    base_session_factory = db.business_session_factory
    session_factory = base_session_factory
    if phase == "open":
        def gated_session_factory() -> TrackingSession:
            gate_worker()
            return base_session_factory()

        session_factory = gated_session_factory
    elif phase == "authorize":
        original_require_access = SqlAlchemyStoryboardSaveStore.require_access

        def gated_require_access(
            store: SqlAlchemyStoryboardSaveStore,
            actor: TrustedActor,
            series_id: str,
        ) -> None:
            gate_worker()
            original_require_access(store, actor, series_id)

        monkeypatch.setattr(
            SqlAlchemyStoryboardSaveStore, "require_access", gated_require_access
        )
    else:
        routes = list(storyboard_save_http._ROUTES)
        for index, route in enumerate(routes):
            if route[0] != "/filter-and-save-props-stream":
                continue

            def gated_workflow(*_args, **_kwargs):
                gate_worker()
                return iter(())

            routes[index] = (*route[:-1], gated_workflow)
            break
        else:
            raise AssertionError("props save route was not registered")
        monkeypatch.setattr(storyboard_save_http, "_ROUTES", tuple(routes))

    app = FastAPI()
    app.include_router(
        build_storyboard_save_router(
            session_factory=session_factory,
            resolve_actor=_actor_resolver(),
        )
    )

    async def exercise_cancellation() -> None:
        request_task: asyncio.Task[httpx.Response] | None = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://storyboard-save.test",
            ) as client:
                request_task = asyncio.create_task(
                    client.post(
                        "/api/storyboard/filter-and-save-props-stream",
                        json={
                            "chapter_id": "chapter-private",
                            "props": [{"name": "铜镜"}],
                            "appearances": [["铜镜"]],
                        },
                    )
                )
                entered = await asyncio.wait_for(
                    asyncio.to_thread(worker_entered.wait, 1.5), timeout=2
                )
                assert entered, f"{phase} worker did not reach its gate"
                request_task.cancel()
                await asyncio.sleep(0)
                release_worker.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request_task, timeout=2)

            for executor in executors:
                for future in tuple(executor.futures):
                    try:
                        await asyncio.to_thread(future.result, 2)
                    except Exception:
                        pass

            assert len(executors) == 1
            assert len(db.opened_sessions) == 1
            assert db.opened_sessions[0].closed is True
            close_events = [event for event in db.events if event[0] == "close"]
            assert len(close_events) == 1
            assert close_events[0][1] == worker_thread_ids[0]
            assert executors[0].shutdown_calls >= 1
        finally:
            release_worker.set()
            if request_task is not None and not request_task.done():
                request_task.cancel()
                try:
                    await asyncio.wait_for(request_task, timeout=1)
                except (asyncio.CancelledError, TimeoutError, Exception):
                    pass
            for executor in executors:
                for future in tuple(executor.futures):
                    try:
                        await asyncio.to_thread(future.result, 2)
                    except Exception:
                        pass
                if executor.shutdown_calls == 0:
                    await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise_cancellation())


@pytest.mark.parametrize("phase", ["open", "authorize", "initialize"])
def test_double_cancellation_during_worker_drain_preserves_resources(
    storyboard_database: StoryboardDatabase,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    db = storyboard_database
    worker_entered = threading.Event()
    release_worker = threading.Event()
    drain_started = threading.Event()
    worker_thread_ids: list[int] = []
    iterator_close_threads: list[int] = []
    created_iterators: list[Any] = []
    executors: list[ThreadPoolExecutor] = []

    class RecordingExecutor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.futures: list[Future[Any]] = []
            self.shutdown_calls = 0
            executors.append(self)

        def submit(self, fn, /, *args, **kwargs) -> Future[Any]:
            future = super().submit(fn, *args, **kwargs)
            self.futures.append(future)
            return future

        def shutdown(self, *args, **kwargs) -> None:
            self.shutdown_calls += 1
            super().shutdown(*args, **kwargs)

    class CloseTrackedIterator:
        def __init__(self) -> None:
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

        def close(self) -> None:
            self.closed = True
            iterator_close_threads.append(threading.get_ident())

    class ShieldObserver:
        def __init__(self) -> None:
            self.calls: dict[asyncio.Future[Any], int] = {}

        def __getattr__(self, name: str) -> Any:
            return getattr(asyncio, name)

        def shield(self, future, *args, **kwargs):
            if isinstance(future, asyncio.Future) and not future.done():
                count = self.calls.get(future, 0) + 1
                self.calls[future] = count
                if count == 2:
                    drain_started.set()
            return asyncio.shield(future, *args, **kwargs)

    monkeypatch.setattr(storyboard_save_http, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(storyboard_save_http, "asyncio", ShieldObserver())

    def gate_worker() -> None:
        worker_thread_ids.append(threading.get_ident())
        worker_entered.set()
        if not release_worker.wait(timeout=3):
            raise TimeoutError("test did not release the gated worker")

    base_session_factory = db.business_session_factory
    session_factory = base_session_factory
    if phase == "open":
        def gated_session_factory() -> TrackingSession:
            gate_worker()
            return base_session_factory()

        session_factory = gated_session_factory
    elif phase == "authorize":
        original_require_access = SqlAlchemyStoryboardSaveStore.require_access

        def gated_require_access(
            store: SqlAlchemyStoryboardSaveStore,
            actor: TrustedActor,
            series_id: str,
        ) -> None:
            gate_worker()
            original_require_access(store, actor, series_id)

        monkeypatch.setattr(
            SqlAlchemyStoryboardSaveStore, "require_access", gated_require_access
        )
    else:
        routes = list(storyboard_save_http._ROUTES)
        for index, route in enumerate(routes):
            if route[0] != "/filter-and-save-props-stream":
                continue

            def gated_workflow(*_args, **_kwargs):
                iterator = CloseTrackedIterator()
                created_iterators.append(iterator)
                gate_worker()
                return iterator

            routes[index] = (*route[:-1], gated_workflow)
            break
        else:
            raise AssertionError("props save route was not registered")
        monkeypatch.setattr(storyboard_save_http, "_ROUTES", tuple(routes))

    app = FastAPI()
    app.include_router(
        build_storyboard_save_router(
            session_factory=session_factory,
            resolve_actor=_actor_resolver(db.events),
        )
    )

    heartbeat_ticks = 0
    stop_heartbeat = asyncio.Event()
    heartbeat_task: asyncio.Task[None] | None = None
    request_task: asyncio.Task[httpx.Response] | None = None

    async def exercise_double_cancellation() -> None:
        nonlocal heartbeat_ticks, heartbeat_task, request_task

        async def heartbeat() -> None:
            nonlocal heartbeat_ticks
            while not stop_heartbeat.is_set():
                heartbeat_ticks += 1
                await asyncio.sleep(0.005)

        async def wait_for_gate(event: threading.Event, description: str) -> None:
            signaled = await asyncio.wait_for(
                asyncio.to_thread(event.wait, 1.5),
                timeout=2,
            )
            assert signaled, description

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://storyboard-save.test",
            ) as client:
                request_task = asyncio.create_task(
                    client.post(
                        "/api/storyboard/filter-and-save-props-stream",
                        json={
                            "chapter_id": "chapter-private",
                            "props": [{"name": "铜镜"}],
                            "appearances": [["铜镜"]],
                        },
                    )
                )
                await wait_for_gate(
                    worker_entered,
                    f"{phase} worker did not reach its gate",
                )
                request_task.cancel()
                await wait_for_gate(
                    drain_started,
                    f"{phase} worker drain did not remain pending after the first cancel",
                )
                assert not release_worker.is_set()
                assert not request_task.done()
                assert heartbeat_ticks > 0, "event loop stopped while the worker was gated"

                request_task.cancel()
                await asyncio.sleep(0)
                assert not release_worker.is_set()
                assert not request_task.done(), "second cancel bypassed gated worker cleanup"

                release_worker.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request_task, timeout=2)

            for executor in executors:
                for future in tuple(executor.futures):
                    try:
                        await asyncio.wait_for(asyncio.to_thread(future.result, 2), timeout=2)
                    except BaseException:
                        pass

            assert len(executors) == 1
            assert len(db.opened_sessions) == 1
            session = db.opened_sessions[0]
            assert session.closed is True
            opened = [thread_id for event, thread_id in db.events if event == "open"]
            closed = [thread_id for event, thread_id in db.events if event == "close"]
            assert len(worker_thread_ids) == 1
            assert len(opened) == 1
            assert len(closed) == 1
            assert closed[0] == opened[0] == worker_thread_ids[0]
            assert executors[0].shutdown_calls >= 1

            if phase == "initialize":
                assert len(created_iterators) == 1
                assert created_iterators[0].closed is True
                assert iterator_close_threads == [opened[0]]
            else:
                assert created_iterators == []
                assert iterator_close_threads == []
        finally:
            release_worker.set()
            stop_heartbeat.set()
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await asyncio.wait_for(heartbeat_task, timeout=1)
                except asyncio.CancelledError:
                    pass
            if request_task is not None and not request_task.done():
                request_task.cancel()
                try:
                    await asyncio.wait_for(request_task, timeout=2)
                except (asyncio.CancelledError, TimeoutError, Exception):
                    pass
            for executor in executors:
                for future in tuple(executor.futures):
                    try:
                        await asyncio.wait_for(asyncio.to_thread(future.result, 2), timeout=2)
                    except BaseException:
                        pass
                # Fallback cleanup only prevents a failed assertion from leaking a test thread.
                if executor.shutdown_calls == 0:
                    await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise_double_cancellation())


def test_response_start_cancellation_closes_session_before_body_iterator_starts(
    storyboard_database: StoryboardDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = storyboard_database
    response_start_entered = threading.Event()
    release_response_start = asyncio.Event()
    workflow_created = threading.Event()
    workflow_started = threading.Event()
    executors: list[ThreadPoolExecutor] = []

    class RecordingExecutor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.futures: list[Future[Any]] = []
            self.shutdown_calls = 0
            executors.append(self)

        def submit(self, fn, /, *args, **kwargs) -> Future[Any]:
            future = super().submit(fn, *args, **kwargs)
            self.futures.append(future)
            return future

        def shutdown(self, *args, **kwargs) -> None:
            self.shutdown_calls += 1
            super().shutdown(*args, **kwargs)

    monkeypatch.setattr(storyboard_save_http, "ThreadPoolExecutor", RecordingExecutor)
    routes = list(storyboard_save_http._ROUTES)
    for index, route in enumerate(routes):
        if route[0] != "/filter-and-save-props-stream":
            continue

        def tracked_workflow(*_args, **_kwargs):
            workflow_created.set()

            def events():
                workflow_started.set()
                yield {"type": "status", "message": "stream iterator started"}

            return events()

        routes[index] = (*route[:-1], tracked_workflow)
        break
    else:
        raise AssertionError("props save route was not registered")
    monkeypatch.setattr(storyboard_save_http, "_ROUTES", tuple(routes))

    app = FastAPI()
    app.include_router(
        build_storyboard_save_router(
            session_factory=db.business_session_factory,
            resolve_actor=_actor_resolver(db.events),
        )
    )
    body = json.dumps(
        {
            "chapter_id": "chapter-private",
            "props": [{"name": "铜镜"}],
            "appearances": [["铜镜"]],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/storyboard/filter-and-save-props-stream",
        "raw_path": b"/api/storyboard/filter-and-save-props-stream",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"storyboard-save.test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("storyboard-save.test", 80),
    }

    async def exercise_response_cancellation() -> None:
        request_delivered = False
        request_task: asyncio.Task[None] | None = None

        async def receive() -> dict[str, Any]:
            nonlocal request_delivered
            if not request_delivered:
                request_delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Future()
            raise AssertionError("unreachable after waiting for client disconnect")

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                response_start_entered.set()
                await release_response_start.wait()

        try:
            request_task = asyncio.create_task(app(scope, receive, send))
            entered = await asyncio.wait_for(
                asyncio.to_thread(response_start_entered.wait, 1.5), timeout=2
            )
            assert entered, "ASGI response.start send did not reach its controlled gate"
            assert workflow_created.is_set()
            assert not workflow_started.is_set()

            request_task.cancel()
            try:
                await asyncio.wait_for(request_task, timeout=2)
            except asyncio.CancelledError:
                pass

            for executor in executors:
                for future in tuple(executor.futures):
                    try:
                        await asyncio.to_thread(future.result, 2)
                    except Exception:
                        pass

            assert len(db.opened_sessions) == 1
            assert db.opened_sessions[0].closed is True
            opened = [thread_id for event, thread_id in db.events if event == "open"]
            closed = [thread_id for event, thread_id in db.events if event == "close"]
            assert len(opened) == 1
            assert len(closed) == 1
            assert closed[0] == opened[0]
            assert len(executors) == 1
            assert executors[0].shutdown_calls >= 1
            assert not workflow_started.is_set()
        finally:
            # Release the controlled send even when an assertion fails.
            release_response_start.set()
            if request_task is not None and not request_task.done():
                request_task.cancel()
                try:
                    await asyncio.wait_for(request_task, timeout=1)
                except (asyncio.CancelledError, TimeoutError):
                    pass
            for executor in executors:
                for future in tuple(executor.futures):
                    try:
                        await asyncio.to_thread(future.result, 2)
                    except Exception:
                        pass
                # Test cleanup only prevents a failed assertion from leaking a thread.
                # Product shutdown is asserted before this fallback runs.
                if executor.shutdown_calls == 0:
                    await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise_response_cancellation())


@pytest.mark.parametrize(
    ("chapter_id", "actor_id", "expected_origin_failure"),
    [
        ("missing-chapter", "owner", "chapter_not_found"),
        ("chapter-private", "other", "series_access_denied"),
    ],
)
def test_authorization_failure_closes_session_when_rollback_raises(
    storyboard_database: StoryboardDatabase,
    monkeypatch: pytest.MonkeyPatch,
    chapter_id: str,
    actor_id: str,
    expected_origin_failure: str,
) -> None:
    db = storyboard_database
    original_rollback = TrackingSession.rollback
    rollback_threads: list[int] = []

    def rollback_then_raise(session: TrackingSession) -> None:
        rollback_threads.append(threading.get_ident())
        original_rollback(session)
        raise RuntimeError("controlled authorization rollback failure")

    monkeypatch.setattr(TrackingSession, "rollback", rollback_then_raise)
    observed_failures: list[str] = []
    if expected_origin_failure == "chapter_not_found":
        original_load_chapter = SqlAlchemyStoryboardSaveStore.load_chapter

        def record_missing_chapter(
            store: SqlAlchemyStoryboardSaveStore, requested_chapter_id: str
        ):
            chapter = original_load_chapter(store, requested_chapter_id)
            if chapter is None:
                observed_failures.append("chapter_not_found")
            return chapter

        monkeypatch.setattr(
            SqlAlchemyStoryboardSaveStore, "load_chapter", record_missing_chapter
        )
    else:
        original_require_access = SqlAlchemyStoryboardSaveStore.require_access

        def record_access_denial(
            store: SqlAlchemyStoryboardSaveStore,
            actor: TrustedActor,
            series_id: str,
        ) -> None:
            try:
                original_require_access(store, actor, series_id)
            except Exception as exc:
                if type(exc).__name__ == "SeriesAccessDenied":
                    observed_failures.append("series_access_denied")
                raise

        monkeypatch.setattr(
            SqlAlchemyStoryboardSaveStore, "require_access", record_access_denial
        )

    executors: list[ThreadPoolExecutor] = []

    class RecordingExecutor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.shutdown_calls = 0
            executors.append(self)

        def shutdown(self, *args, **kwargs) -> None:
            self.shutdown_calls += 1
            super().shutdown(*args, **kwargs)

    monkeypatch.setattr(storyboard_save_http, "ThreadPoolExecutor", RecordingExecutor)
    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-scenes-stream",
        json_body={
            "chapter_id": chapter_id,
            "scenes": [{"title": "宴会厅"}],
            "appearances": [["宴会厅"]],
        },
        headers={"x-actor": actor_id},
    )

    assert observed_failures == [expected_origin_failure]
    assert response.status_code >= 400
    assert len(db.opened_sessions) == 1
    assert db.opened_sessions[0].closed is True
    opened = [thread_id for event, thread_id in db.events if event == "open"]
    closed = [thread_id for event, thread_id in db.events if event == "close"]
    assert len(opened) == 1
    assert len(closed) == 1
    assert closed[0] == opened[0]
    assert rollback_threads
    assert all(thread_id == opened[0] for thread_id in rollback_threads)
    assert len(executors) == 1
    assert executors[0].shutdown_calls >= 1


def test_empty_character_appearances_skip_classification_status(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-characters-stream",
        json_body={
            "chapter_id": "chapter-private",
            "merged_characters": [{"name": "周晴"}],
            "appearances": [[]],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    status_messages = [event["message"] for event in events if event["type"] == "status"]
    assert "发现 0 个出场角色" in status_messages
    assert "正在分类角色..." not in status_messages
    assert events[-2] == {
        "type": "error",
        "message": (
            "AI 没有判定出任何出场的角色，本次未保存任何资产。请重试；"
            "若持续出现，请检查该章节分镜内容与所选提示词。"
        ),
    }
    assert events[-1] == {"type": "done"}
    assert db.opened_sessions[-1].commit_count == 0
    assert db.opened_sessions[-1].closed is True


def test_legacy_truthiness_error_order_precedes_body_model_validation(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-scenes-stream",
        json_body={
            "chapter_id": "chapter-private",
            "scenes": "not-a-list",
            "appearances": [],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "缺少场景出场数据"
    assert db.opened_sessions == []


@pytest.mark.parametrize("raw_body", [[], None])
def test_legacy_non_object_json_body_remains_route_error_500(
    storyboard_database: StoryboardDatabase,
    raw_body: object,
) -> None:
    response = _request(
        _app(storyboard_database),
        "/api/storyboard/filter-and-save-scenes-stream",
        content=json.dumps(raw_body).encode("utf-8"),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "过滤场景失败"
    assert storyboard_database.opened_sessions == []


def test_invalid_target_frame_slots_are_filtered_without_renumbering_rows(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    _replace_chapter_content(db.engine, "chapter-private", [{}, {}, {}])
    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-props-stream",
        json_body={
            "chapter_id": "chapter-private",
            "props": [
                {"name": "铜镜"},
                {"name": "玉佩"},
                {"name": "木匣"},
            ],
            "appearances": [["铜镜"], ["玉佩"], ["木匣"]],
            "target_frames": [1, "bad", 0],
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    events = _events(response)
    plan = next(event for event in events if event["type"] == "plan")
    assert plan["frame_map"] == [
        {"frame": 1, "candidates": ["木匣"]},
        {"frame": 2, "candidates": ["玉佩"]},
    ]
    frames_by_name = {item["name"]: item["frames"] for item in plan["items"]}
    assert frames_by_name == {"铜镜": [], "玉佩": [2], "木匣": [1]}
    assert db.opened_sessions[-1].commit_count == 0


def test_invalid_target_frame_slots_write_absolute_targets_without_renumbering_rows(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    original_content = [
        {"prop": ["old-first"], "storyboard": ["story-0"]},
        {"prop": ["old-middle"], "storyboard": ["story-1"]},
        {"prop": ["old-last"], "storyboard": ["story-2"]},
    ]
    _replace_chapter_content(db.engine, "chapter-private", original_content)
    _insert_storyboard_assets(db.engine, "chapter-private", "series-private", 3)

    session = db.sessions()
    events = list(
        save_props_stream(
            SqlAlchemyStoryboardSaveStore(session),
            TrustedActor("owner"),
            "chapter-private",
            [{"name": "铜镜"}, {"name": "玉佩"}, {"name": "木匣"}],
            [["铜镜"], ["玉佩"], ["木匣"]],
            target_frames=[1, "bad", 0],
        )
    )
    assert events[-1]["type"] == "complete"
    session.close()

    with db.engine.connect() as connection:
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
        ids_by_name = dict(
            connection.execute(
                select(props.c.name, props.c.id).where(props.c.series_id == "series-private")
            ).all()
        )

    assert saved_content[0]["prop"] == [ids_by_name["木匣"]]
    assert saved_content[1]["prop"] == [ids_by_name["铜镜"]]
    assert saved_content[2] == original_content[2]
    assert [frame["storyboard"] for frame in saved_content] == [
        ["story-0"],
        ["story-1"],
        ["story-2"],
    ]


@pytest.mark.parametrize("falsey_target_frames", [False, 0])
def test_falsey_non_list_target_frames_means_all_frames(
    storyboard_database: StoryboardDatabase,
    falsey_target_frames: bool | int,
) -> None:
    db = storyboard_database
    _replace_chapter_content(db.engine, "chapter-private", [{}, {}])
    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-props-stream",
        json_body={
            "chapter_id": "chapter-private",
            "props": [{"name": "铜镜"}],
            "appearances": [["铜镜"], ["铜镜"]],
            "target_frames": falsey_target_frames,
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    plan = next(event for event in _events(response) if event["type"] == "plan")
    assert plan["frame_map"] == [
        {"frame": 1, "candidates": ["铜镜"]},
        {"frame": 2, "candidates": ["铜镜"]},
    ]
    assert db.opened_sessions[-1].commit_count == 0


@pytest.mark.parametrize(
    ("actor_id", "chapter_id", "expected_status"),
    [
        ("owner", "chapter-private", 200),
        ("other", "chapter-private", 403),
        ("member", "chapter-team", 200),
        ("viewer", "chapter-team", 200),
        ("member", "chapter-claimed", 200),
        ("viewer", "chapter-claimed", 403),
    ],
)
def test_private_team_and_claimed_series_access_uses_shared_policy(
    storyboard_database: StoryboardDatabase,
    actor_id: str,
    chapter_id: str,
    expected_status: int,
) -> None:
    response = _request(
        _app(storyboard_database),
        "/api/storyboard/filter-and-save-scenes-stream",
        headers={"x-actor": actor_id},
        json_body={
            "chapter_id": chapter_id,
            "scenes": [{"title": "宴会厅"}],
            "appearances": [["宴会厅"]],
            "dry_run": True,
        },
    )

    assert response.status_code == expected_status
    if expected_status == 200:
        assert _events(response)[-1]["type"] == "done"
    else:
        assert response.json()["detail"]


def test_frame_update_failure_reports_error_and_keeps_only_prior_committed_asset_phases(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    content = [{"text": "原文", "prop": ["old-prop"]}]
    _replace_chapter_content(db.engine, "chapter-private", content)
    with db.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_storyboard_content BEFORE UPDATE OF content ON chapters "
                "BEGIN SELECT RAISE(FAIL, 'fixture rejects chapter content'); END"
            )
        )

    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-props-stream",
        json_body={
            "chapter_id": "chapter-private",
            "props": [{"name": "铜镜", "description": "候选"}],
            "appearances": [["铜镜"]],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert events[-2]["type"] == "error"
    assert events[-2]["message"] == "更新分镜道具关联失败"
    assert not any(event["type"] == "complete" for event in events)
    assert events[-1]["type"] == "done"
    assert db.opened_sessions[-1].commit_count == 2
    assert db.opened_sessions[-1].rollback_count >= 2
    assert db.events[-1][0] == "close"

    with db.engine.connect() as connection:
        saved_content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-private")
        ).scalar_one()
        saved_prop = connection.execute(
            select(props.c.name).where(props.c.series_id == "series-private")
        ).scalar_one()
    assert json.loads(saved_content) == content
    assert saved_prop == "铜镜"


@pytest.mark.parametrize(
    ("failed_phase", "alias_expected", "binding_expected"),
    [(1, False, False), (2, True, False), (3, True, True)],
)
def test_commit_ack_unknown_is_not_retried_and_never_reports_save_success(
    storyboard_database: StoryboardDatabase,
    failed_phase: int,
    alias_expected: bool,
    binding_expected: bool,
) -> None:
    db = storyboard_database
    db.unknown_commit_ack_at = failed_phase
    original_content = [{"prop": ["old-binding"]}]
    _replace_chapter_content(db.engine, "chapter-private", original_content)

    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-props-stream",
        json_body={
            "chapter_id": "chapter-private",
            "props": [{"name": "铜镜", "original_name": "古镜"}],
            "appearances": [["铜镜"]],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert events[-2]["type"] == "error"
    assert "commit acknowledgement unknown" in events[-2]["message"] or events[-2][
        "message"
    ] == "更新分镜道具关联失败"
    assert not any(event["type"] == "complete" for event in events)
    assert events[-1]["type"] == "done"
    assert db.opened_sessions[-1].commit_count == failed_phase

    with db.engine.connect() as connection:
        saved_props = connection.execute(
            select(props.c.id, props.c.aliases).where(props.c.series_id == "series-private")
        ).mappings().all()
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )

    assert len(saved_props) == 1
    asset_id = saved_props[0]["id"]
    assert ("古镜" in json.loads(saved_props[0]["aliases"] or "[]")) is alias_expected
    if binding_expected:
        assert saved_content[0]["prop"] == [asset_id]
    else:
        assert saved_content == original_content


def test_optional_cleanup_precommit_failure_keeps_successful_character_binding(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    _replace_chapter_content(db.engine, "chapter-private", [{}])
    with db.engine.begin() as connection:
        connection.execute(
            insert(characters),
            [
                _character_values("char-unused", "未引用"),
            ],
        )
        connection.execute(
            text(
                "CREATE TRIGGER reject_character_cleanup BEFORE DELETE ON characters "
                "BEGIN SELECT RAISE(FAIL, 'fixture rejects optional cleanup'); END"
            )
        )

    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-characters-stream",
        json_body={
            "chapter_id": "chapter-private",
            "merged_characters": [{"name": "周晴"}],
            "appearances": [["周晴"]],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert events[-2]["type"] == "complete"
    assert events[-2]["success"] is True
    assert events[-1]["type"] == "done"
    assert db.opened_sessions[-1].commit_count == 3

    with db.engine.connect() as connection:
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
        surviving_ids = set(connection.execute(select(characters.c.id)).scalars().all())
    assert saved_content[0]["character"]
    assert surviving_ids == {"char-unused", saved_content[0]["character"][0]}


def test_optional_cleanup_unknown_commit_ack_keeps_legacy_zero_count_but_delete_is_durable(
    storyboard_database: StoryboardDatabase,
) -> None:
    db = storyboard_database
    db.unknown_commit_ack_at = 4
    _replace_chapter_content(db.engine, "chapter-private", [{}])
    with db.engine.begin() as connection:
        connection.execute(insert(characters).values(**_character_values("char-unused", "未引用")))

    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-characters-stream",
        json_body={
            "chapter_id": "chapter-private",
            "merged_characters": [{"name": "周晴"}],
            "appearances": [["周晴"]],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert events[-2]["type"] == "complete"
    assert events[-2]["success"] is True
    assert events[-2]["cleaned_up_count"] == 0
    assert events[-1]["type"] == "done"
    assert db.opened_sessions[-1].commit_count == 4

    with db.engine.connect() as connection:
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
        surviving_ids = set(connection.execute(select(characters.c.id)).scalars().all())
    assert saved_content[0]["character"]
    assert surviving_ids == {saved_content[0]["character"][0]}


def test_same_encoded_chapter_content_skips_media_reconciliation(
    storyboard_database: StoryboardDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = storyboard_database
    content = [{"storyboard": ["story-0"], "prop": ["prop-existing"]}]
    encoded_content = json.dumps(content, ensure_ascii=False)
    _replace_chapter_content(db.engine, "chapter-private", content)
    _insert_storyboard_assets(db.engine, "chapter-private", "series-private", 1)
    with db.engine.begin() as connection:
        connection.execute(insert(props).values(**_prop_values("prop-existing", "铜镜")))

    stage_calls: list[tuple[Any, ...]] = []

    def stage_spy(*args, **kwargs):
        stage_calls.append(args)
        raise AssertionError("unchanged source content must not initialize media state")

    monkeypatch.setattr(storyboard_save_persistence, "stage_media_reconciliation", stage_spy)
    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-props-stream",
        json_body={
            "chapter_id": "chapter-private",
            "props": [{"name": "铜镜", "existing_id": "prop-existing"}],
            "appearances": [["铜镜"]],
        },
    )

    assert response.status_code == 200
    assert _events(response)[-1]["type"] == "done"
    assert stage_calls == []
    with db.engine.connect() as connection:
        saved_content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-private")
        ).scalar_one()
        state_ids = connection.execute(
            select(storyboard_media_states.c.id).where(
                storyboard_media_states.c.chapter_id == "chapter-private"
            )
        ).scalars().all()
    assert saved_content == encoded_content
    assert state_ids == []


@pytest.mark.parametrize("rejection", ["media_cas", "notes_cas", "source_update", "facade"])
def test_media_reconciliation_rejection_rolls_back_the_whole_binding_phase(
    storyboard_database: StoryboardDatabase,
    monkeypatch: pytest.MonkeyPatch,
    rejection: str,
) -> None:
    db = storyboard_database
    content = [{"storyboard": ["story-0"], "prop": ["old-binding"]}]
    _replace_chapter_content(db.engine, "chapter-private", content)
    _insert_storyboard_assets(db.engine, "chapter-private", "series-private", 1)
    _seed_stale_media_state_and_approved_note(db.engine)

    if rejection == "media_cas":
        trigger = (
            "CREATE TRIGGER reject_media_state_cas BEFORE UPDATE ON storyboard_media_states "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
    elif rejection == "notes_cas":
        trigger = (
            "CREATE TRIGGER reject_notes_cas BEFORE UPDATE ON personal_production_notes "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
    elif rejection == "source_update":
        trigger = (
            "CREATE TRIGGER reject_source_update BEFORE UPDATE OF content ON chapters "
            "BEGIN SELECT RAISE(FAIL, 'fixture rejects source update'); END"
        )
    else:
        trigger = None
    if trigger is not None:
        with db.engine.begin() as connection:
            connection.execute(text(trigger))
    else:
        original_stage = public_stage_media_reconciliation

        def reject_after_public_stage(*args, **kwargs):
            original_stage(*args, **kwargs)
            raise RuntimeError("injected public facade rejection")

        monkeypatch.setattr(
            storyboard_save_persistence,
            "stage_media_reconciliation",
            reject_after_public_stage,
        )

    response = _request(
        _app(db),
        "/api/storyboard/filter-and-save-props-stream",
        json_body={
            "chapter_id": "chapter-private",
            "props": [{"name": "铜镜", "description": "候选"}],
            "appearances": [["铜镜"]],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    assert events[-2]["type"] == "error"
    assert events[-2]["message"] == "更新分镜道具关联失败"
    assert not any(event["type"] == "complete" for event in events)
    assert events[-1]["type"] == "done"
    assert db.opened_sessions[-1].commit_count == 2

    with db.engine.connect() as connection:
        saved_content = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-private")
            ).scalar_one()
        )
        media_state = connection.execute(
            select(
                storyboard_media_states.c.media_revision,
                storyboard_media_states.c.asset_image_digest,
                storyboard_media_states.c.preview_digest,
                storyboard_media_states.c.source_valid,
            ).where(storyboard_media_states.c.id == "media-state-0")
        ).mappings().one()
        note = connection.execute(
            select(personal_production_notes.c.revision, personal_production_notes.c.frame_notes)
            .where(personal_production_notes.c.id == "notes-0")
        ).mappings().one()
        prop_names = connection.execute(
            select(props.c.name).where(props.c.series_id == "series-private")
        ).scalars().all()

    assert saved_content == content
    assert media_state == {
        "media_revision": 1,
        "asset_image_digest": "stale-image-digest",
        "preview_digest": "stale-preview-digest",
        "source_valid": True,
    }
    assert note["revision"] == 1
    assert note["frame_notes"] == {
        "story-0": {"status": "approved", "approved_media_revision": 1}
    }
    assert prop_names == ["铜镜"]


def _seed_stale_media_state_and_approved_note(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(storyboard_media_states).values(
                id="media-state-0",
                chapter_id="chapter-private",
                storyboard_asset_id="story-0",
                media_revision=1,
                asset_image_digest="stale-image-digest",
                preview_digest="stale-preview-digest",
                source_valid=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(personal_production_notes).values(
                id="notes-0",
                chapter_id="chapter-private",
                user_id="owner",
                revision=1,
                frame_notes={
                    "story-0": {"status": "approved", "approved_media_revision": 1}
                },
                resume_frame_id=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def _run_direct_save(
    db: StoryboardDatabase,
    kind: str,
    candidates: list[dict[str, Any]],
    appearances: list[object],
) -> tuple[list[dict[str, Any]], TrackingSession]:
    workflow = {
        "character": save_characters_stream,
        "scene": save_scenes_stream,
        "prop": save_props_stream,
    }[kind]
    session = db.sessions()
    events = list(
        workflow(
            SqlAlchemyStoryboardSaveStore(session),
            TrustedActor("owner"),
            "chapter-private",
            candidates,
            appearances,
        )
    )
    session.close()
    return events, session


def _character_values(
    character_id: str,
    name: str,
    *,
    image_url: str | None = None,
    audio_url: str | None = None,
    voice_ref: str | None = None,
    aliases: str = "[]",
) -> dict[str, Any]:
    return {
        "id": character_id,
        "series_id": "series-private",
        "name": name,
        "gender": "",
        "age": "",
        "role": "",
        "appearance": "",
        "description": None,
        "image_url": image_url,
        "audio_url": audio_url,
        "voice_ref": voice_ref,
        "aliases": aliases,
        "canonical_key": make_key("character", name),
        "created_at": NOW,
        "updated_at": NOW,
    }


def _prop_values(prop_id: str, name: str) -> dict[str, Any]:
    return {
        "id": prop_id,
        "series_id": "series-private",
        "name": name,
        "description": None,
        "image_url": None,
        "aliases": "[]",
        "canonical_key": make_key("prop", name),
        "created_at": NOW,
        "updated_at": NOW,
    }
