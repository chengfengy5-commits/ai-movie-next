"""Real JWT and SQLite integration for the three chat media admission routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import user_sessions, users
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.chat_media_admission import ChatMediaAdmissionConfiguration
from haoai_backend.generation_catalog.admission import AdmissionPolicy
from haoai_backend.generation_catalog.admission_types import AdmissionOperation
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_catalog.tables import chapters as catalog_chapters
from haoai_backend.generation_catalog.tables import series as catalog_series
from haoai_backend.generation_catalog.tables import system_models
from haoai_backend.generation_core.admission_receipts import (
    AdmissionCommitUnknown,
    AdmissionConfirmed,
    AdmissionStaged,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExternalSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    GenerationResultTargetRow,
)
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    CanvasApplyAuthority,
    GenerationActor,
    GenerationApplicationCommand,
    StoredMedia,
)
from haoai_backend.generation_credentials import snapshot_producer
from haoai_backend.generation_runtime.assembly import (
    GenerationWorkerConfiguration,
    build_generation_worker_runtime,
)
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation import (
    GenerationResultApplicationFacade,
    application_key_for,
    target_scope_for_snapshot,
)
from haoai_backend.task_observation.canvas_result_discovery import (
    backfill_canvas_result_targets,
)
from haoai_backend.task_observation.canvas_result_http import (
    build_canvas_result_http_router,
)
from generation_core.test_admission_staging import (
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_MODEL_ID,
    _G2_JOINT_NOW,
    _JointIdFactory,
    _joint_authentication_service,
    _joint_build_binding,
    _joint_caller,
    _joint_database,
    _joint_prepare_and_stage,
    _joint_registered_capabilities,
    _joint_request,
)
import generation_core.test_admission_staging as admission_staging_module

_TEST_DIRECTORIES = (
    Path(__file__).resolve().parent / "generation_core",
    Path(__file__).resolve().parent / "generation_execution",
)
_ADDED_TEST_PATHS = [
    str(directory) for directory in _TEST_DIRECTORIES if str(directory) not in sys.path
]
sys.path[:0] = _ADDED_TEST_PATHS
try:
    from test_image_single_worker import _provider_body, _runtime_configuration
finally:
    for _path in _ADDED_TEST_PATHS:
        sys.path.remove(_path)

SECRET = "chat-media-admission-integration-synthetic-secret"
JTI = "chat-media-admission-integration-session"
AUTH_SESSION_ID = "chat-media-admission-integration-auth-session"
BASE_URL = "http://chat-media-admission.test"
BusinessCloseGate = tuple[threading.Event, threading.Event, threading.Event]


class _ObservedSession(Session):
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.info["events"].append(
            (f"execute:{self.info['kind']}", threading.get_ident())
        )
        statement = args[0] if args else None
        if getattr(statement, "is_dml", False):
            self.info["dml_execute_calls"] = int(
                self.info.get("dml_execute_calls", 0)
            ) + 1
        return super().execute(*args, **kwargs)

    def flush(self, objects: Any = None) -> None:
        self.info["flush_calls"] = int(self.info.get("flush_calls", 0)) + 1
        super().flush(objects)

    def rollback(self) -> None:
        self.info["rollback_calls"] = int(self.info.get("rollback_calls", 0)) + 1
        super().rollback()

    def close(self) -> None:
        if not self.info.get("observed_closed"):
            self.info["observed_closed"] = True
            self.info["events"].append(
                (f"close:{self.info['kind']}", threading.get_ident())
            )
        close_gate = self.info.pop("close_gate", None)
        if close_gate is not None:
            close_started, close_release = close_gate
            close_started.set()
            close_release.wait(timeout=5)
        super().close()
        self.info["close_count"] = int(self.info.get("close_count", 0)) + 1
        close_finished = self.info.get("close_finished")
        if isinstance(close_finished, threading.Event):
            close_finished.set()

    def commit(self) -> None:
        self.info["commit_calls"] = int(self.info.get("commit_calls", 0)) + 1
        super().commit()
        state = self.info.get("ack_loss_state")
        if isinstance(state, dict) and state.get("enabled") and not state.get("used"):
            state["used"] = True
            raise RuntimeError("synthetic lost commit acknowledgement")


@dataclass
class _System:
    engine: Engine
    app: Any
    capabilities: Any
    events: list[tuple[str, int]]
    auth_sessions: list[_ObservedSession]
    business_sessions: list[_ObservedSession]
    token_for: Callable[..., str]


def _build_system(
    tmp_path: Path,
    *,
    operation_category: str = "image",
    with_configuration: bool = True,
    enabled_operations: frozenset[str] | None = None,
    image_references_enabled: bool | None = None,
    lose_first_business_commit_ack: bool = False,
    business_close_gate: BusinessCloseGate | None = None,
) -> _System:
    engine = _joint_database(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        wallet_credits=500,
    )
    capabilities, _crypto_state = _joint_registered_capabilities(
        engine,
        model_source=ModelSource.SYSTEM,
    )
    events: list[tuple[str, int]] = []
    auth_sessions: list[_ObservedSession] = []
    business_sessions: list[_ObservedSession] = []
    ack_loss_state = {"enabled": lose_first_business_commit_ack, "used": False}

    with engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.id == _G2_JOINT_ACTOR_ID)
            .values(is_superuser=False)
        )
        connection.execute(
            insert(user_sessions).values(
                id=AUTH_SESSION_ID,
                user_id=_G2_JOINT_ACTOR_ID,
                jti=JTI,
                user_agent="local chat media integration",
                ip_address="127.0.0.1",
                device_name="synthetic test",
                is_active=True,
                created_at=_G2_JOINT_NOW.replace(tzinfo=None),
                last_seen_at=_G2_JOINT_NOW.replace(tzinfo=None),
                revoked_at=None,
            )
        )
        if operation_category != "image":
            connection.execute(
                update(system_models)
                .where(system_models.c.id == _G2_JOINT_MODEL_ID)
                .values(category=operation_category)
            )

    def make_factory(
        kind: str,
        bucket: list[_ObservedSession],
        *,
        use_ack_loss: bool = False,
    ) -> Callable[[], _ObservedSession]:
        maker = sessionmaker(
            engine,
            class_=_ObservedSession,
            expire_on_commit=False,
            info={
                "kind": kind,
                "events": events,
                "ack_loss_state": ack_loss_state if use_ack_loss else None,
                "close_gate": (
                    business_close_gate[:2]
                    if kind == "business" and business_close_gate is not None
                    else None
                ),
                "close_finished": (
                    business_close_gate[2]
                    if kind == "business" and business_close_gate is not None
                    else None
                ),
            },
        )

        def factory() -> _ObservedSession:
            events.append((f"open:{kind}", threading.get_ident()))
            session = maker()
            bucket.append(session)
            return session

        return factory

    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory(
        "business", business_sessions, use_ack_loss=True
    )
    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        token_codec=JoseTokenCodec(SECRET),
        clock=lambda: _G2_JOINT_NOW,
    )
    configuration = None
    if with_configuration:
        policies = {
            operation: AdmissionPolicy(max_concurrent_tasks=0)
            for operation in ("image.single", "video.single", "image.batch")
        }
        configuration = ChatMediaAdmissionConfiguration(
            registry_capabilities=capabilities,
            server_id_factory=_JointIdFactory("chat-media-server-id"),
            policies=policies,
            versions=snapshot_producer.SnapshotVersions(
                projector="chat-media-fixture-projector-v1",
                reference_policy="chat-media-fixture-reference-v1",
            ),
        )
        requested_configuration = {
            name: value
            for name, value in (
                ("enabled_operations", enabled_operations),
                ("image_references_enabled", image_references_enabled),
            )
            if value is not None
        }
        if requested_configuration:
            configuration = replace(configuration, **requested_configuration)
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
        chat_media_admission_configuration=configuration,
    )
    codec = JoseTokenCodec(SECRET)

    def token_for(
        *,
        expired: bool = False,
        jti: str = JTI,
        password_version: str | None = None,
    ) -> str:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        if expired:
            expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        claims: dict[str, object] = {
            "sub": _G2_JOINT_ACTOR_ID,
            "jti": jti,
            "exp": expires_at,
        }
        if password_version is not None:
            claims["password_version"] = password_version
        return codec.encode(claims)

    return _System(
        engine=engine,
        app=app,
        capabilities=capabilities,
        events=events,
        auth_sessions=auth_sessions,
        business_sessions=business_sessions,
        token_for=token_for,
    )


def _post(
    system: _System,
    path: str,
    *,
    body: object | None = None,
    raw: bytes | None = None,
    key: str | None = "chat-media-idempotency-key-01",
    token: str | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["Authorization"] = f"Bearer {system.token_for()}"
        if key is not None:
            headers["Idempotency-Key"] = key
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            if raw is not None:
                headers["Content-Type"] = "application/json"
                return await client.post(path, headers=headers, content=raw)
            return await client.post(path, headers=headers, json=body)

    return asyncio.run(send())


def _get(
    system: _System,
    path: str,
    *,
    token: str | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {token or system.token_for()}",
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            return await client.get(path, headers=headers)

    return asyncio.run(send())


def _body(operation: str) -> dict[str, object]:
    if operation == "image.single":
        return {
            "model_id": _G2_JOINT_MODEL_ID,
            "prompt": "A quiet room at sunrise",
            "target": {"kind": "standalone"},
            "size": "1024x1024",
            "reference_image": "https://references.example.test/one.png",
        }
    if operation == "video.single":
        return {
            "model_id": _G2_JOINT_MODEL_ID,
            "prompt": "A slow camera pan",
            "target": {"kind": "standalone"},
            "duration": 5,
            "resolution": "720p",
            "reference_images": [],
            "reference_audios": [],
            "reference_videos": [],
        }
    if operation == "image.batch":
        return {
            "chapter_id": "joint-chapter-1",
            "assets": [
                {
                    "type": "character",
                    "id": "joint-character-1",
                    "name": "Fixture character",
                    "description": "First ordered description",
                    "model_id": _G2_JOINT_MODEL_ID,
                    "prompt_id": "",
                    "size": "1024x1024",
                    "reference_images": [],
                    "client_api_key": "ignored by legacy batch DTO",
                },
                {
                    "type": "scene",
                    "id": "joint-scene-1",
                    "name": "Fixture scene",
                    "description": "Second ordered description",
                    "model_id": _G2_JOINT_MODEL_ID,
                    "prompt_id": "",
                    "size": "1024x1024",
                    "reference_images": [],
                },
            ],
            "client_api_key": "ignored by legacy batch DTO",
        }
    raise AssertionError(operation)


def test_real_http_registers_canvas_owned_result_application_lookup(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path)
    try:
        response = _get(
            system,
            "/api/chapters/joint-chapter-1/canvas-owned-results/missing-result/application"
            "?task_id=missing-task",
        )

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "画布生成结果不存在或无权访问"}
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("query", "expected_status", "expected_detail", "business_sessions"),
    (
        (None, 422, "查询参数必须且只能包含 task_id", 0),
        ("task_id=valid&extra=1", 422, "查询参数必须且只能包含 task_id", 0),
        ("task_id=first&task_id=second", 422, "查询参数必须且只能包含 task_id", 0),
        ("task_id=", 422, "画布结果标识必须为 1 到 36 个有效字符", 0),
        ("task_id=%20task", 422, "画布结果标识必须为 1 到 36 个有效字符", 0),
        ("task_id=.", 422, "画布结果标识必须为 1 到 36 个有效字符", 0),
        ("task_id=..", 422, "画布结果标识必须为 1 到 36 个有效字符", 0),
        ("task_id=a%2Fb", 422, "画布结果标识必须为 1 到 36 个有效字符", 0),
        ("task_id=a%5Cb", 422, "画布结果标识必须为 1 到 36 个有效字符", 0),
        ("task_id=%01", 422, "画布结果标识必须为 1 到 36 个有效字符", 0),
        (
            "task_id=1234567890123456789012345678901234567",
            422,
            "画布结果标识必须为 1 到 36 个有效字符",
            0,
        ),
        ("task_id=task%2Bref", 404, "画布生成结果不存在或无权访问", 1),
    ),
)
def test_real_http_validates_canvas_lookup_query_after_authentication(
    tmp_path: Path,
    query: str | None,
    expected_status: int,
    expected_detail: str,
    business_sessions: int,
) -> None:
    system = _build_system(tmp_path)
    path = "/api/chapters/joint-chapter-1/canvas-owned-results/missing-result/application"
    if query is not None:
        path = f"{path}?{query}"
    try:
        response = _get(system, path)

        assert response.status_code == expected_status, response.text
        assert response.json() == {"detail": expected_detail}
        assert len(system.auth_sessions) == 1
        assert system.auth_sessions[0].info.get("observed_closed") is True
        assert len(system.business_sessions) == business_sessions
        if business_sessions:
            _assert_last_lookup_session_is_read_only(system)
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("session_factory_kind", "expected_detail"),
    (
        ("missing", "画布结果查询服务尚未接线"),
        ("unavailable", "数据库暂时不可用"),
    ),
)
def test_canvas_lookup_reports_unwired_or_unavailable_database_after_auth(
    session_factory_kind: str,
    expected_detail: str,
) -> None:
    actor_resolutions: list[bool] = []

    def resolve_actor(_request):
        actor_resolutions.append(True)
        return TrustedActor("canvas-lookup-actor")

    if session_factory_kind == "missing":
        session_factory = None
    else:
        def session_factory():
            raise SQLAlchemyError("synthetic database unavailable")

    app = FastAPI()
    app.include_router(
        build_canvas_result_http_router(
            session_factory=session_factory,
            resolve_account_actor=resolve_actor,
        )
    )

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            return await client.get(
                "/api/chapters/joint-chapter-1/canvas-owned-results/"
                "missing-result/application?task_id=missing-task"
            )

    response = asyncio.run(send())
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": expected_detail}
    assert actor_resolutions == [True]


@pytest.mark.parametrize("enrich_optional_snapshot", (False, True))
def test_real_http_reads_canvas_application_history_after_source_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enrich_optional_snapshot: bool,
) -> None:
    """Exercise admission, producer, worker, P5, and the create_app lookup route."""

    monkeypatch.setattr(admission_staging_module, "_G2_JOINT_PROVIDER", "openai")
    system = _build_system(tmp_path)
    raw_chapter = json.dumps(
        _NO_ID_CANVAS_FRAMES,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        with system.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE storyboard_assets ADD COLUMN created_at DATETIME"
            )
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=raw_chapter)
            )
        notes_metadata.create_all(
            system.engine,
            tables=[storyboard_media_states, personal_production_notes],
        )

        authentication, _authentication_sessions = _joint_authentication_service(
            system.engine
        )
        binding = _joint_build_binding(
            authentication,
            system.capabilities,
            _JointIdFactory("canvas-lookup-server"),
            operation=AdmissionOperation.IMAGE_SINGLE.value,
        )
        captured_payloads = []
        optional_text_digest = _canonical_json_digest(_NO_ID_CANVAS_FRAMES[1]["text"])
        original_read_target = snapshot_producer.MediaSnapshotProducer._read_target
        original_prepare_and_stage = (
            snapshot_producer.MediaSnapshotProducer.prepare_and_stage
        )

        def enrich_canonical_target(producer, session, facts):
            full_target = original_read_target(producer, session, facts)
            return replace(
                full_target,
                snapshot=replace(
                    full_target.snapshot,
                    expected_text_digest=optional_text_digest,
                ),
            )

        def capture_producer_payloads(producer, caller, **kwargs):
            result = original_prepare_and_stage(producer, caller, **kwargs)
            if isinstance(result, snapshot_producer.SnapshotPayloadsStaged):
                captured_payloads.append(result)
            return result

        if enrich_optional_snapshot:
            monkeypatch.setattr(
                snapshot_producer.MediaSnapshotProducer,
                "_read_target",
                enrich_canonical_target,
            )
        monkeypatch.setattr(
            snapshot_producer.MediaSnapshotProducer,
            "prepare_and_stage",
            capture_producer_payloads,
        )

        generation_request = _joint_request(AdmissionOperation.IMAGE_SINGLE.value)
        generation_request["reference_images"] = []
        generation_request["target"] = {
            "kind": "canvas_apply",
            "destination": {
                "kind": "frame_preview",
                "chapter_id": "joint-chapter-1",
                "identity_mode": "strict-no-id-v1",
                "frame_index": 1,
                "expected_chapter_digest": _canonical_json_digest(
                    _NO_ID_CANVAS_FRAMES
                ),
            },
        }
        admission_caller = _joint_caller(system.engine)
        try:
            staged = _joint_prepare_and_stage(
                binding,
                admission_caller,
                operation=AdmissionOperation.IMAGE_SINGLE.value,
                raw_key="canvas-result-lookup-real-chain",
                request=generation_request,
            )
            assert isinstance(staged, AdmissionStaged)
            assert len(captured_payloads) == 1
            task_id = staged.ordered_tasks[0].task_id
            target = captured_payloads[0].items[0].snapshot.target_snapshot
            assert target.kind == "canvas_frame_preview"
            if enrich_optional_snapshot:
                assert target.expected_text_digest == optional_text_digest
            else:
                assert target.expected_text_digest is None
            admission_caller.commit()
        finally:
            admission_caller.close()

        worker_environment = SimpleNamespace(
            engine=system.engine,
            capabilities=system.capabilities,
        )
        stored_media: list[StoredMedia] = []
        runtime_config, provider_calls = _runtime_configuration(
            worker_environment,
            _provider_body(),
            stored_media,
        )
        runtime = build_generation_worker_runtime(
            GenerationWorkerConfiguration(runtime_config, system.capabilities)
        )
        worker_outcome = runtime.run_claimed_once("image-single")
        assert worker_outcome is not None
        assert worker_outcome != "pending"
        assert worker_outcome != "ownership_lost"
        assert worker_outcome.result_ref

        second_binding = _joint_build_binding(
            authentication,
            system.capabilities,
            _JointIdFactory("canvas-lookup-second-server"),
            operation=AdmissionOperation.IMAGE_SINGLE.value,
        )
        second_admission_caller = _joint_caller(system.engine)
        try:
            second_staged = _joint_prepare_and_stage(
                second_binding,
                second_admission_caller,
                operation=AdmissionOperation.IMAGE_SINGLE.value,
                raw_key="canvas-result-lookup-real-chain-second",
                request=dict(generation_request),
            )
            assert isinstance(second_staged, AdmissionStaged)
            second_task_id = second_staged.ordered_tasks[0].task_id
            second_target = captured_payloads[-1].items[0].snapshot.target_snapshot
            assert second_target == target
            second_admission_caller.commit()
        finally:
            second_admission_caller.close()

        second_worker_outcome = runtime.run_claimed_once("image-single")
        assert second_worker_outcome is not None
        assert second_worker_outcome != "pending"
        assert second_worker_outcome != "ownership_lost"
        assert second_worker_outcome.result_ref
        assert second_worker_outcome.result_ref != worker_outcome.result_ref
        assert len(provider_calls) == 2
        assert len(stored_media) == 2

        discovery_path = (
            "/api/chapters/joint-chapter-1/canvas-owned-results"
            "?target_kind=frame_preview&identity_mode=strict-no-id-v1"
            f"&frame_index={target.compat_frame_index}"
            f"&expected_chapter_digest={target.full_chapter_digest}&limit=1"
        )

        # A changed chapter invalidates the original no-ID identity even when
        # the frame at the old index still has the same content.
        changed_sibling_frames = json.loads(raw_chapter)
        changed_sibling_frames[0]["text"] = "无关分镜已编辑"
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(
                    content=json.dumps(
                        changed_sibling_frames,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
            )
        before_stale_discovery = _canvas_lookup_write_state(system.engine)
        stale_discovery_response = _get(system, discovery_path)
        assert stale_discovery_response.status_code == 200, stale_discovery_response.text
        _assert_last_lookup_session_is_read_only(system)
        stale_discovery_page = stale_discovery_response.json()
        assert len(stale_discovery_page["items"]) == 1
        assert stale_discovery_page["items"][0]["eligibility"] == "stale"
        assert stale_discovery_page["items"][0]["application"] is None
        assert stale_discovery_page["next_cursor"]
        stale_next_page = _get(
            system,
            f"{discovery_path}&cursor={stale_discovery_page['next_cursor']}",
        )
        assert stale_next_page.status_code == 200, stale_next_page.text
        assert len(stale_next_page.json()["items"]) == 1
        assert stale_next_page.json()["items"][0]["eligibility"] == "stale"
        assert stale_next_page.json()["items"][0]["application"] is None
        assert stale_next_page.json()["next_cursor"] is None
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_stale_discovery
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=raw_chapter)
            )

        before_discovery = _canvas_lookup_write_state(system.engine)
        discovery_response = _get(system, discovery_path)
        assert discovery_response.status_code == 200, discovery_response.text
        _assert_last_lookup_session_is_read_only(system)
        discovery_payload = discovery_response.json()
        assert set(discovery_payload) == {
            "chapter_id",
            "target",
            "items",
            "next_cursor",
        }
        assert discovery_payload["chapter_id"] == "joint-chapter-1"
        assert discovery_payload["target"] == {
            "kind": "frame_preview",
            "chapter_id": "joint-chapter-1",
            "identity_mode": "strict-no-id-v1",
            "frame_index": target.compat_frame_index,
            "expected_chapter_digest": target.full_chapter_digest,
        }
        assert discovery_payload["next_cursor"]
        assert len(discovery_payload["items"]) == 1
        discovery_item = discovery_payload["items"][0]
        assert set(discovery_item) == {
            "task_id",
            "result_ref",
            "operation",
            "completed_at",
            "expected_target",
            "application_key",
            "eligibility",
            "application",
        }
        assert discovery_item["task_id"] in {task_id, second_task_id}
        assert discovery_item["result_ref"] in {
            worker_outcome.result_ref,
            second_worker_outcome.result_ref,
        }
        assert discovery_item["operation"] == "image.single"
        assert discovery_item["eligibility"] == "ready"
        assert isinstance(discovery_item["expected_target"], dict)
        assert set(discovery_item["expected_target"]) == {
            "kind",
            "chapter_id",
            "series_id",
            "stable_storyboard_asset_id",
            "asset_type",
            "asset_id",
            "compat_frame_index",
            "expected_text_digest",
            "media_identity",
            "content_digest",
            "notes_revision",
            "full_frame_digest",
            "full_chapter_digest",
            "expected_asset_image_url",
            "same_series_target_digest",
        }
        assert discovery_item["expected_target"]["asset_id"] is None
        assert discovery_item["expected_target"]["full_chapter_digest"] == (
            target.full_chapter_digest
        )
        assert len(discovery_item["application_key"]) == 64
        assert all(
            character in "0123456789abcdef"
            for character in discovery_item["application_key"]
        )
        assert discovery_item["application"] is None
        assert datetime.fromisoformat(discovery_item["completed_at"]).utcoffset() is not None
        discovery_next_page_response = _get(
            system,
            f"{discovery_path}&cursor={discovery_payload['next_cursor']}",
        )
        assert discovery_next_page_response.status_code == 200, discovery_next_page_response.text
        _assert_last_lookup_session_is_read_only(system)
        discovery_next_page = discovery_next_page_response.json()
        assert discovery_next_page["next_cursor"] is None
        assert len(discovery_next_page["items"]) == 1
        other_discovery_item = discovery_next_page["items"][0]
        assert other_discovery_item["eligibility"] == "ready"
        assert other_discovery_item["application"] is None
        assert other_discovery_item["result_ref"] != discovery_item["result_ref"]
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_discovery

        missing_projection_ref = other_discovery_item["result_ref"]
        with Session(system.engine, expire_on_commit=False) as projection_removal:
            missing_projection = projection_removal.get(
                GenerationResultTargetRow,
                missing_projection_ref,
            )
            assert missing_projection is not None
            projection_removal.delete(missing_projection)
            projection_removal.commit()

        before_missing_projection_lookup = _canvas_lookup_write_state(system.engine)
        missing_projection_page = _get(system, discovery_path)
        assert missing_projection_page.status_code == 200, missing_projection_page.text
        missing_projection_payload = missing_projection_page.json()
        assert len(missing_projection_payload["items"]) == 1
        assert missing_projection_payload["items"][0]["result_ref"] == (
            discovery_item["result_ref"]
        )
        assert missing_projection_payload["next_cursor"] is None
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_missing_projection_lookup

        backfill_session = Session(system.engine, expire_on_commit=False)
        try:
            backfill_report = backfill_canvas_result_targets(
                backfill_session,
                TrustedActor(_G2_JOINT_ACTOR_ID),
                limit=50,
            )
            assert backfill_report.scanned == 2
            assert backfill_report.projected == 1
            assert backfill_report.already_present == 1
            assert backfill_report.unready == 0
            assert backfill_report.conflict == 0
            assert backfill_report.ignored_noncanvas == 0
            assert backfill_report.next_cursor is None
            assert not backfill_report.has_more
            assert any(
                isinstance(row, GenerationResultTargetRow)
                and row.result_ref == missing_projection_ref
                for row in backfill_session.new
            )
            assert _canvas_lookup_write_state(system.engine)[2] == 1
            backfill_session.commit()
        finally:
            backfill_session.close()
        assert _canvas_lookup_write_state(system.engine)[2] == 2

        with Session(system.engine, expire_on_commit=False) as repeated_backfill:
            repeat_report = backfill_canvas_result_targets(
                repeated_backfill,
                TrustedActor(_G2_JOINT_ACTOR_ID),
                limit=50,
            )
            assert repeat_report.projected == 0
            assert repeat_report.already_present == 2
            assert repeat_report.next_cursor is None
            assert not repeated_backfill.new

        worker_by_result_ref = {
            worker_outcome.result_ref: (worker_outcome, task_id, target),
            second_worker_outcome.result_ref: (
                second_worker_outcome,
                second_task_id,
                second_target,
            ),
        }
        worker_outcome, task_id, target = worker_by_result_ref[
            discovery_item["result_ref"]
        ]

        lookup_path = (
            "/api/chapters/joint-chapter-1/canvas-owned-results/"
            f"{worker_outcome.result_ref}/application?task_id={task_id}"
        )
        before_lookup = _canvas_lookup_write_state(system.engine)
        not_applied = _get(system, lookup_path)
        assert not_applied.status_code == 200, not_applied.text
        assert not_applied.json() == {
            "task_id": task_id,
            "result_ref": worker_outcome.result_ref,
            "status": "not_applied",
            "application": None,
        }
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_lookup

        actor = GenerationActor(
            _G2_JOINT_ACTOR_ID,
            TrustedActor(_G2_JOINT_ACTOR_ID),
        )
        apply_session = Session(system.engine, expire_on_commit=False)
        try:
            facade = GenerationResultApplicationFacade(apply_session)
            receipt = facade.load_owned_completed_result(
                apply_session,
                actor,
                task_id,
                worker_outcome.result_ref,
            )
            assert isinstance(receipt, GenerationResultReceipt)
            scope = target_scope_for_snapshot(target)
            authority = CanvasApplyAuthority(
                actor=actor,
                task_id=task_id,
                result_ref=receipt.result_ref,
                operation=receipt.operation,
                target=target,
                result_digest=receipt.content_digest,
            )
            command = GenerationApplicationCommand(
                task_id=task_id,
                result_ref=receipt.result_ref,
                expected_target=target,
                application_key=application_key_for(receipt.result_ref, scope),
            )
            applied = facade.apply_canvas_media(apply_session, authority, command)
            assert applied.kind == "applied"
            assert applied.application_ref
            apply_session.commit()
        finally:
            apply_session.close()

        before_native_lookup = _canvas_lookup_write_state(system.engine)
        native_applied_response = _get(system, lookup_path)
        assert native_applied_response.status_code == 200, native_applied_response.text
        native_applied_payload = native_applied_response.json()
        assert native_applied_payload["task_id"] == task_id
        assert native_applied_payload["result_ref"] == worker_outcome.result_ref
        assert native_applied_payload["status"] == "applied"
        assert native_applied_payload["application"]["application_ref"] == (
            applied.application_ref
        )
        assert native_applied_payload["application"]["target_revision"] == (
            applied.target_revision
        )
        native_committed_at = datetime.fromisoformat(
            native_applied_payload["application"]["committed_at"]
        )
        assert native_committed_at.tzinfo is not None
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_native_lookup

        before_applied_discovery = _canvas_lookup_write_state(system.engine)
        applied_discovery_response = _get(system, discovery_path)
        assert applied_discovery_response.status_code == 200, applied_discovery_response.text
        _assert_last_lookup_session_is_read_only(system)
        applied_discovery_item = applied_discovery_response.json()["items"][0]
        assert applied_discovery_item["eligibility"] == "already_applied"
        assert applied_discovery_item["application"] == native_applied_payload["application"]
        assert applied_discovery_item["expected_target"] == discovery_item["expected_target"]
        assert applied_discovery_response.json()["next_cursor"]
        applied_discovery_next_page = _get(
            system,
            f"{discovery_path}&cursor={applied_discovery_response.json()['next_cursor']}",
        )
        assert applied_discovery_next_page.status_code == 200, applied_discovery_next_page.text
        _assert_last_lookup_session_is_read_only(system)
        stale_unapplied_item = applied_discovery_next_page.json()["items"][0]
        assert stale_unapplied_item["eligibility"] == "stale"
        assert stale_unapplied_item["application"] is None
        assert applied_discovery_next_page.json()["next_cursor"] is None
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_applied_discovery

        # Seed the stored target snapshots into the prior nullable-key shape.
        # The execution step stores payload bindings, not a target snapshot.
        with Session(system.engine, expire_on_commit=False) as legacy_snapshot_fixture:
            task = legacy_snapshot_fixture.get(AITaskRow, task_id)
            receipt_row = legacy_snapshot_fixture.get(
                GenerationResultReceiptRow,
                worker_outcome.result_ref,
            )
            assert task is not None and task.request_data is not None
            legacy_request_data = task.request_data
            assert receipt_row is not None
            task_snapshot_root = json.loads(task.request_data)
            task_target = _target_snapshot_mapping(task_snapshot_root)
            assert task_target["expected_asset_image_url"] is None
            del task_target["expected_asset_image_url"]
            task.request_data = json.dumps(
                task_snapshot_root,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )

            submission = legacy_snapshot_fixture.get(
                ExternalSubmissionRow,
                receipt_row.submission_id,
            )
            assert submission is not None and isinstance(submission.request_snapshot, dict)
            submission_snapshot = json.loads(json.dumps(submission.request_snapshot))
            submission_target = _target_snapshot_mapping(submission_snapshot)
            assert submission_target["expected_asset_image_url"] is None
            del submission_target["expected_asset_image_url"]
            submission.request_snapshot = submission_snapshot
            legacy_snapshot_fixture.commit()

        before_applied_lookup = _canvas_lookup_write_state(system.engine)
        applied_response = _get(system, lookup_path)
        assert applied_response.status_code == 200, applied_response.text
        applied_payload = applied_response.json()
        assert set(applied_payload) == {
            "task_id",
            "result_ref",
            "status",
            "application",
        }
        assert applied_payload["task_id"] == task_id
        assert applied_payload["result_ref"] == worker_outcome.result_ref
        assert applied_payload["status"] == "applied"
        assert isinstance(applied_payload["application"], dict)
        application_payload = applied_payload["application"]
        assert set(application_payload) == {
            "application_ref",
            "target_revision",
            "committed_at",
        }
        assert application_payload["application_ref"] == applied.application_ref
        assert application_payload["target_revision"] == applied.target_revision
        committed_at = datetime.fromisoformat(
            application_payload["committed_at"]
        )
        assert committed_at.tzinfo is not None
        assert len(application_payload["application_ref"]) <= 36
        assert len(application_payload["target_revision"]) == 64
        assert all(
            character in "0123456789abcdef"
            for character in application_payload["target_revision"]
        )
        assert applied_payload == native_applied_payload
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_applied_lookup

        manually_edited_frames = json.loads(raw_chapter)
        manually_edited_frames[1]["text"] = "应用后的人工编辑"
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(
                    content=json.dumps(
                        manually_edited_frames,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
            )
        before_history_lookup = _canvas_lookup_write_state(system.engine)
        history = _get(system, lookup_path)
        assert history.status_code == 200, history.text
        assert history.json() == applied_payload
        before_discovery_history = _canvas_lookup_write_state(system.engine)
        discovery_history = _get(system, discovery_path)
        assert discovery_history.status_code == 200, discovery_history.text
        _assert_last_lookup_session_is_read_only(system)
        discovery_history_payload = discovery_history.json()
        assert discovery_history_payload["items"][0]["eligibility"] == "already_applied"
        assert discovery_history_payload["items"][0]["application"] == (
            native_applied_payload["application"]
        )
        assert discovery_history_payload["next_cursor"]
        discovery_history_next_page = _get(
            system,
            f"{discovery_path}&cursor={discovery_history_payload['next_cursor']}",
        )
        assert discovery_history_next_page.status_code == 200, discovery_history_next_page.text
        _assert_last_lookup_session_is_read_only(system)
        assert discovery_history_next_page.json()["items"][0]["eligibility"] == "stale"
        assert discovery_history_next_page.json()["items"][0]["application"] is None
        assert discovery_history_next_page.json()["next_cursor"] is None
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_discovery_history
        assert _canvas_lookup_write_state(system.engine) == before_history_lookup

        missing_reference_path = (
            "/api/chapters/joint-chapter-1/canvas-owned-results/other-result/application"
            f"?task_id={task_id}"
        )
        missing_task_path = (
            "/api/chapters/joint-chapter-1/canvas-owned-results/"
            f"{worker_outcome.result_ref}/application?task_id=other-task"
        )
        wrong_chapter_path = (
            "/api/chapters/joint-chapter-2/canvas-owned-results/"
            f"{worker_outcome.result_ref}/application?task_id={task_id}"
        )
        other_owner_id = "canvas-lookup-other-owner"
        other_owner_task_id = "canvas-lookup-other-task"
        with system.engine.begin() as connection:
            connection.execute(
                insert(users).values(
                    id=other_owner_id,
                    username="canvas-lookup-other-owner",
                    email="canvas-lookup-other-owner@example.test",
                    hashed_password="fixture-only-hash",
                    is_superuser=False,
                    membership_type="premium",
                    membership_expires_at=None,
                    created_at=_G2_JOINT_NOW.replace(tzinfo=None),
                )
            )
            connection.execute(
                insert(AITaskRow.__table__).values(
                    id=other_owner_task_id,
                    user_id=other_owner_id,
                    type="image-single",
                    status="completed",
                    message_id="canvas-lookup-other-message",
                    credit_cost=3,
                    billing_status="charged",
                    created_at=_G2_JOINT_NOW.replace(tzinfo=None),
                    updated_at=_G2_JOINT_NOW.replace(tzinfo=None),
                )
            )
        other_owner_path = (
            "/api/chapters/joint-chapter-1/canvas-owned-results/"
            f"{worker_outcome.result_ref}/application?task_id={other_owner_task_id}"
        )
        for private_path in (
            missing_reference_path,
            missing_task_path,
            wrong_chapter_path,
            other_owner_path,
        ):
            before_private_lookup = _canvas_lookup_write_state(system.engine)
            private_response = _get(system, private_path)
            assert private_response.status_code == 404, private_response.text
            assert private_response.json() == {
                "detail": "画布生成结果不存在或无权访问"
            }
            _assert_last_lookup_session_is_read_only(system)
            assert _canvas_lookup_write_state(system.engine) == before_private_lookup

        duplicate_payload = json.loads(legacy_request_data)
        duplicate_target = _target_snapshot_mapping(duplicate_payload)
        duplicate_value = json.dumps(
            duplicate_target,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        duplicate_request_data = (
            f'{{"target_snapshot":{duplicate_value},'
            f'"target_snapshot":{duplicate_value}}}'
        )
        unknown_payload = json.loads(legacy_request_data)
        _target_snapshot_mapping(unknown_payload)["unexpected"] = "rejected"
        invalid_request_data_values = (
            "{",
            "{}",
            '{"noise":NaN}',
            '{"noise":1e999}',
            duplicate_request_data,
            json.dumps(unknown_payload, ensure_ascii=False, separators=(",", ":")),
        )
        for invalid_request_data in invalid_request_data_values:
            with Session(system.engine, expire_on_commit=False) as snapshot_fixture:
                task = snapshot_fixture.get(AITaskRow, task_id)
                assert task is not None
                task.request_data = invalid_request_data
                snapshot_fixture.commit()
            before_invalid_snapshot_lookup = _canvas_lookup_write_state(system.engine)
            invalid_snapshot_response = _get(system, lookup_path)
            assert invalid_snapshot_response.status_code == 409
            assert invalid_snapshot_response.json() == {
                "detail": "画布生成结果的原始目标快照无效"
            }
            _assert_last_lookup_session_is_read_only(system)
            assert _canvas_lookup_write_state(system.engine) == before_invalid_snapshot_lookup
        with Session(system.engine, expire_on_commit=False) as snapshot_fixture:
            task = snapshot_fixture.get(AITaskRow, task_id)
            assert task is not None
            task.request_data = legacy_request_data
            snapshot_fixture.commit()

        with Session(system.engine, expire_on_commit=False) as proof_fixture:
            application_row = proof_fixture.execute(
                select(ApplicationRecordRow).where(
                    ApplicationRecordRow.application_ref == applied.application_ref
                )
            ).scalar_one()
            application_row.after_target_digest = "F" * 64
            proof_fixture.commit()
        before_invalid_proof_lookup = _canvas_lookup_write_state(system.engine)
        invalid_proof_response = _get(system, lookup_path)
        assert invalid_proof_response.status_code == 409
        assert invalid_proof_response.json() == {
            "detail": "画布生成结果的应用证明无效"
        }
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_invalid_proof_lookup

        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_series)
                .where(catalog_series.c.id == "joint-series-1")
                .values(user_id=other_owner_id)
            )
        before_denied_discovery = _canvas_lookup_write_state(system.engine)
        denied_discovery = _get(system, discovery_path)
        assert denied_discovery.status_code == 403, denied_discovery.text
        assert denied_discovery.json() == {"detail": "无权访问该剧集"}
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_denied_discovery

        before_denied_lookup = _canvas_lookup_write_state(system.engine)
        denied = _get(system, lookup_path)
        assert denied.status_code == 403, denied.text
        assert denied.json() == {"detail": "无权访问该剧集"}
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before_denied_lookup
        assert len(provider_calls) == 2
        assert len(stored_media) == 2
    finally:
        system.engine.dispose()


def _target_snapshot_mapping(value: object) -> dict[str, object]:
    if type(value) is dict:
        target = value.get("target_snapshot")
        if type(target) is dict:
            return target
        nested = value.get("snapshot")
        if type(nested) is dict and type(nested.get("target_snapshot")) is dict:
            return nested["target_snapshot"]
    raise AssertionError("fixture snapshot must contain a canonical target_snapshot")


def _canvas_lookup_write_state(engine: Engine) -> tuple[int, int, int, int, int]:
    with engine.connect() as connection:
        return (
            connection.execute(
                select(func.count()).select_from(AITaskRow.__table__)
            ).scalar_one(),
            connection.execute(
                select(func.count()).select_from(ApplicationRecordRow.__table__)
            ).scalar_one(),
            connection.execute(
                select(func.count()).select_from(GenerationResultTargetRow.__table__)
            ).scalar_one(),
            connection.execute(
                select(func.count()).select_from(CreditLogRow.__table__)
            ).scalar_one(),
            connection.execute(select(UserCreditRow.__table__.c.credits)).scalar_one(),
        )


def _assert_last_lookup_session_is_read_only(system: _System) -> None:
    session = system.business_sessions[-1]
    assert session.info.get("dml_execute_calls", 0) == 0
    assert session.info.get("flush_calls", 0) == 0
    assert session.info.get("commit_calls", 0) == 0
    assert session.info.get("rollback_calls", 0) == 1
    assert session.info.get("close_count") == 1


@pytest.mark.parametrize(
    ("path", "operation", "category"),
    (
        ("/api/chat/image", "image.single", "image"),
        ("/api/chat/video", "video.single", "video"),
        ("/api/chat/batch-generate-images", "image.batch", "image"),
    ),
)
def test_real_jwt_sqlite_producer_stages_each_route_and_confirms_fresh_receipt(
    tmp_path: Path,
    path: str,
    operation: str,
    category: str,
) -> None:
    system = _build_system(tmp_path, operation_category=category)
    try:
        main_thread = threading.get_ident()
        response = _post(system, path, body=_body(operation))

        assert response.status_code == 200, response.text
        payload = response.json()
        if operation == "image.batch":
            assert set(payload) == {"tasks"}
            assert all(
                set(item)
                == {"type", "asset_id", "asset_name", "message_id", "task_id"}
                for item in payload["tasks"]
            )
            assert [
                {
                    "type": item["type"],
                    "asset_id": item["asset_id"],
                    "asset_name": item["asset_name"],
                }
                for item in payload["tasks"]
            ] == [
                {
                    "type": "character",
                    "asset_id": "joint-character-1",
                    "asset_name": "Fixture character",
                },
                {
                    "type": "scene",
                    "asset_id": "joint-scene-1",
                    "asset_name": "Fixture scene",
                },
            ]
            message_ids = [item["message_id"] for item in payload["tasks"]]
            task_ids = [item["task_id"] for item in payload["tasks"]]
            assert all(isinstance(value, str) and value for value in message_ids)
            assert all(isinstance(value, str) and value for value in task_ids)
            assert len(set(message_ids)) == len(message_ids)
            assert len(set(task_ids)) == len(task_ids)
            expected_tasks, expected_messages, expected_credits = 2, 4, 494
        else:
            assert set(payload) == {
                "submission_id",
                "operation",
                "task_id",
                "message_id",
                "status",
                "quoted_amount",
                "billing_status",
            }
            assert payload["operation"] == operation
            assert payload["status"] == "queued"
            assert payload["quoted_amount"] == 3
            assert payload["billing_status"] == "charged"
            expected_tasks, expected_messages, expected_credits = 1, 0, 497

        with system.engine.connect() as connection:
            tasks = connection.execute(
                select(AITaskRow.__table__)
            ).mappings().all()
            units = connection.execute(
                select(BillingUnitRow.__table__)
            ).mappings().all()
            credits = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            ledger = connection.execute(
                select(CreditLogRow.__table__)
            ).mappings().all()
            messages = connection.execute(select(chat_messages)).mappings().all()
        assert len(tasks) == expected_tasks
        assert len(units) == expected_tasks
        assert len(ledger) == expected_tasks
        assert len(messages) == expected_messages
        assert credits == expected_credits
        assert sum(row["amount"] for row in ledger) == -3 * expected_tasks
        assert all(row["user_id"] == _G2_JOINT_ACTOR_ID for row in tasks)
        assert all(row["billing_status"] == "charged" for row in units)

        first_auth_close = next(
            index for index, event in enumerate(system.events) if event[0] == "close:auth"
        )
        first_business_open = next(
            index for index, event in enumerate(system.events) if event[0] == "open:business"
        )
        first_business_sql = next(
            index for index, event in enumerate(system.events) if event[0] == "execute:business"
        )
        writer_close = next(
            index for index, event in enumerate(system.events) if event[0] == "close:business"
        )
        receipt_open = next(
            index
            for index, event in enumerate(system.events)
            if event[0] == "open:business" and index > writer_close
        )
        auth_close_indexes = [
            index
            for index, event in enumerate(system.events)
            if event[0] == "close:auth"
        ]
        assert auth_close_indexes
        assert max(auth_close_indexes) < first_business_sql
        assert first_auth_close < first_business_open < first_business_sql
        assert writer_close < receipt_open
        assert all(thread_id != main_thread for _label, thread_id in system.events)
        assert all(session.info["observed_closed"] for session in system.auth_sessions)
        assert all(session.info["observed_closed"] for session in system.business_sessions)
    finally:
        system.engine.dispose()


_NO_ID_CANVAS_FRAMES = [
    {"text": "未绑定分镜", "storyboard": [None, ""]},
    {"text": "目标画面", "storyboard": []},
]


def _canonical_json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _strict_no_id_canvas_body(
    *,
    frame_index: object = 1,
    frames: object | None = None,
) -> dict[str, object]:
    expected_frames = _NO_ID_CANVAS_FRAMES if frames is None else frames
    body = _body("image.single")
    body["target"] = {
        "kind": "canvas_apply",
        "destination": {
            "kind": "frame_preview",
            "chapter_id": "joint-chapter-1",
            "identity_mode": "strict-no-id-v1",
            "frame_index": frame_index,
            "expected_chapter_digest": _canonical_json_digest(expected_frames),
        },
    }
    return body


def test_real_http_admits_strict_no_id_canvas_frame_with_locked_canonical_facts(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path)
    raw_content = json.dumps(
        _NO_ID_CANVAS_FRAMES,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=raw_content)
            )

        response = _post(
            system,
            "/api/chat/image",
            body=_strict_no_id_canvas_body(),
            key="strict-no-id-canvas-admission-01",
        )

        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["operation"] == "image.single"
        assert receipt["billing_status"] == "charged"

        with system.engine.connect() as connection:
            task = connection.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == receipt["task_id"]
                )
            ).mappings().one()
            target = json.loads(task["request_data"])["target_snapshot"]
            source_storyboards = connection.execute(
                select(catalog_chapters.c.content).where(
                    catalog_chapters.c.id == "joint-chapter-1"
                )
            ).scalar_one()

        assert target["kind"] == "canvas_frame_preview"
        assert target["stable_storyboard_asset_id"] is None
        assert target["compat_frame_index"] == 1
        assert target["full_chapter_digest"] == _canonical_json_digest(
            _NO_ID_CANVAS_FRAMES
        )
        assert target["full_frame_digest"] == _canonical_json_digest(
            _NO_ID_CANVAS_FRAMES[1]
        )
        assert source_storyboards == raw_content
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("mutation", "mutated_value", "expected_status", "expected_detail"),
    (
        ("identity_mode", None, 422, "请求格式无效"),
        ("identity_mode", "strict-no-id-v2", 422, "请求格式无效"),
        ("frame_index", True, 422, "请求格式无效"),
        ("frame_index", -1, 422, "请求格式无效"),
        ("expected_chapter_digest", "A" * 64, 422, "请求格式无效"),
        ("unexpected", "must-be-rejected", 422, "请求格式无效"),
        (
            "storyboard_asset_id",
            "must-not-fallback-to-stable-id",
            422,
            "请求格式无效",
        ),
        ("frame_index", 2, 409, "指定分镜不再是无稳定ID目标"),
        (
            "expected_chapter_digest",
            "0" * 64,
            409,
            "章节内容与画布目标断言不一致",
        ),
    ),
)
def test_real_http_rejects_malformed_or_mixed_strict_no_id_identity_without_writes(
    tmp_path: Path,
    mutation: str,
    mutated_value: object,
    expected_status: int,
    expected_detail: str,
) -> None:
    system = _build_system(tmp_path)
    raw_content = json.dumps(
        _NO_ID_CANVAS_FRAMES,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    body = _strict_no_id_canvas_body()
    destination = body["target"]["destination"]
    if mutated_value is None:
        destination.pop(mutation)
    else:
        destination[mutation] = mutated_value
    try:
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=raw_content)
            )

        response = _post(
            system,
            "/api/chat/image",
            body=body,
            key=f"strict-no-id-invalid-{mutation}",
        )

        assert response.status_code == expected_status, response.text
        assert response.json() == {"detail": expected_detail}
        if expected_status == 422:
            assert system.business_sessions == []
        with system.engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("chapter_content", "digest_frames", "expected_status"),
    (
        (
            json.dumps(
                [
                    {"text": "兄弟帧", "storyboard": [None, ""]},
                    {"text": "已绑定目标", "storyboard": ["existing-storyboard-id"]},
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            [
                {"text": "兄弟帧", "storyboard": [None, ""]},
                {"text": "已绑定目标", "storyboard": ["existing-storyboard-id"]},
            ],
            409,
        ),
        (
            '[{"text":"重复键","text":"重复键覆盖"},{"text":"目标","storyboard":[]}]',
            None,
            422,
        ),
        (
            '[{"text":"非有限值","value":NaN},{"text":"目标","storyboard":[]}]',
            None,
            422,
        ),
        ('[null,{"text":"目标","storyboard":[]}]', None, 422),
        ('{"text":"根不是数组"}', None, 422),
        ('not-json', None, 422),
        (
            json.dumps(
                [
                    {"text": "兄弟帧", "storyboard": [None, ""]},
                    {"text": "重复引用", "storyboard": ["same-id", "same-id"]},
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            [
                {"text": "兄弟帧", "storyboard": [None, ""]},
                {"text": "重复引用", "storyboard": ["same-id", "same-id"]},
            ],
            409,
        ),
        (
            json.dumps(
                [
                    {"text": "兄弟帧", "storyboard": [None, ""]},
                    {"text": "非法空白引用", "storyboard": [" "]},
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            [
                {"text": "兄弟帧", "storyboard": [None, ""]},
                {"text": "非法空白引用", "storyboard": [" "]},
            ],
            409,
        ),
        (
            json.dumps(
                [
                    {"text": "兄弟帧", "storyboard": [None, ""]},
                    {"text": "非数组引用", "storyboard": "existing-storyboard-id"},
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            [
                {"text": "兄弟帧", "storyboard": [None, ""]},
                {"text": "非数组引用", "storyboard": "existing-storyboard-id"},
            ],
            409,
        ),
    ),
)
def test_real_http_rejects_existing_or_invalid_strict_no_id_chapter_without_writes(
    tmp_path: Path,
    chapter_content: str,
    digest_frames: object | None,
    expected_status: int,
) -> None:
    system = _build_system(tmp_path)
    body = _strict_no_id_canvas_body(frames=digest_frames)
    if digest_frames is None:
        body["target"]["destination"]["expected_chapter_digest"] = "0" * 64
    try:
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=chapter_content)
            )

        response = _post(
            system,
            "/api/chat/image",
            body=body,
            key="strict-no-id-invalid-locked-chapter",
        )

        assert response.status_code == expected_status, response.text
        with system.engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
    finally:
        system.engine.dispose()


def test_authentication_precedes_manual_json_and_dto_validation(tmp_path: Path) -> None:
    system = _build_system(tmp_path)
    try:
        malformed_without_auth = _post(
            system,
            "/api/chat/image",
            raw=b"{",
            token="invalid-token",
        )
        assert malformed_without_auth.status_code == 401
        assert malformed_without_auth.headers.get("www-authenticate") == "Bearer"
        assert system.business_sessions == []

        malformed_with_auth = _post(system, "/api/chat/image", raw=b"{")
        assert malformed_with_auth.status_code == 422
        assert system.business_sessions == []

        strict_extra = dict(_body("image.single"), api_key="client-controlled")
        rejected_extra = _post(system, "/api/chat/image", body=strict_extra)
        assert rejected_extra.status_code == 422
        assert system.business_sessions == []

        expired = _post(
            system,
            "/api/chat/image",
            raw=b"{",
            token=system.token_for(expired=True),
        )
        assert expired.status_code == 401
        assert system.business_sessions == []

        revoked_token = system.token_for()
        with system.engine.begin() as connection:
            connection.execute(
                update(user_sessions)
                .where(user_sessions.c.id == AUTH_SESSION_ID)
                .values(
                    is_active=False,
                    revoked_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
        revoked = _post(
            system,
            "/api/chat/image",
            raw=b"{",
            token=revoked_token,
        )
        assert revoked.status_code == 401
        assert system.business_sessions == []
    finally:
        system.engine.dispose()


def test_authenticated_default_without_admission_configuration_fails_closed(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path, with_configuration=False)
    try:
        response = _post(system, "/api/chat/image", body=_body("image.single"))
        assert response.status_code == 503
        assert system.business_sessions == []
        with system.engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("path", "operation", "category"),
    (
        ("/api/chat/video", "video.single", "video"),
        ("/api/chat/batch-generate-images", "image.batch", "image"),
    ),
)
def test_real_http_rejects_disabled_media_operations_before_staging_or_charging(
    tmp_path: Path,
    path: str,
    operation: str,
    category: str,
) -> None:
    system = _build_system(
        tmp_path,
        operation_category=category,
        enabled_operations=frozenset({"image.single"}),
    )
    try:
        before = _canvas_lookup_write_state(system.engine)
        response = _post(system, path, body=_body(operation))

        assert system.business_sessions == [], response.text
        assert response.status_code == 503, response.text
        assert response.json() == {"detail": "该媒体生成操作尚未启用"}
        assert len(system.auth_sessions) == 1
        assert system.auth_sessions[0].info.get("observed_closed") is True
        assert _canvas_lookup_write_state(system.engine) == before
    finally:
        system.engine.dispose()


@pytest.mark.parametrize("reference_field", ("reference_image", "reference_images"))
def test_real_http_rejects_unconsumed_image_references_before_staging_or_charging(
    tmp_path: Path,
    reference_field: str,
) -> None:
    system = _build_system(
        tmp_path,
        image_references_enabled=False,
    )
    body = _body("image.single")
    if reference_field == "reference_image":
        body[reference_field] = "https://references.example.test/reference.png"
        body.pop("reference_images", None)
    else:
        body.pop("reference_image", None)
        body[reference_field] = ["https://references.example.test/reference.png"]
    try:
        before = _canvas_lookup_write_state(system.engine)
        response = _post(system, "/api/chat/image", body=body)

        assert response.status_code == 422, response.text
        assert response.json() == {"detail": "当前图片生成暂不支持参考图"}
        assert len(system.auth_sessions) == 1
        assert system.auth_sessions[0].info.get("observed_closed") is True
        assert system.business_sessions == []
        assert _canvas_lookup_write_state(system.engine) == before
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    "reference_fields",
    ({}, {"reference_image": ""}, {"reference_images": []}),
)
def test_real_http_allows_image_single_without_references_when_references_are_disabled(
    tmp_path: Path,
    reference_fields: dict[str, object],
) -> None:
    system = _build_system(
        tmp_path,
        image_references_enabled=False,
    )
    body = _body("image.single")
    body.pop("reference_image", None)
    body.update(reference_fields)
    try:
        response = _post(system, "/api/chat/image", body=body)

        assert response.status_code == 200, response.text
        assert response.json()["operation"] == "image.single"
        assert response.json()["billing_status"] == "charged"
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("membership_type", "expires_at"),
    (
        ("free", None),
        ("premium", _G2_JOINT_NOW - timedelta(days=1)),
    ),
)
def test_original_replay_survives_free_or_expired_membership_but_new_key_is_denied(
    tmp_path: Path,
    membership_type: str,
    expires_at: datetime | None,
) -> None:
    system = _build_system(tmp_path)
    try:
        request_body = _body("image.single")
        original = _post(
            system,
            "/api/chat/image",
            body=request_body,
            key="chat-media-original-replay-01",
        )
        assert original.status_code == 200
        with system.engine.begin() as connection:
            connection.execute(
                update(users)
                .where(users.c.id == _G2_JOINT_ACTOR_ID)
                .values(
                    membership_type=membership_type,
                    membership_expires_at=expires_at,
                )
            )
        replay = _post(
            system,
            "/api/chat/image",
            body=request_body,
            key="chat-media-original-replay-01",
        )
        assert replay.status_code == 200
        assert replay.json() == original.json()
        fresh = _post(
            system,
            "/api/chat/image",
            body=request_body,
            key="chat-media-new-admission-02",
        )
        assert fresh.status_code == 403
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(select(UserCreditRow.__table__.c.credits)).scalar_one() == 497
    finally:
        system.engine.dispose()


def test_same_key_exact_replay_conflict_and_disabled_model_preserve_original_receipt(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path)
    try:
        request_body = _body("image.single")
        first = _post(system, "/api/chat/image", body=request_body)
        assert first.status_code == 200
        with system.engine.begin() as connection:
            connection.execute(
                update(system_models)
                .where(system_models.c.id == _G2_JOINT_MODEL_ID)
                .values(is_active=False)
            )
        replay = _post(system, "/api/chat/image", body=request_body)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        changed = dict(request_body, prompt="A different request")
        conflict = _post(system, "/api/chat/image", body=changed)
        assert conflict.status_code == 409
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(select(UserCreditRow.__table__.c.credits)).scalar_one() == 497
    finally:
        system.engine.dispose()


def test_commit_acknowledgement_loss_is_confirmed_without_second_stage_or_charge(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path, lose_first_business_commit_ack=True)
    try:
        response = _post(system, "/api/chat/image", body=_body("image.single"))
        assert response.status_code == 200, response.text
        writers = [
            session for session in system.business_sessions
            if int(session.info.get("commit_calls", 0)) > 0
        ]
        assert len(writers) == 1
        assert writers[0].info["commit_calls"] == 1
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(select(UserCreditRow.__table__.c.credits)).scalar_one() == 497
    finally:
        system.engine.dispose()


def test_unconfirmed_fresh_receipt_returns_503_without_replaying_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from haoai_backend.chat_media_admission import http as chat_media_http

    system = _build_system(tmp_path)
    real_lookup = chat_media_http.lookup_committed_receipt
    lookups: list[object] = []

    def return_unconfirmed(session_factory: object, staged: object) -> object:
        result = real_lookup(session_factory, staged)
        assert isinstance(result, AdmissionConfirmed)
        lookups.append(staged)
        if isinstance(staged, AdmissionCommitUnknown):
            return staged
        return AdmissionCommitUnknown(staged=staged)

    monkeypatch.setattr(chat_media_http, "lookup_committed_receipt", return_unconfirmed)
    try:
        response = _post(system, "/api/chat/image", body=_body("image.single"))
        assert response.status_code == 503
        assert len(lookups) == 1
        assert len(system.business_sessions) == 2
        assert system.business_sessions[0].info.get("commit_calls") == 1
        explicit_replay = _post(
            system,
            "/api/chat/image",
            body=_body("image.single"),
        )
        assert explicit_replay.status_code == 200
        assert len(system.business_sessions) == 3
        assert system.business_sessions[-1].info.get("commit_calls", 0) == 0
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(select(UserCreditRow.__table__.c.credits)).scalar_one() == 497
    finally:
        system.engine.dispose()


def test_http_task_repeated_cancel_drains_real_writer_session_close(
    tmp_path: Path,
) -> None:
    """Request cancellation waits for the actual SQLite writer close."""

    close_started = threading.Event()
    close_release = threading.Event()
    close_finished = threading.Event()
    system = _build_system(
        tmp_path,
        business_close_gate=(close_started, close_release, close_finished),
    )
    request_task: asyncio.Task[httpx.Response] | None = None

    async def cancel_request() -> None:
        nonlocal request_task
        headers = {
            "Authorization": f"Bearer {system.token_for()}",
            "Idempotency-Key": "chat-media-cancel-drain-01",
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=system.app, raise_app_exceptions=False
            ),
            base_url=BASE_URL,
        ) as client:
            request_task = asyncio.create_task(
                client.post(
                    "/api/chat/image",
                    headers=headers,
                    json=_body("image.single"),
                )
            )
            assert request_task is not None
            try:
                assert await asyncio.to_thread(close_started.wait, 5)
                writer = system.business_sessions[0]
                assert writer.info.get("commit_calls") == 1
                assert writer.info.get("close_count", 0) == 0

                request_task.cancel()
                await asyncio.sleep(0)
                request_task.cancel()
                await asyncio.sleep(0)
                assert not request_task.done()
                assert writer.info.get("close_count", 0) == 0

                close_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request_task, timeout=5)
                assert await asyncio.to_thread(close_finished.wait, 5)
                assert writer.info.get("close_count") == 1
                assert writer.info.get("observed_closed") is True
            finally:
                close_release.set()
                if request_task is not None and not request_task.done():
                    request_task.cancel()
                    try:
                        await asyncio.wait_for(request_task, timeout=5)
                    except asyncio.CancelledError:
                        pass
                if close_started.is_set():
                    assert await asyncio.to_thread(close_finished.wait, 5)

    main_thread = threading.get_ident()
    try:
        asyncio.run(cancel_request())
        assert len(system.business_sessions) == 1
        assert system.business_sessions[0].info.get("commit_calls") == 1
        assert system.business_sessions[0].info.get("close_count") == 1
        close_thread = next(
            thread_id
            for label, thread_id in system.events
            if label == "close:business"
        )
        assert close_thread != main_thread
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 497
    finally:
        close_release.set()
        system.engine.dispose()
