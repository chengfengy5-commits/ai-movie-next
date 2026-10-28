from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from itertools import count

import test_video_single_claims as video_fixtures
import test_haoai_provider as provider_fixtures
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_admission_staging import _joint_caller, _joint_prepare_and_stage

from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    PolicyStopEventRow,
    PollReservationRow,
)
from haoai_backend.generation_core.haoai_group.claims import (
    SqlAlchemyHaoaiGroupClaims,
)
from haoai_backend.generation_core.haoai_group.cancellation import (
    SqlAlchemyHaoaiGroupCancellation,
)
from haoai_backend.generation_core.haoai_group.intents import (
    SqlAlchemyHaoaiGroupIntents,
)
from haoai_backend.generation_core.haoai_group.polls import (
    HaoaiGroupPollGrant,
    SqlAlchemyHaoaiGroupPolls,
)
from haoai_backend.generation_core.haoai_group.settlement import (
    SqlAlchemyHaoaiGroupEventSettler,
)
from haoai_backend.generation_core.haoai_group.recovery import (
    SqlAlchemyHaoaiGroupRecovery,
)
from haoai_backend.generation_core.haoai_group.results import (
    SqlAlchemyHaoaiGroupResults,
)
from haoai_backend.generation_contracts.types import (
    BoundCredential,
    ExternalOperationRef,
    OwnerTaskScope,
)
from haoai_backend.generation_providers.video.haoai_provider import (
    HaoaiExecutionBinding,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP


def _admit(environment, key: str) -> str:
    caller = _joint_caller(environment.engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=key,
            request=video_fixtures._video_request("haoai"),
        )
        assert isinstance(staged, AdmissionStaged)
        caller.commit()
        return staged.ordered_tasks[0].task_id
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()


def _setup_group(environment, task_id, clock):
    claim_ids = count()
    claims = SqlAlchemyHaoaiGroupClaims(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: f"haoai-poll-claim-{next(claim_ids)}",
        lease_seconds=180,
    )
    claim = claims.claim_next("video-single", "haoai-poll-worker")
    assert claim is not None and claim.scope.task_id == task_id
    intent_ids = count()
    intents = SqlAlchemyHaoaiGroupIntents(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: f"haoai-poll-submission-{next(intent_ids)}",
        registry=environment.binding._producer._registry,
        crypto=environment.capabilities.crypto,
        proof_authority=environment.capabilities.proof_authority,
    )
    prepared = intents.prepare(claim)
    assert prepared.disposition == "committed"
    external_ref = ExternalOperationRef(
        intent_id=prepared.intent.external_submission_id,
        attempt_id=prepared.intent.attempt_id,
        provider="haoai",
        external_id="haoai-known-provider-id-001",
        create_outcome="submitted",
    )
    assert intents.record_submission(
        claim, prepared.intent, prepared.verified_input, external_ref
    ) == "recorded"
    clock[0] += timedelta(seconds=5)
    resumed = intents.prepare(claim)
    assert resumed.disposition == "resume_query"
    poll_ids = count()
    polls = SqlAlchemyHaoaiGroupPolls(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: f"haoai-poll-{next(poll_ids)}",
        registry=environment.binding._producer._registry,
        crypto=environment.capabilities.crypto,
        proof_authority=environment.capabilities.proof_authority,
    )
    return claim, intents, polls, resumed, external_ref


def _provider(
    claim,
    resumed,
    *,
    status_code=200,
    body=b'{"status":"processing"}',
    request_error=None,
    clock=None,
):
    binding = HaoaiExecutionBinding(
        owner_scope=claim.scope,
        snapshot=resumed.verified_input.snapshot,
        intent=resumed.intent,
        credential_binding=resumed.verified_input.credential_binding,
    )
    provider, counts = provider_fixtures.make_provider(
        response=provider_fixtures.FakeResponse(status_code, body),
        binding=binding,
        credential=resumed.verified_input.provider_credential,
        request_error=request_error,
    )
    if clock is not None:
        provider._utc_clock = lambda: clock[0]
    return provider, counts


def test_haoai_group_poll_reserves_once_and_accepts_signed_ordered_observation(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-poll-ordered-001")
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        with environment.engine.begin() as connection:
            connection.execute(
                ExecutionAttemptGroupRow.__table__
                .update()
                .where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id
                    == resumed.verified_input.unit_id
                )
                .values(auth_unavailable_streak=3)
            )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        provider, counts = _provider(claim, resumed)
        outcome = polls.query_and_accept(grant, provider)

        assert outcome.disposition == "accepted"
        assert outcome.observation is not None
        assert outcome.observation.status == "unknown"
        with Session(environment.engine) as reader:
            evidence = reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.evidence_key
                    == f"haoai-poll-v1:{grant.reservation.reservation.reservation_id}"
                )
            ).mappings().one()
            submissions = reader.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.id == resumed.intent.external_submission_id
                )
            ).mappings().all()
        assert evidence["evidence_kind"] == "haoai_poll_observation_v1"
        assert evidence["validity"] == "unknown"
        assert external_ref.external_id in evidence["result_location"]
        assert resumed.verified_input.provider_credential.secret not in evidence["result_location"]
        assert '{"status":"processing"}' not in evidence["result_location"]
        envelope = json.loads(evidence["result_location"])
        canonical = json.dumps(
            envelope["fact"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert hashlib.sha256(canonical).hexdigest() == evidence["payload_digest"]
        assert environment.capabilities.proof_authority.verify(
            "haoai-group-poll-observation-v1",
            canonical,
            envelope["proof"]["signature"],
        )
        assert len(submissions) == 1
        assert submissions[0]["external_task_id"] == external_ref.external_id
        assert len(counts["client"].calls) == 1
        with Session(environment.engine) as reader:
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().one()
        assert group["auth_unavailable_streak"] == 0
        assert polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        ).disposition == "not_due"
    finally:
        environment.engine.dispose()


def test_haoai_group_poll_rejects_second_query_use_of_same_committed_capability(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-poll-single-use-001")
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        provider, counts = _provider(claim, resumed)

        first = polls.query_and_accept(grant, provider)
        second = polls.query_and_accept(grant, provider)

        assert first.disposition == "accepted"
        assert second.disposition == "capability_unavailable"
        assert len(counts["client"].calls) == 1
    finally:
        environment.engine.dispose()


def test_haoai_group_transport_failure_closes_reservation_without_resetting_auth_streak(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-poll-transport-001")
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        with environment.engine.begin() as connection:
            connection.execute(
                ExecutionAttemptGroupRow.__table__
                .update()
                .where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id
                    == resumed.verified_input.unit_id
                )
                .values(auth_unavailable_streak=7)
            )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        provider, counts = _provider(
            claim, resumed, request_error=RuntimeError("transport unavailable")
        )

        outcome = polls.query_and_accept(grant, provider)

        assert outcome.disposition == "closed_unknown"
        assert len(counts["client"].calls) == 1
        reservation = grant.reservation.reservation
        with Session(environment.engine) as reader:
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            poll = reader.execute(
                select(PollReservationRow.__table__).where(
                    PollReservationRow.reservation_id == reservation.reservation_id
                )
            ).mappings().one()
        assert group["auth_unavailable_streak"] == 7
        assert group["poll_used"] == 1
        assert group["outstanding_reservation_ref"] is None
        assert poll["state"] == "closed_unknown"
    finally:
        environment.engine.dispose()


def test_haoai_group_expired_poll_is_get_only_and_next_reservation_uses_original_id(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-poll-expired-001")
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        old_grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(old_grant, HaoaiGroupPollGrant)
        clock[0] += timedelta(seconds=30)

        closed = polls.close_expired_reservation(claim)
        provider, counts = _provider(claim, resumed)
        stale_query = polls.query_and_accept(old_grant, provider)
        next_grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )

        assert closed.disposition == "closed_unknown"
        assert stale_query.disposition == "capability_unavailable"
        assert isinstance(next_grant, HaoaiGroupPollGrant)
        assert next_grant.external_ref.external_id == external_ref.external_id
        assert next_grant.reservation.reservation.committed_poll_count == 2
        assert counts["client"].calls == []
        with Session(environment.engine) as reader:
            rows = reader.execute(
                select(PollReservationRow.__table__)
                .where(PollReservationRow.unit_id == resumed.verified_input.unit_id)
                .order_by(PollReservationRow.sequence)
            ).mappings().all()
        assert [row["state"] for row in rows] == ["closed_unknown", "reserved"]
        assert [row["external_id"] for row in rows] == [external_ref.external_id] * 2
    finally:
        environment.engine.dispose()


def test_haoai_group_poll_720th_reservation_can_query_but_721st_cannot(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-poll-budget-boundary-001")
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        with environment.engine.begin() as connection:
            connection.execute(
                ExecutionAttemptGroupRow.__table__
                .update()
                .where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id
                    == resumed.verified_input.unit_id
                )
                .values(poll_used=719, poll_sequence=719)
            )

        final_grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(final_grant, HaoaiGroupPollGrant)
        assert final_grant.reservation.reservation.sequence == 720
        assert final_grant.reservation.reservation.committed_poll_count == 720
        provider, counts = _provider(claim, resumed)

        final_outcome = polls.query_and_accept(final_grant, provider)
        after_budget = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )

        assert final_outcome.disposition == "accepted"
        assert after_budget.disposition == "budget_exhausted"
        assert len(counts["client"].calls) == 1
    finally:
        environment.engine.dispose()


def test_haoai_group_poll_default_id_factory_can_reserve_and_query(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-poll-default-ids-001")
        clock = [video_fixtures._NOW]
        claim, _, _, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        assert environment.capabilities.crypto is not None
        assert environment.capabilities.proof_authority is not None
        polls = SqlAlchemyHaoaiGroupPolls(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            registry=environment.binding._producer._registry,
            crypto=environment.capabilities.crypto,
            proof_authority=environment.capabilities.proof_authority,
        )

        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        provider, counts = _provider(claim, resumed)
        outcome = polls.query_and_accept(grant, provider)

        assert outcome.disposition == "accepted"
        assert len(counts["client"].calls) == 1
        assert len(grant.reservation.reservation.reservation_id) == 36
        assert len(grant.reservation.reservation.token) == 36
    finally:
        environment.engine.dispose()


def test_haoai_group_reference_failure_starts_next_original_step_after_three_seconds(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-reference-retry-001")
        clock = [video_fixtures._NOW]
        claim, intents, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        original_first_intent = resumed.intent.first_intent_at
        original_deadline = resumed.intent.deadline_at
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        provider, _ = _provider(
            claim,
            resumed,
            body=(
                b'{"status":"failed","error":{"message":'
                b'"failed to download the provided image"}}'
            ),
        )

        observation = polls.query_and_accept(grant, provider)
        early = intents.prepare(claim)
        clock[0] += timedelta(seconds=3)

        assert observation.disposition == "accepted"
        assert early.disposition == "unresolved"
        retry = intents.prepare(claim)
        assert retry.disposition == "committed"
        assert retry.intent is not None
        assert retry.verified_input is not None
        retry_intent = retry.intent
        retry_verified = retry.verified_input
        assert retry_intent.step_id == retry_verified.step_id
        assert retry_intent.step_id != resumed.intent.step_id
        assert retry_intent.attempt_id == retry_intent.step_id
        assert retry_intent.committed_at == clock[0]
        assert retry_intent.first_intent_at == original_first_intent
        assert retry_intent.deadline_at == original_deadline
        with Session(environment.engine) as reader:
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            submissions = reader.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.step_id.in_(
                        group["attempt_step_ids"][:2]
                    )
                )
            ).mappings().all()
            usage_rows = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.billing_unit_id == resumed.verified_input.unit_id,
                    CreditLogRow.type == "usage",
                )
            ).mappings().all()
        assert group["active_attempt"] == 1
        assert group["poll_used"] == 1
        assert group["next_poll_at"] is None
        assert unit["first_intent_at"] == original_first_intent.replace(tzinfo=None)
        assert unit["deadline_at"] == original_deadline.replace(tzinfo=None)
        assert len(submissions) == 2
        assert {row["step_id"] for row in submissions} == set(
            group["attempt_step_ids"][:2]
        )
        assert len(usage_rows) == 1
    finally:
        environment.engine.dispose()


