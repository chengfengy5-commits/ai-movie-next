from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import timedelta, timezone
from itertools import count
from uuid import uuid4

import test_haoai_group_polls as group_fixtures
import test_haoai_group_worker as group_worker_fixtures
import pytest
import httpx
import test_video_single_claims as video_fixtures
import test_video_single_intents as video_intent_fixtures
import test_video_single_worker as video_worker_fixtures
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from fastapi import FastAPI

from haoai_backend.generation_contracts.types import (
    ExternalOperationRef,
    OwnerTaskScope,
    ReferenceInput,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_core.haoai_group.results import (
    HaoaiGroupResultOutcome,
    SqlAlchemyHaoaiGroupResults,
)
from haoai_backend.generation_core.haoai_group.media import (
    SqlAlchemyHaoaiGroupMedia,
)
from haoai_backend.generation_core.haoai_group.late import (
    read_verified_late_candidate,
)
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    GenerationResultReceiptRow,
    PollReservationRow,
)
from haoai_backend.generation_core.video_single_results import (
    SqlAlchemyVideoSingleResults,
)
from haoai_backend.generation_core.video_single_claims import (
    SqlAlchemyVideoSingleClaims,
)
from haoai_backend.generation_core.video_single_recovery import (
    SqlAlchemyVideoSingleRecovery,
)
from haoai_backend.generation_runtime.video_single_assembly import (
    VideoSingleWorkerConfiguration,
    build_video_single_worker_runtime,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP
from haoai_backend.media_storage.owned_write_values import OwnedObjectVerification
from haoai_backend.task_observation.http import build_task_observation_router
from haoai_backend.task_observation.persistence import (
    task_observation_unit_of_work_factory,
)


def _runtime(environment, clock, *, client=None, media_store=None, id_factory=None):
    selected_client = client or group_worker_fixtures._RoutingHttpClient()
    selected_media_store = media_store or group_worker_fixtures._OwnedMediaStore()
    configuration = video_worker_fixtures._runtime_configuration(
        environment,
        selected_client,
        selected_media_store,
        video_worker_fixtures._PublicFetcher(),
    )
    configuration = replace(
        configuration,
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        id_factory=id_factory or configuration.id_factory,
    )
    return build_video_single_worker_runtime(
        VideoSingleWorkerConfiguration(
            runtime=configuration,
            registry_capabilities=environment.capabilities,
            poll_waiter=lambda _signal, _seconds: False,
        )
    )


def _owner_task_receipt_http(environment, owner_id, task_id):
    app = FastAPI()
    app.include_router(
        build_task_observation_router(
            uow_factory=task_observation_unit_of_work_factory(
                lambda: Session(environment.engine)
            ),
            resolve_account_actor=lambda _request: TrustedActor(user_id=owner_id),
            resolve_active_actor=lambda _request: TrustedActor(user_id=owner_id),
        )
    )

    async def get_receipt():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://late-result-owner.test",
        ) as client:
            return await client.get(
                "/api/chat/tasks", params={"task_id": task_id}
            )

    return asyncio.run(get_receipt())


def _group_results(environment, clock, id_factory):
    return SqlAlchemyHaoaiGroupResults(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        proof_authority=environment.capabilities.proof_authority,
        id_factory=id_factory,
    )


