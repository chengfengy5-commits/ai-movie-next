"""Independent leases for durable frame.optimize result continuation."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import event, update
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import UserCreditRow
from haoai_backend.task_observation.cancellation import SqlTaskCancellationWriter
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    claim_adapter,
    claim_one,
    fee_adapter,
    intent_adapter,
    persist_result,
    persist_result_and_fee,
    prepare_item,
    rows_for,
)


def test_fee_continuation_waits_for_parent_lease_then_renews_and_releases(tmp_path):
    case = admit_batch(tmp_path, frame_count=3, request_indexes=(2, 0))
    try:
        generation_claims = claim_adapter(case, token="parent-before-fee-continuation")
        parent = generation_claims.claim_next("batch-optimize", "generation-worker")
        assert parent is not None
        intents = intent_adapter(case, prefix="continuation-intent")
        item, prepared = prepare_item(case, parent, order=0, intents=intents)
        assert prepared.disposition == "committed"
        receipt = persist_result(case, parent, prepared, text="result awaiting fee")
        assert not isinstance(receipt, str)
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        fees = fee_adapter(case, prefix="continuation-fee")
        continuations = fees.continuation_claims

        assert continuations.claim_continuation("batch-optimize", "fee-worker") is None
        assert rows_for(case)["task"]["claim_token"] == parent.token

        assert generation_claims.release_claim(parent) is True
        released = rows_for(case)["task"]
        assert released["status"] == "reconciling"
        assert released["claim_token"] is None
        assert released["lease_until"] is None
        assert released["execution_generation"] == parent.execution_generation

        continuation = continuations.claim_continuation("batch-optimize", "fee-worker")
        assert continuation is not None
        assert continuation.unit_id == item.unit_id
        assert continuation.work_id == work.work_id
        assert continuation.execution_generation == parent.execution_generation + 1
        assert rows_for(case)["task"]["status"] == "processing"
        assert len(rows_for(case)["external"]) == 1

        case.clock[0] += timedelta(seconds=1)
        state = {"lose": True}

        def lose_renewal_ack(session: Session) -> None:
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                raise RuntimeError("fixture lost continuation renewal acknowledgement")

        event.listen(Session, "after_commit", lose_renewal_ack)
        try:
            renewed = continuations.renew_continuation(continuation)
        finally:
            event.remove(Session, "after_commit", lose_renewal_ack)
        assert renewed is not None
        assert state["lose"] is False
        assert renewed.lease_until > continuation.lease_until
        outcome = fees.consume_fee(renewed)
        assert outcome.disposition == "charged"
        assert outcome.unit_id == item.unit_id

        assert continuations.release_continuation(renewed) is True
        state = rows_for(case)
        assert state["task"]["status"] == "reconciling"
        assert state["task"]["claim_token"] is None
        assert state["task"]["lease_until"] is None
        assert state["task"]["execution_generation"] == renewed.execution_generation
        assert state["work"][0].phase == "delivery_pending"
        assert len(state["receipts"]) == len(state["fees"]) == len(state["creditLogs"]) == 1
        assert len(state["external"]) == 1
        assert state["wallet"][0]["credits"] == 497
        assert state["applications"] == []
    finally:
        case.engine.dispose()


def test_cancelled_parent_claim_cannot_open_a_fee_phase(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        receipt = persist_result(case, claim, prepared, text="result before cancellation")
        assert not isinstance(receipt, str)
        work = rows_for(case)["work"][0]

        assert SqlTaskCancellationWriter(case.engine.begin).request_cancel(case.task_id)
        outcome = fee_adapter(case, prefix="cancelled-fee").resolve_current_fee(
            claim, work.work_id
        )
        rows = rows_for(case)
        assert outcome.disposition == "ownership_lost"
        assert rows["task"]["status"] == "cancelling"
        assert rows["work"][0].phase == "fee_pending"
        assert rows["fees"] == rows["creditLogs"] == []
        assert rows["wallet"][0]["credits"] == 500
        assert len(rows["receipts"]) == len(rows["evidence"]) == 1
    finally:
        case.engine.dispose()


def test_insufficient_continuation_release_clears_its_lease_and_preserves_sibling_debit(tmp_path):
    case = admit_batch(tmp_path, frame_count=3, request_indexes=(2, 0, 1))
    try:
        generation_claims = claim_adapter(case, token="insufficient-fee-parent")
        generation_claim = generation_claims.claim_next(
            "batch-optimize", "generation-test-worker"
        )
        assert generation_claim is not None
        intents = intent_adapter(case, prefix="insufficient-release-intent")
        fees = fee_adapter(case, prefix="insufficient-release-fee")

        paid_item, paid_prepared = prepare_item(case, generation_claim, 0, intents=intents)
        paid_receipt, paid_fee = persist_result_and_fee(
            case, generation_claim, paid_prepared, fees, text="paid sibling output"
        )
        assert paid_item.frame_index == 2
        assert paid_fee.disposition == "charged"

        pending_item, pending_prepared = prepare_item(case, generation_claim, 1, intents=intents)
        assert pending_item.frame_index == 0
        pending_receipt = persist_result(case, generation_claim, pending_prepared, text="insufficient output")
        assert not isinstance(pending_receipt, str)
        assert generation_claims.release_claim(generation_claim) is True

        released_generation = rows_for(case)["task"]
        assert released_generation["status"] == "reconciling"
        assert released_generation["claim_token"] is None
        assert released_generation["lease_until"] is None

        continuation = fees.continuation_claims.claim_continuation(
            "batch-optimize", "insufficient-fee-worker"
        )
        assert continuation is not None
        assert continuation.unit_id == pending_item.unit_id
        with case.engine.begin() as connection:
            connection.execute(
                update(UserCreditRow.__table__)
                .where(UserCreditRow.__table__.c.user_id == case.actor_id)
                .values(credits=0)
            )

        stopped = fees.consume_fee(continuation)
        assert stopped.disposition == "stopped_insufficient"
        assert stopped.result_ref == pending_receipt.result_ref
        assert fees.continuation_claims.release_continuation(continuation) is True

        rows = rows_for(case)
        assert rows["task"]["status"] == "reconciling"
        assert rows["task"]["claimed_by"] is None
        assert rows["task"]["claim_token"] is None
        assert rows["task"]["lease_until"] is None
        assert rows["task"]["execution_generation"] == continuation.execution_generation
        assert rows["task"]["recovery_status"] == "stopped_insufficient"
        assert rows["task"]["billing_status"] == "charged"
        assert rows["task"]["credit_cost"] == 3
        assert rows["wallet"][0]["credits"] == 0
        assert len(rows["receipts"]) == len(rows["evidence"]) == len(rows["work"]) == 2
        assert len(rows["fees"]) == 2
        assert len(rows["creditLogs"]) == 1
        assert rows["creditLogs"][0].billing_unit_id == paid_item.unit_id
        assert rows["applications"] == []
        assert next(row for row in rows["work"] if row.unit_id == paid_item.unit_id).phase == "delivery_pending"
        stopped_work = next(row for row in rows["work"] if row.unit_id == pending_item.unit_id)
        assert stopped_work.phase == "stopped_insufficient"
        assert stopped_work.fee_decision_ref == pending_item.unit_id
    finally:
        case.engine.dispose()
