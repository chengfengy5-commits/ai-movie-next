"""SQLite transaction coverage for the explicit Canvas generation PUT mode.

The fixture below creates its result receipts through the real admission,
snapshot producer, and a controlled image worker. Each behavior test then
copies that completed canonical chain into an isolated SQLite database.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, event, func, insert, inspect, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

_TESTS = Path(__file__).resolve().parent
_EXTRA_TEST_PATHS = (
    str(_TESTS),
    str(_TESTS / "generation_core"),
    str(_TESTS / "generation_execution"),
)
_ADDED_TEST_PATHS = [path for path in _EXTRA_TEST_PATHS if path not in sys.path]
sys.path[:0] = _ADDED_TEST_PATHS
try:
    import test_app_chat_media_admission_integration as admission_fixture
    staging_fixture = admission_fixture.admission_staging_module
    import test_image_single_worker as image_worker_fixture
finally:
    for _path in _ADDED_TEST_PATHS:
        sys.path.remove(_path)

from haoai_backend.asset_data.http import build_asset_data_router
from haoai_backend.asset_data.tables import (
    chapter_locks,
    characters,
    props,
    scenes,
    storyboard_assets as asset_storyboard_assets,
)
from haoai_backend.authentication.tables import users
from haoai_backend.canvas_applications import application as canvas_application
from haoai_backend.canvas_sources.persistence import chapters
from haoai_backend.generation_catalog.admission_types import AdmissionOperation
from haoai_backend.generation_contracts.types import GenerationActor, TargetSnapshot
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_core.schema import ApplicationRecordRow, GenerationResultReceiptRow
from haoai_backend.generation_core.schema import LocalContinuationWorkRow
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.personal_production.notes.application import (
    get_personal_production_notes,
    save_personal_production_notes,
)
from haoai_backend.personal_production.notes.domain import FrameNoteChange, PersonalNotesUpdate
from haoai_backend.personal_production.notes.persistence import SqlAlchemyNotesUnitOfWork
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_data.http import build_series_data_router
from haoai_backend.series_data.tables import storyboard_assets
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation import (
    GenerationResultApplicationFacade,
    application_key_for,
    target_scope_for_snapshot,
)
from haoai_backend.task_observation.canvas_result_http import build_canvas_result_http_router
from haoai_backend.task_observation.errors import TaskObservationConflict


_ACTOR_ID = admission_fixture._G2_JOINT_ACTOR_ID
_CHAPTER_ID = "joint-chapter-1"
_SERIES_ID = "joint-series-1"
_TEAM_ID = "c730-team-1"
_OUTPUT_URL = "https://media.example.test/managed/image.png"
_STORYBOARD_IMAGE_URL = "https://media.example.test/original/storyboard.png"
_STAMP = staging_fixture._G2_JOINT_NOW.replace(tzinfo=None)
_LOCK_EXPIRY = datetime(2099, 1, 1)
_CANONICAL_DATABASE_PATH: str | None = None


@dataclass(frozen=True)
class _CanonicalResult:
    task_id: str
    result_ref: str
    target: TargetSnapshot


@dataclass
class _SqlTrace:
    active: bool = False
    statements: list[str] | None = None
    connection_ids: list[int] | None = None

    def __post_init__(self) -> None:
        self.statements = []
        self.connection_ids = []

    def before_cursor_execute(
        self, connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if self.active:
            assert self.statements is not None and self.connection_ids is not None
            self.statements.append(statement)
            self.connection_ids.append(id(connection.connection.dbapi_connection))


class _ApplicationSession(Session):
    def commit(self) -> None:
        self.info["commit_calls"] = int(self.info.get("commit_calls", 0)) + 1
        failure_mode = self.info["control"].get("commit_failure")
        if failure_mode == "before":
            raise RuntimeError("synthetic failure before the sqlite commit")
        super().commit()
        if failure_mode == "after" and not self.info["control"].get("commit_failure_used"):
            self.info["control"]["commit_failure_used"] = True
            raise RuntimeError("synthetic lost commit acknowledgement after sqlite commit")

    def rollback(self) -> None:
        self.info["rollback_calls"] = int(self.info.get("rollback_calls", 0)) + 1
        super().rollback()

    def close(self) -> None:
        self.info["close_calls"] = int(self.info.get("close_calls", 0)) + 1
        super().close()


@dataclass
class _DatabaseHarness:
    engine: Engine
    sessions: list[_ApplicationSession]
    control: dict[str, object]
    trace: _SqlTrace
    app: FastAPI


@pytest.fixture(scope="module")
def canonical_results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, _CanonicalResult]:
    """Seed one real canonical result for each target identity using a stub worker."""

    global _CANONICAL_DATABASE_PATH
    root = tmp_path_factory.mktemp("canvas-application-canonical")
    monkeypatch = pytest.MonkeyPatch()
    _assert_shared_admission_fixture_module()
    monkeypatch.setattr(staging_fixture, "_G2_JOINT_PROVIDER", "openai")
    system = admission_fixture._build_system(root)
    enrollment_source = system.capabilities.enrollment_source
    assert enrollment_source is not None and len(enrollment_source.records) == 1
    registered_record = enrollment_source.records[0]
    assert registered_record.scope.provider == "openai"
    assert registered_record.material_ref == "vault://joint/provider"
    with system.engine.connect() as connection:
        model_provider = connection.execute(
            select(admission_fixture.system_models.c.provider).where(
                admission_fixture.system_models.c.id == admission_fixture._G2_JOINT_MODEL_ID
            )
        ).scalar_one()
    assert model_provider == "openai"
    result_by_key: dict[str, _CanonicalResult] = {}
    original_prepare = admission_fixture.snapshot_producer.MediaSnapshotProducer.prepare_and_stage
    captured_targets: list[TargetSnapshot] = []
    try:
        _ensure_asset_data_projection_columns(system.engine)
        with system.engine.begin() as connection:
            connection.execute(
                update(admission_fixture.catalog_chapters)
                .where(admission_fixture.catalog_chapters.c.id == _CHAPTER_ID)
                .values(
                    content=json.dumps(
                        [
                            {"text": "No stable identity", "storyboard": []},
                            {
                                "text": "Stable storyboard identity",
                                "storyboard": ["joint-storyboard-c730"],
                            },
                        ],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
            )
            connection.execute(
                update(staging_fixture.series)
                .where(staging_fixture.series.c.id == _SERIES_ID)
                .values(team_id=_TEAM_ID)
            )
            connection.execute(
                insert(storyboard_assets).values(
                    id="joint-storyboard-c730",
                    series_id=_SERIES_ID,
                    chapter_id=_CHAPTER_ID,
                    frame_index=1,
                    name="Stable frame",
                    description="Original storyboard description",
                    image_url=_STORYBOARD_IMAGE_URL,
                    created_at=_STAMP,
                    updated_at=_STAMP,
                )
            )
            connection.execute(
                insert(props).values(
                    id="joint-prop-1",
                    series_id=_SERIES_ID,
                    name="Joint prop",
                    description="Original prop description",
                    image_url=None,
                    aliases=None,
                    canonical_key=None,
                    created_at=_STAMP,
                    updated_at=_STAMP,
                )
            )
            connection.execute(
                insert(chapter_locks).values(
                    id="c730-lock-1",
                    chapter_id=_CHAPTER_ID,
                    user_id=_ACTOR_ID,
                    username="joint-fixture-admin",
                    acquired_at=_STAMP,
                    last_active_at=_STAMP,
                    expires_at=_LOCK_EXPIRY,
                )
            )
        _assert_sqlite_columns(
            system.engine,
            "series",
            {"id", "user_id", "team_id", "claimed_by"},
        )
        _assert_sqlite_columns(
            system.engine,
            "chapter_locks",
            {
                "id",
                "chapter_id",
                "user_id",
                "username",
                "acquired_at",
                "last_active_at",
                "expires_at",
            },
        )
        notes_metadata.create_all(
            system.engine,
            tables=[storyboard_media_states, personal_production_notes],
        )
        _assert_sqlite_columns(
            system.engine,
            "storyboard_media_states",
            {column.name for column in storyboard_media_states.columns},
        )
        _assert_sqlite_columns(
            system.engine,
            "personal_production_notes",
            {column.name for column in personal_production_notes.columns},
        )
        def notes_uow_factory() -> SqlAlchemyNotesUnitOfWork:
            def check_access(session: Session, actor: TrustedActor, series_id: str) -> None:
                require_series_access(
                    SqlAlchemySeriesAccessReader(session),
                    ActorIdentity(actor.user_id),
                    series_id,
                )

            return SqlAlchemyNotesUnitOfWork(
                Session(system.engine, expire_on_commit=False), check_access
            )

        actor = TrustedActor(_ACTOR_ID)
        initial_notes = get_personal_production_notes(
            notes_uow_factory, actor, _CHAPTER_ID
        )
        assert initial_notes["revision"] == 0
        assert initial_notes["frames"][1]["storyboard_asset_id"] == "joint-storyboard-c730"
        assert initial_notes["frames"][1]["media_revision"] == 1
        approved_notes = save_personal_production_notes(
            notes_uow_factory,
            actor,
            _CHAPTER_ID,
            PersonalNotesUpdate(
                expected_revision=0,
                frames=(
                    FrameNoteChange(
                        storyboard_asset_id="joint-storyboard-c730",
                        expected_media_revision=1,
                        status="approved",
                        note="Approved against the seeded source image at revision one.",
                    ),
                ),
                has_resume_patch=False,
                resume_frame_id=None,
            ),
        )
        assert approved_notes["revision"] == 1

        authentication, _auth_sessions = staging_fixture._joint_authentication_service(
            system.engine
        )
        binding = staging_fixture._joint_build_binding(
            authentication,
            system.capabilities,
            staging_fixture._JointIdFactory("c730-server-id"),
            operation=AdmissionOperation.IMAGE_SINGLE.value,
        )
        assert binding._producer._registry_capabilities is system.capabilities

        frames = [
            {"text": "No stable identity", "storyboard": []},
            {
                "text": "Stable storyboard identity",
                "storyboard": ["joint-storyboard-c730"],
            },
        ]
        chapter_digest = admission_fixture._canonical_json_digest(frames)
        targets: dict[str, dict[str, object]] = {
            "no_id": {
                "kind": "canvas_apply",
                "destination": {
                    "kind": "frame_preview",
                    "chapter_id": _CHAPTER_ID,
                    "identity_mode": "strict-no-id-v1",
                    "frame_index": 0,
                    "expected_chapter_digest": chapter_digest,
                },
            },
            "stable": {
                "kind": "canvas_apply",
                "destination": {
                    "kind": "frame_preview",
                    "chapter_id": _CHAPTER_ID,
                    "storyboard_asset_id": "joint-storyboard-c730",
                },
            },
        }
        targets.update(
            {
                asset_type: {
                    "kind": "canvas_apply",
                    "destination": {
                        "kind": "asset_image",
                        "chapter_id": _CHAPTER_ID,
                        "asset_type": asset_type,
                        "asset_id": asset_id,
                    },
                }
                for asset_type, asset_id in (
                    ("character", "joint-character-1"),
                    ("scene", "joint-scene-1"),
                    ("prop", "joint-prop-1"),
                )
            }
        )

        def capture_target(producer, caller, **kwargs):
            staged_payloads = original_prepare(producer, caller, **kwargs)
            if isinstance(
                staged_payloads,
                admission_fixture.snapshot_producer.SnapshotPayloadsStaged,
            ):
                captured_targets.append(staged_payloads.items[0].snapshot.target_snapshot)
            return staged_payloads

        monkeypatch.setattr(
            admission_fixture.snapshot_producer.MediaSnapshotProducer,
            "prepare_and_stage",
            capture_target,
        )
        task_ids: dict[str, str] = {}
        for key, target in targets.items():
            request = staging_fixture._joint_request(AdmissionOperation.IMAGE_SINGLE.value)
            request["reference_images"] = []
            request["target"] = target
            caller = staging_fixture._joint_caller(system.engine)
            try:
                staged = staging_fixture._joint_prepare_and_stage(
                    binding,
                    caller,
                    operation=AdmissionOperation.IMAGE_SINGLE.value,
                    raw_key=f"c730-real-target-{key}",
                    request=request,
                )
                assert isinstance(staged, admission_fixture.AdmissionStaged)
                assert len(staged.ordered_tasks) == 1
                task_ids[key] = staged.ordered_tasks[0].task_id
                caller.commit()
            finally:
                caller.close()

        worker_capabilities = system.capabilities
        assert worker_capabilities is binding._producer._registry_capabilities
        worker_environment = type(
            "WorkerEnvironment",
            (),
            {"engine": system.engine, "capabilities": worker_capabilities},
        )()
        stored_media = []
        runtime_config, provider_calls = image_worker_fixture._runtime_configuration(
            worker_environment,
            image_worker_fixture._provider_body(),
            stored_media,
        )
        from haoai_backend.generation_runtime.assembly import (
            GenerationWorkerConfiguration,
            build_generation_worker_runtime,
        )

        runtime = build_generation_worker_runtime(
            GenerationWorkerConfiguration(runtime_config, worker_capabilities)
        )
        for _key in targets:
            outcome = runtime.run_claimed_once("image-single")
            assert outcome is not None and outcome != "pending" and outcome != "ownership_lost"
        assert len(provider_calls) == len(targets)
        assert len(captured_targets) == len(targets)

        with Session(system.engine, expire_on_commit=False) as seed_reader:
            for index, key in enumerate(targets):
                result_ref = seed_reader.execute(
                    select(GenerationResultReceiptRow.result_ref).where(
                        GenerationResultReceiptRow.task_id == task_ids[key]
                    )
                ).scalar_one()
                result_by_key[key] = _CanonicalResult(
                    task_id=task_ids[key],
                    result_ref=result_ref,
                    target=captured_targets[index],
                )
                assert result_by_key[key].target.kind in {
                    "canvas_frame_preview",
                    "canvas_asset_image",
                }
        database_path = system.engine.url.database
        assert isinstance(database_path, str)
        _CANONICAL_DATABASE_PATH = database_path
    finally:
        monkeypatch.undo()
        system.engine.dispose()
    return result_by_key


def _copy_sqlite_database(source_path: str, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _ensure_asset_data_projection_columns(engine: Engine) -> None:
    """Add nullable current projections to this throwaway SQLite fixture only."""

    if engine.dialect.name != "sqlite":
        raise AssertionError("the transaction fixture must use its isolated SQLite database")
    inspector = inspect(engine)
    identifier = engine.dialect.identifier_preparer.quote
    for table in (characters, scenes, props, asset_storyboard_assets):
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            type_sql = column.type.compile(dialect=engine.dialect)
            engine_name = identifier(table.name)
            column_name = identifier(column.name)
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    f"ALTER TABLE {engine_name} ADD COLUMN {column_name} {type_sql}"
                )
            existing.add(column.name)


def _assert_sqlite_columns(
    engine: Engine,
    table_name: str,
    expected: set[str],
) -> None:
    existing = {column["name"] for column in inspect(engine).get_columns(table_name)}
    missing = expected - existing
    assert not missing, f"SQLite fixture table {table_name} is missing columns: {sorted(missing)}"


def _assert_shared_admission_fixture_module() -> None:
    module = admission_fixture.admission_staging_module
    assert staging_fixture is module
    assert admission_fixture._joint_registered_capabilities.__globals__ is module.__dict__
    assert admission_fixture._joint_build_binding.__globals__ is module.__dict__
    assert staging_fixture._joint_prepare_and_stage.__globals__ is module.__dict__


def _database_harness(
    source_path: str,
    tmp_path: Path,
    *,
    actor_id: str = _ACTOR_ID,
    commit_failure: str | None = None,
) -> _DatabaseHarness:
    database_path = tmp_path / "canvas-application-case.sqlite"
    _copy_sqlite_database(source_path, database_path)
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")

    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(engine, "connect", enable_foreign_keys)
    sessions: list[_ApplicationSession] = []
    control: dict[str, object] = {
        "commit_failure": commit_failure,
        "commit_failure_used": False,
    }
    trace = _SqlTrace()
    event.listen(engine, "before_cursor_execute", trace.before_cursor_execute)

    def session_factory() -> Session:
        session = _ApplicationSession(bind=engine, expire_on_commit=False)
        session.info["control"] = control
        session.info["commit_calls"] = 0
        session.info["rollback_calls"] = 0
        session.info["close_calls"] = 0
        sessions.append(session)
        return session

    actor = TrustedActor(actor_id)
    resolver = lambda _request: actor
    app = FastAPI()
    app.include_router(
        build_series_data_router(
            uow_factory=None,
            resolve_actor=resolver,
            generation_session_factory=session_factory,
        )
    )
    app.include_router(
        build_asset_data_router(
            uow_factory=None,
            resolve_actor=resolver,
            generation_session_factory=session_factory,
        )
    )
    app.include_router(
        build_canvas_result_http_router(
            session_factory=session_factory,
            resolve_account_actor=resolver,
        )
    )
    return _DatabaseHarness(engine, sessions, control, trace, app)


def _wire(result: _CanonicalResult) -> dict[str, object]:
    target_mapping = asdict(result.target)
    scope = target_scope_for_snapshot(result.target)
    return {
        "task_id": result.task_id,
        "result_ref": result.result_ref,
        "expected_target": target_mapping,
        "application_key": application_key_for(result.result_ref, scope),
    }


def _put_path(result: _CanonicalResult, *, wrong_path: bool = False) -> str:
    target = result.target
    if wrong_path:
        return f"/api/chapters/{_CHAPTER_ID}"
    if target.kind == "canvas_frame_preview":
        return f"/api/chapters/{_CHAPTER_ID}"
    if target.asset_type == "character":
        return f"/api/characters/{target.asset_id}"
    if target.asset_type == "scene":
        return f"/api/scenes/{target.asset_id}"
    if target.asset_type == "prop":
        return f"/api/props/{target.asset_id}"
    raise AssertionError("canonical result fixture has an unsupported target")


def _request(app: FastAPI, method: str, path: str, body: dict[str, object] | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://canvas-application.test",
        ) as client:
            return await client.request(method, path, json=body)

    return asyncio.run(send())


def _authenticated_put(
    system,
    path: str,
    body: dict[str, object],
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=admission_fixture.BASE_URL,
        ) as client:
            return await client.put(
                path,
                headers={"Authorization": f"Bearer {system.token_for()}"},
                json=body,
            )

    return asyncio.run(send())


def _put(
    harness: _DatabaseHarness,
    result: _CanonicalResult,
    *,
    wrong_path: bool = False,
    application_wire: dict[str, object] | None = None,
) -> httpx.Response:
    harness.trace.active = True
    try:
        return _request(
            harness.app,
            "PUT",
            _put_path(result, wrong_path=wrong_path),
            {
                "generation_application": (
                    _wire(result) if application_wire is None else application_wire
                )
            },
        )
    finally:
        harness.trace.active = False


def _application_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(ApplicationRecordRow.__table__)).scalar_one()


def _asset_image(engine: Engine, result: _CanonicalResult) -> str | None:
    table = {
        "character": characters,
        "scene": scenes,
        "prop": props,
    }[result.target.asset_type]
    with engine.connect() as connection:
        return connection.execute(
            select(table.c.image_url).where(table.c.id == result.target.asset_id)
        ).scalar_one()


def _chapter_state(engine: Engine) -> tuple[str, list[dict[str, object]]]:
    with engine.connect() as connection:
        raw = connection.execute(
            select(chapters.c.content).where(chapters.c.id == _CHAPTER_ID)
        ).scalar_one()
        states = connection.execute(
            select(storyboard_media_states).where(
                storyboard_media_states.c.chapter_id == _CHAPTER_ID
            ).order_by(storyboard_media_states.c.id)
        ).mappings().all()
    return raw, [dict(row) for row in states]


def _chapter_title(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            select(chapters.c.title).where(chapters.c.id == _CHAPTER_ID)
        ).scalar_one()


def _lock_rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(chapter_locks).where(chapter_locks.c.chapter_id == _CHAPTER_ID)
            ).mappings()
        ]


def _notes_row(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            select(personal_production_notes).where(
                personal_production_notes.c.chapter_id == _CHAPTER_ID
            )
        ).mappings().one()
    return dict(row)


@pytest.mark.parametrize(
    "target_key",
    ("no_id", "stable", "character", "scene", "prop"),
)
def test_real_put_applies_each_producer_target_in_one_sqlite_session_and_commit(
    canonical_results: dict[str, _CanonicalResult],
    tmp_path: Path,
    target_key: str,
) -> None:
    result = canonical_results[target_key]
    source_path = _canonical_database_path(canonical_results)
    harness = _database_harness(source_path, tmp_path)
    try:
        response = _put(harness, result)

        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"task_id", "result_ref", "status", "application"}
        assert payload["task_id"] == result.task_id
        assert payload["result_ref"] == result.result_ref
        assert payload["status"] == "applied"
        assert payload["application"]["application_ref"]
        assert len(payload["application"]["target_revision"]) == 64
        assert len(harness.sessions) == 1
        request_session = harness.sessions[0]
        assert request_session.info["commit_calls"] == 1
        assert request_session.info["close_calls"] == 1
        assert _application_count(harness.engine) == 1
        statements = [
            " ".join(statement.lower().split())
            for statement in harness.trace.statements or []
        ]
        assert "begin immediate" in statements
        begin_index = statements.index("begin immediate")
        user_index = next(i for i, sql in enumerate(statements) if " from users" in sql)
        task_index = next(i for i, sql in enumerate(statements) if " from ai_tasks" in sql)
        unit_index = next(i for i, sql in enumerate(statements) if " from billing_units" in sql)
        chapter_index = next(i for i, sql in enumerate(statements) if " from chapters" in sql)
        result_reads = [
            i for i, sql in enumerate(statements)
            if " from generation_result_receipts" in sql
        ]
        assert begin_index < user_index < task_index < unit_index
        assert any(unit_index < index < chapter_index for index in result_reads)
        assert len(set(harness.trace.connection_ids or [])) == 1

        if result.target.kind == "canvas_frame_preview":
            raw, states = _chapter_state(harness.engine)
            frames = json.loads(raw)
            frame_index = result.target.compat_frame_index
            assert frames[frame_index]["preview"] == _OUTPUT_URL
            if target_key == "stable":
                assert len(states) == 1
                assert states[0]["media_revision"] == 2
                assert states[0]["preview_digest"]
                note = _notes_row(harness.engine)
                assert note["revision"] == 2
                assert note["frame_notes"]["joint-storyboard-c730"]["status"] == "unmarked"
                with Session(harness.engine, expire_on_commit=False) as reader:
                    record = reader.execute(
                        select(ApplicationRecordRow).where(
                            ApplicationRecordRow.result_ref == result.result_ref
                        )
                    ).scalar_one()
                assert record.media_revision_before == 1
                assert record.media_revision_after == 2
                assert record.notes_revision_before == 1
                assert record.notes_revision_after == 2
        else:
            assert _asset_image(harness.engine, result) == _OUTPUT_URL
            with harness.engine.connect() as connection:
                if target_key == "character":
                    row = connection.execute(
                        select(characters.c.name, characters.c.description).where(
                            characters.c.id == result.target.asset_id
                        )
                    ).one()
                    assert row == ("Joint Character", "Fixture description for joint-character-1")
                elif target_key == "scene":
                    row = connection.execute(
                        select(scenes.c.title, scenes.c.description).where(
                            scenes.c.id == result.target.asset_id
                        )
                    ).one()
                    assert row == ("Joint Scene", "Fixture description for joint-scene-1")
                else:
                    row = connection.execute(
                        select(props.c.name, props.c.description).where(
                            props.c.id == result.target.asset_id
                        )
                    ).one()
                    assert row == ("Joint prop", "Original prop description")
        assert len(_lock_rows(harness.engine)) == 1
        refreshed_lock = _lock_rows(harness.engine)[0]
        assert refreshed_lock["last_active_at"] > _STAMP
        assert refreshed_lock["expires_at"] < _LOCK_EXPIRY
    finally:
        harness.engine.dispose()


def test_exact_replay_returns_original_proof_without_refresh_after_source_edit_and_lock_expiry(
    canonical_results: dict[str, _CanonicalResult],
    tmp_path: Path,
) -> None:
    result = canonical_results["stable"]
    harness = _database_harness(_canonical_database_path(canonical_results), tmp_path)
    try:
        first = _put(harness, result)
        assert first.status_code == 200, first.text
        original_payload = first.json()
        original_lock = _lock_rows(harness.engine)[0]
        assert original_lock["last_active_at"] > _STAMP
        edited_frames = json.loads(_chapter_state(harness.engine)[0])
        edited_frames[1]["text"] = "manual source edit after the committed application"
        edited_content = json.dumps(edited_frames, ensure_ascii=False, separators=(",", ":"))
        with harness.engine.begin() as connection:
            connection.execute(
                update(chapters).where(chapters.c.id == _CHAPTER_ID).values(content=edited_content)
            )
            connection.execute(
                update(chapter_locks)
                .where(chapter_locks.c.chapter_id == _CHAPTER_ID)
                .values(expires_at=datetime(2000, 1, 1))
            )
        harness.trace.statements = []
        harness.trace.connection_ids = []

        replay = _put(harness, result)

        assert replay.status_code == 200, replay.text
        assert replay.json() == {
            **original_payload,
            "status": "already_applied",
        }
        assert _chapter_state(harness.engine)[0] == edited_content
        assert _application_count(harness.engine) == 1
        assert _lock_rows(harness.engine)[0]["expires_at"] == datetime(2000, 1, 1)
        replay_session = harness.sessions[-1]
        assert replay_session.info["commit_calls"] == 0
        assert replay_session.info["rollback_calls"] == 1
        assert not any(
            sql.lstrip().lower().startswith(("insert ", "update ", "delete "))
            for sql in harness.trace.statements or []
        )
        assert original_lock["expires_at"] < _LOCK_EXPIRY
    finally:
        harness.engine.dispose()


@pytest.mark.parametrize("lock_case", ("missing", "expired", "foreign", "duplicate"))
def test_invalid_team_chapter_lock_rolls_back_source_and_application_record(
    canonical_results: dict[str, _CanonicalResult],
    tmp_path: Path,
    lock_case: str,
) -> None:
    result = canonical_results["stable"]
    harness = _database_harness(_canonical_database_path(canonical_results), tmp_path)
    try:
        if lock_case == "missing":
            with harness.engine.begin() as connection:
                connection.execute(delete(chapter_locks).where(chapter_locks.c.chapter_id == _CHAPTER_ID))
        elif lock_case == "expired":
            with harness.engine.begin() as connection:
                connection.execute(
                    update(chapter_locks)
                    .where(chapter_locks.c.chapter_id == _CHAPTER_ID)
                    .values(expires_at=datetime(2000, 1, 1))
                )
        elif lock_case == "foreign":
            with harness.engine.begin() as connection:
                connection.execute(
                    insert(users).values(
                        id="c730-other-user",
                        username="c730-other-user",
                        email="c730-other-user@example.test",
                        hashed_password="fixture-only-hash",
                        is_superuser=False,
                        membership_type="premium",
                        membership_expires_at=None,
                        created_at=_STAMP,
                    )
                )
                connection.execute(
                    update(chapter_locks)
                    .where(chapter_locks.c.chapter_id == _CHAPTER_ID)
                    .values(user_id="c730-other-user")
                )
        else:
            _replace_lock_table_with_duplicate_rows(harness.engine)
        original_chapter = _chapter_state(harness.engine)
        original_notes = _notes_row(harness.engine)
        original_locks = _lock_rows(harness.engine)

        response = _put(harness, result)

        assert response.status_code == 409, response.text
        assert _chapter_state(harness.engine) == original_chapter
        assert _notes_row(harness.engine) == original_notes
        assert _application_count(harness.engine) == 0
        assert _lock_rows(harness.engine) == original_locks
        assert harness.sessions[0].info["rollback_calls"] >= 1
        assert any(
            sql.lstrip().lower().startswith(("insert ", "update ", "delete "))
            for sql in harness.trace.statements or []
        ), "the invalid lock must be detected after the real P5 writer stages its changes"
    finally:
        harness.engine.dispose()


def _replace_lock_table_with_duplicate_rows(engine: Engine) -> None:
    existing = _lock_rows(engine)[0]
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE chapter_locks")
        connection.exec_driver_sql(
            "CREATE TABLE chapter_locks ("
            "id VARCHAR(36) PRIMARY KEY, chapter_id VARCHAR(36) NOT NULL, "
            "user_id VARCHAR(36) NOT NULL, username VARCHAR(100) NOT NULL, "
            "acquired_at DATETIME NOT NULL, last_active_at DATETIME NOT NULL, "
            "expires_at DATETIME NOT NULL)"
        )
        connection.execute(insert(chapter_locks).values(**existing))
        duplicate = dict(existing)
        duplicate["id"] = "c730-lock-duplicate"
        connection.execute(insert(chapter_locks).values(**duplicate))


@pytest.mark.parametrize("failure_point", ("after_p5_lock_refresh", "before_commit"))
def test_late_application_failure_rolls_back_source_media_notes_and_record(
    canonical_results: dict[str, _CanonicalResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    result = canonical_results["stable"]
    harness = _database_harness(
        _canonical_database_path(canonical_results),
        tmp_path,
        commit_failure="before" if failure_point == "before_commit" else None,
    )
    before_chapter, before_states = _chapter_state(harness.engine)
    before_notes = _notes_row(harness.engine)
    before_lock = _lock_rows(harness.engine)
    try:
        if failure_point == "after_p5_lock_refresh":
            def fail_lock_refresh(*_args, **_kwargs):
                raise TaskObservationConflict("synthetic current team lock refresh failure")

            monkeypatch.setattr(
                canvas_application,
                "_refresh_existing_team_chapter_lock",
                fail_lock_refresh,
            )

        response = _put(harness, result)

        assert response.status_code == (409 if failure_point == "after_p5_lock_refresh" else 500)
        assert _chapter_state(harness.engine) == (before_chapter, before_states)
        assert _notes_row(harness.engine) == before_notes
        assert _lock_rows(harness.engine) == before_lock
        assert _application_count(harness.engine) == 0
        assert harness.sessions[0].info["rollback_calls"] >= 1
        assert any(
            sql.lstrip().lower().startswith(("insert ", "update ", "delete "))
            for sql in harness.trace.statements or []
        ), "failure must occur after the real P5 writer staged its SQLite changes"
    finally:
        harness.engine.dispose()


def test_commit_acknowledgement_loss_returns_500_but_read_only_get_finds_the_committed_proof(
    canonical_results: dict[str, _CanonicalResult],
    tmp_path: Path,
) -> None:
    result = canonical_results["stable"]
    harness = _database_harness(
        _canonical_database_path(canonical_results),
        tmp_path,
        commit_failure="after",
    )
    try:
        response = _put(harness, result)

        assert response.status_code == 500, response.text
        assert "application" not in response.json()
        assert _application_count(harness.engine) == 1
        with Session(harness.engine, expire_on_commit=False) as reader:
            record = reader.execute(
                select(ApplicationRecordRow).where(
                    ApplicationRecordRow.result_ref == result.result_ref
                )
            ).scalar_one()
        committed_at = record.committed_at
        assert committed_at is not None and record.after_target_digest is not None
        if committed_at.tzinfo is None:
            committed_at = committed_at.replace(tzinfo=timezone.utc)
        else:
            committed_at = committed_at.astimezone(timezone.utc)
        expected_application = {
            "application_ref": record.application_ref,
            "target_revision": record.after_target_digest,
            "committed_at": committed_at.isoformat(),
        }
        proof = _request(
            harness.app,
            "GET",
            f"/api/chapters/{_CHAPTER_ID}/canvas-owned-results/{result.result_ref}/application"
            f"?task_id={result.task_id}",
        )
        assert proof.status_code == 200, proof.text
        assert proof.json() == {
            "task_id": result.task_id,
            "result_ref": result.result_ref,
            "status": "applied",
            "application": expected_application,
        }
        assert proof.json()["application"] == expected_application
        assert harness.sessions[0].info["commit_calls"] == 1
        assert harness.sessions[0].info["close_calls"] == 1
    finally:
        harness.engine.dispose()


@pytest.mark.parametrize("target_key", ("no_id", "character"))
def test_create_app_jwt_put_is_strict_and_uses_one_business_session(
    canonical_results: dict[str, _CanonicalResult],
    tmp_path: Path,
    target_key: str,
) -> None:
    result = canonical_results[target_key]
    app_root = tmp_path / "real-create-app"
    app_root.mkdir()
    system = admission_fixture._build_system(app_root)
    database_path = system.engine.url.database
    assert isinstance(database_path, str)
    system.engine.dispose()
    _copy_sqlite_database(_canonical_database_path(canonical_results), Path(database_path))
    try:
        if target_key == "no_id":
            before_chapter = _chapter_state(system.engine)
            before_title = _chapter_title(system.engine)
            mixed_request = _authenticated_put(
                system,
                _put_path(result),
                {
                    "generation_application": _wire(result),
                    "title": "legacy fallback must not run",
                },
            )

            assert mixed_request.status_code == 422, mixed_request.text
            assert _chapter_state(system.engine) == before_chapter
            assert _chapter_title(system.engine) == before_title
            assert system.business_sessions == []

        response = _authenticated_put(
            system,
            _put_path(result),
            {"generation_application": _wire(result)},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"task_id", "result_ref", "status", "application"}
        assert payload["task_id"] == result.task_id
        assert payload["result_ref"] == result.result_ref
        assert payload["status"] == "applied"
        assert payload["application"]["application_ref"]
        assert len(payload["application"]["target_revision"]) == 64
        assert system.auth_sessions
        assert all(session.info.get("observed_closed") for session in system.auth_sessions)
        assert len(system.business_sessions) == 1
        business_session = system.business_sessions[0]
        assert business_session.info.get("commit_calls") == 1
        assert business_session.info.get("close_count") == 1
        assert business_session.info.get("dml_execute_calls", 0) > 0
        assert _application_count(system.engine) == 1
        if result.target.kind == "canvas_asset_image":
            assert _asset_image(system.engine, result) == _OUTPUT_URL
        else:
            raw, _states = _chapter_state(system.engine)
            assert json.loads(raw)[0]["preview"] == _OUTPUT_URL
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    (
        ("foreign_owner", 404),
        ("unsupported_operation", 409),
        ("wrong_path", 409),
        ("stale_no_id", 409),
        ("stale_asset_target", 409),
        ("duplicate_stable_identity", 409),
    ),
)
def test_canonical_owner_path_and_live_target_guards_reject_without_writes(
    canonical_results: dict[str, _CanonicalResult],
    tmp_path: Path,
    scenario: str,
    expected_status: int,
) -> None:
    result_key = (
        "no_id"
        if scenario == "stale_no_id"
        else "stable"
        if scenario == "duplicate_stable_identity"
        else "character"
    )
    result = canonical_results[result_key]
    actor_id = "c730-not-owner" if scenario == "foreign_owner" else _ACTOR_ID
    harness = _database_harness(
        _canonical_database_path(canonical_results), tmp_path, actor_id=actor_id
    )
    try:
        application_wire = None
        if scenario == "foreign_owner":
            with harness.engine.begin() as connection:
                connection.execute(
                    insert(users).values(
                        id=actor_id,
                        username=actor_id,
                        email=f"{actor_id}@example.test",
                        hashed_password="fixture-only-hash",
                        is_superuser=False,
                        membership_type="premium",
                        membership_expires_at=None,
                        created_at=_STAMP,
                    )
                )
        elif scenario == "unsupported_operation":
            video_task_id, video_result_ref = _insert_targetless_video_receipt(
                harness.engine, result
            )
            with Session(harness.engine, expire_on_commit=False) as reader:
                loaded = GenerationResultApplicationFacade(reader).load_owned_completed_result(
                    reader,
                    GenerationActor(_ACTOR_ID, TrustedActor(_ACTOR_ID)),
                    video_task_id,
                    video_result_ref,
                )
                assert isinstance(loaded, GenerationResultReceipt)
                assert loaded.operation == "video.single"
                reader.rollback()
            wire = _wire(result)
            wire["task_id"] = video_task_id
            wire["result_ref"] = video_result_ref
            wire["application_key"] = application_key_for(
                video_result_ref,
                target_scope_for_snapshot(result.target),
            )
            application_wire = wire
        if scenario == "stale_no_id":
            frames = json.loads(_chapter_state(harness.engine)[0])
            frames[0]["text"] = "changed after canonical admission"
            with harness.engine.begin() as connection:
                connection.execute(
                    update(chapters)
                    .where(chapters.c.id == _CHAPTER_ID)
                    .values(content=json.dumps(frames, ensure_ascii=False, separators=(",", ":")))
                )
        elif scenario == "stale_asset_target":
            with harness.engine.begin() as connection:
                connection.execute(
                    update(characters)
                    .where(characters.c.id == result.target.asset_id)
                    .values(description="manual edit after canonical admission")
                )
        elif scenario == "duplicate_stable_identity":
            frames = json.loads(_chapter_state(harness.engine)[0])
            frames.append({"text": "duplicate stable id", "storyboard": ["joint-storyboard-c730"]})
            with harness.engine.begin() as connection:
                connection.execute(
                    update(chapters)
                    .where(chapters.c.id == _CHAPTER_ID)
                    .values(content=json.dumps(frames, ensure_ascii=False, separators=(",", ":")))
                )
        original_chapter = _chapter_state(harness.engine)[0]
        original_asset = _asset_image(harness.engine, result) if result.target.kind == "canvas_asset_image" else None

        response = _put(
            harness,
            result,
            wrong_path=scenario == "wrong_path",
            application_wire=application_wire,
        )

        assert response.status_code == expected_status, response.text
        if scenario == "unsupported_operation":
            assert response.json() == {"detail": "该生成结果不适用于画布图像目标"}
        assert _application_count(harness.engine) == 0
        if result.target.kind == "canvas_asset_image":
            assert _asset_image(harness.engine, result) == original_asset
        else:
            assert _chapter_state(harness.engine)[0] == original_chapter
    finally:
        harness.engine.dispose()


def _canonical_database_path(
    canonical_results: dict[str, _CanonicalResult],
) -> str:
    if not isinstance(_CANONICAL_DATABASE_PATH, str):
        raise AssertionError("canonical SQLite fixture path was not registered")
    assert len(canonical_results) == 5
    return _CANONICAL_DATABASE_PATH


def _insert_targetless_video_receipt(
    engine: Engine,
    result: _CanonicalResult,
) -> tuple[str, str]:
    """Stage a synthetic lower-P5 video receipt; this is not a Canvas video producer."""

    with Session(engine, expire_on_commit=False) as session:
        source_receipt = session.get(GenerationResultReceiptRow, result.result_ref)
        assert source_receipt is not None
        source_task = session.get(AITaskRow, result.task_id)
        source_unit = session.get(BillingUnitRow, source_receipt.unit_id)
        source_step = session.get(ExecutionStepRow, source_receipt.step_id)
        source_submission = session.get(ExternalSubmissionRow, source_receipt.submission_id)
        source_evidence = session.get(ResultEvidenceRow, source_receipt.evidence_id)
        assert source_task is not None
        assert source_unit is not None
        assert source_step is not None
        assert source_submission is not None
        assert source_evidence is not None

        task_id = str(uuid4())
        unit_id = str(uuid4())
        step_id = str(uuid4())
        submission_id = str(uuid4())
        evidence_id = str(uuid4())
        result_ref = str(uuid4())
        identifiers = {
            AITaskRow: (source_task, task_id),
            BillingUnitRow: (source_unit, unit_id),
            ExecutionStepRow: (source_step, step_id),
            ExternalSubmissionRow: (source_submission, submission_id),
            ResultEvidenceRow: (source_evidence, evidence_id),
        }
        row_values: dict[type, dict[str, object]] = {}
        for model, (source, new_id) in identifiers.items():
            values = {
                column.name: getattr(source, column.name)
                for column in model.__table__.columns
            }
            values["id"] = new_id
            row_values[model] = values

        row_values[AITaskRow].update(
            type="video-single",
            status="completed",
            request_data="{}",
        )
        row_values[BillingUnitRow].update(
            id=unit_id,
            task_id=task_id,
            operation="video.single",
            step_graph_version="video-single-v1",
            quote_id=None,
            quote_unit_key=None,
            result_status="succeeded",
        )
        row_values[ExecutionStepRow].update(
            id=step_id,
            billing_unit_id=unit_id,
            input_snapshot={},
            execution_status="result_observed",
        )
        row_values[ExternalSubmissionRow].update(
            id=submission_id,
            step_id=step_id,
            submission_key=f"synthetic-video-{submission_id}",
            request_snapshot={},
            submission_status="succeeded",
        )
        row_values[ResultEvidenceRow].update(
            id=evidence_id,
            step_id=step_id,
            submission_id=submission_id,
            evidence_key=f"synthetic-video-evidence-{evidence_id}",
            evidence_kind="video",
            result_digest="e" * 64,
            validity="valid",
            application_status="pending",
        )
        for model, values in row_values.items():
            session.execute(insert(model.__table__).values(**values))

        video_receipt = GenerationResultReceipt(
            result_ref=result_ref,
            owner_id=_ACTOR_ID,
            task_id=task_id,
            unit_id=unit_id,
            operation="video.single",
            step_id=step_id,
            submission_id=submission_id,
            evidence_id=evidence_id,
            selected_attempt_id="synthetic-targetless-video-attempt",
            recoverable_locator="https://media.example.test/synthetic/video.mp4",
            content_digest="e" * 64,
            validation_version="synthetic-targetless-video-v1",
            validated_at=_STAMP,
            generation_fulfilled_at=_STAMP,
            late_disposition=None,
            policy_stop_disposition=None,
            automatic_application_allowed=True,
        )
        written = GenerationRecordRepository(session).stage_record(video_receipt)
        assert written.status == "created", written.detail
        session.commit()
    return task_id, result_ref
