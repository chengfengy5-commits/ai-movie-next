from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import test_admission_staging as joint_fixtures
import test_video_single_claims as video_fixtures
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_catalog.tables import model_configs, system_models
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    CommittedIntent,
    PreparedReference,
    StoredMedia,
)
from haoai_backend.generation_core.admission_receipts import (
    AdmissionConfirmed,
    AdmissionStaged,
    lookup_staged_admission,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    TaskSubmissionRow,
)
import haoai_backend.generation_providers.video.aistarslab_provider as aistarslab_provider
from haoai_backend.generation_runtime.video_single_assembly import (
    VideoSingleWorkerConfiguration,
    build_video_single_worker_runtime,
)


_VIDEO_BYTES = b"a controlled fake MP4 payload"
_VIDEO_URL = "https://media.example.test/generated/video.mp4"


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        status_code: int = 200,
        chunk_delay: float = 0.0,
    ) -> None:
        self.headers = {"content-length": str(len(body))}
        self.status_code = status_code
        self._body = body
        self._chunk_delay = chunk_delay

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        if self._chunk_delay <= 0:
            yield self._body
            return
        for offset in range(0, len(self._body), max(1, len(self._body) // 3)):
            await asyncio.sleep(self._chunk_delay)
            yield self._body[offset : offset + max(1, len(self._body) // 3)]


class _HttpClient:
    def __init__(
        self,
        provider: str,
        *,
        query_timeout: bool = False,
        create_without_id: bool = False,
        create_timeout: bool = False,
        create_http_status: int | None = None,
        create_response_code: int = 0,
        create_body_chunk_delay: float = 0.0,
        query_body_chunk_delay: float = 0.0,
        context_cleanup_delay: float = 0.0,
    ) -> None:
        self.provider = provider
        self.query_timeout = query_timeout
        self.create_without_id = create_without_id
        self.create_timeout = create_timeout
        self.create_http_status = create_http_status
        self.create_response_code = create_response_code
        self.create_body_chunk_delay = create_body_chunk_delay
        self.query_body_chunk_delay = query_body_chunk_delay
        self.context_cleanup_delay = context_cleanup_delay
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.request_headers: list[dict[str, str]] = []
        self.request_options: list[dict[str, object]] = []

    async def request(self, method, url, *, content=None, **_kwargs):
        self.calls.append((method, url, content))
        self.request_headers.append(dict(_kwargs.get("headers") or {}))
        self.request_options.append(
            {
                "timeout_seconds": _kwargs.get("timeout_seconds"),
                "follow_redirects": _kwargs.get("follow_redirects"),
            }
        )
        if method == "POST":
            if self.create_timeout:
                raise TimeoutError("controlled provider create timeout")
            if self.create_http_status is not None:
                return _Response(b"", status_code=self.create_http_status)
            if self.create_without_id:
                body = {"code": 0, "data": {}} if self.provider == "aistarslab" else {}
                return _Response(
                    json.dumps(body).encode("utf-8"),
                    chunk_delay=self.create_body_chunk_delay,
                )
            external_id = f"{self.provider}-original-task-id"
            if self.provider == "aistarslab":
                body = {
                    "code": self.create_response_code,
                    "data": {"taskId": external_id},
                }
            else:
                body = (
                    {"request_id": external_id}
                    if self.provider == "xai"
                    else {"id": external_id}
                )
            return _Response(
                json.dumps(body).encode("utf-8"),
                chunk_delay=self.create_body_chunk_delay,
            )
        if self.query_timeout:
            raise TimeoutError("controlled provider query timeout")
        body = (
            {"code": 0, "data": {"status": 3, "outputs": [_VIDEO_URL]}}
            if self.provider == "aistarslab"
            else {"status": "done", "video": {"url": _VIDEO_URL}}
        )
        return _Response(
            json.dumps(body).encode("utf-8"),
            chunk_delay=self.query_body_chunk_delay,
        )


class _HttpContext(AbstractAsyncContextManager):
    def __init__(self, client: _HttpClient) -> None:
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, traceback):
        if self._client.context_cleanup_delay:
            await asyncio.sleep(self._client.context_cleanup_delay)
        return False


class _MediaStore:
    def __init__(self) -> None:
        self.prepared_locators: list[str] = []
        self.stored: list[StoredMedia] = []

    def prepare_reference(self, reference, _capability, _configuration):
        assert reference.kind == "public_http"
        assert isinstance(reference.locator, str)
        self.prepared_locators.append(reference.locator)
        return PreparedReference(
            kind="public_http",
            locator=reference.locator,
            content=None,
            media_type=None,
        )

    def store_bytes_or_provider_content(self, reference, object_policy):
        assert object_policy == {"max_bytes": 50 * 1024 * 1024}
        assert reference.kind == "bytes"
        assert reference.content == _VIDEO_BYTES
        stored = StoredMedia(
            locator="https://media.example.test/managed/video.mp4",
            content_digest=hashlib.sha256(reference.content).hexdigest(),
            media_type=reference.media_type or "",
            size_bytes=len(reference.content),
        )
        self.stored.append(stored)
        return stored


class _PublicFetcher:
    def __init__(self) -> None:
        self.locators: list[str] = []

    async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
        assert max_bytes == 50 * 1024 * 1024
        assert timeout_seconds == 600
        self.locators.append(reference.locator)
        return PreparedReference(
            kind="bytes",
            locator=None,
            content=_VIDEO_BYTES,
            media_type="video/mp4",
        )


def _worker_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    provider: str,
):
    monkeypatch.setattr(joint_fixtures, "_G2_JOINT_PROVIDER", provider)
    insert_catalog_model = joint_fixtures._joint_insert_catalog_model

    def insert_video_model(connection, source: ModelSource) -> None:
        insert_catalog_model(connection, source)
        table = system_models if source is ModelSource.SYSTEM else model_configs
        connection.execute(
            update(table)
            .where(table.c.id == joint_fixtures._G2_JOINT_MODEL_ID)
            .values(
                category="video",
                model=(
                    "47:seedance-2.0"
                    if provider == "aistarslab"
                    else "grok-imagine-video"
                ),
                base_url=(
                    "https://provider.example.test/openapi"
                    if provider == "aistarslab"
                    else "https://provider.example.test/v2"
                ),
                params=json.dumps(
                    {
                        "billing_mode": "per_second",
                        "per_second_cost": 1,
                        "duration": {"enabled": True, "default": 5, "options": [5]},
                        "resolution": {
                            "enabled": True,
                            "default": "720p",
                            "options": ["720p"],
                        },
                        "image_size": {
                            "enabled": True,
                            "default": "16:9",
                            "options": ["16:9"],
                        },
                    }
                ),
            )
        )

    monkeypatch.setattr(
        joint_fixtures,
        "_joint_insert_catalog_model",
        insert_video_model,
    )
    return joint_fixtures._joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation="video.single",
    )


def _admit_video_task(
    environment,
    *,
    key: str,
    reference_audios: tuple[str, ...] = (),
    reference_videos: tuple[str, ...] = (),
) -> tuple[str, str]:
    caller = joint_fixtures._joint_caller(environment.engine)
    try:
        staged = joint_fixtures._joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=key,
            request={
                "model_id": joint_fixtures._G2_JOINT_MODEL_ID,
                "prompt": "A quiet room at sunset",
                "target": {"kind": "standalone"},
                "size": "16:9",
                "duration": 5,
                "resolution": "720p",
                "reference_images": [
                    "https://ref.example.test/image-a?ordered=1",
                    "https://ref.example.test/image-b?ordered=2",
                    "https://ref.example.test/image-a?ordered=1",
                ],
                "reference_audios": list(reference_audios),
                "reference_videos": list(reference_videos),
            },
        )
        assert isinstance(staged, AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        caller.commit()
        with Session(environment.engine) as reader:
            confirmed = lookup_staged_admission(reader, staged)
            parent = reader.execute(
                select(TaskSubmissionRow.__table__).where(
                    TaskSubmissionRow.__table__.c.id == staged.submission_id
                )
            ).mappings().one_or_none()
        assert isinstance(confirmed, AdmissionConfirmed)
        assert parent is not None and parent["task_ids"] == [task_id]
        return task_id, staged.submission_id
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()


def _disable_aistarslab_fixture_credential(environment) -> None:
    scope = joint_fixtures.CredentialScope(
        "system",
        joint_fixtures._G2_JOINT_MODEL_ID,
        "system",
        "aistarslab",
    )
    with Session(environment.engine) as reader:
        binding = reader.execute(
            select(joint_fixtures.credential_bindings).where(
                joint_fixtures.credential_bindings.c.source == "system",
                joint_fixtures.credential_bindings.c.row_id
                == joint_fixtures._G2_JOINT_MODEL_ID,
                joint_fixtures.credential_bindings.c.owner_or_system == "system",
                joint_fixtures.credential_bindings.c.provider == "aistarslab",
            )
        ).mappings().one()
    authority = environment.capabilities.proof_authority
    session_factory = environment.capabilities.read_session_factory
    assert authority is not None and session_factory is not None
    attempt = joint_fixtures.make_server_attempt(
        authority,
        attempt_id="aistarslab-disable-before-provider-call",
        actor_id=joint_fixtures._G2_JOINT_ACTOR_ID,
        operation="disable",
        scope=scope,
        expected_revision=int(binding["revision"]),
        predecessor_version=int(binding["current_version"]),
    )
    registry = joint_fixtures.build_credential_registry(environment.capabilities)
    with joint_fixtures._joint_allow_credential_rotation(environment.engine):
        with session_factory() as caller:
            disabled = registry.stage_disable_binding(
                caller,
                joint_fixtures.TrustedActor(joint_fixtures._G2_JOINT_ACTOR_ID),
                scope,
                int(binding["revision"]),
                attempt,
            )
            assert isinstance(disabled, joint_fixtures.RegistrationStaged)
            caller.commit()


def _runtime_configuration(environment, client, media_store, fetcher):
    id_value = 0

    def new_id() -> str:
        nonlocal id_value
        id_value += 1
        return f"video-worker-id-{id_value}"

    registry = joint_fixtures.build_credential_registry(environment.capabilities)
    configuration = GenerationRuntimeConfig(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: joint_fixtures._G2_JOINT_NOW.replace(tzinfo=timezone.utc),
        id_factory=new_id,
        http_client_factory=lambda: _HttpContext(client),
        credential_resolver=registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=lambda: media_store,
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: fetcher,
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
    )
    return configuration


def _build_runtime(environment, client, media_store, fetcher):
    return build_video_single_worker_runtime(
        VideoSingleWorkerConfiguration(
            runtime=_runtime_configuration(environment, client, media_store, fetcher),
            registry_capabilities=environment.capabilities,
            poll_waiter=lambda _signal, _seconds: False,
        )
    )


def test_aistarslab_request_budget_uses_original_intent_deadline() -> None:
    now = datetime(2026, 10, 28, 2, 30, tzinfo=timezone.utc)
    clock = [now]
    intent = CommittedIntent(
        step_id="original-step",
        attempt_id="original-attempt",
        external_submission_id="original-submission",
        provider="aistarslab",
        model_id="original-model",
        request_digest="a" * 64,
        snapshot_ref="original-snapshot",
        committed_at=now - timedelta(minutes=1),
        first_intent_at=now - timedelta(minutes=1),
        deadline_at=now + timedelta(seconds=4),
    )
    provider = object.__new__(aistarslab_provider.AistarslabVideoProvider)
    provider._clock = lambda: clock[0]
    provider._intent = intent

    assert provider._remaining_request_timeout(120.0) == 4.0
    assert provider._remaining_request_timeout(3.0) == 3.0
    clock[0] += timedelta(seconds=3)
    assert provider._remaining_request_timeout(120.0) == 1.0


@pytest.mark.parametrize("provider", ("xai", "manxiaobai", "aistarslab"))
def test_video_worker_creates_queries_original_id_and_stores_video(
    provider: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider=provider)
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment,
        key=f"worker-{provider}",
        reference_audios=(
            (
                "https://ref.example.test/audio-a?ordered=1",
                "https://ref.example.test/audio-b?ordered=2",
                "https://ref.example.test/audio-a?ordered=1",
            )
            if provider == "aistarslab"
            else ()
        ),
        reference_videos=(
            (
                "https://ref.example.test/video-a?ordered=1",
                "https://ref.example.test/video-b?ordered=2",
                "https://ref.example.test/video-a?ordered=1",
            )
            if provider == "aistarslab"
            else ()
        ),
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    client = _HttpClient(provider)
    media_store = _MediaStore()
    fetcher = _PublicFetcher()
    runtime = _build_runtime(environment, client, media_store, fetcher)
    try:
        outcome = runtime.run_claimed_once("video-single")

        assert isinstance(outcome, GenerationResultReceipt), {
            "outcome": outcome,
            "provider_calls": [(method, url) for method, url, _ in client.calls],
            "prepared_references": len(media_store.prepared_locators),
            "fetched_media": fetcher.locators,
            "stored_media": len(media_store.stored),
        }
        assert len(client.calls) == 2
        post, get = client.calls
        assert post[0] == "POST"
        assert get[0] == "GET"
        external_id = f"{provider}-original-task-id"
        assert external_id in get[1]
        if provider == "aistarslab":
            assert get[1].endswith(
                f"/openapi/generation/status?taskId={external_id}"
            )
        else:
            assert get[1].endswith(f"/v1/videos/{external_id}")
        wire_payload = json.loads(post[2] or b"{}")
        expected_image_urls = [
            "https://ref.example.test/image-a?ordered=1",
            "https://ref.example.test/image-b?ordered=2",
            "https://ref.example.test/image-a?ordered=1",
        ]
        wire_issues = {}
        if provider == "aistarslab":
            expected_audio_urls = [
                "https://ref.example.test/audio-a?ordered=1",
                "https://ref.example.test/audio-b?ordered=2",
                "https://ref.example.test/audio-a?ordered=1",
            ]
            expected_video_urls = [
                "https://ref.example.test/video-a?ordered=1",
                "https://ref.example.test/video-b?ordered=2",
                "https://ref.example.test/video-a?ordered=1",
            ]
            assert wire_payload == {
                "channel": "47",
                "model": "seedance-2.0",
                "prompt": (
                    "A quiet room at sunset @图片1 @图片2 @图片3 "
                    "@音频1 @音频2 @音频3 @视频1 @视频2 @视频3"
                ),
                "aspectRatio": "16:9",
                "quality": "720p",
                "duration": 5,
                "mode": "image2video",
                "inputImages": expected_image_urls,
                "inputAudios": expected_audio_urls,
                "inputVideos": expected_video_urls,
            }
        elif provider == "xai":
            expected_reference_images = [
                {"url": locator} for locator in expected_image_urls
            ]
            if wire_payload.get("reference_images") != expected_reference_images:
                wire_issues["reference_images"] = wire_payload.get("reference_images")
            for key, expected in (
                ("aspect_ratio", "16:9"),
                ("duration", 5),
                ("resolution", "720p"),
            ):
                if wire_payload.get(key) != expected:
                    wire_issues[key] = wire_payload.get(key)
        elif wire_payload.get("input_reference") != expected_image_urls:
            wire_issues["input_reference"] = wire_payload.get("input_reference")
        assert not wire_issues, {"provider": provider, "wire_issues": wire_issues}
        if provider == "aistarslab":
            assert media_store.prepared_locators == [
                *expected_image_urls,
                *expected_audio_urls,
                *expected_video_urls,
            ]
            assert client.request_headers[0] == {
                "Authorization": "Bearer joint-fixture-provider-secret",
                "Content-Type": "application/json",
            }
            assert client.request_headers[1] == {
                "Authorization": "Bearer joint-fixture-provider-secret"
            }
            assert client.request_options == [
                {"timeout_seconds": 120.0, "follow_redirects": False},
                {"timeout_seconds": 30.0, "follow_redirects": False},
            ]
        else:
            assert media_store.prepared_locators == expected_image_urls
        assert fetcher.locators == [_VIDEO_URL]
        assert len(media_store.stored) == 1
        assert outcome.task_id == task_id
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        assert task["status"] == "completed"
        assert submission["submission_status"] == "completed"
        assert submission["external_task_id"] == external_id
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    finally:
        runtime.shutdown("test_complete")


@pytest.mark.parametrize("provider", ("xai", "manxiaobai", "haoai"))
@pytest.mark.parametrize("reference_kind", ("reference_audios", "reference_videos"))
def test_unsupported_video_references_are_refused_before_admission(
    provider: str,
    reference_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider=provider)
    request.addfinalizer(environment.engine.dispose)
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    caller = joint_fixtures._joint_caller(environment.engine)
    payload = {
        "model_id": joint_fixtures._G2_JOINT_MODEL_ID,
        "prompt": "A quiet room at sunset",
        "target": {"kind": "standalone"},
        "size": "16:9",
        "duration": 5,
        "resolution": "720p",
        "reference_images": [],
        "reference_audios": [],
        "reference_videos": [],
    }
    payload[reference_kind] = [f"https://ref.example.test/{reference_kind}/unsupported"]
    try:
        result = joint_fixtures._joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=f"unsupported-{provider}-{reference_kind}",
            request=payload,
        )
        assert isinstance(result, joint_fixtures.AdmissionRefusal)
        assert result.status_code == 422
        assert result.code == "catalog_selection_failed"
        assert caller.execute(select(AITaskRow.__table__)).all() == []
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()
    assert video_fixtures._financial_snapshot(environment.engine) == before_financial


