"""Owned SQL tests for complete frame.optimize cancellation."""

import json

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import OwnerTaskScope
from haoai_backend.generation_core.base_schema import (
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
)
from haoai_backend.generation_core.schema import ExecutionAttemptGroupRow, PolicyStopEventRow
from haoai_backend.series_data.tables import chapters
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.cancellation import SqlTaskCancellationWriter
from generation_core.test_admission_staging import ModelSource
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    apply_current,
    claim_adapter,
    cancellation_adapter,
    claim_one,
    fee_adapter,
    finalization_adapter,
    intent_adapter,
    locked_batch,
    persist_result,
    prepare_item,
    rows_for,
)


@pytest.mark.parametrize("billing_status", ["no_charge", "charged"])
@pytest.mark.parametrize("operation", ["prepare", "renew"])
def test_unstarted_positive_sibling_billing_status_blocks_new_work(
    tmp_path, billing_status, operation
):
    case = admit_batch(tmp_path, frame_count=2, request_indexes=(0, 1))
    try:
        claim = claim_one(case)
        graph = locked_batch(case)
        assert graph is not None
        selected, sibling = graph.verified.items
        assert selected.unit_id != sibling.unit_id
        assert graph.units_by_id[sibling.unit_id]["quoted_amount"] > 0

        with case.engine.begin() as connection:
            connection.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.id == sibling.unit_id)
                .values(billing_status=billing_status)
            )

        before = rows_for(case)
        old_lease = before["task"]["lease_until"]
        if operation == "prepare":
            outcome = intent_adapter(case, prefix="cancel-graph-invalid").prepare(
                claim, selected.unit_id
            )
            assert outcome.disposition == "unresolved"
        else:
            assert claim_adapter(case, token=claim.token).renew_or_revoke(claim) == "ownership_lost"

        after = rows_for(case)
        assert after["external"] == before["external"] == []
        assert after["task"]["lease_until"] == old_lease
        assert all(row["first_intent_at"] is None for row in after["units"])
        assert after["creditLogs"] == before["creditLogs"] == []
        assert after["fees"] == before["fees"] == []
    finally:
        case.engine.dispose()


def test_owner_cancel_stops_full_batch_and_preserves_applied_order(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=3,
        request_indexes=(2, 0, 1),
        include_source_tables=True,
    )
    try:
        generation = claim_one(case)
        intents = intent_adapter(case, prefix="cancel-n3-intent")
        fees = fee_adapter(case, prefix="cancel-n3-fee")
        applied_order = []

        for order in (0, 1):
            item, prepared = prepare_item(case, generation, order, intents=intents)
            receipt = persist_result(case, generation, prepared, text=f"kept {item.frame_index}")
            assert not isinstance(receipt, str)
            work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
            assert fees.resolve_current_fee(generation, work.work_id).disposition == "charged"
            assert apply_current(case, generation, item.unit_id).kind == "applied"
            assert finalization_adapter(case).finalize_current(
                generation, work.work_id
            ).disposition == "work_finalized"
            applied_order.append((item.frame_index, f"kept {item.frame_index}"))

        pending_item, pending = prepare_item(case, generation, 2, intents=intents)
        pending_receipt = persist_result(case, generation, pending, text="result before cancel")
        assert not isinstance(pending_receipt, str)
        pending_work = next(
            row for row in rows_for(case)["work"] if row.unit_id == pending_item.unit_id
        )
        assert fees.resolve_current_fee(generation, pending_work.work_id).disposition == "charged"
        with case.sessions() as session:
            source_before = session.execute(
                select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
            ).scalar_one()

        outcome = cancellation_adapter(case).cancel_owned(
            TrustedActor(user_id=case.actor_id), case.task_id
        )

        assert outcome == "settled"
        rows = rows_for(case)
        assert rows["task"]["status"] == "failed"
        assert rows["task"]["progress"] == 100
        assert rows["task"]["claim_token"] is None
        assert rows["task"]["claimed_by"] is None
        assert rows["task"]["lease_until"] is None
        result = json.loads(rows["task"]["result"])
        assert result == {
            "partial": [
                {"index": index, "optimized_text": text} for index, text in applied_order
            ],
            "error": "用户已停止批量优化",
        }
        assert rows["task"]["credit_cost"] == 9
        assert rows["wallet"][0]["credits"] == 491
        assert len(rows["receipts"]) == len(rows["evidence"]) == 3
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 3
        assert len(rows["applications"]) == 2
        work_by_unit = {row.unit_id: row for row in rows["work"]}
        assert work_by_unit[pending_item.unit_id].phase == "stopped_cancelled"
        assert work_by_unit[pending_item.unit_id].phase_version == 2
        assert all(
            row.phase == "done" and row.phase_version == 3
            for unit_id, row in work_by_unit.items()
            if unit_id != pending_item.unit_id
        )
        assert all(row["user_cancelled_at"] is not None for row in rows["units"])
        assert len({row["cancellation_reason"] for row in rows["units"]}) == 1
        assert rows["task"]["cancellation_reason"] == rows["units"][0]["cancellation_reason"]
        with case.sessions() as session:
            source_after = session.execute(
                select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
            ).scalar_one()
        assert source_after == source_before

        assert cancellation_adapter(case, prefix="cancel-replay").cancel_owned(
            TrustedActor(user_id=case.actor_id), case.task_id
        ) == "retained"
        replayed = rows_for(case)
        assert len(replayed["fees"]) == len(replayed["creditLogs"]) == 3
        assert len(replayed["applications"]) == 2
        assert replayed["task"]["result"] == rows["task"]["result"]
    finally:
        case.engine.dispose()


