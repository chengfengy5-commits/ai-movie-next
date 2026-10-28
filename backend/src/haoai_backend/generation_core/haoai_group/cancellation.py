"""Owner-scoped cancellation and exact fee settlement for HaoAI Groups."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import insert, or_, select, text, update
from sqlalchemy.orm import Session

from haoai_backend.generation_credentials.domain import HmacProofAuthority
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.haoai_group.persistence import (
    LockedHaoaiGroupGraph,
    lock_haoai_group_graph,
)
from haoai_backend.generation_core.haoai_group.intents import (
    is_valid_started_group_submission,
)
from haoai_backend.generation_core.haoai_group.settlement import (
    VerifiedHaoaiGroupFee,
    read_verified_group_fee,
    settle_pending_policy_stop_event,
    verify_policy_stop_event,
)
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    PolicyStopEventRow,
    PollReservationRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
CancelOutcome = Literal[
    "not_selected",
    "settled",
    "retained",
    "pending",
    "terminal_projection",
]
_CANCEL_MARKER = re.compile(r"^user_cancelled:v1:[0-9a-f]{32}$")
_TASK_CANCEL_RESULT = json.dumps(
    {
        "code": "user_cancelled",
        "message": "视频任务已取消",
        "status": "failed",
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)


class SqlAlchemyHaoaiGroupCancellation:
    """Cancel one complete Group graph in the same transaction as its fee proof."""

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
            raise TypeError("Group cancellation dependencies must be explicit")
        self._session_factory = session_factory
        self._db_clock = db_clock
        self._proof_authority = proof_authority
        self._id_factory = id_factory

    def cancel_owned(self, actor, task_id: str) -> CancelOutcome:
        """Commit cancellation only after proving the original Group and ledger."""

        owner_id = getattr(actor, "user_id", None)
        if not _valid_id(owner_id) or not _valid_id(task_id):
            return "pending"
        try:
            marker = f"user_cancelled:v1:{secrets.token_hex(16)}"
        except Exception:
            return "pending"
        session = self._session_factory()
        try:
            with session.begin():
                _set_read_committed(session)
                graph = lock_haoai_group_graph(session, owner_id, task_id)
                if graph is None:
                    return (
                        "pending"
                        if _has_group_candidate(session, owner_id, task_id)
                        else "not_selected"
                    )
                events = _lock_group_events(session, graph)
                if events is None:
                    return "pending"
                receipts = _lock_group_receipts(session, graph)
                if receipts is None:
                    return "pending"
                works = _lock_group_work(session, graph)
                if works is None or works:
                    return "pending"
                now = self._read_clock()
                if now is None:
                    return "pending"
                if receipts:
                    return (
                        "terminal_projection"
                        if len(receipts) == 1
                        and _timely_terminal_receipt(graph, receipts[0])
                        else "pending"
                    )
                if graph.task.get("user_cancelled_at") is not None or graph.unit.get(
                    "user_cancelled_at"
                ) is not None:
                    now = self._read_clock()
                    if now is None:
                        return "pending"
                    return self._confirm_existing_cancel(
                        session, graph, events, now
                    )
                if graph.task.get("status") not in {"queued", "processing", "reconciling"}:
                    return "terminal_projection" if graph.task.get("status") in {"failed", "completed"} else "pending"
                deadline = _aware_or_none(graph.unit.get("deadline_at"))
                if deadline is not None and deadline <= now:
                    # The original due policy has precedence over a new cancellation.
                    return "pending"
                if events and len(events) != 1:
                    return "pending"

                wallet = None
                if graph.binding.quoted_amount > 0:
                    wallet = _lock_wallet(session, graph.owner_id)
                    if wallet is None:
                        return "pending"
                    now = self._read_clock()
                    if now is None:
                        return "pending"
                    deadline = _aware_or_none(graph.unit.get("deadline_at"))
                    if deadline is not None and deadline <= now:
                        return "pending"
                if events:
                    event = events[0]
                    disposition = settle_pending_policy_stop_event(
                        session,
                        graph,
                        event,
                        proof_authority=self._proof_authority,
                        verified_quote=graph.binding.quoted_amount,
                        now=now,
                        id_factory=self._id_factory,
                    )
                    if disposition not in {
                        "refunded",
                        "retained_by_started_cancel",
                        "no_debit",
                    }:
                        raise RuntimeError("HaoAI Group policy event is not settled")
                    financial_outcome = (
                        "retained"
                        if disposition == "retained_by_started_cancel"
                        else "settled"
                    )
                else:
                    financial_outcome = self._settle_without_event(
                        session, graph, wallet, now
                    )
                    if financial_outcome not in {"settled", "retained"}:
                        raise RuntimeError("HaoAI Group cancellation fee is unproven")

                if not _close_outstanding_reservation(session, graph, now):
                    raise RuntimeError("HaoAI Group outstanding poll cannot be closed")
                _write_cancel_projection(
                    session,
                    graph,
                    marker,
                    now,
                    preserve_policy_stop=bool(events),
                )
            return financial_outcome
        except Exception:
            return self._confirm_after_unknown(owner_id, task_id, marker)
        finally:
            session.close()

    def _settle_without_event(
        self,
        session: Session,
        graph: LockedHaoaiGroupGraph,
        wallet: Mapping[str, object] | None,
        now: datetime,
    ) -> Literal["settled", "retained"] | None:
        proof = read_verified_group_fee(
            session, graph, graph.binding.quoted_amount
        )
        if proof is None:
            return None
        if proof.state == "no_charge" or proof.state == "refunded":
            return "settled"

        started_at = _aware_or_none(graph.unit.get("first_intent_at"))
        if started_at is not None:
            if (
                started_at >= now
                or not _valid_started_submission(graph, now)
            ):
                return None
            return "retained"
        if graph.external_submissions:
            return None
        if wallet is None or proof.original_debit_ref is None:
            return None
        return _refund_unstarted_group(
            session,
            graph,
            proof,
            wallet,
            now,
            self._id_factory,
        )

    def _confirm_existing_cancel(
        self,
        session: Session,
        graph: LockedHaoaiGroupGraph,
        events: list[Mapping[str, object]],
        now: datetime,
    ) -> CancelOutcome:
        marker = graph.task.get("cancellation_reason")
        task_cancelled = _aware_or_none(graph.task.get("user_cancelled_at"))
        unit_cancelled = _aware_or_none(graph.unit.get("user_cancelled_at"))
        if (
            not isinstance(marker, str)
            or _CANCEL_MARKER.fullmatch(marker) is None
            or task_cancelled is None
            or task_cancelled != unit_cancelled
            or graph.unit.get("cancellation_reason") != marker
            or graph.task.get("status") != "failed"
            or graph.task.get("claimed_by") is not None
            or graph.task.get("claim_token") is not None
            or graph.task.get("lease_until") is not None
            or graph.group.get("outstanding_reservation_ref") is not None
        ):
            return "pending"
        if events:
            if (
                len(events) != 1
                or graph.group.get("status") != "policy_stopped"
                or graph.group.get("terminal_reason") != "auth_unavailable_12"
                or graph.group.get("sticky_policy_stop_event_ref")
                != events[0].get("event_id")
            ):
                return "pending"
            if not verify_policy_stop_event(
                session,
                graph,
                events[0],
                proof_authority=self._proof_authority,
                verified_quote=graph.binding.quoted_amount,
            ):
                return "pending"
            disposition = events[0].get("financial_disposition")
            if disposition == "retained_by_started_cancel":
                return "retained"
            return "settled" if disposition in {"refunded", "no_debit"} else "pending"

        if (
            graph.group.get("status") != "cancelled"
            or graph.group.get("terminal_reason") != "user_cancelled"
            or graph.group.get("sticky_policy_stop_event_ref") is not None
        ):
            return "pending"

        if graph.binding.quoted_amount > 0:
            if _lock_wallet(session, graph.owner_id) is None:
                return "pending"
            now = self._read_clock()
            if now is None:
                return "pending"
        proof = read_verified_group_fee(
            session, graph, graph.binding.quoted_amount
        )
        if proof is None:
            return "pending"
        if proof.state in {"no_charge", "refunded"}:
            return "settled"
        started_at = _aware_or_none(graph.unit.get("first_intent_at"))
        deadline = _aware_or_none(graph.unit.get("deadline_at"))
        return (
            "retained"
            if started_at is not None
            and started_at < task_cancelled
            and task_cancelled <= now
            and deadline is not None
            and task_cancelled < deadline
            and _valid_started_submission(graph, task_cancelled)
            else "pending"
        )

    def _confirm_after_unknown(
        self,
        owner_id: str,
        task_id: str,
        marker: str,
    ) -> CancelOutcome:
        try:
            with self._session_factory() as session:
                with session.begin():
                    _set_read_committed(session)
                    graph = lock_haoai_group_graph(session, owner_id, task_id)
                    if graph is None:
                        return (
                            "pending"
                            if _has_group_candidate(session, owner_id, task_id)
                            else "not_selected"
                        )
                    events = _lock_group_events(session, graph)
                    receipts = _lock_group_receipts(session, graph)
                    works = _lock_group_work(session, graph)
                    if (
                        events is None
                        or receipts is None
                        or works is None
                        or works
                        or graph.task.get("cancellation_reason") != marker
                    ):
                        return "pending"
                    if receipts:
                        return "pending"
                    now = self._read_clock()
                    if now is None:
                        return "pending"
                    return self._confirm_existing_cancel(
                        session, graph, events, now
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


def _refund_unstarted_group(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    proof: VerifiedHaoaiGroupFee,
    wallet: Mapping[str, object],
    now: datetime,
    id_factory: IdFactory,
    *,
    description: str = "HaoAI 视频任务在提交前取消退款",
) -> Literal["settled"] | None:
    amount = proof.amount
    debit_ref = proof.original_debit_ref
    wallet_id = wallet.get("id")
    credits = wallet.get("credits")
    if (
        type(amount) is not int
        or amount <= 0
        or not isinstance(debit_ref, str)
        or not isinstance(wallet_id, str)
        or type(credits) is not int
        or wallet.get("user_id") != graph.owner_id
    ):
        return None
    try:
        refund_id = id_factory()
    except Exception:
        return None
    if not _valid_id(refund_id):
        return None
    balance = credits + amount
    wallet_table = UserCreditRow.__table__
    wallet_change = session.execute(
        update(wallet_table)
        .where(
            wallet_table.c.id == wallet_id,
            wallet_table.c.user_id == graph.owner_id,
            wallet_table.c.credits == credits,
        )
        .values(credits=balance, updated_at=_database_datetime(now))
    )
    if wallet_change.rowcount != 1:
        raise RuntimeError("HaoAI Group cancellation wallet compare-and-swap failed")
    session.execute(
        insert(CreditLogRow.__table__).values(
            id=refund_id,
            user_id=graph.owner_id,
            amount=amount,
            balance_after=balance,
            type="refund",
            description=description,
            task_id=graph.task_id,
            business_key=f"refund:{graph.binding.unit_id}",
            billing_unit_id=graph.binding.unit_id,
            related_debit_id=debit_ref,
            created_at=_database_datetime(now),
        )
    )
    unit_table = BillingUnitRow.__table__
    unit_change = session.execute(
        update(unit_table)
        .where(
            unit_table.c.id == graph.binding.unit_id,
            unit_table.c.task_id == graph.task_id,
            unit_table.c.billing_status == "charged",
            unit_table.c.debited_amount == amount,
            unit_table.c.refunded_amount == 0,
            unit_table.c.user_cancelled_at.is_(None),
        )
        .values(
            billing_status="refunded",
            refunded_amount=amount,
            updated_at=_database_datetime(now),
        )
    )
    task_table = AITaskRow.__table__
    task_change = session.execute(
        update(task_table)
        .where(
            task_table.c.id == graph.task_id,
            task_table.c.user_id == graph.owner_id,
            task_table.c.billing_status == "charged",
            task_table.c.credit_cost == amount,
            task_table.c.user_cancelled_at.is_(None),
        )
        .values(billing_status="refunded", updated_at=_database_datetime(now))
    )
    if unit_change.rowcount != 1 or task_change.rowcount != 1:
        raise RuntimeError("HaoAI Group cancellation refund compare-and-swap failed")
    return "settled"


def _valid_started_submission(
    graph: LockedHaoaiGroupGraph,
    at_time: datetime,
) -> bool:
    rows = tuple(
        row
        for row in graph.external_submissions
        if row.get("step_id") == graph.binding.step_id
    )
    return bool(
        len(rows) == 1
        and is_valid_started_group_submission(graph, rows[0], at_time)
    )


def _write_cancel_projection(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    marker: str,
    now: datetime,
    *,
    preserve_policy_stop: bool,
) -> None:
    timestamp = _database_datetime(now)
    started = _aware_or_none(graph.unit.get("first_intent_at")) is not None
    unit_table = BillingUnitRow.__table__
    unit_change = session.execute(
        update(unit_table)
        .where(
            unit_table.c.id == graph.binding.unit_id,
            unit_table.c.task_id == graph.task_id,
            unit_table.c.user_cancelled_at.is_(None),
            unit_table.c.cancellation_reason.is_(None),
            unit_table.c.result_status.in_(("pending", "unknown")),
        )
        .values(
            user_cancelled_at=timestamp,
            cancellation_reason=marker,
            result_status="unknown" if started else "failed",
            updated_at=timestamp,
        )
    )
    if unit_change.rowcount != 1:
        raise RuntimeError("HaoAI Group Unit cancellation compare-and-swap failed")

    task_table = AITaskRow.__table__
    task_change = session.execute(
        update(task_table)
        .where(
            task_table.c.id == graph.task_id,
            task_table.c.user_id == graph.owner_id,
            task_table.c.status == graph.task.get("status"),
            task_table.c.execution_generation == graph.task.get("execution_generation"),
            task_table.c.claimed_by == graph.task.get("claimed_by"),
            task_table.c.claim_token == graph.task.get("claim_token"),
            task_table.c.lease_until == graph.task.get("lease_until"),
            task_table.c.user_cancelled_at.is_(None),
            task_table.c.cancellation_reason.is_(None),
        )
        .values(
            status="failed",
            recovery_status="reconciling" if started else graph.task.get("recovery_status"),
            result=_TASK_CANCEL_RESULT,
            progress=100,
            progress_message="视频任务已取消" if not started else "视频任务已停止，原任务结果保留核对",
            claimed_by=None,
            claim_token=None,
            lease_until=None,
            execution_generation=graph.task["execution_generation"] + 1,
            user_cancelled_at=timestamp,
            cancellation_reason=marker,
            updated_at=timestamp,
        )
    )
    if task_change.rowcount != 1:
        raise RuntimeError("HaoAI Group Task cancellation compare-and-swap failed")

    if not preserve_policy_stop:
        group_table = ExecutionAttemptGroupRow.__table__
        group_change = session.execute(
            update(group_table)
            .where(
                group_table.c.unit_id == graph.binding.unit_id,
                group_table.c.group_key == graph.binding.group_key,
                group_table.c.status == graph.group.get("status"),
                group_table.c.terminal_reason == graph.group.get("terminal_reason"),
                group_table.c.outstanding_reservation_ref.is_(None),
                group_table.c.sticky_policy_stop_event_ref.is_(None),
            )
            .values(
                status="cancelled",
                terminal_reason="user_cancelled",
                next_poll_at=None,
            )
        )
        if group_change.rowcount != 1:
            raise RuntimeError("HaoAI Group cancellation compare-and-swap failed")


def _close_outstanding_reservation(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    now: datetime,
) -> bool:
    reservation_id = graph.group.get("outstanding_reservation_ref")
    if reservation_id is None:
        return True
    if not isinstance(reservation_id, str):
        return False
    poll_table = PollReservationRow.__table__
    reservation = session.execute(
        select(poll_table)
        .where(
            poll_table.c.reservation_id == reservation_id,
            poll_table.c.unit_id == graph.binding.unit_id,
            poll_table.c.group_key == graph.binding.group_key,
            poll_table.c.state == "reserved",
            poll_table.c.observation_ref.is_(None),
        )
        .with_for_update()
    ).mappings().one_or_none()
    if reservation is None:
        return False
    changed = session.execute(
        update(poll_table)
        .where(
            poll_table.c.reservation_id == reservation_id,
            poll_table.c.sequence == reservation.get("sequence"),
            poll_table.c.token == reservation.get("token"),
            poll_table.c.state == "reserved",
            poll_table.c.observation_ref.is_(None),
        )
        .values(state="closed_unknown")
    )
    if changed.rowcount != 1:
        return False
    group_table = ExecutionAttemptGroupRow.__table__
    result = session.execute(
        update(group_table)
        .where(
            group_table.c.unit_id == graph.binding.unit_id,
            group_table.c.group_key == graph.binding.group_key,
            group_table.c.outstanding_reservation_ref == reservation_id,
        )
        .values(outstanding_reservation_ref=None, next_poll_at=None)
    )
    return result.rowcount == 1


def _lock_group_events(
    session: Session,
    graph: LockedHaoaiGroupGraph,
) -> list[Mapping[str, object]] | None:
    rows = session.execute(
        select(PolicyStopEventRow.__table__)
        .where(PolicyStopEventRow.unit_id == graph.binding.unit_id)
        .order_by(PolicyStopEventRow.event_id)
        .with_for_update()
    ).mappings().all()
    if any(
        row.get("group_key") != graph.binding.group_key
        or row.get("policy_version") != HAOAI_ATTEMPT_GROUP.stop_policy_version
        for row in rows
    ):
        return None
    return rows


def _lock_group_receipts(
    session: Session,
    graph: LockedHaoaiGroupGraph,
) -> list[Mapping[str, object]] | None:
    rows = session.execute(
        select(GenerationResultReceiptRow.__table__)
        .where(
            GenerationResultReceiptRow.__table__.c.task_id == graph.task_id,
            GenerationResultReceiptRow.__table__.c.unit_id == graph.binding.unit_id,
        )
        .order_by(GenerationResultReceiptRow.__table__.c.result_ref)
        .with_for_update()
    ).mappings().all()
    return rows


def _lock_group_work(
    session: Session,
    graph: LockedHaoaiGroupGraph,
) -> list[Mapping[str, object]] | None:
    return session.execute(
        select(LocalContinuationWorkRow.__table__)
        .where(
            LocalContinuationWorkRow.__table__.c.task_id == graph.task_id,
            LocalContinuationWorkRow.__table__.c.unit_id == graph.binding.unit_id,
        )
        .order_by(LocalContinuationWorkRow.__table__.c.work_id)
        .with_for_update()
    ).mappings().all()


def _timely_terminal_receipt(
    graph: LockedHaoaiGroupGraph,
    receipt: Mapping[str, object],
) -> bool:
    unit = graph.unit
    group = graph.group
    step = graph.steps[graph.group["active_attempt"]]
    submission = next(
        (row for row in graph.external_submissions if row.get("step_id") == step.get("id")),
        None,
    )
    snapshot = step.get("input_snapshot")
    validated_at = _aware_or_none(receipt.get("validated_at"))
    deadline = _aware_or_none(unit.get("deadline_at"))
    return bool(
        submission is not None
        and graph.task.get("status") == "completed"
        and unit.get("result_status") == "succeeded"
        and unit.get("success_confirmed_at") is not None
        and group.get("status") == "succeeded"
        and group.get("selected_evidence_id") == receipt.get("evidence_id")
        and receipt.get("owner_id") == graph.owner_id
        and receipt.get("task_id") == graph.task_id
        and receipt.get("unit_id") == graph.binding.unit_id
        and receipt.get("operation") == "video.single"
        and receipt.get("step_id") == step.get("id")
        and receipt.get("submission_id") == submission.get("id")
        and isinstance(snapshot, Mapping)
        and receipt.get("selected_attempt_id") == snapshot.get("server_attempt_id")
        and receipt.get("automatic_application_allowed") is False
        and receipt.get("late_disposition") is None
        and receipt.get("policy_stop_disposition") is None
        and validated_at is not None
        and deadline is not None
        and validated_at < deadline
    )


def _has_group_candidate(session: Session, owner_id: str, task_id: str) -> bool:
    task = session.execute(
        select(AITaskRow.__table__)
        .where(
            AITaskRow.__table__.c.id == task_id,
            AITaskRow.__table__.c.user_id == owner_id,
        )
        .with_for_update()
    ).mappings().one_or_none()
    if task is None:
        return False
    try:
        request_data = json.loads(task.get("request_data"))
    except (TypeError, ValueError):
        request_data = None
    task_hint = (
        isinstance(request_data, dict)
        and (
            request_data.get("step_graph_version")
            == HAOAI_ATTEMPT_GROUP.registry_version
            or request_data.get("graph_version")
            == HAOAI_ATTEMPT_GROUP.registry_version
        )
    )
    unit_rows = session.execute(
        select(BillingUnitRow.__table__)
        .where(BillingUnitRow.__table__.c.task_id == task_id)
        .order_by(BillingUnitRow.__table__.c.id)
        .with_for_update()
    ).mappings().all()
    unit_ids = tuple(row["id"] for row in unit_rows)
    step_rows = (
        session.execute(
            select(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.billing_unit_id.in_(unit_ids))
            .order_by(
                ExecutionStepRow.__table__.c.billing_unit_id,
                ExecutionStepRow.__table__.c.step_order,
                ExecutionStepRow.__table__.c.id,
            )
            .with_for_update()
        ).mappings().all()
        if unit_ids
        else []
    )
    step_ids = tuple(row["id"] for row in step_rows)
    submission_id = (
        request_data.get("submission_id")
        if isinstance(request_data, dict)
        else None
    )
    if isinstance(submission_id, str) and submission_id:
        session.execute(
            select(TaskSubmissionRow.__table__)
            .where(TaskSubmissionRow.__table__.c.id == submission_id)
            .with_for_update()
        ).mappings().one_or_none()
    external_rows = (
        session.execute(
            select(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.__table__.c.step_id.in_(step_ids))
            .order_by(
                ExternalSubmissionRow.__table__.c.step_id,
                ExternalSubmissionRow.__table__.c.id,
            )
            .with_for_update()
        ).mappings().all()
        if step_ids
        else []
    )
    group_rows = (
        session.execute(
            select(ExecutionAttemptGroupRow.__table__)
            .where(ExecutionAttemptGroupRow.__table__.c.unit_id.in_(unit_ids))
            .order_by(ExecutionAttemptGroupRow.__table__.c.unit_id)
            .with_for_update()
        ).mappings().all()
        if unit_ids
        else []
    )
    event_rows = (
        session.execute(
            select(PolicyStopEventRow.__table__)
            .where(PolicyStopEventRow.__table__.c.unit_id.in_(unit_ids))
            .order_by(PolicyStopEventRow.__table__.c.event_id)
            .with_for_update()
        ).mappings().all()
        if unit_ids
        else []
    )
    return bool(
        task_hint
        or any(
            row.get("step_graph_version") == HAOAI_ATTEMPT_GROUP.registry_version
            for row in unit_rows
        )
        or any(
            row.get("group_key") == HAOAI_ATTEMPT_GROUP.group_key
            or row.get("registry_version") == HAOAI_ATTEMPT_GROUP.registry_version
            for row in group_rows
        )
        or any(
            row.get("group_key") == HAOAI_ATTEMPT_GROUP.group_key
            or row.get("policy_version") == HAOAI_ATTEMPT_GROUP.stop_policy_version
            or row.get("reason") == "auth_unavailable_12"
            for row in event_rows
        )
    )


def _lock_wallet(session: Session, owner_id: str) -> Mapping[str, object] | None:
    return session.execute(
        select(UserCreditRow.__table__)
        .where(UserCreditRow.__table__.c.user_id == owner_id)
        .with_for_update()
    ).mappings().one_or_none()


def _set_read_committed(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))


def _aware_or_none(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_or_none(value).replace(tzinfo=None)


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 36


__all__ = ("SqlAlchemyHaoaiGroupCancellation",)