def test_group_due_preserves_unknown_provider_truth_and_refunds_exact_original_fee(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-due-unknown-original-fee-001"
        )
        clock = [video_fixtures._NOW]
        claim, _intents, _polls, resumed, _external_ref = (
            group_fixtures._setup_group(environment, task_id, clock)
        )
        with Session(environment.engine) as reader:
            task_before = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_before = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            wallet_before = reader.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == task_before["user_id"]
                )
            ).scalar_one()
        assert unit_before["first_intent_at"] is not None
        deadline = unit_before["first_intent_at"] + timedelta(seconds=7200)
        deadline_utc = deadline.replace(tzinfo=timezone.utc)
        ids = count()
        results = SqlAlchemyHaoaiGroupResults(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
            id_factory=lambda: f"haoai-group-due-id-{next(ids)}",
        )
        scope = OwnerTaskScope(
            user_id=task_before["user_id"],
            task_id=task_id,
            operation="video.single",
        )

        clock[0] = deadline_utc - timedelta(microseconds=1)
        assert results.settle_due(scope) == "pending"
        clock[0] = deadline_utc
        assert results.settle_due(scope) == "settled"
        assert results.settle_due(scope) == "settled"

        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id == unit_before["id"]
                )
            ).mappings().one()
            due_rows = reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_kind
                    == "haoai_group_due_v1",
                    ResultEvidenceRow.__table__.c.evidence_key
                    == f"haoai-group-due-v1:{unit_before['id']}",
                )
            ).mappings().all()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit_before["id"],
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()
            wallet_after = reader.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == task_before["user_id"]
                )
            ).scalar_one()

        assert task_after["status"] == "failed"
        assert json.loads(task_after["result"])["code"] == "result_unconfirmed"
        assert unit_after["result_status"] == "unknown"
        assert unit_after["billing_status"] == "refunded"
        assert unit_after["refunded_amount"] == unit_before["quoted_amount"]
        assert group["status"] == "failed"
        assert group["terminal_reason"] == "original_deadline_expired"
        assert len(due_rows) == len(refunds) == 1
        assert due_rows[0]["validity"] == "unknown"
        assert due_rows[0]["result_digest"] is None
        envelope = json.loads(due_rows[0]["result_location"])
        fact = envelope["fact"]
        canonical = json.dumps(
            fact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert hashlib.sha256(canonical).hexdigest() == due_rows[0]["payload_digest"]
        assert environment.capabilities.proof_authority.verify(
            "haoai-group-original-deadline-v1",
            canonical,
            envelope["proof"]["signature"],
        )
        assert fact["provider_truth"] == "unknown"
        assert fact["original_debit_ref"] is not None
        assert fact["refund_ref"] == refunds[0]["id"]
        assert refunds[0]["business_key"] == f"refund:{unit_before['id']}"
        assert refunds[0]["related_debit_id"] == fact["original_debit_ref"]
        assert wallet_after == wallet_before + unit_before["quoted_amount"]
        assert resumed.intent.external_submission_id
    finally:
        environment.engine.dispose()


def test_group_late_owned_receipt_after_due_refund_is_not_adopted_or_refunded_again(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-late-result-after-refund-001"
        )
        clock = [video_fixtures._NOW]
        claim, _intents, polls, resumed, external_ref = (
            group_fixtures._setup_group(environment, task_id, clock)
        )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        provider, _ = group_fixtures._provider(
            claim,
            resumed,
            body=(
                b'{"status":"completed","video_url":'
                b'"https://cdn.example/late-result.mp4"}'
            ),
            clock=clock,
        )
        poll_outcome = polls.query_and_accept(grant, provider)
        assert poll_outcome.disposition == "accepted"
        assert poll_outcome.observation is not None

        actual_results = _group_results(environment, clock, lambda: "unused-result-id")

        class DeferredResultConsumer:
            def __init__(self):
                self.complete_arguments = None

            def authorize_media_materialization(self, *arguments):
                return actual_results.authorize_media_materialization(*arguments)

            def complete(self, *arguments):
                self.complete_arguments = arguments
                return HaoaiGroupResultOutcome("pending")

        deferred_results = DeferredResultConsumer()

        class ContentProvider:
            _binding = provider._binding

            def query_existing(self, snapshot, reference):
                return provider.query_existing(snapshot, reference)

            def fetch_provider_content(self, _snapshot, _reference):
                return ReferenceInput(
                    kind="bytes",
                    locator=None,
                    content=group_worker_fixtures._VIDEO_CONTENT,
                    media_type="video/mp4",
                )

        media_store = group_worker_fixtures._OwnedMediaStore()
        media = SqlAlchemyHaoaiGroupMedia(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
            result_consumer=deferred_results,
        )
        staged = media.materialize(
            claim,
            resumed.intent,
            resumed.verified_input,
            grant,
            poll_outcome,
            ContentProvider(),
            media_store,
        )
        assert staged.disposition == "pending"
        assert deferred_results.complete_arguments is not None
        assert media_store.put_count == 1
        assert media_store.verify_count == 1

        clock[0] = resumed.intent.deadline_at
        scope = OwnerTaskScope(
            user_id=claim.scope.user_id,
            task_id=task_id,
            operation="video.single",
        )
        assert actual_results.settle_due(scope) == "settled"
        after_due = video_fixtures._financial_snapshot(environment.engine)
        with Session(environment.engine) as reader:
            candidate = read_verified_late_candidate(
                reader,
                scope,
                registry=environment.binding._producer._registry,
                crypto=environment.capabilities.crypto,
                proof_authority=environment.capabilities.proof_authority,
            )
        assert candidate is not None
        late_client = group_worker_fixtures._RoutingHttpClient()
        late_runtime = _runtime(
            environment,
            clock,
            client=late_client,
            media_store=media_store,
        )
        try:
            assert late_runtime.run_recovery_once("video-single") is True
        finally:
            late_runtime.shutdown("test complete")

        assert video_fixtures._financial_snapshot(environment.engine) == after_due
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
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
            receipts = reader.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.task_id == task_id
                )
            ).mappings().all()
            result_evidence = reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_key
                    == f"haoai-group-late-result-v1:{resumed.intent.external_submission_id}"
                )
            ).mappings().all()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()

        assert task["status"] == "failed"
        assert unit["billing_status"] == "refunded"
        assert unit["result_status"] == "unknown"
        assert group["status"] == "failed"
        assert group["selected_evidence_id"] is None
        assert len(refunds) == 1
        assert len(receipts) == 1
        assert receipts[0]["late_disposition"] == "stopped_late_generation"
        assert receipts[0]["automatic_application_allowed"] is False
        assert len(result_evidence) == 1
        late_projection = json.loads(task["result"])["late_generation"]
        assert late_projection["version"] == "haoai-group-late-generation-v1"
        assert late_projection["content_digest"] == receipts[0]["content_digest"]
        assert late_projection["recoverable_locator"] == receipts[0]["recoverable_locator"]
        assert media_store.put_count == 1
        assert media_store.verify_count == 2
        assert late_client.calls == []

        response = _owner_task_receipt_http(environment, claim.scope.user_id, task_id)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "failed"
        owner_result = json.loads(response.json()["result"])
        assert owner_result["code"] == "result_unconfirmed"
        assert owner_result["late_generation"] == late_projection
    finally:
        environment.engine.dispose()


