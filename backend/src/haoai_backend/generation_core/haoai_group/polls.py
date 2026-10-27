"""Persisted HaoAI Group poll grants and ordered safe observations."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.ports import CommittedPollReservation
from haoai_backend.generation_contracts.records import PollReservation, PolicyStopEvent
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    ExternalOperationRef,
    ProviderObservation,
)
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
)
from haoai_backend.generation_credentials.haoai_group_execution_snapshot import (
    VerifiedHaoaiGroupExecutionInput,
    read_original_haoai_group_execution_input,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.haoai_group.claims import _claim_live
from haoai_backend.generation_core.haoai_group.evidence import (
    is_signed_auth_unavailable_observation,
)
from haoai_backend.generation_core.haoai_group.intents import (
    _current_submission_rows,
    _database_datetime,
    _intent_matches_graph,
)
from haoai_backend.generation_core.haoai_group.persistence import (
    LockedHaoaiGroupGraph,
    lock_haoai_group_graph,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    PolicyStopEventRow,
    PollReservationRow,
)
from haoai_backend.generation_providers.video.haoai_provider import HaoaiVideoProvider
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
SnapshotReader = Callable[..., VerifiedHaoaiGroupExecutionInput | None]


def _new_uuid_id() -> str:
    return str(uuid.uuid4())

_OBSERVATION_PURPOSE = "haoai-group-poll-observation-v1"
_EVIDENCE_KIND = "haoai_poll_observation_v1"
_QUERY_INTERVAL = timedelta(seconds=5)
_QUERY_TIMEOUT = timedelta(seconds=30)
_POLL_BUDGET = 720
_AUTH_MARKERS = frozenset(
    {
        "auth_unavailable",
        "no auth available",
        "no available auth candidates",
        "all candidates are unavailable",
    }
)
_SOURCE_FACTS = frozenset(
    {
        "http_error",
        "object",
        "malformed_json",
        "non_object",
        "source_completed",
        "source_failed",
        "source_unknown",
        "response_unusable",
        "transport_unavailable",
        "original_credential_unavailable",
        "original_identity_unusable",
    }
)
_RECONCILING_RESULT = json.dumps(
    {"message": "视频任务策略处理中", "status": "reconciling"},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)


@dataclass(frozen=True, slots=True)
class HaoaiGroupPollGrant:
    """One committed, one-use in-process permission to query an original ID."""

    reservation: CommittedPollReservation
    claim: ClaimAuthority = field(repr=False, compare=False)
    intent: CommittedIntent = field(repr=False)
    verified_input: VerifiedHaoaiGroupExecutionInput = field(repr=False)
    external_ref: ExternalOperationRef = field(repr=False)
    _nonce: str = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class HaoaiGroupPollOutcome:
    disposition: Literal[
        "accepted",
        "closed_unknown",
        "stale",
        "ownership_lost",
        "capability_unavailable",
        "blocked",
        "not_due",
        "budget_exhausted",
        "policy_stopped",
    ]
    observation: ProviderObservation | None = field(default=None, repr=False)
    observation_id: str | None = None


@dataclass(frozen=True, slots=True)
class _ReservationExpectation:
    reservation: PollReservation
    confirmed_at: datetime


@dataclass(slots=True)
class SqlAlchemyHaoaiGroupPolls:
    """Reserve, query, and accept HaoAI Group polls without loosening video-single."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory = field(default_factory=lambda: _new_uuid_id)
    registry: CredentialRegistry | None = None
    crypto: CredentialCrypto | None = None
    proof_authority: HmacProofAuthority | None = None
    snapshot_reader: SnapshotReader = read_original_haoai_group_execution_input
    _grant_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _grants: dict[str, HaoaiGroupPollGrant] = field(
        default_factory=dict, init=False, repr=False
    )

    def reserve_query(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedHaoaiGroupExecutionInput,
        external_ref: ExternalOperationRef,
    ) -> HaoaiGroupPollGrant | HaoaiGroupPollOutcome:
        if not self._query_input_matches(claim, intent, verified, external_ref):
            return HaoaiGroupPollOutcome("blocked")
        expected: _ReservationExpectation | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    now = self._server_now()
                    if graph is None or not _claim_live(graph.task, claim, now):
                        return HaoaiGroupPollOutcome("ownership_lost")
                    state = _queryable_graph_state(
                        graph, claim, intent, verified, external_ref, now
                    )
                    if state == "not_due":
                        return HaoaiGroupPollOutcome("not_due")
                    if state == "budget_exhausted":
                        return HaoaiGroupPollOutcome("budget_exhausted")
                    if state != "ready":
                        return HaoaiGroupPollOutcome("blocked")
                    if not self._read_verified(session, graph, verified):
                        return HaoaiGroupPollOutcome("blocked")
                    group = graph.group
                    if (
                        type(group.get("poll_used")) is not int
                        or type(group.get("poll_sequence")) is not int
                    ):
                        return HaoaiGroupPollOutcome("blocked")
                    try:
                        reservation_id = self.id_factory()
                        token = self.id_factory()
                    except Exception:
                        return HaoaiGroupPollOutcome("blocked")
                    if not _valid_id(reservation_id) or not _valid_id(token):
                        return HaoaiGroupPollOutcome("blocked")
                    deadline = _aware_utc(graph.unit.get("deadline_at"))
                    timeout_at = min(now + _QUERY_TIMEOUT, deadline)
                    reservation = PollReservation(
                        reservation_id=reservation_id,
                        group_key=str(graph.group["group_key"]),
                        sequence=group["poll_sequence"] + 1,
                        attempt_id=intent.attempt_id,
                        step_id=intent.step_id,
                        submission_id=intent.external_submission_id,
                        external_id=str(external_ref.external_id),
                        payload_digest=intent.request_digest,
                        committed_poll_count=group["poll_used"] + 1,
                        reserved_at=now,
                        timeout_at=timeout_at,
                        next_poll_at=now + _QUERY_INTERVAL,
                        token=token,
                        state="reserved",
                    )
                    result = GenerationRecordRepository(session).reserve_poll(
                        reservation,
                        unit_id=verified.unit_id,
                        expected_poll_used=group["poll_used"],
                        expected_poll_sequence=group["poll_sequence"],
                    )
                    if result.status != "created":
                        return HaoaiGroupPollOutcome("blocked")
                    expected = _ReservationExpectation(reservation, now)
        except Exception:
            return HaoaiGroupPollOutcome("blocked")
        if expected is None:
            return HaoaiGroupPollOutcome("blocked")
        if not self._confirm_reservation(claim, intent, verified, expected):
            return HaoaiGroupPollOutcome("blocked")
        capability = CommittedPollReservation(
            expected.reservation, confirmed_at=expected.confirmed_at
        )
        try:
            nonce = self.id_factory()
        except Exception:
            return HaoaiGroupPollOutcome("blocked")
        if not _valid_id(nonce):
            return HaoaiGroupPollOutcome("blocked")
        grant = HaoaiGroupPollGrant(
            reservation=capability,
            claim=claim,
            intent=intent,
            verified_input=verified,
            external_ref=external_ref,
            _nonce=nonce,
        )
        with self._grant_lock:
            self._grants[nonce] = grant
        return grant

    def close_expired_reservation(
        self, claim: ClaimAuthority
    ) -> HaoaiGroupPollOutcome:
        """Close a crash-left reservation after its persisted timeout; never refund budget."""

        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != "video.single"
            or claim.local_lost_signal.is_set()
        ):
            return HaoaiGroupPollOutcome("blocked")
        expected: PollReservation | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    now = self._server_now()
                    if graph is None or not _claim_live(graph.task, claim, now):
                        return HaoaiGroupPollOutcome("ownership_lost")
                    reservation_id = graph.group.get("outstanding_reservation_ref")
                    if not isinstance(reservation_id, str) or not reservation_id:
                        return HaoaiGroupPollOutcome("blocked")
                    row = session.execute(
                        select(PollReservationRow.__table__)
                        .where(
                            PollReservationRow.__table__.c.reservation_id
                            == reservation_id
                        )
                        .with_for_update()
                    ).mappings().one_or_none()
                    if row is None or not _outstanding_reservation_matches(graph, row):
                        return HaoaiGroupPollOutcome("blocked")
                    if row.get("state") != "reserved":
                        return HaoaiGroupPollOutcome("blocked")
                    if now < _aware_utc(row.get("timeout_at")):
                        return HaoaiGroupPollOutcome("not_due")
                    reservation = _poll_record_from_row(row)
                    poll_change = session.execute(
                        update(PollReservationRow.__table__)
                        .where(
                            PollReservationRow.__table__.c.reservation_id
                            == reservation_id,
                            PollReservationRow.__table__.c.state == "reserved",
                            PollReservationRow.__table__.c.observation_ref.is_(None),
                        )
                        .values(state="closed_unknown")
                    )
                    group_change = session.execute(
                        update(ExecutionAttemptGroupRow.__table__)
                        .where(
                            ExecutionAttemptGroupRow.__table__.c.unit_id
                            == graph.binding.unit_id,
                            ExecutionAttemptGroupRow.__table__.c.group_key
                            == graph.binding.group_key,
                            ExecutionAttemptGroupRow.__table__.c.active_attempt
                            == graph.binding.attempt_index,
                            ExecutionAttemptGroupRow.__table__.c.outstanding_reservation_ref
                            == reservation_id,
                        )
                        .values(outstanding_reservation_ref=None)
                    )
                    if poll_change.rowcount != 1 or group_change.rowcount != 1:
                        raise RuntimeError("expired HaoAI poll close CAS lost")
                    expected = reservation
        except Exception:
            return HaoaiGroupPollOutcome("blocked")
        if expected is None or not self._confirm_expired_close(claim, expected):
            return HaoaiGroupPollOutcome("blocked")
        with self._grant_lock:
            self._grants = {
                nonce: grant
                for nonce, grant in self._grants.items()
                if grant.reservation.reservation.reservation_id
                != expected.reservation_id
            }
        return HaoaiGroupPollOutcome("closed_unknown")

    def query_and_accept(
        self,
        grant: HaoaiGroupPollGrant,
        provider: HaoaiVideoProvider,
    ) -> HaoaiGroupPollOutcome:
        if type(grant) is not HaoaiGroupPollGrant or not isinstance(
            provider, HaoaiVideoProvider
        ):
            return HaoaiGroupPollOutcome("capability_unavailable")
        with self._grant_lock:
            issued = self._grants.pop(grant._nonce, None)
        if issued is not grant:
            return HaoaiGroupPollOutcome("capability_unavailable")
        if not self._provider_binding_matches(provider, grant):
            return HaoaiGroupPollOutcome("blocked")
        authorization = self._authorize_before_query(grant)
        if authorization != "ready":
            return HaoaiGroupPollOutcome(authorization)
        try:
            observation = provider.query_existing(
                grant.verified_input.snapshot,
                grant.external_ref,
                reservation=grant.reservation,
            )
        except Exception:
            observation = self._transport_unknown(grant)
        if not _observation_matches(observation, grant):
            observation = self._transport_unknown(grant)
        return self._persist_and_accept(grant, observation)

    def _authorize_before_query(self, grant: HaoaiGroupPollGrant) -> str:
        reservation = grant.reservation.reservation
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session,
                        grant.claim.scope.user_id,
                        grant.claim.scope.task_id,
                    )
                    now = self._server_now()
                    if graph is None or not _claim_live(graph.task, grant.claim, now):
                        return "ownership_lost"
                    row = session.execute(
                        select(PollReservationRow.__table__).where(
                            PollReservationRow.__table__.c.reservation_id
                            == reservation.reservation_id
                        )
                    ).mappings().one_or_none()
                    if (
                        row is None
                        or not _reservation_row_matches(row, grant, state="reserved")
                        or now >= _aware_utc(row.timeout_at)
                        or graph.group.get("outstanding_reservation_ref")
                        != reservation.reservation_id
                        or _queryable_graph_state(
                            graph,
                            grant.claim,
                            grant.intent,
                            grant.verified_input,
                            grant.external_ref,
                            now,
                            require_due=False,
                            expected_reservation_id=reservation.reservation_id,
                        )
                        != "ready"
                        or not self._read_verified(session, graph, grant.verified_input)
                    ):
                        return "stale"
                    return "ready"
        except Exception:
            return "stale"

    def _persist_and_accept(
        self,
        grant: HaoaiGroupPollGrant,
        observation: ProviderObservation,
    ) -> HaoaiGroupPollOutcome:
        evidence_id: str | None = None
        accepted = False
        policy_stopped = False
        closed_unknown = False
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session,
                        grant.claim.scope.user_id,
                        grant.claim.scope.task_id,
                    )
                    if graph is None or not _graph_matches_grant(graph, grant):
                        return HaoaiGroupPollOutcome("stale", observation)
                    reservation = grant.reservation.reservation
                    reservation_row = session.execute(
                        select(PollReservationRow.__table__)
                        .where(
                            PollReservationRow.__table__.c.reservation_id
                            == reservation.reservation_id
                        )
                        .with_for_update()
                    ).mappings().one_or_none()
                    if reservation_row is None or not _reservation_row_matches(
                        reservation_row, grant
                    ):
                        return HaoaiGroupPollOutcome("stale", observation)
                    evidence = _observation_evidence(
                        grant,
                        observation,
                        self.proof_authority,
                        self.id_factory,
                    )
                    if evidence is None:
                        return HaoaiGroupPollOutcome("blocked", observation)
                    evidence_id = self._persist_evidence(session, grant, evidence)
                    if evidence_id is None:
                        return HaoaiGroupPollOutcome("blocked", observation)
                    now = self._server_now()
                    live_claim = _claim_live(graph.task, grant.claim, now)
                    source_fact = _source_fact(observation)
                    timely_reservation = now < _aware_utc(reservation.timeout_at)
                    has_outstanding = (
                        graph.group.get("outstanding_reservation_ref")
                        == reservation.reservation_id
                    )
                    if not live_claim or not timely_reservation or not has_outstanding:
                        closed_unknown = self._close_reservation_unknown(
                            session, graph, reservation_row, evidence_id, now
                        )
                    elif source_fact in {
                        "transport_unavailable",
                        "original_credential_unavailable",
                        "original_identity_unusable",
                    }:
                        closed_unknown = self._close_reservation_unknown(
                            session, graph, reservation_row, evidence_id, now
                        )
                    else:
                        accepted, policy_stopped = self._accept_observation(
                            session,
                            graph,
                            reservation_row,
                            grant,
                            observation,
                            evidence_id,
                            now,
                        )
        except Exception:
            # Unknown transaction outcomes are resolved from the stable evidence key.
            return self._confirm_observation(grant, observation)
        if policy_stopped:
            return HaoaiGroupPollOutcome("policy_stopped", observation, evidence_id)
        if accepted:
            return HaoaiGroupPollOutcome("accepted", observation, evidence_id)
        if closed_unknown:
            return HaoaiGroupPollOutcome("closed_unknown", observation, evidence_id)
        return self._confirm_observation(grant, observation)

    def _persist_evidence(
        self,
        session: Session,
        grant: HaoaiGroupPollGrant,
        evidence: tuple[str, str, str, str],
    ) -> str | None:
        evidence_id, evidence_key, payload_digest, result_location = evidence
        reservation = grant.reservation.reservation
        existing = session.execute(
            select(ResultEvidenceRow.__table__).where(
                ResultEvidenceRow.__table__.c.evidence_key == evidence_key
            )
        ).mappings().one_or_none()
        if existing is not None:
            if not _evidence_row_matches(
                existing,
                grant,
                payload_digest,
                result_location,
            ):
                return None
            return str(existing["id"])
        session.execute(
            insert(ResultEvidenceRow.__table__).values(
                id=evidence_id,
                step_id=reservation.step_id,
                submission_id=reservation.submission_id,
                evidence_key=evidence_key,
                source="worker",
                evidence_kind=_EVIDENCE_KIND,
                payload_digest=payload_digest,
                result_location=result_location,
                result_digest=observation_body_digest(result_location),
                received_at=_database_datetime(_observed_at(result_location)),
                validity="unknown",
                application_status="pending",
                applied_at=None,
            )
        )
        return evidence_id

    def _accept_observation(
        self,
        session: Session,
        graph: LockedHaoaiGroupGraph,
        reservation_row: Mapping[str, object],
        grant: HaoaiGroupPollGrant,
        observation: ProviderObservation,
        evidence_id: str,
        now: datetime,
    ) -> tuple[bool, bool]:
        reservation = grant.reservation.reservation
        source_fact = _source_fact(observation)
        group = graph.group
        prior_streak = group.get("auth_unavailable_streak")
        if type(prior_streak) is not int or not 0 <= prior_streak < 12:
            return False, False
        markers = _auth_markers(observation)
        next_streak = prior_streak + 1 if source_fact == "http_error" and markers else 0
        policy_stop = next_streak >= 12
        poll_table = PollReservationRow.__table__
        poll_change = session.execute(
            update(poll_table)
            .where(
                poll_table.c.reservation_id == reservation.reservation_id,
                poll_table.c.unit_id == grant.verified_input.unit_id,
                poll_table.c.group_key == reservation.group_key,
                poll_table.c.sequence == reservation.sequence,
                poll_table.c.state == "reserved",
                poll_table.c.observation_ref.is_(None),
            )
            .values(state="accepted", observation_ref=evidence_id)
        )
        if poll_change.rowcount != 1:
            return False, False
        group_table = ExecutionAttemptGroupRow.__table__
        group_values: dict[str, object] = {
            "auth_unavailable_streak": next_streak,
            "outstanding_reservation_ref": None,
        }
        retryable_reference_failure = (
            observation.status == "failed"
            and source_fact == "source_failed"
            and observation.payload.get("source_status") == "failed"
            and bool(_image_download_markers(observation))
            and grant.verified_input.attempt_index < HAOAI_ATTEMPT_GROUP.max_retries
        )
        if retryable_reference_failure:
            group_values.update(
                {
                    "active_attempt": grant.verified_input.attempt_index + 1,
                    "next_poll_at": _database_datetime(
                        now + timedelta(seconds=3)
                    ),
                }
            )
        if policy_stop:
            event = self._new_policy_stop_event(
                session,
                graph,
                reservation,
                evidence_id,
                now,
                next_streak,
            )
            if event is None:
                raise RuntimeError("HaoAI policy event cannot be safely staged")
            staged = GenerationRecordRepository(session).stage_record(event)
            if staged.status not in {"created", "same"}:
                raise RuntimeError("HaoAI policy event could not be committed")
            group_values.update(
                {
                    "status": "policy_stopped",
                    "terminal_reason": "auth_unavailable_12",
                    "sticky_policy_stop_event_ref": event.event_id,
                }
            )
            task_change = session.execute(
                update(AITaskRow.__table__)
                .where(
                    AITaskRow.__table__.c.id == grant.claim.scope.task_id,
                    AITaskRow.__table__.c.user_id == grant.claim.scope.user_id,
                    AITaskRow.__table__.c.status.in_(("processing", "reconciling")),
                    AITaskRow.__table__.c.claimed_by == grant.claim.worker_instance,
                    AITaskRow.__table__.c.claim_token == grant.claim.token,
                    AITaskRow.__table__.c.execution_generation
                    == grant.claim.execution_generation,
                    AITaskRow.__table__.c.user_cancelled_at.is_(None),
                )
                .values(
                    status="reconciling",
                    recovery_status="reconciling",
                    result=_RECONCILING_RESULT,
                    progress_message="视频任务策略处理中",
                    updated_at=_database_datetime(now),
                )
            )
            if task_change.rowcount != 1:
                raise RuntimeError("HaoAI policy stop Task fence changed")
        group_change = session.execute(
            update(group_table)
            .where(
                group_table.c.unit_id == grant.verified_input.unit_id,
                group_table.c.group_key == reservation.group_key,
                group_table.c.active_attempt == grant.verified_input.attempt_index,
                group_table.c.status == group.get("status"),
                group_table.c.poll_used == group.get("poll_used"),
                group_table.c.poll_sequence == reservation.sequence,
                group_table.c.outstanding_reservation_ref
                == reservation.reservation_id,
                group_table.c.auth_unavailable_streak == prior_streak,
                group_table.c.sticky_policy_stop_event_ref.is_(None),
            )
            .values(**group_values)
        )
        if group_change.rowcount != 1:
            raise RuntimeError("HaoAI Group ordered observation CAS lost")
        status = observation.status
        if status in {"completed", "failed"}:
            step_table = ExecutionStepRow.__table__
            step_change = session.execute(
                update(step_table)
                .where(
                    step_table.c.id == reservation.step_id,
                    step_table.c.billing_unit_id == grant.verified_input.unit_id,
                    step_table.c.execution_status.in_(("submitted", "reconciling")),
                )
                .values(
                    execution_status="result_observed",
                    updated_at=_database_datetime(now),
                )
            )
            if step_change.rowcount != 1:
                raise RuntimeError("HaoAI Group result observation step CAS lost")
        return True, policy_stop

    def _new_policy_stop_event(
        self,
        session: Session,
        graph: LockedHaoaiGroupGraph,
        reservation: PollReservation,
        observation_id: str,
        now: datetime,
        streak: int,
    ) -> PolicyStopEvent | None:
        prior_reservations = session.execute(
            select(PollReservationRow.__table__)
            .where(
                PollReservationRow.__table__.c.unit_id == graph.binding.unit_id,
                PollReservationRow.__table__.c.group_key == reservation.group_key,
                PollReservationRow.__table__.c.state == "accepted",
                PollReservationRow.__table__.c.sequence <= reservation.sequence,
            )
            .order_by(PollReservationRow.__table__.c.sequence.desc())
            .limit(12)
        ).mappings().all()
        auth_pairs: list[tuple[int, str, str]] = []
        for row in prior_reservations:
            evidence_id = row.get("observation_ref")
            evidence = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.id == evidence_id
                )
            ).mappings().one_or_none()
            if evidence is not None and is_signed_auth_unavailable_observation(
                evidence, self.proof_authority
            ):
                auth_pairs.append((int(row["sequence"]), str(row["reservation_id"]), str(evidence_id)))
        auth_pairs.sort()
        if len(auth_pairs) < 12 or streak != 12:
            return None
        selected = auth_pairs[-12:]
        try:
            event_id = self.id_factory()
        except Exception:
            return None
        if not _valid_id(event_id):
            return None
        unit = graph.unit
        owner_id = graph.owner_id
        task_id = graph.task_id
        amount = 0
        original_debit_ref = None
        if unit.get("billing_status") == "charged":
            debits = session.execute(
                select(CreditLogRow.__table__)
                .where(
                    CreditLogRow.__table__.c.billing_unit_id == unit.get("id"),
                    CreditLogRow.__table__.c.task_id == task_id,
                    CreditLogRow.__table__.c.user_id == owner_id,
                    CreditLogRow.__table__.c.type == "usage",
                    CreditLogRow.__table__.c.amount < 0,
                )
                .order_by(CreditLogRow.__table__.c.created_at, CreditLogRow.__table__.c.id)
            ).mappings().all()
            if len(debits) != 1:
                return None
            original_debit_ref = str(debits[0]["id"])
            amount = -int(debits[0]["amount"])
            disposition = "pending"
        elif unit.get("billing_status") == "no_charge":
            disposition = "no_debit"
        else:
            return None
        return PolicyStopEvent(
            event_id=event_id,
            group_key=reservation.group_key,
            policy_version=HAOAI_ATTEMPT_GROUP.stop_policy_version,
            reason="auth_unavailable_12",
            reservation_refs=tuple(item[1] for item in selected),
            observation_refs=tuple(item[2] for item in selected),
            active_step_id=reservation.step_id,
            active_submission_id=reservation.submission_id,
            external_id=reservation.external_id,
            stopped_at=now,
            financial_disposition=disposition,
            original_debit_ref=original_debit_ref,
            refund_ref=None,
            amount=amount,
            owner_id=owner_id,
            task_id=task_id,
            unit_id=graph.binding.unit_id,
        )

    def _close_reservation_unknown(
        self,
        session: Session,
        graph: LockedHaoaiGroupGraph,
        reservation_row: Mapping[str, object],
        evidence_id: str,
        now: datetime,
    ) -> bool:
        reservation = str(reservation_row["reservation_id"])
        poll_change = session.execute(
            update(PollReservationRow.__table__)
            .where(
                PollReservationRow.__table__.c.reservation_id == reservation,
                PollReservationRow.__table__.c.state == "reserved",
            )
            .values(state="closed_unknown", observation_ref=evidence_id)
        )
        if poll_change.rowcount != 1:
            return False
        group_change = session.execute(
            update(ExecutionAttemptGroupRow.__table__)
            .where(
                ExecutionAttemptGroupRow.__table__.c.unit_id == graph.binding.unit_id,
                ExecutionAttemptGroupRow.__table__.c.group_key == graph.binding.group_key,
                ExecutionAttemptGroupRow.__table__.c.outstanding_reservation_ref
                == reservation,
            )
            .values(outstanding_reservation_ref=None)
        )
        if group_change.rowcount != 1:
            raise RuntimeError("HaoAI expired poll could not release its outstanding fence")
        return True

    def _confirm_reservation(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedHaoaiGroupExecutionInput,
        expected: _ReservationExpectation,
    ) -> bool:
        reservation = expected.reservation
        try:
            with self.session_factory() as session:
                graph = lock_haoai_group_graph(
                    session, claim.scope.user_id, claim.scope.task_id
                )
                now = self._server_now()
                row = session.execute(
                    select(PollReservationRow.__table__).where(
                        PollReservationRow.__table__.c.reservation_id
                        == reservation.reservation_id
                    )
                ).mappings().one_or_none()
                return bool(
                    graph is not None
                    and _claim_live(graph.task, claim, now)
                    and _intent_matches_graph(graph, claim, intent, verified)
                    and row is not None
                    and _reservation_row_matches(row, _grant_shape(
                        reservation, claim, intent, verified,
                        ExternalOperationRef(
                            intent_id=intent.external_submission_id,
                            attempt_id=intent.attempt_id,
                            provider="haoai",
                            external_id=reservation.external_id,
                            create_outcome="submitted",
                        ),
                    ), state="reserved")
                    and graph.group.get("outstanding_reservation_ref")
                    == reservation.reservation_id
                    and graph.group.get("poll_used") == reservation.committed_poll_count
                    and graph.group.get("poll_sequence") == reservation.sequence
                    and reservation.reserved_at == expected.confirmed_at
                )
        except Exception:
            return False

    def _confirm_expired_close(
        self, claim: ClaimAuthority, reservation: PollReservation
    ) -> bool:
        try:
            with self.session_factory() as session:
                graph = lock_haoai_group_graph(
                    session, claim.scope.user_id, claim.scope.task_id
                )
                row = session.execute(
                    select(PollReservationRow.__table__).where(
                        PollReservationRow.__table__.c.reservation_id
                        == reservation.reservation_id
                    )
                ).mappings().one_or_none()
                now = self._server_now()
                return bool(
                    graph is not None
                    and _claim_live(graph.task, claim, now)
                    and row is not None
                    and row.get("unit_id") == graph.binding.unit_id
                    and row.get("group_key") == reservation.group_key
                    and row.get("sequence") == reservation.sequence
                    and row.get("state") == "closed_unknown"
                    and row.get("observation_ref") is None
                    and graph.group.get("outstanding_reservation_ref") is None
                    and graph.group.get("poll_used")
                    == reservation.committed_poll_count
                    and graph.group.get("poll_sequence") == reservation.sequence
                )
        except Exception:
            return False

    def _confirm_observation(
        self,
        grant: HaoaiGroupPollGrant,
        observation: ProviderObservation,
    ) -> HaoaiGroupPollOutcome:
        reservation = grant.reservation.reservation
        try:
            with self.session_factory() as session:
                evidence = session.execute(
                    select(ResultEvidenceRow.__table__).where(
                        ResultEvidenceRow.__table__.c.evidence_key
                        == f"haoai-poll-v1:{reservation.reservation_id}"
                    )
                ).mappings().one_or_none()
                poll = session.get(PollReservationRow, reservation.reservation_id)
                if evidence is None or poll is None:
                    return HaoaiGroupPollOutcome("blocked", observation)
                if poll.state == "accepted":
                    return HaoaiGroupPollOutcome(
                        "accepted", observation, str(evidence["id"])
                    )
                if poll.state == "closed_unknown":
                    return HaoaiGroupPollOutcome(
                        "closed_unknown", observation, str(evidence["id"])
                    )
        except Exception:
            pass
        return HaoaiGroupPollOutcome("blocked", observation)

    def _read_verified(
        self,
        session: Session,
        graph: LockedHaoaiGroupGraph,
        expected: VerifiedHaoaiGroupExecutionInput,
    ) -> bool:
        if (
            type(self.registry) is not CredentialRegistry
            or type(self.crypto) is not CredentialCrypto
            or type(self.proof_authority) is not HmacProofAuthority
        ):
            return False
        try:
            current = self.snapshot_reader(
                session,
                graph.binding,
                registry=self.registry,
                crypto=self.crypto,
                proof_authority=self.proof_authority,
                credential_purpose="query",
            )
        except Exception:
            return False
        return bool(
            type(current) is VerifiedHaoaiGroupExecutionInput
            and current.task_id == expected.task_id
            and current.unit_id == expected.unit_id
            and current.step_id == expected.step_id
            and current.attempt_index == expected.attempt_index
            and current.snapshot == expected.snapshot
            and current.credential_binding == expected.credential_binding
            and current.provider_credential.provider
            == expected.provider_credential.provider
            and current.provider_credential.principal_id
            == expected.provider_credential.principal_id
        )

    def _provider_binding_matches(
        self,
        provider: HaoaiVideoProvider,
        grant: HaoaiGroupPollGrant,
    ) -> bool:
        binding = getattr(provider, "_binding", None)
        return bool(
            binding is not None
            and binding.owner_scope == grant.claim.scope
            and binding.snapshot == grant.verified_input.snapshot
            and binding.intent == grant.intent
            and binding.credential_binding
            == grant.verified_input.credential_binding
        )

    def _transport_unknown(self, grant: HaoaiGroupPollGrant) -> ProviderObservation:
        now = self._server_now()
        body_digest = hashlib.sha256(b"").hexdigest()
        ref = grant.external_ref
        return ProviderObservation(
            owner_scope=grant.claim.scope,
            intent_id=ref.intent_id,
            attempt_id=ref.attempt_id,
            external_ref=ref,
            dedupe_key=hashlib.sha256(
                f"{grant.reservation.reservation.reservation_id}\0{body_digest}".encode()
            ).hexdigest(),
            payload_digest=body_digest,
            received_at=now,
            status="unknown",
            payload={"source_fact": "transport_unavailable"},
        )

    def _query_input_matches(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedHaoaiGroupExecutionInput,
        external_ref: ExternalOperationRef,
    ) -> bool:
        return bool(
            isinstance(claim, ClaimAuthority)
            and claim.scope.operation == "video.single"
            and not claim.local_lost_signal.is_set()
            and type(intent) is CommittedIntent
            and type(verified) is VerifiedHaoaiGroupExecutionInput
            and type(external_ref) is ExternalOperationRef
            and verified.task_id == claim.scope.task_id
            and verified.step_id == intent.step_id == intent.attempt_id
            and intent.provider == external_ref.provider == "haoai"
            and intent.external_submission_id == external_ref.intent_id
            and external_ref.external_id is not None
            and external_ref.create_outcome == "submitted"
            and intent.model_id == verified.snapshot.model_ref.model_id
            and intent.request_digest
        )

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("database clock must return an aware UTC datetime")
        return value.astimezone(timezone.utc)


