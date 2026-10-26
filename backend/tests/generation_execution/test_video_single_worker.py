from __future__ import annotations

import hashlib
import json
from contextlib import AbstractAsyncContextManager
from datetime import timezone
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
from haoai_backend.generation_contracts.types import PreparedReference, StoredMedia
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
from haoai_backend.generation_runtime.video_single_assembly import (
    VideoSingleWorkerConfiguration,
    build_video_single_worker_runtime,
)


_VIDEO_BYTES = b"a controlled fake MP4 payload"
_VIDEO_URL = "https://media.example.test/generated/video.mp4"


class _Response:
    headers = {"content-length": "0"}

    def __init__(self, body: bytes) -> None:
        self.status_code = 200
        self._body = body

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self._body


class _HttpClient:
    def __init__(
        self,
        provider: str,
        *,
        query_timeout: bool = False,
        create_without_id: bool = False,
    ) -> None:
        self.provider = provider
        self.query_timeout = query_timeout
        self.create_without_id = create_without_id
        self.calls: list[tuple[str, str, bytes | None]] = []

    async def request(self, method, url, *, content=None, **_kwargs):
        self.calls.append((method, url, content))
        if method == "POST":
            if self.create_without_id:
                return _Response(b"{}")
            external_id = f"{self.provider}-original-task-id"
            body = (
                {"request_id": external_id}
                if self.provider == "xai"
                else {"id": external_id}
            )
            return _Response(json.dumps(body).encode("utf-8"))
        if self.query_timeout:
            raise TimeoutError("controlled provider query timeout")
        return _Response(
            json.dumps(
                {"status": "done", "video": {"url": _VIDEO_URL}}
            ).encode("utf-8")
        )


class _HttpContext(AbstractAsyncContextManager):
    def __init__(self, client: _HttpClient) -> None:
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, traceback):
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
                model="grok-imagine-video",
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


def _admit_video_task(environment, *, key: str) -> tuple[str, str]:
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
                "reference_audios": [
                    "https://ref.example.test/audio-a?ordered=1",
                    "https://ref.example.test/audio-b?ordered=2",
                    "https://ref.example.test/audio-a?ordered=1",
                ],
                "reference_videos": [
                    "https://ref.example.test/video-a?ordered=1",
                    "https://ref.example.test/video-b?ordered=2",
                    "https://ref.example.test/video-a?ordered=1",
                ],
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


@pytest.mark.parametrize("provider", ("xai", "manxiaobai"))
def test_video_worker_creates_queries_original_id_and_stores_video(
    provider: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider=provider)
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(environment, key=f"worker-{provider}")
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
        assert get[1].endswith(f"/v1/videos/{external_id}")
        wire_payload = json.loads(post[2] or b"{}")
        expected_image_urls = [
            "https://ref.example.test/image-a?ordered=1",
            "https://ref.example.test/image-b?ordered=2",
            "https://ref.example.test/image-a?ordered=1",
        ]
        wire_issues = {}
        if provider == "xai":
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


def test_video_worker_rebuilds_runtime_and_queries_original_id_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider="xai")
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(environment, key="worker-query-timeout")
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient("xai", query_timeout=True)
    first_store = _MediaStore()
    first_runtime = _build_runtime(
        environment, first_client, first_store, _PublicFetcher()
    )
    second_client = _HttpClient("xai")
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
        assert "xai-original-task-id" in first_client.calls[1][1]
        assert isinstance(second, GenerationResultReceipt)
        assert [call[0] for call in second_client.calls] == ["GET"]
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


def test_video_worker_does_not_repost_unknown_create_without_provider_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider="xai")
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment, key="worker-unknown-create-no-id"
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient("xai", create_without_id=True)
    first_runtime = _build_runtime(
        environment, first_client, _MediaStore(), _PublicFetcher()
    )
    second_client = _HttpClient("xai")
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


def test_video_worker_does_not_query_cancelled_known_provider_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _worker_environment(monkeypatch, tmp_path, provider="xai")
    request.addfinalizer(environment.engine.dispose)
    task_id, _submission_id = _admit_video_task(
        environment, key="worker-query-timeout-cancelled"
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    first_client = _HttpClient("xai", query_timeout=True)
    first_runtime = _build_runtime(
        environment, first_client, _MediaStore(), _PublicFetcher()
    )
    second_client = _HttpClient("xai")
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