def test_group_closed_unknown_completion_is_materialized_after_due_and_owner_readable(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    runtime = None
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-late-response-after-deadline-001"
        )
        clock = [video_fixtures._NOW]
        claim, _intents, polls, resumed, external_ref = (
            group_fixtures._setup_group(environment, task_id, clock)
        )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        provider, query_counts = group_fixtures._provider(
            claim,
            resumed,
            body=(
                b'{"status":"completed","video_url":'
                b'"https://cdn.example/late-response.mp4"}'
            ),
            clock=clock,
        )
        original_query = provider.query_existing

        def complete_after_deadline(*args, **kwargs):
            clock[0] = resumed.intent.deadline_at
            return original_query(*args, **kwargs)

        provider.query_existing = complete_after_deadline
        late_observation = polls.query_and_accept(grant, provider)
        assert late_observation.disposition == "closed_unknown"
        assert late_observation.observation is not None
        assert late_observation.observation.status == "completed"
        assert len(query_counts["client"].calls) == 1
        assert query_counts["client"].calls[0]["method"] == "GET"

        with Session(environment.engine) as reader:
            reservation = reader.execute(
                select(PollReservationRow.__table__).where(
                    PollReservationRow.__table__.c.reservation_id
                    == grant.reservation.reservation.reservation_id
                )
            ).mappings().one()
            task_before = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_before = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            group_before = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id == unit_before["id"]
                )
            ).mappings().one()
        assert reservation["state"] == "closed_unknown"
        assert reservation["observation_ref"] == late_observation.observation_id
        assert task_before["status"] == "processing"
        assert group_before["poll_used"] == 1
        assert group_before["poll_sequence"] == 1

        scope = OwnerTaskScope(
            user_id=claim.scope.user_id,
            task_id=task_id,
            operation="video.single",
        )
        due_results = _group_results(environment, clock, lambda: "unused-late-due-id")
        assert due_results.settle_due(scope) == "settled"
        after_due_financial = video_fixtures._financial_snapshot(environment.engine)
        with Session(environment.engine) as reader:
            assert read_verified_late_candidate(
                reader,
                scope,
                registry=environment.binding._producer._registry,
                crypto=environment.capabilities.crypto,
                proof_authority=environment.capabilities.proof_authority,
            ) is not None
        original_error = "legacy provider error text"
        with environment.engine.begin() as connection:
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == task_id)
                .values(result=original_error)
            )

        client = group_worker_fixtures._RoutingHttpClient()
        client.context_cleanup_delay = 0.0
        store = group_worker_fixtures._OwnedMediaStore()
        store.verification_results.append(
            OwnedObjectVerification("not_found_pending")
        )
        late_ids = count()

        def late_id_factory():
            return f"late-owner-{next(late_ids)}"

        runtime = _runtime(
            environment,
            clock,
            client=client,
            media_store=store,
            id_factory=late_id_factory,
        )
        assert runtime.run_recovery_once("video-single") is True
        runtime.shutdown("test restart recovery complete")
        runtime = None
        assert [method for method, _url, _body in client.calls] == ["GET"]
        assert client.calls[0][1].endswith(
            f"/openai/v1/videos/{external_ref.external_id}/content"
        )
        assert store.put_attempt_count == 1
        assert store.put_count == 1
        assert store.verify_count == 1
        with Session(environment.engine) as reader:
            assert reader.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.task_id == task_id
                )
            ).mappings().all() == []
            assert reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_key
                    == f"haoai-group-late-result-v1:{resumed.intent.external_submission_id}"
                )
            ).mappings().all() == []

        runtime = _runtime(
            environment,
            clock,
            client=client,
            media_store=store,
            id_factory=late_id_factory,
        )
        assert runtime.run_recovery_once("video-single") is True
        runtime.shutdown("test resume unknown owned write")
        runtime = None
        assert [method for method, _url, _body in client.calls] == ["GET"]
        assert store.put_attempt_count == 1
        assert store.put_count == 1
        assert store.verify_count == 2

        runtime = _runtime(
            environment,
            clock,
            client=client,
            media_store=store,
            id_factory=late_id_factory,
        )
        assert runtime.run_recovery_once("video-single") is True
        runtime.shutdown("test replay late projection")
        runtime = None
        assert [method for method, _url, _body in client.calls] == ["GET"]
        assert store.put_attempt_count == 1
        assert store.put_count == 1
        assert store.verify_count == 2

        runtime = _runtime(
            environment,
            clock,
            client=client,
            media_store=store,
            id_factory=late_id_factory,
        )
        assert runtime.run_recovery_once("video-single") is True
        runtime.shutdown("test repeated late projection scan")
        runtime = None
        assert [method for method, _url, _body in client.calls] == ["GET"]
        assert store.put_attempt_count == 1
        assert store.put_count == 1
        assert store.verify_count == 2

        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            group_after = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id == unit_after["id"]
                )
            ).mappings().one()
            late_receipts = reader.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.task_id == task_id
                )
            ).mappings().all()
            result_evidence = reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_key
                    == f"haoai-group-late-result-v1:{resumed.intent.external_submission_id}"
                )
            ).mappings().one()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit_after["id"],
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()

        assert task_after["status"] == "failed"
        assert unit_after["result_status"] == "unknown"
        assert unit_after["billing_status"] == "refunded"
        assert unit_after["refunded_amount"] == unit_after["quoted_amount"]
        assert group_after["status"] == "failed"
        assert group_after["selected_evidence_id"] is None
        late_document = json.loads(task_after["result"])
        assert late_document["original_result"] == original_error
        assert len(refunds) == 1
        assert len(late_receipts) == 1
        assert late_receipts[0]["late_disposition"] == "stopped_late_generation"
        assert late_receipts[0]["automatic_application_allowed"] is False
        assert result_evidence["validity"] == "valid"
        assert result_evidence["result_location"] == late_receipts[0]["recoverable_locator"]
        assert video_fixtures._financial_snapshot(environment.engine) == after_due_financial
        assert [method for method, _url, _body in client.calls] == ["GET"]
        assert client.calls[0][1].endswith(
            f"/openai/v1/videos/{external_ref.external_id}/content"
        )
        assert store.put_count == 1
        assert store.verify_count == 2

        response = _owner_task_receipt_http(
            environment, claim.scope.user_id, task_id
        )
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["status"] == "failed"
        owner_result = json.loads(receipt["result"])
        assert owner_result["original_result"] == original_error
        assert owner_result["late_generation"]["automatic_application_allowed"] is False
        assert owner_result["late_generation"]["recoverable_locator"]
        assert "late-response.mp4" not in receipt["result"]

        with environment.engine.begin() as connection:
            connection.execute(
                delete(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.task_id == task_id
                )
            )
            connection.execute(
                delete(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_key
                    == f"haoai-group-late-result-v1:{resumed.intent.external_submission_id}"
                )
            )
        store.verification_results.append(
            OwnedObjectVerification("not_found_pending")
        )
        runtime = _runtime(
            environment,
            clock,
            client=client,
            media_store=store,
            id_factory=late_id_factory,
        )
        assert runtime.run_recovery_once("video-single") is True
        runtime.shutdown("test projection without durable late receipt")
        runtime = None
        assert store.verify_count == 3
        assert store.put_attempt_count == 1
        assert store.put_count == 1
        assert [method for method, _url, _body in client.calls] == ["GET"]
        with Session(environment.engine) as reader:
            assert reader.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.task_id == task_id
                )
            ).mappings().all() == []
            assert reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_key
                    == f"haoai-group-late-result-v1:{resumed.intent.external_submission_id}"
                )
            ).mappings().all() == []
    finally:
        if runtime is not None:
            runtime.shutdown("test cleanup")
        environment.engine.dispose()


