from __future__ import annotations

import asyncio
import json
from datetime import timedelta, timezone
from itertools import count

import httpx
import test_haoai_group_polls as group_fixtures
import test_video_single_claims as video_fixtures
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.orm import Session

import haoai_backend.generation_core.haoai_group.cancellation as cancellation_module
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.haoai_group.cancellation import (
    SqlAlchemyHaoaiGroupCancellation,
)
from haoai_backend.generation_core.haoai_group.claims import (
    SqlAlchemyHaoaiGroupClaims,
)
from haoai_backend.generation_core.haoai_group.settlement import (
    SqlAlchemyHaoaiGroupEventSettler,
)
from haoai_backend.generation_core.haoai_group.intents import (
    SqlAlchemyHaoaiGroupIntents,
)
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    PollReservationRow,
    PolicyStopEventRow,
)
from haoai_backend.generation_contracts.types import ExternalOperationRef
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.http import build_task_observation_router
from haoai_backend.task_observation.persistence import (
    SqlAlchemyTaskObservationUnitOfWork,
)


def _cancellation(environment, clock, *, refund_id="haoai-group-cancel-refund-001"):
    ids = count()
    return SqlAlchemyHaoaiGroupCancellation(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        proof_authority=environment.capabilities.proof_authority,
        id_factory=lambda: f"{refund_id}-{next(ids)}",
    )


def _accepted_auth12(environment, task_id, clock):
    claim, intents, polls, resumed, _ = group_fixtures._setup_group(
        environment, task_id, clock
    )
    external_ref = ExternalOperationRef(
        intent_id=resumed.intent.external_submission_id,
        attempt_id=resumed.intent.attempt_id,
        provider="haoai",
        external_id="haoai-known-provider-id-001",
        create_outcome="submitted",
    )
    for index in range(12):
        if index:
            clock[0] += timedelta(seconds=5)
            resumed = intents.prepare(claim)
            assert resumed.disposition == "resume_query"
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        provider, _ = group_fixtures._provider(
            claim,
            resumed,
            status_code=403,
            body=b"no auth available",
        )
        provider._utc_clock = lambda: clock[0]
        outcome = polls.query_and_accept(grant, provider)
    assert outcome.disposition == "policy_stopped"
    return resumed


@pytest.mark.parametrize(
    "corrupt_row",
    [
        lambda row: {"request_digest": "f" * 64},
        lambda row: {"submission_key": "not-the-group-submission-key"},
        lambda row: {"request_snapshot": None},
        lambda row: {},
    ],
    ids=("wrong-digest", "wrong-key", "missing-snapshot", "non-active-attempt"),
)
def test_cancel_does_not_retain_fee_from_unproven_submission_row(
    tmp_path, monkeypatch, corrupt_row
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-cancel-unproven-row-001"
        )
        clock = [video_fixtures._NOW]
        _, _, _, resumed, _ = group_fixtures._setup_group(
            environment, task_id, clock
        )
        row_id = resumed.intent.external_submission_id
        changes = corrupt_row({})
        with environment.engine.begin() as connection:
            if not changes:
                active_step_id = resumed.intent.step_id
                other_step_id = connection.execute(
                    select(ExecutionStepRow.__table__.c.id).where(
                        ExecutionStepRow.__table__.c.billing_unit_id
                        == resumed.verified_input.unit_id,
                        ExecutionStepRow.__table__.c.id != active_step_id,
                    )
                    .limit(1)
                ).scalar_one()
                changes = {"step_id": other_step_id}
            connection.execute(
                update(ExternalSubmissionRow.__table__)
                .where(ExternalSubmissionRow.__table__.c.id == row_id)
                .values(**changes)
            )
            task = connection.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()

        cancellation = _cancellation(environment, clock)
        actor = TrustedActor(user_id=task["user_id"])
        assert cancellation.cancel_owned(actor, task_id) == "pending"

        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
        assert task_after["user_cancelled_at"] is None
        assert unit_after["user_cancelled_at"] is None
        assert unit_after["billing_status"] == "charged"
    finally:
        environment.engine.dispose()


