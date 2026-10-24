"""Unknown provider results remain observations, never receipts or charges."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta, timezone
from threading import Event

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    apply_current,
    application_adapter,
    claim_adapter,
    claim_one,
    cancellation_adapter,
    fee_adapter,
    finalize_current,
    finalization_adapter,
    intent_adapter,
    persist_result,
    prepare_item,
    recovery_adapter,
    result_adapter,
    rows_for,
)
from haoai_backend.generation_contracts.types import OwnerTaskScope, TextResult
from haoai_backend.generation_core.base_schema import BillingUnitRow, ResultEvidenceRow, UserCreditRow
from haoai_backend.generation_core.schema import ExecutionAttemptGroupRow, PolicyStopEventRow
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.generation_core.frame_optimize_recovery import (
    SqlAlchemyFrameOptimizeRecovery,
)
from haoai_backend.task_observation.cancellation import SqlTaskCancellationWriter


def test_unknown_result_is_persisted_as_observation_without_application(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert prepared.verified_batch is not None
        assert prepared.verified_item is not None

        recovery_adapter(case).record_observation(
            claim,
            prepared.intent,
            prepared.verified_batch,
            prepared.verified_item,
            code="provider_outcome_unknown",
            request_started=True,
            http_status=503,
            observed_text="late but unselected text",
        )

        state = rows_for(case)
        assert len(state["evidence"]) == 1
        observation = state["evidence"][0]
        assert observation["source"] == "worker"
        assert observation["evidence_kind"] == "frame_optimize_observation_v1"
        assert observation["validity"] == "unknown"
        payload = json.loads(observation["result_location"])
        assert payload == {
            "code": "provider_outcome_unknown",
            "http_status": 503,
            "observed_text": "late but unselected text",
            "request_started": True,
        }
        assert state["receipts"] == []
        assert state["fees"] == []
        assert state["applications"] == []
        assert state["work"] == []
    finally:
        case.engine.dispose()


def test_repeated_unknown_observation_keeps_original_evidence_and_projects_live_claim(
    tmp_path,
) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert prepared.verified_batch is not None
        assert prepared.verified_item is not None
        recovery = recovery_adapter(case)
        observation = {
            "code": "provider_outcome_unknown",
            "request_started": True,
            "http_status": 503,
            "observed_text": "provider response not selected",
        }

        assert recovery.record_observation(
            claim, prepared.intent, prepared.verified_batch, prepared.verified_item, **observation
        ) == "pending"
        before = rows_for(case)
        original_evidence_id = before["evidence"][0]["id"]
        case.clock[0] += timedelta(seconds=1)

        assert recovery.record_observation(
            claim, prepared.intent, prepared.verified_batch, prepared.verified_item, **observation
        ) == "pending"
        after = rows_for(case)

        assert len(after["evidence"]) == 1
        assert after["evidence"][0]["id"] == original_evidence_id
        assert after["task"]["updated_at"] != before["task"]["updated_at"]
        assert after["task"]["status"] == "processing"
        assert after["task"]["claimed_by"] == claim.worker_instance
        assert after["task"]["claim_token"] == claim.token
        assert after["task"]["execution_generation"] == claim.execution_generation
        assert after["receipts"] == []
        assert after["work"] == []
        assert after["fees"] == []
        assert after["applications"] == []
    finally:
        case.engine.dispose()


def test_repeated_observation_confirms_original_evidence_after_commit_ack_loss(
    tmp_path, monkeypatch
) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert prepared.verified_batch is not None
        assert prepared.verified_item is not None
        recovery = recovery_adapter(case)
        observation = {
            "code": "provider_outcome_unknown",
            "request_started": True,
            "http_status": 503,
            "observed_text": "provider response not selected",
        }
        recovery.record_observation(
            claim, prepared.intent, prepared.verified_batch, prepared.verified_item, **observation
        )
        before = rows_for(case)
        original_evidence_id = before["evidence"][0]["id"]
        case.clock[0] += timedelta(seconds=1)

        confirmed_ids: list[str] = []
        original_confirm = SqlAlchemyFrameOptimizeRecovery._confirm_observation

        def record_confirmation(self, evidence_key, evidence_id, *args):
            if self is recovery:
                confirmed_ids.append(evidence_id)
            return original_confirm(self, evidence_key, evidence_id, *args)

        monkeypatch.setattr(
            SqlAlchemyFrameOptimizeRecovery,
            "_confirm_observation",
            record_confirmation,
        )
        state = {"lose_ack": True}

        def lose_commit_ack(session: Session) -> None:
            if session.bind is case.engine and state["lose_ack"]:
                state["lose_ack"] = False
                raise RuntimeError("synthetic observation commit acknowledgement loss")

        event.listen(Session, "after_commit", lose_commit_ack)
        try:
            recovery.record_observation(
                claim,
                prepared.intent,
                prepared.verified_batch,
                prepared.verified_item,
                **observation,
            )
        finally:
            event.remove(Session, "after_commit", lose_commit_ack)

        after = rows_for(case)
        assert confirmed_ids == [original_evidence_id]
        assert len(after["evidence"]) == 1
        assert after["evidence"][0]["id"] == original_evidence_id
        assert after["task"]["updated_at"] != before["task"]["updated_at"]
        assert after["receipts"] == []
        assert after["work"] == []
        assert after["fees"] == []
        assert after["applications"] == []
    finally:
        case.engine.dispose()


@pytest.mark.parametrize(
    ("offset", "is_due"),
    [
        pytest.param(timedelta(microseconds=-1), False, id="one-microsecond-before"),
        pytest.param(timedelta(0), True, id="at-deadline"),
        pytest.param(timedelta(microseconds=1), True, id="one-microsecond-after"),
    ],
)
def test_due_intent_is_recorded_only_at_its_original_deadline(tmp_path, offset, is_due) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="opt-due-recovery")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert claims.release_claim(claim)

        case.clock[0] = prepared.intent.deadline_at + offset
        recovery = recovery_adapter(case)
        outcomes = recovery.scan_due()

        after = rows_for(case)
        due_unit = next(unit for unit in after["units"] if unit["id"] == item.unit_id)
        due_facts = [row for row in after["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1"]
        if is_due:
            assert outcomes == ("terminal_projection",)
            assert due_unit["result_status"] == "unknown"
            assert after["task"]["status"] == "failed"
            assert after["steps"][0]["execution_status"] == "reconciling"
            assert after["external"][0]["submission_status"] == "unknown"
            assert len(due_facts) == 1
            received_at = due_facts[0]["received_at"].replace(tzinfo=timezone.utc)
            assert received_at >= prepared.intent.deadline_at
            external = after["external"][0]
            payload = json.loads(due_facts[0]["result_location"])
            assert due_facts[0]["evidence_key"] == (
                f"frame-optimize-due-v1:{prepared.intent.external_submission_id}"
            )
            assert payload == {
                "owner_id": case.actor_id,
                "task_id": case.task_id,
                "unit_id": item.unit_id,
                "step_id": prepared.intent.step_id,
                "submission_id": prepared.intent.external_submission_id,
                "submission_key": external["submission_key"],
                "request_digest": prepared.intent.request_digest,
                "first_intent_at": prepared.intent.first_intent_at.isoformat(
                    timespec="microseconds"
                ),
                "deadline_at": prepared.intent.deadline_at.isoformat(
                    timespec="microseconds"
                ),
                "received_at": received_at.isoformat(timespec="microseconds"),
            }

            # A late result cannot convert the deadline control into a receipt or Work.
            late = result_adapter(case).persist_success(
                claim,
                prepared.intent,
                prepared.verified_batch,
                prepared.verified_item,
                TextResult("late provider text"),
            )
            assert late in {"pending", "ownership_lost"}
            after_late = rows_for(case)
            assert after_late["receipts"] == []
            assert after_late["work"] == []
            assert after_late["fees"] == []
            assert after_late["creditLogs"] == []
            assert after_late["applications"] == []

            scope = OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize")
            assert recovery._project_due(scope, None) == "settled"
            repeated = rows_for(case)
            repeated_due = [
                row
                for row in repeated["evidence"]
                if row["evidence_kind"] == "frame_optimize_due_v1"
            ]
            assert [row["id"] for row in repeated_due] == [due_facts[0]["id"]]
        else:
            assert outcomes == ("pending",)
            assert due_unit["result_status"] == "pending"
            assert after["task"]["status"] == "reconciling"
            assert after["steps"][0]["execution_status"] == "submitting"
            assert after["external"][0]["submission_status"] == "submitting"
            assert due_facts == []
        assert after["receipts"] == []
        assert after["work"] == []
        assert after["fees"] == []
        assert after["creditLogs"] == []
        assert after["applications"] == []
    finally:
        case.engine.dispose()


def test_due_projection_rechecks_claim_lease_at_commit_time(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="opt-due-commit-lease", lease_seconds=8000)
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None

        before = rows_for(case)
        recovery = recovery_adapter(case)
        commit_times = iter(
            (
                claim.lease_until + timedelta(seconds=1),
                prepared.intent.deadline_at + timedelta(seconds=1),
            )
        )
        recovery.db_clock = lambda: next(commit_times)

        outcome = recovery._project_due(
            OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize"), None
        )

        after = rows_for(case)
        assert outcome == "pending"
        assert after["task"] == before["task"]
        assert after["units"] == before["units"]
        assert after["steps"] == before["steps"]
        assert after["external"] == before["external"]
        assert after["evidence"] == before["evidence"]
        assert after["receipts"] == before["receipts"] == []
        assert after["fees"] == before["fees"] == []
        assert after["creditLogs"] == before["creditLogs"] == []
        assert after["applications"] == before["applications"] == []
        assert after["work"] == before["work"] == []
    finally:
        case.engine.dispose()


def test_due_scan_waits_for_live_generation_lease_and_ignores_units_without_intent(tmp_path) -> None:
    live_path = tmp_path / "live"
    live_path.mkdir()
    live_case = admit_batch(live_path, frame_count=1, request_indexes=(0,))
    try:
        claims = claim_adapter(live_case, token="opt-due-live-lease", lease_seconds=8000)
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        item, prepared = prepare_item(live_case, claim)
        assert prepared.intent is not None
        live_case.clock[0] = prepared.intent.deadline_at

        assert recovery_adapter(live_case).scan_due() == ("pending",)
        after = rows_for(live_case)
        unit = next(row for row in after["units"] if row["id"] == item.unit_id)
        assert after["task"]["status"] == "processing"
        lease_until = after["task"]["lease_until"].replace(tzinfo=timezone.utc)
        assert lease_until > live_case.clock[0]
        assert unit["result_status"] == "pending"
        assert after["evidence"] == []
    finally:
        live_case.engine.dispose()

    unstarted_path = tmp_path / "unstarted"
    unstarted_path.mkdir()
    unstarted_case = admit_batch(unstarted_path, frame_count=1, request_indexes=(0,))
    try:
        unstarted_case.clock[0] += timedelta(days=1)
        assert recovery_adapter(unstarted_case).scan_due() == ("pending",)
        after = rows_for(unstarted_case)
        assert after["task"]["status"] == "queued"
        assert after["units"][0]["first_intent_at"] is None
        assert after["units"][0]["result_status"] == "pending"
        assert after["external"] == []
        assert after["evidence"] == []
    finally:
        unstarted_case.engine.dispose()


def test_due_scan_reopens_only_a_prepared_sibling_without_reusing_expired_intent(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=2, request_indexes=(0, 1))
    try:
        first_claims = claim_adapter(case, token="opt-due-first-claim")
        first_claim = first_claims.claim_next("batch-optimize", "optimize-worker")
        assert first_claim is not None
        due_item, due_intent = prepare_item(case, first_claim, 0)
        assert due_intent.intent is not None
        assert first_claims.release_claim(first_claim)

        case.clock[0] = due_intent.intent.deadline_at
        recovery = recovery_adapter(case)
        assert recovery.scan_due() == ("retained",)
        before = rows_for(case)
        due_row = next(row for row in before["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1")
        due_external_before = next(
            row for row in before["external"] if row["step_id"] == due_intent.intent.step_id
        )
        due_unit = next(row for row in before["units"] if row["id"] == due_item.unit_id)
        assert before["task"]["status"] == "reconciling"
        assert due_unit["result_status"] == "unknown"

        sibling_claim = claim_adapter(case, token="opt-due-sibling-claim").claim_next(
            "batch-optimize", "optimize-worker"
        )
        assert sibling_claim is not None
        sibling_item, sibling_intent = prepare_item(case, sibling_claim, 1)
        assert sibling_item.unit_id != due_item.unit_id
        assert sibling_intent.disposition == "committed"

        after = rows_for(case)
        assert len(after["external"]) == 2
        expired_external = next(row for row in after["external"] if row["step_id"] == due_intent.intent.step_id)
        assert expired_external["submission_status"] == "unknown"
        assert expired_external["submission_key"] == due_external_before["submission_key"]
        due_unit_after = next(row for row in after["units"] if row["id"] == due_item.unit_id)
        assert due_unit_after["result_status"] == "unknown"
        due_after = [row for row in after["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1"]
        assert [row["id"] for row in due_after] == [due_row["id"]]
        assert after["receipts"] == after["work"] == []
    finally:
        case.engine.dispose()


def test_due_deadline_preserves_successful_application_and_financial_facts(tmp_path) -> None:
    case = admit_batch(
        tmp_path,
        frame_count=2,
        request_indexes=(0, 1),
        include_source_tables=True,
    )
    try:
        claims = claim_adapter(case, token="opt-due-mixed-claim")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        intents = intent_adapter(case, prefix="due-mixed-intent")
        first_item, first_prepared = prepare_item(case, claim, 0, intents=intents)
        first_receipt = persist_result(case, claim, first_prepared, text="already applied")
        assert not isinstance(first_receipt, str)
        work = next(row for row in rows_for(case)["work"] if row.unit_id == first_item.unit_id)
        assert fee_adapter(case).resolve_current_fee(claim, work.work_id).disposition == "charged"
        assert apply_current(case, claim, first_item.unit_id).kind == "applied"
        assert finalize_current(case, claim, first_item.unit_id).disposition == "work_finalized"

        due_item, due_prepared = prepare_item(case, claim, 1, intents=intents)
        assert due_prepared.intent is not None
        assert claims.release_claim(claim)
        before = rows_for(case)
        applied_unit_before = next(row for row in before["units"] if row["id"] == first_item.unit_id)
        assert applied_unit_before["result_status"] == "succeeded"
        before_money = (before["task"]["credit_cost"], before["task"]["billing_status"], len(before["creditLogs"]))

        case.clock[0] = due_prepared.intent.deadline_at
        assert recovery_adapter(case).scan_due() == ("terminal_projection",)

        after = rows_for(case)
        due_unit = next(row for row in after["units"] if row["id"] == due_item.unit_id)
        applied_unit = next(row for row in after["units"] if row["id"] == first_item.unit_id)
        assert after["task"]["status"] == "completed"
        assert json.loads(after["task"]["result"]) == [
            {"index": first_item.frame_index, "optimized_text": "already applied"}
        ]
        assert due_unit["result_status"] == "unknown"
        assert applied_unit["result_status"] == "succeeded"
        assert (after["task"]["credit_cost"], after["task"]["billing_status"], len(after["creditLogs"])) == before_money
        assert len(after["applications"]) == len(before["applications"]) == 1
        assert len(after["receipts"]) == len(before["receipts"]) == 1
        assert len(after["fees"]) == len(before["fees"]) == 1
        assert not any(row.unit_id == due_item.unit_id for row in after["fees"])
        assert len([row for row in after["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1"]) == 1
    finally:
        case.engine.dispose()


def test_due_deadline_keeps_timely_continuation_work_at_each_existing_phase(tmp_path) -> None:
    case = admit_batch(
        tmp_path,
        frame_count=4,
        request_indexes=(0, 1, 2, 3),
        include_source_tables=True,
    )
    try:
        claims = claim_adapter(case, token="opt-due-work-claim")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        intents = intent_adapter(case, prefix="due-work-intent")
        fees = fee_adapter(case, prefix="due-work-fee")
        for order in range(3):
            item, prepared = prepare_item(case, claim, order, intents=intents)
            receipt = persist_result(case, claim, prepared, text=f"timely {item.frame_index}")
            assert not isinstance(receipt, str)
            work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
            if order >= 1:
                assert fees.resolve_current_fee(claim, work.work_id).disposition == "charged"
            if order >= 2:
                assert apply_current(case, claim, item.unit_id).kind == "applied"

        due_item, due_prepared = prepare_item(case, claim, 3, intents=intents)
        assert due_prepared.intent is not None
        assert claims.release_claim(claim)
        before = rows_for(case)
        before_work = {
            row.unit_id: (row.work_id, row.phase, row.phase_version, row.fee_decision_ref)
            for row in before["work"]
        }
        assert {phase[1] for phase in before_work.values()} == {
            "fee_pending",
            "delivery_pending",
            "finalize_pending",
        }
        before_result = before["task"]["result"]
        before_financial = (
            before["task"]["credit_cost"],
            before["task"]["billing_status"],
            len(before["creditLogs"]),
            len(before["fees"]),
            len(before["applications"]),
        )

        case.clock[0] = due_prepared.intent.deadline_at
        assert recovery_adapter(case).scan_due() == ("retained",)

        after = rows_for(case)
        due_unit = next(row for row in after["units"] if row["id"] == due_item.unit_id)
        after_work = {
            row.unit_id: (row.work_id, row.phase, row.phase_version, row.fee_decision_ref)
            for row in after["work"]
        }
        assert after["task"]["status"] == "reconciling"
        assert after["task"]["result"] == before_result
        assert due_unit["result_status"] == "unknown"
        assert after_work == before_work
        assert (
            after["task"]["credit_cost"],
            after["task"]["billing_status"],
            len(after["creditLogs"]),
            len(after["fees"]),
            len(after["applications"]),
        ) == before_financial
    finally:
        case.engine.dispose()


def test_due_deadline_work_continues_through_finalization_and_rescan(tmp_path) -> None:
    case = admit_batch(
        tmp_path,
        frame_count=2,
        request_indexes=(0, 1),
        include_source_tables=True,
    )
    try:
        generation_claims = claim_adapter(case, token="opt-due-continuation-parent")
        claim = generation_claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None

        timely_item, timely_prepared = prepare_item(case, claim, 0)
        receipt = persist_result(case, claim, timely_prepared, text="timely result")
        assert not isinstance(receipt, str)
        timely_work = next(
            row for row in rows_for(case)["work"] if row.unit_id == timely_item.unit_id
        )
        assert timely_work.phase == "fee_pending"

        due_item, due_prepared = prepare_item(case, claim, 1)
        assert due_prepared.intent is not None
        assert generation_claims.release_claim(claim)
        case.clock[0] = due_prepared.intent.deadline_at
        recovery = recovery_adapter(case)
        assert recovery.scan_due() == ("retained",)

        fees = fee_adapter(case, prefix="opt-due-continuation-fee")
        continuations = fees.continuation_claims
        applications = application_adapter(case, continuation_claims=continuations)
        finalization = finalization_adapter(case, continuation_claims=continuations)

        continuation = continuations.claim_continuation("batch-optimize", "continuation-worker")
        assert continuation is not None
        fee_outcome = fees.consume_fee(continuation)
        assert fee_outcome.disposition == "charged"
        assert continuations.release_continuation(continuation)

        continuation = continuations.claim_continuation("batch-optimize", "continuation-worker")
        assert continuation is not None
        delivery_outcome = applications.consume_delivery(continuation)
        assert delivery_outcome.kind == "applied"
        assert continuations.release_continuation(continuation)

        continuation = continuations.claim_continuation("batch-optimize", "continuation-worker")
        assert continuation is not None
        finalize_outcome = finalization.consume_finalize(continuation)
        assert finalize_outcome.disposition == "work_finalized"
        assert continuations.release_continuation(continuation)

        before_rescan = rows_for(case)
        due_unit_before = next(row for row in before_rescan["units"] if row["id"] == due_item.unit_id)
        assert before_rescan["task"]["status"] == "reconciling"
        assert before_rescan["wallet"][0]["credits"] == 497
        assert len(before_rescan["creditLogs"]) == 1

        assert recovery.scan_due() == ("terminal_projection",)

        after_rescan = rows_for(case)
        due_unit_after = next(row for row in after_rescan["units"] if row["id"] == due_item.unit_id)
        assert after_rescan["task"]["status"] == "completed"
        assert after_rescan["wallet"][0]["credits"] == 497
        assert len(after_rescan["creditLogs"]) == 1
        assert len(after_rescan["fees"]) == 1
        assert len(after_rescan["applications"]) == 1
        assert len(after_rescan["receipts"]) == 1
        assert after_rescan["work"][0].phase == "done"
        assert due_unit_before["result_status"] == due_unit_after["result_status"] == "unknown"
        assert due_unit_after["billing_status"] == "unbilled"
        assert due_unit_after["debited_amount"] == due_unit_after["refunded_amount"] == 0
        assert not any(row.unit_id == due_item.unit_id for row in after_rescan["fees"])
    finally:
        case.engine.dispose()


def test_due_unit_keeps_existing_insufficient_failure_reason(tmp_path) -> None:
    case = admit_batch(
        tmp_path,
        frame_count=2,
        request_indexes=(0, 1),
    )
    try:
        with case.engine.begin() as connection:
            connection.execute(
                UserCreditRow.__table__.update()
                .where(UserCreditRow.__table__.c.user_id == case.actor_id)
                .values(credits=0)
            )
        claims = claim_adapter(case, token="opt-due-insufficient-claim")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        intents = intent_adapter(case, prefix="due-insufficient-intent")
        insufficient_item, insufficient_prepared = prepare_item(case, claim, 0, intents=intents)
        due_item, due_prepared = prepare_item(case, claim, 1, intents=intents)
        assert insufficient_prepared.intent is not None
        assert due_prepared.intent is not None
        assert not isinstance(persist_result(case, claim, insufficient_prepared), str)
        insufficient_work = next(
            row for row in rows_for(case)["work"] if row.unit_id == insufficient_item.unit_id
        )
        assert fee_adapter(case).resolve_current_fee(
            claim, insufficient_work.work_id
        ).disposition == "stopped_insufficient"
        assert claims.release_claim(claim)

        case.clock[0] = due_prepared.intent.deadline_at
        assert recovery_adapter(case).scan_due() == ("terminal_projection",)

        after = rows_for(case)
        due_unit = next(row for row in after["units"] if row["id"] == due_item.unit_id)
        insufficient_work_after = next(
            row for row in after["work"] if row.unit_id == insufficient_item.unit_id
        )
        assert after["task"]["status"] == "failed"
        assert json.loads(after["task"]["result"]) == {
            "partial": [],
            "error": "insufficient_credits",
        }
        assert insufficient_work_after.phase == "stopped_insufficient"
        assert insufficient_work_after.terminal_reason == "insufficient_credits"
        assert due_unit["result_status"] == "unknown"
        assert len([row for row in after["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1"]) == 1
        assert len(after["fees"]) == 1
        assert after["fees"][0].resolution == "stopped_insufficient"
        assert after["creditLogs"] == []
        assert after["applications"] == []
    finally:
        case.engine.dispose()


def test_cancellation_before_deadline_suppresses_due_fact(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert SqlTaskCancellationWriter(lambda: case.engine.begin()).request_cancel(case.task_id)
        case.clock[0] = prepared.intent.deadline_at

        recovery = recovery_adapter(case)
        scope = OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize")
        assert recovery.consume_cancel_and_due(scope) == "settled"
        after = rows_for(case)
        assert after["task"]["status"] == "failed"
        assert after["task"]["result"] is not None
        assert after["evidence"] == []
        assert after["receipts"] == after["work"] == []
    finally:
        case.engine.dispose()


def test_cancellation_after_due_preserves_unknown_unit_and_original_due_fact(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=2, request_indexes=(0, 1))
    try:
        claims = claim_adapter(case, token="opt-due-cancel-claim")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        item, prepared = prepare_item(case, claim, 0)
        assert prepared.intent is not None
        assert claims.release_claim(claim)
        case.clock[0] = prepared.intent.deadline_at

        recovery = recovery_adapter(case)
        assert recovery.scan_due() == ("retained",)
        due_before = next(
            row for row in rows_for(case)["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1"
        )
        assert cancellation_adapter(case).cancel_owned(TrustedActor(case.actor_id), case.task_id) == "settled"

        after = rows_for(case)
        due_unit = next(row for row in after["units"] if row["id"] == item.unit_id)
        assert after["task"]["status"] == "failed"
        assert due_unit["result_status"] == "unknown"
        assert due_unit["billing_status"] == "unbilled"
        assert due_unit["debited_amount"] == due_unit["refunded_amount"] == 0
        due_after = [row for row in after["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1"]
        assert [row["id"] for row in due_after] == [due_before["id"]]
        assert after["creditLogs"] == []
        assert not after["receipts"] and not after["work"] and not after["applications"]
    finally:
        case.engine.dispose()


def test_due_scan_rolls_back_when_stopped_after_staging_control_fact(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="opt-due-stop-claim")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert claims.release_claim(claim)
        case.clock[0] = prepared.intent.deadline_at
        stop = Event()
        was_inserted = False

        def stop_after_due_insert(connection, cursor, statement, parameters, context, executemany):
            nonlocal was_inserted
            if "INSERT INTO result_evidence" in statement and not was_inserted:
                was_inserted = True
                stop.set()

        event.listen(case.engine, "after_cursor_execute", stop_after_due_insert)
        try:
            scope = OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize")
            outcome = recovery_adapter(case)._project_due(scope, stop)
        finally:
            event.remove(case.engine, "after_cursor_execute", stop_after_due_insert)

        after = rows_for(case)
        due_unit = next(row for row in after["units"] if row["id"] == item.unit_id)
        assert was_inserted
        assert outcome == "pending"
        assert due_unit["result_status"] == "pending"
        assert after["task"]["status"] == "reconciling"
        assert after["external"][0]["submission_status"] == "submitting"
        assert after["evidence"] == []
    finally:
        case.engine.dispose()


@pytest.mark.parametrize(
    "drift",
    ["fact_payload", "fact_request_digest", "unit_deadline", "debit"],
)
def test_due_facts_fail_closed_when_the_original_binding_or_financial_projection_drifts(
    tmp_path, drift
) -> None:
    case = admit_batch(tmp_path, frame_count=2, request_indexes=(0, 1))
    try:
        claims = claim_adapter(case, token=f"opt-due-drift-{drift}")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        due_item, prepared = prepare_item(case, claim, 0)
        assert prepared.intent is not None
        assert claims.release_claim(claim)
        case.clock[0] = prepared.intent.deadline_at
        recovery = recovery_adapter(case)
        assert recovery.scan_due() == ("retained",)

        before = rows_for(case)
        fact = next(row for row in before["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1")
        if drift in {"fact_payload", "fact_request_digest"}:
            if drift == "fact_payload":
                replacement = "{}"
            else:
                payload = json.loads(fact["result_location"])
                payload["request_digest"] = "f" * 64
                replacement = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            digest = hashlib.sha256(replacement.encode("utf-8")).hexdigest()
            with case.engine.begin() as connection:
                connection.execute(
                    ResultEvidenceRow.__table__.update()
                    .where(ResultEvidenceRow.__table__.c.id == fact["id"])
                    .values(result_location=replacement, payload_digest=digest)
                )
        elif drift == "unit_deadline":
            with case.engine.begin() as connection:
                connection.execute(
                    BillingUnitRow.__table__.update()
                    .where(BillingUnitRow.__table__.c.id == due_item.unit_id)
                    .values(
                        deadline_at=prepared.intent.deadline_at.replace(tzinfo=None)
                        + timedelta(seconds=1)
                    )
                )
        else:
            with case.engine.begin() as connection:
                connection.execute(
                    BillingUnitRow.__table__.update()
                    .where(BillingUnitRow.__table__.c.id == due_item.unit_id)
                    .values(debited_amount=1)
                )

        drifted = rows_for(case)
        scope = OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize")
        assert recovery._project_due(scope, None) == "pending"
        after = rows_for(case)
        assert after["task"]["updated_at"] == drifted["task"]["updated_at"]
        assert after["task"]["status"] == "reconciling"
        assert after["units"] == drifted["units"]
        assert after["external"] == drifted["external"]
        assert after["evidence"] == drifted["evidence"]
        assert after["receipts"] == after["work"] == []
        assert after["creditLogs"] == []
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("blocker", ["group", "event"])
def test_due_scan_refuses_unregistered_retry_groups_or_policy_events(tmp_path, blocker) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token=f"opt-due-block-{blocker}")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert claims.release_claim(claim)
        before = rows_for(case)
        unit = before["units"][0]
        step = before["steps"][0]
        external = before["external"][0]
        with case.engine.begin() as connection:
            connection.execute(
                ExecutionAttemptGroupRow.__table__.insert().values(
                    unit_id=unit["id"],
                    group_key="unregistered-due-group",
                    registry_version="unregistered",
                    attempt_step_ids=[step["id"], "second", "third"],
                    original_payload_digest="a" * 64,
                    max_retries=2,
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
                    active_eligibility_version="unregistered",
                )
            )
            if blocker == "event":
                connection.execute(
                    PolicyStopEventRow.__table__.insert().values(
                        event_id="unregistered-due-event",
                        unit_id=unit["id"],
                        group_key="unregistered-due-group",
                        policy_version="unregistered",
                        reason="auth_unavailable_12",
                        reservation_refs=[],
                        observation_refs=[],
                        active_step_id=step["id"],
                        active_submission_id=external["id"],
                        external_id="synthetic-due-external",
                        stopped_at=case.clock[0].replace(tzinfo=None),
                        financial_disposition="no_debit",
                        original_debit_ref=None,
                        refund_ref=None,
                        amount=0,
                        owner_id=case.actor_id,
                        task_id=case.task_id,
                    )
                )
        case.clock[0] = prepared.intent.deadline_at

        assert recovery_adapter(case).scan_due() == ("pending",)
        after = rows_for(case)
        assert after["task"] == before["task"]
        assert after["units"] == before["units"]
        assert after["steps"] == before["steps"]
        assert after["external"] == before["external"]
        assert after["evidence"] == before["evidence"]
        assert after["receipts"] == after["work"] == []
        assert after["fees"] == after["creditLogs"] == after["applications"] == []
    finally:
        case.engine.dispose()


def test_due_commit_ack_loss_confirms_the_same_immutable_control_fact(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="opt-due-ack-loss-claim")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert claims.release_claim(claim)
        case.clock[0] = prepared.intent.deadline_at
        recovery = recovery_adapter(case)
        state = {"lose_ack": True}

        def lose_commit_ack(session: Session) -> None:
            if session.bind is case.engine and state["lose_ack"]:
                state["lose_ack"] = False
                raise RuntimeError("synthetic due commit acknowledgement loss")

        event.listen(Session, "after_commit", lose_commit_ack)
        try:
            scope = OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize")
            outcome = recovery._project_due(scope, None)
        finally:
            event.remove(Session, "after_commit", lose_commit_ack)

        first = rows_for(case)
        due_first = [row for row in first["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1"]
        assert outcome == "terminal_projection"
        assert len(due_first) == 1
        assert recovery._project_due(scope, None) == "settled"
        second = rows_for(case)
        due_second = [row for row in second["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1"]
        assert [row["id"] for row in due_second] == [due_first[0]["id"]]
        assert [row["result_location"] for row in due_second] == [due_first[0]["result_location"]]
    finally:
        case.engine.dispose()


def test_due_ack_loss_rejects_same_id_fact_with_drifted_payload(tmp_path, monkeypatch) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="opt-due-drift-ack")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert claims.release_claim(claim)
        case.clock[0] = prepared.intent.deadline_at + timedelta(seconds=10)
        recovery = recovery_adapter(case)
        original_confirm = SqlAlchemyFrameOptimizeRecovery._confirm_due
        changed: dict[str, object] = {}

        def mutate_before_confirmation(self, *args):
            if self is recovery and not changed:
                fact = next(
                    row
                    for row in rows_for(case)["evidence"]
                    if row["evidence_kind"] == "frame_optimize_due_v1"
                )
                payload = json.loads(fact["result_location"])
                received = fact["received_at"]
                received = (
                    received.astimezone(timezone.utc)
                    if received.tzinfo is not None
                    else received.replace(tzinfo=timezone.utc)
                ) - timedelta(seconds=1)
                payload["received_at"] = received.isoformat(timespec="microseconds")
                payload_text = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                payload_digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
                with case.engine.begin() as connection:
                    connection.execute(
                        ResultEvidenceRow.__table__
                        .update()
                        .where(ResultEvidenceRow.__table__.c.id == fact["id"])
                        .values(
                            received_at=received,
                            result_location=payload_text,
                            payload_digest=payload_digest,
                        )
                    )
                changed.update(
                    id=fact["id"],
                    original_payload=fact["result_location"],
                    original_digest=fact["payload_digest"],
                    original_received_at=(received + timedelta(seconds=1)),
                    received_at=received,
                    payload=payload_text,
                    digest=payload_digest,
                )
            return original_confirm(self, *args)

        monkeypatch.setattr(
            SqlAlchemyFrameOptimizeRecovery, "_confirm_due", mutate_before_confirmation
        )
        state = {"lose_ack": True}

        def lose_commit_ack(session: Session) -> None:
            if session.bind is case.engine and state["lose_ack"]:
                state["lose_ack"] = False
                raise RuntimeError("synthetic due commit acknowledgement loss")

        event.listen(Session, "after_commit", lose_commit_ack)
        try:
            outcome = recovery._project_due(
                OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize"), None
            )
        finally:
            event.remove(Session, "after_commit", lose_commit_ack)

        after = rows_for(case)
        due_facts = [
            row for row in after["evidence"] if row["evidence_kind"] == "frame_optimize_due_v1"
        ]
        assert outcome == "pending"
        assert changed["id"] == due_facts[0]["id"]
        assert len(due_facts) == 1
        changed_received = due_facts[0]["received_at"]
        changed_received = (
            changed_received.astimezone(timezone.utc)
            if changed_received.tzinfo is not None
            else changed_received.replace(tzinfo=timezone.utc)
        )
        assert changed_received == changed["received_at"]
        assert changed_received == changed["original_received_at"] - timedelta(seconds=1)
        assert changed_received >= prepared.intent.deadline_at
        original_payload = json.loads(changed["original_payload"])
        original_payload["received_at"] = changed_received.isoformat(timespec="microseconds")
        assert json.loads(due_facts[0]["result_location"]) == original_payload
        assert due_facts[0]["payload_digest"] != changed["original_digest"]
        assert due_facts[0]["payload_digest"] == changed["digest"]
        assert due_facts[0]["result_location"] == changed["payload"]
        assert hashlib.sha256(due_facts[0]["result_location"].encode("utf-8")).hexdigest() == (
            due_facts[0]["payload_digest"]
        )
        assert after["task"]["status"] == "failed"
        assert after["receipts"] == after["fees"] == []
        assert after["creditLogs"] == after["applications"] == after["work"] == []
    finally:
        case.engine.dispose()


def test_due_write_rollback_does_not_create_control_or_change_unit_state(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="opt-due-rollback-claim")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert claims.release_claim(claim)
        case.clock[0] = prepared.intent.deadline_at
        fail_once = {"enabled": True}

        def fail_before_commit(session: Session) -> None:
            if session.bind is case.engine and fail_once["enabled"]:
                fail_once["enabled"] = False
                raise RuntimeError("synthetic rollback after due staging")

        event.listen(Session, "before_commit", fail_before_commit)
        try:
            scope = OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize")
            outcome = recovery_adapter(case)._project_due(scope, None)
        finally:
            event.remove(Session, "before_commit", fail_before_commit)

        after = rows_for(case)
        unit = next(row for row in after["units"] if row["id"] == item.unit_id)
        assert outcome == "pending"
        assert unit["result_status"] == "pending"
        assert after["task"]["status"] == "reconciling"
        assert after["external"][0]["submission_status"] == "submitting"
        assert after["evidence"] == []
    finally:
        case.engine.dispose()