def test_registered_runtime_scanner_settles_due_group_through_group_recovery(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    runtime = None
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-runtime-recovery-original-fee-001"
        )
        clock = [video_fixtures._NOW]
        claim, _intents, _polls, resumed, _external_ref = (
            group_fixtures._setup_group(environment, task_id, clock)
        )
        assert resumed.intent is not None
        clock[0] = resumed.intent.deadline_at
        ordinary_due_calls = []
        settle_ordinary_due = SqlAlchemyVideoSingleResults.settle_due

        def observe_ordinary_due(self, scope, *, stop_signal=None):
            ordinary_due_calls.append(scope.task_id)
            return settle_ordinary_due(self, scope, stop_signal=stop_signal)

        monkeypatch.setattr(
            SqlAlchemyVideoSingleResults, "settle_due", observe_ordinary_due
        )
        runtime = _runtime(environment, clock)

        assert runtime.supports_recovery_scan("video-single") is True
        assert runtime.run_recovery_once("video-single") is True

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()

        assert task["status"] == "failed"
        assert unit["result_status"] == "unknown"
        assert unit["refunded_amount"] == unit["quoted_amount"]
        assert len(refunds) == 1
        assert ordinary_due_calls == []
    finally:
        if runtime is not None:
            runtime.shutdown("test complete")
        environment.engine.dispose()


