"""Real PostgreSQL checks for continuous chapter operation receipts.

This module runs only against a supervisor-owned loopback PostgreSQL instance.
"""

from __future__ import annotations

import json
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import wraps
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, insert, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from haoai_backend.personal_production.rough_cut.tables import metadata as rough_metadata
from haoai_backend.series_data.continuous_operation_receipts import operation_receipts
from haoai_backend.series_data.continuous_operation_schema import (
    apply_continuous_operation_migration,
    check_continuous_operation_schema,
)
from haoai_backend.series_data.continuous_operation_schemas import ContinuousChapterOperationRequest
from haoai_backend.series_data.continuous_operations import (
    execute_continuous_chapter_operation,
    get_continuous_chapter_operation,
)
from haoai_backend.series_data.errors import SeriesDataConflict, SeriesDataForbidden, SeriesDataNotFound
from haoai_backend.series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from haoai_backend.series_data.tables import (
    canvas_documents,
    chapters,
    characters,
    chapter_locks,
    chat_messages,
    metadata as series_metadata,
    rough_cut_drafts,
    series,
    storyboard_assets,
    team_members,
    teams,
)
from haoai_backend.shared.identity import TrustedActor


_MARKER = "haoai-continuous-chapter-operation-1164"
_PREFIX = "HAOAI_OWNED_PG_1145_"
_ENV_NAMES = tuple(_PREFIX + suffix for suffix in ("URL", "PORT", "MARKER", "METADATA", "RECEIPT", "REPO"))


def _write_receipt(path: Path | None, status: str, cases: dict[str, object], error: str | None = None) -> None:
    if path is None:
        return
    existing: dict[str, Any] = {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("purpose") == _MARKER:
            existing = value
    except (OSError, json.JSONDecodeError):
        pass
    merged = dict(existing.get("cases", {})) if isinstance(existing.get("cases"), dict) else {}
    merged.update(cases)
    final_status = "FAIL" if status == "FAIL" or existing.get("status") == "FAIL" else status
    payload: dict[str, object] = {
        "status": final_status,
        "purpose": _MARKER,
        "cases": merged,
        "database": "supervisor-owned temporary PostgreSQL",
        "network": "loopback PostgreSQL only",
    }
    if error is not None:
        payload["failureType"] = error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_case(name: str):
    def decorate(test):
        @wraps(test)
        def wrapped(*args, **kwargs):
            try:
                result = test(*args, **kwargs)
            except Exception as error:
                fixture = kwargs.get("owned_postgres_1164")
                if fixture is None and args:
                    fixture = args[0]
                receipt = fixture[2] if isinstance(fixture, tuple) and len(fixture) == 3 else None
                _write_receipt(receipt, "FAIL", {name: False}, type(error).__name__)
                raise
            fixture = kwargs.get("owned_postgres_1164")
            if fixture is None and args:
                fixture = args[0]
            receipt = fixture[2] if isinstance(fixture, tuple) and len(fixture) == 3 else None
            _write_receipt(receipt, "PASS", {name: True})
            return result
        return wrapped
    return decorate


def _owned_config() -> tuple[str, int, Path, Path, Path | None]:
    values = {name: os.environ.get(name) for name in _ENV_NAMES}
    receipt_value = values[_PREFIX + "RECEIPT"]
    receipt = Path(receipt_value).resolve() if receipt_value else None
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
            raise RuntimeError("owned PostgreSQL process metadata is invalid")
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
def owned_postgres_1164() -> tuple[Engine, sessionmaker[Session], Path | None]:
    if not any(os.environ.get(name) for name in _ENV_NAMES):
        pytest.skip("requires supervisor-owned HAOAI_OWNED_PG_1145_* environment")
    url, port, _tmp_root, expected_data_dir, receipt = _owned_config()
    _write_receipt(receipt, "RUNNING", {})
    schema_name = "h1164_" + uuid4().hex
    engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 3,
            "options": f"-c search_path={schema_name} -c lock_timeout=5000",
        },
    )
    try:
        with engine.begin() as connection:
            data_dir = Path(connection.execute(text("SHOW data_directory")).scalar_one()).resolve()
            server_port = int(connection.execute(text("SHOW port")).scalar_one())
            if data_dir != expected_data_dir or server_port != port:
                raise RuntimeError("connected PostgreSQL instance did not match owned metadata")
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        auth_metadata.create_all(engine, tables=[auth_users])
        series_metadata.create_all(engine)
        notes_metadata.create_all(engine)
        rough_metadata.create_all(engine)
        with engine.begin() as connection:
            now = datetime(2026, 1, 2, 3, 4, 5)
            connection.execute(insert(auth_users).values(
                id="user-a", username="作者", email="user-a@h1164.invalid", hashed_password="x",
                is_superuser=False, membership_type="free", created_at=now, password_updated_at=None,
            ))
        apply_continuous_operation_migration(engine)
        sessions = sessionmaker(engine, expire_on_commit=False, future=True)
        yield engine, sessions, receipt
    except Exception as error:
        _write_receipt(receipt, "FAIL", {"ownedInstanceSetup": False}, type(error).__name__)
        raise
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        finally:
            engine.dispose()


