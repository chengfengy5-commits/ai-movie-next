from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import create_engine, delete, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.dml import Delete, Insert, Update

from haoai_backend.asset_data.tables import (
    characters,
    metadata as asset_metadata,
    storyboard_assets,
)
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
    users,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_save.http import build_storyboard_save_router

NOW = datetime(2026, 10, 14)
PATH = "/api/storyboard/filter-and-save-characters"


class TrackedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.close_count = 0
        self.dml_count = 0

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(statement, (Insert, Update, Delete)):
            self.dml_count += 1
        self.info["events"].append(("execute", threading.get_ident()))
        return super().execute(statement, *args, **kwargs)

    def commit(self) -> None:
        self.commit_count += 1
        if self.info.get("fail_commit_at") == self.commit_count:
            raise RuntimeError("injected commit failure")
        super().commit()

    def close(self) -> None:
        self.close_count += 1
        self.info["events"].append(("close", threading.get_ident()))
        super().close()


@dataclass
class Database:
    engine: Any
    sessions: sessionmaker[TrackedSession]
    events: list[tuple[str, int]]
    opened: list[TrackedSession]
    fail_commit_at: int | None = None

    def factory(self) -> TrackedSession:
        self.events.append(("open", threading.get_ident()))
        session = self.sessions()
        session.info["fail_commit_at"] = self.fail_commit_at
        self.opened.append(session)
        return session


def _series_values(series_id: str, owner_id: str) -> dict[str, Any]:
    return {
        "id": series_id,
        "user_id": owner_id,
        "name": series_id,
        "description": None,
        "image_url": None,
        "style_prompt_id": None,
        "team_id": None,
        "claimed_by": None,
        "claimed_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _character_values(character_id: str, series_id: str, name: str) -> dict[str, Any]:
    return {
        "id": character_id,
        "series_id": series_id,
        "name": name,
        "gender": "",
        "age": "",
        "role": "",
        "appearance": "",
        "description": None,
        "image_url": None,
        "audio_url": None,
        "voice_ref": "",
        "aliases": "[]",
        "canonical_key": name,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _seed(engine: Any) -> None:
    frames = [
        {
            "text": f"frame-{index}",
            "character": ["character-owned"],
            "storyboard": [f"story-{index}"],
        }
        for index in range(5)
    ]
    frames[0]["extra"] = {"unicode": "雪山·夜"}
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [{"id": "owner", "username": "owner"}, {"id": "peer", "username": "peer"}],
        )
        connection.execute(
            insert(series),
            [_series_values("series-owned", "owner"), _series_values("series-peer", "peer")],
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-owned",
                series_id="series-owned",
                title="Owned chapter",
                content=json.dumps(frames, ensure_ascii=False),
                order=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(characters),
            [
                _character_values("character-owned", "series-owned", "林澈"),
                _character_values("character-peer", "series-peer", "跨剧集角色"),
            ],
        )
        connection.execute(
            insert(storyboard_assets),
            [
                {
                    "id": f"story-{index}",
                    "series_id": "series-owned",
                    "chapter_id": "chapter-owned",
                    "frame_index": index,
                    "name": f"frame-{index}",
                    "description": None,
                    "image_url": f"fixture://story/{index}",
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for index in range(5)
            ],
        )


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'storyboard-non-stream.sqlite3'}", future=True)
    series_metadata.create_all(engine)
    asset_metadata.create_all(engine)
    access_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    _seed(engine)
    events: list[tuple[str, int]] = []
    opened: list[TrackedSession] = []
    sessions = sessionmaker(
        engine,
        class_=TrackedSession,
        expire_on_commit=False,
        future=True,
        info={"events": events},
    )
    database = Database(engine, sessions, events, opened)
    try:
        yield database
    finally:
        engine.dispose()


def _app(database: Database) -> FastAPI:
    app = FastAPI()

    def resolve_actor(request: Request) -> TrustedActor:
        return TrustedActor(request.headers.get("x-actor", "owner"))

    app.include_router(
        build_storyboard_save_router(
            session_factory=database.factory,
            resolve_actor=resolve_actor,
        )
    )
    return app


def _request(
    app: FastAPI,
    body: Any = None,
    *,
    actor: str = "owner",
    raw_body: bytes | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = {"x-actor": actor}
        if raw_body is not None:
            headers["content-type"] = "application/json"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://storyboard-save-non-stream.test",
        ) as client:
            if raw_body is not None:
                return await client.post(PATH, headers=headers, content=raw_body)
            return await client.post(PATH, headers=headers, json=body)

    return asyncio.run(send())


def _candidate_body() -> dict[str, Any]:
    return {
        "chapter_id": "chapter-owned",
        "characters": [
            {"name": "林澈", "is_existing": True, "existing_id": "character-owned"},
            {"name": "已删除", "is_existing": True, "existing_id": "character-deleted"},
            {
                "name": "阿蘅",
                "is_existing": False,
                "gender": "女",
                "age": "17",
                "role": "主角",
                "appearance": "青衣",
                "voice_ref": "声线甲",
                "original_metadata": {"source": "原始候选"},
            },
            {"name": "未出场", "appearance": "不应写入"},
        ],
        "appearances": [["林澈", "阿蘅"], ["已删除"], [], None],
    }


def test_exact_name_save_and_two_phase_frame_binding_use_real_sql(database: Database) -> None:
    app = _app(database)
    loop_thread = threading.get_ident()
    response = _request(app, _candidate_body())

    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["reused_existing_count"] == 2
    assert result["filtered_out_count"] == 1
    assert result["total_count"] == 4
    assert result["saved_new_characters"] == [_candidate_body()["characters"][2]]
    assert result["filtered_out_characters"] == [_candidate_body()["characters"][3]]
    assert result["kept_binding_frames"] == [2, 4, 5]
    assert len(database.opened) == 1
    business = database.opened[0]
    assert business.commit_count == 2
    assert business.close_count == 1
    assert business.dml_count >= 2
    worker_threads = {
        thread_id
        for event, thread_id in database.events
        if event in {"open", "execute", "close"}
    }
    assert len(worker_threads) == 1
    assert loop_thread not in worker_threads

    with database.engine.connect() as connection:
        saved = connection.execute(
            select(characters).where(
                characters.c.series_id == "series-owned", characters.c.name == "阿蘅"
            )
        ).mappings().one()
        assert (
            saved["gender"],
            saved["age"],
            saved["role"],
            saved["appearance"],
            saved["voice_ref"],
        ) == ("女", "17", "主角", "青衣", "声线甲")
        frames = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-owned")
            ).scalar_one()
        )
        assert frames[0]["character"] == ["character-owned", saved["id"]]
        assert frames[0]["extra"] == {"unicode": "雪山·夜"}
        assert frames[1]["character"] == ["character-owned"]
        assert frames[2]["character"] == []
        assert frames[3]["character"] == ["character-owned"]
        assert frames[4]["character"] == ["character-owned"]
        assert connection.execute(select(storyboard_media_states.c.id)).first() is not None
        assert connection.execute(select(personal_production_notes.c.id)).first() is None