@pytest.mark.parametrize(
    ("field_name", "count"),
    (("reference_images", 10), ("reference_audios", 6), ("reference_videos", 6)),
)
def test_aistarslab_reference_limits_are_refused_before_admission(
    field_name: str,
    count: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider="aistarslab")
    request.addfinalizer(environment.engine.dispose)
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    caller = joint_fixtures._joint_caller(environment.engine)
    payload = {
        "model_id": joint_fixtures._G2_JOINT_MODEL_ID,
        "prompt": "A quiet room at sunset",
        "target": {"kind": "standalone"},
        "size": "16:9",
        "duration": 5,
        "resolution": "720p",
        "reference_images": [],
        "reference_audios": [],
        "reference_videos": [],
    }
    payload[field_name] = [
        f"https://ref.example.test/{field_name}/{index}"
        for index in range(count)
    ]
    try:
        result = joint_fixtures._joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=f"aistarslab-limit-{field_name}",
            request=payload,
        )
        if field_name == "reference_images":
            assert isinstance(result, joint_fixtures.AdmissionRefusal)
            assert result.status_code == 422
            assert result.code == "catalog_selection_failed"
        else:
            assert isinstance(result, joint_fixtures.AdmissionStageRefusal)
            assert result.code == "invalid_context"
        assert caller.execute(select(AITaskRow.__table__)).all() == []
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()
    assert video_fixtures._financial_snapshot(environment.engine) == before_financial