def test_haoai_group_auth_unavailable_12_creates_sticky_pending_financial_event(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-auth12-001")
        clock = [video_fixtures._NOW]
        claim, intents, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )

        outcomes = []
        for index in range(12):
            if index:
                clock[0] += timedelta(seconds=5)
                resumed = intents.prepare(claim)
                assert resumed.disposition == "resume_query"
            grant = polls.reserve_query(
                claim, resumed.intent, resumed.verified_input, external_ref
            )
            assert isinstance(grant, HaoaiGroupPollGrant)
            provider, _ = _provider(
                claim,
                resumed,
                status_code=403,
                body=b"no auth available",
                clock=clock,
            )
            outcomes.append(polls.query_and_accept(grant, provider))

        assert all(outcome.disposition == "accepted" for outcome in outcomes[:-1])
        assert outcomes[-1].disposition == "policy_stopped"
        with Session(environment.engine) as reader:
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            event = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.event_id
                    == group["sticky_policy_stop_event_ref"]
                )
            ).mappings().one()
            last_auth_observation = reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.id == event["observation_refs"][-1]
                )
            ).mappings().one()
            accepted_polls = reader.execute(
                select(PollReservationRow.__table__).where(
                    PollReservationRow.unit_id == resumed.verified_input.unit_id,
                    PollReservationRow.state == "accepted",
                )
            ).mappings().all()
        assert group["status"] == "policy_stopped"
        assert group["auth_unavailable_streak"] == 12
        assert group["sticky_policy_stop_event_ref"] == event["event_id"]
        assert event["financial_disposition"] == "pending"
        assert event["amount"] == unit["quoted_amount"]
        assert event["original_debit_ref"] is not None
        assert event["policy_version"] == HAOAI_ATTEMPT_GROUP.stop_policy_version
        assert len(event["reservation_refs"]) == 12
        assert len(event["observation_refs"]) == 12
        observation_fact = json.loads(last_auth_observation["result_location"])["fact"]
        assert observation_fact["policy_version"] == HAOAI_ATTEMPT_GROUP.policy_version
        assert observation_fact["registry_version"] == HAOAI_ATTEMPT_GROUP.registry_version
        assert len(accepted_polls) == 12
        assert unit["billing_status"] == "charged"
        assert unit["refunded_amount"] == 0
        assert task["status"] == "reconciling"
    finally:
        environment.engine.dispose()


