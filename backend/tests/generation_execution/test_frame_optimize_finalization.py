"""Durable parent finalization for frame.optimize batches."""

import json
from datetime import timedelta

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session
import pytest

from haoai_backend.generation_core.base_schema import (
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
)
from haoai_backend.generation_contracts.records import ExecutionAttemptGroup, PolicyStopEvent
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    LocalContinuationWorkRow,
    PolicyStopEventRow,
    UnitFeeDecisionRow,
)
from haoai_backend.series_data.tables import chapters
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    application_adapter,
    apply_current,
    claim_adapter,
    claim_one,
    continuation_claim_adapter,
    fee_adapter,
    finalization_adapter,
    finalize_current,
    intent_adapter,
    locked_batch,
    persist_result,
    prepare_item,
    rows_for,
)
from generation_core.test_admission_staging import ModelSource


def _prepare_apply(case, claim, order, *, intent_port):
    item, prepared = prepare_item(case, claim, order, intents=intent_port)
    receipt = persist_result(case, claim, prepared, text=f"optimized {item.frame_index}")
    assert not isinstance(receipt, str)
    work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
    fee = fee_adapter(case).resolve_current_fee(claim, work.work_id)
    assert fee.disposition in {"charged", "no_charge"}
    applied = apply_current(case, claim, item.unit_id)
    assert applied.kind == "applied"
    return item, applied.application_ref


@pytest.mark.parametrize("frame_count", (3, 30))
def test_finalization_preserves_manifest_and_real_application_order(tmp_path, frame_count):
    request_indexes = (
        (2, 0, 1)
        if frame_count == 3
        else tuple(reversed(range(frame_count)))
    )
    delivery_order = (1, 0, 2) if frame_count == 3 else tuple(range(frame_count))
    case = admit_batch(
        tmp_path,
        frame_count=frame_count,
        request_indexes=request_indexes,
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="finalize-three-intent")
        applied = []
        for order in delivery_order:
            item, application_ref = _prepare_apply(case, claim, order, intent_port=intents)
            applied.append((item.unit_id, item.frame_index, f"optimized {item.frame_index}", application_ref))

        assert json.loads(rows_for(case)["task"]["result"]) == [
            {"index": frame_index, "optimized_text": text}
            for _unit_id, frame_index, text, _ref in applied
        ]
        for index, (unit_id, _frame_index, _text, _ref) in enumerate(applied):
            outcome = finalize_current(case, claim, unit_id)
            expected = "completed" if index == len(applied) - 1 else "work_finalized"
            assert outcome.disposition == expected
            state = rows_for(case)
            assert state["task"]["status"] == ("completed" if expected == "completed" else "processing")
            assert state["task"]["progress"] == (100 if expected == "completed" else 0)

        state = rows_for(case)
        assert len(state["work"]) == frame_count
        assert [row.phase for row in state["work"]] == ["done"] * frame_count
        assert [row.phase_version for row in state["work"]] == [3] * frame_count
        assert state["task"]["execution_generation"] == claim.execution_generation
        assert state["task"]["claim_token"] is None
        assert state["task"]["claimed_by"] is None
        assert state["task"]["lease_until"] is None
        assert state["task"]["credit_cost"] == 3 * frame_count
        assert state["task"]["billing_status"] == "charged"
        assert state["wallet"][0]["credits"] == 500 - (3 * frame_count)
        assert (
            len(state["applications"])
            == len(state["fees"])
            == len(state["creditLogs"])
            == frame_count
        )
    finally:
        case.engine.dispose()