def _queryable_graph_state(
    graph: LockedHaoaiGroupGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedHaoaiGroupExecutionInput,
    external_ref: ExternalOperationRef,
    now: datetime,
    *,
    require_due: bool = True,
    expected_reservation_id: str | None = None,
) -> Literal["ready", "not_due", "budget_exhausted", "blocked"]:
    group = graph.group
    unit = graph.unit
    step = graph.steps[verified.attempt_index]
    rows = _current_submission_rows(graph)
    if len(rows) != 1:
        return "blocked"
    submission = rows[0]
    if (
        not _claim_live(graph.task, claim, now)
        or not _intent_matches_graph(graph, claim, intent, verified)
        or graph.binding.attempt_index != group.get("active_attempt")
        or group.get("status") != "active"
        or group.get("sticky_policy_stop_event_ref") is not None
        or group.get("outstanding_reservation_ref")
        not in (None, expected_reservation_id)
        or unit.get("user_cancelled_at") is not None
        or unit.get("success_confirmed_at") is not None
        or unit.get("result_status") not in {"pending", "unknown"}
        or unit.get("billing_status") not in {"charged", "no_charge"}
        or step.get("execution_status") not in {"submitted", "reconciling"}
        or submission.get("submission_status") != "submitted"
        or submission.get("external_task_id") != external_ref.external_id
        or submission.get("id") != intent.external_submission_id
        or submission.get("request_digest") != group.get("original_payload_digest")
        or not _time_matches(submission.get("deadline_at"), intent.deadline_at)
        or not _time_matches(unit.get("deadline_at"), intent.deadline_at)
        or _time_matches(unit.get("deadline_at"), None)
        or type(group.get("poll_used")) is not int
        or type(group.get("poll_sequence")) is not int
        or group.get("poll_used") < 0
        or group.get("poll_sequence") < 0
    ):
        return "blocked"
    if group["poll_used"] >= _POLL_BUDGET and (
        group["poll_used"] != _POLL_BUDGET
        or expected_reservation_id is None
        or group.get("outstanding_reservation_ref") != expected_reservation_id
    ):
        return "budget_exhausted"
    deadline = _aware_utc(unit.get("deadline_at"))
    if now >= deadline:
        return "blocked"
    next_poll = group.get("next_poll_at")
    if require_due and next_poll is not None and now < _aware_utc(next_poll):
        return "not_due"
    return "ready"