def test_haoai_group_zero_fee_event_requires_verified_zero_ledger_before_terminal(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        model_table = video_fixtures.joint_fixtures.system_models
        with environment.engine.begin() as connection:
            params = json.loads(
                connection.execute(
                    select(model_table.c.params).where(
                        model_table.c.id
                        == video_fixtures.joint_fixtures._G2_JOINT_MODEL_ID
                    )
                ).scalar_one()
            )
            params["per_second_cost"] = 0
            connection.execute(
                model_table.update()
                .where(
                    model_table.c.id
                    == video_fixtures.joint_fixtures._G2_JOINT_MODEL_ID
                )
                .values(params=json.dumps(params))
            )

        task_id = _admit(environment, "haoai-group-zero-fee-conflicting-ledger-001")
        clock = [video_fixtures._NOW]
        claim, intents, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.task_id == task_id
                )
            ).mappings().one()
        assert task["billing_status"] == unit["billing_status"] == "no_charge"
        assert task["credit_cost"] == unit["quoted_amount"] == 0
        assert unit["debited_amount"] == unit["refunded_amount"] == 0

        # A stale contradictory ledger row must make zero-fee proof fail closed.
        with environment.engine.begin() as connection:
            connection.execute(
                CreditLogRow.__table__.insert().values(
                    id="synthetic-zero-conflict-ledger",
                    user_id=task["user_id"],
                    amount=-1,
                    balance_after=499,
                    type="usage",
                    description="synthetic conflicting ledger evidence",
                    task_id=task_id,
                    business_key="synthetic-zero-conflict",
                    billing_unit_id=unit["id"],
                    related_debit_id=None,
                )
            )

        outcomes = []
        for index in range(12):
            if index:
                clock[0] += timedelta(seconds=5)
                resumed = intents.prepare(claim)
                assert resumed.disposition == "resume_query"
            grant = polls.reserve_query(
                claim, resumed.intent, resumed.verified_input, external_ref
            )
            assert isinstance(grant, HaoaiGroupPollGrant)
            provider, _ = _provider(
                claim,
                resumed,
                status_code=403,
                body=b"no auth available",
                clock=clock,
            )
            outcomes.append(polls.query_and_accept(grant, provider))

        with Session(environment.engine) as reader:
            events = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.unit_id == unit["id"]
                )
            ).mappings().all()
        assert outcomes[-1].disposition != "policy_stopped"
        assert events == []
    finally:
        environment.engine.dispose()