def _factory(sessions: sessionmaker[Session]):
    return lambda: SqlAlchemySeriesDataUnitOfWork(sessions())


@pytest.fixture(autouse=True)
def clear_operation_test_rows(owned_postgres_1164) -> None:
    engine, _sessions, _receipt = owned_postgres_1164
    with engine.begin() as connection:
        connection.execute(delete(operation_receipts))
        connection.execute(delete(storyboard_assets))
        connection.execute(delete(chat_messages))
        connection.execute(delete(rough_cut_drafts))
        connection.execute(delete(canvas_documents))
        connection.execute(delete(chapter_locks))
        connection.execute(delete(chapters))
        connection.execute(delete(characters))
        connection.execute(delete(team_members))
        connection.execute(delete(teams))
        connection.execute(delete(series))


def _seed_chapter(engine: Engine, *, chapter_id: str = "chapter-a", series_id: str = "series-a") -> list[dict[str, Any]]:
    now = datetime(2026, 1, 2, 3, 4, 5)
    content = [{
        "text": "原始正文", "original_text": "打开时原文", "character": [], "scene": [], "prop": [],
        "storyboard": [], "preview": None, "unknown": {"preserved": True},
    }]
    with engine.begin() as connection:
        connection.execute(insert(series).values(
            id=series_id, user_id="user-a", name="受控测试剧集", description=None, image_url=None,
            style_prompt_id=None, team_id=None, claimed_by=None, claimed_at=None,
            created_at=now, updated_at=now,
        ))
        connection.execute(insert(chapters).values(
            id=chapter_id, series_id=series_id, title="测试章节", content=json.dumps(content), order=1,
            created_at=now, updated_at=now,
        ))
    return content


def _save_request(content: list[dict[str, Any]], *, text_value: str = "保存后的正文") -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "editor_session_id": str(uuid4()),
        "command": {
            "kind": "save-chapter",
            "content": [{**content[0], "text": text_value}],
            "chapter_edit_guard": {
                "version": 1,
                "expected_chapter_id": "chapter-a",
                "expected_series_id": "series-a",
                "expected_order": 1,
                "expected_content": content,
                "expected_storyboards": [],
                "expected_lock_identity": None,
                "frame_sources": [{"kind": "retained", "source_index": 0, "storyboard_id": None}],
                "deleted_source_indexes": [],
            },
            "draft_frame_ids": ["draft-a"],
        },
    }


def _execute(factory, actor: TrustedActor, chapter_id: str, operation_id: str, body: dict[str, Any]):
    request = ContinuousChapterOperationRequest.model_validate(body)
    raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return execute_continuous_chapter_operation(
        factory, actor, chapter_id, operation_id, request, hashlib.sha256(raw_body.encode()).hexdigest(),
    )


@_record_case("feature_migration_and_postgresql_readiness")
def test_feature_migration_is_ready_on_postgresql(owned_postgres_1164) -> None:
    engine, _sessions, _receipt = owned_postgres_1164
    with engine.connect() as connection:
        status = check_continuous_operation_schema(connection)
    assert status.status == "ready", status.issues