def _observation_evidence(
    grant: HaoaiGroupPollGrant,
    observation: ProviderObservation,
    authority: HmacProofAuthority | None,
    id_factory: IdFactory,
) -> tuple[str, str, str, str] | None:
    if type(authority) is not HmacProofAuthority or not _observation_matches(
        observation, grant
    ):
        return None
    safe_payload = _safe_observation_payload(observation)
    if safe_payload is None:
        return None
    credential_identity = {
        "source": grant.verified_input.credential_binding.source,
        "row_id": grant.verified_input.credential_binding.row_id,
        "owner_or_system": grant.verified_input.credential_binding.owner_or_system,
        "provider": grant.verified_input.credential_binding.provider,
        "principal_id": grant.verified_input.credential_binding.original_principal_id,
        "binding_version": grant.verified_input.credential_binding.binding_version,
        "rotation_provenance": grant.verified_input.credential_binding.rotation_provenance,
    }
    credential_digest = hashlib.sha256(_canonical_json(credential_identity)).hexdigest()
    reservation = grant.reservation.reservation
    fact: dict[str, object] = {
        "schema": _EVIDENCE_KIND,
        "policy_version": HAOAI_ATTEMPT_GROUP.policy_version,
        "owner_id": grant.claim.scope.user_id,
        "task_id": grant.claim.scope.task_id,
        "unit_id": grant.verified_input.unit_id,
        "group_key": reservation.group_key,
        "registry_version": HAOAI_ATTEMPT_GROUP.registry_version,
        "step_id": reservation.step_id,
        "submission_id": reservation.submission_id,
        "attempt_id": reservation.attempt_id,
        "external_id": reservation.external_id,
        "request_digest": reservation.payload_digest,
        "reservation_id": reservation.reservation_id,
        "sequence": reservation.sequence,
        "reservation_token_digest": hashlib.sha256(
            reservation.token.encode("utf-8")
        ).hexdigest(),
        "reserved_at": _aware_utc(reservation.reserved_at).isoformat(),
        "timeout_at": _aware_utc(reservation.timeout_at).isoformat(),
        "received_at": _aware_utc(observation.received_at).isoformat(),
        "transport": "provider_response",
        "http_status": None,
        "body_kind": safe_payload.get("source_fact"),
        "body_sha256": observation.payload_digest,
        "source_fact": safe_payload.get("source_fact"),
        "source_status": safe_payload.get("source_status"),
        "auth_unavailable_markers": safe_payload.get("auth_unavailable_markers", []),
        "image_download_markers": safe_payload.get("image_download_markers", []),
        "content_status": safe_payload.get("content_status"),
        "content_path_suffix": safe_payload.get("content_path_suffix"),
        "locator_source": safe_payload.get("locator_source"),
        "credential_binding_digest": credential_digest,
    }
    canonical = _canonical_json(fact)
    digest = hashlib.sha256(canonical).hexdigest()
    envelope = {
        "fact": fact,
        "proof": {
            "key_id": authority.key_id,
            "signature": authority.sign(_OBSERVATION_PURPOSE, canonical),
        },
    }
    location = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    try:
        evidence_id = id_factory()
    except Exception:
        return None
    if not _valid_id(evidence_id):
        return None
    return (
        evidence_id,
        f"haoai-poll-v1:{reservation.reservation_id}",
        digest,
        location,
    )