@pytest.mark.parametrize("provider", ("xai", "aistarslab"))
def test_video_worker_rebuilds_runtime_and_queries_original_id_after_timeout(
    provider: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider=provider)
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment,
        key=f"worker-query-timeout-{provider}",
        reference_audios=(
            ("https://ref.example.test/audio?after-timeout=1",)
            if provider == "aistarslab"
            else ()
        ),
        reference_videos=(
            ("https://ref.example.test/video?after-timeout=1",)
            if provider == "aistarslab"
            else ()
        ),
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient(provider, query_timeout=True)
    first_store = _MediaStore()
    first_runtime = _build_runtime(
        environment, first_client, first_store, _PublicFetcher()
    )
    second_client = _HttpClient(provider)
    second_store = _MediaStore()
    second_fetcher = _PublicFetcher()
    second_runtime = None
    try:
        first = first_runtime.run_claimed_once("video-single")
        first_runtime.shutdown("test_restart")
        with Session(environment.engine) as reader:
            task_before = reader.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == task_id
                )
            ).mappings().one()
            unit_before = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission_before = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        original = {
            "generation": task_before["execution_generation"],
            "first_intent_at": unit_before["first_intent_at"],
            "deadline_at": unit_before["deadline_at"],
            "submission_id": submission_before["id"],
            "external_task_id": submission_before["external_task_id"],
            "submitted_at": submission_before["submitted_at"],
            "request_snapshot": submission_before["request_snapshot"],
        }
        second_runtime = _build_runtime(
            environment, second_client, second_store, second_fetcher
        )
        second = second_runtime.run_claimed_once("video-single")

        assert first == "pending"
        assert [call[0] for call in first_client.calls] == ["POST", "GET"]
        external_id = f"{provider}-original-task-id"
        assert external_id in first_client.calls[1][1]
        assert isinstance(second, GenerationResultReceipt)
        assert [call[0] for call in second_client.calls] == ["GET"]
        if provider == "aistarslab":
            assert second_client.calls[0][1].endswith(
                "/openapi/generation/status?taskId=aistarslab-original-task-id"
            )
        else:
            assert second_client.calls[0][1].endswith(
                "/v1/videos/xai-original-task-id"
            )
        assert second_store.prepared_locators == []
        assert second_fetcher.locators == [_VIDEO_URL]
        assert len(second_store.stored) == 1
        assert second.task_id == task_id
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        assert task["status"] == "completed"
        assert task["execution_generation"] == original["generation"] + 1
        assert unit["result_status"] == "succeeded"
        assert unit["first_intent_at"] == original["first_intent_at"]
        assert unit["deadline_at"] == original["deadline_at"]
        assert submission["id"] == original["submission_id"]
        assert submission["submission_status"] == "completed"
        assert submission["external_task_id"] == original["external_task_id"]
        assert submission["submitted_at"] == original["submitted_at"]
        assert submission["request_snapshot"] == original["request_snapshot"]
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    finally:
        first_runtime.shutdown("test_complete")
        if second_runtime is not None:
            second_runtime.shutdown("test_complete")


