"""Bounded recovery for registered optimize controls and provider observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, RLock
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    OwnerTaskScope,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
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
from haoai_backend.generation_core.frame_optimize_progress import (
    lock_frame_optimize_progress,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    VerifiedFrameOptimizeExecutionBatch,
    VerifiedFrameOptimizeExecutionItem,
)

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
    """Persist unselected observations and keep expired intents in reconciliation."""

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
        if type(scope) is not OwnerTaskScope or scope.operation != _OPERATION or _stopping(stop_signal):
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
                    progress = lock_frame_optimize_progress(session, graph)
                    if progress is None or progress.cancellation_marker is not None:
                        return "pending"
                    if graph.task.get("status") in {"completed", "failed"}:
                        return "settled"
                    lease = _utc(graph.task.get("lease_until"))
                    if lease is not None and lease > now:
                        return "pending"
                    due = any(
                        _utc(row.get("deadline_at")) is not None
                        and _utc(row.get("deadline_at")) <= now
                        and row.get("submission_status") in {"submitting", "unknown"}
                        for row in graph.external_submissions
                    )
                    if not due:
                        return "pending"
                    if _stopping(stop_signal):
                        return "pending"
                    table = AITaskRow.__table__
                    session.execute(
                        update(table)
                        .where(
                            table.c.id == scope.task_id,
                            table.c.user_id == scope.user_id,
                            table.c.type == _TASK_TYPE,
                            table.c.status == graph.task.get("status"),
                            table.c.execution_generation == graph.task.get("execution_generation"),
                            table.c.claim_token == graph.task.get("claim_token"),
                            table.c.lease_until == graph.task.get("lease_until"),
                            table.c.user_cancelled_at.is_(None),
                        )
                        .values(
                            status="reconciling",
                            claimed_by=None,
                            claim_token=None,
                            lease_until=None,
                            progress_message="分镜优化结果超过核验期限，等待人工核验",
                            updated_at=now,
                        )
                    )
                    return "pending"
        except Exception:
            return "pending"

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


def _stopping(signal: Event | None) -> bool:
    return signal is not None and signal.is_set()


def _utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _aware_utc(value):
    normalized = _utc(value)
    if normalized is None:
        raise ValueError("database clock must return an aware datetime")
    return normalized


__all__ = ("SqlAlchemyFrameOptimizeRecovery",)
