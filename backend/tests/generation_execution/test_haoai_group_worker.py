from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import timedelta
from uuid import uuid4

import test_haoai_group_polls as group_fixtures
import test_haoai_provider as provider_fixtures
import test_video_single_claims as video_fixtures
from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import PreparedReference, StoredMedia
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
)
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    GenerationResultReceiptRow,
    PolicyStopEventRow,
)
from haoai_backend.generation_runtime.video_single_assembly import (
    VideoSingleWorkerConfiguration,
    build_video_single_worker_runtime,
)
from haoai_backend.media_storage.owned_write_values import (
    OwnedObjectMaterial,
    OwnedObjectPlan,
    OwnedObjectPutResult,
    OwnedObjectVerification,
)

_VIDEO_CONTENT = b"synthetic-owned-video-content"
_PROVIDER_ID = "haoai-group-worker-original-id"
_SOURCE_URL = "https://cdn.example/source-video.mp4"


class _AsyncHttpContext:
    def __init__(self, client) -> None:
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *_args):
        return False


class _RoutingHttpClient:
    def __init__(self, query_responses=None) -> None:
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.query_responses = list(query_responses or ())

    async def request(self, method, url, *, content=None, **_kwargs):
        self.calls.append((method, url, content))
        if method == "POST":
            return provider_fixtures.FakeResponse(
                200,
                json.dumps({"request_id": _PROVIDER_ID}).encode(),
                headers={"Content-Type": "application/json"},
            )
        if method != "GET":
            raise AssertionError(f"unexpected method: {method}")
        if url.endswith("/content"):
            return provider_fixtures.FakeResponse(
                200,
                _VIDEO_CONTENT,
                headers={"Content-Type": "video/mp4"},
            )
        if self.query_responses:
            status_code, body = self.query_responses.pop(0)
            return provider_fixtures.FakeResponse(
                status_code,
                body,
            )
        return provider_fixtures.FakeResponse(
            200,
            json.dumps(
                {"status": "completed", "video_url": _SOURCE_URL}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )


class _OwnedMediaStore:
    def __init__(self) -> None:
        self.prepared_references: list[str] = []
        self.put_count = 0
        self.put_attempt_count = 0
        self.verify_count = 0
        self.remote: dict[str, bytes] = {}
        self.stored: StoredMedia | None = None
        self.verification_results: list[OwnedObjectVerification] = []
        self.before_permit_claim: Callable[[], None] | None = None

    def prepare_reference(self, reference, _capability, _configuration):
        self.prepared_references.append(reference.locator)
        return PreparedReference(
            kind="public_http",
            locator=reference.locator,
            content=None,
            media_type=None,
        )

    def prepare_owned_bytes(
        self,
        source,
        object_policy,
        *,
        owner_ref,
        submission_ref,
        slot,
        provider_result_digest,
    ):
        assert object_policy == {"max_bytes": 50 * 1024 * 1024}
        assert source.content == _VIDEO_CONTENT
        digest = hashlib.sha256(source.content).hexdigest()
        plan = OwnedObjectPlan(
            write_id=uuid4().hex,
            owner_ref=owner_ref,
            submission_ref=submission_ref,
            slot=slot,
            provider_result_digest=provider_result_digest,
            material_sha256=digest,
            media_type=source.media_type,
            size_bytes=len(source.content),
            config_fingerprint="d" * 64,
            config_version=1,
            bucket="video-output",
            object_key=f"haoai/group/{submission_ref}.mp4",
            public_base="https://media.example/owned",
        )
        return OwnedObjectMaterial(plan=plan, content=source.content)

    def put_planned_media(self, material, permit):
        self.put_attempt_count += 1
        if self.before_permit_claim is not None:
            self.before_permit_claim()
        if permit.claim(material.plan):
            self.put_count += 1
            self.remote[material.plan.object_key] = material.content
        return OwnedObjectPutResult("stored")

    def verify_planned_media(self, plan):
        self.verify_count += 1
        if self.verification_results:
            result = self.verification_results.pop(0)
            if result.status != "verified":
                return result
        content = self.remote.get(plan.object_key)
        if content is None:
            return OwnedObjectVerification("not_found_pending")
        digest = hashlib.sha256(content).hexdigest()
        self.stored = StoredMedia(
            locator=f"{plan.public_base}/{plan.object_key}",
            content_digest=digest,
            media_type=plan.media_type,
            size_bytes=len(content),
        )
        return OwnedObjectVerification(
            "verified",
            stored_media=self.stored,
            observed_sha256=digest,
            observed_size_bytes=len(content),
        )


def test_video_single_worker_runs_haoai_group_create_poll_and_owned_receipt(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-worker-owned-chain-001"
        )
        clock = [video_fixtures._NOW]
        client = _RoutingHttpClient()
        media_store = _OwnedMediaStore()
        credentials = video_fixtures.joint_fixtures.build_credential_registry(
            environment.capabilities
        )
        runtime_config = GenerationRuntimeConfig(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            id_factory=lambda: f"group-worker-{uuid4().hex[:16]}",
            http_client_factory=lambda: _AsyncHttpContext(client),
            credential_resolver=credentials.resolve_original_credential,
            provider_factories=(),
            media_store_factory=lambda: media_store,
            local_muse_staging_dir=None,
            public_media_fetcher_factory=lambda: object(),
            bound_provider_content_fetcher_factory=lambda: object(),
            local_muse_capability_factory=lambda: object(),
            r2_storage_factory=lambda: object(),
        )
        runtime = build_video_single_worker_runtime(
            VideoSingleWorkerConfiguration(
                runtime=runtime_config,
                registry_capabilities=environment.capabilities,
                poll_waiter=lambda _signal, _seconds: False,
            )
        )

        first = runtime.run_claimed_once("video-single")
        assert first == "pending"
        assert [method for method, _url, _body in client.calls] == ["POST"]
        assert client.calls[0][1].endswith("/openai/v1/videos")
        assert json.loads(client.calls[0][2] or b"{}")["model"]

        clock[0] += timedelta(seconds=5)
        second = runtime.run_claimed_once("video-single")

        assert isinstance(second, GenerationResultReceipt)
        assert second.task_id == task_id
        assert [method for method, _url, _body in client.calls] == [
            "POST",
            "GET",
            "GET",
        ]
        assert _PROVIDER_ID in client.calls[1][1]
        assert client.calls[2][1].endswith(
            f"/openai/v1/videos/{_PROVIDER_ID}/content"
        )
        assert _SOURCE_URL not in " ".join(url for _method, url, _body in client.calls)
        assert media_store.put_count == 1
        assert media_store.verify_count == 1

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__)
            ).mappings().one()
            receipts = reader.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.task_id == task_id
                )
            ).mappings().all()
            usage = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == group["unit_id"],
                    CreditLogRow.__table__.c.type == "usage",
                )
            ).mappings().all()
        assert task["status"] == "completed"
        assert json.loads(task["result"])["url"] == media_store.stored.locator
        assert group["status"] == "succeeded"
        assert len(receipts) == 1
        assert len(usage) == 1
    finally:
        environment.engine.dispose()


