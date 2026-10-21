"""Whole-batch pre-apply fee settlement for frame.optimize results."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import UserCreditRow
from haoai_backend.generation_contracts.types import TextResult
from generation_core.test_admission_staging import ModelSource
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    claim_adapter,
    claim_one,
    fee_adapter,
    intent_adapter,
    persist_result,
    persist_result_and_fee,
    prepare_item,
    result_adapter,
    rows_for,
)


def test_fee_final_task_cas_rejects_lease_expired_during_ledger_id_creation(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        receipt = persist_result(case, claim, prepared, text="fee lease boundary")
        assert not isinstance(receipt, str)

        fees = fee_adapter(case, prefix="fee-lease-boundary")
        original_id_factory = fees.id_factory
        generated_ids = []

        def expire_lease_while_generating_ledger_id():
            case.clock[0] = claim.lease_until + timedelta(seconds=1)
            ledger_id = original_id_factory()
            generated_ids.append(ledger_id)
            return ledger_id

        fees.id_factory = expire_lease_while_generating_ledger_id
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        outcome = fees.resolve_current_fee(claim, work.work_id)

        assert generated_ids and len(generated_ids[0]) == 36
        assert outcome.disposition == "pending"
        rows = rows_for(case)
        assert len(rows["receipts"]) == len(rows["evidence"]) == len(rows["work"]) == 1
        assert rows["receipts"][0].result_ref == receipt.result_ref
        assert rows["fees"] == rows["creditLogs"] == []
        assert rows["units"][0]["billing_status"] == "unbilled"
        assert rows["units"][0]["debited_amount"] == 0
        assert rows["work"][0].phase == "fee_pending"
        assert rows["wallet"][0]["credits"] == 500
    finally:
        case.engine.dispose()


def test_timely_result_after_original_deadline_can_charge_under_live_task_lease(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_adapter(case, token="fee-after-original-deadline", lease_seconds=9000).claim_next(
            "batch-optimize", "fee-deadline-worker"
        )
        assert claim is not None
        item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        case.clock[0] = prepared.intent.deadline_at - timedelta(seconds=1)
        receipt = result_adapter(case, prefix="fee-after-deadline-result").persist_success(
            claim,
            prepared.intent,
            prepared.verified_batch,
            prepared.verified_item,
            TextResult("timely output, later settlement"),
        )
        assert not isinstance(receipt, str)
        assert receipt.late_disposition is None

        case.clock[0] = prepared.intent.deadline_at + timedelta(seconds=1)
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        outcome = fee_adapter(case, prefix="fee-after-deadline-charge").resolve_current_fee(
            claim, work.work_id
        )

        assert outcome.disposition == "charged"
        rows = rows_for(case)
        assert rows["task"]["lease_until"] > case.clock[0].replace(tzinfo=None)
        assert rows["task"]["credit_cost"] == 3
        assert rows["fees"][0].generation_result_ref == receipt.result_ref
        assert rows["wallet"][0]["credits"] == 497
    finally:
        case.engine.dispose()


def test_nonlinear_three_unit_results_charge_once_and_allow_the_next_intent(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claim = claim_one(case)
        original_task = rows_for(case)["task"]
        intents = intent_adapter(case, prefix="fee-sequence-intent")
        fees = fee_adapter(case, prefix="fee-sequence")

        for order, frame_index in enumerate((2, 0, 1)):
            item, prepared = prepare_item(case, claim, order, intents=intents)
            assert item.frame_index == frame_index
            assert prepared.disposition == "committed"
            assert prepared.intent is not None
            assert prepared.intent.provider_idempotency_key is None
            assert prepared.intent.external_submission_id

            receipt, fee = persist_result_and_fee(
                case,
                claim,
                prepared,
                fees,
                text=f"optimized frame {frame_index}",
            )
            assert fee.disposition == "charged"
            assert fee.unit_id == item.unit_id
            assert fee.result_ref == receipt.result_ref
            assert fee.amount == 3
            replay = result_adapter(case).persist_success(
                claim,
                prepared.intent,
                prepared.verified_batch,
                prepared.verified_item,
                TextResult(f"optimized frame {frame_index}"),
            )
            assert replay == receipt

            rows = rows_for(case)
            assert rows["task"]["status"] == "processing"
            assert rows["task"]["claim_token"] == original_task["claim_token"]
            assert rows["task"]["execution_generation"] == original_task["execution_generation"]
            assert rows["task"]["lease_until"] == original_task["lease_until"]
            assert rows["task"]["result"] is None
            assert rows["task"]["credit_cost"] == 3 * (order + 1)
            assert rows["task"]["billing_status"] == "charged"
            assert len(rows["receipts"]) == len(rows["evidence"]) == len(rows["work"]) == order + 1
            assert len(rows["fees"]) == len(rows["creditLogs"]) == order + 1
            assert rows["applications"] == []
            assert rows["wallet"][0]["credits"] == 500 - 3 * (order + 1)

            unit = next(row for row in rows["units"] if row["id"] == item.unit_id)
            decision = next(row for row in rows["fees"] if row.unit_id == item.unit_id)
            ledger = next(row for row in rows["creditLogs"] if row.billing_unit_id == item.unit_id)
            work = next(row for row in rows["work"] if row.unit_id == item.unit_id)
            assert unit["billing_status"] == "charged"
            assert unit["debited_amount"] == 3
            assert decision.charge_phase == "pre_apply"
            assert decision.resolution == "charged"
            assert decision.generation_result_ref == receipt.result_ref
            assert decision.application_ref is None
            assert decision.ledger_ref == ledger.id
            assert ledger.amount == -3
            assert ledger.business_key == f"debit:{item.unit_id}"
            assert work.phase == "delivery_pending"
            assert work.phase_version == 1

        final_rows = rows_for(case)
        assert final_rows["wallet"][0]["credits"] == 491
        assert len({row.business_key for row in final_rows["creditLogs"]}) == 3

        first_work = final_rows["work"][0]
        replay = fees.resolve_current_fee(claim, first_work.work_id)
        assert replay.disposition == "ownership_lost"
        assert len(rows_for(case)["creditLogs"]) == 3
        assert rows_for(case)["wallet"][0]["credits"] == 491
    finally:
        case.engine.dispose()


def test_byok_zero_quote_creates_no_wallet_or_ledger(tmp_path):
    case = admit_batch(
        tmp_path,
        request_indexes=(0,),
        model_source=ModelSource.USER,
        prompt_source=None,
        wallet_credits=None,
    )
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        assert prepared.disposition == "committed"
        assert item.snapshot.credential_binding_ref
        receipt, fee = persist_result_and_fee(
            case,
            claim,
            prepared,
            fee_adapter(case, prefix="byok-zero"),
            text="BYOK output",
        )
        rows = rows_for(case)
        assert fee.disposition == "no_charge"
        assert fee.amount == 0
        assert fee.result_ref == receipt.result_ref
        assert rows["wallet"] == []
        assert rows["creditLogs"] == []
        assert len(rows["fees"]) == 1
        assert rows["fees"][0].resolution == "no_charge"
        assert rows["fees"][0].ledger_ref is None
        assert rows["units"][0]["billing_status"] == "no_charge"
        assert rows["task"]["billing_status"] == "no_charge"
        assert rows["task"]["credit_cost"] == 0
    finally:
        case.engine.dispose()


def test_insufficient_fee_stops_new_intents_even_after_wallet_recharge(tmp_path):
    case = admit_batch(tmp_path, frame_count=3, request_indexes=(2, 0, 1), wallet_credits=9)
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="insufficient-intent")
        first, prepared = prepare_item(case, claim, 0, intents=intents)
        assert first.frame_index == 2
        receipt = persist_result(case, claim, prepared, text="first paid result")
        assert not isinstance(receipt, str)
        with case.engine.begin() as connection:
            connection.execute(
                update(UserCreditRow.__table__)
                .where(UserCreditRow.__table__.c.user_id == case.actor_id)
                .values(credits=3)
            )
        work = next(row for row in rows_for(case)["work"] if row.unit_id == first.unit_id)
        fees = fee_adapter(case, prefix="insufficient-fee")
        fee = fees.resolve_current_fee(
            claim, work.work_id
        )
        assert fee.disposition == "charged"
        assert fee.result_ref == receipt.result_ref
        assert fee.amount == 3

        second, second_intent = prepare_item(case, claim, 1, intents=intents)
        assert second.frame_index == 0
        assert second_intent.disposition == "committed"
        second_receipt = persist_result(case, claim, second_intent, text="second result")
        assert not isinstance(second_receipt, str)
        second_work = next(row for row in rows_for(case)["work"] if row.unit_id == second.unit_id)
        stopped_fee = fees.resolve_current_fee(claim, second_work.work_id)
        assert stopped_fee.disposition == "stopped_insufficient"
        assert stopped_fee.result_ref == second_receipt.result_ref

        rows = rows_for(case)
        assert rows["task"]["recovery_status"] == "stopped_insufficient"
        assert rows["task"]["credit_cost"] == 3
        assert rows["task"]["billing_status"] == "charged"
        first_fee = next(row for row in rows["fees"] if row.unit_id == first.unit_id)
        second_fee = next(row for row in rows["fees"] if row.unit_id == second.unit_id)
        assert first_fee.resolution == "charged"
        assert first_fee.ledger_ref == rows["creditLogs"][0].id
        assert second_fee.resolution == "stopped_insufficient"
        assert second_fee.ledger_ref is None
        first_work = next(row for row in rows["work"] if row.unit_id == first.unit_id)
        second_work = next(row for row in rows["work"] if row.unit_id == second.unit_id)
        assert first_work.phase == "delivery_pending"
        assert second_work.phase == "stopped_insufficient"
        assert second_work.terminal_reason == "insufficient_credits"
        assert rows["wallet"][0]["credits"] == 0
        assert len(rows["creditLogs"]) == 1
        assert rows["applications"] == []

        with case.engine.begin() as connection:
            connection.execute(
                update(UserCreditRow.__table__)
                .where(UserCreditRow.__table__.c.user_id == case.actor_id)
                .values(credits=50)
            )
        third, third_intent = prepare_item(case, claim, 2, intents=intents)
        assert third.frame_index == 1
        assert third_intent.disposition == "ownership_lost"
        assert third_intent.intent is None
        after = rows_for(case)
        assert len(after["external"]) == 2
        assert len(after["receipts"]) == 2
        assert len(after["fees"]) == 2
        assert len(after["creditLogs"]) == 1
        assert after["wallet"][0]["credits"] == 50
        assert next(row for row in after["work"] if row.unit_id == first.unit_id).phase == "delivery_pending"
        assert next(row for row in after["work"] if row.unit_id == second.unit_id).phase == "stopped_insufficient"
    finally:
        case.engine.dispose()


def test_inflight_sibling_result_is_saved_as_stopped_after_insufficient_gate(tmp_path):
    case = admit_batch(tmp_path, frame_count=3, request_indexes=(2, 0), wallet_credits=6)
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="insufficient-flight")
        first, first_intent = prepare_item(case, claim, 0, intents=intents)
        second, second_intent = prepare_item(case, claim, 1, intents=intents)
        assert first.frame_index == 2
        assert second.frame_index == 0
        assert first_intent.disposition == second_intent.disposition == "committed"

        first_receipt = persist_result(case, claim, first_intent, text="first real output")
        assert not isinstance(first_receipt, str)
        with case.engine.begin() as connection:
            connection.execute(
                update(UserCreditRow.__table__)
                .where(UserCreditRow.__table__.c.user_id == case.actor_id)
                .values(credits=2)
            )
        first_work = next(row for row in rows_for(case)["work"] if row.unit_id == first.unit_id)
        first_fee = fee_adapter(case, prefix="insufficient-flight-first").resolve_current_fee(
            claim, first_work.work_id
        )
        assert first_fee.disposition == "stopped_insufficient"

        second_receipt = persist_result(case, claim, second_intent, text="inflight output")
        assert not isinstance(second_receipt, str)
        rows = rows_for(case)
        second_work = next(row for row in rows["work"] if row.unit_id == second.unit_id)
        second_fee = next(row for row in rows["fees"] if row.unit_id == second.unit_id)
        assert second_receipt.unit_id == second.unit_id
        assert second_work.phase == "stopped_insufficient"
        assert second_work.phase_version == 1
        assert second_work.terminal_reason == "insufficient_credits"
        assert second_fee.resolution == "stopped_insufficient"
        assert second_fee.generation_result_ref == second_receipt.result_ref
        assert second_fee.ledger_ref is None
        assert len(rows["receipts"]) == len(rows["evidence"]) == len(rows["work"]) == 2
        assert len(rows["fees"]) == 2
        assert rows["creditLogs"] == rows["applications"] == []
        assert rows["task"]["recovery_status"] == "stopped_insufficient"
        assert rows["task"]["credit_cost"] == 0
        assert rows["wallet"][0]["credits"] == 2
    finally:
        case.engine.dispose()


def test_fee_commit_acknowledgement_loss_confirms_one_debit_and_ledger(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        _item, prepared = prepare_item(case, claim)
        from generation_execution.frame_optimize_execution_helpers import persist_result

        persist_result(case, claim, prepared, text="fee ack output")
        work = rows_for(case)["work"][0]
        fees = fee_adapter(case, prefix="fee-ack-lost")
        state = {"lose": True}

        def lose_ack_once(session: Session) -> None:
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                raise RuntimeError("fixture lost fee commit acknowledgement")

        event.listen(Session, "after_commit", lose_ack_once)
        try:
            outcome = fees.resolve_current_fee(claim, work.work_id)
        finally:
            event.remove(Session, "after_commit", lose_ack_once)

        rows = rows_for(case)
        assert state["lose"] is False
        assert outcome.disposition == "charged"
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 1
        assert rows["wallet"][0]["credits"] == 497
        assert rows["fees"][0].ledger_ref == rows["creditLogs"][0].id
        assert rows["work"][0].phase == "delivery_pending"
    finally:
        case.engine.dispose()


def test_fee_precommit_rollback_leaves_original_result_and_can_retry_once(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        _item, prepared = prepare_item(case, claim)
        from generation_execution.frame_optimize_execution_helpers import persist_result

        persist_result(case, claim, prepared, text="fee rollback output")
        work = rows_for(case)["work"][0]
        fees = fee_adapter(case, prefix="fee-rollback")
        state = {"rollback": True}

        def fail_before_commit(session: Session) -> None:
            if session.bind is case.engine and state["rollback"]:
                state["rollback"] = False
                raise RuntimeError("fixture rolled back before fee commit")

        event.listen(Session, "before_commit", fail_before_commit)
        try:
            pending = fees.resolve_current_fee(claim, work.work_id)
        finally:
            event.remove(Session, "before_commit", fail_before_commit)

        assert state["rollback"] is False
        assert pending.disposition == "pending"
        rows = rows_for(case)
        assert len(rows["receipts"]) == len(rows["evidence"]) == len(rows["work"]) == 1
        assert rows["work"][0].phase == "fee_pending"
        assert rows["fees"] == rows["creditLogs"] == []
        assert rows["wallet"][0]["credits"] == 500

        retry = fees.resolve_current_fee(claim, work.work_id)
        rows = rows_for(case)
        assert retry.disposition == "charged"
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 1
        assert rows["wallet"][0]["credits"] == 497
    finally:
        case.engine.dispose()