def test_cancel_rereads_database_clock_after_graph_locks(tmp_path, monkeypatch):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-cancel-lock-deadline-001"
        )
        clock = [video_fixtures._NOW]
        group_fixtures._setup_group(environment, task_id, clock)
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
        deadline = unit["deadline_at"].replace(tzinfo=timezone.utc)
        original_lock = cancellation_module.lock_haoai_group_graph

        def lock_then_cross_deadline(session, owner_id, locked_task_id):
            graph = original_lock(session, owner_id, locked_task_id)
            clock[0] = deadline
            return graph

        monkeypatch.setattr(
            cancellation_module,
            "lock_haoai_group_graph",
            lock_then_cross_deadline,
        )
        cancellation = _cancellation(environment, clock)
        assert cancellation.cancel_owned(
            TrustedActor(user_id=task["user_id"]), task_id
        ) == "pending"

        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
        assert task_after["user_cancelled_at"] is None
        assert unit_after["user_cancelled_at"] is None
        assert unit_after["billing_status"] == "charged"
    finally:
        environment.engine.dispose()


def test_controls_inspect_group_graph_read_only_and_fail_closed_for_corruption(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-controls-read-only-001"
        )
        clock = [video_fixtures._NOW]
        group_fixtures._setup_group(environment, task_id, clock)
        with Session(environment.engine) as reader:
            task_before = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_before = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()

        sessions = []

        class ReadTrackingSession(Session):
            def __init__(self, bind):
                super().__init__(bind)
                self.commit_count = 0
                self.rollback_count = 0
                self.for_update_queries = []
                sessions.append(self)

            def execute(self, statement, *args, **kwargs):
                self.for_update_queries.append(
                    getattr(statement, "_for_update_arg", None) is not None
                )
                return super().execute(statement, *args, **kwargs)

            def commit(self):
                self.commit_count += 1
                return super().commit()

            def rollback(self):
                self.rollback_count += 1
                return super().rollback()

        cancellation = SqlAlchemyHaoaiGroupCancellation(
            session_factory=lambda: ReadTrackingSession(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
            id_factory=lambda: "unused-control-inspection-id",
        )
        actor = TrustedActor(user_id=task_before["user_id"])

        status, reason = cancellation.inspect_controls(actor, task_id)

        assert status == task_before["status"]
        assert reason == "available"
        assert len(sessions) == 1
        assert sessions[0].commit_count == 0
        assert sessions[0].rollback_count >= 1
        assert sessions[0].for_update_queries
        assert not any(sessions[0].for_update_queries)
        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
        assert task_after["status"] == task_before["status"]
        assert task_after["user_cancelled_at"] == task_before["user_cancelled_at"]
        assert task_after["cancellation_reason"] == task_before["cancellation_reason"]
        assert task_after["billing_status"] == task_before["billing_status"]
        assert unit_after["user_cancelled_at"] == unit_before["user_cancelled_at"]
        assert unit_after["cancellation_reason"] == unit_before["cancellation_reason"]
        assert unit_after["billing_status"] == unit_before["billing_status"]

        ordinary_task_id = "ordinary-video-control-001"
        with Session(environment.engine) as writer:
            writer.add(
                AITaskRow(
                    id=ordinary_task_id,
                    user_id=actor.user_id,
                    type="video-single",
                    status="processing",
                    message_id="ordinary-video-message-001",
                    credit_cost=0,
                    billing_status="unbilled",
                    request_data="{}",
                    progress=0,
                    created_at=video_fixtures._NOW,
                    updated_at=video_fixtures._NOW,
                )
            )
            writer.commit()
        assert cancellation.inspect_controls(actor, ordinary_task_id) == (
            None,
            "not_selected",
        )

        with Session(environment.engine) as writer:
            writer.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == task_id)
                .values(type="image-single")
            )
            writer.commit()
        assert cancellation.inspect_controls(actor, task_id) == (
            None,
            "unsafe_task_graph",
        )
        assert all(session.commit_count == 0 for session in sessions)
        assert all(not any(session.for_update_queries) for session in sessions)
    finally:
        environment.engine.dispose()