def test_video_single_worker_recovers_original_owned_plan_after_new_poll_observation(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-worker-owned-recovery-001"
        )
        clock = [video_fixtures._NOW]
        client = _RoutingHttpClient()
        media_store = _OwnedMediaStore()
        media_store.verification_results.append(
            OwnedObjectVerification("not_found_pending")
        )
        credentials = video_fixtures.joint_fixtures.build_credential_registry(
            environment.capabilities
        )
        runtime_config = GenerationRuntimeConfig(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            id_factory=lambda: f"group-recovery-{uuid4().hex[:16]}",
            http_client_factory=lambda: _AsyncHttpContext(client),
            credential_resolver=credentials.resolve_original_credential,
            provider_factories=(),
            media_store_factory=lambda: media_store,
            local_muse_staging_dir=None,
            public_media_fetcher_factory=lambda: object(),
            bound_provider_content_fetcher_factory=lambda: object(),
            local_muse_capability_factory=lambda: object(),
            r2_storage_factory=lambda: object(),
        )
        runtime = build_video_single_worker_runtime(
            VideoSingleWorkerConfiguration(
                runtime=runtime_config,
                registry_capabilities=environment.capabilities,
                poll_waiter=lambda _signal, _seconds: False,
            )
        )

        first = runtime.run_claimed_once("video-single")
        assert first == "pending"
        clock[0] += timedelta(seconds=5)
        second = runtime.run_claimed_once("video-single")
        assert second == "pending"
        assert media_store.put_count == 1
        assert media_store.verify_count == 1

        clock[0] += timedelta(seconds=5)
        third = runtime.run_claimed_once("video-single")

        assert isinstance(third, GenerationResultReceipt), (
            third,
            client.calls,
            media_store.put_attempt_count,
            media_store.verify_count,
        )
        assert third.task_id == task_id
        assert [method for method, _url, _body in client.calls] == [
            "POST",
            "GET",
            "GET",
        ]
        assert media_store.put_attempt_count == 1
        assert media_store.put_count == 1
        assert media_store.verify_count == 2
        with Session(environment.engine) as reader:
            receipts = reader.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.task_id == task_id
                )
            ).mappings().all()
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        assert len(receipts) == 1
        assert task["status"] == "completed"
    finally:
        environment.engine.dispose()