@_record_case("same_operation_has_one_effect_and_other_body_conflicts")
def test_same_operation_replay_and_changed_body_conflict(owned_postgres_1164) -> None:
    engine, sessions, _receipt = owned_postgres_1164
    original = _seed_chapter(engine)
    factory = _factory(sessions)
    operation_id = str(uuid4())
    first_body = _save_request(original)
    first = _execute(factory, TrustedActor(user_id="user-a"), "chapter-a", operation_id, first_body)
    replay = _execute(factory, TrustedActor(user_id="user-a"), "chapter-a", operation_id, first_body)
    changed_body = _save_request(original, text_value="不能作为第二次写入")
    with pytest.raises(SeriesDataConflict):
        _execute(factory, TrustedActor(user_id="user-a"), "chapter-a", operation_id, changed_body)

    assert first.status_code == replay.status_code == 200
    assert first.payload == replay.payload
    with sessions() as session:
        saved = json.loads(session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one())
        receipts = session.execute(select(operation_receipts)).mappings().all()
    assert saved[0]["text"] == "保存后的正文"
    assert len(receipts) == 1


@_record_case("two_independent_sessions_race_one_operation_once")
def test_two_independent_sessions_race_the_same_operation_once(owned_postgres_1164) -> None:
    engine, sessions, _receipt = owned_postgres_1164
    original = _seed_chapter(engine)
    operation_id = str(uuid4())
    body = _save_request(original)
    receipt_read_barrier = Barrier(2)

    class BarrierUnitOfWork(SqlAlchemySeriesDataUnitOfWork):
        def get_continuous_operation_receipt(self, owner_id, operation_id, *, for_update):
            receipt = super().get_continuous_operation_receipt(
                owner_id,
                operation_id,
                for_update=for_update,
            )
            if for_update and receipt is None:
                receipt_read_barrier.wait(timeout=10)
            return receipt

    def factory():
        return BarrierUnitOfWork(sessions())

    def execute_once():
        return _execute(
            factory,
            TrustedActor(user_id="user-a"),
            "chapter-a",
            operation_id,
            body,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: execute_once(), range(2)))

    assert [result.status_code for result in results] == [200, 200]
    assert results[0].payload == results[1].payload
    with sessions() as session:
        saved = json.loads(session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one())
        receipts = session.execute(select(operation_receipts)).mappings().all()
    assert saved[0]["text"] == "保存后的正文"
    assert len(receipts) == 1


@_record_case("receipt_insert_failure_rolls_back_chapter_effect")
def test_receipt_insert_failure_rolls_back_chapter_effect(owned_postgres_1164) -> None:
    engine, sessions, _receipt = owned_postgres_1164
    original = _seed_chapter(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE FUNCTION reject_continuous_receipt() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'receipt unavailable'; END $$"
        ))
        connection.execute(text(
            "CREATE TRIGGER reject_continuous_receipt BEFORE INSERT ON continuous_chapter_operation_receipts "
            "FOR EACH ROW EXECUTE FUNCTION reject_continuous_receipt()"
        ))
    operation_id = str(uuid4())
    try:
        with pytest.raises(Exception):
            _execute(_factory(sessions), TrustedActor(user_id="user-a"), "chapter-a", operation_id, _save_request(original))
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER IF EXISTS reject_continuous_receipt ON continuous_chapter_operation_receipts"))
            connection.execute(text("DROP FUNCTION IF EXISTS reject_continuous_receipt()"))
    with sessions() as session:
        saved = json.loads(session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one())
        receipts = session.execute(select(operation_receipts)).all()
    assert saved == original
    assert receipts == []