@pytest.mark.parametrize("tamper", ("token", "reserved_at", "stopped_at"))
def test_policy_event_rejects_reservation_or_stop_time_tampering(
    tmp_path, monkeypatch, tamper
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, f"haoai-group-cancel-event-{tamper}-001"
        )
        clock = [video_fixtures._NOW]
        resumed = _accepted_auth12(environment, task_id, clock)
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            event = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.__table__.c.unit_id
                    == resumed.verified_input.unit_id
                )
            ).mappings().one()
        with environment.engine.begin() as connection:
            if tamper == "token":
                reservation_id = event["reservation_refs"][-1]
                row = connection.execute(
                    select(PollReservationRow.__table__).where(
                        PollReservationRow.__table__.c.reservation_id == reservation_id
                    )
                ).mappings().one()
                connection.execute(
                    update(PollReservationRow.__table__)
                    .where(
                        PollReservationRow.__table__.c.reservation_id == reservation_id
                    )
                    .values(token=row["token"] + "tampered")
                )
            elif tamper == "reserved_at":
                reservation_id = event["reservation_refs"][-1]
                row = connection.execute(
                    select(PollReservationRow.__table__).where(
                        PollReservationRow.__table__.c.reservation_id == reservation_id
                    )
                ).mappings().one()
                connection.execute(
                    update(PollReservationRow.__table__)
                    .where(
                        PollReservationRow.__table__.c.reservation_id == reservation_id
                    )
                    .values(reserved_at=row["reserved_at"] - timedelta(seconds=1))
                )
            else:
                reservation = connection.execute(
                    select(PollReservationRow.__table__).where(
                        PollReservationRow.__table__.c.reservation_id
                        == event["reservation_refs"][-1]
                    )
                ).mappings().one()
                connection.execute(
                    update(PolicyStopEventRow.__table__)
                    .where(PolicyStopEventRow.__table__.c.event_id == event["event_id"])
                    .values(
                        stopped_at=reservation["timeout_at"] + timedelta(seconds=1)
                    )
                )

        cancellation = _cancellation(environment, clock)
        assert cancellation.cancel_owned(
            TrustedActor(user_id=task["user_id"]), task_id
        ) == "pending"
        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            event_after = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.__table__.c.event_id == event["event_id"]
                )
            ).mappings().one()
        assert task_after["user_cancelled_at"] is None
        assert unit_after["user_cancelled_at"] is None
        assert unit_after["billing_status"] == "charged"
        assert event_after["financial_disposition"] == "pending"
    finally:
        environment.engine.dispose()