@pytest.mark.parametrize("provider", ("xai", "aistarslab"))
@pytest.mark.parametrize("create_outcome", ("missing_id", "timeout"))
def test_video_worker_does_not_repost_unknown_create_without_provider_id(
    provider: str,
    create_outcome: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider=provider)
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment, key=f"worker-unknown-create-no-id-{provider}-{create_outcome}"
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient(
        provider,
        create_without_id=create_outcome == "missing_id",
        create_timeout=create_outcome == "timeout",
    )
    first_runtime = _build_runtime(
        environment, first_client, _MediaStore(), _PublicFetcher()
    )
    second_client = _HttpClient(provider)
    second_runtime = _build_runtime(
        environment, second_client, _MediaStore(), _PublicFetcher()
    )
    try:
        assert first_runtime.run_claimed_once("video-single") == "pending"
        first_runtime.shutdown("test_restart")
        assert [call[0] for call in first_client.calls] == ["POST"]
        assert second_runtime.run_claimed_once("video-single") == "pending"
        assert second_client.calls == []
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        assert task["status"] == "reconciling"
        assert unit["result_status"] == "unknown"
        assert submission["submission_status"] == "unknown"
        assert submission["external_task_id"] is None
        assert submission["submitted_at"] is None
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    finally:
        first_runtime.shutdown("test_complete")
        second_runtime.shutdown("test_complete")