def test_persisted_cancel_stops_unknown_intent_without_inventing_a_result(tmp_path):
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        assert prepared.disposition == "committed"
        assert prepared.intent is not None

        assert SqlTaskCancellationWriter(lambda: case.engine.begin()).request_cancel(
            case.task_id
        )
        outcome = cancellation_adapter(case, prefix="persisted-cancel").consume_persisted(
            OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize")
        )

        assert outcome == "settled"
        rows = rows_for(case)
        unit = next(row for row in rows["units"] if row["id"] == item.unit_id)
        fee = next(row for row in rows["fees"] if row.unit_id == item.unit_id)
        assert rows["task"]["status"] == "failed"
        assert json.loads(rows["task"]["result"]) == {
            "partial": [],
            "error": "用户已停止批量优化",
        }
        assert unit["billing_status"] == "unbilled"
        assert fee.resolution == "waived_cancelled"
        assert fee.generation_result_ref is None
        assert rows["receipts"] == rows["evidence"] == rows["work"] == []
        assert rows["applications"] == rows["creditLogs"] == []
        assert rows["wallet"][0]["credits"] == 500
    finally:
        case.engine.dispose()


def test_byok_cancel_records_true_zero_without_creating_wallet(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=1,
        request_indexes=(0,),
        model_source=ModelSource.USER,
        prompt_source=None,
        wallet_credits=None,
    )
    try:
        outcome = cancellation_adapter(case, prefix="byok-cancel").cancel_owned(
            TrustedActor(user_id=case.actor_id), case.task_id
        )

        rows = rows_for(case)
        assert outcome == "settled"
        assert rows["task"]["status"] == "failed"
        assert rows["task"]["billing_status"] == "no_charge"
        assert rows["task"]["credit_cost"] == 0
        assert rows["wallet"] == []
        assert rows["creditLogs"] == []
        assert len(rows["fees"]) == 1
        assert rows["fees"][0].resolution == "no_charge"
        assert rows["fees"][0].generation_result_ref is None
        assert rows["units"][0]["billing_status"] == "no_charge"
        assert rows["work"] == rows["receipts"] == rows["applications"] == []
    finally:
        case.engine.dispose()