def test_video_single_worker_rechecks_fence_at_actual_owned_put_permission(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-worker-owned-permit-fence-001"
        )
        clock = [video_fixtures._NOW]
        client = _RoutingHttpClient()
        media_store = _OwnedMediaStore()

        def revoke_claim_before_permit() -> None:
            with environment.engine.begin() as connection:
                connection.execute(
                    AITaskRow.__table__
                    .update()
                    .where(AITaskRow.__table__.c.id == task_id)
                    .values(claim_token="revoked-before-owned-put")
                )

        media_store.before_permit_claim = revoke_claim_before_permit
        credentials = video_fixtures.joint_fixtures.build_credential_registry(
            environment.capabilities
        )
        runtime_config = GenerationRuntimeConfig(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            id_factory=lambda: f"group-permit-{uuid4().hex[:16]}",
            http_client_factory=lambda: _AsyncHttpContext(client),
            credential_resolver=credentials.resolve_original_credential,
            provider_factories=(),
            media_store_factory=lambda: media_store,
            local_muse_staging_dir=None,
            public_media_fetcher_factory=lambda: object(),
            bound_provider_content_fetcher_factory=lambda: object(),
            local_muse_capability_factory=lambda: object(),
            r2_storage_factory=lambda: object(),
        )
        runtime = build_video_single_worker_runtime(
            VideoSingleWorkerConfiguration(
                runtime=runtime_config,
                registry_capabilities=environment.capabilities,
                poll_waiter=lambda _signal, _seconds: False,
            )
        )

        assert runtime.run_claimed_once("video-single") == "pending"
        clock[0] += timedelta(seconds=5)
        outcome = runtime.run_claimed_once("video-single")

        assert outcome in {"pending", "ownership_lost"}
        assert media_store.put_attempt_count == 1
        assert media_store.put_count == 0
        assert media_store.remote == {}
        with Session(environment.engine) as reader:
            receipts = reader.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.task_id == task_id
                )
            ).mappings().all()
        assert receipts == []
    finally:
        environment.engine.dispose()