def test_aistarslab_http_408_keeps_create_outcome_unknown_without_repost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider="aistarslab")
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment, key="aistarslab-http-408-unknown"
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient("aistarslab", create_http_status=408)
    first_runtime = _build_runtime(
        environment, first_client, _MediaStore(), _PublicFetcher()
    )
    second_client = _HttpClient("aistarslab")
    second_runtime = _build_runtime(
        environment, second_client, _MediaStore(), _PublicFetcher()
    )
    try:
        assert first_runtime.run_claimed_once("video-single") == "pending"
        first_runtime.shutdown("test_restart")
        assert [call[0] for call in first_client.calls] == ["POST"]
        assert second_runtime.run_claimed_once("video-single") == "pending"
        assert second_client.calls == []
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        assert task["status"] == "reconciling"
        assert unit["result_status"] == "unknown"
        assert submission["submission_status"] == "unknown"
        assert submission["external_task_id"] is None
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    finally:
        first_runtime.shutdown("test_complete")
        second_runtime.shutdown("test_complete")


def test_aistarslab_business_rejection_keeps_definitive_failure_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider="aistarslab")
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment,
        key="aistarslab-business-rejection",
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    client = _HttpClient("aistarslab", create_response_code=17)
    runtime = _build_runtime(environment, client, _MediaStore(), _PublicFetcher())
    try:
        assert runtime.run_claimed_once("video-single") is None
        assert [call[0] for call in client.calls] == ["POST"]
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        assert task["status"] == "failed"
        assert unit["result_status"] == "failed"
        assert submission["submission_status"] == "failed"
        after_financial = video_fixtures._financial_snapshot(environment.engine)
        assert after_financial != before_financial
        assert after_financial[0][0][1] >= before_financial[0][0][1]
    finally:
        runtime.shutdown("test_complete")