def test_cancel_preserves_existing_insufficient_sibling_stop(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=3,
        request_indexes=(2, 0, 1),
        wallet_credits=3,
    )
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="cancel-insuff-intent")
        first, first_prepared = prepare_item(case, claim, order=0, intents=intents)
        persist_result(case, claim, first_prepared, text="charged before stop")
        first_work = next(row for row in rows_for(case)["work"] if row.unit_id == first.unit_id)
        assert fee_adapter(case, prefix="cancel-insuff-first").resolve_current_fee(
            claim, first_work.work_id
        ).disposition == "charged"

        second, second_prepared = prepare_item(case, claim, order=1, intents=intents)
        persist_result(case, claim, second_prepared, text="insufficient result")
        second_work = next(row for row in rows_for(case)["work"] if row.unit_id == second.unit_id)
        assert fee_adapter(case, prefix="cancel-insuff-second").resolve_current_fee(
            claim, second_work.work_id
        ).disposition == "stopped_insufficient"
        before = rows_for(case)
        preserved_insufficient = {
            row.unit_id: (row.resolution, row.generation_result_ref, row.resolved_at)
            for row in before["fees"]
            if row.resolution == "stopped_insufficient"
        }
        assert len(preserved_insufficient) == 2

        outcome = cancellation_adapter(case, prefix="cancel-after-insuff").cancel_owned(
            TrustedActor(user_id=case.actor_id), case.task_id
        )

        after = rows_for(case)
        assert outcome == "settled"
        assert after["task"]["status"] == "failed"
        assert after["task"]["recovery_status"] == "stopped_insufficient"
        assert after["task"]["billing_status"] == "charged"
        assert after["task"]["credit_cost"] == 3
        assert after["wallet"][0]["credits"] == 0
        assert {
            row.unit_id: (row.resolution, row.generation_result_ref, row.resolved_at)
            for row in after["fees"]
            if row.resolution == "stopped_insufficient"
        } == preserved_insufficient
        assert len(after["creditLogs"]) == 1
        assert next(row for row in after["work"] if row.unit_id == first.unit_id).phase == "stopped_cancelled"
        assert next(row for row in after["work"] if row.unit_id == second.unit_id).phase == "stopped_insufficient"
        assert all(row["user_cancelled_at"] is not None for row in after["units"])
    finally:
        case.engine.dispose()


def test_cancel_unknown_commit_is_freshly_confirmed_once(tmp_path):
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        state = {"lose": True}

        def lose_cancel_ack(session):
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                raise RuntimeError("owned cancellation commit acknowledgement loss")

        event.listen(Session, "after_commit", lose_cancel_ack)
        try:
            outcome = cancellation_adapter(case, prefix="cancel-ack-lost").cancel_owned(
                TrustedActor(user_id=case.actor_id), case.task_id
            )
        finally:
            event.remove(Session, "after_commit", lose_cancel_ack)

        rows = rows_for(case)
        assert state["lose"] is False
        assert outcome == "settled"
        assert rows["task"]["status"] == "failed"
        assert len(rows["fees"]) == 1
        assert rows["fees"][0].resolution == "waived_cancelled"
        assert rows["work"] == rows["receipts"] == rows["creditLogs"] == []
        assert all(row["user_cancelled_at"] is not None for row in rows["units"])
    finally:
        case.engine.dispose()