def test_video_single_group_non_reference_provider_failure_refunds_immediately(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-worker-known-failure-001"
        )
        clock = [video_fixtures._NOW]
        client = _RoutingHttpClient(
            [(200, b'{"status":"failed","error":{"message":"provider rejected"}}')]
        )
        media_store = _OwnedMediaStore()
        credentials = video_fixtures.joint_fixtures.build_credential_registry(
            environment.capabilities
        )
        runtime = build_video_single_worker_runtime(
            VideoSingleWorkerConfiguration(
                runtime=GenerationRuntimeConfig(
                    session_factory=lambda: Session(environment.engine),
                    db_clock=lambda: clock[0],
                    id_factory=lambda: f"group-known-failure-{uuid4().hex[:16]}",
                    http_client_factory=lambda: _AsyncHttpContext(client),
                    credential_resolver=credentials.resolve_original_credential,
                    provider_factories=(),
                    media_store_factory=lambda: media_store,
                    local_muse_staging_dir=None,
                    public_media_fetcher_factory=lambda: object(),
                    bound_provider_content_fetcher_factory=lambda: object(),
                    local_muse_capability_factory=lambda: object(),
                    r2_storage_factory=lambda: object(),
                ),
                registry_capabilities=environment.capabilities,
                poll_waiter=lambda _signal, _seconds: False,
            )
        )

        assert runtime.run_claimed_once("video-single") == "pending"
        clock[0] += timedelta(seconds=5)
        outcome = runtime.run_claimed_once("video-single")

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id == unit["id"]
                )
            ).mappings().one()
            usage = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                    CreditLogRow.__table__.c.type == "usage",
                )
            ).mappings().all()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.business_key == f"refund:{unit['id']}"
                )
            ).mappings().all()

        assert task["status"] == "failed", (outcome, client.calls, group)
        assert task["billing_status"] == "refunded"
        assert unit["result_status"] == "failed"
        assert unit["billing_status"] == "refunded"
        assert unit["refunded_amount"] == unit["quoted_amount"]
        assert group["status"] == "failed"
        assert group["terminal_reason"] == "provider_failed"
        assert outcome is None
        assert [method for method, _url, _body in client.calls] == ["POST", "GET"]
        assert len(usage) == 1
        assert len(refunds) == 1
        assert media_store.put_count == 0
    finally:
        environment.engine.dispose()


def test_video_single_group_third_reference_failure_settles_once_without_repost(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-worker-final-reference-failure-001"
        )
        clock = [video_fixtures._NOW]
        failed_reference = (
            b'{"status":"failed","error":{"message":'
            b'"failed to download the provided image"}}'
        )
        client = _RoutingHttpClient([(200, failed_reference)] * 3)
        media_store = _OwnedMediaStore()
        credentials = video_fixtures.joint_fixtures.build_credential_registry(
            environment.capabilities
        )
        runtime = build_video_single_worker_runtime(
            VideoSingleWorkerConfiguration(
                runtime=GenerationRuntimeConfig(
                    session_factory=lambda: Session(environment.engine),
                    db_clock=lambda: clock[0],
                    id_factory=lambda: f"grf-{uuid4().hex[:20]}",
                    http_client_factory=lambda: _AsyncHttpContext(client),
                    credential_resolver=credentials.resolve_original_credential,
                    provider_factories=(),
                    media_store_factory=lambda: media_store,
                    local_muse_staging_dir=None,
                    public_media_fetcher_factory=lambda: object(),
                    bound_provider_content_fetcher_factory=lambda: object(),
                    local_muse_capability_factory=lambda: object(),
                    r2_storage_factory=lambda: object(),
                ),
                registry_capabilities=environment.capabilities,
                poll_waiter=lambda _signal, _seconds: False,
            )
        )

        outcomes = [runtime.run_claimed_once("video-single")]
        clock[0] += timedelta(seconds=5)
        outcomes.append(runtime.run_claimed_once("video-single"))
        for _ in range(2):
            clock[0] += timedelta(seconds=3)
            outcomes.append(runtime.run_claimed_once("video-single"))
            clock[0] += timedelta(seconds=5)
            outcomes.append(runtime.run_claimed_once("video-single"))
        before_restart_posts = sum(method == "POST" for method, *_ in client.calls)
        clock[0] += timedelta(seconds=5)
        runtime.run_claimed_once("video-single")
        after_restart_posts = sum(method == "POST" for method, *_ in client.calls)

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id == unit["id"]
                )
            ).mappings().one()
            usage = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                    CreditLogRow.__table__.c.type == "usage",
                )
            ).mappings().all()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.business_key == f"refund:{unit['id']}"
                )
            ).mappings().all()

        assert before_restart_posts == after_restart_posts == 3
        assert [method for method, _url, _body in client.calls].count("GET") == 3
        assert task["status"] == "failed", (outcomes, client.calls, group)
        assert task["billing_status"] == "refunded"
        assert unit["result_status"] == "failed"
        assert unit["refunded_amount"] == unit["quoted_amount"]
        assert group["active_attempt"] == 2
        assert group["status"] == "failed"
        assert group["terminal_reason"] == "provider_failed"
        assert len(usage) == len(refunds) == 1
        assert all(outcome != "completed" for outcome in outcomes)
    finally:
        environment.engine.dispose()