def test_aistarslab_slow_create_body_is_bounded_and_never_reposted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    monkeypatch.setattr(aistarslab_provider, "CREATE_TIMEOUT_SECONDS", 0.025)
    environment = _worker_environment(monkeypatch, tmp_path, provider="aistarslab")
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment, key="aistarslab-slow-create-body"
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient("aistarslab", create_body_chunk_delay=0.02)
    first_runtime = _build_runtime(
        environment, first_client, _MediaStore(), _PublicFetcher()
    )
    second_client = _HttpClient("aistarslab")
    second_runtime = _build_runtime(
        environment, second_client, _MediaStore(), _PublicFetcher()
    )
    try:
        assert first_runtime.run_claimed_once("video-single") == "pending"
        first_runtime.shutdown("test_restart")
        assert [call[0] for call in first_client.calls] == ["POST"]
        assert second_runtime.run_claimed_once("video-single") == "pending"
        assert second_client.calls == []
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        assert task["status"] == "reconciling"
        assert unit["result_status"] == "unknown"
        assert submission["submission_status"] == "unknown"
        assert submission["external_task_id"] is None
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    finally:
        first_runtime.shutdown("test_complete")
        second_runtime.shutdown("test_complete")


def test_aistarslab_slow_context_cleanup_keeps_create_unknown_without_repost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    monkeypatch.setattr(aistarslab_provider, "CREATE_TIMEOUT_SECONDS", 0.025)
    environment = _worker_environment(monkeypatch, tmp_path, provider="aistarslab")
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment,
        key="aistarslab-slow-cleanup",
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient("aistarslab", context_cleanup_delay=0.05)
    first_runtime = _build_runtime(
        environment, first_client, _MediaStore(), _PublicFetcher()
    )
    second_client = _HttpClient("aistarslab")
    second_runtime = _build_runtime(
        environment, second_client, _MediaStore(), _PublicFetcher()
    )
    try:
        assert first_runtime.run_claimed_once("video-single") == "pending"
        first_runtime.shutdown("test_restart")
        assert [call[0] for call in first_client.calls] == ["POST"]
        assert second_runtime.run_claimed_once("video-single") == "pending"
        assert second_client.calls == []
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        assert task["status"] == "reconciling"
        assert unit["result_status"] == "unknown"
        assert submission["submission_status"] == "unknown"
        assert submission["external_task_id"] is None
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    finally:
        first_runtime.shutdown("test_complete")
        second_runtime.shutdown("test_complete")