def test_policy_event_cannot_skip_accepted_non_auth_observation(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-cancel-skipped-reset-001"
        )
        clock = [video_fixtures._NOW]
        claim, intents, polls, resumed, external_ref = group_fixtures._setup_group(
            environment, task_id, clock
        )
        accepted = []

        def query(status_code, body):
            nonlocal resumed
            grant = polls.reserve_query(
                claim, resumed.intent, resumed.verified_input, external_ref
            )
            provider, _ = group_fixtures._provider(
                claim, resumed, status_code=status_code, body=body
            )
            provider._utc_clock = lambda: clock[0]
            outcome = polls.query_and_accept(grant, provider)
            assert outcome.disposition in {"accepted", "policy_stopped"}
            accepted.append(grant.reservation.reservation.reservation_id)

        for index in range(11):
            if index:
                clock[0] += timedelta(seconds=5)
                resumed = intents.prepare(claim)
                assert resumed.disposition == "resume_query"
            query(403, b"no auth available")

        clock[0] += timedelta(seconds=5)
        resumed = intents.prepare(claim)
        assert resumed.disposition == "resume_query"
        query(200, b'{"status":"processing"}')

        for _ in range(12):
            clock[0] += timedelta(seconds=5)
            resumed = intents.prepare(claim)
            assert resumed.disposition == "resume_query"
            query(403, b"no auth available")

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            event = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.__table__.c.unit_id
                    == resumed.verified_input.unit_id
                )
            ).mappings().one()
            reservations = reader.execute(
                select(PollReservationRow.__table__)
                .where(PollReservationRow.__table__.c.unit_id == resumed.verified_input.unit_id)
                .order_by(PollReservationRow.__table__.c.sequence)
            ).mappings().all()
        assert len(accepted) == 24
        assert [row["sequence"] for row in reservations] == list(range(1, 25))
        # Replace the true final 12-auth window with 11 earlier auth facts and
        # the first later auth fact, skipping the signed non-auth reset at 12.
        skipped_refs = tuple(accepted[:11] + [accepted[12]])
        skipped_evidence = tuple(
            next(row["observation_ref"] for row in reservations if row["reservation_id"] == ref)
            for ref in skipped_refs
        )
        with environment.engine.begin() as connection:
            connection.execute(
                update(PolicyStopEventRow.__table__)
                .where(PolicyStopEventRow.__table__.c.event_id == event["event_id"])
                .values(
                    reservation_refs=list(skipped_refs),
                    observation_refs=list(skipped_evidence),
                )
            )

        assert _cancellation(environment, clock).cancel_owned(
            TrustedActor(user_id=task["user_id"]), task_id
        ) == "pending"
        with Session(environment.engine) as reader:
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            event_after = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.__table__.c.event_id == event["event_id"]
                )
            ).mappings().one()
        assert unit["billing_status"] == "charged"
        assert unit["user_cancelled_at"] is None
        assert event_after["financial_disposition"] == "pending"
    finally:
        environment.engine.dispose()


def test_cancel_settles_committed_auth_event_before_cancel_markers(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-cancel-event-first-001"
        )
        clock = [video_fixtures._NOW]
        resumed = _accepted_auth12(environment, task_id, clock)
        with Session(environment.engine) as reader:
            task_before = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_before = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            wallet_before = reader.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == task_before["user_id"]
                )
            ).scalar_one()
        assert unit_before["quoted_amount"] > 0

        cancellation = _cancellation(environment, clock)
        actor = TrustedActor(user_id=task_before["user_id"])
        assert cancellation.cancel_owned(actor, task_id) == "settled"
        assert cancellation.cancel_owned(actor, task_id) == "settled"
        event_settler = SqlAlchemyHaoaiGroupEventSettler(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
            id_factory=lambda: "event-settlement-replay-id",
        )
        assert event_settler.settle_owned(task_before["user_id"], task_id) == "settled"

        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id
                    == resumed.verified_input.unit_id
                )
            ).mappings().one()
            event = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.__table__.c.unit_id
                    == resumed.verified_input.unit_id
                )
            ).mappings().one()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id
                    == resumed.verified_input.unit_id,
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()
            wallet_after = reader.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == task_before["user_id"]
                )
            ).scalar_one()

        assert event["financial_disposition"] == "refunded"
        assert len(refunds) == 1
        assert refunds[0]["business_key"] == f"refund:{unit_before['id']}"
        assert refunds[0]["related_debit_id"] == event["original_debit_ref"]
        assert wallet_after == wallet_before + unit_before["quoted_amount"]
        assert task_after["status"] == "failed"
        assert task_after["result"] == (
            '{"code":"user_cancelled","message":"视频任务已取消",'
            '"status":"failed"}'
        )
        assert task_after["user_cancelled_at"] is not None
        assert unit_after["billing_status"] == "refunded"
        assert unit_after["user_cancelled_at"] is not None
        assert group["status"] == "policy_stopped"
        assert group["sticky_policy_stop_event_ref"] == event["event_id"]
    finally:
        environment.engine.dispose()