def test_group_due_requires_original_submission_binding_before_refund(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-due-rejects-mutated-submission-001"
        )
        clock = [video_fixtures._NOW]
        claim, _intents, _polls, resumed, _external_ref = (
            group_fixtures._setup_group(environment, task_id, clock)
        )
        assert resumed.intent is not None
        clock[0] = resumed.intent.deadline_at
        with environment.engine.begin() as connection:
            connection.execute(
                update(ExternalSubmissionRow.__table__)
                .where(
                    ExternalSubmissionRow.__table__.c.id
                    == resumed.intent.external_submission_id
                )
                .values(request_digest="f" * 64)
            )

        outcome = _group_results(
            environment, clock, lambda: "haoai-group-due-mutated-refund-id"
        ).settle_due(
            OwnerTaskScope(
                user_id=claim.scope.user_id,
                task_id=task_id,
                operation="video.single",
            )
        )

        with Session(environment.engine) as reader:
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()
            evidence = reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_key
                    == f"haoai-group-due-v1:{unit['id']}"
                )
            ).mappings().all()

        assert outcome == "pending"
        assert unit["billing_status"] == "charged"
        assert unit["refunded_amount"] == 0
        assert refunds == []
        assert evidence == []
    finally:
        environment.engine.dispose()


def test_group_due_rolls_back_refund_if_due_evidence_id_is_invalid(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-due-evidence-id-rollback-001"
        )
        clock = [video_fixtures._NOW]
        claim, _intents, _polls, resumed, _external_ref = (
            group_fixtures._setup_group(environment, task_id, clock)
        )
        assert resumed.intent is not None
        clock[0] = resumed.intent.deadline_at
        ids = iter(("haoai-group-valid-refund-id", "x" * 37))
        before_financial = video_fixtures._financial_snapshot(environment.engine)

        outcome = _group_results(environment, clock, lambda: next(ids)).settle_due(
            OwnerTaskScope(
                user_id=claim.scope.user_id,
                task_id=task_id,
                operation="video.single",
            )
        )

        with Session(environment.engine) as reader:
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()
            evidence = reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_key
                    == f"haoai-group-due-v1:{unit['id']}"
                )
            ).mappings().all()

        assert outcome == "pending"
        assert task["status"] == "processing"
        assert unit["billing_status"] == "charged"
        assert unit["refunded_amount"] == 0
        assert refunds == []
        assert evidence == []
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    finally:
        environment.engine.dispose()


