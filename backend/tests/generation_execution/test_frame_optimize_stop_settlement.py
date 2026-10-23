"""Narrow tests for the insufficient-credit stop projection."""

import json

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import BillingUnitRow
from haoai_backend.generation_core.schema import LocalContinuationWorkRow, UnitFeeDecisionRow
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    apply_current,
    claim_one,
    fee_adapter,
    finalize_current,
    intent_adapter,
    locked_batch,
    persist_result,
    prepare_item,
    rows_for,
)


def test_insufficient_trigger_records_exact_stop_for_unstarted_sibling(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1), wallet_credits=3)
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="stop-settlement-intent")

        paid_item, paid_intent = prepare_item(case, claim, 0, intents=intents)
        paid_receipt = persist_result(case, claim, paid_intent, text="paid frame")
        assert not isinstance(paid_receipt, str)
        paid_work = next(row for row in rows_for(case)["work"] if row.unit_id == paid_item.unit_id)
        paid_fee = fee_adapter(case).resolve_current_fee(claim, paid_work.work_id)
        assert paid_fee.disposition == "charged"

        insufficient_item, insufficient_intent = prepare_item(case, claim, 1, intents=intents)
        insufficient_receipt = persist_result(case, claim, insufficient_intent, text="unpaid result")
        assert not isinstance(insufficient_receipt, str)
        insufficient_work = next(
            row for row in rows_for(case)["work"] if row.unit_id == insufficient_item.unit_id
        )
        stopped = fee_adapter(case).resolve_current_fee(claim, insufficient_work.work_id)
        assert stopped.disposition == "stopped_insufficient"

        unstarted_item = locked_batch(case).verified.items[2]
        rows = rows_for(case)
        unit = next(row for row in rows["units"] if row["id"] == unstarted_item.unit_id)
        step = next(row for row in rows["steps"] if row["id"] == unstarted_item.step_id)
        assert unit["result_status"] == "pending"
        assert unit["billing_status"] == "unbilled"
        assert unit["first_intent_at"] is None and unit["deadline_at"] is None
        assert step["execution_status"] == "prepared"
        assert not any(row.unit_id == unstarted_item.unit_id for row in rows["receipts"])
        assert not any(row.unit_id == unstarted_item.unit_id for row in rows["work"])

        with case.sessions() as session:
            decision = session.get(UnitFeeDecisionRow, unstarted_item.unit_id)
            assert decision is not None, "the same insufficient transaction must stop this sibling"
            assert decision.charge_phase == "pre_apply"
            assert decision.resolution == "stopped_insufficient"
            assert decision.ledger_ref is None
            assert decision.generation_result_ref is None
            assert decision.application_ref is None
            assert decision.resolved_at is not None
            assert session.execute(
                select(LocalContinuationWorkRow.work_id).where(
                    LocalContinuationWorkRow.unit_id == unstarted_item.unit_id
                )
            ).scalar_one_or_none() is None

        after = rows_for(case)
        assert after["task"]["recovery_status"] == "stopped_insufficient"
        assert after["task"]["credit_cost"] == 3
        assert after["wallet"][0]["credits"] == 0
        assert len(after["fees"]) == 3
        assert len(after["creditLogs"]) == 1
        assert after["applications"] == []
    finally:
        case.engine.dispose()