def test_cancel_after_intent_retains_fee_and_confirms_cancelled_group(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-cancel-first-001"
        )
        clock = [video_fixtures._NOW]
        group_fixtures._setup_group(environment, task_id, clock)
        with Session(environment.engine) as reader:
            task_before = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_before = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            wallet_before = reader.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == task_before["user_id"]
                )
            ).scalar_one()
        assert unit_before["first_intent_at"] is not None

        cancellation = _cancellation(environment, clock)
        actor = TrustedActor(user_id=task_before["user_id"])
        assert cancellation.cancel_owned(actor, task_id) == "retained"
        assert cancellation.cancel_owned(actor, task_id) == "retained"

        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id == unit_before["id"]
                )
            ).mappings().one()
            ledger = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit_before["id"]
                )
            ).mappings().all()
            wallet_after = reader.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == task_before["user_id"]
                )
            ).scalar_one()

        assert task_after["status"] == "failed"
        assert task_after["user_cancelled_at"] is not None
        assert unit_after["user_cancelled_at"] is not None
        assert unit_after["billing_status"] == "charged"
        assert group["status"] == "cancelled"
        assert group["terminal_reason"] == "user_cancelled"
        assert [row["type"] for row in ledger] == ["usage"]
        assert wallet_after == wallet_before
    finally:
        environment.engine.dispose()


def test_cancel_retains_fee_for_original_intent_with_unknown_create_result(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-cancel-unknown-create-001"
        )
        clock = [video_fixtures._NOW]
        claim_ids = count()
        claims = SqlAlchemyHaoaiGroupClaims(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            id_factory=lambda: f"haoai-cancel-claim-{next(claim_ids)}",
            lease_seconds=180,
        )
        claim = claims.claim_next("video-single", "haoai-cancel-worker")
        assert claim is not None and claim.scope.task_id == task_id
        intent_ids = count()
        intents = SqlAlchemyHaoaiGroupIntents(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            id_factory=lambda: f"haoai-cancel-submission-{next(intent_ids)}",
            registry=environment.binding._producer._registry,
            crypto=environment.capabilities.crypto,
            proof_authority=environment.capabilities.proof_authority,
        )
        prepared = intents.prepare(claim)
        assert prepared.disposition == "committed"
        unknown_ref = ExternalOperationRef(
            intent_id=prepared.intent.external_submission_id,
            attempt_id=prepared.intent.attempt_id,
            provider="haoai",
            external_id=None,
            create_outcome="unknown",
        )
        assert intents.record_submission(
            claim, prepared.intent, prepared.verified_input, unknown_ref
        ) == "recorded"
        clock[0] += timedelta(seconds=5)

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.__table__.c.id
                    == prepared.intent.external_submission_id
                )
            ).mappings().one()
        assert submission["submission_status"] == "unknown"
        assert submission["external_task_id"] is None
        assert task["status"] == "reconciling"
        assert unit["result_status"] == "unknown"

        cancellation = _cancellation(environment, clock)
        actor = TrustedActor(user_id=task["user_id"])
        assert cancellation.cancel_owned(actor, task_id) == "retained"
        assert cancellation.cancel_owned(actor, task_id) == "retained"

        with Session(environment.engine) as reader:
            cancelled_task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            cancelled_unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
        assert cancelled_task["user_cancelled_at"] is not None
        assert cancelled_unit["user_cancelled_at"] is not None
        assert cancelled_unit["billing_status"] == "charged"
        assert cancelled_unit["refunded_amount"] == 0
    finally:
        environment.engine.dispose()


