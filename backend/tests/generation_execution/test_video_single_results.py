from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

import test_video_single_claims as video_fixtures
import test_video_single_intents as intent_fixtures
import test_video_execution_snapshot_reader as snapshot_fixtures
from haoai_backend.generation_contracts.types import (
    ExternalOperationRef,
    OwnerTaskScope,
    StoredMedia,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.schema import (
    GenerationResultReceiptRow,
    GenerationResultTargetRow,
)
from haoai_backend.generation_core.video_single_results import (
    SqlAlchemyVideoSingleResults,
)


def _results(environment, clock, ids: tuple[str, ...]) -> SqlAlchemyVideoSingleResults:
    pending_ids = iter(ids)
    return SqlAlchemyVideoSingleResults(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: next(pending_ids),
    )


def _external_ref(intent, *, external_id: str | None, outcome: str) -> ExternalOperationRef:
    return ExternalOperationRef(
        intent_id=intent.external_submission_id,
        attempt_id=intent.attempt_id,
        provider=intent.provider,
        external_id=external_id,
        create_outcome=outcome,
        last_safe_provider_state="queued" if external_id is not None else None,
    )


def _prepared_claim(tmp_path, monkeypatch, request, raw_key):
    environment, claim = intent_fixtures._claimed_video_task(
        tmp_path,
        monkeypatch,
        request,
        raw_key,
    )
    prepared = intent_fixtures._intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    return environment, claim, prepared.intent, prepared.verified_input


def test_submitted_video_result_is_receipted_without_changing_billing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment, claim, intent, verified = _prepared_claim(
        tmp_path, monkeypatch, request, "video-result-success-916"
    )
    clock = [video_fixtures._NOW]
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    writer = _results(
        environment,
        clock,
        ("video-result-ref-916", "video-evidence-id-916"),
    )
    external_ref = _external_ref(
        intent, external_id="xai-video-task-916", outcome="submitted"
    )

    assert writer.record_submission(claim, intent, verified, external_ref) == "recorded"
    assert writer.record_submission(claim, intent, verified, external_ref) == "recorded"
    stored = StoredMedia(
        locator="https://media.example.test/video-916.mp4",
        content_digest=hashlib.sha256(b"video result").hexdigest(),
        media_type="video/mp4",
        size_bytes=12,
    )
    receipt = writer.complete(claim, intent, verified, stored)

    assert receipt is not None and not isinstance(receipt, str)
    assert receipt.operation == "video.single"
    assert receipt.automatic_application_allowed is False
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.task_id == claim.scope.task_id
            )
        ).mappings().one()
        step = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.id == intent.step_id
            )
        ).mappings().one()
        submission = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.id == intent.external_submission_id
            )
        ).mappings().one()
        evidence = reader.execute(
            select(ResultEvidenceRow.__table__).where(
                ResultEvidenceRow.evidence_key
                == f"video-single-result-v1:{intent.external_submission_id}"
            )
        ).mappings().one()
        stored_receipt = reader.get(GenerationResultReceiptRow, receipt.result_ref)
    assert task["status"] == "completed"
    assert task["message_id"] == ""
    assert json.loads(task["result"])["kind"] == "video"
    assert unit["result_status"] == "succeeded"
    assert unit["success_confirmed_at"] is not None
    assert step["execution_status"] == "result_observed"
    assert submission["submission_status"] == "completed"
    assert submission["external_task_id"] == "xai-video-task-916"
    assert evidence["evidence_kind"] == "provider_video"
    assert evidence["result_location"] == stored.locator
    assert stored_receipt is not None
    assert video_fixtures._financial_snapshot(environment.engine) == before_financial
    assert writer.settle_due(claim.scope) == "retained"


def test_provider_rejection_refunds_once_and_keeps_original_external_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment, claim, intent, verified = _prepared_claim(
        tmp_path, monkeypatch, request, "video-result-failure-916"
    )
    clock = [video_fixtures._NOW]
    writer = _results(environment, clock, ("video-refund-id-916",))
    external_ref = _external_ref(
        intent, external_id="xai-video-rejected-916", outcome="submitted"
    )
    assert writer.record_submission(claim, intent, verified, external_ref) == "recorded"

    assert writer.fail(
        claim,
        intent,
        verified,
        code="provider_rejected",
        safe_detail="The provider rejected this video job.",
        provider_request_started=True,
    ) is None
    before_replay = video_fixtures._financial_snapshot(environment.engine)
    assert writer.fail(
        claim,
        intent,
        verified,
        code="provider_rejected",
        safe_detail="The provider rejected this video job.",
        provider_request_started=True,
    ) == "ownership_lost"

    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.task_id == claim.scope.task_id
            )
        ).mappings().one()
        submission = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.id == intent.external_submission_id
            )
        ).mappings().one()
        refunds = reader.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).mappings().all()
    assert task["status"] == "failed"
    assert task["billing_status"] == "refunded"
    assert unit["result_status"] == "failed"
    assert unit["billing_status"] == "refunded"
    assert unit["refunded_amount"] == unit["debited_amount"]
    assert submission["submission_status"] == "failed"
    assert submission["external_task_id"] == "xai-video-rejected-916"
    assert len(refunds) == 1
    assert video_fixtures._financial_snapshot(environment.engine) == before_replay


