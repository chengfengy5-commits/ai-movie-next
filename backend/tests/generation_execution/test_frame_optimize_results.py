"""Durable frame.optimize result receipts and fee-phase transitions."""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import TextResult
from haoai_backend.generation_core.frame_optimize_progress import decode_optimize_text
from haoai_backend.task_observation.cancellation import SqlTaskCancellationWriter
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    claim_adapter,
    claim_one,
    prepare_item,
    result_adapter,
    rows_for,
)


def test_result_persistence_keeps_exact_text_and_original_batch(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim, order=0)
        assert item.frame_index == 2
        assert prepared.disposition == "committed"
        result_text = "  revised frame two\n"

        receipt = result_adapter(case).persist_success(
            claim,
            prepared.intent,
            prepared.verified_batch,
            prepared.verified_item,
            TextResult(result_text),
        )
        assert not isinstance(receipt, str)
        state = rows_for(case)
        unit = next(row for row in state["units"] if row["id"] == item.unit_id)
        step = next(row for row in state["steps"] if row["billing_unit_id"] == item.unit_id)
        external = next(row for row in state["external"] if row["step_id"] == item.step_id)
        evidence = next(row for row in state["evidence"] if row["id"] == receipt.evidence_id)
        work = next(row for row in state["work"] if row.unit_id == item.unit_id)

        assert receipt.result_ref == state["receipts"][0].result_ref
        assert receipt.selected_attempt_id == item.item_id
        assert decode_optimize_text(evidence["result_location"], receipt.content_digest) == result_text
        assert evidence["application_status"] == "pending"
        assert unit["result_status"] == "succeeded"
        assert step["execution_status"] == "result_observed"
        assert external["submission_status"] == "completed"
        assert receipt.generation_fulfilled_at == case.clock[0]
        assert work.phase == "fee_pending"
        assert work.phase_version == 0
        assert len(state["receipts"]) == len(state["evidence"]) == len(state["work"]) == 1
        assert state["fees"] == state["creditLogs"] == state["applications"] == []
        assert state["task"]["status"] == "processing"
        assert state["task"]["result"] is None
        assert state["task"]["credit_cost"] == 0
        assert state["wallet"][0]["credits"] == 500

        reopened = result_adapter(case).persist_success(
            claim,
            prepared.intent,
            prepared.verified_batch,
            prepared.verified_item,
            TextResult(result_text),
        )
        assert reopened == receipt
        assert len(rows_for(case)["receipts"]) == 1
    finally:
        case.engine.dispose()


def test_result_commit_acknowledgement_loss_confirms_one_original_receipt(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        state = {"lose": True}
        results = result_adapter(case, prefix="result-ack-lost")

        def lose_ack_once(session: Session) -> None:
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                raise RuntimeError("fixture lost result commit acknowledgement")

        event.listen(Session, "after_commit", lose_ack_once)
        try:
            receipt = results.persist_success(
                claim,
                prepared.intent,
                prepared.verified_batch,
                prepared.verified_item,
                TextResult("saved once"),
            )
        finally:
            event.remove(Session, "after_commit", lose_ack_once)

        assert state["lose"] is False
        assert not isinstance(receipt, str)
        rows = rows_for(case)
        assert len(rows["receipts"]) == len(rows["evidence"]) == len(rows["work"]) == 1
        assert rows["receipts"][0].result_ref == receipt.result_ref
        assert rows["fees"] == rows["creditLogs"] == rows["applications"] == []
        assert len(rows["external"]) == 1
        assert rows["task"]["status"] == "processing"
        assert rows["task"]["credit_cost"] == 0
        assert rows["wallet"][0]["credits"] == 500
    finally:
        case.engine.dispose()


def test_result_rollback_before_commit_leaves_no_partial_records(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        state = {"rollback": True}

        def fail_before_commit(session: Session) -> None:
            if session.bind is case.engine and state["rollback"]:
                state["rollback"] = False
                raise RuntimeError("fixture rolled back before result commit")

        event.listen(Session, "before_commit", fail_before_commit)
        try:
            result = result_adapter(case, prefix="result-rollback").persist_success(
                claim,
                prepared.intent,
                prepared.verified_batch,
                prepared.verified_item,
                TextResult("not saved"),
            )
        finally:
            event.remove(Session, "before_commit", fail_before_commit)

        assert state["rollback"] is False
        assert result == "pending"
        rows = rows_for(case)
        unit = next(row for row in rows["units"] if row["id"] == item.unit_id)
        step = next(row for row in rows["steps"] if row["billing_unit_id"] == item.unit_id)
        assert unit["result_status"] == "pending"
        assert unit["success_confirmed_at"] is None
        assert step["execution_status"] == "submitting"
        assert rows["external"][0]["submission_status"] == "submitting"
        assert rows["receipts"] == rows["evidence"] == rows["work"] == []
        assert rows["fees"] == rows["creditLogs"] == rows["applications"] == []
        assert rows["wallet"][0]["credits"] == 500
    finally:
        case.engine.dispose()


def test_result_at_original_deadline_is_persisted_as_late_without_fee_authority(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="result-deadline-claim", lease_seconds=9000)
        claim = claims.claim_next("batch-optimize", "late-result-worker")
        assert claim is not None
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        case.clock[0] = prepared.intent.deadline_at

        receipt = result_adapter(case, prefix="result-deadline").persist_success(
            claim,
            prepared.intent,
            prepared.verified_batch,
            prepared.verified_item,
            TextResult("arrived at deadline"),
        )
        assert not isinstance(receipt, str)
        rows = rows_for(case)
        assert receipt.generation_fulfilled_at == prepared.intent.deadline_at
        assert receipt.late_disposition == "stopped_late_generation"
        assert receipt.automatic_application_allowed is False
        assert rows["work"][0].phase == "stopped_late_generation"
        assert rows["work"][0].phase_version == 1
        assert rows["fees"][0].resolution == "stopped_late_generation"
        assert rows["fees"][0].ledger_ref is None
        assert rows["creditLogs"] == rows["applications"] == []
        assert rows["wallet"][0]["credits"] == 500
    finally:
        case.engine.dispose()


def test_cancelled_task_does_not_accept_a_new_result_fulfillment(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        assert SqlTaskCancellationWriter(case.engine.begin).request_cancel(case.task_id)

        result = result_adapter(case, prefix="cancelled-result").persist_success(
            claim,
            prepared.intent,
            prepared.verified_batch,
            prepared.verified_item,
            TextResult("late after cancellation"),
        )
        assert result == "ownership_lost"
        rows = rows_for(case)
        unit = next(row for row in rows["units"] if row["id"] == item.unit_id)
        assert rows["task"]["status"] == "cancelling"
        assert unit["result_status"] == "pending"
        assert unit["success_confirmed_at"] is None
        assert rows["steps"][0]["execution_status"] == "submitting"
        assert rows["external"][0]["submission_status"] == "submitting"
        assert rows["receipts"] == rows["evidence"] == rows["work"] == []
        assert rows["fees"] == rows["creditLogs"] == rows["applications"] == []
        assert rows["wallet"][0]["credits"] == 500
    finally:
        case.engine.dispose()