def test_haoai_group_verified_zero_fee_event_settles_without_ledger_rows(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        model_table = video_fixtures.joint_fixtures.system_models
        with environment.engine.begin() as connection:
            params = json.loads(
                connection.execute(
                    select(model_table.c.params).where(
                        model_table.c.id
                        == video_fixtures.joint_fixtures._G2_JOINT_MODEL_ID
                    )
                ).scalar_one()
            )
            params["per_second_cost"] = 0
            connection.execute(
                model_table.update()
                .where(
                    model_table.c.id
                    == video_fixtures.joint_fixtures._G2_JOINT_MODEL_ID
                )
                .values(params=json.dumps(params))
            )

        task_id = _admit(environment, "haoai-group-zero-fee-settle-001")
        clock = [video_fixtures._NOW]
        claim, intents, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        for index in range(12):
            if index:
                clock[0] += timedelta(seconds=5)
                resumed = intents.prepare(claim)
                assert resumed.disposition == "resume_query"
            grant = polls.reserve_query(
                claim, resumed.intent, resumed.verified_input, external_ref
            )
            assert isinstance(grant, HaoaiGroupPollGrant)
            provider, _ = _provider(
                claim,
                resumed,
                status_code=403,
                body=b"no auth available",
                clock=clock,
            )
            outcome = polls.query_and_accept(grant, provider)
        assert outcome.disposition == "policy_stopped"

        with Session(environment.engine) as reader:
            event = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            owner_id = reader.execute(
                select(AITaskRow.__table__.c.user_id).where(AITaskRow.id == task_id)
            ).scalar_one()
        assert event["financial_disposition"] == "pending"

        settler = SqlAlchemyHaoaiGroupEventSettler(
            lambda: Session(environment.engine),
            lambda: clock[0],
            environment.capabilities.proof_authority,
            lambda: "unused-zero-refund-id",
        )
        first = settler.settle_owned(owner_id, task_id)
        replay = settler.settle_owned(owner_id, task_id)
        assert first == replay == "settled"

        with Session(environment.engine) as reader:
            settled = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.event_id == event["event_id"]
                )
            ).mappings().one()
            ledger_rows = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.task_id == task_id
                )
            ).mappings().all()
        assert settled["financial_disposition"] == "no_debit"
        assert settled["original_debit_ref"] is None
        assert settled["refund_ref"] is None
        assert ledger_rows == []
    finally:
        environment.engine.dispose()