@_record_case("asset_create_replay_returns_exact_original_id")
def test_asset_create_replay_returns_the_exact_original_asset_id(owned_postgres_1164) -> None:
    engine, sessions, _receipt = owned_postgres_1164
    original = _seed_chapter(engine)
    operation_id = str(uuid4())
    body = {
        "protocol_version": 1,
        "editor_session_id": str(uuid4()),
        "command": {
            "kind": "create-character",
            "input": {"name": "唯一原操作角色"},
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
    factory = _factory(sessions)
    first = _execute(factory, TrustedActor(user_id="user-a"), "chapter-a", operation_id, body)
    replay = _execute(factory, TrustedActor(user_id="user-a"), "chapter-a", operation_id, body)

    assert first.payload == replay.payload
    assert first.payload["result"]["id"] == first.payload["receipt"]["result_identity"]["asset_id"]
    with sessions() as session:
        assets = session.execute(select(characters).where(characters.c.series_id == "series-a")).mappings().all()
        receipts = session.execute(select(operation_receipts)).mappings().all()
    assert [asset["id"] for asset in assets] == [first.payload["result"]["id"]]
    assert len(receipts) == 1


@_record_case("forty_character_draft_id_create_replays_exactly")
def test_forty_character_draft_id_create_replays_exactly(owned_postgres_1164) -> None:
    engine, sessions, _receipt = owned_postgres_1164
    original = _seed_chapter(engine)
    operation_id = str(uuid4())
    target_draft_id = f"new:{uuid4()}"
    assert len(target_draft_id) == 40
    body = {
        "protocol_version": 1,
        "editor_session_id": str(uuid4()),
        "command": {
            "kind": "create-character",
            "input": {"name": "长草稿编号目标角色"},
            "target_draft_id": target_draft_id,
            "draft_frame_ids": [target_draft_id],
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
    factory = _factory(sessions)
    first = _execute(factory, TrustedActor(user_id="user-a"), "chapter-a", operation_id, body)
    replay = _execute(factory, TrustedActor(user_id="user-a"), "chapter-a", operation_id, body)

    assert first.payload == replay.payload
    assert first.payload["receipt"]["target_draft_id"] == target_draft_id
    with sessions() as session:
        receipts = session.execute(select(operation_receipts)).mappings().all()
    assert len(receipts) == 1
    assert receipts[0]["target_draft_id"] == target_draft_id


@_record_case("cross_owner_lookup_does_not_disclose_receipt")
def test_cross_owner_lookup_does_not_disclose_original_receipt(owned_postgres_1164) -> None:
    engine, sessions, _receipt = owned_postgres_1164
    original = _seed_chapter(engine)
    operation_id = str(uuid4())
    _execute(_factory(sessions), TrustedActor(user_id="user-a"), "chapter-a", operation_id, _save_request(original))

    with pytest.raises(SeriesDataNotFound):
        get_continuous_chapter_operation(_factory(sessions), TrustedActor(user_id="user-b"), "chapter-a", operation_id)


@_record_case("receipt_owner_loses_series_permission")
def test_receipt_owner_cannot_read_after_series_permission_is_removed(owned_postgres_1164) -> None:
    engine, sessions, _receipt = owned_postgres_1164
    original = _seed_chapter(engine)
    now = datetime(2026, 1, 2, 3, 4, 5)
    with engine.begin() as connection:
        connection.execute(insert(auth_users).values(
            id="user-b", username="协作者", email="user-b@h1164.invalid", hashed_password="x",
            is_superuser=False, membership_type="free", created_at=now, password_updated_at=None,
        ))
        connection.execute(insert(teams).values(
            id="team-a", name="授权测试团队", owner_id="user-a",
        ))
        connection.execute(insert(team_members).values(
            id="member-b", team_id="team-a", user_id="user-b", role="member", permissions="[]",
        ))
        connection.execute(text(
            "UPDATE series SET team_id = 'team-a' WHERE id = 'series-a'"
        ))

    operation_id = str(uuid4())
    _execute(
        _factory(sessions),
        TrustedActor(user_id="user-b"),
        "chapter-a",
        operation_id,
        _save_request(original),
    )
    with engine.begin() as connection:
        connection.execute(delete(team_members).where(team_members.c.user_id == "user-b"))

    with pytest.raises(SeriesDataForbidden):
        get_continuous_chapter_operation(
            _factory(sessions),
            TrustedActor(user_id="user-b"),
            "chapter-a",
            operation_id,
        )