def test_cancel_precommit_failure_rolls_back_markers_and_financial_plan(tmp_path):
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        state = {"fail": True}

        def fail_cancel_before_commit(session):
            if session.bind is case.engine and state["fail"]:
                state["fail"] = False
                raise RuntimeError("owned cancellation precommit failure")

        event.listen(Session, "before_commit", fail_cancel_before_commit)
        try:
            outcome = cancellation_adapter(case, prefix="cancel-rollback").cancel_owned(
                TrustedActor(user_id=case.actor_id), case.task_id
            )
        finally:
            event.remove(Session, "before_commit", fail_cancel_before_commit)

        rows = rows_for(case)
        assert state["fail"] is False
        assert outcome == "pending"
        assert rows["task"]["status"] == "queued"
        assert rows["task"]["user_cancelled_at"] is None
        assert rows["task"]["cancellation_reason"] is None
        assert rows["fees"] == []
        assert all(row["user_cancelled_at"] is None for row in rows["units"])
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("blocker", ("group", "event"))
def test_cancel_refuses_group_or_policy_event_before_any_new_write(tmp_path, blocker):
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        if blocker == "event":
            _item, prepared = prepare_item(case, claim)
            assert prepared.disposition == "committed"
        unit = rows_for(case)["units"][0]
        with case.sessions() as session:
            step = session.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
                )
            ).mappings().one()
            external = (
                session.execute(
                    select(ExternalSubmissionRow.__table__).where(
                        ExternalSubmissionRow.__table__.c.step_id == step["id"]
                    )
                ).mappings().one_or_none()
            )
        with case.engine.begin() as connection:
            connection.execute(
                ExecutionAttemptGroupRow.__table__.insert().values(
                    unit_id=unit["id"],
                    group_key="unregistered-group",
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
                assert external is not None
                connection.execute(
                    PolicyStopEventRow.__table__.insert().values(
                        event_id="unregistered-event",
                        unit_id=unit["id"],
                        group_key="unregistered-group",
                        policy_version="unregistered",
                        reason="auth_unavailable_12",
                        reservation_refs=[],
                        observation_refs=[],
                        active_step_id=step["id"],
                        active_submission_id=external["id"],
                        external_id=external["external_task_id"] or "owned-test-external",
                        stopped_at=case.clock[0].replace(tzinfo=None),
                        financial_disposition="no_debit",
                        original_debit_ref=None,
                        refund_ref=None,
                        amount=0,
                        owner_id=case.actor_id,
                        task_id=case.task_id,
                    )
                )
        before = rows_for(case)

        outcome = cancellation_adapter(case, prefix="group-cancel").cancel_owned(
            TrustedActor(user_id=case.actor_id), case.task_id
        )

        after = rows_for(case)
        assert outcome == "pending"
        assert after["task"] == before["task"]
        assert after["units"] == before["units"]
        assert after["fees"] == before["fees"] == []
        assert after["work"] == before["work"] == []
        assert after["creditLogs"] == before["creditLogs"] == []
    finally:
        case.engine.dispose()


def test_cancel_after_task_completion_retains_original_terminal_projection(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=1,
        request_indexes=(0,),
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        persist_result(case, claim, prepared, text="completed before cancellation")
        work = rows_for(case)["work"][0]
        assert fee_adapter(case, prefix="completed-cancel-fee").resolve_current_fee(
            claim, work.work_id
        ).disposition == "charged"
        assert apply_current(case, claim, item.unit_id).kind == "applied"
        assert (
            finalization_adapter(case).finalize_current(claim, work.work_id).disposition
            == "completed"
        )
        before = rows_for(case)

        outcome = cancellation_adapter(case, prefix="late-cancel").cancel_owned(
            TrustedActor(user_id=case.actor_id), case.task_id
        )

        after = rows_for(case)
        assert outcome == "retained"
        assert after["task"] == before["task"]
        assert after["units"] == before["units"]
        assert _persisted_state(after["fees"]) == _persisted_state(before["fees"])
        assert _persisted_state(after["applications"]) == _persisted_state(
            before["applications"]
        )
        assert _persisted_state(after["creditLogs"]) == _persisted_state(before["creditLogs"])
        assert after["wallet"] == before["wallet"]
    finally:
        case.engine.dispose()


def _persisted_state(rows):
    """Compare mapped rows by persisted columns, not Session-local identity."""
    return tuple(
        sorted(
            (
                tuple(getattr(row, column.name) for column in row.__table__.columns)
                for row in rows
            ),
            key=repr,
        )
    )


def _assert_cancelled_claim_cannot_start_unit(case, claim, unit_id):
    before = rows_for(case)
    outcome = intent_adapter(case, prefix="cancelled-claim-replay").prepare(claim, unit_id)
    assert outcome.disposition == "ownership_lost"
    after = rows_for(case)
    assert after["task"] == before["task"]
    assert after["external"] == before["external"]
    for key in ("receipts", "fees", "creditLogs", "work", "applications"):
        assert _persisted_state(after[key]) == _persisted_state(before[key])
    assert after["wallet"] == before["wallet"]


def test_fee_ack_is_confirmed_after_cancellation_stops_delivery(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=2,
        request_indexes=(0, 1),
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim, order=0)
        unstarted_unit_id = locked_batch(case).verified.items[1].unit_id
        persist_result(case, claim, prepared, text="fee acknowledged after cancel")
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        fees = fee_adapter(case, prefix="fee-cancel-ack")
        cancellation = cancellation_adapter(case, prefix="fee-cancel-owner")
        state = {"lose": True, "cancel": None, "application": None}

        def cancel_after_fee_commit(session):
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                state["application"] = apply_current(case, claim, item.unit_id)
                state["cancel"] = cancellation.cancel_owned(
                    TrustedActor(user_id=case.actor_id), case.task_id
                )
                raise RuntimeError(
                    "owned fee acknowledgement lost after application and cancellation"
                )

        event.listen(Session, "after_commit", cancel_after_fee_commit)
        try:
            outcome = fees.resolve_current_fee(claim, work.work_id)
        finally:
            event.remove(Session, "after_commit", cancel_after_fee_commit)

        rows = rows_for(case)
        assert state["lose"] is False
        assert state["cancel"] == "settled"
        assert state["application"].kind == "applied"
        _assert_cancelled_claim_cannot_start_unit(case, claim, unstarted_unit_id)
        assert outcome.disposition == "charged", (
            outcome,
            rows["fees"],
            rows["work"],
            rows["applications"],
        )
        assert rows["task"]["status"] == "failed"
        assert rows["work"][0].phase == "stopped_cancelled"
        assert rows["work"][0].phase_version == 3
        assert sorted(row.resolution for row in rows["fees"]) == [
            "charged",
            "waived_cancelled",
        ]
        assert len(rows["creditLogs"]) == 1
        assert rows["wallet"][0]["credits"] == 497
        assert len(rows["applications"]) == 1
        assert rows["fees"][0].application_ref is None
        assert rows["work"][0].application_ref == rows["applications"][0].application_ref
    finally:
        case.engine.dispose()


def test_result_ack_is_confirmed_after_cancellation_preserves_original_receipt(tmp_path):
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        cancellation = cancellation_adapter(case, prefix="result-cancel-owner")
        state = {"lose": True, "cancel": None}

        def cancel_after_result_commit(session):
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                state["cancel"] = cancellation.cancel_owned(
                    TrustedActor(user_id=case.actor_id), case.task_id
                )
                raise RuntimeError("owned result acknowledgement lost after cancellation")

        event.listen(Session, "after_commit", cancel_after_result_commit)
        try:
            receipt = persist_result(
                case, claim, prepared, text="receipt acknowledged after cancel"
            )
        finally:
            event.remove(Session, "after_commit", cancel_after_result_commit)

        rows = rows_for(case)
        assert state["lose"] is False
        assert state["cancel"] == "settled"
        assert not isinstance(receipt, str)
        assert receipt.unit_id == item.unit_id
        assert rows["task"]["status"] == "failed"
        assert rows["receipts"][0].result_ref == receipt.result_ref
        assert rows["fees"][0].resolution == "waived_cancelled"
        assert rows["work"][0].phase == "stopped_cancelled"
        assert rows["work"][0].phase_version == 1
        assert rows["creditLogs"] == rows["applications"] == []
        assert rows["wallet"][0]["credits"] == 500
    finally:
        case.engine.dispose()


def test_application_ack_is_confirmed_after_cancellation_preserves_app(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=2,
        request_indexes=(0, 1),
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim, order=0)
        unstarted_unit_id = locked_batch(case).verified.items[1].unit_id
        persist_result(case, claim, prepared, text="application acknowledged after cancel")
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        assert fee_adapter(case, prefix="application-cancel-fee").resolve_current_fee(
            claim, work.work_id
        ).disposition == "charged"
        cancellation = cancellation_adapter(case, prefix="application-cancel-owner")
        state = {"lose": True, "cancel": None, "finalization": None}

        def cancel_after_application_commit(session):
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                state["finalization"] = finalization_adapter(case).finalize_current(
                    claim, work.work_id
                )
                state["cancel"] = cancellation.cancel_owned(
                    TrustedActor(user_id=case.actor_id), case.task_id
                )
                raise RuntimeError(
                    "owned application acknowledgement lost after finalization and cancellation"
                )

        event.listen(Session, "after_commit", cancel_after_application_commit)
        try:
            applied = apply_current(case, claim, item.unit_id)
        finally:
            event.remove(Session, "after_commit", cancel_after_application_commit)

        rows = rows_for(case)
        assert state["lose"] is False
        assert state["cancel"] == "settled"
        assert state["finalization"].disposition == "work_finalized"
        _assert_cancelled_claim_cannot_start_unit(case, claim, unstarted_unit_id)
        assert applied.kind == "applied"
        assert rows["task"]["status"] == "failed"
        assert rows["work"][0].phase == "done"
        assert rows["work"][0].phase_version == 3
        assert rows["work"][0].application_ref == rows["applications"][0].application_ref
        assert rows["evidence"][0]["application_status"] == "applied"
        assert len(rows["applications"]) == len(rows["creditLogs"]) == 1
        assert len(rows["fees"]) == 2
        assert rows["wallet"][0]["credits"] == 497
    finally:
        case.engine.dispose()


def test_done_ack_is_confirmed_after_cancellation_preserves_task_order(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=3,
        request_indexes=(2, 0, 1),
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim, order=0)
        unstarted_unit_id = locked_batch(case).verified.items[2].unit_id
        persist_result(case, claim, prepared, text="done acknowledged after cancel")
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        assert fee_adapter(case, prefix="done-cancel-fee").resolve_current_fee(
            claim, work.work_id
        ).disposition == "charged"
        assert apply_current(case, claim, item.unit_id).kind == "applied"
        current = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        cancellation = cancellation_adapter(case, prefix="done-cancel-owner")
        finalizer = finalization_adapter(case)
        state = {
            "lose": True,
            "cancel": None,
            "second_application": None,
            "next_item": None,
        }

        def cancel_after_done_commit(session):
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                intents = intent_adapter(case, prefix="done-cancel-next-intent")
                next_item, next_prepared = prepare_item(case, claim, order=1, intents=intents)
                state["next_item"] = next_item
                next_receipt = persist_result(
                    case, claim, next_prepared, text="second applied after first done"
                )
                assert not isinstance(next_receipt, str)
                next_work = next(
                    row for row in rows_for(case)["work"] if row.unit_id == next_item.unit_id
                )
                assert fee_adapter(case, prefix="done-cancel-next-fee").resolve_current_fee(
                    claim, next_work.work_id
                ).disposition == "charged"
                state["second_application"] = apply_current(case, claim, next_item.unit_id)
                state["cancel"] = cancellation.cancel_owned(
                    TrustedActor(user_id=case.actor_id), case.task_id
                )
                raise RuntimeError(
                    "owned done acknowledgement lost after later application and cancellation"
                )

        event.listen(Session, "after_commit", cancel_after_done_commit)
        try:
            finalized = finalizer.finalize_current(claim, current.work_id)
        finally:
            event.remove(Session, "after_commit", cancel_after_done_commit)

        rows = rows_for(case)
        assert state["lose"] is False
        assert state["cancel"] == "settled"
        assert state["second_application"].kind == "applied"
        _assert_cancelled_claim_cannot_start_unit(case, claim, unstarted_unit_id)
        assert finalized.disposition == "work_finalized"
        assert rows["task"]["status"] == "failed"
        assert json.loads(rows["task"]["result"]) == {
            "partial": [
                {"index": item.frame_index, "optimized_text": "done acknowledged after cancel"},
                {
                    "index": state["next_item"].frame_index,
                    "optimized_text": "second applied after first done",
                },
            ],
            "error": "用户已停止批量优化",
        }
        assert len([row for row in rows["work"] if row.phase == "done"]) == 1
        assert all(row.phase in {"done", "stopped_cancelled"} for row in rows["work"])
        assert len(rows["applications"]) == 2
        assert len(rows["creditLogs"]) == 2
        assert rows["wallet"][0]["credits"] == 494
    finally:
        case.engine.dispose()