def test_registered_runtime_scanner_keeps_ordinary_due_recovery(
    tmp_path, monkeypatch, request: pytest.FixtureRequest
):
    environment, claim = video_intent_fixtures._claimed_video_task(
        tmp_path,
        monkeypatch,
        request,
        "video-single-runtime-recovery-ordinary-due-001",
    )
    runtime = None
    try:
        prepared = video_intent_fixtures._intents(environment).prepare(claim)
        assert prepared.disposition == "committed"
        assert prepared.intent is not None and prepared.verified_input is not None
        clock = [prepared.intent.first_intent_at]
        results = SqlAlchemyVideoSingleResults(
            lambda: Session(environment.engine),
            lambda: clock[0],
            lambda: "ordinary-runtime-recovery-refund-001",
        )
        assert results.record_submission(
            claim,
            prepared.intent,
            prepared.verified_input,
            ExternalOperationRef(
                intent_id=prepared.intent.external_submission_id,
                attempt_id=prepared.intent.attempt_id,
                provider=prepared.intent.provider,
                external_id=None,
                create_outcome="unknown",
                last_safe_provider_state=None,
            ),
        ) == "pending"
        clock[0] = prepared.intent.deadline_at
        runtime = _runtime(environment, clock)

        assert runtime.run_recovery_once("video-single") is True

        with Session(environment.engine) as reader:
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == claim.scope.task_id
                )
            ).mappings().one()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()

        assert unit["result_status"] == "failed"
        assert unit["refunded_amount"] == unit["debited_amount"]
        assert len(refunds) == 1
    finally:
        if runtime is not None:
            runtime.shutdown("test complete")
        environment.engine.dispose()


def test_registered_runtime_quarantines_ordinary_looking_partial_group_graph(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="xai"
    )
    runtime = None
    try:
        task_id, _submission_id = video_fixtures._admit_video_task(
            environment, key="haoai-group-runtime-half-graph-quarantine-001"
        )
        with Session(environment.engine) as reader:
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
        with environment.engine.begin() as connection:
            connection.execute(
                ExecutionAttemptGroupRow.__table__.insert().values(
                    unit_id=unit["id"],
                    group_key=HAOAI_ATTEMPT_GROUP.group_key,
                    registry_version=HAOAI_ATTEMPT_GROUP.registry_version,
                    attempt_step_ids=list(HAOAI_ATTEMPT_GROUP.attempt_step_ids),
                    original_payload_digest=unit["input_digest"],
                    max_retries=HAOAI_ATTEMPT_GROUP.max_retries,
                    active_attempt=0,
                    selected_evidence_id=None,
                    status="prepared",
                    poll_used=0,
                    auth_unavailable_streak=0,
                    terminal_reason=None,
                    poll_sequence=0,
                    next_poll_at=None,
                    outstanding_reservation_ref=None,
                    sticky_policy_stop_event_ref=None,
                    active_eligibility_version=HAOAI_ATTEMPT_GROUP.registry_version,
                )
            )

        clock = [video_fixtures._NOW]
        ordinary_claims = SqlAlchemyVideoSingleClaims(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            id_factory=lambda: "ordinary-claim-must-not-own-group-marker",
        )
        assert ordinary_claims.claim_next("video-single", "legacy-worker") is None
        runtime = _runtime(environment, clock)

        assert runtime.run_claimed_once("video-single") == "pending"

        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == task_id
                )
            ).mappings().one()
        assert task_after["execution_generation"] == 0
        assert task_after["claim_token"] is None
    finally:
        if runtime is not None:
            runtime.shutdown("test complete")
        environment.engine.dispose()