def _safe_observation_payload(
    observation: ProviderObservation,
) -> dict[str, object] | None:
    payload = observation.payload
    if not isinstance(payload, Mapping):
        return None
    if set(payload).difference(
        {
            "source_fact",
            "source_status",
            "auth_unavailable_markers",
            "image_download_markers",
            "content_status",
            "content_path_suffix",
            "locator_source",
        }
    ):
        return None
    source_fact = payload.get("source_fact")
    if source_fact not in _SOURCE_FACTS:
        return None
    sanitized: dict[str, object] = {"source_fact": source_fact}
    source_status = payload.get("source_status")
    if source_status is not None:
        if source_status not in {"completed", "done", "failed"}:
            return None
        sanitized["source_status"] = source_status
    for name, allowed in (
        ("auth_unavailable_markers", _AUTH_MARKERS),
        (
            "image_download_markers",
            frozenset(
                {
                    "image_download",
                    "failed to download the provided image",
                    "downloading the image",
                }
            ),
        ),
    ):
        values = payload.get(name, ())
        if not isinstance(values, (tuple, list)) or any(
            not isinstance(value, str) or value not in allowed for value in values
        ):
            return None
        if values:
            sanitized[name] = list(values)
    for name, allowed in (
        ("content_status", {"bound_content_needed", "unsafe_locator"}),
        ("content_path_suffix", {"/content"}),
        ("locator_source", {"video_url", "video", "url"}),
    ):
        value = payload.get(name)
        if value is not None:
            if value not in allowed:
                return None
            sanitized[name] = value
    return sanitized