def test_haoai_group_policy_event_refunds_original_debit_once_and_replays(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-auth12-refund-001")
        clock = [video_fixtures._NOW]
        claim, intents, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        for index in range(12):
            if index:
                clock[0] += timedelta(seconds=5)
                resumed = intents.prepare(claim)
                assert resumed.disposition == "resume_query"
            grant = polls.reserve_query(
                claim, resumed.intent, resumed.verified_input, external_ref
            )
            assert isinstance(grant, HaoaiGroupPollGrant)
            provider, _ = _provider(
                claim,
                resumed,
                status_code=403,
                body=b"no auth available",
                clock=clock,
            )
            outcome = polls.query_and_accept(grant, provider)
        assert outcome.disposition == "policy_stopped"

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.task_id == task_id
                )
            ).mappings().one()
            debit = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.business_key == f"debit:{unit['id']}"
                )
            ).mappings().one()
            wallet_before = reader.execute(
                select(UserCreditRow.credits).where(UserCreditRow.user_id == task["user_id"])
            ).scalar_one()
        id_factory = lambda: "haoai-group-auth12-refund-event-001"
        settler = SqlAlchemyHaoaiGroupEventSettler(
            lambda: Session(environment.engine),
            lambda: clock[0],
            environment.capabilities.proof_authority,
            id_factory,
        )
        recovery = SqlAlchemyHaoaiGroupRecovery(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            cancellation=SqlAlchemyHaoaiGroupCancellation(
                lambda: Session(environment.engine),
                lambda: clock[0],
                environment.capabilities.proof_authority,
                id_factory,
            ),
            event_settler=settler,
            results=SqlAlchemyHaoaiGroupResults(
                lambda: Session(environment.engine),
                lambda: clock[0],
                environment.capabilities.proof_authority,
                id_factory,
            ),
        )
        outcome = recovery.consume_due(
            OwnerTaskScope(
                user_id=task["user_id"],
                task_id=task_id,
                operation="video.single",
            )
        )
        assert outcome == "settled"

        with Session(environment.engine) as reader:
            events = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.unit_id == unit["id"]
                )
            ).mappings().all()
            refunds = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.business_key == f"refund:{unit['id']}"
                )
            ).mappings().all()
            wallet_after = reader.execute(
                select(UserCreditRow.credits).where(UserCreditRow.user_id == task["user_id"])
            ).scalar_one()
            settled_unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == unit["id"]
                )
            ).mappings().one()
            settled_task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            settled_group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id == unit["id"]
                )
            ).mappings().one()
        assert len(events) == 1
        assert events[0]["financial_disposition"] == "refunded"
        assert events[0]["original_debit_ref"] == debit["id"]
        assert events[0]["refund_ref"] == refunds[0]["id"]
        assert len(refunds) == 1
        assert refunds[0]["related_debit_id"] == debit["id"]
        assert refunds[0]["amount"] == unit["quoted_amount"]
        assert wallet_after == wallet_before + unit["quoted_amount"]
        assert settled_unit["billing_status"] == settled_task["billing_status"] == "refunded"
        assert settled_unit["refunded_amount"] == unit["quoted_amount"]
        assert settled_task["status"] == "failed"
        assert settled_task["result"] is not None
        assert json.loads(settled_task["result"])["code"] == "auth_unavailable_12"
        assert settled_unit["result_status"] == "unknown"
        assert settled_group["status"] == "policy_stopped"
        assert settled_group["terminal_reason"] == "auth_unavailable_12"
        assert settled_task["claimed_by"] is None
        assert settled_task["claim_token"] is None
        assert SqlAlchemyHaoaiGroupClaims(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
        ).claim_next("video-single", "haoai-auth12-terminal-check") is None
    finally:
        environment.engine.dispose()