def test_owned_http_cancel_fails_closed_for_group_markers_on_wrong_task_type(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-cancel-wrong-task-type-001"
        )
        clock = [video_fixtures._NOW]
        group_fixtures._setup_group(environment, task_id, clock)
        with environment.engine.begin() as connection:
            task_table = AITaskRow.__table__
            task_row = connection.execute(
                select(task_table).where(task_table.c.id == task_id)
            ).mappings().one()
            owner_id = task_row["user_id"]
            request_data = json.loads(task_row["request_data"])
            request_data.pop("graph_version", None)
            request_data.pop("step_graph_version", None)
            connection.execute(
                task_table.update()
                .where(task_table.c.id == task_id)
                .values(type="image", request_data=json.dumps(request_data))
            )

        cancellation = _cancellation(environment, clock)

        class LegacyWriter:
            def request_cancel(self, task_id: str) -> bool:
                raise AssertionError(
                    f"malformed Group reached legacy writer: {task_id}"
                )

        def actor_resolver(_request):
            return TrustedActor(user_id=owner_id)

        app = FastAPI()
        app.include_router(
            build_task_observation_router(
                uow_factory=lambda: SqlAlchemyTaskObservationUnitOfWork(
                    Session(environment.engine)
                ),
                resolve_account_actor=actor_resolver,
                resolve_active_actor=actor_resolver,
                cancellation_writer=LegacyWriter(),
                haoai_group_cancellation=cancellation,
            )
        )

        async def send_cancel() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.post(
                    f"/api/chat/batch-optimize/{task_id}/cancel"
                )

        response = asyncio.run(send_cancel())
        assert response.status_code == 503
        assert response.json() == {"detail": "视频任务取消状态暂时无法安全确认"}
        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
        assert task_after["user_cancelled_at"] is None
        assert unit_after["user_cancelled_at"] is None
    finally:
        environment.engine.dispose()


def test_owned_http_cancel_routes_valid_group_to_owner_scoped_settlement(
    tmp_path, monkeypatch
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-cancel-http-valid-owner-001"
        )
        clock = [video_fixtures._NOW]
        group_fixtures._setup_group(environment, task_id, clock)
        with Session(environment.engine) as reader:
            task_before = reader.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == task_id
                )
            ).mappings().one()
        cancellation = _cancellation(environment, clock)

        class LegacyWriter:
            def request_cancel(self, task_id: str) -> bool:
                raise AssertionError(
                    f"valid Group request reached legacy writer: {task_id}"
                )

        def actor_resolver(_request):
            return TrustedActor(user_id=task_before["user_id"])

        app = FastAPI()
        app.include_router(
            build_task_observation_router(
                uow_factory=lambda: SqlAlchemyTaskObservationUnitOfWork(
                    Session(environment.engine)
                ),
                resolve_account_actor=actor_resolver,
                resolve_active_actor=actor_resolver,
                cancellation_writer=LegacyWriter(),
                haoai_group_cancellation=cancellation,
            )
        )

        async def send_controls_and_cancel() -> tuple[httpx.Response, httpx.Response]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                controls = await client.get(
                    f"/api/chat/tasks/{task_id}/controls"
                )
                cancel = await client.post(f"/api/chat/batch-optimize/{task_id}/cancel")
                return controls, cancel

        controls_response, response = asyncio.run(send_controls_and_cancel())

        assert controls_response.status_code == 200
        assert controls_response.json() == {
            "task_id": task_id,
            "status": "processing",
            "cancel": {"allowed": True, "reason": "available"},
        }
        assert response.status_code == 200
        assert response.json() == {"task_id": task_id, "status": "cancelling"}
        with Session(environment.engine) as reader:
            task_after = reader.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == task_id
                )
            ).mappings().one()
            unit_after = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            group_after = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id == unit_after["id"]
                )
            ).mappings().one()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit_after["id"],
                    CreditLogRow.__table__.c.type == "refund",
                )
            ).mappings().all()

        assert task_after["user_cancelled_at"] is not None
        assert unit_after["user_cancelled_at"] is not None
        assert unit_after["billing_status"] == "charged"
        assert unit_after["refunded_amount"] == 0
        assert group_after["status"] == "cancelled"
        assert group_after["terminal_reason"] == "user_cancelled"
        assert refunds == []
    finally:
        environment.engine.dispose()