def test_aistarslab_slow_query_body_preserves_original_id_and_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    monkeypatch.setattr(aistarslab_provider, "QUERY_TIMEOUT_SECONDS", 0.025)
    environment = _worker_environment(monkeypatch, tmp_path, provider="aistarslab")
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment, key="aistarslab-slow-query-body"
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient("aistarslab", query_body_chunk_delay=0.02)
    first_runtime = _build_runtime(
        environment, first_client, _MediaStore(), _PublicFetcher()
    )
    second_client = _HttpClient("aistarslab")
    second_runtime = _build_runtime(
        environment, second_client, _MediaStore(), _PublicFetcher()
    )
    try:
        assert first_runtime.run_claimed_once("video-single") == "pending"
        first_runtime.shutdown("test_restart")
        with Session(environment.engine) as reader:
            task_before = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_before = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission_before = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        original_deadline = unit_before["deadline_at"]
        original_intent = unit_before["first_intent_at"]
        original_id = submission_before["external_task_id"]
        assert first_client.calls[0][0] == "POST"
        assert first_client.calls[1][0] == "GET"
        assert original_id == "aistarslab-original-task-id"

        second_outcome = second_runtime.run_claimed_once("video-single")
        assert isinstance(second_outcome, GenerationResultReceipt)
        assert [method for method, _url, _body in second_client.calls] == ["GET"]
        assert original_id in second_client.calls[0][1]
        with Session(environment.engine) as reader:
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission_after = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one()
        assert unit_after["deadline_at"] == original_deadline
        assert unit_after["first_intent_at"] == original_intent
        assert submission_after["external_task_id"] == original_id
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    finally:
        first_runtime.shutdown("test_complete")
        second_runtime.shutdown("test_complete")


