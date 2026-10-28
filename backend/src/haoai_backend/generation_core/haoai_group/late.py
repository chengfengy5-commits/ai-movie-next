"""Controlled, owner-readable late results for terminal HaoAI video Groups."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import PollReservation
from haoai_backend.generation_contracts.types import (
    CommittedIntent,
    ControlledRecoveryScope,
    ExternalOperationRef,
    OwnerTaskScope,
    ProviderObservation,
)
from haoai_backend.generation_core.base_schema import ResultEvidenceRow
from haoai_backend.generation_core.haoai_group.evidence import (
    verified_group_observation_fact,
)
from haoai_backend.generation_core.haoai_group.intents import (
    _intent_from_existing_submission,
    _safe_intent_snapshot,
    _safe_provider_id,
    is_valid_started_group_submission,
)
from haoai_backend.generation_core.haoai_group.persistence import (
    LockedHaoaiGroupGraph,
    lock_haoai_group_graph,
)
from haoai_backend.generation_core.haoai_group.polls import (
    HaoaiGroupPollOutcome,
    _poll_record_from_row,
    observation_body_digest,
)
from haoai_backend.generation_core.haoai_group.results import (
    _has_group_work,
    _existing_due_matches,
)
from haoai_backend.generation_core.haoai_group.settlement import (
    read_verified_group_fee,
    verify_policy_stop_event,
)
from haoai_backend.generation_core.schema import (
    GenerationResultReceiptRow,
    PolicyStopEventRow,
    PollReservationRow,
)
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
)
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.haoai_group_execution_snapshot import (
    VerifiedHaoaiGroupExecutionInput,
    read_original_haoai_group_execution_input,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP


_DUE_EVIDENCE_KEY = "haoai-group-due-v1:"
_CANCEL_MARKER = re.compile(r"^user_cancelled:v1:[0-9a-f]{32}$")
_POLL_EVIDENCE_KEY = "haoai-poll-v1:"
_POLL_EVIDENCE_KIND = "haoai_poll_observation_v1"


@dataclass(frozen=True, slots=True)
class HaoaiGroupLateCandidate:
    """Authenticated original result facts; not a query or write capability."""

    controlled_scope: ControlledRecoveryScope
    verified_input: VerifiedHaoaiGroupExecutionInput
    intent: CommittedIntent
    external_ref: ExternalOperationRef
    reservation: PollReservation
    poll_outcome: HaoaiGroupPollOutcome
    observation_fact: Mapping[str, object]
    financial_disposition: str


@dataclass(slots=True)
class SqlAlchemyHaoaiGroupLateRecovery:
    """Recover only the original content of a financially terminal Group."""

    session_factory: Callable[[], Session]
    db_clock: Callable[[], datetime]
    registry: CredentialRegistry
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    provider_factory: Callable[[HaoaiGroupLateCandidate, object], object]
    media_store_factory: Callable[[], object]
    id_factory: Callable[[], str]

    def __post_init__(self) -> None:
        if (
            not callable(self.session_factory)
            or not callable(self.db_clock)
            or type(self.registry) is not CredentialRegistry
            or type(self.crypto) is not CredentialCrypto
            or type(self.proof_authority) is not HmacProofAuthority
            or not callable(self.provider_factory)
            or not callable(self.media_store_factory)
            or not callable(self.id_factory)
        ):
            raise TypeError("Group late recovery dependencies must be explicit")

    def recover(self, scope: OwnerTaskScope) -> str:
        from .media import SqlAlchemyHaoaiGroupMedia
        from .results import SqlAlchemyHaoaiGroupResults

        candidate = self._read_candidate(scope)
        if candidate is None:
            return "pending"
        guard = _LateRecoveryGuard(self, candidate)
        try:
            provider = self.provider_factory(candidate, guard)
            store = self.media_store_factory()
        except Exception:
            return "pending"
        content_provider = _LateContentOnlyProvider(provider, guard, candidate)
        results = SqlAlchemyHaoaiGroupResults(
            self.session_factory,
            self.db_clock,
            self.proof_authority,
            self.id_factory,
        )
        media = SqlAlchemyHaoaiGroupMedia(
            self.session_factory,
            self.db_clock,
            self.proof_authority,
            results,
            self.id_factory,
        )
        outcome = media.materialize_controlled_late(
            candidate,
            content_provider,
            store,
            registry=self.registry,
            crypto=self.crypto,
            candidate_reader=self._read_candidate_in_session,
            authorize=guard.is_live,
        )
        return "projected" if outcome.disposition == "completed" else "pending"

    def _read_candidate(self, scope: OwnerTaskScope) -> HaoaiGroupLateCandidate | None:
        try:
            with self.session_factory() as session:
                with session.begin():
                    return self._read_candidate_in_session(session, scope)
        except Exception:
            return None

    def _read_candidate_in_session(
        self,
        session: Session,
        scope: OwnerTaskScope,
    ) -> HaoaiGroupLateCandidate | None:
        return read_verified_late_candidate(
            session,
            scope,
            registry=self.registry,
            crypto=self.crypto,
            proof_authority=self.proof_authority,
        )

    def _still_current(self, expected: HaoaiGroupLateCandidate) -> bool:
        current = self._read_candidate(expected.controlled_scope.owner_scope)
        return same_late_candidate(expected, current)


class _LateRecoveryGuard:
    """Provider I/O guard that rechecks terminal Group facts, never a normal lease."""

    def __init__(
        self,
        recovery: SqlAlchemyHaoaiGroupLateRecovery,
        candidate: HaoaiGroupLateCandidate,
    ) -> None:
        self._recovery = recovery
        self._candidate = candidate

    def require_live(self) -> None:
        if not self._recovery._still_current(self._candidate):
            raise RuntimeError("controlled late recovery scope is no longer valid")

    def is_live(self) -> bool:
        try:
            self.require_live()
            return True
        except Exception:
            return False

    def mark_provider_request_started(self) -> None:
        self.require_live()


class _LateContentOnlyProvider:
    """Expose only original bound-content GET to the late media consumer."""

    def __init__(
        self,
        provider: object,
        guard: _LateRecoveryGuard,
        candidate: HaoaiGroupLateCandidate,
    ) -> None:
        fetch = getattr(provider, "fetch_provider_content", None)
        if not callable(fetch):
            raise TypeError("late recovery requires the original bound-content adapter")
        self._fetch = fetch
        self._guard = guard
        self._candidate = candidate

    def fetch_provider_content(self, snapshot: object, external_ref: object):
        self._guard.require_live()
        if (
            snapshot != self._candidate.verified_input.snapshot
            or external_ref != self._candidate.external_ref
        ):
            raise ValueError("late recovery content binding changed")
        result = self._fetch(snapshot, external_ref)
        self._guard.require_live()
        return result


def read_verified_late_candidate(
    session: Session,
    scope: OwnerTaskScope,
    *,
    registry: CredentialRegistry,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> HaoaiGroupLateCandidate | None:
    """Reopen a terminal Group and verify its original completion and fee facts."""

    if (
        not isinstance(session, Session)
        or type(scope) is not OwnerTaskScope
        or scope.operation != "video.single"
        or type(registry) is not CredentialRegistry
        or type(crypto) is not CredentialCrypto
        or type(proof_authority) is not HmacProofAuthority
    ):
        return None
    graph = lock_haoai_group_graph(session, scope.user_id, scope.task_id)
    if graph is None:
        return None

    financial_disposition = _verified_terminal_financial_disposition(
        session, graph, proof_authority
    )
    if financial_disposition is None:
        return None
    if (
        graph.group.get("selected_evidence_id") is not None
        or graph.group.get("outstanding_reservation_ref") is not None
        or graph.task.get("status") != "failed"
        or graph.unit.get("result_status") != "unknown"
        or graph.unit.get("success_confirmed_at") is not None
        or graph.binding.attempt_index != graph.group.get("active_attempt")
        or _has_group_work(session, graph)
    ):
        return None

    active_step = graph.steps[graph.binding.attempt_index]
    submissions = tuple(
        row
        for row in graph.external_submissions
        if row.get("step_id") == active_step.get("id")
    )
    if len(submissions) != 1:
        return None
    submission = submissions[0]
    external_id = submission.get("external_task_id")
    if (
        submission.get("submission_status") != "submitted"
        or not _safe_provider_id(external_id)
    ):
        return None

    verified_input = read_original_haoai_group_execution_input(
        session,
        graph.binding,
        registry=registry,
        crypto=crypto,
        proof_authority=proof_authority,
        credential_purpose="query",
    )
    if verified_input is None:
        return None
    intent = _intent_from_existing_submission(graph, submission, verified_input)
    if (
        intent is None
        or intent.step_id != graph.binding.step_id
        or intent.request_digest != graph.binding.request_digest
        or submission.get("request_snapshot")
        != _safe_intent_snapshot(graph, verified_input)
    ):
        return None
    result_receipts = session.execute(
        select(GenerationResultReceiptRow.__table__).where(
            GenerationResultReceiptRow.__table__.c.unit_id == graph.binding.unit_id
        )
    ).mappings().all()
    if len(result_receipts) > 1 or any(
        receipt.get("submission_id") != intent.external_submission_id
        or receipt.get("step_id") != intent.step_id
        or receipt.get("late_disposition") != "stopped_late_generation"
        or receipt.get("automatic_application_allowed") is not False
        for receipt in result_receipts
    ):
        return None

    external_ref = ExternalOperationRef(
        intent_id=intent.external_submission_id,
        attempt_id=intent.attempt_id,
        provider="haoai",
        external_id=str(external_id),
        create_outcome="submitted",
    )
    poll_rows = session.execute(
        select(PollReservationRow.__table__)
        .where(
            PollReservationRow.__table__.c.unit_id == graph.binding.unit_id,
            PollReservationRow.__table__.c.group_key == graph.binding.group_key,
            PollReservationRow.__table__.c.step_id == intent.step_id,
            PollReservationRow.__table__.c.submission_id
            == intent.external_submission_id,
            PollReservationRow.__table__.c.external_id == external_id,
            PollReservationRow.__table__.c.state.in_(("accepted", "closed_unknown")),
            PollReservationRow.__table__.c.observation_ref.is_not(None),
        )
        .order_by(PollReservationRow.__table__.c.sequence.desc())
        .with_for_update()
    ).mappings().all()
    completed: list[tuple[PollReservation, dict[str, object], str]] = []
    for row in poll_rows:
        reservation = _poll_record_from_row(row)
        evidence = session.execute(
            select(ResultEvidenceRow.__table__).where(
                ResultEvidenceRow.__table__.c.id == row.get("observation_ref"),
                ResultEvidenceRow.__table__.c.evidence_key
                == _POLL_EVIDENCE_KEY + reservation.reservation_id,
                ResultEvidenceRow.__table__.c.step_id == intent.step_id,
                ResultEvidenceRow.__table__.c.submission_id
                == intent.external_submission_id,
            )
        ).mappings().one_or_none()
        if evidence is None:
            return None
        fact = verified_group_observation_fact(evidence, proof_authority)
        if not isinstance(fact, Mapping):
            return None
        if fact.get("source_fact") != "source_completed":
            continue
        if not _valid_completed_poll(
            session,
            evidence,
            row,
            reservation,
            fact,
            graph,
            intent,
            external_id=str(external_id),
            proof_authority=proof_authority,
        ):
            return None
        completed.append((reservation, dict(fact), str(evidence["id"])))

    if not completed:
        return None
    reservation, fact, observation_id = completed[0]
    observation = _late_provider_observation(
        graph, intent, verified_input, external_ref, reservation, fact
    )
    if observation is None:
        return None
    outcome = HaoaiGroupPollOutcome(
        reservation.state,
        observation,
        observation_id,
    )
    return HaoaiGroupLateCandidate(
        controlled_scope=ControlledRecoveryScope(
            owner_scope=scope,
            group_key=graph.binding.group_key,
            registry_version=HAOAI_ATTEMPT_GROUP.registry_version,
        ),
        verified_input=verified_input,
        intent=intent,
        external_ref=external_ref,
        reservation=reservation,
        poll_outcome=outcome,
        observation_fact=fact,
        financial_disposition=financial_disposition,
    )


def same_late_candidate(
    expected: HaoaiGroupLateCandidate,
    current: HaoaiGroupLateCandidate | None,
) -> bool:
    """Compare persisted authority facts while keeping credentials non-comparable."""

    return bool(
        type(expected) is HaoaiGroupLateCandidate
        and type(current) is HaoaiGroupLateCandidate
        and current.controlled_scope == expected.controlled_scope
        and current.intent == expected.intent
        and current.external_ref == expected.external_ref
        and current.reservation == expected.reservation
        and current.poll_outcome == expected.poll_outcome
        and current.observation_fact == expected.observation_fact
        and current.financial_disposition == expected.financial_disposition
        and current.verified_input.task_id == expected.verified_input.task_id
        and current.verified_input.unit_id == expected.verified_input.unit_id
        and current.verified_input.step_id == expected.verified_input.step_id
        and current.verified_input.attempt_index
        == expected.verified_input.attempt_index
        and current.verified_input.prepared_payload_id
        == expected.verified_input.prepared_payload_id
        and current.verified_input.server_attempt_id
        == expected.verified_input.server_attempt_id
        and current.verified_input.snapshot == expected.verified_input.snapshot
        and current.verified_input.credential_binding
        == expected.verified_input.credential_binding
    )


def _verified_terminal_financial_disposition(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    proof_authority: HmacProofAuthority,
) -> str | None:
    events = session.execute(
        select(PolicyStopEventRow.__table__)
        .where(PolicyStopEventRow.__table__.c.unit_id == graph.binding.unit_id)
        .order_by(PolicyStopEventRow.__table__.c.stopped_at, PolicyStopEventRow.__table__.c.event_id)
        .with_for_update()
    ).mappings().all()
    due_key = _DUE_EVIDENCE_KEY + graph.binding.unit_id
    due = session.execute(
        select(ResultEvidenceRow.__table__)
        .where(ResultEvidenceRow.__table__.c.evidence_key == due_key)
        .with_for_update()
    ).mappings().one_or_none()
    if due is not None:
        if len(events) > 1 or not _existing_due_matches(
            session, graph, list(events), due, proof_authority
        ):
            return None
        return "refunded" if graph.unit.get("billing_status") == "refunded" else "no_debit"

    if events:
        if (
            len(events) != 1
            or graph.task.get("status") != "failed"
            or graph.group.get("status") != "policy_stopped"
            or graph.group.get("sticky_policy_stop_event_ref")
            != events[0].get("event_id")
            or events[0].get("financial_disposition")
            not in {"refunded", "no_debit", "retained_by_started_cancel"}
            or not verify_policy_stop_event(
                session,
                graph,
                events[0],
                proof_authority=proof_authority,
                verified_quote=graph.binding.quoted_amount,
            )
        ):
            return None
        return str(events[0]["financial_disposition"])

    task_cancelled = _as_utc(graph.task.get("user_cancelled_at"))
    unit_cancelled = _as_utc(graph.unit.get("user_cancelled_at"))
    marker = graph.task.get("cancellation_reason")
    if (
        task_cancelled is None
        or unit_cancelled != task_cancelled
        or not isinstance(marker, str)
        or _CANCEL_MARKER.fullmatch(marker) is None
        or graph.unit.get("cancellation_reason") != marker
        or graph.task.get("status") != "failed"
        or graph.group.get("status") != "cancelled"
        or graph.group.get("terminal_reason") != "user_cancelled"
        or graph.group.get("sticky_policy_stop_event_ref") is not None
        or graph.group.get("outstanding_reservation_ref") is not None
    ):
        return None
    fee = read_verified_group_fee(session, graph, graph.binding.quoted_amount)
    if fee is None:
        return None
    first_intent = _as_utc(graph.unit.get("first_intent_at"))
    deadline = _as_utc(graph.unit.get("deadline_at"))
    submissions = tuple(
        row
        for row in graph.external_submissions
        if row.get("step_id") == graph.binding.step_id
    )
    if (
        first_intent is None
        or deadline is None
        or not first_intent < task_cancelled < deadline
        or len(submissions) != 1
        or not is_valid_started_group_submission(
            graph, submissions[0], task_cancelled
        )
    ):
        return None
    if fee.state == "charged":
        return "retained_by_started_cancel"
    if fee.state == "no_charge" and graph.binding.quoted_amount == 0:
        return "no_debit"
    return None


def _valid_completed_poll(
    session: Session,
    evidence: Mapping[str, object],
    poll_row: Mapping[str, object],
    reservation: PollReservation,
    fact: Mapping[str, object],
    graph: LockedHaoaiGroupGraph,
    intent: CommittedIntent,
    *,
    external_id: str,
    proof_authority: HmacProofAuthority,
) -> bool:
    if (
        evidence.get("source") != "worker"
        or evidence.get("evidence_kind") != _POLL_EVIDENCE_KIND
        or evidence.get("evidence_key")
        != _POLL_EVIDENCE_KEY + reservation.reservation_id
        or evidence.get("validity") != "unknown"
        or evidence.get("application_status") != "pending"
        or evidence.get("applied_at") is not None
        or not isinstance(evidence.get("result_location"), str)
    ):
        return False
    if (
        fact.get("schema") != "haoai_poll_observation_v1"
        or fact.get("policy_version") != HAOAI_ATTEMPT_GROUP.policy_version
        or fact.get("registry_version") != HAOAI_ATTEMPT_GROUP.registry_version
        or fact.get("source_fact") != "source_completed"
        or fact.get("source_status") not in {"completed", "done"}
        or fact.get("owner_id") != graph.owner_id
        or fact.get("task_id") != graph.task_id
        or fact.get("unit_id") != graph.binding.unit_id
        or fact.get("group_key") != graph.binding.group_key
        or fact.get("step_id") != intent.step_id
        or fact.get("submission_id") != intent.external_submission_id
        or fact.get("attempt_id") != intent.attempt_id
        or fact.get("external_id") != external_id
        or fact.get("request_digest") != intent.request_digest
        or fact.get("reservation_id") != reservation.reservation_id
        or fact.get("sequence") != reservation.sequence
        or fact.get("reservation_token_digest")
        != hashlib.sha256(reservation.token.encode("utf-8")).hexdigest()
        or not isinstance(fact.get("body_sha256"), str)
        or len(str(fact.get("body_sha256"))) != 64
        or poll_row.get("unit_id") != graph.binding.unit_id
        or poll_row.get("group_key") != graph.binding.group_key
        or poll_row.get("step_id") != intent.step_id
        or poll_row.get("submission_id") != intent.external_submission_id
        or poll_row.get("external_id") != external_id
        or poll_row.get("attempt_id") != intent.attempt_id
        or poll_row.get("payload_digest") != intent.request_digest
        or poll_row.get("observation_ref") != evidence.get("id")
        or reservation.sequence > graph.group.get("poll_sequence", -1)
        or reservation.committed_poll_count > graph.group.get("poll_used", -1)
        or evidence.get("step_id") != intent.step_id
        or evidence.get("submission_id") != intent.external_submission_id
        or evidence.get("payload_digest")
        != hashlib.sha256(_canonical_json(fact)).hexdigest()
        or evidence.get("result_digest")
        != observation_body_digest(str(evidence.get("result_location", "")))
    ):
        return False

    reserved_at = _as_utc(poll_row.get("reserved_at"))
    timeout_at = _as_utc(poll_row.get("timeout_at"))
    received_at = _parse_iso(fact.get("received_at"))
    deadline = _as_utc(intent.deadline_at)
    first_intent = _as_utc(intent.first_intent_at)
    evidence_received_at = _as_utc(evidence.get("received_at"))
    if (
        reserved_at is None
        or timeout_at is None
        or received_at is None
        or deadline is None
        or first_intent is None
        or evidence_received_at != received_at
        or reserved_at != _parse_iso(fact.get("reserved_at"))
        or timeout_at != _parse_iso(fact.get("timeout_at"))
        or not first_intent <= reserved_at < deadline
        or received_at < reserved_at
    ):
        return False

    if poll_row.get("state") == "accepted":
        return bool(
            reservation.state == "accepted"
            and poll_row.get("observation_ref") == evidence.get("id")
            and received_at < timeout_at
            and received_at < deadline
        )

    if poll_row.get("state") != "closed_unknown":
        return False
    return True


def _late_provider_observation(
    graph: LockedHaoaiGroupGraph,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    external_ref: ExternalOperationRef,
    reservation: PollReservation,
    fact: Mapping[str, object],
) -> ProviderObservation | None:
    received_at = _parse_iso(fact.get("received_at"))
    body_digest = fact.get("body_sha256")
    if (
        received_at is None
        or not isinstance(body_digest, str)
        or len(body_digest) != 64
    ):
        return None
    payload: dict[str, object] = {
        "source_fact": "source_completed",
        "source_status": fact.get("source_status"),
    }
    for key, allowed in (
        ("content_status", {"bound_content_needed", "unsafe_locator"}),
        ("content_path_suffix", {"/content"}),
        ("locator_source", {"video_url", "video", "url"}),
    ):
        value = fact.get(key)
        if value is not None:
            if value not in allowed:
                return None
            payload[key] = value
    try:
        return ProviderObservation(
            owner_scope=OwnerTaskScope(
                user_id=graph.owner_id,
                task_id=graph.task_id,
                operation="video.single",
            ),
            intent_id=intent.external_submission_id,
            attempt_id=intent.attempt_id,
            external_ref=external_ref,
            dedupe_key=hashlib.sha256(
                f"{reservation.reservation_id}\0{body_digest}".encode("utf-8")
            ).hexdigest(),
            payload_digest=body_digest,
            received_at=received_at,
            status="completed",
            payload=payload,
        )
    except (TypeError, ValueError):
        return None


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return _as_utc(parsed)


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    try:
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return normalized.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


__all__ = (
    "HaoaiGroupLateCandidate",
    "SqlAlchemyHaoaiGroupLateRecovery",
    "read_verified_late_candidate",
    "same_late_candidate",
)