def test_runtime_quarantines_task_hint_when_group_unit_is_missing(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    runtime = None
    try:
        owner_task_id = group_fixtures._admit(
            environment, "haoai-group-runtime-missing-unit-owner-001"
        )
        with Session(environment.engine) as reader:
            owner_id = reader.execute(
                select(AITaskRow.__table__.c.user_id).where(
                    AITaskRow.__table__.c.id == owner_task_id
                )
            ).scalar_one()
        task_id = str(uuid4())
        now = video_fixtures._NOW.replace(tzinfo=None)
        with environment.engine.begin() as connection:
            connection.execute(
                AITaskRow.__table__.insert().values(
                    id=task_id,
                    user_id=owner_id,
                    type="video-single",
                    status="queued",
                    claimed_by=None,
                    lease_until=None,
                    execution_generation=0,
                    claim_token=None,
                    recovery_status="ready",
                    billing_status="unbilled",
                    user_cancelled_at=None,
                    cancellation_reason=None,
                    message_id="",
                    credit_cost=0,
                    result=None,
                    request_data=json.dumps(
                        {
                            "step_graph_version": HAOAI_ATTEMPT_GROUP.registry_version
                        },
                        separators=(",", ":"),
                    ),
                    model_name=None,
                    progress=0,
                    progress_message=None,
                    external_task_id=None,
                    external_provider=None,
                    created_at=now,
                    updated_at=now,
                )
            )

        clock = [video_fixtures._NOW]
        runtime = _runtime(environment, clock)
        recovery = runtime._recovery_for(runtime._registered_factory("video-single"))
        group_candidates = recovery.group_recovery._read_candidates(
            clock[0], None, 16
        )
        assert [candidate.task_id for candidate in group_candidates] == [task_id]

        assert runtime.run_recovery_once("video-single") is True

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
        assert task["status"] == "queued"
        assert task["execution_generation"] == 0
    finally:
        if runtime is not None:
            runtime.shutdown("test complete")
        environment.engine.dispose()


def test_runtime_keeps_corrupted_group_task_type_out_of_ordinary_due_writer(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    runtime = None
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-runtime-corrupt-type-quarantine-001"
        )
        clock = [video_fixtures._NOW]
        _claim, _intents, _polls, resumed, _external_ref = (
            group_fixtures._setup_group(environment, task_id, clock)
        )
        assert resumed.intent is not None
        clock[0] = resumed.intent.deadline_at
        with environment.engine.begin() as connection:
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == task_id)
                .values(type="image")
            )
        runtime = _runtime(environment, clock)

        assert runtime.run_recovery_once("video-single") is True

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()

        assert task["type"] == "image"
        assert task["status"] == "processing"
        assert unit["result_status"] == "pending"
        assert refunds == []
    finally:
        if runtime is not None:
            runtime.shutdown("test complete")
        environment.engine.dispose()


def test_combined_scanner_preserves_ordinary_read_failure_as_pending():
    class Results:
        def settle_due(self, _scope, *, stop_signal=None):
            raise AssertionError("a failed scan must not invoke the writer")

    class EmptyGroupRecovery:
        def scan_due(self, *, stop_signal=None):
            return ()

    def unavailable_session_factory():
        raise OSError("controlled ordinary recovery read failure")

    recovery = SqlAlchemyVideoSingleRecovery(
        session_factory=unavailable_session_factory,
        db_clock=lambda: video_fixtures._NOW,
        results=Results(),
        group_recovery=EmptyGroupRecovery(),
    )

    assert recovery.scan_due() == ("pending",)