def test_haoai_group_auth12_streak_crosses_closed_unknown_reservation_gap(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-auth12-reservation-gap-001")
        clock = [video_fixtures._NOW]
        claim, intents, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        outcomes = []
        gap_outcome = None
        for index in range(12):
            if index:
                clock[0] += timedelta(seconds=5)
                resumed = intents.prepare(claim)
                assert resumed.disposition == "resume_query"
            if index == 6:
                gap_grant = polls.reserve_query(
                    claim, resumed.intent, resumed.verified_input, external_ref
                )
                assert isinstance(gap_grant, HaoaiGroupPollGrant)
                gap_provider, _ = _provider(
                    claim,
                    resumed,
                    request_error=RuntimeError("synthetic transport failure"),
                )
                gap_outcome = polls.query_and_accept(gap_grant, gap_provider)
                assert gap_outcome.disposition == "closed_unknown"
                clock[0] += timedelta(seconds=5)
                resumed = intents.prepare(claim)
                assert resumed.disposition == "resume_query"

            grant = polls.reserve_query(
                claim, resumed.intent, resumed.verified_input, external_ref
            )
            assert isinstance(grant, HaoaiGroupPollGrant)
            provider, _ = _provider(
                claim,
                resumed,
                status_code=403,
                body=b"no auth available",
                clock=clock,
            )
            outcomes.append(polls.query_and_accept(grant, provider))

        assert gap_outcome is not None
        assert all(item.disposition == "accepted" for item in outcomes[:-1])
        assert outcomes[-1].disposition == "policy_stopped"
        with Session(environment.engine) as reader:
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            event = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.event_id
                    == group["sticky_policy_stop_event_ref"]
                )
            ).mappings().one()
            rows = reader.execute(
                select(PollReservationRow.__table__)
                .where(PollReservationRow.unit_id == resumed.verified_input.unit_id)
                .order_by(PollReservationRow.sequence)
            ).mappings().all()
        assert group["auth_unavailable_streak"] == 12
        assert len(event["reservation_refs"]) == 12
        assert [row["state"] for row in rows].count("closed_unknown") == 1
        assert len([row for row in rows if row["state"] == "accepted"]) == 12
        assert rows[5]["state"] == "accepted"
        assert rows[6]["state"] == "closed_unknown"
        assert rows[7]["state"] == "accepted"
    finally:
        environment.engine.dispose()


