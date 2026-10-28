"""Owner-scoped policy-event proof and finance transitions for HaoAI Groups."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from sqlalchemy import and_, insert, or_, select, text, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import PolicyStopEvent
from haoai_backend.generation_credentials.domain import HmacProofAuthority
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    TaskQuoteRow,
    UserCreditRow,
)
from haoai_backend.generation_core.haoai_group.evidence import (
    verified_group_observation_fact,
)
from haoai_backend.generation_core.haoai_group.persistence import (
    LockedHaoaiGroupGraph,
    lock_haoai_group_graph,
)
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    PolicyStopEventRow,
    PollReservationRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class VerifiedHaoaiGroupFee:
    amount: int
    state: Literal["no_charge", "charged", "refunded"]
    original_debit_ref: str | None
    refund_ref: str | None


_AUTH_MARKERS = frozenset(
    {
        "auth_unavailable",
        "no auth available",
        "no available auth candidates",
        "all candidates are unavailable",
    }
)
_OBSERVATION_PURPOSE = "haoai-group-poll-observation-v1"
_USER_CANCEL_MARKER = re.compile(r"^user_cancelled:v1:[0-9a-f]{32}$")
_POLL_TIMEOUT_SECONDS = 30
_POLICY_STOP_RESULT = (
    '{"code":"auth_unavailable_12","message":"视频任务因认证策略停止，原任务结果状态未知",'
    '"provider_status":"unknown","status":"failed"}'
)
_TASK_CANCEL_RESULT = (
    '{"code":"user_cancelled","message":"视频任务已取消","status":"failed"}'
)
_CANCEL_MARKER = re.compile(r"^user_cancelled:v1:[0-9a-f]{32}$")
_RECONCILING_RESULT = (
    '{"message":"视频任务策略处理中","status":"reconciling"}'
)


def establish_pending_policy_stop_event(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    event: PolicyStopEvent,
    *,
    proof_authority: HmacProofAuthority,
    verified_quote: int,
) -> bool:
    """Insert a Group event only after its ordered evidence and actual fee are proven.

    This is deliberately separate from ``GenerationRecordRepository.stage_record``:
    that API accepts immutable records and cannot establish a zero-debit pending
    event based on a mutable caller-supplied disposition.
    """

    if (
        not isinstance(session, Session)
        or type(event) is not PolicyStopEvent
        or type(proof_authority) is not HmacProofAuthority
        or type(verified_quote) is not int
        or event.financial_disposition != "pending"
        or event.refund_ref is not None
        or not _event_scope_matches_graph(event, graph, require_committed=False)
        or event.amount != verified_quote
        or not _valid_event_observations(session, graph, event, proof_authority)
        or not _valid_original_fee(
            session, graph, event, verified_quote, disposition="pending"
        )
    ):
        return False

    event_table = PolicyStopEventRow.__table__
    existing = session.execute(
        select(event_table.c.event_id).where(
            event_table.c.unit_id == event.unit_id,
            event_table.c.group_key == event.group_key,
            event_table.c.policy_version == event.policy_version,
            event_table.c.reason == event.reason,
        ).with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        return False

    session.execute(
        insert(event_table).values(
            event_id=event.event_id,
            unit_id=event.unit_id,
            group_key=event.group_key,
            policy_version=event.policy_version,
            reason=event.reason,
            reservation_refs=list(event.reservation_refs),
            observation_refs=list(event.observation_refs),
            active_step_id=event.active_step_id,
            active_submission_id=event.active_submission_id,
            external_id=event.external_id,
            stopped_at=_database_datetime(event.stopped_at),
            financial_disposition="pending",
            original_debit_ref=event.original_debit_ref,
            refund_ref=None,
            amount=event.amount,
            owner_id=event.owner_id,
            task_id=event.task_id,
        )
    )
    session.flush()
    return True


def verify_pending_policy_stop_event(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    event: Mapping[str, object],
    *,
    proof_authority: HmacProofAuthority,
    verified_quote: int,
) -> bool:
    """Revalidate the complete immutable event and its original fee before settlement."""

    record = _policy_stop_record(event)
    if record is None:
        return False
    return bool(
        record.financial_disposition == "pending"
        and record.refund_ref is None
        and _event_scope_matches_graph(record, graph, require_committed=True)
        and type(verified_quote) is int
        and record.amount == verified_quote
        and _valid_event_observations(session, graph, record, proof_authority)
        and _valid_original_fee(
            session, graph, record, verified_quote, disposition="pending"
        )
    )


def verify_policy_stop_event(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    event: Mapping[str, object],
    *,
    proof_authority: HmacProofAuthority,
    verified_quote: int,
) -> bool:
    """Verify either a pending event or its immutable financial terminal proof."""

    record = _policy_stop_record(event)
    if (
        record is None
        or not _valid_event_observations(session, graph, record, proof_authority)
    ):
        return False
    if record.financial_disposition == "pending":
        return bool(
            record.refund_ref is None
            and _event_scope_matches_graph(record, graph, require_committed=True)
            and type(verified_quote) is int
            and record.amount == verified_quote
            and _valid_original_fee(
                session, graph, record, verified_quote, disposition="pending"
            )
        )
    return _verify_terminal_event(
        session, graph, record, verified_quote=verified_quote
    )


def settle_pending_policy_stop_event(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    event: Mapping[str, object],
    *,
    proof_authority: HmacProofAuthority,
    verified_quote: int,
    now: datetime,
    id_factory,
) -> str | None:
    """Settle or replay one locked event without trusting mutable Task status."""

    if not isinstance(session, Session) or not callable(id_factory):
        return None
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return None
    event_table = PolicyStopEventRow.__table__
    locked_row = session.execute(
        select(event_table)
        .where(event_table.c.event_id == event_id)
        .with_for_update()
    ).mappings().one_or_none()
    if locked_row is None:
        return None
    record = _policy_stop_record(locked_row)
    if record is None or not _valid_event_observations(
        session, graph, record, proof_authority
    ):
        return None

    if record.financial_disposition != "pending":
        if not _verify_terminal_event(
            session, graph, record, verified_quote=verified_quote
        ):
            return None
        return record.financial_disposition

    if (
        not _event_scope_matches_graph(record, graph, require_committed=True)
        or type(verified_quote) is not int
        or record.amount != verified_quote
    ):
        return None
    if verified_quote == 0:
        if not _valid_original_fee(
            session, graph, record, verified_quote, disposition="pending"
        ):
            return None
        changed = session.execute(
            update(event_table)
            .where(
                event_table.c.event_id == event_id,
                event_table.c.financial_disposition == "pending",
                event_table.c.amount == 0,
                event_table.c.original_debit_ref.is_(None),
                event_table.c.refund_ref.is_(None),
            )
            .values(financial_disposition="no_debit")
        )
        return "no_debit" if changed.rowcount == 1 else None

    wallet_table = UserCreditRow.__table__
    wallet = session.execute(
        select(wallet_table)
        .where(wallet_table.c.user_id == graph.owner_id)
        .with_for_update()
    ).mappings().one_or_none()
    if (
        wallet is None
        or type(wallet.get("credits")) is not int
        or not _valid_original_fee(
            session, graph, record, verified_quote, disposition="pending"
        )
    ):
        return None
    return _apply_exact_refund(
        session,
        graph,
        record,
        wallet=wallet,
        now=now,
        id_factory=id_factory,
    )


def _policy_stop_record(event: Mapping[str, object]) -> PolicyStopEvent | None:
    try:
        required_strings = (
            "event_id",
            "group_key",
            "policy_version",
            "active_step_id",
            "active_submission_id",
            "external_id",
            "owner_id",
            "task_id",
            "unit_id",
        )
        if any(not isinstance(event.get(name), str) or not event.get(name) for name in required_strings):
            return None
        reservations = event.get("reservation_refs")
        observations = event.get("observation_refs")
        if (
            not isinstance(reservations, (list, tuple))
            or not isinstance(observations, (list, tuple))
            or any(not isinstance(item, str) or not item for item in reservations)
            or any(not isinstance(item, str) or not item for item in observations)
        ):
            return None
        disposition = event.get("financial_disposition")
        if disposition not in {"pending", "refunded", "retained_by_started_cancel", "no_debit"}:
            return None
        debit_ref = event.get("original_debit_ref")
        refund_ref = event.get("refund_ref")
        if debit_ref is not None and not isinstance(debit_ref, str):
            return None
        if refund_ref is not None and not isinstance(refund_ref, str):
            return None
        amount = event.get("amount")
        if type(amount) is not int or amount < 0:
            return None
        if event.get("reason") != "auth_unavailable_12":
            return None
        return PolicyStopEvent(
            event_id=event["event_id"],  # type: ignore[arg-type]
            group_key=event["group_key"],  # type: ignore[arg-type]
            policy_version=event["policy_version"],  # type: ignore[arg-type]
            reason="auth_unavailable_12",
            reservation_refs=tuple(reservations),
            observation_refs=tuple(observations),
            active_step_id=event["active_step_id"],  # type: ignore[arg-type]
            active_submission_id=event["active_submission_id"],  # type: ignore[arg-type]
            external_id=event["external_id"],  # type: ignore[arg-type]
            stopped_at=_aware_utc(event.get("stopped_at")),
            financial_disposition=disposition,  # type: ignore[arg-type]
            original_debit_ref=debit_ref,
            refund_ref=refund_ref,
            amount=amount,
            owner_id=event["owner_id"],  # type: ignore[arg-type]
            task_id=event["task_id"],  # type: ignore[arg-type]
            unit_id=event["unit_id"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError):
        return None


def _verify_terminal_event(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    event: PolicyStopEvent,
    *,
    verified_quote: int,
) -> bool:
    if (
        not _event_scope_matches_graph(event, graph, require_committed=True)
        or type(verified_quote) is not int
        or event.amount != verified_quote
    ):
        return False
    if event.financial_disposition == "no_debit":
        return _valid_original_fee(
            session, graph, event, verified_quote, disposition="no_debit"
        )
    if event.financial_disposition == "refunded":
        return _valid_original_fee(
            session, graph, event, verified_quote, disposition="refunded"
        )
    if event.financial_disposition == "retained_by_started_cancel":
        return _valid_original_fee(
            session, graph, event, verified_quote, disposition="retained_by_started_cancel"
        ) and _trusted_started_cancel(graph, event)
    return False


def _trusted_started_cancel(
    graph: LockedHaoaiGroupGraph,
    event: PolicyStopEvent,
) -> bool:
    task = graph.task
    unit = graph.unit
    marker = task.get("cancellation_reason")
    task_cancelled = _aware_or_none(task.get("user_cancelled_at"))
    unit_cancelled = _aware_or_none(unit.get("user_cancelled_at"))
    first_intent = _aware_or_none(unit.get("first_intent_at"))
    return bool(
        event.financial_disposition == "retained_by_started_cancel"
        and isinstance(marker, str)
        and _USER_CANCEL_MARKER.fullmatch(marker) is not None
        and task.get("status") == "failed"
        and task_cancelled is not None
        and unit_cancelled == task_cancelled
        and unit.get("cancellation_reason") == marker
        and first_intent is not None
        and task_cancelled > first_intent
    )


def _event_scope_matches_graph(
    event: PolicyStopEvent,
    graph: LockedHaoaiGroupGraph,
    *,
    require_committed: bool,
) -> bool:
    active_index = graph.group.get("active_attempt")
    if type(active_index) is not int or not 0 <= active_index < len(graph.steps):
        return False
    active_step = graph.steps[active_index]
    active_rows = [
        row for row in graph.external_submissions if row.get("step_id") == active_step.get("id")
    ]
    if len(active_rows) != 1:
        return False
    submission = active_rows[0]
    sticky = (
        graph.group.get("status") == "policy_stopped"
        and graph.group.get("terminal_reason") == event.reason
        and graph.group.get("sticky_policy_stop_event_ref") == event.event_id
    )
    uncommitted = (
        graph.group.get("status") == "active"
        and graph.group.get("terminal_reason") is None
        and graph.group.get("sticky_policy_stop_event_ref") is None
    )
    return bool(
        event.reason == "auth_unavailable_12"
        and event.policy_version == HAOAI_ATTEMPT_GROUP.stop_policy_version
        and event.owner_id == graph.owner_id
        and event.task_id == graph.task_id
        and event.unit_id == graph.binding.unit_id
        and event.group_key == graph.binding.group_key
        and event.active_step_id == graph.binding.step_id == active_step.get("id")
        and event.active_submission_id == submission.get("id")
        and event.external_id == submission.get("external_task_id")
        and submission.get("submission_status") == "submitted"
        and submission.get("request_digest") == graph.binding.request_digest
        and (sticky if require_committed else uncommitted)
    )


def _valid_event_observations(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    event: PolicyStopEvent,
    authority: HmacProofAuthority,
) -> bool:
    reservations = event.reservation_refs
    observations = event.observation_refs
    if (
        type(reservations) is not tuple
        or type(observations) is not tuple
        or len(reservations) != 12
        or len(observations) != 12
        or len(set(reservations)) != 12
        or len(set(observations)) != 12
        or not isinstance(authority, HmacProofAuthority)
    ):
        return False

    reservation_rows = session.execute(
        select(PollReservationRow.__table__).where(
            PollReservationRow.__table__.c.reservation_id.in_(reservations)
        ).with_for_update()
    ).mappings().all()
    if len(reservation_rows) != 12:
        return False
    by_reservation = {row["reservation_id"]: row for row in reservation_rows}
    evidence_rows = session.execute(
        select(ResultEvidenceRow.__table__).where(
            ResultEvidenceRow.__table__.c.id.in_(observations)
        ).with_for_update()
    ).mappings().all()
    if len(evidence_rows) != 12:
        return False
    by_observation = {row["id"]: row for row in evidence_rows}
    sequences: list[int] = []
    deadline = _aware_or_none(graph.unit.get("deadline_at"))
    if deadline is None:
        return False
    step = next(
        (item for item in graph.steps if item.get("id") == event.active_step_id),
        None,
    )
    if step is None:
        return False

    for reservation_id, observation_id in zip(reservations, observations, strict=True):
        reservation = by_reservation.get(reservation_id)
        evidence = by_observation.get(observation_id)
        if reservation is None or evidence is None:
            return False
        sequence = reservation.get("sequence")
        if type(sequence) is not int or type(reservation.get("committed_poll_count")) is not int:
            return False
        sequences.append(sequence)
        reserved_at = _aware_or_none(reservation.get("reserved_at"))
        timeout_at = _aware_or_none(reservation.get("timeout_at"))
        received_at = _aware_or_none(evidence.get("received_at"))
        fact = verified_group_observation_fact(evidence, authority)
        fact_reserved_at = _fact_datetime(fact.get("reserved_at")) if fact else None
        fact_timeout_at = _fact_datetime(fact.get("timeout_at")) if fact else None
        fact_received_at = _fact_datetime(fact.get("received_at")) if fact else None
        token = reservation.get("token")
        expected_timeout = (
            min(
                reserved_at + timedelta(seconds=_POLL_TIMEOUT_SECONDS),
                deadline,
            )
            if reserved_at is not None
            else None
        )
        if (
            reserved_at is None
            or timeout_at is None
            or received_at is None
            or fact_reserved_at != reserved_at
            or fact_timeout_at != timeout_at
            or fact_received_at != received_at
            or expected_timeout != timeout_at
            or not reserved_at <= received_at <= timeout_at
            or received_at >= deadline
            or not isinstance(token, str)
            or not token
            or fact is None
            or fact.get("reservation_token_digest")
            != hashlib.sha256(token.encode("utf-8")).hexdigest()
        ):
            return False
        if not all(
            (
                reservation.get("unit_id") == graph.binding.unit_id,
                reservation.get("group_key") == event.group_key,
                sequence <= graph.group.get("poll_sequence"),
                reservation.get("state") == "accepted",
                reservation.get("observation_ref") == observation_id,
                reservation.get("step_id") == event.active_step_id,
                reservation.get("attempt_id") == event.active_step_id,
                reservation.get("submission_id") == event.active_submission_id,
                reservation.get("external_id") == event.external_id,
                reservation.get("payload_digest") == graph.binding.request_digest,
                evidence.get("step_id") == event.active_step_id,
                evidence.get("submission_id") == event.active_submission_id,
                evidence.get("evidence_kind") == "haoai_poll_observation_v1",
                evidence.get("source") == "worker",
                evidence.get("validity") == "unknown",
                evidence.get("application_status") == "pending",
                evidence.get("applied_at") is None,
                evidence.get("evidence_key") == f"haoai-poll-v1:{reservation_id}",
                evidence.get("result_digest") == fact.get("body_sha256"),
                reservation.get("committed_poll_count") == sequence,
            )
        ):
            return False
        markers = fact.get("auth_unavailable_markers") if fact is not None else None
        if (
            fact is None
            or fact.get("schema") != "haoai_poll_observation_v1"
            or fact.get("policy_version") != HAOAI_ATTEMPT_GROUP.policy_version
            or fact.get("registry_version") != HAOAI_ATTEMPT_GROUP.registry_version
            or fact.get("owner_id") != graph.owner_id
            or fact.get("task_id") != graph.task_id
            or fact.get("unit_id") != graph.binding.unit_id
            or fact.get("group_key") != event.group_key
            or fact.get("step_id") != event.active_step_id
            or fact.get("submission_id") != event.active_submission_id
            or fact.get("attempt_id") != reservation.get("attempt_id")
            or fact.get("external_id") != event.external_id
            or fact.get("request_digest") != graph.binding.request_digest
            or fact.get("reservation_id") != reservation_id
            or fact.get("sequence") != sequence
            or fact.get("transport") != "provider_response"
            or fact.get("source_fact") != "http_error"
            or not isinstance(markers, list)
            or not markers
            or not all(isinstance(marker, str) and marker in _AUTH_MARKERS for marker in markers)
        ):
            return False

    if sequences != sorted(sequences) or len(set(sequences)) != 12:
        return False
    first_sequence = sequences[0]
    last_sequence = sequences[-1]
    if last_sequence != graph.group.get("poll_sequence"):
        return False
    accepted_window = session.execute(
        select(PollReservationRow.__table__.c.reservation_id)
        .where(
            PollReservationRow.__table__.c.unit_id == graph.binding.unit_id,
            PollReservationRow.__table__.c.group_key == event.group_key,
            PollReservationRow.__table__.c.state == "accepted",
            PollReservationRow.__table__.c.sequence >= first_sequence,
            PollReservationRow.__table__.c.sequence <= last_sequence,
        )
        .order_by(PollReservationRow.__table__.c.sequence)
    ).scalars().all()
    if list(reservations) != accepted_window:
        return False
    final_reservation = by_reservation.get(reservations[-1])
    final_evidence = by_observation.get(observations[-1])
    stopped_at = _aware_or_none(event.stopped_at)
    final_timeout = _aware_or_none(final_reservation.get("timeout_at")) if final_reservation else None
    final_received = _aware_or_none(final_evidence.get("received_at")) if final_evidence else None
    return bool(
        stopped_at is not None
        and final_timeout is not None
        and final_received is not None
        and final_received <= stopped_at <= final_timeout
        and stopped_at < deadline
    )


def _fact_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return _aware_utc(parsed)


def _valid_original_fee(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    event: PolicyStopEvent,
    verified_quote: int,
    *,
    disposition: str,
) -> bool:
    proof = read_verified_group_fee(session, graph, verified_quote)
    if proof is None or event.amount != verified_quote:
        return False
    if verified_quote == 0:
        return bool(
            event.original_debit_ref is None
            and event.refund_ref is None
            and disposition in {"pending", "no_debit"}
            and proof.state == "no_charge"
        )
    if event.original_debit_ref != proof.original_debit_ref:
        return False
    if disposition in {"pending", "retained_by_started_cancel"}:
        return (
            proof.state in {"charged", "refunded"}
            and event.refund_ref is None
        )
    if disposition == "refunded":
        return proof.state == "refunded" and event.refund_ref == proof.refund_ref
    return False


def read_verified_group_fee(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    expected_quote: int,
) -> VerifiedHaoaiGroupFee | None:
    """Prove the original quote, Unit state, and all scoped ledger entries."""

    task = graph.task
    unit = graph.unit
    if (
        not isinstance(session, Session)
        or type(expected_quote) is not int
        or expected_quote < 0
        or task.get("credit_cost") != expected_quote
        or graph.binding.quoted_amount != expected_quote
        or unit.get("quoted_amount") != expected_quote
        or unit.get("task_id") != graph.task_id
        or unit.get("operation") != "video.single"
        or task.get("user_id") != graph.owner_id
    ):
        return None

    quote_id = unit.get("quote_id")
    parent_quote_id = graph.parent_submission.get("task_quote_id")
    if quote_id is None:
        # Legacy video.single admission predates task_quotes; its validated Group
        # binding carries the frozen amount alongside Task and Unit projections.
        if parent_quote_id is not None or unit.get("quote_unit_key") is not None:
            return None
    else:
        if not isinstance(quote_id, str) or not quote_id or parent_quote_id != quote_id:
            return None
        quote = session.execute(
            select(TaskQuoteRow.__table__)
            .where(TaskQuoteRow.__table__.c.id == quote_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            quote is None
            or quote.get("user_id") != graph.owner_id
            or quote.get("operation") != "video.single"
            or quote.get("total_amount") != expected_quote
            or quote.get("status") != "consumed"
            or quote.get("consumed_at") is None
        ):
            return None

    unit_id = graph.binding.unit_id
    task_id = graph.task_id
    fee_row = session.execute(
        select(UnitFeeDecisionRow.__table__)
        .where(UnitFeeDecisionRow.__table__.c.unit_id == unit_id)
        .with_for_update()
    ).mappings().one_or_none()
    if fee_row is not None and fee_row.get("charge_phase") != "single_admission":
        return None

    debit_key = f"debit:{unit_id}"
    refund_key = f"refund:{unit_id}"
    rows = session.execute(
        select(CreditLogRow.__table__)
        .where(
            or_(
                CreditLogRow.__table__.c.task_id == task_id,
                CreditLogRow.__table__.c.billing_unit_id == unit_id,
                CreditLogRow.__table__.c.business_key.in_((debit_key, refund_key)),
            )
        )
        .with_for_update()
    ).mappings().all()

    if expected_quote == 0:
        if (
            task.get("billing_status") != "no_charge"
            or unit.get("billing_status") != "no_charge"
            or unit.get("debited_amount") != 0
            or unit.get("refunded_amount") != 0
            or rows
            or (
                fee_row is not None
                and (
                    fee_row.get("resolution") != "no_charge"
                    or fee_row.get("ledger_ref") is not None
                )
            )
        ):
            return None
        return VerifiedHaoaiGroupFee(0, "no_charge", None, None)

    if unit.get("debited_amount") != expected_quote:
        return None
    debits = [
        row
        for row in rows
        if row.get("business_key") == debit_key
        and row.get("user_id") == graph.owner_id
        and row.get("task_id") == task_id
        and row.get("billing_unit_id") == unit_id
        and row.get("type") == "usage"
        and row.get("amount") == -expected_quote
        and row.get("related_debit_id") is None
        and isinstance(row.get("id"), str)
        and type(row.get("balance_after")) is int
        and row.get("balance_after") >= 0
    ]
    if len(debits) != 1:
        return None
    debit_ref = str(debits[0]["id"])
    refunds = [
        row
        for row in rows
        if row.get("type") == "refund"
        or row.get("business_key") == refund_key
        or row.get("related_debit_id") == debit_ref
    ]
    if len(rows) != 1 + len(refunds) or len(refunds) > 1:
        return None
    if fee_row is not None and (
        fee_row.get("resolution") != "charged" or fee_row.get("ledger_ref") != debit_ref
    ):
        return None
    if not refunds:
        if (
            task.get("billing_status") != "charged"
            or unit.get("billing_status") != "charged"
            or unit.get("refunded_amount") != 0
        ):
            return None
        return VerifiedHaoaiGroupFee(expected_quote, "charged", debit_ref, None)

    refund = refunds[0]
    if not _refund_row_matches(refund, graph, debit_ref, expected_quote):
        return None
    if (
        task.get("billing_status") != "refunded"
        or unit.get("billing_status") != "refunded"
        or unit.get("refunded_amount") != expected_quote
    ):
        return None
    return VerifiedHaoaiGroupFee(
        expected_quote,
        "refunded",
        debit_ref,
        str(refund["id"]),
    )


def _apply_exact_refund(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    event: PolicyStopEvent,
    *,
    wallet: Mapping[str, object],
    now: datetime,
    id_factory,
) -> str | None:
    unit_id = graph.binding.unit_id
    task_id = graph.task_id
    owner_id = graph.owner_id
    amount = event.amount
    original_debit_ref = event.original_debit_ref
    if (
        type(amount) is not int
        or amount <= 0
        or not isinstance(original_debit_ref, str)
        or event.refund_ref is not None
        or event.financial_disposition != "pending"
    ):
        return None
    if (
        wallet.get("user_id") != owner_id
        or type(wallet.get("credits")) is not int
        or not isinstance(wallet.get("id"), str)
    ):
        return None
    log_table = CreditLogRow.__table__
    debit = session.execute(
        select(log_table)
        .where(
            log_table.c.id == original_debit_ref,
            log_table.c.user_id == owner_id,
            log_table.c.task_id == task_id,
            log_table.c.billing_unit_id == unit_id,
            log_table.c.type == "usage",
            log_table.c.amount == -amount,
            log_table.c.business_key == f"debit:{unit_id}",
            log_table.c.related_debit_id.is_(None),
        )
        .with_for_update()
    ).mappings().one_or_none()
    if debit is None:
        return None
    refunds = session.execute(
        select(log_table)
        .where(
            or_(
                log_table.c.business_key == f"refund:{unit_id}",
                and_(
                    log_table.c.type == "refund",
                    log_table.c.related_debit_id == original_debit_ref,
                ),
            )
        )
        .with_for_update()
    ).mappings().all()
    if len(refunds) > 1:
        return None
    refund_id: str
    if refunds:
        refund = refunds[0]
        if not _refund_row_matches(refund, graph, original_debit_ref, amount):
            return None
        if (
            graph.task.get("billing_status") != "refunded"
            or graph.unit.get("billing_status") != "refunded"
            or graph.unit.get("refunded_amount") != amount
        ):
            return None
        refund_id = str(refund["id"])
    else:
        if (
            graph.task.get("billing_status") != "charged"
            or graph.unit.get("billing_status") != "charged"
            or graph.unit.get("refunded_amount") != 0
        ):
            return None
        try:
            refund_id = id_factory()
        except Exception:
            return None
        if not isinstance(refund_id, str) or not refund_id or len(refund_id) > 36:
            return None
        wallet_table = UserCreditRow.__table__
        balance_before = wallet["credits"]
        balance_after = balance_before + amount
        wallet_change = session.execute(
            update(wallet_table)
            .where(
                wallet_table.c.id == wallet["id"],
                wallet_table.c.user_id == owner_id,
                wallet_table.c.credits == balance_before,
            )
            .values(credits=balance_after, updated_at=_database_datetime(now))
        )
        if wallet_change.rowcount != 1:
            raise RuntimeError("HaoAI Group refund wallet compare-and-swap failed")
        session.execute(
            insert(log_table).values(
                id=refund_id,
                user_id=owner_id,
                amount=amount,
                balance_after=balance_after,
                type="refund",
                description="HaoAI 视频策略停止退款",
                task_id=task_id,
                business_key=f"refund:{unit_id}",
                billing_unit_id=unit_id,
                related_debit_id=original_debit_ref,
                created_at=_database_datetime(now),
            )
        )
    updated_unit = session.execute(
        update(BillingUnitRow.__table__)
        .where(
            BillingUnitRow.__table__.c.id == unit_id,
            BillingUnitRow.__table__.c.task_id == task_id,
            BillingUnitRow.__table__.c.billing_status == graph.unit.get("billing_status"),
            BillingUnitRow.__table__.c.debited_amount == amount,
            BillingUnitRow.__table__.c.refunded_amount == graph.unit.get("refunded_amount"),
        )
        .values(
            billing_status="refunded",
            refunded_amount=amount,
            updated_at=_database_datetime(now),
        )
    )
    updated_task = session.execute(
        update(AITaskRow.__table__)
        .where(
            AITaskRow.__table__.c.id == task_id,
            AITaskRow.__table__.c.user_id == owner_id,
            AITaskRow.__table__.c.billing_status == graph.task.get("billing_status"),
            AITaskRow.__table__.c.credit_cost == amount,
        )
        .values(billing_status="refunded", updated_at=_database_datetime(now))
    )
    updated_event = session.execute(
        update(PolicyStopEventRow.__table__)
        .where(
            PolicyStopEventRow.__table__.c.event_id == event.event_id,
            PolicyStopEventRow.__table__.c.financial_disposition == "pending",
            PolicyStopEventRow.__table__.c.original_debit_ref == original_debit_ref,
            PolicyStopEventRow.__table__.c.amount == amount,
            PolicyStopEventRow.__table__.c.refund_ref.is_(None),
        )
        .values(
            financial_disposition="refunded",
            refund_ref=refund_id,
        )
    )
    if updated_unit.rowcount != 1 or updated_task.rowcount != 1 or updated_event.rowcount != 1:
        raise RuntimeError("HaoAI Group refund financial compare-and-swap failed")
    return "refunded"


def _refund_row_matches(
    refund: Mapping[str, object],
    graph: LockedHaoaiGroupGraph,
    debit_id: str,
    amount: int,
) -> bool:
    return bool(
        refund.get("user_id") == graph.owner_id
        and refund.get("task_id") == graph.task_id
        and refund.get("billing_unit_id") == graph.binding.unit_id
        and refund.get("business_key") == f"refund:{graph.binding.unit_id}"
        and refund.get("type") == "refund"
        and refund.get("amount") == amount
        and refund.get("related_debit_id") == debit_id
        and isinstance(refund.get("id"), str)
        and type(refund.get("balance_after")) is int
        and refund.get("balance_after") >= 0
    )


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("expected datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aware_or_none(value: object) -> datetime | None:
    try:
        return _aware_utc(value)
    except TypeError:
        return None


class SqlAlchemyHaoaiGroupEventSettler:
    """Settle one Group policy event under its owner and complete graph locks."""

    def __init__(
        self,
        session_factory: SessionFactory,
        db_clock: DbClock,
        proof_authority: HmacProofAuthority,
        id_factory: IdFactory,
    ) -> None:
        if not all(
            (
                callable(session_factory),
                callable(db_clock),
                isinstance(proof_authority, HmacProofAuthority),
                callable(id_factory),
            )
        ):
            raise TypeError("Group event settlement dependencies must be explicit")
        self._session_factory = session_factory
        self._db_clock = db_clock
        self._proof_authority = proof_authority
        self._id_factory = id_factory

    def settle_owned(self, owner_id: str, task_id: str) -> str:
        """Settle an event and independently confirm any uncertain commit."""

        now = self._read_clock()
        if now is None or not _valid_identifier(owner_id) or not _valid_identifier(task_id):
            return "pending"
        known_event_ids: tuple[str, ...] = ()
        session = self._session_factory()
        try:
            with session.begin():
                _set_read_committed(session)
                graph = lock_haoai_group_graph(session, owner_id, task_id)
                if graph is None:
                    return "pending"
                rows = _locked_policy_events(session, graph)
                if not rows:
                    return "not_selected"
                known_event_ids = tuple(str(row["event_id"]) for row in rows)
                if len(rows) != 1:
                    raise RuntimeError("HaoAI Group policy event set is ambiguous")
                row = rows[0]
                outcome = settle_pending_policy_stop_event(
                    session,
                    graph,
                    row,
                    proof_authority=self._proof_authority,
                    verified_quote=graph.binding.quoted_amount,
                    now=now,
                    id_factory=self._id_factory,
                )
                if outcome not in {
                    "refunded",
                    "retained_by_started_cancel",
                    "no_debit",
                }:
                    raise RuntimeError("HaoAI Group policy event remains unresolved")
                if outcome in {"refunded", "no_debit"}:
                    fresh_graph = lock_haoai_group_graph(
                        session, owner_id, task_id
                    )
                    fresh_rows = (
                        _locked_policy_events(session, fresh_graph)
                        if fresh_graph is not None
                        else None
                    )
                    if (
                        fresh_graph is None
                        or fresh_rows is None
                        or len(fresh_rows) != 1
                        or not _project_policy_stop_terminal(
                            session,
                            fresh_graph,
                            fresh_rows[0],
                            proof_authority=self._proof_authority,
                            now=now,
                        )
                    ):
                        raise RuntimeError(
                            "HaoAI Group policy terminal projection could not be committed"
                        )
            return "settled"
        except Exception:
            return self._confirm(owner_id, task_id, known_event_ids)
        finally:
            session.close()

    def _confirm(
        self,
        owner_id: str,
        task_id: str,
        expected_event_ids: tuple[str, ...],
    ) -> str:
        try:
            with self._session_factory() as session:
                with session.begin():
                    _set_read_committed(session)
                    graph = lock_haoai_group_graph(session, owner_id, task_id)
                    if graph is None:
                        return "pending"
                    rows = _locked_policy_events(session, graph)
                    actual_ids = tuple(str(row["event_id"]) for row in rows)
                    if not rows or (expected_event_ids and actual_ids != expected_event_ids):
                        return "pending"
                    if len(rows) != 1:
                        return "pending"
                    row = rows[0]
                    if not verify_policy_stop_event(
                        session,
                        graph,
                        row,
                        proof_authority=self._proof_authority,
                        verified_quote=graph.binding.quoted_amount,
                    ):
                        return "pending"
                    if row["financial_disposition"] in {"refunded", "no_debit"}:
                        return (
                            "settled"
                            if _policy_stop_terminal_projection_matches(graph, row)
                            else "pending"
                        )
                    return (
                        "settled"
                        if row["financial_disposition"]
                        in {"refunded", "retained_by_started_cancel", "no_debit"}
                        else "pending"
                    )
        except Exception:
            return "pending"

    def _read_clock(self) -> datetime | None:
        try:
            value = self._db_clock()
        except Exception:
            return None
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            return None
        return value.astimezone(timezone.utc)


def _locked_policy_events(
    session: Session,
    graph: LockedHaoaiGroupGraph,
) -> list[Mapping[str, object]]:
    rows = session.execute(
        select(PolicyStopEventRow.__table__)
        .where(PolicyStopEventRow.__table__.c.unit_id == graph.binding.unit_id)
        .order_by(PolicyStopEventRow.__table__.c.event_id)
        .with_for_update()
    ).mappings().all()
    if any(
        row.get("group_key") != graph.binding.group_key
        or row.get("policy_version") != HAOAI_ATTEMPT_GROUP.stop_policy_version
        for row in rows
    ):
        raise RuntimeError("HaoAI Group policy event has an unknown scope")
    return rows


def _project_policy_stop_terminal(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    event: Mapping[str, object],
    *,
    proof_authority: HmacProofAuthority,
    now: datetime,
) -> bool:
    if (
        not verify_policy_stop_event(
            session,
            graph,
            event,
            proof_authority=proof_authority,
            verified_quote=graph.binding.quoted_amount,
        )
        or event.get("financial_disposition") not in {"refunded", "no_debit"}
        or event.get("reason") != "auth_unavailable_12"
        or graph.group.get("status") != "policy_stopped"
        or graph.group.get("terminal_reason") != "auth_unavailable_12"
        or graph.group.get("sticky_policy_stop_event_ref") != event.get("event_id")
    ):
        return False
    if _policy_stop_terminal_projection_matches(graph, event):
        return True

    expected_billing = (
        "refunded"
        if event.get("financial_disposition") == "refunded"
        else "no_charge"
    )
    task = graph.task
    unit = graph.unit
    if (
        task.get("status") not in {"queued", "processing", "reconciling"}
        or task.get("result") not in (None, _RECONCILING_RESULT)
        or task.get("user_cancelled_at") is not None
        or task.get("cancellation_reason") is not None
        or task.get("billing_status") != expected_billing
        or task.get("credit_cost") != graph.binding.quoted_amount
        or unit.get("billing_status") != expected_billing
        or unit.get("result_status") not in {"pending", "unknown"}
        or unit.get("success_confirmed_at") is not None
        or unit.get("user_cancelled_at") is not None
        or unit.get("cancellation_reason") is not None
        or unit.get("refunded_amount")
        != (graph.binding.quoted_amount if expected_billing == "refunded" else 0)
    ):
        return False
    task_generation = task.get("execution_generation")
    if type(task_generation) is not int or task_generation < 0:
        return False

    timestamp = _database_datetime(now)
    unit_table = BillingUnitRow.__table__
    unit_change = session.execute(
        update(unit_table)
        .where(
            unit_table.c.id == graph.binding.unit_id,
            unit_table.c.task_id == graph.task_id,
            unit_table.c.result_status.in_(("pending", "unknown")),
            unit_table.c.billing_status == expected_billing,
            unit_table.c.refunded_amount
            == (graph.binding.quoted_amount if expected_billing == "refunded" else 0),
            unit_table.c.success_confirmed_at.is_(None),
            unit_table.c.user_cancelled_at.is_(None),
        )
        .values(result_status="unknown", updated_at=timestamp)
    )
    task_table = AITaskRow.__table__
    result_predicate = (
        task_table.c.result.is_(None)
        if task.get("result") is None
        else task_table.c.result == task.get("result")
    )
    task_change = session.execute(
        update(task_table)
        .where(
            task_table.c.id == graph.task_id,
            task_table.c.user_id == graph.owner_id,
            task_table.c.type == "video-single",
            task_table.c.status == task.get("status"),
            task_table.c.status.in_(("queued", "processing", "reconciling")),
            result_predicate,
            task_table.c.execution_generation == task_generation,
            task_table.c.billing_status == expected_billing,
            task_table.c.credit_cost == graph.binding.quoted_amount,
            task_table.c.user_cancelled_at.is_(None),
            task_table.c.cancellation_reason.is_(None),
        )
        .values(
            status="failed",
            result=_POLICY_STOP_RESULT,
            recovery_status="reconciling",
            progress=100,
            progress_message="视频任务因认证策略停止，原任务结果状态未知",
            claimed_by=None,
            claim_token=None,
            lease_until=None,
            execution_generation=task_generation + 1,
            updated_at=timestamp,
        )
    )
    if unit_change.rowcount != 1 or task_change.rowcount != 1:
        raise RuntimeError("HaoAI Group policy terminal projection CAS lost")
    session.flush()
    terminal_graph = lock_haoai_group_graph(
        session, graph.owner_id, graph.task_id
    )
    terminal_event = session.execute(
        select(PolicyStopEventRow.__table__).where(
            PolicyStopEventRow.__table__.c.event_id == event.get("event_id")
        )
    ).mappings().one_or_none()
    return bool(
        terminal_graph is not None
        and terminal_event is not None
        and _policy_stop_terminal_projection_matches(terminal_graph, terminal_event)
    )


def _policy_stop_terminal_projection_matches(
    graph: LockedHaoaiGroupGraph,
    event: Mapping[str, object],
) -> bool:
    return _policy_stop_task_terminal_matches(graph, event) or _policy_stop_cancel_terminal_matches(
        graph, event
    )


def _policy_stop_task_terminal_matches(
    graph: LockedHaoaiGroupGraph,
    event: Mapping[str, object],
) -> bool:
    expected_billing = (
        "refunded"
        if event.get("financial_disposition") == "refunded"
        else "no_charge"
        if event.get("financial_disposition") == "no_debit"
        else None
    )
    return bool(
        expected_billing is not None
        and event.get("reason") == "auth_unavailable_12"
        and graph.group.get("status") == "policy_stopped"
        and graph.group.get("terminal_reason") == "auth_unavailable_12"
        and graph.group.get("sticky_policy_stop_event_ref") == event.get("event_id")
        and graph.task.get("status") == "failed"
        and graph.task.get("result") == _POLICY_STOP_RESULT
        and graph.task.get("billing_status") == expected_billing
        and graph.task.get("credit_cost") == graph.binding.quoted_amount
        and graph.task.get("claimed_by") is None
        and graph.task.get("claim_token") is None
        and graph.task.get("lease_until") is None
        and graph.unit.get("billing_status") == expected_billing
        and graph.unit.get("result_status") == "unknown"
        and graph.unit.get("refunded_amount")
        == (graph.binding.quoted_amount if expected_billing == "refunded" else 0)
    )


def _policy_stop_cancel_terminal_matches(
    graph: LockedHaoaiGroupGraph,
    event: Mapping[str, object],
) -> bool:
    expected_billing = (
        "refunded"
        if event.get("financial_disposition") == "refunded"
        else "no_charge"
        if event.get("financial_disposition") == "no_debit"
        else None
    )
    task = graph.task
    unit = graph.unit
    marker = task.get("cancellation_reason")
    task_cancelled = _aware_or_none(task.get("user_cancelled_at"))
    unit_cancelled = _aware_or_none(unit.get("user_cancelled_at"))
    first_intent = _aware_or_none(unit.get("first_intent_at"))
    deadline = _aware_or_none(unit.get("deadline_at"))
    event_stopped = _aware_or_none(event.get("stopped_at"))
    return bool(
        expected_billing is not None
        and event.get("reason") == "auth_unavailable_12"
        and graph.group.get("status") == "policy_stopped"
        and graph.group.get("terminal_reason") == "auth_unavailable_12"
        and graph.group.get("sticky_policy_stop_event_ref") == event.get("event_id")
        and graph.group.get("outstanding_reservation_ref") is None
        and task.get("status") == "failed"
        and task.get("result") == _TASK_CANCEL_RESULT
        and task.get("billing_status") == expected_billing
        and task.get("credit_cost") == graph.binding.quoted_amount
        and task.get("claimed_by") is None
        and task.get("claim_token") is None
        and task.get("lease_until") is None
        and isinstance(marker, str)
        and _CANCEL_MARKER.fullmatch(marker) is not None
        and task_cancelled is not None
        and task_cancelled == unit_cancelled
        and first_intent is not None
        and deadline is not None
        and event_stopped is not None
        and unit.get("cancellation_reason") == marker
        and task_cancelled >= first_intent
        and task_cancelled >= event_stopped
        and task_cancelled < deadline
        and unit.get("billing_status") == expected_billing
        and unit.get("result_status") == "unknown"
        and unit.get("refunded_amount")
        == (graph.binding.quoted_amount if expected_billing == "refunded" else 0)
    )


def _set_read_committed(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 36


__all__ = (
    "SqlAlchemyHaoaiGroupEventSettler",
    "VerifiedHaoaiGroupFee",
    "establish_pending_policy_stop_event",
    "read_verified_group_fee",
    "settle_pending_policy_stop_event",
    "verify_policy_stop_event",
    "verify_pending_policy_stop_event",
)