def test_commit_failures_distinguish_unconfirmed_assets_from_binding_phase(
    database: Database,
) -> None:
    app = _app(database)
    database.fail_commit_at = 1
    asset_failure = _request(app, _candidate_body())
    assert asset_failure.status_code == 200
    assert asset_failure.json()["success"] is False
    assert database.opened[-1].commit_count == 1
    with database.engine.connect() as connection:
        assert connection.execute(
            select(characters.c.id).where(characters.c.name == "阿蘅")
        ).first() is None

    database.fail_commit_at = 2
    binding_failure = _request(app, _candidate_body())
    assert binding_failure.status_code == 200
    payload = binding_failure.json()
    assert payload["success"] is True
    assert "只能确认角色资产提交" in payload["message"]
    assert "绑定未确认持久化" in payload["message"]
    assert database.opened[-1].commit_count == 2
    assert database.opened[-1].close_count == 1
    with database.engine.connect() as connection:
        assert connection.execute(
            select(characters.c.id).where(characters.c.name == "阿蘅")
        ).first() is not None
        frames = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-owned")
            ).scalar_one()
        )
        assert frames[0]["character"] == ["character-owned"]


def test_binding_phase_rechecks_new_asset_after_first_commit(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haoai_backend.storyboard_save import persistence

    original_commit = persistence.SqlAlchemyStoryboardSaveStore.commit
    commits = 0

    def delete_after_asset_commit(store: Any) -> None:
        nonlocal commits
        original_commit(store)
        commits += 1
        if commits == 1:
            with database.engine.begin() as connection:
                connection.execute(delete(characters).where(characters.c.name == "阿蘅"))

    monkeypatch.setattr(
        persistence.SqlAlchemyStoryboardSaveStore, "commit", delete_after_asset_commit
    )
    response = _request(
        _app(database),
        {
            "chapter_id": "chapter-owned",
            "characters": [{"name": "阿蘅", "appearance": "青衣"}],
            "appearances": [["阿蘅"]],
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert 1 in response.json()["kept_binding_frames"]
    with database.engine.connect() as connection:
        assert connection.execute(
            select(characters.c.id).where(characters.c.name == "阿蘅")
        ).first() is None
        frames = json.loads(
            connection.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-owned")
            ).scalar_one()
        )
        assert frames[0]["character"] == ["character-owned"]


def test_foreign_existing_id_is_rejected_before_any_dml(database: Database) -> None:
    response = _request(
        _app(database),
        {
            "chapter_id": "chapter-owned",
            "characters": [
                {"name": "跨剧集角色", "is_existing": True, "existing_id": "character-peer"}
            ],
            "appearances": [["跨剧集角色"]],
        },
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "素材引用不属于当前剧集"}
    assert database.opened[-1].commit_count == 0
    assert database.opened[-1].dml_count == 0
    assert database.opened[-1].close_count == 1


def test_body_validation_and_series_access_keep_legacy_http_errors(database: Database) -> None:
    app = _app(database)
    missing_chapter = _request(app, {"characters": [{"name": "角色"}], "appearances": []})
    assert missing_chapter.status_code == 400
    assert missing_chapter.json() == {"detail": "缺少章节ID"}
    missing_characters = _request(app, {"chapter_id": "chapter-owned", "appearances": []})
    assert missing_characters.status_code == 400
    assert missing_characters.json() == {"detail": "缺少角色列表"}
    missing_appearances = _request(
        app, {"chapter_id": "chapter-owned", "characters": [{"name": "角色"}]}
    )
    assert missing_appearances.status_code == 400
    assert missing_appearances.json() == {"detail": "缺少角色出场数据"}
    empty_appearances = _request(
        app,
        {
            "chapter_id": "chapter-owned",
            "characters": [{"name": "角色"}],
            "appearances": [],
        },
    )
    assert empty_appearances.status_code == 400
    assert empty_appearances.json() == {"detail": "缺少角色出场数据"}
    malformed = _request(app, raw_body=b"[")
    assert malformed.status_code == 500
    assert malformed.json() == {"detail": "过滤角色失败"}
    missing_row = _request(
        app,
        {
            "chapter_id": "missing-chapter",
            "characters": [{"name": "角色"}],
            "appearances": [["角色"]],
        },
    )
    assert missing_row.status_code == 404
    denied = _request(
        app,
        {
            "chapter_id": "chapter-owned",
            "characters": [{"name": "角色"}],
            "appearances": [["角色"]],
        },
        actor="peer",
    )
    assert denied.status_code == 403


@pytest.mark.parametrize("cancel_count", [1, 2])
def test_cancel_waits_for_late_session_open_and_close_on_worker(
    database: Database, cancel_count: int
) -> None:
    app = FastAPI()
    factory_entered = threading.Event()
    release_factory = threading.Event()
    worker_thread: list[int] = []
    base_factory = database.factory

    def late_factory() -> TrackedSession:
        worker_thread.append(threading.get_ident())
        factory_entered.set()
        if not release_factory.wait(timeout=3):
            raise TimeoutError("late Session factory was not released")
        return base_factory()

    def resolve_actor(request: Request) -> TrustedActor:
        return TrustedActor("owner")

    app.include_router(
        build_storyboard_save_router(
            session_factory=late_factory,
            resolve_actor=resolve_actor,
        )
    )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://storyboard-save-non-stream.test",
        ) as client:
            request = asyncio.create_task(
                client.post(PATH, json=_candidate_body())
            )
            entered = await asyncio.wait_for(asyncio.to_thread(factory_entered.wait, 1), timeout=2)
            assert entered
            assert database.opened == []
            for _ in range(cancel_count):
                request.cancel()
                await asyncio.sleep(0)
                assert not request.done()
            release_factory.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, timeout=2)

    try:
        asyncio.run(exercise())
        assert len(database.opened) == 1
        assert database.opened[0].close_count == 1
        assert database.opened[0].commit_count == 2
        opened_thread = next(
            thread_id for event, thread_id in database.events if event == "open"
        )
        closed_thread = next(
            thread_id for event, thread_id in database.events if event == "close"
        )
        assert worker_thread == [opened_thread]
        assert closed_thread == worker_thread[0]
    finally:
        release_factory.set()