@pytest.mark.parametrize("unavailable_fact", ["credential", "identity"])
def test_haoai_group_auth_streak_survives_non_http_identity_observation(
    tmp_path,
    monkeypatch,
    unavailable_fact: str,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(
            environment, f"haoai-group-auth-streak-no-http-{unavailable_fact}-001"
        )
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        with environment.engine.begin() as connection:
            connection.execute(
                ExecutionAttemptGroupRow.__table__
                .update()
                .where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id
                    == resumed.verified_input.unit_id
                )
                .values(auth_unavailable_streak=11)
            )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        provider, counts = _provider(claim, resumed)
        if unavailable_fact == "credential":
            provider._credential_resolver = lambda _binding, _purpose: None
        else:
            provider._credential_resolver = lambda binding, _purpose: BoundCredential(
                provider=binding.provider,
                principal_id=binding.original_principal_id,
                secret=external_ref.external_id,
            )

        outcome = polls.query_and_accept(grant, provider)

        assert outcome.disposition == "closed_unknown"
        assert counts["client"].calls == []
        with Session(environment.engine) as reader:
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            reservation = reader.execute(
                select(PollReservationRow.__table__).where(
                    PollReservationRow.reservation_id
                    == grant.reservation.reservation.reservation_id
                )
            ).mappings().one()
            events = reader.execute(
                select(PolicyStopEventRow.__table__).where(
                    PolicyStopEventRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().all()
        assert group["auth_unavailable_streak"] == 11
        assert reservation["state"] == "closed_unknown"
        assert events == []
    finally:
        environment.engine.dispose()


def test_haoai_group_accepted_non_auth_http_observation_resets_auth_streak(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = _admit(environment, "haoai-group-auth-streak-http-reset-001")
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = _setup_group(
            environment, task_id, clock
        )
        with environment.engine.begin() as connection:
            connection.execute(
                ExecutionAttemptGroupRow.__table__
                .update()
                .where(
                    ExecutionAttemptGroupRow.__table__.c.unit_id
                    == resumed.verified_input.unit_id
                )
                .values(auth_unavailable_streak=11)
            )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        provider, _ = _provider(
            claim, resumed, status_code=429, body=b'{"error":"provider busy"}'
        )

        outcome = polls.query_and_accept(grant, provider)

        assert outcome.disposition == "accepted"
        with Session(environment.engine) as reader:
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().one()
        assert group["auth_unavailable_streak"] == 0
    finally:
        environment.engine.dispose()
