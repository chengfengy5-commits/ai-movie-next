"""Lease, renewal, release and fencing checks for optimize tasks."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_core.frame_optimize_claims import SqlAlchemyFrameOptimizeClaims
from haoai_backend.task_observation.cancellation import SqlTaskCancellationWriter
from generation_core.test_admission_staging import _G2_JOINT_NOW
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    assert_only_original_graph,
    claim_adapter,
    claim_one,
    fee_adapter,
    intent_adapter,
    persist_result,
    prepare_item,
    rows_for,
)


def test_claim_owns_one_complete_batch_renews_and_releases_without_intents(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        claims = claim_adapter(case, token="opt-claim-first")
        claim = claims.claim_next("batch-optimize", "optimize-worker-a")
        assert claim is not None
        assert claim.execution_generation == 1
        assert claim.scope.operation == "frame.optimize"
        assert claim.scope.task_id == case.task_id
        assert claim.token == "opt-claim-first"
        state = rows_for(case)
        assert state["task"]["status"] == "processing"
        assert state["task"]["execution_generation"] == 1
        assert state["task"]["claim_token"] == "opt-claim-first"
        assert state["task"]["lease_until"] == (_G2_JOINT_NOW + timedelta(seconds=60)).replace(tzinfo=None)
        assert len(state["units"]) == 3
        assert len(state["external"]) == 0

        competitor = claim_adapter(case, token="opt-claim-second")
        assert competitor.claim_next("batch-optimize", "optimize-worker-b") is None
        assert rows_for(case)["task"]["claim_token"] == claim.token

        case.clock[0] += timedelta(seconds=10)
        assert claims.renew_or_revoke(claim) == "renewed"
        renewed = rows_for(case)["task"]["lease_until"]
        assert renewed == (_G2_JOINT_NOW + timedelta(seconds=70)).replace(tzinfo=None)
        assert claims.release_claim(claim) is True
        state = rows_for(case)
        assert state["task"]["status"] == "queued"
        assert state["task"]["claimed_by"] is None
        assert state["task"]["claim_token"] is None
        assert state["task"]["lease_until"] is None
        assert state["task"]["execution_generation"] == 1
        assert_only_original_graph(case, external_count=0)

        reclaimed = claim_adapter(case, token="opt-claim-third").claim_next(
            "batch-optimize", "optimize-worker-c"
        )
        assert reclaimed is not None
        assert reclaimed.execution_generation == 2
        assert reclaimed.token == "opt-claim-third"
    finally:
        case.engine.dispose()


def test_expired_lease_and_foreign_token_cannot_renew_or_release(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="opt-expiring-claim")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        forged = type(claim)(
            scope=claim.scope,
            worker_instance=claim.worker_instance,
            token="different-token",
            execution_generation=claim.execution_generation,
            lease_until=claim.lease_until,
            local_lost_signal=claim.local_lost_signal,
        )
        assert claims.renew_or_revoke(forged) == "ownership_lost"
        assert claims.release_claim(forged) is False
        assert rows_for(case)["task"]["claim_token"] == claim.token

        case.clock[0] += timedelta(seconds=61)
        assert claims.renew_or_revoke(claim) == "ownership_lost"
        assert claims.release_claim(claim) is False
        state = rows_for(case)
        assert state["task"]["status"] == "processing"
        assert state["task"]["claim_token"] == claim.token
        assert state["task"]["execution_generation"] == 1
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_claim_commit_acknowledgement_loss_confirms_only_the_committed_lease(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        state = {"lose": True}

        claims = SqlAlchemyFrameOptimizeClaims(
            session_factory=case.sessions,
            db_clock=lambda: case.clock[0],
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
            id_factory=lambda: "opt-claim-ack-loss",
        )
        def lose_ack_once(session: Session) -> None:
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                raise RuntimeError("fixture lost claim commit acknowledgement")

        event.listen(Session, "after_commit", lose_ack_once)
        try:
            claim = claims.claim_next("batch-optimize", "optimize-worker")
        finally:
            event.remove(Session, "after_commit", lose_ack_once)
        assert state["lose"] is False
        assert claim is not None
        assert claim.token == "opt-claim-ack-loss"
        assert rows_for(case)["task"]["claim_token"] == claim.token
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_claim_commit_acknowledgement_loss_after_independent_cancel_returns_no_authority(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        cancel = SqlTaskCancellationWriter(case.engine.begin)
        state = {"cancel": True}

        claims = SqlAlchemyFrameOptimizeClaims(
            session_factory=case.sessions,
            db_clock=lambda: case.clock[0],
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
            id_factory=lambda: "opt-claim-cancelled-ack",
        )
        def cancel_after_commit(session: Session) -> None:
            if session.bind is case.engine and state["cancel"]:
                state["cancel"] = False
                assert cancel.request_cancel(case.task_id)
                raise RuntimeError("fixture cancellation followed committed claim")

        event.listen(Session, "after_commit", cancel_after_commit)
        try:
            assert claims.claim_next("batch-optimize", "optimize-worker") is None
        finally:
            event.remove(Session, "after_commit", cancel_after_commit)
        assert state["cancel"] is False
        state_after = rows_for(case)["task"]
        assert state_after["status"] == "cancelling"
        assert state_after["execution_generation"] == 1
        assert state_after["claim_token"] == "opt-claim-cancelled-ack"
        assert state_after["user_cancelled_at"] is None
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_release_and_renew_never_clear_a_cancellation_status(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        claims = claim_adapter(case, token="opt-claim-before-cancel")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        assert SqlTaskCancellationWriter(case.engine.begin).request_cancel(case.task_id)
        before = rows_for(case)["task"]
        assert before["status"] == "cancelling"
        assert claims.renew_or_revoke(claim) == "ownership_lost"
        assert claims.release_claim(claim) is False
        after = rows_for(case)["task"]
        assert after["status"] == "cancelling"
        assert after["claim_token"] == before["claim_token"]
        assert after["execution_generation"] == before["execution_generation"]
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_generation_heartbeat_accepts_its_charged_unit_progress(tmp_path):
    case = admit_batch(tmp_path, frame_count=3, request_indexes=(2, 0))
    try:
        claims = claim_adapter(case, token="opt-claim-before-fee-heartbeat")
        claim = claims.claim_next("batch-optimize", "optimize-worker")
        assert claim is not None
        intents = intent_adapter(case, prefix="heartbeat-after-fee-intent")
        item, prepared = prepare_item(case, claim, order=0, intents=intents)
        receipt = persist_result(case, claim, prepared, text="first frame charged")
        assert not isinstance(receipt, str)
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        charged = fee_adapter(case, prefix="heartbeat-after-fee").resolve_current_fee(
            claim, work.work_id
        )
        assert charged.disposition == "charged"

        before = rows_for(case)["task"]
        case.clock[0] += timedelta(seconds=1)
        assert claims.renew_or_revoke(claim) == "renewed"
        after = rows_for(case)["task"]
        assert not claim.local_lost_signal.is_set()
        assert after["status"] == "processing"
        assert after["claim_token"] == claim.token
        assert after["execution_generation"] == claim.execution_generation
        assert after["lease_until"] == (_G2_JOINT_NOW + timedelta(seconds=61)).replace(tzinfo=None)
        assert after["lease_until"] > before["lease_until"]
        assert after["credit_cost"] == 3
        assert after["billing_status"] == "charged"
        assert len(rows_for(case)["fees"]) == len(rows_for(case)["creditLogs"]) == 1
    finally:
        case.engine.dispose()