def test_frame_message_video_result_stages_canonical_receipt_without_canvas_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = snapshot_fixtures._video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    asset_id = "video-result-storyboard-916"
    chapter_content = (
        f'[ {{ "storyboard" : ["{asset_id}"], '
        '"text" : "Keep this frame target unchanged" } ]'
    )
    snapshot_fixtures._seed_video_storyboard_asset(
        environment,
        asset_id=asset_id,
        chapter_content=chapter_content,
    )
    video_request = snapshot_fixtures._video_request()
    video_request["target"] = {
        "kind": "frame_message",
        "chapter_id": "joint-chapter-1",
        "storyboard_asset_id": asset_id,
    }
    binding = snapshot_fixtures._commit_video_request(
        environment,
        raw_key="video-result-frame-message-916",
        request=video_request,
    )
    clock = [video_fixtures._NOW]
    claim = video_fixtures._claim_adapter(
        environment.engine,
        clock,
        "video-result-frame-claim-916",
    ).claim_next("video-single", "video-result-frame-worker")
    assert claim is not None and claim.scope.task_id == binding.task_id
    prepared = intent_fixtures._intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    assert prepared.verified_input.snapshot.target_snapshot.kind == "frame_message"
    writer = _results(
        environment,
        clock,
        ("video-frame-result-ref-916", "video-frame-evidence-id-916"),
    )
    external_ref = _external_ref(
        prepared.intent,
        external_id="xai-video-frame-task-916",
        outcome="submitted",
    )
    assert (
        writer.record_submission(
            claim, prepared.intent, prepared.verified_input, external_ref
        )
        == "recorded"
    )
    receipt = writer.complete(
        claim,
        prepared.intent,
        prepared.verified_input,
        StoredMedia(
            locator="https://media.example.test/frame-video-916.mp4",
            content_digest=hashlib.sha256(b"frame video result").hexdigest(),
            media_type="video/mp4",
            size_bytes=18,
        ),
    )

    assert receipt is not None and not isinstance(receipt, str)
    with Session(environment.engine) as reader:
        stored_receipt = reader.get(GenerationResultReceiptRow, receipt.result_ref)
        target_projections = reader.execute(
            select(GenerationResultTargetRow.__table__).where(
                GenerationResultTargetRow.__table__.c.task_id == binding.task_id
            )
        ).mappings().all()
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == binding.task_id)
        ).mappings().one()
    assert stored_receipt is not None
    assert stored_receipt.operation == "video.single"
    assert target_projections == []
    assert task["status"] == "completed"


def test_unknown_without_id_waits_for_original_deadline_before_refund(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment, claim, intent, verified = _prepared_claim(
        tmp_path, monkeypatch, request, "video-result-unknown-916"
    )
    clock = [video_fixtures._NOW]
    writer = _results(environment, clock, ("video-unknown-refund-916",))

    assert writer.record_submission(
        claim,
        intent,
        verified,
        _external_ref(intent, external_id=None, outcome="unknown"),
    ) == "pending"
    with Session(environment.engine) as reader:
        before_deadline = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.task_id == claim.scope.task_id
            )
        ).mappings().one()
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
        ).mappings().one()
        step = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.id == intent.step_id
            )
        ).mappings().one()
        submission = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.id == intent.external_submission_id
            )
        ).mappings().one()
    assert task["status"] == "reconciling"
    assert before_deadline["result_status"] == "unknown"
    assert step["execution_status"] == "reconciling"
    assert submission["submission_status"] == "unknown"
    assert submission["external_task_id"] is None

    clock[0] = intent.deadline_at
    scope = OwnerTaskScope(claim.scope.user_id, claim.scope.task_id, "video.single")
    assert writer.settle_due(scope) == "settled"
    settled_financial = video_fixtures._financial_snapshot(environment.engine)
    assert writer.settle_due(scope) == "settled"
    assert video_fixtures._financial_snapshot(environment.engine) == settled_financial
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.task_id == claim.scope.task_id
            )
        ).mappings().one()
    assert task["status"] == "failed"
    assert unit["result_status"] == "failed"
    assert unit["deadline_at"] == before_deadline["deadline_at"]
    assert unit["refunded_amount"] == unit["debited_amount"]


def test_stale_claim_cannot_persist_a_video_provider_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment, claim, intent, verified = _prepared_claim(
        tmp_path, monkeypatch, request, "video-result-stale-claim-916"
    )
    clock = [video_fixtures._NOW]
    writer = _results(environment, clock, ("video-stale-refund-916",))
    with environment.engine.begin() as connection:
        connection.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == claim.scope.task_id)
            .values(
                claimed_by="video-result-replacement-worker",
                claim_token="video-result-replacement-token",
                execution_generation=claim.execution_generation + 1,
                lease_until=(video_fixtures._NOW + timedelta(seconds=60)).replace(
                    tzinfo=None
                ),
            )
        )
    # A graph with an intent is deliberately no longer claimable; emulate its
    # later ownership transfer so this assertion isolates the stale fence.
    before_financial = video_fixtures._financial_snapshot(environment.engine)

    assert writer.record_submission(
        claim,
        intent,
        verified,
        _external_ref(intent, external_id="must-not-be-written", outcome="submitted"),
    ) == "ownership_lost"
    with Session(environment.engine) as reader:
        submission = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.id == intent.external_submission_id
            )
        ).mappings().one()
    assert submission["submission_status"] == "submitting"
    assert submission["external_task_id"] is None
    assert video_fixtures._financial_snapshot(environment.engine) == before_financial