@pytest.mark.parametrize("provider", ("xai", "aistarslab"))
def test_video_worker_does_not_query_cancelled_known_provider_id(
    provider: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider=provider)
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment, key=f"worker-query-timeout-cancelled-{provider}"
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient(provider, query_timeout=True)
    first_runtime = _build_runtime(
        environment, first_client, _MediaStore(), _PublicFetcher()
    )
    second_client = _HttpClient(provider)
    second_runtime = _build_runtime(
        environment, second_client, _MediaStore(), _PublicFetcher()
    )
    cancelled_at = joint_fixtures._G2_JOINT_NOW.replace(tzinfo=timezone.utc)
    try:
        assert first_runtime.run_claimed_once("video-single") == "pending"
        first_runtime.shutdown("test_cancel")
        with Session(environment.engine) as session, session.begin():
            session.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == task_id)
                .values(user_cancelled_at=cancelled_at)
            )
            session.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.__table__.c.task_id == task_id)
                .values(
                    user_cancelled_at=cancelled_at,
                    cancellation_reason="user_cancelled",
                )
            )
        assert second_runtime.run_claimed_once("video-single") == "pending"
        assert [call[0] for call in first_client.calls] == ["POST", "GET"]
        assert second_client.calls == []
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    finally:
        first_runtime.shutdown("test_complete")
        second_runtime.shutdown("test_complete")


def test_revoked_aistarslab_credential_is_not_sent_to_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider="aistarslab")
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment,
        key="aistarslab-revoked-credential-no-post",
    )
    _disable_aistarslab_fixture_credential(environment)
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    client = _HttpClient("aistarslab")
    runtime = _build_runtime(environment, client, _MediaStore(), _PublicFetcher())
    try:
        runtime.run_claimed_once("video-single")
        assert client.calls == []
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .join(
                    ExecutionStepRow.__table__,
                    ExecutionStepRow.__table__.c.id
                    == ExternalSubmissionRow.__table__.c.step_id,
                )
                .join(
                    BillingUnitRow.__table__,
                    BillingUnitRow.__table__.c.id
                    == ExecutionStepRow.__table__.c.billing_unit_id,
                )
                .where(BillingUnitRow.__table__.c.task_id == task_id)
            ).mappings().one_or_none()
        assert task["status"] != "completed"
        assert submission is None or submission["external_task_id"] is None
        after_financial = video_fixtures._financial_snapshot(environment.engine)
        assert after_financial[0][0][1] >= before_financial[0][0][1]
    finally:
        runtime.shutdown("test_complete")