def test_finalize_acknowledgement_loss_confirms_one_done_work_and_task(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, _application_ref = _prepare_apply(
            case, claim, 0, intent_port=intent_adapter(case, prefix="finalize-ack-intent")
        )
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        adapter = finalization_adapter(case)
        lost = {"once": True}

        def lose_commit_ack(session):
            if session.bind is case.engine and lost["once"]:
                lost["once"] = False
                raise RuntimeError("owned finalization commit acknowledgement loss")

        event.listen(Session, "after_commit", lose_commit_ack)
        try:
            outcome = adapter.finalize_current(claim, work.work_id)
        finally:
            event.remove(Session, "after_commit", lose_commit_ack)

        assert lost["once"] is False
        assert outcome.disposition == "completed"
        state = rows_for(case)
        assert state["task"]["status"] == "completed"
        assert state["work"][0].phase == "done"
        assert state["work"][0].phase_version == 3
        assert len(state["applications"]) == len(state["fees"]) == len(state["creditLogs"]) == 1
        assert state["wallet"][0]["credits"] == 497
    finally:
        case.engine.dispose()


def test_final_work_and_parent_projection_rollback_together_before_commit(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, _application_ref = _prepare_apply(
            case, claim, 0, intent_port=intent_adapter(case, prefix="finalize-rollback-intent")
        )
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        state = {"fail_once": True}

        def abort_commit(session):
            if session.bind is case.engine and state["fail_once"]:
                state["fail_once"] = False
                raise RuntimeError("owned finalization precommit rollback")

        event.listen(Session, "before_commit", abort_commit)
        try:
            outcome = finalization_adapter(case).finalize_current(claim, work.work_id)
        finally:
            event.remove(Session, "before_commit", abort_commit)

        assert outcome.disposition == "pending"
        rolled_back = rows_for(case)
        assert rolled_back["task"]["status"] == "processing"
        assert rolled_back["task"]["progress"] == 0
        assert rolled_back["task"]["claim_token"] == claim.token
        assert rolled_back["work"][0].phase == "finalize_pending"
        assert rolled_back["work"][0].phase_version == 2
        assert len(rolled_back["applications"]) == len(rolled_back["fees"]) == 1
        assert len(rolled_back["creditLogs"]) == 1

        completed = finalize_current(case, claim, item.unit_id)
        assert completed.disposition == "completed"
        after = rows_for(case)
        assert after["task"]["status"] == "completed"
        assert after["work"][0].phase == "done"
        assert len(after["applications"]) == len(after["fees"]) == len(after["creditLogs"]) == 1
        assert after["wallet"][0]["credits"] == 497
    finally:
        case.engine.dispose()


def test_independent_finalize_claim_routes_ready_phase_and_closes_parent(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        generation_claims = claim_adapter(case, token="finalize-independent-parent")
        claim = generation_claims.claim_next("batch-optimize", "optimize-test-worker")
        assert claim is not None
        item, _application_ref = _prepare_apply(
            case, claim, 0, intent_port=intent_adapter(case, prefix="finalize-independent-intent")
        )
        assert generation_claims.release_claim(claim) is True
        assert rows_for(case)["task"]["status"] == "queued"

        continuations = continuation_claim_adapter(case, token="finalize-independent-continuation")
        continuation = continuations.claim_continuation("batch-optimize", "finalize-worker")
        assert continuation is not None
        assert continuation.unit_id == item.unit_id
        assert continuation.phase_version == 2
        outcome = finalization_adapter(case, continuation_claims=continuations).consume_finalize(
            continuation
        )
        assert outcome.disposition == "completed"
        state = rows_for(case)
        assert state["task"]["status"] == "completed"
        assert state["task"]["execution_generation"] == continuation.execution_generation
        assert state["work"][0].phase == "done"
        assert state["work"][0].last_execution_generation == continuation.execution_generation
    finally:
        case.engine.dispose()


def test_independent_finalize_claim_ack_loss_is_confirmed_and_consumable(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        generation_claims = claim_adapter(case, token="finalize-ack-parent")
        generation = generation_claims.claim_next("batch-optimize", "generation-worker")
        assert generation is not None
        item, _application_ref = _prepare_apply(
            case,
            generation,
            0,
            intent_port=intent_adapter(case, prefix="finalize-ack-lost-intent"),
        )
        assert generation_claims.release_claim(generation) is True

        continuations = continuation_claim_adapter(case, token="finalize-ack-lost-continuation")
        lost = {"once": True}

        def lose_claim_ack(session):
            if session.bind is case.engine and lost["once"]:
                lost["once"] = False
                raise RuntimeError("owned continuation claim commit acknowledgement loss")

        event.listen(Session, "after_commit", lose_claim_ack)
        try:
            continuation = continuations.claim_continuation(
                "batch-optimize", "finalize-worker"
            )
        finally:
            event.remove(Session, "after_commit", lose_claim_ack)

        assert lost["once"] is False
        assert continuation is not None
        assert continuation.unit_id == item.unit_id
        assert continuation.phase_version == 2
        outcome = finalization_adapter(
            case, continuation_claims=continuations
        ).consume_finalize(continuation)
        assert outcome.disposition == "completed"
        state = rows_for(case)
        assert state["task"]["status"] == "completed"
        assert state["task"]["execution_generation"] == continuation.execution_generation
        assert state["work"][0].phase == "done"
        assert len(state["applications"]) == len(state["fees"]) == len(state["creditLogs"]) == 1
        assert state["wallet"][0]["credits"] == 497
    finally:
        case.engine.dispose()


def test_independent_finalize_n3_can_release_after_each_local_done(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=3,
        request_indexes=(2, 0, 1),
        include_source_tables=True,
    )
    try:
        generation_claims = claim_adapter(case, token="finalize-n3-parent")
        generation = generation_claims.claim_next("batch-optimize", "generation-worker")
        assert generation is not None
        intents = intent_adapter(case, prefix="finalize-n3-intent")
        application_order = (1, 0, 2)
        expected_results = []
        for order in application_order:
            item, _application_ref = _prepare_apply(
                case, generation, order, intent_port=intents
            )
            expected_results.append(
                {"index": item.frame_index, "optimized_text": f"optimized {item.frame_index}"}
            )
        assert generation_claims.release_claim(generation) is True

        finalized_units = set()
        for index in range(3):
            continuations = continuation_claim_adapter(
                case, token=f"finalize-n3-continuation-{index}"
            )
            continuation = continuations.claim_continuation(
                "batch-optimize", "finalize-worker"
            )
            assert continuation is not None
            assert continuation.unit_id not in finalized_units
            outcome = finalization_adapter(
                case, continuation_claims=continuations
            ).consume_finalize(continuation)
            finalized_units.add(continuation.unit_id)

            state = rows_for(case)
            if index < 2:
                assert outcome.disposition == "work_finalized"
                assert state["task"]["status"] == "processing"
                assert sum(row.phase == "done" for row in state["work"]) == index + 1
                assert json.loads(state["task"]["result"]) == expected_results
                case.clock[0] += timedelta(seconds=1)
                renewed = continuations.renew_continuation(continuation)
                assert renewed is not None
                assert renewed.token == continuation.token
                assert renewed.execution_generation == continuation.execution_generation
                assert renewed.lease_until > continuation.lease_until
                assert continuations.release_continuation(renewed) is True
                released = rows_for(case)["task"]
                assert released["status"] == "reconciling"
                assert released["claim_token"] is None
                assert released["claimed_by"] is None
                assert released["lease_until"] is None
            else:
                assert outcome.disposition == "completed"
                assert state["task"]["status"] == "completed"

        final = rows_for(case)
        assert len(finalized_units) == 3
        assert [row.phase for row in final["work"]] == ["done"] * 3
        assert [row.phase_version for row in final["work"]] == [3] * 3
        assert final["task"]["progress"] == 100
        assert final["task"]["claim_token"] is None
        assert final["task"]["claimed_by"] is None
        assert final["task"]["lease_until"] is None
        assert json.loads(final["task"]["result"]) == expected_results
        assert len(final["applications"]) == len(final["fees"]) == len(final["creditLogs"]) == 3
        assert final["wallet"][0]["credits"] == 491
    finally:
        case.engine.dispose()


def test_sibling_unit_cancel_marker_blocks_new_finalize_write(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0, 1), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, _application_ref = _prepare_apply(
            case, claim, 0, intent_port=intent_adapter(case, prefix="finalize-marker-intent")
        )
        sibling = next(
            row for row in rows_for(case)["units"] if row["id"] != item.unit_id
        )
        with case.engine.begin() as connection:
            connection.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.__table__.c.id == sibling["id"])
                .values(user_cancelled_at=case.clock[0].replace(tzinfo=None))
            )

        result = finalize_current(case, claim, item.unit_id)
        assert result.disposition == "ownership_lost"
        rows = rows_for(case)
        assert rows["task"]["status"] == "processing"
        assert next(row for row in rows["work"] if row.unit_id == item.unit_id).phase == "finalize_pending"
        assert all(row.phase != "done" for row in rows["work"])
        assert rows["task"]["progress"] == 0
        assert len(rows["applications"]) == len(rows["fees"]) == len(rows["creditLogs"]) == 1
    finally:
        case.engine.dispose()


def test_no_debit_policy_event_blocks_new_normal_finalization(tmp_path):
    case = admit_batch(
        tmp_path,
        request_indexes=(0,),
        model_source=ModelSource.USER,
        prompt_source=None,
        wallet_credits=None,
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        item, _application_ref = _prepare_apply(
            case,
            claim,
            0,
            intent_port=intent_adapter(case, prefix="policy-event-finalize-intent"),
        )
        before = rows_for(case)
        event = _record_no_debit_policy_event(case, item.unit_id)
        assert event.financial_disposition == "no_debit"
        assert event.amount == 0

        outcome = finalize_current(case, claim, item.unit_id)

        assert outcome.disposition == "pending"
        after = rows_for(case)
        assert after["task"]["status"] == "processing"
        assert after["task"]["claim_token"] == claim.token
        assert after["task"]["result"] == before["task"]["result"]
        assert [row.phase for row in after["work"]] == ["finalize_pending"]
        assert len(after["applications"]) == len(after["fees"]) == 1
        assert after["creditLogs"] == before["creditLogs"] == []
        assert after["wallet"] == before["wallet"] == []
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("wrong_source", (False, True), ids=("bound-event", "unvalidated-source"))
def test_policy_event_blocks_zero_application_control_projection(tmp_path, wrong_source):
    case = admit_batch(
        tmp_path,
        request_indexes=(0, 1),
        model_source=ModelSource.USER,
        prompt_source=None,
        wallet_credits=None,
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="policy-event-control-intent")
        items = []
        for order in (0, 1):
            item, prepared = prepare_item(case, claim, order, intents=intents)
            assert not isinstance(persist_result(case, claim, prepared), str)
            work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
            assert fee_adapter(case, prefix=f"policy-event-control-fee-{order}").resolve_current_fee(
                claim, work.work_id
            ).disposition == "no_charge"
            with case.engine.begin() as connection:
                chapter = connection.execute(
                    select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
                ).scalar_one()
                frames = json.loads(chapter)
                frames[item.frame_index]["text"] = f"human edit {item.frame_index}"
                connection.execute(
                    update(chapters)
                    .where(chapters.c.id == "joint-chapter-1")
                    .values(content=json.dumps(frames, ensure_ascii=False))
                )
            assert apply_current(case, claim, item.unit_id).kind == "target_conflict"
            items.append(item)

        before = rows_for(case)
        assert before["applications"] == before["creditLogs"] == []
        assert all(row.phase == "stopped_delivery_failed" for row in before["work"])
        if wrong_source:
            _record_no_debit_policy_event(case, items[0].unit_id, source_unit_id=items[1].unit_id)
        else:
            _record_no_debit_policy_event(case, items[0].unit_id)

        outcome = finalization_adapter(case).finalize_stopped_task(
            "batch-optimize",
            "optimize-test-worker",
            case.task_id,
            generation_claim=claim,
        )

        assert outcome.disposition == "pending"
        after = rows_for(case)
        assert after["task"]["status"] == "processing"
        assert after["task"]["claim_token"] == claim.token
        assert after["task"]["result"] == before["task"]["result"] is None
        assert [row.phase for row in after["work"]] == ["stopped_delivery_failed"] * 2
        assert after["applications"] == after["creditLogs"] == []
        assert after["wallet"] == []
        assert len(after["receipts"]) == len(after["fees"]) == 2
    finally:
        case.engine.dispose()


def _record_no_debit_policy_event(case, unit_id, *, source_unit_id=None):
    state = rows_for(case)
    source_unit_id = source_unit_id or unit_id
    step = next(row for row in state["steps"] if row["billing_unit_id"] == source_unit_id)
    submission = next(row for row in state["external"] if row["step_id"] == step["id"])
    external_id = submission["external_task_id"] or f"fixture-policy-stop-{source_unit_id}"
    group_key = f"fixture-policy-stop-{unit_id}"
    event_id = f"fixture-policy-event-{unit_id}"
    event_record = PolicyStopEvent(
        event_id=event_id,
        group_key=group_key,
        policy_version="fixture-policy-v1",
        reason="auth_unavailable_12",
        reservation_refs=(),
        observation_refs=(),
        active_step_id=step["id"],
        active_submission_id=submission["id"],
        external_id=external_id,
        stopped_at=case.clock[0],
        financial_disposition="no_debit",
        original_debit_ref=None,
        refund_ref=None,
        amount=0,
        owner_id=case.actor_id,
        task_id=case.task_id,
        unit_id=unit_id,
    )
    with case.sessions() as session, session.begin():
        session.execute(
            update(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.__table__.c.id == submission["id"])
            .values(external_task_id=external_id)
        )
        session.add(
            ExecutionAttemptGroupRow(
                unit_id=unit_id,
                group_key=group_key,
                registry_version="fixture-policy-v1",
                attempt_step_ids=[step["id"]],
                original_payload_digest=submission["request_digest"],
                max_retries=2,
                active_attempt=0,
                status="policy_stopped",
                active_eligibility_version="fixture-policy-v1",
            )
        )
        session.flush()
        result = GenerationRecordRepository(session).stage_record(event_record)
        if source_unit_id == unit_id:
            assert result.status == "created", result.detail
        else:
            assert result.status == "invalid_binding"
            session.add(
                PolicyStopEventRow(
                    event_id=event_id,
                    unit_id=unit_id,
                    group_key=group_key,
                    policy_version="fixture-policy-v1",
                    reason="auth_unavailable_12",
                    reservation_refs=[],
                    observation_refs=[],
                    active_step_id=step["id"],
                    active_submission_id=submission["id"],
                    external_id=external_id,
                    stopped_at=case.clock[0].replace(tzinfo=None),
                    financial_disposition="no_debit",
                    original_debit_ref=None,
                    refund_ref=None,
                    amount=0,
                    owner_id=case.actor_id,
                    task_id=case.task_id,
                )
            )
    return event_record


def test_insufficient_stop_does_not_waive_a_started_sibling_intent(tmp_path):
    case = admit_batch(
        tmp_path,
        request_indexes=(2, 0, 1),
        wallet_credits=3,
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="finalize-insufficient-intent")

        paid_item, paid_intent = prepare_item(case, claim, 0, intents=intents)
        assert not isinstance(persist_result(case, claim, paid_intent), str)
        paid_work = next(row for row in rows_for(case)["work"] if row.unit_id == paid_item.unit_id)
        assert fee_adapter(case).resolve_current_fee(claim, paid_work.work_id).disposition == "charged"

        started_item, started = prepare_item(case, claim, 2, intents=intents)
        assert started.disposition == "committed"
        assert started_item.unit_id != paid_item.unit_id
        started_state = rows_for(case)
        started_external = next(
            row for row in started_state["external"] if row["step_id"] == started.intent.step_id
        )
        assert started_external["submission_status"] == "submitting"
        assert started_external["external_task_id"] is None

        insufficient_item, insufficient = prepare_item(case, claim, 1, intents=intents)
        assert not isinstance(persist_result(case, claim, insufficient), str)
        insufficient_work = next(
            row for row in rows_for(case)["work"] if row.unit_id == insufficient_item.unit_id
        )
        assert fee_adapter(case).resolve_current_fee(
            claim, insufficient_work.work_id
        ).disposition == "stopped_insufficient"

        after_stop = rows_for(case)
        assert len(after_stop["fees"]) == 2
        assert len(after_stop["creditLogs"]) == 1
        assert len(after_stop["receipts"]) == 2
        assert not any(row.unit_id == started_item.unit_id for row in after_stop["work"])
        assert not any(row.unit_id == started_item.unit_id for row in after_stop["fees"])
        assert next(
            row for row in after_stop["external"] if row["step_id"] == started.intent.step_id
        )["external_task_id"] is None

        assert apply_current(case, claim, paid_item.unit_id).kind == "applied"
        outcome = finalize_current(case, claim, paid_item.unit_id)
        assert outcome.disposition == "work_finalized"
        final = rows_for(case)
        assert final["task"]["status"] == "processing"
        assert final["task"]["result"] is not None
        assert final["task"]["claim_token"] == claim.token
        assert next(row for row in final["work"] if row.unit_id == paid_item.unit_id).phase == "done"
        assert next(
            row for row in final["work"] if row.unit_id == insufficient_item.unit_id
        ).phase == "stopped_insufficient"
        assert not any(row.unit_id == started_item.unit_id for row in final["work"])
        assert not any(row.unit_id == started_item.unit_id for row in final["fees"])
        assert final["wallet"][0]["credits"] == 0
        assert len(final["creditLogs"]) == 1
        assert len(final["applications"]) == 1
    finally:
        case.engine.dispose()


def test_insufficient_stop_finalize_claim_ack_loss_is_confirmed(tmp_path):
    case = admit_batch(
        tmp_path,
        request_indexes=(0, 1),
        wallet_credits=3,
        include_source_tables=True,
    )
    try:
        generation_claims = claim_adapter(case, token="insufficient-finalize-ack-parent")
        generation = generation_claims.claim_next("batch-optimize", "generation-worker")
        assert generation is not None
        paid_item, _application_ref = _prepare_apply(
            case,
            generation,
            0,
            intent_port=intent_adapter(case, prefix="insufficient-finalize-paid-intent"),
        )

        stopped_item, stopped_prepared = prepare_item(
            case,
            generation,
            1,
            intents=intent_adapter(case, prefix="insufficient-finalize-stopped-intent"),
        )
        stopped_receipt = persist_result(case, generation, stopped_prepared, text="insufficient")
        assert not isinstance(stopped_receipt, str)
        stopped_work = next(
            row for row in rows_for(case)["work"] if row.unit_id == stopped_item.unit_id
        )
        stopped_fee = fee_adapter(case, prefix="insufficient-finalize-fee").resolve_current_fee(
            generation, stopped_work.work_id
        )
        assert stopped_fee.disposition == "stopped_insufficient"
        assert generation_claims.release_claim(generation) is True
        assert rows_for(case)["task"]["status"] == "queued"

        continuations = continuation_claim_adapter(
            case, token="insufficient-finalize-ack-token"
        )
        lost = {"once": True}

        def lose_claim_ack(session):
            if session.bind is case.engine and lost["once"]:
                lost["once"] = False
                raise RuntimeError("owned stopped-insufficient finalize claim ack loss")

        event.listen(Session, "after_commit", lose_claim_ack)
        try:
            continuation = continuations.claim_continuation(
                "batch-optimize", "finalize-worker"
            )
        finally:
            event.remove(Session, "after_commit", lose_claim_ack)

        assert lost["once"] is False
        assert continuation is not None
        assert continuation.unit_id == paid_item.unit_id
        assert continuation.phase_version == 2
        outcome = finalization_adapter(
            case, continuation_claims=continuations
        ).consume_finalize(continuation)
        assert outcome.disposition == "completed"

        rows = rows_for(case)
        assert rows["task"]["status"] == "completed"
        assert rows["task"]["execution_generation"] == continuation.execution_generation
        work_by_unit = {row.unit_id: row for row in rows["work"]}
        assert work_by_unit[paid_item.unit_id].phase == "done"
        assert work_by_unit[stopped_item.unit_id].phase == "stopped_insufficient"
        assert len(rows["receipts"]) == len(rows["evidence"]) == 2
        assert len(rows["applications"]) == len(rows["creditLogs"]) == 1
        assert len(rows["fees"]) == 2
        assert rows["wallet"][0]["credits"] == 0
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("seed_fake_fee", (False, True), ids=("prepared", "fake-insufficient-fee"))
def test_open_or_unanchored_insufficient_graph_cannot_terminalize(tmp_path, seed_fake_fee):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        if seed_fake_fee:
            item = locked_batch(case).verified.items[0]
            with case.sessions() as session:
                session.add(
                    UnitFeeDecisionRow(
                        unit_id=item.unit_id,
                        charge_phase="pre_apply",
                        resolution="stopped_insufficient",
                        ledger_ref=None,
                        generation_result_ref=None,
                        application_ref=None,
                        resolved_at=case.clock[0].replace(tzinfo=None),
                    )
                )
                session.commit()

        outcome = finalization_adapter(case).finalize_stopped_task(
            "batch-optimize",
            "optimize-test-worker",
            case.task_id,
            generation_claim=claim,
        )
        assert outcome.disposition == "pending"
        state = rows_for(case)
        assert state["task"]["status"] == "processing"
        assert state["task"]["result"] is None
        assert state["task"]["claim_token"] == claim.token
        assert state["work"] == state["receipts"] == state["applications"] == []
        assert state["creditLogs"] == []
        assert state["wallet"][0]["credits"] == 500
        if seed_fake_fee:
            assert len(state["fees"]) == 1
            assert state["fees"][0].resolution == "stopped_insufficient"
        else:
            assert state["fees"] == []
    finally:
        case.engine.dispose()


def test_unresolved_submitted_intent_keeps_zero_application_parent_pending(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0, 1), include_source_tables=True)
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="finalize-unknown-intent")
        failed_item, prepared = prepare_item(case, claim, 0, intents=intents)
        receipt = persist_result(case, claim, prepared, text="conflicting output")
        assert not isinstance(receipt, str)
        failed_work = next(row for row in rows_for(case)["work"] if row.unit_id == failed_item.unit_id)
        assert fee_adapter(case).resolve_current_fee(claim, failed_work.work_id).disposition == "charged"
        with case.engine.begin() as connection:
            chapter = connection.execute(
                select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
            ).scalar_one()
            frames = json.loads(chapter)
            frames[failed_item.frame_index]["text"] = "human edit"
            connection.execute(
                update(chapters)
                .where(chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps(frames, ensure_ascii=False))
            )
        assert apply_current(case, claim, failed_item.unit_id).kind == "target_conflict"

        pending_item, pending_intent = prepare_item(case, claim, 1, intents=intents)
        assert pending_item.unit_id != failed_item.unit_id
        assert pending_intent.disposition == "committed"
        projected = finalization_adapter(case).finalize_stopped_task(
            "batch-optimize",
            "optimize-test-worker",
            case.task_id,
            generation_claim=claim,
        )
        assert projected.disposition == "pending"
        rows = rows_for(case)
        assert rows["task"]["status"] == "processing"
        assert rows["task"]["result"] is None
        assert len(rows["external"]) == 2
        assert len(rows["receipts"]) == 1
        assert len(rows["work"]) == 1
        assert next(row for row in rows["work"] if row.unit_id == failed_item.unit_id).phase == "stopped_delivery_failed"
        assert not any(row.unit_id == pending_item.unit_id for row in rows["work"])
        assert len(rows["creditLogs"]) == 1
    finally:
        case.engine.dispose()


def test_final_task_cas_at_lease_deadline_rolls_back_work_and_task(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, _application_ref = _prepare_apply(
            case, claim, 0, intent_port=intent_adapter(case, prefix="finalize-equality-intent")
        )
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        adapter = finalization_adapter(case)
        samples = []

        def deadline_on_final_sample():
            value = claim.lease_until if len(samples) == 1 else case.clock[0]
            samples.append(value)
            return value

        adapter.db_clock = deadline_on_final_sample
        outcome = adapter.finalize_current(claim, work.work_id)

        assert len(samples) == 2, "the second finalizer clock sample is the terminal Task CAS fence"
        assert samples[-1] == claim.lease_until
        assert outcome.disposition == "ownership_lost"
        state = rows_for(case)
        assert state["task"]["status"] == "processing"
        assert state["task"]["progress"] == 0
        assert state["task"]["claim_token"] == claim.token
        assert state["task"]["lease_until"].replace(tzinfo=claim.lease_until.tzinfo) == claim.lease_until
        assert state["work"][0].phase == "finalize_pending"
        assert state["work"][0].phase_version == 2
        assert len(state["applications"]) == len(state["fees"]) == len(state["creditLogs"]) == 1
        assert state["wallet"][0]["credits"] == 497
    finally:
        case.engine.dispose()


def test_all_late_units_use_zero_application_terminal_projection(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0, 1))
    try:
        claim = claim_adapter(case, token="finalize-late-parent", lease_seconds=20000).claim_next(
            "batch-optimize", "optimize-test-worker"
        )
        assert claim is not None
        intents = intent_adapter(case, prefix="finalize-late-intent")
        for order in (0, 1):
            item, prepared = prepare_item(case, claim, order, intents=intents)
            assert prepared.disposition == "committed"
            case.clock[0] = prepared.intent.deadline_at + timedelta(seconds=1)
            receipt = persist_result(case, claim, prepared, text=f"late {item.frame_index}")
            assert not isinstance(receipt, str)
            assert receipt.late_disposition == "stopped_late_generation"

        before = rows_for(case)
        assert before["applications"] == []
        assert before["creditLogs"] == []
        assert len(before["receipts"]) == len(before["work"]) == len(before["fees"]) == 2
        assert all(row.phase == "stopped_late_generation" for row in before["work"])
        assert before["task"]["status"] == "processing"

        outcome = finalization_adapter(case).finalize_stopped_task(
            "batch-optimize",
            "optimize-test-worker",
            case.task_id,
            generation_claim=claim,
        )
        assert outcome.disposition == "failed"
        after = rows_for(case)
        assert after["task"]["status"] == "failed"
        assert json.loads(after["task"]["result"]) == {
            "partial": [],
            "error": "original_generation_deadline_elapsed",
        }
        assert after["task"]["credit_cost"] == 0
        assert after["task"]["billing_status"] == "unbilled"
        assert after["applications"] == after["creditLogs"] == []
        assert all(row.phase == "stopped_late_generation" for row in after["work"])
    finally:
        case.engine.dispose()


def test_all_target_conflicts_use_control_projection_without_fake_work(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0, 1), include_source_tables=True)
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="finalize-conflict-intent")
        for order in (0, 1):
            item, prepared = prepare_item(case, claim, order, intents=intents)
            receipt = persist_result(case, claim, prepared, text=f"conflict {item.frame_index}")
            assert not isinstance(receipt, str)
            work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
            fee = fee_adapter(case).resolve_current_fee(claim, work.work_id)
            assert fee.disposition == "charged"
            with case.engine.begin() as connection:
                chapter = connection.execute(
                    select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
                ).scalar_one()
                frames = json.loads(chapter)
                frames[item.frame_index]["text"] = f"human edit {item.frame_index}"
                connection.execute(
                    update(chapters)
                    .where(chapters.c.id == "joint-chapter-1")
                    .values(content=json.dumps(frames, ensure_ascii=False))
                )
            outcome = apply_current(case, claim, item.unit_id)
            assert outcome.kind == "target_conflict"

        state = rows_for(case)
        assert all(row.phase == "stopped_delivery_failed" for row in state["work"])
        assert state["applications"] == []
        assert state["task"]["result"] is None
        assert len(state["creditLogs"]) == 2
        assert state["task"]["credit_cost"] == 6

        projected = finalization_adapter(case).finalize_stopped_task(
            "batch-optimize",
            "optimize-test-worker",
            case.task_id,
            generation_claim=claim,
        )
        assert projected.disposition == "failed"
        state = rows_for(case)
        assert state["task"]["status"] == "failed"
        assert state["task"]["progress"] == 100
        assert json.loads(state["task"]["result"]) == {
            "partial": [],
            "error": "frame_optimize_target_conflict",
        }
        assert state["work"] and all(row.phase == "stopped_delivery_failed" for row in state["work"])
        assert len(state["creditLogs"]) == 2
        assert state["wallet"][0]["credits"] == 494
        assert state["task"]["claim_token"] is None
        assert state["task"]["lease_until"] is None
        assert session_scalar(
            case,
            select(LocalContinuationWorkRow.work_id).where(
                LocalContinuationWorkRow.task_id == case.task_id,
                LocalContinuationWorkRow.phase == "done",
            ),
        ) is None
    finally:
        case.engine.dispose()


def session_scalar(case, statement):
    with case.sessions() as session:
        return session.execute(statement).scalar_one_or_none()
