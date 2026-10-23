"""Per-Unit intent persistence, replay and cancellation fencing for optimize."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import (
    ContinuationClaimAuthority,
    OwnerTaskScope,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
)
from haoai_backend.generation_core.frame_optimize_claims import (
    SqlAlchemyFrameOptimizeClaims,
)
from haoai_backend.generation_core.frame_optimize_intents import (
    SqlAlchemyFrameOptimizeIntents,
)
from haoai_backend.generation_core.frame_optimize_execution import (
    valid_intent_projection,
)
from haoai_backend.generation_core.schema import LocalContinuationWorkRow
from haoai_backend.task_observation.cancellation import SqlTaskCancellationWriter
from generation_core.test_admission_staging import _G2_JOINT_NOW, ModelSource
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    assert_only_original_graph,
    claim_adapter,
    claim_one,
    fee_adapter,
    intent_adapter,
    locked_batch,
    persist_result,
    persist_result_and_fee,
    prepare_item,
    rows_for,
)


def _ordered_units(case):
    graph = locked_batch(case)
    assert graph is not None
    return [item.unit_id for item in graph.verified.items]


def test_nonlinear_batch_prepares_each_unit_once_and_keeps_first_deadline(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claim = claim_one(case)
        intents = intent_adapter(case)
        ordered_units = _ordered_units(case)
        outcomes = [intents.prepare(claim, unit_id) for unit_id in (ordered_units[0], ordered_units[1], ordered_units[2])]

        assert [outcome.disposition for outcome in outcomes] == ["committed"] * 3
        assert [outcome.verified_item.frame_index for outcome in outcomes] == [2, 0, 1]
        assert all(outcome.intent is not None for outcome in outcomes)
        assert len({outcome.intent.external_submission_id for outcome in outcomes}) == 3
        assert [outcome.intent.deadline_at for outcome in outcomes] == [
            _G2_JOINT_NOW + timedelta(seconds=7200)
        ] * 3
        assert [outcome.intent.first_intent_at for outcome in outcomes] == [_G2_JOINT_NOW] * 3

        duplicate = intents.prepare(claim, ordered_units[1])
        assert duplicate.disposition == "existing"
        assert duplicate.intent is None
        state = rows_for(case)
        assert state["task"]["status"] == "processing"
        assert state["task"]["execution_generation"] == 1
        assert [row["submission_status"] for row in state["external"]] == ["submitting"] * 3
        assert [row["first_intent_at"] for row in state["units"]] == [
            _G2_JOINT_NOW.replace(tzinfo=None)
        ] * 3
        assert [row["deadline_at"] for row in state["units"]] == [
            (_G2_JOINT_NOW + timedelta(seconds=7200)).replace(tzinfo=None)
        ] * 3
        assert all(row["execution_status"] == "submitting" for row in state["steps"])
        assert_only_original_graph(case, external_count=3)
    finally:
        case.engine.dispose()


def test_prepare_rejects_foreign_unit_and_continuation_authority(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claim = claim_one(case)
        intents = intent_adapter(case)
        assert intents.prepare(claim, "foreign-unit").disposition == "unresolved"
        unit_id = _ordered_units(case)[0]
        continuation = ContinuationClaimAuthority(
            owner_scope=OwnerTaskScope(case.actor_id, case.task_id, "frame.optimize"),
            unit_id=unit_id,
            work_id="not-a-generation-claim",
            result_ref="fixture-result",
            phase_version=1,
            worker_instance=claim.worker_instance,
            token=claim.token,
            execution_generation=claim.execution_generation,
            lease_until=claim.lease_until,
        )
        assert intents.prepare(continuation, unit_id).disposition == "ownership_lost"
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_cancel_after_first_intent_blocks_the_next_without_rewriting_cancel_gate(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claim = claim_one(case)
        intents = intent_adapter(case)
        ordered_units = _ordered_units(case)
        first = intents.prepare(claim, ordered_units[0])
        assert first.disposition == "committed"
        assert SqlTaskCancellationWriter(case.engine.begin).request_cancel(case.task_id)

        second = intents.prepare(claim, ordered_units[1])
        assert second.disposition == "ownership_lost"
        assert second.intent is None
        replay = intents.prepare(claim, ordered_units[0])
        assert replay.disposition == "existing"
        assert replay.intent is None
        assert claim_adapter(case, token="not-used-after-cancel").release_claim(claim) is False
        state = rows_for(case)
        assert state["task"]["status"] == "cancelling"
        assert state["task"]["execution_generation"] == claim.execution_generation
        assert len(state["external"]) == 1
        assert state["external"][0]["id"] == first.intent.external_submission_id
        assert state["units"].count(next(unit for unit in state["units"] if unit["first_intent_at"] is not None)) == 1
        assert_only_original_graph(case, external_count=1)
    finally:
        case.engine.dispose()


def test_queued_cancel_has_no_claim_or_intent_effects(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        assert SqlTaskCancellationWriter(case.engine.begin).request_cancel(case.task_id)
        assert claim_adapter(case, token="cancelled-task-token").claim_next(
            "batch-optimize", "optimize-worker"
        ) is None
        state = rows_for(case)
        assert state["task"]["status"] == "cancelling"
        assert state["task"]["execution_generation"] == 0
        assert state["task"]["claim_token"] is None
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_intent_commit_acknowledgement_loss_confirms_original_row(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claim = claim_one(case)
        unit_id = _ordered_units(case)[0]
        state = {"lose": True}

        intents = SqlAlchemyFrameOptimizeIntents(
            session_factory=case.sessions,
            db_clock=lambda: case.clock[0],
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
            id_factory=lambda: "int-ack-lost-0000000000000001",
        )
        def lose_ack_once(session: Session) -> None:
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                raise RuntimeError("fixture lost intent commit acknowledgement")

        event.listen(Session, "after_commit", lose_ack_once)
        try:
            prepared = intents.prepare(claim, unit_id)
        finally:
            event.remove(Session, "after_commit", lose_ack_once)
        assert state["lose"] is False
        assert prepared.disposition == "committed"
        assert prepared.intent is not None
        assert prepared.intent.external_submission_id == "int-ack-lost-0000000000000001"
        assert rows_for(case)["external"][0]["id"] == prepared.intent.external_submission_id
        assert_only_original_graph(case, external_count=1)
    finally:
        case.engine.dispose()


def test_true_rollback_after_staging_does_not_confirm_or_recreate_intent(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        unit_id = _ordered_units(case)[0]
        state = {"rollback": True}

        ids = []
        intents = SqlAlchemyFrameOptimizeIntents(
            session_factory=case.sessions,
            db_clock=lambda: case.clock[0],
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
            id_factory=lambda: ids.append("generated-intent") or "int-rollback-000000000000000001",
        )
        def fail_before_commit(session: Session) -> None:
            if session.bind is case.engine and state["rollback"]:
                state["rollback"] = False
                raise RuntimeError("fixture rolled back before intent commit")

        event.listen(Session, "before_commit", fail_before_commit)
        try:
            outcome = intents.prepare(claim, unit_id)
        finally:
            event.remove(Session, "before_commit", fail_before_commit)
        assert state["rollback"] is False
        assert outcome.disposition == "unresolved"
        assert outcome.intent is None
        assert ids == ["generated-intent"]
        state_after = rows_for(case)
        assert state_after["external"] == []
        assert all(unit["first_intent_at"] is None and unit["deadline_at"] is None for unit in state_after["units"])
        assert all(step["execution_status"] == "prepared" for step in state_after["steps"])
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_intent_acknowledgement_after_cancel_confirms_no_send_authority(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0, 1))
    try:
        claim = claim_one(case)
        unit_id = _ordered_units(case)[0]
        cancel = SqlTaskCancellationWriter(case.engine.begin)
        state = {"cancel": True}

        intents = SqlAlchemyFrameOptimizeIntents(
            session_factory=case.sessions,
            db_clock=lambda: case.clock[0],
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
            id_factory=lambda: "int-cancelled-00000000000000001",
        )
        def cancel_after_commit(session: Session) -> None:
            if session.bind is case.engine and state["cancel"]:
                state["cancel"] = False
                assert cancel.request_cancel(case.task_id)
                raise RuntimeError("fixture cancellation followed intent commit")

        event.listen(Session, "after_commit", cancel_after_commit)
        try:
            outcome = intents.prepare(claim, unit_id)
        finally:
            event.remove(Session, "after_commit", cancel_after_commit)
        assert state["cancel"] is False
        assert outcome.disposition == "existing"
        assert outcome.intent is None
        state_after = rows_for(case)
        assert state_after["task"]["status"] == "cancelling"
        assert len(state_after["external"]) == 1
        assert state_after["external"][0]["id"] == "int-cancelled-00000000000000001"
        assert_only_original_graph(case, external_count=1)
    finally:
        case.engine.dispose()


def test_ack_loss_after_unit_progress_confirms_existing_without_send_authority(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        unit_id = _ordered_units(case)[0]
        observed_at = (_G2_JOINT_NOW + timedelta(seconds=1)).replace(tzinfo=None)
        state = {"mutated": False}
        intents = SqlAlchemyFrameOptimizeIntents(
            session_factory=case.sessions,
            db_clock=lambda: case.clock[0],
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
            id_factory=lambda: "int-progressed-before-ack-000001",
        )

        def progress_and_lose_ack(session: Session) -> None:
            if session.bind is not case.engine or state["mutated"]:
                return
            state["mutated"] = True
            with case.engine.begin() as connection:
                connection.execute(
                    update(ExecutionStepRow.__table__)
                    .where(ExecutionStepRow.__table__.c.billing_unit_id == unit_id)
                    .values(execution_status="submitted", updated_at=observed_at)
                )
                connection.execute(
                    update(ExternalSubmissionRow.__table__)
                    .where(
                        ExternalSubmissionRow.__table__.c.id
                        == "int-progressed-before-ack-000001"
                    )
                    .values(
                        submission_status="unknown",
                        submitted_at=observed_at,
                        updated_at=observed_at,
                    )
                )
            raise RuntimeError("fixture lost acknowledgement after external progress")

        event.listen(Session, "after_commit", progress_and_lose_ack)
        try:
            outcome = intents.prepare(claim, unit_id)
        finally:
            event.remove(Session, "after_commit", progress_and_lose_ack)

        assert state["mutated"] is True
        assert outcome.disposition == "existing"
        assert outcome.intent is None
        persisted = rows_for(case)
        assert persisted["task"]["status"] == "processing"
        assert persisted["task"]["lease_until"] > case.clock[0].replace(tzinfo=None)
        assert len(persisted["external"]) == 1
        assert persisted["external"][0]["id"] == "int-progressed-before-ack-000001"
        assert persisted["external"][0]["submission_status"] == "unknown"
        assert persisted["external"][0]["submitted_at"] == observed_at
        assert persisted["steps"][0]["execution_status"] == "submitted"
        assert_only_original_graph(case, external_count=1)
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("deadline_elapsed_seconds", [7200, 7201])
def test_ack_loss_after_original_deadline_returns_existing_without_send_authority(
    tmp_path, deadline_elapsed_seconds
):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_adapter(
            case,
            token="long-frame-optimize-claim",
            lease_seconds=10_000,
        ).claim_next("batch-optimize", "optimize-test-worker")
        assert claim is not None
        unit_id = _ordered_units(case)[0]
        state = {"expired": False}
        intents = SqlAlchemyFrameOptimizeIntents(
            session_factory=case.sessions,
            db_clock=lambda: case.clock[0],
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
            id_factory=lambda: "int-deadline-before-ack-00001",
        )

        def expire_original_deadline_and_lose_ack(session: Session) -> None:
            if session.bind is not case.engine or state["expired"]:
                return
            state["expired"] = True
            case.clock[0] = _G2_JOINT_NOW + timedelta(
                seconds=deadline_elapsed_seconds
            )
            raise RuntimeError("fixture deadline elapsed before acknowledgement")

        event.listen(Session, "after_commit", expire_original_deadline_and_lose_ack)
        try:
            outcome = intents.prepare(claim, unit_id)
        finally:
            event.remove(Session, "after_commit", expire_original_deadline_and_lose_ack)

        assert state["expired"] is True
        assert outcome.disposition == "existing"
        assert outcome.intent is None
        persisted = rows_for(case)
        assert persisted["task"]["status"] == "processing"
        assert persisted["task"]["lease_until"] > case.clock[0].replace(tzinfo=None)
        assert len(persisted["external"]) == 1
        assert persisted["external"][0]["id"] == "int-deadline-before-ack-00001"
        assert persisted["external"][0]["deadline_at"] == (
            _G2_JOINT_NOW + timedelta(seconds=7200)
        ).replace(tzinfo=None)
        assert_only_original_graph(case, external_count=1)
    finally:
        case.engine.dispose()


def test_local_submission_key_is_not_claimed_as_provider_idempotency(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        unit_id = _ordered_units(case)[0]
        intents = intent_adapter(case, prefix="local-key")
        prepared = intents.prepare(claim, unit_id)
        assert prepared.disposition == "committed"
        assert prepared.intent is not None

        state = rows_for(case)
        assert len(state["external"]) == 1
        external = state["external"][0]
        expected_local_key = f"frame-optimize-generate-v1:{unit_id}:{prepared.intent.step_id}"
        assert external["submission_key"] == expected_local_key
        assert prepared.intent.provider_idempotency_key is None

        duplicate = intents.prepare(claim, unit_id)
        assert duplicate.disposition == "existing"
        assert duplicate.intent is None
        after = rows_for(case)
        assert len(after["external"]) == 1
        assert after["external"][0]["submission_key"] == expected_local_key
        assert_only_original_graph(case, external_count=1)
    finally:
        case.engine.dispose()


def test_expired_lease_before_intent_commit_creates_no_submission(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        unit_id = _ordered_units(case)[0]

        def expire_claim_while_preparing_id() -> str:
            case.clock[0] += timedelta(seconds=61)
            return "int-expired-before-commit-00000001"

        intents = SqlAlchemyFrameOptimizeIntents(
            session_factory=case.sessions,
            db_clock=lambda: case.clock[0],
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
            id_factory=expire_claim_while_preparing_id,
        )
        outcome = intents.prepare(claim, unit_id)
        assert outcome.disposition == "ownership_lost"
        assert outcome.intent is None
        state = rows_for(case)
        assert state["external"] == []
        assert state["task"]["claim_token"] == claim.token
        assert state["task"]["status"] == "processing"
        assert all(unit["first_intent_at"] is None for unit in state["units"])
        assert all(step["execution_status"] == "prepared" for step in state["steps"])
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_projection_rejects_a_deadline_extended_in_both_mutable_rows(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claim = claim_one(case)
        unit_id = _ordered_units(case)[0]
        prepared = intent_adapter(case).prepare(claim, unit_id)
        assert prepared.disposition == "committed"
        assert prepared.intent is not None
        extended_deadline = prepared.intent.deadline_at + timedelta(seconds=1)
        with case.engine.begin() as connection:
            connection.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.__table__.c.id == unit_id)
                .values(deadline_at=extended_deadline.replace(tzinfo=None))
            )
            connection.execute(
                update(ExternalSubmissionRow.__table__)
                .where(
                    ExternalSubmissionRow.__table__.c.id
                    == prepared.intent.external_submission_id
                )
                .values(deadline_at=extended_deadline.replace(tzinfo=None))
            )

        graph = locked_batch(case)
        assert graph is not None
        assert not valid_intent_projection(graph)
        assert intent_adapter(case).prepare(claim, unit_id).disposition == "unresolved"
        state = rows_for(case)
        assert len(state["external"]) == 1
        assert state["external"][0]["deadline_at"] == extended_deadline.replace(tzinfo=None)
        assert state["units"][0]["deadline_at"] == extended_deadline.replace(tzinfo=None)
        assert_only_original_graph(case, external_count=1)
    finally:
        case.engine.dispose()


def test_release_after_intent_reclaims_only_unstarted_siblings(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claims = claim_adapter(case, token="opt-claim-release-after-intent")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        ordered_units = _ordered_units(case)
        already_submitted_unit_id = ordered_units[1]
        first_intent = intent_adapter(case).prepare(claim, already_submitted_unit_id)
        assert first_intent.disposition == "committed"
        assert first_intent.intent is not None
        before_release = rows_for(case)
        assert claims.release_claim(claim) is True
        released = rows_for(case)
        assert released["task"]["status"] == "reconciling"
        assert released["task"]["claim_token"] is None
        assert released["task"]["execution_generation"] == claim.execution_generation
        assert len(released["external"]) == 1
        original_unit = next(
            unit for unit in released["units"] if unit["id"] == already_submitted_unit_id
        )
        original_intent_facts = {
            "first_intent_at": original_unit["first_intent_at"],
            "deadline_at": original_unit["deadline_at"],
        }
        original_submission = dict(released["external"][0])
        original_unit_inputs = {
            unit["id"]: {
                "input_digest": unit["input_digest"],
                "candidate_digest": unit["candidate_digest"],
            }
            for unit in released["units"]
        }
        original_billing_facts = {
            unit["id"]: {
                name: unit[name]
                for name in (
                    "quoted_amount",
                    "debited_amount",
                    "refunded_amount",
                    "billing_status",
                    "result_status",
                )
            }
            for unit in released["units"]
        }
        original_wallet = [dict(row) for row in released["wallet"]]
        assert original_unit_inputs[already_submitted_unit_id]["input_digest"] == next(
            unit["input_digest"]
            for unit in before_release["units"]
            if unit["id"] == already_submitted_unit_id
        )

        case.clock[0] += timedelta(seconds=120)
        next_claim = claim_adapter(case, token="opt-reclaim-unstarted-sibling").claim_next(
            "batch-optimize", "another-worker"
        )
        assert next_claim is not None
        assert next_claim.execution_generation == claim.execution_generation + 1
        assert next_claim.token != claim.token

        intents = intent_adapter(case, prefix="opt-reclaimed-intent")
        duplicate = intents.prepare(next_claim, already_submitted_unit_id)
        assert duplicate.disposition == "existing"
        assert duplicate.intent is None
        after_duplicate = rows_for(case)
        assert after_duplicate["task"]["status"] == "processing"
        assert after_duplicate["task"]["claim_token"] == next_claim.token
        assert after_duplicate["task"]["execution_generation"] == next_claim.execution_generation
        assert len(after_duplicate["external"]) == 1
        assert dict(after_duplicate["external"][0]) == original_submission

        unstarted_unit_id = ordered_units[0]
        unstarted = next(
            unit for unit in after_duplicate["units"] if unit["id"] == unstarted_unit_id
        )
        assert unstarted["first_intent_at"] is None
        assert unstarted["deadline_at"] is None
        prepared_sibling = intents.prepare(next_claim, unstarted_unit_id)
        assert prepared_sibling.disposition == "committed"
        assert prepared_sibling.intent is not None

        after = rows_for(case)
        assert len(after["external"]) == 2
        assert next(
            row for row in after["external"] if row["id"] == original_submission["id"]
        ) == original_submission
        new_submission = next(
            row
            for row in after["external"]
            if row["id"] == prepared_sibling.intent.external_submission_id
        )
        assert new_submission["step_id"] == prepared_sibling.intent.step_id
        assert new_submission["step_id"] != original_submission["step_id"]
        assert next(
            unit for unit in after["units"] if unit["id"] == already_submitted_unit_id
        )["first_intent_at"] == original_intent_facts["first_intent_at"]
        assert next(
            unit for unit in after["units"] if unit["id"] == already_submitted_unit_id
        )["deadline_at"] == original_intent_facts["deadline_at"]
        assert {
            unit["id"]: {
                "input_digest": unit["input_digest"],
                "candidate_digest": unit["candidate_digest"],
            }
            for unit in after["units"]
        } == original_unit_inputs
        assert {
            unit["id"]: {
                name: unit[name]
                for name in (
                    "quoted_amount",
                    "debited_amount",
                    "refunded_amount",
                    "billing_status",
                    "result_status",
                )
            }
            for unit in after["units"]
        } == original_billing_facts
        assert [dict(row) for row in after["wallet"]] == original_wallet
        assert after["creditLogs"] == []
        assert after["fees"] == []
        assert_only_original_graph(case, external_count=2)
    finally:
        case.engine.dispose()


def test_release_after_single_unit_intent_does_not_reclaim_fully_started_batch(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="opt-single-item-claim")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        unit_id = _ordered_units(case)[0]
        prepared = intent_adapter(case).prepare(claim, unit_id)
        assert prepared.disposition == "committed"
        assert prepared.intent is not None
        before_release = rows_for(case)
        assert claims.release_claim(claim) is True
        released = rows_for(case)
        assert released["task"]["status"] == "reconciling"
        assert released["task"]["claim_token"] is None
        assert len(released["external"]) == 1
        original_submission = dict(released["external"][0])
        original_unit = next(unit for unit in released["units"] if unit["id"] == unit_id)
        original_facts = {
            "input_digest": original_unit["input_digest"],
            "first_intent_at": original_unit["first_intent_at"],
            "deadline_at": original_unit["deadline_at"],
            "quoted_amount": original_unit["quoted_amount"],
            "debited_amount": original_unit["debited_amount"],
            "refunded_amount": original_unit["refunded_amount"],
            "billing_status": original_unit["billing_status"],
            "result_status": original_unit["result_status"],
        }
        assert original_facts["input_digest"] == before_release["units"][0]["input_digest"]

        case.clock[0] += timedelta(seconds=120)
        assert claim_adapter(case, token="opt-no-reclaim-fully-started").claim_next(
            "batch-optimize", "another-worker"
        ) is None

        after = rows_for(case)
        assert after["task"]["status"] == "reconciling"
        assert after["task"]["claim_token"] is None
        assert after["task"]["execution_generation"] == claim.execution_generation
        assert len(after["external"]) == 1
        assert dict(after["external"][0]) == original_submission
        after_unit = next(unit for unit in after["units"] if unit["id"] == unit_id)
        assert {name: after_unit[name] for name in original_facts} == original_facts
        assert_only_original_graph(case, external_count=1)
    finally:
        case.engine.dispose()


def test_byok_zero_quote_without_prompt_can_create_intent_without_wallet(tmp_path):
    case = admit_batch(
        tmp_path,
        request_indexes=(0,),
        model_source=ModelSource.USER,
        prompt_source=None,
        wallet_credits=None,
    )
    try:
        claim = claim_one(case)
        unit_id = _ordered_units(case)[0]
        prepared = intent_adapter(case).prepare(claim, unit_id)
        assert prepared.disposition == "committed"
        assert prepared.intent is not None
        assert_only_original_graph(case, external_count=1)
        state = rows_for(case)
        assert state["wallet"] == []
        assert state["units"][0]["quoted_amount"] == 0
        assert state["units"][0]["debited_amount"] == 0
    finally:
        case.engine.dispose()


def test_charged_result_does_not_block_the_next_original_sibling_intent(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claim = claim_one(case)
        intents = intent_adapter(case, prefix="post-fee-sibling-intent")
        first, first_intent = prepare_item(case, claim, order=0, intents=intents)
        assert first.frame_index == 2
        receipt = persist_result(case, claim, first_intent, text="first result")
        assert not isinstance(receipt, str)
        work = next(row for row in rows_for(case)["work"] if row.unit_id == first.unit_id)
        fee = fee_adapter(case, prefix="post-fee-sibling").resolve_current_fee(
            claim, work.work_id
        )
        assert fee.disposition == "charged"

        second, second_intent = prepare_item(case, claim, order=1, intents=intents)
        assert second.frame_index == 0
        assert second.unit_id != first.unit_id
        assert second_intent.disposition == "committed"
        assert second_intent.intent is not None
        assert second_intent.intent.provider_idempotency_key is None

        rows = rows_for(case)
        first_unit = next(row for row in rows["units"] if row["id"] == first.unit_id)
        second_unit = next(row for row in rows["units"] if row["id"] == second.unit_id)
        assert rows["task"]["status"] == "processing"
        assert rows["task"]["claim_token"] == claim.token
        assert rows["task"]["execution_generation"] == claim.execution_generation
        assert rows["task"]["credit_cost"] == 3
        assert rows["task"]["billing_status"] == "charged"
        assert first_unit["billing_status"] == "charged"
        assert second_unit["billing_status"] == "unbilled"
        assert len(rows["external"]) == 2
        assert {row["submission_status"] for row in rows["external"]} == {"completed", "submitting"}
        assert len(rows["receipts"]) == len(rows["fees"]) == len(rows["creditLogs"]) == 1
        assert len(rows["work"]) == 1
        assert rows["work"][0].phase == "delivery_pending"
        assert rows["applications"] == []
        assert rows["wallet"][0]["credits"] == 497
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("broken_link", ["missing", "sibling"])
def test_progress_rejects_delivery_work_without_its_own_fee_reference(tmp_path, broken_link):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claim = claim_one(case, token=f"fee-link-{broken_link}")
        intents = intent_adapter(case, prefix=f"fee-link-intent-{broken_link}")
        fees = fee_adapter(case, prefix=f"fee-link-fee-{broken_link}")
        unit_ids = _ordered_units(case)
        charged_count = 1 if broken_link == "missing" else 2

        for order in range(charged_count):
            item, prepared = prepare_item(case, claim, order, intents=intents)
            receipt, fee = persist_result_and_fee(
                case, claim, prepared, fees, text=f"charged unit {order}"
            )
            assert receipt.unit_id == unit_ids[order]
            assert fee.disposition == "charged"

        invalid_reference = None if broken_link == "missing" else unit_ids[1]
        with case.engine.begin() as connection:
            changed = connection.execute(
                update(LocalContinuationWorkRow.__table__)
                .where(
                    LocalContinuationWorkRow.__table__.c.task_id == case.task_id,
                    LocalContinuationWorkRow.__table__.c.unit_id == unit_ids[0],
                )
                .values(fee_decision_ref=invalid_reference)
            )
        assert changed.rowcount == 1

        before = rows_for(case)
        before_fee_facts = sorted(
            (fee.unit_id, fee.resolution, fee.ledger_ref, fee.generation_result_ref)
            for fee in before["fees"]
        )
        before_ledger_facts = sorted(
            (log.id, log.user_id, log.billing_unit_id, log.amount, log.business_key)
            for log in before["creditLogs"]
        )
        outcome = intents.prepare(claim, unit_ids[2])
        assert outcome.disposition == "unresolved"
        assert outcome.intent is None

        after = rows_for(case)
        assert len(after["external"]) == charged_count
        assert len(after["receipts"]) == len(after["evidence"]) == len(after["work"]) == charged_count
        assert len(after["fees"]) == len(after["creditLogs"]) == charged_count
        assert after["wallet"][0]["credits"] == 500 - 3 * charged_count
        assert after["applications"] == []
        assert after["task"]["credit_cost"] == 3 * charged_count
        assert after["external"] == before["external"]
        assert sorted(
            (fee.unit_id, fee.resolution, fee.ledger_ref, fee.generation_result_ref)
            for fee in after["fees"]
        ) == before_fee_facts
        assert sorted(
            (log.id, log.user_id, log.billing_unit_id, log.amount, log.business_key)
            for log in after["creditLogs"]
        ) == before_ledger_facts
    finally:
        case.engine.dispose()