def _observation_matches(
    observation: ProviderObservation | None,
    grant: HaoaiGroupPollGrant,
) -> bool:
    if type(observation) is not ProviderObservation:
        return False
    ref = observation.external_ref
    try:
        parsed = datetime.fromisoformat(observation.received_at.isoformat())
        digest_valid = (
            len(observation.payload_digest) == 64
            and all(char in "0123456789abcdef" for char in observation.payload_digest)
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(
        observation.owner_scope == grant.claim.scope
        and observation.intent_id == grant.intent.external_submission_id
        and observation.attempt_id == grant.intent.attempt_id
        and type(ref) is ExternalOperationRef
        and ref.intent_id == grant.external_ref.intent_id
        and ref.attempt_id == grant.external_ref.attempt_id
        and ref.provider == "haoai"
        and ref.external_id == grant.external_ref.external_id
        and ref.create_outcome == "submitted"
        and observation.status in {"completed", "failed", "unknown"}
        and parsed.tzinfo is not None
        and observation.received_at.utcoffset() == timezone.utc.utcoffset(observation.received_at)
        and digest_valid
        and _safe_observation_payload(observation) is not None
    )


def _source_fact(observation: ProviderObservation) -> str | None:
    value = observation.payload.get("source_fact")
    return value if isinstance(value, str) else None


def _auth_markers(observation: ProviderObservation) -> tuple[str, ...]:
    values = observation.payload.get("auth_unavailable_markers", ())
    if not isinstance(values, (tuple, list)):
        return ()
    return tuple(value for value in values if isinstance(value, str) and value in _AUTH_MARKERS)


def _image_download_markers(observation: ProviderObservation) -> tuple[str, ...]:
    values = observation.payload.get("image_download_markers", ())
    allowed = {
        "image_download",
        "failed to download the provided image",
        "downloading the image",
    }
    if not isinstance(values, (tuple, list)):
        return ()
    return tuple(
        value for value in values if isinstance(value, str) and value in allowed
    )


def _reservation_row_matches(
    row: Mapping[str, object],
    grant: HaoaiGroupPollGrant,
    *,
    state: str | None = None,
) -> bool:
    reservation = grant.reservation.reservation
    expected = {
        "reservation_id": reservation.reservation_id,
        "unit_id": grant.verified_input.unit_id,
        "group_key": reservation.group_key,
        "sequence": reservation.sequence,
        "attempt_id": reservation.attempt_id,
        "step_id": reservation.step_id,
        "submission_id": reservation.submission_id,
        "external_id": reservation.external_id,
        "payload_digest": reservation.payload_digest,
        "committed_poll_count": reservation.committed_poll_count,
        "token": reservation.token,
    }
    return all(row.get(key) == value for key, value in expected.items()) and (
        state is None or row.get("state") == state
    )


def _outstanding_reservation_matches(
    graph: LockedHaoaiGroupGraph,
    row: Mapping[str, object],
) -> bool:
    attempt_ids = graph.group.get("attempt_step_ids")
    if (
        not isinstance(attempt_ids, list)
        or graph.group.get("outstanding_reservation_ref")
        != row.get("reservation_id")
        or row.get("unit_id") != graph.binding.unit_id
        or row.get("group_key") != graph.binding.group_key
        or type(row.get("sequence")) is not int
        or row.get("sequence") != graph.group.get("poll_sequence")
        or row.get("committed_poll_count") != graph.group.get("poll_used")
        or row.get("payload_digest") != graph.group.get("original_payload_digest")
        or type(graph.group.get("active_attempt")) is not int
        or graph.group.get("active_attempt") != graph.binding.attempt_index
        or not 0 <= graph.binding.attempt_index < len(attempt_ids)
        or row.get("step_id") != attempt_ids[graph.binding.attempt_index]
        or row.get("attempt_id") != row.get("step_id")
        or graph.group.get("sticky_policy_stop_event_ref") is not None
    ):
        return False
    submissions = _current_submission_rows(graph)
    return bool(
        len(submissions) == 1
        and submissions[0].get("id") == row.get("submission_id")
        and submissions[0].get("submission_status") == "submitted"
        and submissions[0].get("external_task_id") == row.get("external_id")
        and submissions[0].get("request_digest") == row.get("payload_digest")
    )


def _poll_record_from_row(row: Mapping[str, object]) -> PollReservation:
    return PollReservation(
        reservation_id=str(row["reservation_id"]),
        group_key=str(row["group_key"]),
        sequence=int(row["sequence"]),
        attempt_id=str(row["attempt_id"]),
        step_id=str(row["step_id"]),
        submission_id=str(row["submission_id"]),
        external_id=str(row["external_id"]),
        payload_digest=str(row["payload_digest"]),
        committed_poll_count=int(row["committed_poll_count"]),
        reserved_at=_aware_utc(row["reserved_at"]),
        timeout_at=_aware_utc(row["timeout_at"]),
        next_poll_at=_aware_utc(row["next_poll_at"]),
        token=str(row["token"]),
        state=str(row["state"]),
        observation_ref=row.get("observation_ref")
        if isinstance(row.get("observation_ref"), str)
        else None,
    )


def _graph_matches_grant(
    graph: LockedHaoaiGroupGraph, grant: HaoaiGroupPollGrant
) -> bool:
    return bool(
        graph.owner_id == grant.claim.scope.user_id
        and graph.task_id == grant.claim.scope.task_id
        and graph.binding.unit_id == grant.verified_input.unit_id
        and graph.binding.step_id == grant.intent.step_id
        and graph.binding.attempt_index == grant.verified_input.attempt_index
        and graph.group.get("group_key") == grant.reservation.reservation.group_key
    )


def _grant_shape(
    reservation: PollReservation,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedHaoaiGroupExecutionInput,
    external_ref: ExternalOperationRef,
) -> HaoaiGroupPollGrant:
    return HaoaiGroupPollGrant(
        reservation=CommittedPollReservation(reservation, reservation.reserved_at),
        claim=claim,
        intent=intent,
        verified_input=verified,
        external_ref=external_ref,
        _nonce="confirmation-only",
    )


def _evidence_row_matches(
    row: Mapping[str, object],
    grant: HaoaiGroupPollGrant,
    payload_digest: str,
    result_location: str,
) -> bool:
    reservation = grant.reservation.reservation
    return all(
        (
            row.get("step_id") == reservation.step_id,
            row.get("submission_id") == reservation.submission_id,
            row.get("evidence_key") == f"haoai-poll-v1:{reservation.reservation_id}",
            row.get("source") == "worker",
            row.get("evidence_kind") == _EVIDENCE_KIND,
            row.get("payload_digest") == payload_digest,
            row.get("result_location") == result_location,
            row.get("validity") == "unknown",
            row.get("application_status") == "pending",
            row.get("applied_at") is None,
        )
    )


def observation_body_digest(result_location: str) -> str:
    try:
        envelope = json.loads(result_location)
        digest = envelope["fact"]["body_sha256"]
        if isinstance(digest, str) and len(digest) == 64:
            return digest
    except (KeyError, TypeError, ValueError, UnicodeError):
        pass
    return hashlib.sha256(b"").hexdigest()


def _observed_at(result_location: str) -> datetime:
    try:
        value = json.loads(result_location)["fact"]["received_at"]
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, UnicodeError):
        pass
    return datetime.now(timezone.utc)


def _time_matches(value: object, expected: datetime | None) -> bool:
    if expected is None:
        return value is None
    if not isinstance(value, datetime):
        return False
    try:
        return _aware_utc(value) == _aware_utc(expected)
    except ValueError:
        return False


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("persisted timestamp is unavailable")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("persisted timestamp is not UTC")
    return value.astimezone(timezone.utc)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _valid_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 36
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


__all__ = (
    "HaoaiGroupPollGrant",
    "HaoaiGroupPollOutcome",
    "SqlAlchemyHaoaiGroupPolls",
)
