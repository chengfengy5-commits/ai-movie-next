"""Authenticated previews for images bound to canonical Canvas receipts."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, insert, inspect, select, update
from sqlalchemy.exc import SQLAlchemyError

from haoai_backend.asset_data.tables import (
    characters as asset_characters,
    props as asset_props,
    scenes as asset_scenes,
    storyboard_assets as asset_storyboard_assets,
)
from haoai_backend.authentication.tables import user_sessions, users
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.generation_catalog.admission_types import AdmissionOperation
from haoai_backend.generation_catalog.tables import ASSET_TABLES as CATALOG_ASSET_TABLES
from haoai_backend.generation_core.base_schema import AITaskRow, ResultEvidenceRow
from haoai_backend.generation_core.schema import GenerationResultReceiptRow
from haoai_backend.generation_contracts.types import StoredMedia
from haoai_backend.generation_runtime.assembly import (
    GenerationWorkerConfiguration,
    build_generation_worker_runtime,
)
from haoai_backend.runtime_status.ports import (
    PublicImageFetchResult,
    RemoteContentTooLarge,
    RemoteContentTypeRejected,
    RemoteFetchError,
    UnsafeRemoteURL,
)

_TEST_DIRECTORIES = (
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parent / "generation_core",
    Path(__file__).resolve().parent / "generation_execution",
)
_ADDED_TEST_PATHS = [
    str(directory) for directory in _TEST_DIRECTORIES if str(directory) not in sys.path
]
sys.path[:0] = _ADDED_TEST_PATHS
try:
    import test_app_chat_media_admission_integration as admission_fixture
finally:
    for _path in _ADDED_TEST_PATHS:
        sys.path.remove(_path)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n"
_ASSET_DATA_PROJECTIONS = (
    asset_characters,
    asset_scenes,
    asset_props,
    asset_storyboard_assets,
)


class _FixedImageFetcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.results: list[bool] = []
        self.result = PublicImageFetchResult(_PNG_BYTES, "image/png")
        self.failure: Exception | None = None
        self.session_closed_check: Callable[[], bool] | None = None

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: int,
        allowed_content_prefixes: Sequence[str],
    ) -> PublicImageFetchResult:
        self.results.append(
            self.session_closed_check() if self.session_closed_check is not None else False
        )
        self.calls.append({
            "url": url,
            "max_bytes": max_bytes,
            "timeout_seconds": timeout_seconds,
            "allowed_content_prefixes": allowed_content_prefixes,
        })
        if self.failure is not None:
            raise self.failure
        return self.result


def _build_preview_system(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetcher: _FixedImageFetcher,
    *,
    with_configuration: bool = True,
    fail_rollback: bool = False,
):
    original_create_app = admission_fixture.create_app

    def create_app_with_fixture_fetcher(**kwargs):
        kwargs["runtime_status_image_fetcher"] = fetcher
        if fail_rollback:
            session_factory = kwargs["session_factory"]

            def rollback_failure_factory():
                session = session_factory()
                if session.info.get("kind") == "business":

                    def fail_rollback() -> None:
                        session.info["rollback_failure_seen"] = True
                        raise SQLAlchemyError("synthetic preview cleanup failure")

                    session.rollback = fail_rollback
                return session

            kwargs["session_factory"] = rollback_failure_factory
        return original_create_app(**kwargs)

    monkeypatch.setattr(
        admission_fixture,
        "create_app",
        create_app_with_fixture_fetcher,
    )
    monkeypatch.setattr(
        admission_fixture.admission_staging_module,
        "_G2_JOINT_PROVIDER",
        "openai",
    )
    system = admission_fixture._build_system(tmp_path, with_configuration=with_configuration)

    def read_session_released_before_fetch() -> bool:
        if not system.business_sessions:
            return False
        session_info = system.business_sessions[-1].info
        return (
            session_info.get("rollback_calls") == 1
            and session_info.get("observed_closed") is True
            and session_info.get("dml_execute_calls", 0) == 0
            and session_info.get("flush_calls", 0) == 0
            and session_info.get("commit_calls", 0) == 0
        )

    fetcher.session_closed_check = read_session_released_before_fetch
    return system


def _target_for_case(target_kind: str) -> tuple[list[dict[str, object]], dict[str, object]]:
    if target_kind == "no_id":
        frames = admission_fixture._NO_ID_CANVAS_FRAMES
        destination: dict[str, object] = {
            "kind": "frame_preview",
            "chapter_id": "joint-chapter-1",
            "identity_mode": "strict-no-id-v1",
            "frame_index": 1,
            "expected_chapter_digest": admission_fixture._canonical_json_digest(frames),
        }
        return frames, destination
    if target_kind == "stable_frame":
        return [
            {"text": "Stable preview frame", "storyboard": ["preview-storyboard-1"]}
        ], {
            "kind": "frame_preview",
            "chapter_id": "joint-chapter-1",
            "storyboard_asset_id": "preview-storyboard-1",
        }
    if target_kind in {"character", "scene", "prop"}:
        asset_ids = {
            "character": "joint-character-1",
            "scene": "joint-scene-1",
            "prop": "joint-prop-1",
        }
        return admission_fixture._NO_ID_CANVAS_FRAMES, {
            "kind": "asset_image",
            "chapter_id": "joint-chapter-1",
            "asset_type": target_kind,
            "asset_id": asset_ids[target_kind],
        }
    raise AssertionError(f"unsupported image target fixture: {target_kind}")


def _ensure_asset_data_projection_columns(engine) -> None:
    """Expand only this disposable SQLite fixture to the production read projection."""

    if engine.dialect.name != "sqlite":
        raise AssertionError("the preview producer fixture must use isolated SQLite")
    identifier = engine.dialect.identifier_preparer.quote
    for table in _ASSET_DATA_PROJECTIONS:
        existing = {
            column["name"] for column in inspect(engine).get_columns(table.name)
        }
        for column in table.columns:
            if column.name in existing:
                continue
            type_sql = column.type.compile(dialect=engine.dialect)
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    f"ALTER TABLE {identifier(table.name)} "
                    f"ADD COLUMN {identifier(column.name)} {type_sql}"
                )
            existing.add(column.name)


def _seed_completed_canvas_result(
    system,
    target_kind: str = "no_id",
    *,
    raw_key: str = "canvas-owned-image-preview",
) -> tuple[str, str, StoredMedia]:
    frames, destination = _target_for_case(target_kind)
    raw_chapter = json.dumps(frames, ensure_ascii=False, separators=(",", ":"))
    _ensure_asset_data_projection_columns(system.engine)
    columns = {
        column["name"]
        for column in inspect(system.engine).get_columns("storyboard_assets")
    }
    with system.engine.begin() as connection:
        connection.execute(
            update(admission_fixture.catalog_chapters)
            .where(admission_fixture.catalog_chapters.c.id == "joint-chapter-1")
            .values(content=raw_chapter)
        )
        if target_kind == "stable_frame":
            stamp = admission_fixture._G2_JOINT_NOW.replace(tzinfo=None)
            values: dict[str, object] = {
                "id": "preview-storyboard-1",
                "series_id": "joint-series-1",
                "chapter_id": "joint-chapter-1",
                "frame_index": 0,
                "name": "Preview storyboard",
                "description": "Synthetic test asset",
                "image_url": None,
                "updated_at": stamp,
            }
            if "created_at" in columns:
                values["created_at"] = stamp
            connection.execute(insert(asset_storyboard_assets).values(**values))
        elif target_kind in {"character", "scene", "prop"}:
            asset_table = CATALOG_ASSET_TABLES[target_kind]
            asset_id = str(destination["asset_id"])
            asset_values: dict[str, object] = {
                "image_url": f"https://assets.example.test/{target_kind}.png",
                "aliases": '["fixture-alias"]',
                "canonical_key": f"fixture-{target_kind}",
            }
            if target_kind == "character":
                asset_values.update({
                    "gender": "female",
                    "age": "28",
                    "role": "lead",
                    "appearance": "dark coat",
                    "audio_url": "https://assets.example.test/voice.mp3",
                    "voice_ref": "voice-fixture",
                })
            if target_kind == "prop":
                connection.execute(insert(asset_table).values(
                    id=asset_id,
                    series_id="joint-series-1",
                    name="Joint Prop",
                    description="Fixture prop description",
                    image_url=asset_values["image_url"],
                    aliases=asset_values["aliases"],
                    canonical_key=asset_values["canonical_key"],
                    updated_at=admission_fixture._G2_JOINT_NOW.replace(tzinfo=None),
                ))
            else:
                connection.execute(
                    update(asset_table)
                    .where(asset_table.c.id == asset_id)
                    .values(**asset_values)
                )
    admission_fixture.notes_metadata.create_all(
        system.engine,
        tables=[
            admission_fixture.storyboard_media_states,
            admission_fixture.personal_production_notes,
        ],
    )

    authentication, _authentication_sessions = admission_fixture._joint_authentication_service(
        system.engine
    )
    binding = admission_fixture._joint_build_binding(
        authentication,
        system.capabilities,
        admission_fixture._JointIdFactory("owned-image-preview-server"),
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )
    request = admission_fixture._joint_request(AdmissionOperation.IMAGE_SINGLE.value)
    request["reference_images"] = []
    request["target"] = {
        "kind": "canvas_apply",
        "destination": destination,
    }
    admission_caller = admission_fixture._joint_caller(system.engine)
    try:
        staged = admission_fixture._joint_prepare_and_stage(
            binding,
            admission_caller,
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            raw_key=raw_key,
            request=request,
        )
        assert isinstance(staged, admission_fixture.AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        admission_caller.commit()
    finally:
        admission_caller.close()

    worker_environment = SimpleNamespace(
        engine=system.engine,
        capabilities=system.capabilities,
    )
    stored_media: list[StoredMedia] = []
    runtime_config, _provider_calls = admission_fixture._runtime_configuration(
        worker_environment,
        admission_fixture._provider_body(),
        stored_media,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, system.capabilities)
    )
    outcome = runtime.run_claimed_once("image-single")
    assert outcome is not None
    assert outcome != "pending"
    assert outcome != "ownership_lost"
    assert outcome.result_ref
    assert len(stored_media) == 1
    assert stored_media[0].content_digest == hashlib.sha256(_PNG_BYTES).hexdigest()
    return task_id, outcome.result_ref, stored_media[0]


def _result_path(
    task_id: str,
    result_ref: str,
    *,
    chapter_id: str = "joint-chapter-1",
) -> str:
    return (
        f"/api/chapters/{chapter_id}/canvas-owned-results/{result_ref}/image"
        f"?task_id={task_id}"
    )


def _assert_last_preview_read_was_read_only(system) -> None:
    assert len(system.business_sessions) == 1
    session = system.business_sessions[0]
    assert session.info.get("dml_execute_calls", 0) == 0
    assert session.info.get("flush_calls", 0) == 0
    assert session.info.get("commit_calls", 0) == 0
    assert session.info.get("rollback_calls") == 1
    assert session.info.get("close_count") == 1
    assert session.info.get("observed_closed") is True


def _assert_preview_response(
    response,
    fetcher: _FixedImageFetcher,
    stored: StoredMedia,
) -> None:
    assert response.status_code == 200, response.text
    assert response.content == _PNG_BYTES
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0] == {
        "url": stored.locator,
        "max_bytes": 10 * 1024 * 1024,
        "timeout_seconds": 30,
        "allowed_content_prefixes": ("image/png", "image/jpeg", "image/webp"),
    }
    assert fetcher.results == [True], "the business Session must close before external fetch"


@pytest.mark.parametrize(
    "target_kind",
    ("no_id", "stable_frame", "character", "scene", "prop"),
)
def test_real_jwt_previews_each_supported_canvas_target_from_completed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    fetcher = _FixedImageFetcher()
    system = _build_preview_system(tmp_path, monkeypatch, fetcher)
    try:
        task_id, result_ref, stored = _seed_completed_canvas_result(system, target_kind)
        response = admission_fixture._get(system, _result_path(task_id, result_ref))

        _assert_preview_response(response, fetcher, stored)
        _assert_last_preview_read_was_read_only(system)
    finally:
        system.engine.dispose()


@pytest.mark.parametrize("target_kind", ("no_id", "character"))
def test_historical_preview_survives_current_source_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    fetcher = _FixedImageFetcher()
    system = _build_preview_system(tmp_path, monkeypatch, fetcher)
    try:
        task_id, result_ref, stored = _seed_completed_canvas_result(system, target_kind)
        with system.engine.begin() as connection:
            if target_kind == "no_id":
                changed_frames = [
                    {"text": "Changed after generation", "storyboard": [None]}
                ]
                connection.execute(
                    update(admission_fixture.catalog_chapters)
                    .where(admission_fixture.catalog_chapters.c.id == "joint-chapter-1")
                    .values(
                        content=json.dumps(
                            changed_frames,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                )
            else:
                connection.execute(
                    update(CATALOG_ASSET_TABLES[target_kind])
                    .where(CATALOG_ASSET_TABLES[target_kind].c.id == "joint-character-1")
                    .values(image_url="https://assets.example.test/changed.png")
                )

        response = admission_fixture._get(system, _result_path(task_id, result_ref))

        _assert_preview_response(response, fetcher, stored)
        _assert_last_preview_read_was_read_only(system)
    finally:
        system.engine.dispose()


def test_historical_preview_survives_current_asset_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = _FixedImageFetcher()
    system = _build_preview_system(tmp_path, monkeypatch, fetcher)
    try:
        task_id, result_ref, stored = _seed_completed_canvas_result(system, "character")
        with system.engine.begin() as connection:
            connection.execute(
                delete(CATALOG_ASSET_TABLES["character"]).where(
                    CATALOG_ASSET_TABLES["character"].c.id == "joint-character-1"
                )
            )

        response = admission_fixture._get(system, _result_path(task_id, result_ref))

        _assert_preview_response(response, fetcher, stored)
        _assert_last_preview_read_was_read_only(system)
    finally:
        system.engine.dispose()


def test_preview_remains_available_when_generation_is_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = _FixedImageFetcher()
    system = _build_preview_system(
        tmp_path,
        monkeypatch,
        fetcher,
        with_configuration=False,
    )
    try:
        task_id, result_ref, stored = _seed_completed_canvas_result(system, "no_id")
        response = admission_fixture._get(system, _result_path(task_id, result_ref))

        _assert_preview_response(response, fetcher, stored)
        _assert_last_preview_read_was_read_only(system)
    finally:
        system.engine.dispose()


def _create_foreign_user_token(system, *, user_id: str = "preview-foreign-user") -> str:
    stamp = admission_fixture._G2_JOINT_NOW.replace(tzinfo=None)
    with system.engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id=user_id,
                username=user_id,
                email=f"{user_id}@example.test",
                hashed_password="synthetic-only-hash",
                is_superuser=False,
                membership_type="free",
                created_at=stamp,
            )
        )
        connection.execute(
            insert(user_sessions).values(
                id=f"{user_id}-session",
                user_id=user_id,
                jti=f"{user_id}-jti",
                user_agent="synthetic preview request",
                ip_address="127.0.0.1",
                device_name="synthetic test",
                is_active=True,
                created_at=stamp,
                last_seen_at=stamp,
                revoked_at=None,
            )
        )
    return JoseTokenCodec(admission_fixture.SECRET).encode(
        {
            "sub": user_id,
            "jti": f"{user_id}-jti",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )


def _stage_pending_canvas_task(system) -> str:
    authentication, _sessions = admission_fixture._joint_authentication_service(
        system.engine
    )
    binding = admission_fixture._joint_build_binding(
        authentication,
        system.capabilities,
        admission_fixture._JointIdFactory("owned-image-preview-pending"),
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )
    request = admission_fixture._joint_request(AdmissionOperation.IMAGE_SINGLE.value)
    request["reference_images"] = []
    _frames, destination = _target_for_case("no_id")
    request["target"] = {"kind": "canvas_apply", "destination": destination}
    caller = admission_fixture._joint_caller(system.engine)
    try:
        staged = admission_fixture._joint_prepare_and_stage(
            binding,
            caller,
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            raw_key="owned-image-preview-pending-task",
            request=request,
        )
        assert isinstance(staged, admission_fixture.AdmissionStaged)
        caller.commit()
        return staged.ordered_tasks[0].task_id
    finally:
        caller.close()


@pytest.mark.parametrize(
    ("query", "expected_status"),
    (
        ("task_id=valid&extra=ignored", 422),
        ("task_id=valid&task_id=duplicate", 422),
        ("", 422),
    ),
)
def test_preview_requires_exactly_one_task_id_after_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_status: int,
) -> None:
    fetcher = _FixedImageFetcher()
    system = _build_preview_system(tmp_path, monkeypatch, fetcher)
    try:
        path = "/api/chapters/joint-chapter-1/canvas-owned-results/result-ref/image"
        if query:
            path = f"{path}?{query}"

        unauthorized = admission_fixture._get(system, path, token="not-a-valid-jwt")
        assert unauthorized.status_code == 401
        assert system.business_sessions == []

        response = admission_fixture._get(system, path)

        assert response.status_code == expected_status, response.text
        assert system.business_sessions == []
        assert fetcher.calls == []
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("case", "expected_status"),
    (
        ("missing_result", 404),
        ("foreign_owner", 404),
        ("wrong_chapter", 404),
        ("revoked_access", 403),
        ("pending_task", 409),
        ("invalid_evidence", 409),
        ("changed_original_target", 409),
    ),
)
def test_preview_rejects_unowned_or_unready_canonical_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_status: int,
) -> None:
    fetcher = _FixedImageFetcher()
    system = _build_preview_system(tmp_path, monkeypatch, fetcher)
    try:
        task_id, result_ref, _stored = _seed_completed_canvas_result(system, "no_id")
        token = None
        chapter_id = "joint-chapter-1"
        if case == "missing_result":
            result_ref = "missing-result-ref"
        elif case == "foreign_owner":
            token = _create_foreign_user_token(system)
        elif case == "wrong_chapter":
            chapter_id = "other-chapter-1"
        elif case == "revoked_access":
            with system.engine.begin() as connection:
                connection.execute(
                    update(admission_fixture.catalog_series)
                    .where(admission_fixture.catalog_series.c.id == "joint-series-1")
                    .values(user_id="revoked-owner-id", team_id=None, claimed_by=None)
                )
        elif case == "pending_task":
            task_id = _stage_pending_canvas_task(system)
        elif case == "invalid_evidence":
            with system.engine.begin() as connection:
                evidence_id = connection.execute(
                    select(GenerationResultReceiptRow.evidence_id).where(
                        GenerationResultReceiptRow.result_ref == result_ref
                    )
                ).scalar_one()
                connection.execute(
                    update(ResultEvidenceRow)
                    .where(ResultEvidenceRow.id == evidence_id)
                    .values(validity="invalid")
                )
        elif case == "changed_original_target":
            with system.engine.begin() as connection:
                request_data = connection.execute(
                    select(AITaskRow.request_data).where(AITaskRow.id == task_id)
                ).scalar_one()
                envelope = json.loads(request_data)
                envelope["target_snapshot"]["full_chapter_digest"] = "0" * 64
                connection.execute(
                    update(AITaskRow)
                    .where(AITaskRow.id == task_id)
                    .values(request_data=json.dumps(envelope, separators=(",", ":")))
                )

        path = _result_path(task_id, result_ref, chapter_id=chapter_id)
        response = admission_fixture._get(system, path, token=token)

        assert response.status_code == expected_status, response.text
        assert fetcher.calls == []
        assert response.headers.get("cache-control") is None
        _assert_last_preview_read_was_read_only(system)
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("failure", "result", "expected_status"),
    (
        (TimeoutError("synthetic timeout"), None, 502),
        (UnsafeRemoteURL("https://private.example.test/image.png"), None, 502),
        (RemoteFetchError("https://media.example.test/private-token"), None, 502),
        (RemoteContentTooLarge("too large"), None, 413),
        (RemoteContentTypeRejected("text/html"), None, 415),
        (None, PublicImageFetchResult(_PNG_BYTES, "image/svg+xml"), 415),
        (None, PublicImageFetchResult(b"x" * (10 * 1024 * 1024 + 1), "image/png"), 413),
        (None, PublicImageFetchResult(b"", "image/png"), 502),
        (None, PublicImageFetchResult(b"different bytes", "image/png"), 502),
    ),
)
def test_preview_bounds_and_validates_fetched_bytes_without_leaking_locator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception | None,
    result: PublicImageFetchResult | None,
    expected_status: int,
) -> None:
    fetcher = _FixedImageFetcher()
    fetcher.failure = failure
    if result is not None:
        fetcher.result = result
    system = _build_preview_system(tmp_path, monkeypatch, fetcher)
    try:
        task_id, result_ref, stored = _seed_completed_canvas_result(system, "no_id")
        response = admission_fixture._get(system, _result_path(task_id, result_ref))

        assert response.status_code == expected_status, response.text
        assert len(fetcher.calls) == 1
        assert fetcher.results == [True]
        assert stored.locator not in response.text
        assert admission_fixture.SECRET not in response.text
        _assert_last_preview_read_was_read_only(system)
    finally:
        system.engine.dispose()


def test_preview_cleanup_failure_returns_unavailable_without_fetching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = _FixedImageFetcher()
    system = _build_preview_system(
        tmp_path,
        monkeypatch,
        fetcher,
        fail_rollback=True,
    )
    try:
        task_id, result_ref, _stored = _seed_completed_canvas_result(system, "no_id")
        response = admission_fixture._get(system, _result_path(task_id, result_ref))

        assert response.status_code == 503, response.text
        assert fetcher.calls == []
        assert len(system.business_sessions) == 1
        session = system.business_sessions[0]
        assert session.info.get("rollback_failure_seen") is True
        assert session.info.get("observed_closed") is True
        assert session.info.get("close_count") == 1
    finally:
        system.engine.dispose()
