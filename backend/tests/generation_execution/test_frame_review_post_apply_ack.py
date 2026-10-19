"""Post-apply continuation and durable result-ACK integration coverage."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import TextResult
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_core.frame_review_continuation import (
    SqlAlchemyFrameReviewContinuationConsumer,
)
from haoai_backend.generation_core.frame_review_continuation_claims import (
    SqlAlchemyFrameReviewContinuationClaims,
)
from haoai_backend.generation_core.frame_review_results import SqlAlchemyFrameReviewResults
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_credentials.frame_review_snapshot_reader import (
    PersistedFrameReviewExecutionBinding,
    read_original_frame_review_execution_input,
)
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from generation_execution.test_frame_review_results import _claimed_review


def test_commit_ack_confirmation_after_real_post_apply_charge(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch, initial_now=datetime.now(timezone.utc)
    )
    with engine.begin() as connection:
        # The admission fixture models the narrower catalog table; P5 reads the
        # full series_data projection, which also carries this persisted column.
        connection.exec_driver_sql(
            "ALTER TABLE storyboard_assets ADD COLUMN created_at DATETIME"
        )
    notes_metadata.create_all(engine)
    output = TextResult("经真实投递和后扣费后仍可确认的分镜正文")
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=iter(
            ("post-fee-ack-result", "post-fee-ack-evidence", "post-fee-ack-work")
        ).__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    continuation_ids = iter(
        (
            "post-fee-continuation-1",
            "post-fee-continuation-2",
            "post-fee-continuation-3",
            "post-fee-ledger",
        )
    )
    continuation_claims = SqlAlchemyFrameReviewContinuationClaims(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=continuation_ids.__next__,
    )
    continuation = SqlAlchemyFrameReviewContinuationConsumer(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=continuation_ids.__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
        claims=continuation_claims,
    )

    def finish_real_post_apply_phases():
        now[0] = claim.lease_until + timedelta(seconds=1)
        expected_outcomes = ("delivered", "fee_resolved", "finalized")
        for index, expected in enumerate(expected_outcomes):
            phase_claim = continuation_claims.claim_continuation(
                "ai-review", "post-fee-worker", lease_seconds=60
            )
            assert phase_claim is not None
            outcome = continuation.consume(phase_claim)
            assert outcome == expected
            if index < len(expected_outcomes) - 1:
                now[0] = phase_claim.lease_until + timedelta(seconds=1)

    acknowledgement_lost = [False]

    def lose_result_commit_ack(_session):
        if acknowledgement_lost[0]:
            return
        acknowledgement_lost[0] = True
        raise RuntimeError("synthetic result commit acknowledgement loss")

    confirm_persisted = SqlAlchemyFrameReviewResults._confirm_persisted

    def confirm_after_real_fee_progress(adapter, *args):
        if adapter is results:
            finish_real_post_apply_phases()
        return confirm_persisted(adapter, *args)

    monkeypatch.setattr(
        SqlAlchemyFrameReviewResults,
        "_confirm_persisted",
        confirm_after_real_fee_progress,
    )
    event.listen(Session, "after_commit", lose_result_commit_ack)
    try:
        try:
            acknowledgement_result = results.persist_success(
                claim, intent, verified, output
            )
        finally:
            event.remove(Session, "after_commit", lose_result_commit_ack)

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == claim.scope.task_id
                )
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
            receipts = session.execute(
                select(GenerationResultReceiptRow.__table__)
            ).mappings().all()
            evidence = session.execute(
                select(ResultEvidenceRow.__table__)
            ).mappings().all()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__)
            ).mappings().one()
            fee = session.execute(
                select(UnitFeeDecisionRow.__table__)
            ).mappings().one()
            application = session.execute(
                select(ApplicationRecordRow.__table__)
            ).mappings().one()
            ledger = session.execute(
                select(CreditLogRow.__table__)
            ).mappings().one()
            wallet = session.execute(
                select(UserCreditRow.__table__)
            ).mappings().one()
            request_data = json.loads(task["request_data"])
            step = session.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == verified.step_id
                )
            ).mappings().one()
            snapshot = step["input_snapshot"]
            binding = PersistedFrameReviewExecutionBinding(
                owner_id=task["user_id"],
                task_id=task["id"],
                unit_id=unit["id"],
                step_id=verified.step_id,
                submission_id=request_data["submission_id"],
                raw_key=f"frame-review:{request_data['submission_id']}",
                task_message_id=task["message_id"] or None,
                task_model_name=task["model_name"],
                task_model_id=request_data["model_id"],
                task_model_source=request_data["model_source"],
                task_model_owner_id=request_data["model_owner_id"],
                request_digest=request_data["request_digest"],
                rule_digest=request_data["rule_digest"],
                snapshot_digest=snapshot["snapshot_digest"],
                manifest_snapshot_digest=snapshot["manifest_snapshot_digest"],
                server_attempt_id=snapshot["server_attempt_id"],
                prepared_payload_id=snapshot["prepared_payload_id"],
                protected_ref=snapshot["protected_ref"],
                quoted_amount=unit["quoted_amount"],
            )
            directly_reopened = read_original_frame_review_execution_input(
                session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )

        assert task["status"] == "completed"
        assert task["execution_generation"] == work["last_execution_generation"] == 4
        assert task["credit_cost"] == unit["debited_amount"] == 3
        assert unit["billing_status"] == "charged"
        assert unit["refunded_amount"] == 0
        assert len(receipts) == len(evidence) == 1
        assert work["phase"] == "done"
        assert fee["charge_phase"] == "post_apply" and fee["resolution"] == "charged"
        assert fee["generation_result_ref"] == receipts[0]["result_ref"]
        assert fee["application_ref"] == application["application_ref"]
        assert application["delivery_status"] == "applied"
        assert application["result_ref"] == receipts[0]["result_ref"]
        assert ledger["amount"] == -3 and ledger["billing_unit_id"] == unit["id"]
        assert wallet["credits"] == 497

        old_claim_replay = results.persist_success(claim, intent, verified, output)
        ack_result_ref = getattr(
            acknowledgement_result, "result_ref", acknowledgement_result
        )
        assert (ack_result_ref, directly_reopened, old_claim_replay) == (
            receipts[0]["result_ref"],
            None,
            "ownership_lost",
        )
    finally:
        engine.dispose()