def test_paid_application_drains_then_p6_completes_with_unstarted_stop(tmp_path):
    case = admit_batch(
        tmp_path,
        request_indexes=(2, 0, 1),
        wallet_credits=3,
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="stop-drain-intent")
        paid_item, paid_intent = prepare_item(case, claim, 0, intents=intents)
        paid_receipt = persist_result(case, claim, paid_intent, text="paid frame")
        assert not isinstance(paid_receipt, str)
        paid_work = next(row for row in rows_for(case)["work"] if row.unit_id == paid_item.unit_id)
        assert fee_adapter(case).resolve_current_fee(claim, paid_work.work_id).disposition == "charged"

        insufficient_item, insufficient_intent = prepare_item(case, claim, 1, intents=intents)
        insufficient_receipt = persist_result(case, claim, insufficient_intent, text="insufficient frame")
        assert not isinstance(insufficient_receipt, str)
        insufficient_work = next(
            row for row in rows_for(case)["work"] if row.unit_id == insufficient_item.unit_id
        )
        assert fee_adapter(case).resolve_current_fee(
            claim, insufficient_work.work_id
        ).disposition == "stopped_insufficient"

        unstarted = locked_batch(case).verified.items[2]
        before_apply = rows_for(case)
        assert len(before_apply["fees"]) == 3
        assert len(before_apply["work"]) == 2
        assert not any(row.unit_id == unstarted.unit_id for row in before_apply["work"])
        assert not any(row.unit_id == unstarted.unit_id for row in before_apply["receipts"])

        assert apply_current(case, claim, paid_item.unit_id).kind == "applied"
        applied_rows = rows_for(case)
        assert applied_rows["task"]["status"] == "processing"
        assert next(row for row in applied_rows["work"] if row.unit_id == paid_item.unit_id).phase == "finalize_pending"
        assert finalize_current(case, claim, paid_item.unit_id).disposition == "completed"

        after = rows_for(case)
        assert after["task"]["status"] == "completed"
        assert after["task"]["credit_cost"] == 3
        assert after["task"]["billing_status"] == "charged"
        assert after["wallet"][0]["credits"] == 0
        assert json.loads(after["task"]["result"]) == [
            {"index": 2, "optimized_text": "paid frame"}
        ]
        assert len(after["fees"]) == 3
        assert len(after["work"]) == 2
        assert len(after["applications"]) == 1
        assert len(after["creditLogs"]) == 1
        assert next(row for row in after["work"] if row.unit_id == paid_item.unit_id).phase == "done"
        assert next(row for row in after["work"] if row.unit_id == insufficient_item.unit_id).phase == "stopped_insufficient"
    finally:
        case.engine.dispose()


def test_insufficient_anchor_and_unstarted_stop_roll_back_together(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1), wallet_credits=3)
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="stop-rollback-intent")
        paid_item, paid_intent = prepare_item(case, claim, 0, intents=intents)
        assert not isinstance(persist_result(case, claim, paid_intent), str)
        paid_work = next(row for row in rows_for(case)["work"] if row.unit_id == paid_item.unit_id)
        assert fee_adapter(case).resolve_current_fee(claim, paid_work.work_id).disposition == "charged"

        insufficient_item, prepared = prepare_item(case, claim, 1, intents=intents)
        assert not isinstance(persist_result(case, claim, prepared), str)
        insufficient_work = next(
            row for row in rows_for(case)["work"] if row.unit_id == insufficient_item.unit_id
        )
        fees = fee_adapter(case)
        state = {"abort": True}

        def abort_fee_commit(session):
            if session.bind is case.engine and state["abort"]:
                state["abort"] = False
                raise RuntimeError("owned insufficient-stop transaction rollback")

        event.listen(Session, "before_commit", abort_fee_commit)
        try:
            outcome = fees.resolve_current_fee(claim, insufficient_work.work_id)
        finally:
            event.remove(Session, "before_commit", abort_fee_commit)

        assert outcome.disposition == "pending"
        rolled_back = rows_for(case)
        assert rolled_back["task"]["recovery_status"] == "ready"
        assert len(rolled_back["fees"]) == 1
        assert len(rolled_back["creditLogs"]) == 1
        assert len(rolled_back["work"]) == 2
        assert next(row for row in rolled_back["work"] if row.unit_id == insufficient_item.unit_id).phase == "fee_pending"
        unstarted = locked_batch(case).verified.items[2]
        assert not any(row.unit_id == unstarted.unit_id for row in rolled_back["fees"])

        retry = fees.resolve_current_fee(claim, insufficient_work.work_id)
        assert retry.disposition == "stopped_insufficient"
        committed = rows_for(case)
        assert committed["task"]["recovery_status"] == "stopped_insufficient"
        assert len(committed["fees"]) == 3
        assert len(committed["creditLogs"]) == 1
        assert not any(row.unit_id == unstarted.unit_id for row in committed["work"])
    finally:
        case.engine.dispose()
