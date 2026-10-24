"""Bounded recovery for registered optimize controls and provider observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, RLock
from typing import Literal

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    OwnerTaskScope,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.frame_optimize_cancellation import (
    SqlAlchemyFrameOptimizeCancellation,
)
from haoai_backend.generation_core.frame_optimize_execution import (
    intent_row_matches,
    lock_frame_optimize_batch,
    valid_intent_projection,
)
from haoai_backend.generation_core.frame_optimize_due_facts import (
    build_due_fact_values,
    due_fact_fingerprint,
)
from haoai_backend.generation_core.frame_optimize_progress import (
    expected_failure_reason,
    lock_frame_optimize_progress,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    VerifiedFrameOptimizeExecutionBatch,
    VerifiedFrameOptimizeExecutionItem,
)
from haoai_backend.generation_core.schema import ExecutionAttemptGroupRow, PolicyStopEventRow

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
RecoveryOutcome = Literal["settled", "retained", "pending", "terminal_projection"]

_OPERATION = "frame.optimize"
_TASK_TYPE = "batch-optimize"
_OBSERVATION_PREFIX = "frame-optimize-observation-v1:"
_SAFE_CODES = frozenset(
    {
        "configuration_unavailable",
        "invalid_frozen_snapshot",
        "provider_rejected",
        "provider_outcome_unknown",
        "provider_empty_result",
        "provider_transport_unavailable",
        "frozen_execution_mismatch",
        "wrong_execution_context",
    }
)
_MAX_PAGE_SIZE = 32


@dataclass(slots=True)
class SqlAlchemyFrameOptimizeRecovery:
    """Persist provider observations and project expired intents from durable facts."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    cancellation: SqlAlchemyFrameOptimizeCancellation
    page_size: int = _MAX_PAGE_SIZE
    _cursor: tuple[datetime, str] | None = None
    _scan_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not callable(self.session_factory)
            or not callable(self.db_clock)
            or not callable(self.id_factory)
            or type(self.crypto) is not CredentialCrypto
            or type(self.proof_authority) is not HmacProofAuthority
            or not isinstance(self.cancellation, SqlAlchemyFrameOptimizeCancellation)
            or type(self.page_size) is not int
            or not 1 <= self.page_size <= _MAX_PAGE_SIZE
        ):
            raise ValueError("frame.optimize recovery dependencies must be explicit")

    def record_observation(
        self,
        claim: ClaimAuthority | None,
        intent: CommittedIntent,
        verified_batch: VerifiedFrameOptimizeExecutionBatch,
        verified_item: VerifiedFrameOptimizeExecutionItem,
        *,
        code: str,
        request_started: bool,
        http_status: int | None,
        observed_text: str | None = None,
    ) -> Literal["pending", "ownership_lost"]:
        """Store a bounded result observation without granting receipt or phase authority."""
        if (
            type(intent) is not CommittedIntent
            or type(verified_batch) is not VerifiedFrameOptimizeExecutionBatch
            or type(verified_item) is not VerifiedFrameOptimizeExecutionItem
            or verified_item.unit_id == ""
        ):
            return "ownership_lost"
        safe_code = code if code in _SAFE_CODES else "provider_outcome_unknown"
        if observed_text is not None and (
            not isinstance(observed_text, str)
            or len(observed_text.encode("utf-8", errors="ignore")) > 8 * 1024 * 1024
        ):
            observed_text = None
        payload: dict[str, object] = {
            "code": safe_code,
            "request_started": bool(request_started),
            "http_status": http_status if type(http_status) is int and 100 <= http_status <= 599 else None,
            "observed_text": observed_text,
        }
        payload_text = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        payload_digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        try:
            evidence_id = self.id_factory()
        except Exception:
            return "pending"
        if not isinstance(evidence_id, str) or not evidence_id or len(evidence_id) > 36:
            return "pending"
        evidence_key = f"{_OBSERVATION_PREFIX}{intent.external_submission_id}:{payload_digest}"
        confirmation_evidence_id = evidence_id
        try:
            with self.session_factory() as session:
                with session.begin():
                    owner_id = _owner_from_batch(session, verified_batch)
                    if owner_id is None:
                        return "pending"
                    graph = lock_frame_optimize_batch(
                        session,
                        owner_id,
                        verified_batch.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    if (
                        graph is None
                        or graph.verified != verified_batch
                        or not valid_intent_projection(graph)
                    ):
                        return "pending"
                    progress = lock_frame_optimize_progress(session, graph)
                    if progress is None or verified_item.unit_id in progress.receipts_by_unit:
                        return "pending"
                    step = graph.steps_by_unit.get(verified_item.unit_id)
                    unit = graph.units_by_id.get(verified_item.unit_id)
                    external = graph.external_by_step.get(intent.step_id)
                    if (
                        step is None
                        or unit is None
                        or external is None
                        or graph.items_by_unit.get(verified_item.unit_id) != verified_item
                        or not intent_row_matches(external, graph, unit, step)
                        or external.get("id") != intent.external_submission_id
                        or intent.step_id != verified_item.step_id
                        or intent.attempt_id != verified_item.item_id
                    ):
                        return "pending"
                    table = ResultEvidenceRow.__table__
                    existing = session.execute(
                        select(table)
                        .where(table.c.evidence_key == evidence_key)
                        .with_for_update()
                    ).mappings().one_or_none()
                    if existing is not None:
                        if not _evidence_matches(existing, verified_item, intent, payload_digest, payload_text):
                            return "pending"
                        confirmation_evidence_id = existing["id"]
                    now = _aware_utc(self.db_clock())
                    if existing is None:
                        session.execute(
                            table.insert().values(
                                id=evidence_id,
                                step_id=intent.step_id,
                                submission_id=intent.external_submission_id,
                                evidence_key=evidence_key,
                                source="worker",
                                evidence_kind="frame_optimize_observation_v1",
                                payload_digest=payload_digest,
                                result_location=payload_text,
                                result_digest=(
                                    hashlib.sha256(observed_text.encode("utf-8")).hexdigest()
                                    if observed_text is not None
                                    else None
                                ),
                                received_at=now,
                                validity="unknown",
                                application_status="pending",
                                applied_at=None,
                            )
                        )
                    if claim is not None and _claim_live(graph.task, claim, now):
                        task = AITaskRow.__table__
                        session.execute(
                            update(task)
                            .where(
                                task.c.id == claim.scope.task_id,
                                task.c.user_id == claim.scope.user_id,
                                task.c.type == _TASK_TYPE,
                                task.c.status == "processing",
                                task.c.claimed_by == claim.worker_instance,
                                task.c.claim_token == claim.token,
                                task.c.execution_generation == claim.execution_generation,
                                task.c.lease_until == graph.task.get("lease_until"),
                                task.c.user_cancelled_at.is_(None),
                            )
                            .values(
                                progress_message="分镜优化结果需要核验",
                                updated_at=now,
                            )
                        )
            return "pending"
        except Exception:
            self._confirm_observation(
                evidence_key,
                confirmation_evidence_id,
                verified_item,
                intent,
                payload_digest,
                payload_text,
            )
            return "pending"

    def scan_due(
        self, *, stop_signal: Event | None = None
    ) -> tuple[RecoveryOutcome, ...]:
        if _stopping(stop_signal):
            return ()
        now = self._now()
        if now is None:
            return ("pending",)
        with self._scan_lock:
            candidates = self._next_page(now)
            outcomes: list[RecoveryOutcome] = []
            for scope in candidates:
                if _stopping(stop_signal):
                    break
                outcomes.append(self.consume_cancel_and_due(scope, stop_signal=stop_signal))
            return tuple(outcomes)

    def consume_cancel_and_due(
        self,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> RecoveryOutcome:
        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != _OPERATION
            or _stopping(stop_signal)
        ):
            return "pending"
        cancelled = self.cancellation.consume_persisted(scope, stop_signal=stop_signal)
        if cancelled == "settled":
            return "settled"
        if cancelled == "retained":
            return "retained"
        if cancelled != "not_selected":
            return "pending"
        return self._project_due(scope, stop_signal)

    def _project_due(
        self, scope: OwnerTaskScope, stop_signal: Event | None
    ) -> RecoveryOutcome:
        if _stopping(stop_signal):
            return "pending"
        expected_due_facts: dict[str, Mapping[str, object]] = {}
        expected_projection: dict[str, object] | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_frame_optimize_batch(
                        session,
                        scope.user_id,
                        scope.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    now = self._now()
                    if graph is None or now is None or not valid_intent_projection(graph):
                        return "pending"
                    if graph.task.get("status") in {"completed", "failed"}:
                        return "settled"
                    if (
                        graph.task.get("status") not in {"queued", "processing", "reconciling"}
                        or graph.task.get("user_cancelled_at") is not None
                        or graph.task.get("cancellation_reason") is not None
                    ):
                        return "pending"
                    if not _lock_no_registered_groups_or_events(session, graph):
                        return "pending"
                    progress = lock_frame_optimize_progress(session, graph)
                    if progress is None or progress.cancellation_marker is not None:
                        return "pending"
                    lease = _utc(graph.task.get("lease_until"))
                    if lease is not None and lease > now:
                        return "pending"
                    due_task_fence = _due_task_fence(graph.task)
                    due_writes = self._due_fact_writes(graph, progress, now)
                    if due_writes is None:
                        return "pending"
                    due_units = set(progress.due_by_unit) | set(due_writes)
                    if not due_units:
                        return "pending"
                    expected_due_facts = {
                        unit_id: dict(fact)
                        for unit_id, fact in progress.due_by_unit.items()
                    }

                    evidence_table = ResultEvidenceRow.__table__
                    for unit_id, values in due_writes.items():
                        if _stopping(stop_signal):
                            raise _RecoveryStopped
                        session.execute(evidence_table.insert().values(**values))
                        expected_due_facts[unit_id] = dict(values)
                        if _stopping(stop_signal):
                            raise _RecoveryStopped

                    for unit_id in due_writes:
                        if _stopping(stop_signal):
                            raise _RecoveryStopped
                        self._mark_unit_due(session, graph, unit_id, now, stop_signal)

                    if _stopping(stop_signal):
                        raise _RecoveryStopped
                    session.flush()

                    fresh_graph = lock_frame_optimize_batch(
                        session,
                        scope.user_id,
                        scope.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    if (
                        fresh_graph is None
                        or not valid_intent_projection(fresh_graph)
                        or not _lock_no_registered_groups_or_events(session, fresh_graph)
                    ):
                        raise _DueWriteConflict
                    fresh_progress = lock_frame_optimize_progress(session, fresh_graph)
                    commit_now = self._now()
                    if (
                        fresh_progress is None
                        or commit_now is None
                        or fresh_progress.cancellation_marker is not None
                        or _due_task_fence(fresh_graph.task) != due_task_fence
                        or _live_lease_at(fresh_graph.task, commit_now)
                        or fresh_graph.task.get("user_cancelled_at") is not None
                        or fresh_graph.task.get("cancellation_reason") is not None
                        or not due_units.issubset(fresh_progress.due_by_unit)
                        or any(
                            due_fact_fingerprint(fresh_progress.due_by_unit[unit_id])
                            != due_fact_fingerprint(expected_due_facts[unit_id])
                            for unit_id in due_units
                        )
                        or any(
                            _utc(fresh_progress.due_by_unit[unit_id].get("received_at"))
                            is None
                            or _utc(fresh_progress.due_by_unit[unit_id].get("received_at"))
                            > commit_now
                            for unit_id in due_units
                        )
                        or any(
                            _utc(fresh_graph.units_by_id[unit_id].get("deadline_at")) is None
                            or _utc(fresh_graph.units_by_id[unit_id].get("deadline_at")) > commit_now
                            for unit_id in due_units
                        )
                    ):
                        raise _DueWriteConflict

                    expected_projection = _due_parent_projection(fresh_graph, fresh_progress)
                    if expected_projection is None:
                        raise _DueWriteConflict
                    if not _due_projection_already_matches(
                        fresh_graph, fresh_progress, expected_projection
                    ):
                        if _stopping(stop_signal):
                            raise _RecoveryStopped
                        task_table = AITaskRow.__table__
                        old_task = fresh_graph.task
                        updated = session.execute(
                            update(task_table)
                            .where(
                                task_table.c.id == scope.task_id,
                                task_table.c.user_id == scope.user_id,
                                task_table.c.type == _TASK_TYPE,
                                task_table.c.status == old_task.get("status"),
                                task_table.c.execution_generation
                                == old_task.get("execution_generation"),
                                _nullable_equal(task_table.c.claimed_by, old_task.get("claimed_by")),
                                _nullable_equal(task_table.c.claim_token, old_task.get("claim_token")),
                                _nullable_equal(task_table.c.lease_until, old_task.get("lease_until")),
                                task_table.c.user_cancelled_at.is_(None),
                                task_table.c.cancellation_reason.is_(None),
                                task_table.c.progress == old_task.get("progress"),
                                task_table.c.billing_status == old_task.get("billing_status"),
                                task_table.c.credit_cost == old_task.get("credit_cost"),
                                task_table.c.recovery_status == old_task.get("recovery_status"),
                                _nullable_equal(task_table.c.result, old_task.get("result")),
                            )
                            .values(**expected_projection, updated_at=_db_time(commit_now))
                        )
                        if updated.rowcount != 1:
                            raise _DueWriteConflict
                        if _stopping(stop_signal):
                            raise _RecoveryStopped

                    final_graph = lock_frame_optimize_batch(
                        session,
                        scope.user_id,
                        scope.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    if (
                        final_graph is None
                        or not valid_intent_projection(final_graph)
                        or not _lock_no_registered_groups_or_events(session, final_graph)
                    ):
                        raise _DueWriteConflict
                    final_progress = lock_frame_optimize_progress(session, final_graph)
                    if (
                        final_progress is None
                        or not _due_projection_already_matches(
                            final_graph, final_progress, expected_projection
                        )
                        or _stopping(stop_signal)
                    ):
                        raise _DueWriteConflict
                    outcome: RecoveryOutcome = (
                        "terminal_projection"
                        if final_graph.task.get("status") in {"completed", "failed"}
                        else "retained"
                    )
            return outcome
        except _RecoveryStopped:
            return "pending"
        except Exception:
            if expected_due_facts and self._confirm_due(
                scope, expected_due_facts, expected_projection
            ):
                return (
                    "terminal_projection"
                    if expected_projection is not None
                    and expected_projection.get("status") in {"completed", "failed"}
                    else "retained"
                )
            return "pending"

    def _due_fact_writes(self, graph, progress, now) -> dict[str, dict[str, object]] | None:
        writes: dict[str, dict[str, object]] = {}
        units = graph.units_by_id
        steps = graph.steps_by_unit
        for external in graph.external_submissions:
            step_id = str(external.get("step_id", ""))
            unit_id = next(
                (candidate for candidate, step in steps.items() if step.get("id") == step_id),
                "",
            )
            unit = units.get(unit_id)
            step = steps.get(unit_id)
            deadline = _utc(external.get("deadline_at"))
            if (
                unit is None
                or step is None
                or deadline is None
                or deadline > now
                or external.get("submission_status") not in {"submitting", "unknown"}
            ):
                continue
            if unit_id in progress.due_by_unit:
                continue
            if (
                unit_id in progress.receipts_by_unit
                or unit_id in progress.works_by_unit
                or unit_id in progress.fees_by_unit
                or unit_id in progress.ledgers_by_unit
                or unit_id in progress.applications_by_unit
                or unit_id in progress.unstarted_insufficient_by_unit
                or unit_id in progress.unstarted_cancelled_by_unit
                or unit.get("result_status") != "pending"
                or unit.get("billing_status") != "unbilled"
                or unit.get("debited_amount") != 0
                or unit.get("refunded_amount") != 0
                or unit.get("success_confirmed_at") is not None
                or unit.get("user_cancelled_at") is not None
                or unit.get("cancellation_reason") is not None
                or step.get("execution_status")
                not in {"submitting", "submitted", "reconciling"}
            ):
                return None
            try:
                evidence_id = self.id_factory()
            except Exception:
                return None
            values = build_due_fact_values(graph, unit_id, evidence_id, now)
            if values is None:
                return None
            writes[unit_id] = values
        return writes

    def _mark_unit_due(self, session, graph, unit_id, now, stop_signal) -> None:
        unit = graph.units_by_id[unit_id]
        step = graph.steps_by_unit[unit_id]
        external = graph.external_by_step[str(step["id"])]
        unit_table = BillingUnitRow.__table__
        changed = session.execute(
            update(unit_table)
            .where(
                unit_table.c.id == unit_id,
                unit_table.c.task_id == graph.task.get("id"),
                unit_table.c.result_status == "pending",
                unit_table.c.billing_status == "unbilled",
                unit_table.c.debited_amount == 0,
                unit_table.c.refunded_amount == 0,
                _nullable_equal(unit_table.c.first_intent_at, unit.get("first_intent_at")),
                _nullable_equal(unit_table.c.deadline_at, unit.get("deadline_at")),
                unit_table.c.success_confirmed_at.is_(None),
                unit_table.c.user_cancelled_at.is_(None),
                unit_table.c.cancellation_reason.is_(None),
            )
            .values(result_status="unknown", updated_at=_db_time(now))
        )
        if changed.rowcount != 1:
            raise _DueWriteConflict
        if _stopping(stop_signal):
            raise _RecoveryStopped

        step_table = ExecutionStepRow.__table__
        if step.get("execution_status") != "reconciling":
            changed = session.execute(
                update(step_table)
                .where(
                    step_table.c.id == step.get("id"),
                    step_table.c.billing_unit_id == unit_id,
                    step_table.c.execution_status == step.get("execution_status"),
                    step_table.c.input_digest == step.get("input_digest"),
                )
                .values(execution_status="reconciling", updated_at=_db_time(now))
            )
            if changed.rowcount != 1:
                raise _DueWriteConflict
            if _stopping(stop_signal):
                raise _RecoveryStopped

        if external.get("submission_status") != "unknown":
            external_table = ExternalSubmissionRow.__table__
            changed = session.execute(
                update(external_table)
                .where(
                    external_table.c.id == external.get("id"),
                    external_table.c.step_id == step.get("id"),
                    external_table.c.submission_key == external.get("submission_key"),
                    external_table.c.request_digest == external.get("request_digest"),
                    external_table.c.deadline_at == _db_time(external.get("deadline_at")),
                    external_table.c.submission_status == external.get("submission_status"),
                )
                .values(submission_status="unknown", updated_at=_db_time(now))
            )
            if changed.rowcount != 1:
                raise _DueWriteConflict
            if _stopping(stop_signal):
                raise _RecoveryStopped

    def _confirm_due(self, scope, expected_due_facts, expected_projection) -> bool:
        if expected_projection is None:
            return False
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_frame_optimize_batch(
                        session,
                        scope.user_id,
                        scope.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    if (
                        graph is None
                        or not valid_intent_projection(graph)
                        or not _lock_no_registered_groups_or_events(session, graph)
                    ):
                        return False
                    progress = lock_frame_optimize_progress(session, graph)
                    if progress is None:
                        return False
                    return bool(
                        all(
                            unit_id in progress.due_by_unit
                            and due_fact_fingerprint(progress.due_by_unit[unit_id])
                            == due_fact_fingerprint(expected_fact)
                            for unit_id, expected_fact in expected_due_facts.items()
                        )
                        and _due_projection_already_matches(graph, progress, expected_projection)
                    )
        except Exception:
            return False

    def _next_page(self, now: datetime) -> list[OwnerTaskScope]:
        table = AITaskRow.__table__
        conditions = [
            table.c.type == _TASK_TYPE,
            table.c.status.in_(["queued", "processing", "reconciling"]),
            table.c.user_cancelled_at.is_(None),
        ]
        if self._cursor is not None:
            created_at, task_id = self._cursor
            conditions.append(
                (table.c.created_at > created_at)
                | ((table.c.created_at == created_at) & (table.c.id > task_id))
            )
        with self.session_factory() as session:
            rows = session.execute(
                select(table.c.user_id, table.c.id, table.c.created_at)
                .where(*conditions)
                .order_by(table.c.created_at, table.c.id)
                .limit(self.page_size)
            ).all()
        if len(rows) < self.page_size and self._cursor is not None:
            with self.session_factory() as session:
                wrapped = session.execute(
                    select(table.c.user_id, table.c.id, table.c.created_at)
                    .where(
                        table.c.type == _TASK_TYPE,
                        table.c.status.in_(["queued", "processing", "reconciling"]),
                        table.c.user_cancelled_at.is_(None),
                    )
                    .order_by(table.c.created_at, table.c.id)
                    .limit(self.page_size - len(rows))
                ).all()
            seen = {row.id for row in rows}
            rows.extend(row for row in wrapped if row.id not in seen)
        if rows:
            last = rows[-1]
            self._cursor = (_aware_utc(last.created_at), str(last.id))
        else:
            self._cursor = None
        return [
            OwnerTaskScope(user_id=str(row.user_id), task_id=str(row.id), operation=_OPERATION)
            for row in rows
        ]

    def _confirm_observation(
        self, evidence_key, evidence_id, item, intent, payload_digest, payload_text
    ) -> bool:
        try:
            with self.session_factory() as session:
                row = session.execute(
                    select(ResultEvidenceRow.__table__).where(
                        ResultEvidenceRow.__table__.c.evidence_key == evidence_key,
                        ResultEvidenceRow.__table__.c.id == evidence_id,
                    )
                ).mappings().one_or_none()
            return bool(row and _evidence_matches(row, item, intent, payload_digest, payload_text))
        except Exception:
            return False

    def _now(self) -> datetime | None:
        try:
            return _aware_utc(self.db_clock())
        except Exception:
            return None


def _owner_from_batch(session: Session, batch: VerifiedFrameOptimizeExecutionBatch) -> str | None:
    return session.execute(
        select(AITaskRow.user_id).where(
            AITaskRow.id == batch.task_id,
            AITaskRow.type == _TASK_TYPE,
        )
    ).scalar_one_or_none()


def _evidence_matches(row, item, intent, digest, payload_text) -> bool:
    return bool(
        row.get("step_id") == intent.step_id
        and row.get("submission_id") == intent.external_submission_id
        and row.get("evidence_key", "").startswith(
            f"{_OBSERVATION_PREFIX}{intent.external_submission_id}:"
        )
        and row.get("source") == "worker"
        and row.get("evidence_kind") == "frame_optimize_observation_v1"
        and row.get("payload_digest") == digest
        and row.get("result_location") == payload_text
        and row.get("validity") == "unknown"
        and row.get("application_status") == "pending"
        and item.step_id == intent.step_id
    )


def _claim_live(task, claim, now) -> bool:
    return bool(
        task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
        and task.get("status") == "processing"
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and _utc(task.get("lease_until")) is not None
        and _utc(task.get("lease_until")) > _utc(now)
        and not claim.local_lost_signal.is_set()
    )


def _due_task_fence(task) -> tuple[object, ...]:
    return (
        task.get("status"),
        task.get("execution_generation"),
        task.get("claimed_by"),
        task.get("claim_token"),
        _utc(task.get("lease_until")),
    )


def _live_lease_at(task, now: datetime) -> bool:
    raw_lease = task.get("lease_until")
    lease = _utc(raw_lease)
    return raw_lease is not None and (lease is None or lease > now)


def _stopping(signal: Event | None) -> bool:
    return signal is not None and signal.is_set()


def _utc(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aware_utc(value):
    normalized = _utc(value)
    if normalized is None:
        raise ValueError("database clock must return an aware datetime")
    return normalized


def _lock_no_registered_groups_or_events(session: Session, graph) -> bool:
    unit_ids = tuple(sorted(graph.units_by_id))
    if not unit_ids:
        return False
    groups = session.execute(
        select(ExecutionAttemptGroupRow.__table__)
        .where(ExecutionAttemptGroupRow.__table__.c.unit_id.in_(unit_ids))
        .order_by(
            ExecutionAttemptGroupRow.__table__.c.unit_id,
            ExecutionAttemptGroupRow.__table__.c.group_key,
        )
        .with_for_update()
    ).mappings().all()
    events = session.execute(
        select(PolicyStopEventRow.__table__)
        .where(
            or_(
                PolicyStopEventRow.__table__.c.task_id == graph.task.get("id"),
                PolicyStopEventRow.__table__.c.unit_id.in_(unit_ids),
            )
        )
        .order_by(PolicyStopEventRow.__table__.c.event_id)
        .with_for_update()
    ).mappings().all()
    return not groups and not events


def _due_parent_projection(graph, progress) -> dict[str, object] | None:
    if not progress.due_by_unit:
        return None
    if progress.all_slots_closed:
        if progress.applied_by_unit:
            if progress.terminal_error is not None:
                return None
            result = json.dumps(
                [
                    {"index": index, "optimized_text": text}
                    for index, text in progress.partial_results
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            status = "completed"
            progress_message = f"分镜优化完成（已应用 {len(progress.applied_by_unit)} 项）"
        else:
            failure_reason = expected_failure_reason(
                progress.works_by_unit,
                progress.unstarted_insufficient_by_unit,
                progress.due_by_unit,
            )
            if failure_reason is None:
                return None
            result = json.dumps(
                {"partial": [], "error": failure_reason},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            status = "failed"
            progress_message = failure_reason
        return {
            "status": status,
            "claimed_by": None,
            "claim_token": None,
            "lease_until": None,
            "progress": 100,
            "progress_message": progress_message,
            "result": result,
        }

    return {
        "status": "reconciling",
        "claimed_by": None,
        "claim_token": None,
        "lease_until": None,
        "progress": graph.task.get("progress"),
        "progress_message": "分镜结果超过原核验期限；未确认镜头保持未知，其他镜头仍可继续",
        "result": graph.task.get("result"),
    }


def _due_projection_already_matches(graph, progress, expected) -> bool:
    task = graph.task
    if (
        not progress.due_by_unit
        or task.get("status") != expected.get("status")
        or task.get("claimed_by") is not None
        or task.get("claim_token") is not None
        or task.get("lease_until") is not None
        or task.get("progress") != expected.get("progress")
        or task.get("progress_message") != expected.get("progress_message")
        or task.get("result") != expected.get("result")
    ):
        return False
    if expected.get("status") == "completed":
        return bool(
            progress.all_slots_closed
            and progress.applied_by_unit
            and progress.terminal_error is None
        )
    if expected.get("status") == "failed":
        failure_reason = expected_failure_reason(
            progress.works_by_unit,
            progress.unstarted_insufficient_by_unit,
            progress.due_by_unit,
        )
        return bool(
            progress.all_slots_closed
            and not progress.applied_by_unit
            and failure_reason is not None
            and progress.terminal_error == failure_reason
        )
    return bool(expected.get("status") == "reconciling" and not progress.all_slots_closed)


def _nullable_equal(column, value):
    return column.is_(None) if value is None else column == value


def _db_time(value: object) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


class _RecoveryStopped(RuntimeError):
    pass


class _DueWriteConflict(RuntimeError):
    pass


__all__ = ("SqlAlchemyFrameOptimizeRecovery",)
