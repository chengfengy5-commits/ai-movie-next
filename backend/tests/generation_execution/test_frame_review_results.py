"""Atomic durable-text, fulfillment and continuation creation for frame.review."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.generation_contracts.types import TextResult
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.frame_review_claims import SqlAlchemyFrameReviewClaims
from haoai_backend.generation_core.frame_review_intents import SqlAlchemyFrameReviewIntents
from haoai_backend.generation_core.frame_review_results import (
    SqlAlchemyFrameReviewResults,
    decode_inline_text_result,
)
from haoai_backend.generation_core.schema import (
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_credentials.frame_review_snapshot_reader import (
    PersistedFrameReviewExecutionBinding,
    read_original_frame_review_execution_input,
)
from generation_core.test_admission_staging import _G2_JOINT_NOW
from generation_execution.test_frame_review_admission import _build_app, _post_review


def _claimed_review(
    tmp_path, monkeypatch, *, lease_seconds=60, initial_now=_G2_JOINT_NOW
):
    engine, app, token, capabilities, _ = _build_app(tmp_path, monkeypatch)
    response = asyncio.run(_post_review(app, token))
    assert response.status_code == 200, response.text
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = [initial_now]
    ids = iter(
        (
            "frame-review-claim",
            "frame-review-submission",
            "frame-review-result",
            "frame-review-evidence",
            "frame-review-work",
        )
    )
    claims = SqlAlchemyFrameReviewClaims(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=lambda: next(ids),
        lease_seconds=lease_seconds,
    )
    claim = claims.claim_next("ai-review", "results-test-worker")
    assert claim is not None
    intent = SqlAlchemyFrameReviewIntents(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=lambda: "frame-review-submission",
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    ).prepare(claim)
    assert intent.disposition == "committed"
    assert intent.verified_input is not None
    assert intent.intent is not None
    return (
        engine,
        sessions,
        now,
        claim,
        capabilities,
        intent.verified_input,
        intent.intent,
    )


def test_result_text_receipt_unit_and_work_commit_atomically_and_reopen(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=iter(
            ("frame-review-result-1", "frame-review-evidence-1", "frame-review-work-1")
        ).__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    output = TextResult("优化后的分镜正文\n保留完整换行", finish_reason="stop")
    try:
        receipt = results.persist_success(claim, intent, verified, output)
        assert receipt not in {"pending", "ownership_lost"}
        assert receipt.content_digest == hashlib.sha256(output.text.encode("utf-8")).hexdigest()

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
            step = session.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == verified.step_id
                )
            ).mappings().one()
            submission = session.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.id == intent.external_submission_id
                )
            ).mappings().one()
            evidence = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.id == receipt.evidence_id
                )
            ).mappings().one()
            stored_receipts = session.execute(
                select(GenerationResultReceiptRow.__table__)
            ).mappings().all()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__)
            ).mappings().one()
            fee_rows = session.execute(select(UnitFeeDecisionRow.__table__)).mappings().all()

        assert task["status"] == "processing"
        assert task["result"] is None
        assert task["credit_cost"] == 0
        assert unit["result_status"] == "succeeded"
        assert unit["billing_status"] == "unbilled"
        assert unit["debited_amount"] == 0
        assert unit["success_confirmed_at"] == now[0].replace(tzinfo=None)
        assert step["execution_status"] == "result_observed"
        assert submission["submission_status"] == "completed"
        assert evidence["validity"] == "valid"
        assert evidence["evidence_kind"] == "provider_text"
        assert decode_inline_text_result(
            evidence["result_location"], receipt.content_digest
        ) == output.text
        assert len(stored_receipts) == 1
        assert work["phase"] == "delivery_pending"
        assert work["phase_version"] == 0
        assert work["phase_order"] == ["delivery_pending", "fee_pending", "finalize_pending"]
        assert work["result_ref"] == receipt.result_ref
        assert work["content_digest"] == receipt.content_digest
        assert work["original_first_intent_at"] == intent.first_intent_at.replace(tzinfo=None)
        assert work["original_deadline_at"] == intent.deadline_at.replace(tzinfo=None)
        assert fee_rows == []

        replay = results.persist_success(claim, intent, verified, output)
        assert replay == receipt
        with sessions() as session:
            assert session.execute(select(ResultEvidenceRow.__table__)).mappings().all() == [evidence]
            assert session.execute(select(LocalContinuationWorkRow.__table__)).mappings().all() == [work]
    finally:
        engine.dispose()


def test_result_commit_failure_leaves_no_partial_fulfillment_or_work(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=iter(
            (
                "frame-review-result-failed",
                "frame-review-evidence-failed",
                "frame-review-work-failed",
                "frame-review-result-retry",
                "frame-review-evidence-retry",
                "frame-review-work-retry",
            )
        ).__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )

    def fail_commit(_session):
        raise RuntimeError("synthetic commit acknowledgement failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        failed = results.persist_success(
            claim, intent, verified, TextResult("保留这条完整结果")
        )
    finally:
        event.remove(Session, "before_commit", fail_commit)

    try:
        assert failed == "pending"
        with sessions() as session:
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
            step = session.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == verified.step_id
                )
            ).mappings().one()
            assert session.execute(select(ResultEvidenceRow.__table__)).mappings().all() == []
            assert session.execute(select(GenerationResultReceiptRow.__table__)).mappings().all() == []
            assert session.execute(select(LocalContinuationWorkRow.__table__)).mappings().all() == []
        assert unit["result_status"] == "pending"
        assert step["execution_status"] == "submitting"

        recovered = results.persist_success(
            claim, intent, verified, TextResult("保留这条完整结果")
        )
        assert recovered not in {"pending", "ownership_lost"}
    finally:
        engine.dispose()


def test_result_commit_ack_loss_is_confirmed_from_durable_sql_rows(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=iter(
            ("frame-review-ack-result", "frame-review-ack-evidence", "frame-review-ack-work")
        ).__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )

    def lose_commit_ack(_session):
        raise RuntimeError("synthetic acknowledgement loss after commit")

    event.listen(Session, "after_commit", lose_commit_ack)
    try:
        receipt = results.persist_success(
            claim, intent, verified, TextResult("SQL 已提交的完整正文")
        )
    finally:
        event.remove(Session, "after_commit", lose_commit_ack)

    try:
        assert receipt not in {"pending", "ownership_lost"}
        with sessions() as session:
            receipts = session.execute(
                select(GenerationResultReceiptRow.__table__)
            ).mappings().all()
            evidence = session.execute(
                select(ResultEvidenceRow.__table__)
            ).mappings().all()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__)
            ).mappings().all()
        assert len(receipts) == len(evidence) == len(work) == 1
        assert receipts[0]["result_ref"] == receipt.result_ref
        assert evidence[0]["id"] == receipt.evidence_id
        assert work[0]["result_ref"] == receipt.result_ref
    finally:
        engine.dispose()


def test_result_at_the_original_deadline_is_durable_but_stopped_late(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch, lease_seconds=8000
    )
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=iter(
            ("frame-review-late-result", "frame-review-late-evidence", "frame-review-late-work")
        ).__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    now[0] = intent.deadline_at
    try:
        receipt = results.persist_success(
            claim, intent, verified, TextResult("已过原时限的真实正文")
        )
        assert receipt not in {"pending", "ownership_lost"}
        assert receipt.late_disposition == "stopped_late_generation"
        assert receipt.automatic_application_allowed is False
        with sessions() as session:
            work = session.execute(
                select(LocalContinuationWorkRow.__table__)
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
            fee_rows = session.execute(select(UnitFeeDecisionRow.__table__)).mappings().all()
        assert work["phase"] == "stopped_late_generation"
        assert work["terminal_reason"] == "original_generation_deadline_elapsed"
        assert unit["billing_status"] == "unbilled"
        assert unit["debited_amount"] == 0
        assert fee_rows == []
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "tamper",
    (
        "snapshot_ref",
        "snapshot_target",
        "verified_text",
        "caller_intent",
        "submission_deadline",
        "work_deadline",
        "work_operation",
        "receipt_attempt",
    ),
)
def test_successful_replay_rejects_changed_original_proof(
    tmp_path, monkeypatch, tamper
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=iter(
            ("replay-proof-result", "replay-proof-evidence", "replay-proof-work")
        ).__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    output = TextResult("必须保持原样的已提交结果")
    try:
        receipt = results.persist_success(claim, intent, verified, output)
        assert receipt not in {"pending", "ownership_lost"}

        with sessions.begin() as session:
            if tamper in {"snapshot_ref", "snapshot_target"}:
                submission = session.execute(
                    select(ExternalSubmissionRow.__table__).where(
                        ExternalSubmissionRow.id == intent.external_submission_id
                    )
                ).mappings().one()
                snapshot = dict(submission["request_snapshot"])
                if tamper == "snapshot_ref":
                    snapshot["snapshot_ref"] = "frame-review-v1:changed"
                else:
                    snapshot["attempt_id"] = "different-original-attempt"
                session.execute(
                    update(ExternalSubmissionRow.__table__)
                    .where(ExternalSubmissionRow.id == intent.external_submission_id)
                    .values(request_snapshot=snapshot)
                )
            elif tamper == "submission_deadline":
                changed_deadline = intent.deadline_at.replace(tzinfo=None) + timedelta(seconds=1)
                session.execute(
                    update(ExternalSubmissionRow.__table__)
                    .where(ExternalSubmissionRow.id == intent.external_submission_id)
                    .values(deadline_at=changed_deadline)
                )
                session.execute(
                    update(BillingUnitRow.__table__)
                    .where(BillingUnitRow.id == verified.unit_id)
                    .values(deadline_at=changed_deadline)
                )
            elif tamper in {"verified_text", "caller_intent"}:
                pass
            elif tamper == "work_deadline":
                session.execute(
                    update(LocalContinuationWorkRow.__table__)
                    .where(LocalContinuationWorkRow.task_id == claim.scope.task_id)
                    .values(
                        original_deadline_at=(
                            intent.deadline_at.replace(tzinfo=None)
                            + timedelta(seconds=1)
                        )
                    )
                )
            elif tamper == "work_operation":
                session.execute(
                    update(LocalContinuationWorkRow.__table__)
                    .where(LocalContinuationWorkRow.task_id == claim.scope.task_id)
                    .values(operation="frame.review.other")
                )
            else:
                session.execute(
                    update(GenerationResultReceiptRow.__table__)
                    .where(GenerationResultReceiptRow.result_ref == receipt.result_ref)
                    .values(selected_attempt_id="different-original-attempt")
                )

        replay_input = verified
        replay_intent = intent
        if tamper == "verified_text":
            replay_input = replace(verified, original_text="未经认证的替换正文")
        elif tamper == "caller_intent":
            replay_intent = replace(
                intent,
                deadline_at=intent.deadline_at + timedelta(seconds=1),
            )
        replay = results.persist_success(claim, replay_intent, replay_input, output)
        assert replay == "pending"
    finally:
        engine.dispose()


def test_successful_replay_accepts_valid_continuation_phase_progress(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=iter(
            ("replay-phase-result", "replay-phase-evidence", "replay-phase-work")
        ).__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    output = TextResult("继续阶段仍应关联这份原结果")
    try:
        receipt = results.persist_success(claim, intent, verified, output)
        assert receipt not in {"pending", "ownership_lost"}
        with sessions.begin() as session:
            session.execute(
                update(LocalContinuationWorkRow.__table__)
                .where(LocalContinuationWorkRow.task_id == claim.scope.task_id)
                .values(phase="fee_pending", phase_version=1)
            )

        replay = results.persist_success(claim, intent, verified, output)

        assert replay == receipt
    finally:
        engine.dispose()


def test_commit_ack_confirmation_checks_staged_work_identity(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=iter(
            ("ack-proof-result", "ack-proof-evidence", "ack-proof-work")
        ).__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    callbacks = [0]

    def change_work_after_commit(_session):
        callbacks[0] += 1
        if callbacks[0] == 1:
            with sessions.begin() as changed:
                changed.execute(
                    update(LocalContinuationWorkRow.__table__)
                    .where(LocalContinuationWorkRow.task_id == claim.scope.task_id)
                    .values(work_id="different-durable-work")
                )

    event.listen(Session, "after_commit", change_work_after_commit)
    try:
        result = results.persist_success(
            claim, intent, verified, TextResult("ACK 必须确认本次 staged work")
        )
    finally:
        event.remove(Session, "after_commit", change_work_after_commit)
        engine.dispose()

    assert result == "pending"
