"""H1145 PostgreSQL transaction checks for a supervisor-owned temporary server."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    storyboard_media_states,
)
from haoai_backend.personal_production.rough_cut.tables import metadata as rough_metadata
from haoai_backend.series_data.errors import SeriesDataConflict
from haoai_backend.series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from haoai_backend.series_data.schemas import ChapterEditGuard
from haoai_backend.series_data.tables import (
    chapter_locks,
    chapters,
    chat_messages,
    metadata as series_metadata,
    series,
    storyboard_assets,
    team_members,
    teams,
)
from haoai_backend.shared.identity import TrustedActor


_MARKER = "haoai-continuous-chapter-guard-1145"
_PREFIX = "HAOAI_OWNED_PG_1145_"
_REQUIRED_ENV = tuple(
    _PREFIX + suffix
    for suffix in ("URL", "PORT", "MARKER", "METADATA", "RECEIPT", "REPO")
)


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:16]}"


def _record_pg_case_failure(case: str):
    def decorate(test):
        @wraps(test)
        def wrapped(*args, **kwargs):
            try:
                return test(*args, **kwargs)
            except Exception as error:
                fixture = kwargs.get("owned_postgres_1145")
                if fixture is None and args:
                    fixture = args[0]
                receipt = fixture[2] if isinstance(fixture, tuple) and len(fixture) == 3 else None
                _write_receipt(receipt, "FAIL", {case: False}, type(error).__name__)
                raise
        return wrapped
    return decorate


def _write_receipt(path: Path | None, status: str, cases: dict[str, object], error: str | None = None) -> None:
    if path is None:
        return
    existing: dict[str, Any] = {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict) and parsed.get("purpose") == _MARKER:
            existing = parsed
    except (OSError, json.JSONDecodeError):
        pass
    merged_cases = dict(existing.get("cases", {})) if isinstance(existing.get("cases"), dict) else {}
    merged_cases.update(cases)
    final_status = "FAIL" if status == "FAIL" or existing.get("status") == "FAIL" else status
    payload: dict[str, object] = {
        "status": final_status,
        "purpose": _MARKER,
        "cases": merged_cases,
        "database": "supervisor-owned temporary PostgreSQL",
        "network": "loopback PostgreSQL only",
    }
    if error:
        payload["failureType"] = error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _owned_config() -> tuple[str, int, Path, Path, Path]:
    receipt_value = os.environ.get(_PREFIX + "RECEIPT")
    receipt = Path(receipt_value).resolve() if receipt_value else None
    values = {name: os.environ.get(name) for name in _REQUIRED_ENV}
    try:
        if any(value is None or value == "" for value in values.values()):
            raise RuntimeError("owned PostgreSQL evidence variables are incomplete")
        if values[_PREFIX + "MARKER"] != _MARKER:
            raise RuntimeError("owned PostgreSQL marker did not match")

        repo = Path(str(values[_PREFIX + "REPO"])).resolve(strict=True)
        if repo != Path(__file__).resolve().parents[2]:
            raise RuntimeError("owned PostgreSQL test is outside the declared repository")
        metadata_path = Path(str(values[_PREFIX + "METADATA"]))
        if not metadata_path.is_absolute():
            raise RuntimeError("owned PostgreSQL metadata path must be absolute")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise RuntimeError("owned PostgreSQL metadata is invalid")
        for key, expected in {
            "purpose": _MARKER,
            "host": "127.0.0.1",
            "user": "haoai1145",
            "database": "postgres",
            "ownedPidConfirmed": True,
        }.items():
            if metadata.get(key) != expected:
                raise RuntimeError("owned PostgreSQL metadata did not match")

        port = int(str(values[_PREFIX + "PORT"]))
        if port == 5432 or type(metadata.get("port")) is not int or metadata["port"] != port:
            raise RuntimeError("owned PostgreSQL port did not match")
        if type(metadata.get("postgresPid")) is not int or metadata["postgresPid"] <= 0:
            raise RuntimeError("owned PostgreSQL PID metadata is invalid")
        tmp_root = Path(str(metadata.get("tmpRoot"))).resolve(strict=True)
        data_dir = Path(str(metadata.get("dataDirectory"))).resolve(strict=True)
        if not data_dir.is_relative_to(tmp_root):
            raise RuntimeError("PostgreSQL data directory is outside its owned temporary root")

        url_text = str(values[_PREFIX + "URL"])
        url = make_url(url_text)
        if (
            not url.drivername.startswith("postgresql")
            or url.host != "127.0.0.1"
            or url.port != port
            or url.username != "haoai1145"
            or url.database != "postgres"
            or url.password is not None
            or url.query
        ):
            raise RuntimeError("PostgreSQL URL is not an authorized owned-local endpoint")
        return url_text, port, tmp_root, data_dir, receipt
    except Exception as error:
        _write_receipt(receipt, "FAIL", {"ownedInstanceValidation": False}, type(error).__name__)
        raise


@pytest.fixture(scope="module")
def owned_postgres_1145() -> tuple[Engine, sessionmaker[Session], Path | None]:
    if not any(os.environ.get(name) for name in _REQUIRED_ENV):
        pytest.skip("requires supervisor-owned HAOAI_OWNED_PG_1145_* environment")
    url, port, tmp_root, expected_data_dir, receipt = _owned_config()
    _write_receipt(receipt, "RUNNING", {})
    schema = f"h1145_{uuid4().hex}"
    engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 3,
            "options": f"-c search_path={schema} -c lock_timeout=5000",
        },
    )
    try:
        with engine.begin() as connection:
            data_dir = Path(connection.execute(text("SHOW data_directory")).scalar_one()).resolve()
            server_port = int(connection.execute(text("SHOW port")).scalar_one())
            if data_dir != expected_data_dir or server_port != port:
                raise RuntimeError("connected PostgreSQL instance did not match owned metadata")
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        auth_metadata.create_all(engine, tables=[auth_users])
        series_metadata.create_all(engine)
        notes_metadata.create_all(engine)
        rough_metadata.create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False, future=True)
        yield engine, sessions, receipt
    except Exception as error:
        _write_receipt(receipt, "FAIL", {"ownedInstanceConnection": False}, type(error).__name__)
        raise
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            engine.dispose()


def _insert_actor_and_series(
    connection,
    *,
    series_id: str,
    chapter_id: str,
    team_id: str | None = None,
) -> tuple[datetime, str]:
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    actor_id = _new_id("u-")
    connection.execute(insert(auth_users).values(
        id=actor_id, username=f"作者-{actor_id}", email=f"{actor_id}@h1145.invalid", hashed_password="x",
        is_superuser=False, membership_type="free", created_at=now, password_updated_at=None,
    ))
    if team_id:
        connection.execute(insert(teams).values(id=team_id, name="测试团队", owner_id=actor_id))
        connection.execute(insert(team_members).values(
            id=_new_id("m-"), team_id=team_id, user_id=actor_id, role="owner", permissions="[]",
        ))
    connection.execute(insert(series).values(
        id=series_id, user_id=actor_id, name="临时测试剧集", description=None,
        image_url=None, style_prompt_id=None, team_id=team_id, claimed_by=None,
        claimed_at=None, created_at=now, updated_at=now,
    ))
    old_content = [{
        "text": "基线镜头",
        "character": [],
        "scene": [],
        "prop": [],
        "storyboard": [f"shot-{chapter_id}"],
        "preview": None,
        "original_text": "打开时的原文",
        "unknown": {"score": 1.0},
    }]
    connection.execute(insert(chapters).values(
        id=chapter_id, series_id=series_id, title="临时章节",
        content=json.dumps(old_content, ensure_ascii=False), order=1,
        created_at=now, updated_at=now,
    ))
    connection.execute(insert(storyboard_assets).values(
        id=f"shot-{chapter_id}", series_id=series_id, chapter_id=chapter_id,
        frame_index=0, name="基线镜头", description="基线镜头", image_url=None,
        created_at=now, updated_at=now,
    ))
    connection.execute(insert(storyboard_assets).values(
        id=f"orphan-{chapter_id}", series_id=series_id, chapter_id=chapter_id,
        frame_index=8, name="游离基线行", description=None, image_url=None,
        created_at=now, updated_at=now,
    ))
    connection.execute(insert(storyboard_media_states).values(
        id=f"media-orphan-{chapter_id}", chapter_id=chapter_id,
        storyboard_asset_id=f"orphan-{chapter_id}", media_revision=7,
        asset_image_digest="a" * 64, preview_digest="b" * 64,
        source_valid=True, created_at=now, updated_at=now,
    ))
    connection.execute(insert(chat_messages).values(
        id=f"chat-{chapter_id}", chapter_id=chapter_id, frame_index=0, asset_type=None,
        asset_id=None, chat_mode="text", role="user", content="保留的聊天",
        model_name=None, created_at=now,
    ))
    return now, actor_id


def _guard(series_id: str, chapter_id: str, now: datetime, *, lock: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], ChapterEditGuard]:
    old = [{
        "text": "基线镜头",
        "character": [],
        "scene": [],
        "prop": [],
        "storyboard": [f"shot-{chapter_id}"],
        "preview": None,
        "original_text": "打开时的原文",
        "unknown": {"score": 1},
    }]
    new = [
        {**old[0], "text": "保存后的镜头"},
        {"text": "新增镜头", "character": [], "scene": [], "prop": [], "storyboard": [], "preview": None},
    ]
    payload = {
        "version": 1,
        "expected_chapter_id": chapter_id,
        "expected_series_id": series_id,
        "expected_order": 1,
        "expected_content": old,
        "expected_storyboards": [
            {"id": f"shot-{chapter_id}", "series_id": series_id, "chapter_id": chapter_id, "frame_index": 0},
            {"id": f"orphan-{chapter_id}", "series_id": series_id, "chapter_id": chapter_id, "frame_index": 8},
        ],
        "expected_lock_identity": lock,
        "frame_sources": [
            {"kind": "retained", "source_index": 0, "storyboard_id": f"shot-{chapter_id}"},
            {"kind": "new"},
        ],
        "deleted_source_indexes": [],
    }
    return new, ChapterEditGuard.model_validate(payload)


def _run_guard(sessions: sessionmaker[Session], actor: TrustedActor, chapter_id: str, content: list[dict[str, Any]], guard: ChapterEditGuard) -> str:
    with sessions() as session:
        uow = SqlAlchemySeriesDataUnitOfWork(session)
        try:
            updated = uow.update_chapter_guarded(actor, chapter_id, content, guard, datetime.now(timezone.utc).replace(tzinfo=None))
            return f"saved:{updated.id}"
        except SeriesDataConflict:
            return "conflict"


@_record_pg_case_failure("twoConnectionRace")
def test_two_connections_same_baseline_have_one_commit_and_keep_orphan_media(owned_postgres_1145) -> None:
    engine, sessions, receipt = owned_postgres_1145
    series_id, chapter_id = _new_id("s-"), _new_id("c-")
    with engine.begin() as connection:
        _now, actor_id = _insert_actor_and_series(connection, series_id=series_id, chapter_id=chapter_id)
    new_content, guard = _guard(series_id, chapter_id, datetime.now(timezone.utc).replace(tzinfo=None))
    barrier = Barrier(2)

    def contender() -> str:
        barrier.wait(timeout=10)
        return _run_guard(sessions, TrustedActor(user_id=actor_id), chapter_id, new_content, guard)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _index: contender(), range(2)))
        assert sum(result.startswith("saved:") for result in outcomes) == 1, outcomes
        assert outcomes.count("conflict") == 1, outcomes
        with sessions() as session:
            chapter = session.execute(select(chapters).where(chapters.c.id == chapter_id)).mappings().one()
            assets = session.execute(select(storyboard_assets).where(storyboard_assets.c.chapter_id == chapter_id)).mappings().all()
            chats = session.execute(select(chat_messages).where(chat_messages.c.chapter_id == chapter_id)).mappings().all()
            orphan_state = session.execute(
                select(storyboard_media_states).where(
                    storyboard_media_states.c.storyboard_asset_id == f"orphan-{chapter_id}"
                )
            ).mappings().one()
        saved_content = json.loads(chapter["content"])
        assert len(saved_content) == 2
        assert saved_content[0]["original_text"] == "打开时的原文"
        assert len(assets) == 3
        assert {item["frame_index"] for item in assets if item["id"] != f"orphan-{chapter_id}"} == {0, 1}
        assert len(chats) == 1 and chats[0]["frame_index"] == 0
        assert orphan_state["source_valid"] is True and orphan_state["media_revision"] == 7
        _write_receipt(receipt, "PASS", {"twoConnectionRace": "one commit, one baseline conflict", "orphanMedia": "preserved"})
    except Exception as error:
        _write_receipt(receipt, "FAIL", {"twoConnectionRace": False}, type(error).__name__)
        raise


@_record_pg_case_failure("replacedLockAndChatRollback")
def test_replaced_lock_and_chat_sync_failure_leave_no_partial_chapter_write(owned_postgres_1145) -> None:
    engine, sessions, receipt = owned_postgres_1145
    series_id, chapter_id, team_id = _new_id("s-"), _new_id("c-"), _new_id("t-")
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    with engine.begin() as connection:
        _now, actor_id = _insert_actor_and_series(
            connection, series_id=series_id, chapter_id=chapter_id, team_id=team_id,
        )
        connection.execute(insert(chapter_locks).values(
            id=_new_id("l-"), chapter_id=chapter_id, user_id=actor_id, username=f"作者-{actor_id}",
            acquired_at=now, last_active_at=now, expires_at=now + timedelta(hours=1),
        ))
    content, guard = _guard(
        series_id,
        chapter_id,
        now,
        lock={"user_id": actor_id, "acquired_at": now.isoformat()},
    )
    replaced = now + timedelta(seconds=1)
    with engine.begin() as connection:
        connection.execute(update(chapter_locks).where(
            chapter_locks.c.chapter_id == chapter_id,
        ).values(acquired_at=replaced, last_active_at=replaced))
    try:
        outcome = _run_guard(sessions, TrustedActor(user_id=actor_id), chapter_id, content, guard)
        assert outcome == "conflict"

        with sessions() as session:
            before_chapter = session.execute(select(chapters.c.content).where(chapters.c.id == chapter_id)).scalar_one()
        with engine.begin() as connection:
            connection.execute(update(chapter_locks).where(
                chapter_locks.c.chapter_id == chapter_id,
            ).values(acquired_at=now, last_active_at=now))
            connection.execute(text(
                f"CREATE OR REPLACE FUNCTION fail_h1145_chat_update() RETURNS trigger AS $$ "
                f"BEGIN RAISE EXCEPTION 'h1145 injected chat mapping failure'; END; $$ LANGUAGE plpgsql"
            ))
            connection.execute(text(
                f"CREATE TRIGGER fail_h1145_chat_update BEFORE UPDATE ON chat_messages "
                f"FOR EACH ROW EXECUTE FUNCTION fail_h1145_chat_update()"
            ))
        reordered = [{**content[0], "text": "触发聊天映射写入"}, content[1]]
        reordered[0], reordered[1] = reordered[1], reordered[0]
        # This guard deliberately maps the original indexed chat to index 1.
        remap_guard_payload = guard.model_dump(mode="json")
        remap_guard_payload["frame_sources"] = [
            {"kind": "new"},
            {"kind": "retained", "source_index": 0, "storyboard_id": f"shot-{chapter_id}"},
        ]
        remap_guard = ChapterEditGuard.model_validate(remap_guard_payload)
        with pytest.raises(Exception, match="h1145 injected chat mapping failure"):
            with sessions() as session:
                uow = SqlAlchemySeriesDataUnitOfWork(session)
                uow.update_chapter_guarded(
                    TrustedActor(user_id=actor_id), chapter_id, reordered, remap_guard,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                )
        with sessions() as session:
            after_chapter = session.execute(select(chapters.c.content).where(chapters.c.id == chapter_id)).scalar_one()
            assets = session.execute(select(storyboard_assets).where(storyboard_assets.c.chapter_id == chapter_id)).mappings().all()
            chat = session.execute(select(chat_messages).where(chat_messages.c.chapter_id == chapter_id)).mappings().one()
        assert after_chapter == before_chapter
        assert len(assets) == 2
        assert chat["frame_index"] == 0
        _write_receipt(receipt, "PASS", {"replacedLock": "conflict without write", "chatFailure": "whole transaction rolled back"})
    except Exception as error:
        _write_receipt(receipt, "FAIL", {"replacedLockAndChatRollback": False}, type(error).__name__)
        raise
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER IF EXISTS fail_h1145_chat_update ON chat_messages"))
            connection.execute(text("DROP FUNCTION IF EXISTS fail_h1145_chat_update()"))


@_record_pg_case_failure("emptyChapterMapping")
def test_empty_chapter_removes_all_mapped_chats_but_preserves_unrelated_storyboard(owned_postgres_1145) -> None:
    engine, sessions, receipt = owned_postgres_1145
    series_id, chapter_id = _new_id("s-"), _new_id("c-")
    with engine.begin() as connection:
        _now, actor_id = _insert_actor_and_series(connection, series_id=series_id, chapter_id=chapter_id)
    _content, guard = _guard(series_id, chapter_id, datetime.now(timezone.utc).replace(tzinfo=None))
    guard_payload = guard.model_dump(mode="json")
    guard_payload["frame_sources"] = []
    guard_payload["deleted_source_indexes"] = [0]
    empty_guard = ChapterEditGuard.model_validate(guard_payload)

    try:
        with sessions() as session:
            uow = SqlAlchemySeriesDataUnitOfWork(session)
            updated = uow.update_chapter_guarded(
                TrustedActor(user_id=actor_id), chapter_id, [], empty_guard,
                datetime.now(timezone.utc).replace(tzinfo=None),
            )
        assert updated.content == "[]"
        with sessions() as session:
            assets = session.execute(
                select(storyboard_assets.c.id).where(storyboard_assets.c.chapter_id == chapter_id)
            ).scalars().all()
            chat_ids = session.execute(
                select(chat_messages.c.id).where(chat_messages.c.chapter_id == chapter_id)
            ).scalars().all()
            orphan_state = session.execute(
                select(storyboard_media_states).where(
                    storyboard_media_states.c.storyboard_asset_id == f"orphan-{chapter_id}"
                )
            ).mappings().one()
        assert assets == [f"orphan-{chapter_id}"]
        assert chat_ids == []
        assert orphan_state["source_valid"] is True and orphan_state["media_revision"] == 7
        _write_receipt(receipt, "PASS", {"emptyChapterMapping": "all chat indexes removed; unrelated storyboard media preserved"})
    except Exception as error:
        _write_receipt(receipt, "FAIL", {"emptyChapterMapping": False}, type(error).__name__)
        raise
