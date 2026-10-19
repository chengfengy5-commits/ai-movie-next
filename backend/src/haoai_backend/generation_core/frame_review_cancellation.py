"""Owner-scoped SQL settlement for registered frame.review cancellation."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Callable, Literal

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import (
    LocalContinuationWork,
    UnitFeeDecision,
)
from haoai_backend.generation_contracts.types import OwnerTaskScope
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    TaskSubmissionRow,
    UserRow,
)
from haoai_backend.generation_core.frame_review_results import decode_inline_text_result
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    ExecutionAttemptGroupRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    PolicyStopEventRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.frame_review_snapshot_reader import (
    PersistedFrameReviewExecutionBinding,
    VerifiedFrameReviewExecutionInput,
    read_original_frame_review_execution_input,
)
from haoai_backend.generation_runtime.registry import LOCAL_CONTINUATION_POLICY

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
CancelOutcome = Literal[
    "settled", "retained", "pending", "not_found", "not_selected",
    "terminal_projection",
]

_TASK_TYPE = "ai-review"
_OPERATION = "frame.review"
_GRAPH_VERSION = "frame-review-v1"
_STEP_KEY = "generate"
_INTENT_KEY_PREFIX = "frame-review-generate-v1:"
_CANCEL_MARKER_PREFIX = "user_cancelled:v1:"
_CANCEL_MARKER_PATTERN = re.compile(r"^user_cancelled:v1:[0-9a-f]{32}$")
_CANCELLED_MESSAGE = "用户已取消本次分镜复核。"
_DEADLINE_SECONDS = 7200
_PHASE_ORDER = ("delivery_pending", "fee_pending", "finalize_pending")
_ACTIVE_TASK_STATUSES = {"queued", "processing", "reconciling", "cancelling"}
_TERMINAL_WORK_PHASES = {
    "done", "stopped_cancelled", "stopped_insufficient",
    "stopped_delivery_failed", "stopped_late_generation",
}


@dataclass(frozen=True, slots=True)
class _CancellationGraph:
    task: dict[str, object]
    unit: dict[str, object]
    step: dict[str, object]
    parent: dict[str, object]
    request_data: dict[str, object]
    external: dict[str, object] | None
    binding: PersistedFrameReviewExecutionBinding
    verified: VerifiedFrameReviewExecutionInput | None
    evidences: tuple[dict[str, object], ...]
    receipts: tuple[dict[str, object], ...]
    works: tuple[dict[str, object], ...]
    applications: tuple[dict[str, object], ...]
    fee: dict[str, object] | None
    ledgers: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _FinancialPlan:
    actual_cost: int
    billing_status: str
    fee_to_create: UnitFeeDecision | None


class _CancelWriteConflict(RuntimeError):
    """Abort the full transaction when any compare-and-set or stage rejects."""


class _CancelStopped(RuntimeError):
    """Abort the full transaction when runtime shutdown requests a stop."""


@dataclass(slots=True)
class SqlAlchemyFrameReviewCancellation:
    """Own the SQL transaction for an authenticated or persisted cancel request."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: Callable[[], str]
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority

    def __post_init__(self) -> None:
        if (
            not callable(self.session_factory)
            or not callable(self.db_clock)
            or not callable(self.id_factory)
            or type(self.crypto) is not CredentialCrypto
            or type(self.proof_authority) is not HmacProofAuthority
        ):
            raise ValueError("frame.review cancellation dependencies must be explicit")

    def cancel_owned(
        self,
        owner_id: str,
        task_id: str,
        *,
        stop_signal: Event | None = None,
    ) -> CancelOutcome:
        """Cancel after the caller has obtained an active TrustedActor."""

        marker = _CANCEL_MARKER_PREFIX + secrets.token_hex(16)
        return self._run(owner_id, task_id, marker, source="owner", stop_signal=stop_signal)

    def consume_persisted(
        self,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> CancelOutcome:
        """Settle only a cancellation already represented by locked SQL state."""

        if type(scope) is not OwnerTaskScope or scope.operation != _OPERATION:
            return "pending"
        marker = _CANCEL_MARKER_PREFIX + secrets.token_hex(16)
        return self._run(
            scope.user_id,
            scope.task_id,
            marker,
            source="persisted",
            stop_signal=stop_signal,
        )

    def _run(
        self,
        owner_id: str,
        task_id: str,
        marker: str,
        *,
        source: Literal["owner", "persisted", "confirm"],
        stop_signal: Event | None,
    ) -> CancelOutcome:
        outcome: CancelOutcome = "pending"
        try:
            with self.session_factory() as session:
                with session.begin():
                    outcome = self._stage(
                        session,
                        owner_id,
                        task_id,
                        marker,
                        source=source,
                        stop_signal=stop_signal,
                    )
            return outcome
        except _CancelStopped:
            return "pending"
        except Exception:
            if source in {"owner", "persisted"} and self._confirm_marker(owner_id, task_id, marker):
                return "settled"
            return "pending"

    def _confirm_marker(self, owner_id: str, task_id: str, marker: str) -> bool:
        if not _is_cancel_marker(marker):
            return False
        try:
            with self.session_factory() as session:
                with session.begin():
                    outcome = self._stage(
                        session,
                        owner_id,
                        task_id,
                        marker,
                        source="confirm",
                        stop_signal=None,
                    )
            return outcome == "retained"
        except Exception:
            return False

    def _stage(
        self,
        session: Session,
        owner_id: str,
        task_id: str,
        marker: str,
        *,
        source: Literal["owner", "persisted", "confirm"],
        stop_signal: Event | None,
    ) -> CancelOutcome:
        if _is_stopping(stop_signal):
            return "pending"
        status, graph = self._lock_graph(
            session, owner_id, task_id, stop_signal=stop_signal
        )
        if status != "loaded" or graph is None:
            return status
        task = graph.task

        existing_marker = task.get("cancellation_reason")
        task_cancelled_at = task.get("user_cancelled_at")
        if existing_marker is not None or task_cancelled_at is not None:
            if (
                not _is_cancel_marker(existing_marker)
                or task_cancelled_at is None
                or graph.unit.get("cancellation_reason") != existing_marker
                or not _same_time(
                    graph.unit.get("user_cancelled_at"), task_cancelled_at
                )
            ):
                return "pending"
            if source == "confirm" and existing_marker != marker:
                return "pending"
            return "retained" if self._cancelled_projection_matches(graph) else "pending"

        if source == "confirm":
            return "pending"
        if source == "persisted" and task.get("status") != "cancelling":
            return "not_selected"
        if task.get("status") == "completed":
            return "terminal_projection" if self._completed_projection_matches(graph) else "pending"
        if task.get("status") == "failed":
            return "retained" if self._failed_projection_matches(graph) else "pending"
        if task.get("status") not in _ACTIVE_TASK_STATUSES:
            return "pending"
        if not _is_cancel_marker(marker):
            return "pending"

        now = self._now()
        if now is None or not self._active_graph_matches(graph):
            return "pending"
        plan = self._financial_plan(graph, now)
        if plan is None:
            return "pending"
        work_to_stop = self._work_to_stop(graph)
        if work_to_stop is False:
            return "pending"
        if _is_stopping(stop_signal):
            return "pending"

        if plan.fee_to_create is not None:
            staged_fee = GenerationRecordRepository(session).stage_record(plan.fee_to_create)
            if staged_fee.status != "created":
                raise _CancelWriteConflict
            if _is_stopping(stop_signal):
                raise _CancelStopped
        self._mark_unit(session, graph, marker, now, plan)
        if _is_stopping(stop_signal):
            raise _CancelStopped
        self._mark_task(session, graph, marker, now)
        if _is_stopping(stop_signal):
            raise _CancelStopped
        if work_to_stop is not None:
            self._stop_work(session, work_to_stop, graph, plan)
        if _is_stopping(stop_signal):
            raise _CancelStopped
        self._revoke_task_fence(session, graph, marker, now, plan)
        if _is_stopping(stop_signal):
            raise _CancelStopped
        return "settled"

    def _lock_graph(
        self,
        session: Session,
        owner_id: str,
        task_id: str,
        *,
        stop_signal: Event | None = None,
    ) -> tuple[CancelOutcome | Literal["loaded"], _CancellationGraph | None]:
        user = session.execute(
            select(UserRow.id).where(UserRow.id == owner_id).with_for_update()
        ).scalar_one_or_none()
        if _is_stopping(stop_signal):
            return "pending", None
        if user is None:
            return "not_found", None
        task_row = session.execute(
            select(AITaskRow.__table__)
            .where(AITaskRow.id == task_id, AITaskRow.user_id == owner_id)
            .with_for_update()
        ).mappings().one_or_none()
        if _is_stopping(stop_signal):
            return "pending", None
        if task_row is None:
            return "not_found", None
        task = dict(task_row)
        units = session.execute(
            select(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.task_id == task_id)
            .order_by(BillingUnitRow.__table__.c.id)
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None
        if not _selected_candidate(task, units):
            return "not_selected", None
        if task.get("type") != _TASK_TYPE or len(units) != 1:
            return "pending", None
        unit = dict(units[0])
        steps = session.execute(
            select(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.billing_unit_id == unit["id"])
            .order_by(ExecutionStepRow.__table__.c.step_order, ExecutionStepRow.__table__.c.id)
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None
        if len(steps) != 1:
            return "pending", None
        step = dict(steps[0])
        externals = session.execute(
            select(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.__table__.c.step_id == step["id"])
            .order_by(ExternalSubmissionRow.__table__.c.id)
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None
        if len(externals) > 1:
            return "pending", None
        external = dict(externals[0]) if externals else None
        request_data = _json_object(task.get("request_data"))
        parent_id = request_data.get("submission_id") if request_data else None
        if not isinstance(parent_id, str) or not parent_id:
            return "pending", None
        parent_row = session.execute(
            select(TaskSubmissionRow.__table__)
            .where(
                TaskSubmissionRow.id == parent_id,
                TaskSubmissionRow.user_id == owner_id,
                TaskSubmissionRow.operation == _OPERATION,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if _is_stopping(stop_signal):
            return "pending", None
        if parent_row is None:
            return "pending", None
        parent = dict(parent_row)
        groups = session.execute(
            select(ExecutionAttemptGroupRow.__table__)
            .where(ExecutionAttemptGroupRow.__table__.c.unit_id == unit["id"])
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None
        events = session.execute(
            select(PolicyStopEventRow.__table__)
            .where(PolicyStopEventRow.__table__.c.unit_id == unit["id"])
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None

        # Lock the durable outcome graph in the same order used by continuation.
        evidence_rows = session.execute(
            select(ResultEvidenceRow.__table__)
            .where(ResultEvidenceRow.__table__.c.step_id == step["id"])
            .order_by(ResultEvidenceRow.__table__.c.id)
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None
        receipt_rows = session.execute(
            select(GenerationResultReceiptRow.__table__)
            .where(
                or_(
                    GenerationResultReceiptRow.__table__.c.task_id == task_id,
                    GenerationResultReceiptRow.__table__.c.unit_id == unit["id"],
                )
            )
            .order_by(GenerationResultReceiptRow.__table__.c.result_ref)
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None
        work_rows = session.execute(
            select(LocalContinuationWorkRow.__table__)
            .where(
                or_(
                    LocalContinuationWorkRow.__table__.c.task_id == task_id,
                    LocalContinuationWorkRow.__table__.c.unit_id == unit["id"],
                )
            )
            .order_by(LocalContinuationWorkRow.__table__.c.work_id)
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None
        result_refs = tuple(row["result_ref"] for row in receipt_rows)
        application_filter = [
            ApplicationRecordRow.__table__.c.task_id == task_id,
            ApplicationRecordRow.__table__.c.unit_id == unit["id"],
        ]
        if result_refs:
            application_filter.append(
                ApplicationRecordRow.__table__.c.result_ref.in_(result_refs)
            )
        application_rows = session.execute(
            select(ApplicationRecordRow.__table__)
            .where(or_(*application_filter))
            .order_by(ApplicationRecordRow.__table__.c.application_ref)
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None
        fee_row = session.execute(
            select(UnitFeeDecisionRow.__table__)
            .where(UnitFeeDecisionRow.__table__.c.unit_id == unit["id"])
            .with_for_update()
        ).mappings().one_or_none()
        if _is_stopping(stop_signal):
            return "pending", None
        ledger_rows = session.execute(
            select(CreditLogRow.__table__)
            .where(
                or_(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                    CreditLogRow.__table__.c.business_key == f"debit:{unit['id']}",
                )
            )
            .order_by(CreditLogRow.__table__.c.id)
            .with_for_update()
        ).mappings().all()
        if _is_stopping(stop_signal):
            return "pending", None

        binding = _make_binding(task, unit, step, parent, request_data)
        if binding is None or groups or events:
            return "pending", None
        verified: VerifiedFrameReviewExecutionInput | None = None
        if task.get("status") in _ACTIVE_TASK_STATUSES or task.get("credit_cost") == 0:
            verified = read_original_frame_review_execution_input(
                session,
                binding,
                crypto=self.crypto,
                proof_authority=self.proof_authority,
            )
            if _is_stopping(stop_signal):
                return "pending", None
            if verified is None:
                return "pending", None
        graph = _CancellationGraph(
            task=task,
            unit=unit,
            step=step,
            parent=parent,
            request_data=request_data,
            external=external,
            binding=binding,
            verified=verified,
            evidences=tuple(dict(row) for row in evidence_rows),
            receipts=tuple(dict(row) for row in receipt_rows),
            works=tuple(dict(row) for row in work_rows),
            applications=tuple(dict(row) for row in application_rows),
            fee=None if fee_row is None else dict(fee_row),
            ledgers=tuple(dict(row) for row in ledger_rows),
        )
        return "loaded", graph

    def _external_matches(self, graph: _CancellationGraph) -> bool:
        unit, step, parent, request = (
            graph.unit, graph.step, graph.parent, graph.request_data
        )
        external = graph.external
        if external is None:
            return (
                unit.get("first_intent_at") is None
                and unit.get("deadline_at") is None
                and unit.get("result_status") == "pending"
                and step.get("execution_status") in {"prepared", "ready"}
                and not graph.evidences
            )
        first = _as_utc(external.get("intent_at"))
        deadline = _as_utc(external.get("deadline_at"))
        if first is None or deadline is None:
            return False
        snapshot = step.get("input_snapshot")
        if not isinstance(snapshot, dict):
            return False
        if graph.verified is None:
            # Nonzero-cost tasks are only inspected for an exact terminal no-op.
            return self._completed_external_matches(graph, first, deadline)
        expected_snapshot = {
            "owner_id": graph.task["user_id"],
            "task_id": graph.task["id"],
            "unit_id": unit["id"],
            "step_id": step["id"],
            "submission_id": parent["id"],
            "operation": _OPERATION,
            "graph_version": _GRAPH_VERSION,
            "attempt_id": snapshot.get("server_attempt_id"),
            "prepared_payload_id": snapshot.get("prepared_payload_id"),
            "protected_ref": snapshot.get("protected_ref"),
            "request_digest": request.get("request_digest"),
            "rule_digest": request.get("rule_digest"),
            "snapshot_digest": request.get("snapshot_digest"),
            "manifest_snapshot_digest": request.get("manifest_snapshot_digest"),
            "snapshot_ref": (
                f"{_GRAPH_VERSION}:{graph.binding.prepared_payload_id}:"
                f"{request.get('snapshot_digest')}:{request.get('manifest_snapshot_digest')}"
            ),
            "credential_binding_ref": graph.verified.snapshot.credential_binding_ref,
            "provider": graph.verified.snapshot.model_ref.provider,
            "model_id": graph.verified.snapshot.model_ref.model_id,
        }
        completed = bool(graph.receipts)
        observed_failure = (
            not completed
            and graph.task.get("status") == "failed"
            and graph.unit.get("result_status") == "failed"
        )
        return all(
            (
                external.get("step_id") == step.get("id"),
                external.get("submission_key") == f"{_INTENT_KEY_PREFIX}{unit['id']}:{step['id']}",
                external.get("provider") == graph.verified.snapshot.model_ref.provider,
                external.get("model_ref") == graph.verified.snapshot.model_ref.model_id,
                external.get("request_digest") == graph.binding.request_digest,
                external.get("request_snapshot") == expected_snapshot,
                external.get("external_task_id") is None,
                (
                    _as_utc(external.get("submitted_at")) is not None
                    if completed
                    else external.get("submitted_at") is None
                ),
                (
                    external.get("submission_status") == "completed"
                    if completed
                    else (
                        external.get("submission_status") == "failed"
                        if observed_failure
                        else external.get("submission_status") in {"submitting", "unknown"}
                    )
                ),
                _same_time(external.get("intent_at"), unit.get("first_intent_at")),
                _same_time(external.get("deadline_at"), unit.get("deadline_at")),
                deadline == first + timedelta(seconds=_DEADLINE_SECONDS),
            )
        )

    def _completed_external_matches(
        self, graph: _CancellationGraph, first: datetime, deadline: datetime
    ) -> bool:
        external = graph.external
        unit, step = graph.unit, graph.step
        snapshot = step.get("input_snapshot")
        request_snapshot = external.get("request_snapshot") if external else None
        return bool(
            external
            and isinstance(snapshot, dict)
            and isinstance(request_snapshot, dict)
            and external.get("step_id") == step.get("id")
            and external.get("submission_key") == f"{_INTENT_KEY_PREFIX}{unit['id']}:{step['id']}"
            and external.get("request_digest") == graph.binding.request_digest
            and external.get("submission_status") == "completed"
            and _as_utc(external.get("submitted_at")) is not None
            and external.get("external_task_id") is None
            and external.get("provider") == request_snapshot.get("provider")
            and external.get("model_ref") == request_snapshot.get("model_id")
            and isinstance(request_snapshot.get("credential_binding_ref"), str)
            and bool(request_snapshot.get("credential_binding_ref"))
            and request_snapshot.get("owner_id") == graph.task.get("user_id")
            and request_snapshot.get("task_id") == graph.task.get("id")
            and request_snapshot.get("unit_id") == unit.get("id")
            and request_snapshot.get("step_id") == step.get("id")
            and request_snapshot.get("submission_id") == graph.parent.get("id")
            and request_snapshot.get("operation") == _OPERATION
            and request_snapshot.get("graph_version") == _GRAPH_VERSION
            and request_snapshot.get("attempt_id") == snapshot.get("server_attempt_id")
            and request_snapshot.get("prepared_payload_id") == snapshot.get("prepared_payload_id")
            and request_snapshot.get("protected_ref") == snapshot.get("protected_ref")
            and request_snapshot.get("request_digest") == graph.binding.request_digest
            and request_snapshot.get("rule_digest") == graph.binding.rule_digest
            and request_snapshot.get("snapshot_digest") == graph.binding.snapshot_digest
            and request_snapshot.get("manifest_snapshot_digest") == graph.binding.manifest_snapshot_digest
            and request_snapshot.get("snapshot_ref") == (
                f"{_GRAPH_VERSION}:{graph.binding.prepared_payload_id}:"
                f"{graph.binding.snapshot_digest}:{graph.binding.manifest_snapshot_digest}"
            )
            and _same_time(external.get("intent_at"), unit.get("first_intent_at"))
            and _same_time(external.get("deadline_at"), unit.get("deadline_at"))
            and deadline == first + timedelta(seconds=_DEADLINE_SECONDS)
        )

    def _financial_plan(
        self, graph: _CancellationGraph, now: datetime
    ) -> _FinancialPlan | None:
        unit, task = graph.unit, graph.task
        amount = unit.get("quoted_amount")
        if type(amount) is not int or amount < 0:
            return None
        receipt = graph.receipts[0] if len(graph.receipts) == 1 else None
        application = graph.applications[0] if len(graph.applications) == 1 else None
        result_ref = receipt.get("result_ref") if receipt else None
        application_ref = application.get("application_ref") if application else None
        fee = graph.fee
        ledgers = graph.ledgers
        if fee is None:
            if (
                task.get("credit_cost") != 0
                or unit.get("billing_status") != "unbilled"
                or task.get("billing_status") != "unbilled"
                or unit.get("debited_amount") != 0
                or unit.get("refunded_amount") != 0
                or ledgers
            ):
                return None
            if amount == 0:
                record = UnitFeeDecision(
                    unit_id=unit["id"],
                    charge_phase="post_apply",
                    resolution="no_charge",
                    ledger_ref=None,
                    generation_result_ref=result_ref,
                    application_ref=application_ref,
                    resolved_at=now,
                )
                return _FinancialPlan(0, "no_charge", record)
            record = UnitFeeDecision(
                unit_id=unit["id"],
                charge_phase="post_apply",
                resolution="waived_cancelled",
                ledger_ref=None,
                generation_result_ref=result_ref,
                application_ref=application_ref,
                resolved_at=now,
            )
            return _FinancialPlan(0, "unbilled", record)

        if (
            fee.get("charge_phase") != "post_apply"
            or fee.get("resolved_at") is None
            or fee.get("generation_result_ref") != result_ref
            or fee.get("application_ref") != application_ref
        ):
            return None
        resolution = fee.get("resolution")
        if resolution == "charged":
            if (
                amount <= 0
                or task.get("credit_cost")
                != (0 if task.get("status") in _ACTIVE_TASK_STATUSES else amount)
                or unit.get("billing_status") != "charged"
                or unit.get("debited_amount") != amount
                or unit.get("refunded_amount") != 0
                or task.get("billing_status") != "charged"
                or len(ledgers) != 1
                or not _ledger_matches(ledgers[0], task, unit, amount)
                or fee.get("ledger_ref") != ledgers[0].get("id")
                or receipt is None
                or application is None
            ):
                return None
            return _FinancialPlan(amount, "charged", None)
        if resolution == "no_charge":
            if (
                amount != 0
                or task.get("credit_cost") != 0
                or unit.get("billing_status") != "no_charge"
                or task.get("billing_status") != "no_charge"
                or unit.get("debited_amount") != 0
                or unit.get("refunded_amount") != 0
                or fee.get("ledger_ref") is not None
                or ledgers
            ):
                return None
            return _FinancialPlan(0, "no_charge", None)
        if resolution in {
            "waived_insufficient", "waived_charge_failed", "waived_cancelled",
            "waived_delivery_failed", "stopped_insufficient", "stopped_late_generation",
        }:
            if (
                task.get("credit_cost") != 0
                or unit.get("billing_status") != "unbilled"
                or task.get("billing_status") != "unbilled"
                or unit.get("debited_amount") != 0
                or unit.get("refunded_amount") != 0
                or fee.get("ledger_ref") is not None
                or ledgers
            ):
                return None
            if resolution == "waived_cancelled":
                return None
            return _FinancialPlan(0, "unbilled", None)
        return None

    def _work_to_stop(
        self, graph: _CancellationGraph
    ) -> dict[str, object] | None | Literal[False]:
        if not graph.receipts:
            return None if not graph.works and not graph.applications else False
        if len(graph.receipts) != 1 or len(graph.works) != 1 or len(graph.applications) > 1:
            return False
        receipt = graph.receipts[0]
        work = graph.works[0]
        application = graph.applications[0] if graph.applications else None
        if not self._result_graph_matches(graph, receipt, work, application):
            return False
        if work.get("phase") in _TERMINAL_WORK_PHASES:
            if work.get("phase") == "done" and graph.task.get("status") != "completed":
                return False
            if work.get("phase") in {
                "stopped_late_generation",
                "stopped_delivery_failed",
            }:
                return None
            return False
        return work

    def _preserved_late_cancel_fee_matches(
        self,
        graph: _CancellationGraph,
        receipt: dict[str, object],
        work: dict[str, object],
        application: dict[str, object] | None,
    ) -> bool:
        fee = graph.fee
        amount = graph.unit.get("quoted_amount")
        marker = graph.task.get("cancellation_reason")
        if fee is None:
            return False
        if not all(
            (
                work.get("phase") == "stopped_late_generation",
                _is_cancel_marker(marker),
                graph.task.get("status") == "failed",
                graph.task.get("user_cancelled_at") is not None,
                graph.unit.get("cancellation_reason") == marker,
                graph.unit.get("user_cancelled_at") is not None,
                _same_time(
                    graph.task.get("user_cancelled_at"),
                    graph.unit.get("user_cancelled_at"),
                ),
                type(graph.task.get("credit_cost")) is int
                and graph.task.get("credit_cost") == 0,
                graph.unit.get("debited_amount") == 0,
                graph.unit.get("refunded_amount") == 0,
                application is None,
                not graph.applications,
                not graph.ledgers,
                fee.get("unit_id") == graph.unit.get("id"),
                fee.get("charge_phase") == "post_apply",
                fee.get("resolved_at") is not None,
                fee.get("generation_result_ref") == receipt.get("result_ref"),
                fee.get("application_ref") is None,
                fee.get("ledger_ref") is None,
            )
        ):
            return False
        if fee["resolution"] == "waived_cancelled":
            return bool(
                type(amount) is int
                and amount > 0
                and graph.task.get("billing_status") == "unbilled"
                and graph.unit.get("billing_status") == "unbilled"
            )
        if fee["resolution"] == "no_charge":
            return bool(
                amount == 0
                and graph.task.get("billing_status") == "no_charge"
                and graph.unit.get("billing_status") == "no_charge"
            )
        return False

    def _result_graph_matches(
        self,
        graph: _CancellationGraph,
        receipt: dict[str, object],
        work: dict[str, object],
        application: dict[str, object] | None,
    ) -> bool:
        task, unit, step, external = graph.task, graph.unit, graph.step, graph.external
        if external is None or len(graph.evidences) != 1 or not isinstance(external, dict):
            return False
        evidence = graph.evidences[0]
        expected_attempt = graph.binding.server_attempt_id
        fulfilled_at = _as_utc(receipt.get("generation_fulfilled_at"))
        deadline = _as_utc(unit.get("deadline_at"))
        timely = fulfilled_at is not None and deadline is not None and fulfilled_at < deadline
        phase = work.get("phase")
        retained_late_fee = self._preserved_late_cancel_fee_matches(
            graph, receipt, work, application
        )
        text = decode_inline_text_result(
            receipt.get("recoverable_locator"), receipt.get("content_digest")
        )
        if not all(
            (
                receipt.get("owner_id") == task.get("user_id"),
                receipt.get("task_id") == task.get("id"),
                receipt.get("unit_id") == unit.get("id"),
                receipt.get("operation") == _OPERATION,
                receipt.get("step_id") == step.get("id"),
                receipt.get("submission_id") == external.get("id"),
                receipt.get("evidence_id") == evidence.get("id"),
                receipt.get("selected_attempt_id") == expected_attempt,
                receipt.get("validation_version") == "frame-review-text-v1",
                receipt.get("late_disposition")
                == (None if timely else "stopped_late_generation"),
                receipt.get("policy_stop_disposition") is None,
                receipt.get("automatic_application_allowed") is timely,
                fulfilled_at is not None,
                _same_time(receipt.get("validated_at"), fulfilled_at),
                isinstance(text, str),
                unit.get("result_status") == "succeeded",
                _same_time(unit.get("success_confirmed_at"), fulfilled_at),
                step.get("execution_status")
                == ("applied" if application is not None else "result_observed"),
                external.get("submission_status") == "completed",
                _same_time(external.get("submitted_at"), fulfilled_at),
                evidence.get("step_id") == step.get("id"),
                evidence.get("submission_id") == external.get("id"),
                evidence.get("evidence_key")
                == f"frame-review-result-v1:{external.get('id')}",
                evidence.get("source") == "provider",
                evidence.get("evidence_kind") == "provider_text",
                evidence.get("validity") == "valid",
                evidence.get("payload_digest") == receipt.get("content_digest"),
                evidence.get("result_digest") == receipt.get("content_digest"),
                evidence.get("result_location") == receipt.get("recoverable_locator"),
                _same_time(evidence.get("received_at"), fulfilled_at),
                work.get("owner_id") == task.get("user_id"),
                work.get("task_id") == task.get("id"),
                work.get("unit_id") == unit.get("id"),
                work.get("operation") == _OPERATION,
                work.get("policy_version") == LOCAL_CONTINUATION_POLICY,
                work.get("result_ref") == receipt.get("result_ref"),
                work.get("content_digest") == receipt.get("content_digest"),
                work.get("fee_decision_ref") == (
                    unit.get("id")
                    if graph.fee is not None and not retained_late_fee
                    else None
                ),
                work.get("application_ref") == (
                    application.get("application_ref") if application else None
                ),
                tuple(work.get("phase_order") or ()) == _PHASE_ORDER,
                work.get("phase") in set(_PHASE_ORDER) | _TERMINAL_WORK_PHASES,
                type(work.get("phase_version")) is int and work["phase_version"] >= 0,
                _work_phase_version_matches(work),
                work.get("last_execution_generation") == task.get("execution_generation"),
                _same_time(work.get("generation_fulfilled_at"), receipt.get("generation_fulfilled_at")),
                _same_time(work.get("original_first_intent_at"), unit.get("first_intent_at")),
                _same_time(work.get("original_deadline_at"), unit.get("deadline_at")),
                _same_time(unit.get("success_confirmed_at"), receipt.get("generation_fulfilled_at")),
            )
        ):
            return False
        if application is None:
            if work.get("application_ref") is not None:
                return False
            if work.get("phase") in {"fee_pending", "finalize_pending", "done"}:
                return False
            if evidence.get("application_status") != "pending" or evidence.get("applied_at") is not None:
                return False
            if phase == "delivery_pending":
                expected_reason = (
                    None if timely else "original_generation_deadline_elapsed"
                )
                if work.get("terminal_reason") != expected_reason:
                    return False
            elif phase == "stopped_late_generation":
                if (
                    timely
                    or work.get("terminal_reason")
                    != "original_generation_deadline_elapsed"
                ):
                    return False
            elif phase == "stopped_delivery_failed":
                if (
                    not timely
                    or work.get("terminal_reason")
                    != "frame_review_target_delivery_failed"
                ):
                    return False
            elif phase == "stopped_cancelled":
                if work.get("terminal_reason") != "user_cancelled":
                    return False
            elif phase == "stopped_insufficient":
                return False
        else:
            if not timely:
                return False
            if not all(
                (
                    application.get("owner_id") == task.get("user_id"),
                    application.get("task_id") == task.get("id"),
                    application.get("unit_id") == unit.get("id"),
                    application.get("result_ref") == receipt.get("result_ref"),
                    application.get("result_digest") == receipt.get("content_digest"),
                    application.get("delivery_status") == "applied",
                    application.get("committed_at") is not None,
                    application.get("target_scope") == _target_scope(graph),
                    work.get("application_ref") == application.get("application_ref"),
                    work.get("phase") in {"fee_pending", "finalize_pending", "done", "stopped_cancelled", "stopped_insufficient"},
                    evidence.get("application_status") == "applied",
                    evidence.get("applied_at") is not None,
                )
            ):
                return False
        return True

    def _cancelled_projection_matches(self, graph: _CancellationGraph) -> bool:
        task, unit = graph.task, graph.unit
        marker = task.get("cancellation_reason")
        if not all(
            (
                _is_cancel_marker(marker),
                task.get("status") == "failed",
                task.get("user_cancelled_at") is not None,
                unit.get("cancellation_reason") == marker,
                unit.get("user_cancelled_at") is not None,
                task.get("claimed_by") is None,
                task.get("claim_token") is None,
                task.get("lease_until") is None,
                task.get("billing_status") == unit.get("billing_status"),
                task.get("credit_cost") == unit.get("debited_amount"),
                unit.get("refunded_amount") == 0,
            )
        ):
            return False
        if (
            not self._base_binding_matches(graph, allow_finalized_cost=True)
            or not self._external_matches(graph)
        ):
            return False
        if graph.receipts:
            if len(graph.receipts) != 1 or len(graph.works) != 1:
                return False
            work = graph.works[0]
            application = graph.applications[0] if len(graph.applications) == 1 else None
            if not self._result_graph_matches(graph, graph.receipts[0], work, application):
                return False
            if work.get("phase") == "stopped_cancelled":
                if work.get("terminal_reason") != "user_cancelled":
                    return False
            elif work.get("phase") == "stopped_late_generation":
                if (
                    graph.receipts[0].get("automatic_application_allowed") is not False
                    or graph.receipts[0].get("late_disposition")
                    != "stopped_late_generation"
                    or work.get("terminal_reason")
                    != "original_generation_deadline_elapsed"
                ):
                    return False
            else:
                return False
        else:
            if not self._cancelled_no_result_graph_matches(graph):
                return False
        plan = self._replay_financial_matches(graph)
        return plan

    def _cancelled_no_result_graph_matches(
        self, graph: _CancellationGraph
    ) -> bool:
        if graph.works or graph.applications or graph.receipts:
            return False
        return self._no_result_execution_graph_matches(graph)

    def _no_result_execution_graph_matches(
        self, graph: _CancellationGraph
    ) -> bool:
        if graph.task.get("result") is not None:
            return False
        if graph.external is None:
            return (
                graph.unit.get("result_status") == "pending"
                and graph.step.get("execution_status") in {"prepared", "ready"}
                and not graph.evidences
            )
        if (
            graph.unit.get("result_status") == "pending"
            and graph.step.get("execution_status") == "submitting"
            and graph.external.get("submission_status") == "submitting"
        ):
            return not graph.evidences
        if (
            graph.unit.get("result_status") == "unknown"
            and graph.step.get("execution_status") == "reconciling"
            and graph.external.get("submission_status") == "unknown"
            and len(graph.evidences) == 1
        ):
            return self._observations_match(
                graph, expected_kind="outcome_unknown"
            )
        return False

    def _replay_financial_matches(self, graph: _CancellationGraph) -> bool:
        unit, task, fee = graph.unit, graph.task, graph.fee
        if fee is None or fee.get("charge_phase") != "post_apply" or fee.get("resolved_at") is None:
            return False
        receipt = graph.receipts[0] if graph.receipts else None
        application = graph.applications[0] if graph.applications else None
        if (
            fee.get("generation_result_ref") != (receipt.get("result_ref") if receipt else None)
            or fee.get("application_ref") != (application.get("application_ref") if application else None)
        ):
            return False
        return self._financial_plan_for_replay(graph)

    def _financial_plan_for_replay(self, graph: _CancellationGraph) -> bool:
        unit, task, fee = graph.unit, graph.task, graph.fee
        if fee is None:
            return False
        amount = unit.get("quoted_amount")
        resolution = fee.get("resolution")
        if resolution == "charged":
            return bool(
                type(amount) is int
                and amount > 0
                and unit.get("billing_status") == task.get("billing_status") == "charged"
                and unit.get("debited_amount") == task.get("credit_cost") == amount
                and unit.get("refunded_amount") == 0
                and len(graph.ledgers) == 1
                and _ledger_matches(graph.ledgers[0], task, unit, amount)
                and fee.get("ledger_ref") == graph.ledgers[0].get("id")
            )
        if resolution == "no_charge":
            return bool(
                amount == 0
                and unit.get("billing_status") == task.get("billing_status") == "no_charge"
                and unit.get("debited_amount") == task.get("credit_cost") == 0
                and unit.get("refunded_amount") == 0
                and fee.get("ledger_ref") is None
                and not graph.ledgers
            )
        if resolution in {"waived_cancelled", "waived_insufficient", "waived_charge_failed", "waived_delivery_failed", "stopped_insufficient", "stopped_late_generation"}:
            return bool(
                unit.get("billing_status") == task.get("billing_status") == "unbilled"
                and unit.get("debited_amount") == task.get("credit_cost") == 0
                and unit.get("refunded_amount") == 0
                and fee.get("ledger_ref") is None
                and not graph.ledgers
                and (resolution != "waived_cancelled" or graph.task.get("user_cancelled_at") is not None)
            )
        return False

    def _completed_projection_matches(self, graph: _CancellationGraph) -> bool:
        task, unit = graph.task, graph.unit
        if not all(
            (
                task.get("status") == "completed",
                len(graph.receipts) == 1,
                len(graph.works) == 1,
                graph.works[0].get("phase") == "done",
                graph.works[0].get("phase_version") == len(_PHASE_ORDER),
                graph.works[0].get("fee_decision_ref") == unit.get("id"),
                task.get("claim_token") is None,
                task.get("claimed_by") is None,
                task.get("lease_until") is None,
                task.get("credit_cost") == unit.get("debited_amount"),
                task.get("billing_status") == unit.get("billing_status"),
            )
        ):
            return False
        if (
            not self._base_binding_matches(graph, allow_finalized_cost=True)
            or not self._external_matches(graph)
        ):
            return False
        receipt = graph.receipts[0]
        application = graph.applications[0] if len(graph.applications) == 1 else None
        work = graph.works[0]
        if application is None or not self._result_graph_matches(graph, receipt, work, application):
            return False
        now = self._now()
        return now is not None and self._financial_plan(graph, now) is not None

    def _failed_projection_matches(self, graph: _CancellationGraph) -> bool:
        task = graph.task
        if (
            task.get("status") != "failed"
            or task.get("credit_cost") != 0
            or task.get("billing_status") != "unbilled"
            or graph.unit.get("billing_status") != "unbilled"
            or graph.unit.get("debited_amount") != 0
            or graph.unit.get("refunded_amount") != 0
            or not self._base_binding_matches(graph)
        ):
            return False
        if graph.receipts:
            if (
                len(graph.receipts) != 1
                or len(graph.works) != 1
                or len(graph.applications) > 1
                or graph.works[0].get("phase")
                not in {
                    "stopped_late_generation",
                    "stopped_delivery_failed",
                    "stopped_insufficient",
                }
                or not self._result_graph_matches(
                    graph,
                    graph.receipts[0],
                    graph.works[0],
                    graph.applications[0] if graph.applications else None,
                )
                or not self._fee_graph_matches(graph)
            ):
                return False
            return True
        if graph.works or graph.applications or graph.fee or graph.ledgers:
            return False
        if graph.external is None:
            return (
                graph.unit.get("result_status") == "pending"
                and graph.step.get("execution_status") in {"prepared", "ready"}
                and not graph.evidences
            )
        if (
            graph.unit.get("result_status") != "failed"
            or graph.step.get("execution_status") != "failed"
            or graph.external.get("submission_status") != "failed"
            or not self._external_matches(graph)
            or len(graph.evidences) != 1
        ):
            return False
        return self._observations_match(graph)

    def _active_graph_matches(self, graph: _CancellationGraph) -> bool:
        return bool(
            graph.task.get("status") in _ACTIVE_TASK_STATUSES
            and graph.unit.get("user_cancelled_at") is None
            and graph.unit.get("cancellation_reason") is None
            and graph.task.get("user_cancelled_at") is None
            and graph.task.get("cancellation_reason") is None
            and self._base_binding_matches(graph, allow_finalized_cost=True)
            and self._external_matches(graph)
            and self._outcome_graph_matches(graph)
            and graph.task.get("result") is None
        )

    def _outcome_graph_matches(self, graph: _CancellationGraph) -> bool:
        unit = graph.unit
        if not graph.receipts:
            if graph.works or graph.applications or graph.fee or graph.ledgers:
                return False
            if graph.external is None:
                return (
                    self._no_result_execution_graph_matches(graph)
                    and unit.get("billing_status") == "unbilled"
                    and unit.get("debited_amount") == 0
                    and unit.get("refunded_amount") == 0
                    and not graph.ledgers
                )
            return self._no_result_execution_graph_matches(graph)
        if len(graph.receipts) != 1 or len(graph.works) != 1 or len(graph.applications) > 1:
            return False
        if not self._result_graph_matches(
            graph,
            graph.receipts[0],
            graph.works[0],
            graph.applications[0] if graph.applications else None,
        ):
            return False
        return self._fee_graph_matches(graph)

    def _base_binding_matches(
        self, graph: _CancellationGraph, *, allow_finalized_cost: bool = False
    ) -> bool:
        task, unit, step, parent, request, binding = (
            graph.task, graph.unit, graph.step, graph.parent,
            graph.request_data, graph.binding,
        )
        valid_cost = type(task.get("credit_cost")) is int and task.get("credit_cost") == 0
        if allow_finalized_cost:
            if task.get("status") in _ACTIVE_TASK_STATUSES:
                valid_cost = (
                    type(task.get("credit_cost")) is int
                    and task.get("credit_cost") == 0
                    and task.get("billing_status") == unit.get("billing_status")
                )
            else:
                valid_cost = (
                    type(task.get("credit_cost")) is int
                    and task.get("credit_cost") >= 0
                    and task.get("credit_cost") == unit.get("debited_amount")
                    and task.get("billing_status") == unit.get("billing_status")
                )
        snapshot = step.get("input_snapshot")
        snapshot_matches = bool(
            isinstance(snapshot, dict)
            and set(snapshot)
            == {
                "server_attempt_id", "prepared_payload_id", "protected_ref",
                "snapshot_digest", "manifest_snapshot_digest",
            }
            and snapshot.get("server_attempt_id") == binding.server_attempt_id
            and snapshot.get("prepared_payload_id") == binding.prepared_payload_id
            and snapshot.get("protected_ref") == binding.protected_ref
            and snapshot.get("snapshot_digest") == binding.snapshot_digest
            and snapshot.get("manifest_snapshot_digest") == binding.manifest_snapshot_digest
        )
        return valid_cost and all(
            (
                snapshot_matches,
                _claim_state_matches(task),
                task.get("type") == _TASK_TYPE,
                task.get("user_id") == binding.owner_id,
                task.get("id") == binding.task_id,
                (task.get("message_id") or None) == binding.task_message_id,
                task.get("model_name") == binding.task_model_name,
                request.get("via") == "registered_generation_graph",
                request.get("mode") == "frame_review",
                request.get("operation") == _OPERATION,
                request.get("graph_version") == _GRAPH_VERSION,
                request.get("step_graph_version") == _GRAPH_VERSION,
                request.get("submission_id") == parent.get("id"),
                request.get("parent_attempt_id") == parent.get("id"),
                request.get("billing_unit_id") == unit.get("id"),
                request.get("request_digest") == unit.get("input_digest") == step.get("input_digest"),
                request.get("rule_digest") == binding.rule_digest,
                request.get("snapshot_digest") == binding.snapshot_digest,
                request.get("manifest_snapshot_digest") == binding.manifest_snapshot_digest,
                request.get("server_attempt_id") == binding.server_attempt_id,
                request.get("prepared_payload_id") == binding.prepared_payload_id,
                request.get("protected_ref") == binding.protected_ref,
                request.get("model_id") == binding.task_model_id,
                request.get("model_source") == binding.task_model_source,
                request.get("model_owner_id") == binding.task_model_owner_id,
                request.get("model_display_name") == binding.task_model_name,
                request.get("cost_per_call") == binding.quoted_amount,
                request.get("item_id") == binding.server_attempt_id,
                request.get("unit_key") == unit.get("item_key"),
                request.get("target_ref") == f"{binding.protected_ref}#target",
                request.get("model_ref") == f"{binding.protected_ref}#model",
                request.get("grant_ref") == f"{binding.protected_ref}#grant",
                unit.get("task_id") == task.get("id"),
                unit.get("operation") == _OPERATION,
                unit.get("step_graph_version") == _GRAPH_VERSION,
                unit.get("item_key") == binding.server_attempt_id,
                unit.get("quoted_amount") == binding.quoted_amount,
                type(unit.get("quoted_amount")) is int and unit.get("quoted_amount") >= 0,
                step.get("id") == binding.step_id,
                step.get("billing_unit_id") == unit.get("id"),
                step.get("step_key") == _STEP_KEY,
                step.get("required") is True,
                step.get("step_order") == 0,
                step.get("dependencies") in ([], ()),
                parent.get("id") == binding.submission_id,
                parent.get("user_id") == task.get("user_id"),
                parent.get("operation") == _OPERATION,
                parent.get("idempotency_key") == binding.raw_key,
                parent.get("request_digest") == binding.request_digest,
                parent.get("task_ids") == [task.get("id")],
            )
        )

    def _fee_graph_matches(self, graph: _CancellationGraph) -> bool:
        if graph.fee is None:
            return (
                graph.unit.get("billing_status") == "unbilled"
                and graph.unit.get("debited_amount") == 0
                and graph.unit.get("refunded_amount") == 0
                and graph.task.get("billing_status") == "unbilled"
                and not graph.ledgers
                and (not graph.applications or graph.works[0].get("phase") == "fee_pending")
            )
        now = self._now()
        return now is not None and self._financial_plan(graph, now) is not None

    def _observations_match(
        self,
        graph: _CancellationGraph,
        *,
        expected_kind: str | None = None,
    ) -> bool:
        external = graph.external
        if external is None:
            return not graph.evidences
        for row in graph.evidences:
            text = row.get("result_location")
            digest = row.get("payload_digest")
            if not all(
                (
                    row.get("source") == "worker",
                    row.get("evidence_kind") == "frame_review_observation_v1",
                    row.get("evidence_key", "").startswith("frame-review-observation-v1:"),
                    row.get("submission_id") == external.get("id"),
                    row.get("result_digest") is None,
                    row.get("validity") == "unknown",
                    row.get("application_status") == "pending",
                    row.get("applied_at") is None,
                    isinstance(text, str),
                    isinstance(digest, str) and len(digest) == 64,
                    hashlib.sha256(text.encode("utf-8")).hexdigest() == digest,
                    text.startswith("frame-review-observation-v1:"),
                )
            ):
                return False
            try:
                payload = json.loads(text[len("frame-review-observation-v1:") :])
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            if not all(
                (
                    isinstance(payload, dict),
                    set(payload) == {
                        "version", "owner_id", "task_id", "unit_id", "step_id",
                        "submission_id", "attempt_id", "request_digest", "kind",
                        "stage", "code", "request_started", "http_status", "observed_at",
                    },
                    payload.get("version") == 1,
                    payload.get("owner_id") == graph.task.get("user_id"),
                    payload.get("task_id") == graph.task.get("id"),
                    payload.get("unit_id") == graph.unit.get("id"),
                    payload.get("step_id") == graph.step.get("id"),
                    payload.get("submission_id") == external.get("id"),
                    payload.get("attempt_id") == graph.binding.server_attempt_id,
                    payload.get("request_digest") == graph.binding.request_digest,
                    payload.get("kind") in {"known_not_started", "provider_rejected", "outcome_unknown"}
                    and (expected_kind is None or payload.get("kind") == expected_kind),
                    row.get("evidence_key") == f"frame-review-observation-v1:{external['id']}:{payload.get('kind')}",
                    _same_time(
                        row.get("received_at"),
                        _parse_utc(payload.get("observed_at")),
                    ),
                    _valid_observation_payload(payload),
                )
            ):
                return False
        return True

    def _mark_unit(
        self,
        session: Session,
        graph: _CancellationGraph,
        marker: str,
        now: datetime,
        plan: _FinancialPlan,
    ) -> None:
        unit = BillingUnitRow.__table__
        values: dict[str, object] = {
            "user_cancelled_at": _db_time(now),
            "cancellation_reason": marker,
            "updated_at": _db_time(now),
        }
        if plan.billing_status == "no_charge" and graph.unit.get("billing_status") == "unbilled":
            values["billing_status"] = "no_charge"
        changed = session.execute(
            update(unit)
            .where(
                unit.c.id == graph.unit["id"],
                unit.c.task_id == graph.task["id"],
                unit.c.user_cancelled_at.is_(None),
                unit.c.cancellation_reason.is_(None),
                unit.c.billing_status == graph.unit.get("billing_status"),
                unit.c.debited_amount == graph.unit.get("debited_amount"),
                unit.c.refunded_amount == graph.unit.get("refunded_amount"),
                unit.c.first_intent_at == graph.unit.get("first_intent_at"),
                unit.c.deadline_at == graph.unit.get("deadline_at"),
            )
            .values(**values)
        )
        if changed.rowcount != 1:
            raise _CancelWriteConflict

    def _mark_task(
        self, session: Session, graph: _CancellationGraph, marker: str, now: datetime
    ) -> None:
        task = AITaskRow.__table__
        changed = session.execute(
            update(task)
            .where(
                task.c.id == graph.task["id"],
                task.c.user_id == graph.task["user_id"],
                task.c.type == _TASK_TYPE,
                task.c.status == graph.task.get("status"),
                task.c.execution_generation == graph.task.get("execution_generation"),
                task.c.claimed_by == graph.task.get("claimed_by"),
                task.c.claim_token == graph.task.get("claim_token"),
                task.c.lease_until == graph.task.get("lease_until"),
                task.c.user_cancelled_at.is_(None),
                task.c.cancellation_reason.is_(None),
            )
            .values(user_cancelled_at=_db_time(now), cancellation_reason=marker, updated_at=_db_time(now))
        )
        if changed.rowcount != 1:
            raise _CancelWriteConflict

    def _stop_work(
        self,
        session: Session,
        work_row: dict[str, object],
        graph: _CancellationGraph,
        plan: _FinancialPlan,
    ) -> None:
        work = _work_record(work_row)
        if plan.fee_to_create is not None or graph.fee is not None:
            fee_ref = graph.unit["id"]
        else:
            fee_ref = work.fee_decision_ref
        updated = replace(
            work,
            phase="stopped_cancelled",
            phase_version=work.phase_version + 1,
            fee_decision_ref=fee_ref,
            application_ref=(
                graph.applications[0]["application_ref"] if graph.applications else work.application_ref
            ),
            terminal_reason="user_cancelled",
        )
        result = GenerationRecordRepository(session).advance_continuation_work(
            updated,
            expected_phase_version=work.phase_version,
        )
        if result.status != "updated":
            raise _CancelWriteConflict

    def _revoke_task_fence(
        self,
        session: Session,
        graph: _CancellationGraph,
        marker: str,
        now: datetime,
        plan: _FinancialPlan,
    ) -> None:
        task = AITaskRow.__table__
        changed = session.execute(
            update(task)
            .where(
                task.c.id == graph.task["id"],
                task.c.user_id == graph.task["user_id"],
                task.c.type == _TASK_TYPE,
                task.c.status == graph.task.get("status"),
                task.c.execution_generation == graph.task.get("execution_generation"),
                task.c.claimed_by == graph.task.get("claimed_by"),
                task.c.claim_token == graph.task.get("claim_token"),
                task.c.lease_until == graph.task.get("lease_until"),
                task.c.user_cancelled_at == _db_time(now),
                task.c.cancellation_reason == marker,
            )
            .values(
                status="failed",
                progress=100,
                progress_message=_CANCELLED_MESSAGE,
                claimed_by=None,
                claim_token=None,
                lease_until=None,
                billing_status=plan.billing_status,
                credit_cost=plan.actual_cost,
                updated_at=_db_time(now),
            )
        )
        if changed.rowcount != 1:
            raise _CancelWriteConflict

    def _now(self) -> datetime | None:
        try:
            value = self.db_clock()
        except Exception:
            return None
        return _as_utc(value) if _valid_datetime(value) else None


def _selected_candidate(task: dict[str, object], units) -> bool:
    if task.get("type") != _TASK_TYPE:
        return False
    request = _json_object(task.get("request_data"))
    task_hints = (
        "via", "mode", "operation", "graph_version", "step_graph_version",
        "submission_id", "billing_unit_id", "prepared_payload_id", "protected_ref",
    )
    if request and any(request.get(name) is not None for name in task_hints):
        return True
    if task.get("user_cancelled_at") is not None or _is_cancel_marker(task.get("cancellation_reason")):
        return True
    return any(
        row.get("operation") == _OPERATION or row.get("step_graph_version") == _GRAPH_VERSION
        for row in units
    )


def _make_binding(task, unit, step, parent, request):
    snapshot = step.get("input_snapshot")
    if not isinstance(snapshot, dict):
        return None
    try:
        return PersistedFrameReviewExecutionBinding(
            owner_id=task["user_id"],
            task_id=task["id"],
            unit_id=unit["id"],
            step_id=step["id"],
            submission_id=parent["id"],
            raw_key=parent["idempotency_key"],
            task_message_id=task.get("message_id") or None,
            task_model_name=task["model_name"],
            task_model_id=request["model_id"],
            task_model_source=request["model_source"],
            task_model_owner_id=request["model_owner_id"],
            request_digest=request["request_digest"],
            rule_digest=request["rule_digest"],
            snapshot_digest=request["snapshot_digest"],
            manifest_snapshot_digest=request["manifest_snapshot_digest"],
            server_attempt_id=snapshot["server_attempt_id"],
            prepared_payload_id=snapshot["prepared_payload_id"],
            protected_ref=snapshot["protected_ref"],
            quoted_amount=unit["quoted_amount"],
        )
    except (KeyError, TypeError):
        return None


def _work_record(row) -> LocalContinuationWork:
    return LocalContinuationWork(
        work_id=row["work_id"],
        owner_id=row["owner_id"],
        task_id=row["task_id"],
        unit_id=row["unit_id"],
        operation=row["operation"],
        policy_version=row["policy_version"],
        result_ref=row["result_ref"],
        content_digest=row["content_digest"],
        generation_fulfilled_at=_as_utc(row["generation_fulfilled_at"]),
        original_first_intent_at=_as_utc(row["original_first_intent_at"]),
        original_deadline_at=_as_utc(row["original_deadline_at"]),
        phase_order=tuple(row["phase_order"]),
        phase=row["phase"],
        phase_version=row["phase_version"],
        fee_decision_ref=row["fee_decision_ref"],
        application_ref=row["application_ref"],
        terminal_reason=row["terminal_reason"],
        last_execution_generation=row["last_execution_generation"],
    )


def _ledger_matches(row, task, unit, amount: int) -> bool:
    return all(
        (
            row.get("user_id") == task.get("user_id"),
            row.get("task_id") == task.get("id"),
            row.get("billing_unit_id") == unit.get("id"),
            row.get("type") == "usage",
            row.get("amount") == -amount,
            row.get("business_key") == f"debit:{unit.get('id')}",
            row.get("related_debit_id") is None,
        )
    )


def _claim_state_matches(task: dict[str, object]) -> bool:
    generation = task.get("execution_generation")
    if type(generation) is not int or generation < 0:
        return False
    claimed_by = task.get("claimed_by")
    claim_token = task.get("claim_token")
    lease_until = task.get("lease_until")
    claim_absent = claimed_by is None and claim_token is None and lease_until is None
    claim_present = (
        isinstance(claimed_by, str)
        and bool(claimed_by)
        and isinstance(claim_token, str)
        and bool(claim_token)
        and _as_utc(lease_until) is not None
    )
    status = task.get("status")
    if status == "queued":
        return claim_absent
    if status in {"processing", "reconciling"}:
        return claim_present
    if status == "cancelling":
        return claim_absent or claim_present
    return claim_absent


def _work_phase_version_matches(work: dict[str, object]) -> bool:
    phase = work.get("phase")
    version = work.get("phase_version")
    expected: dict[str, int | frozenset[int]] = {
        "delivery_pending": 0,
        "fee_pending": 1,
        "finalize_pending": 2,
        "done": 3,
        "stopped_cancelled": frozenset({1, 2, 3}),
        "stopped_insufficient": 2,
        "stopped_delivery_failed": 1,
        "stopped_late_generation": 1,
    }
    allowed = expected.get(phase)
    return type(version) is int and (
        version in allowed if isinstance(allowed, frozenset) else version == allowed
    )


def _target_scope(graph: _CancellationGraph) -> str | None:
    if graph.verified is not None:
        target = graph.verified.snapshot.target_snapshot
        kind = target.kind
        series_id = target.series_id
        chapter_id = target.chapter_id
        stable_id = target.stable_storyboard_asset_id
        frame_index = target.compat_frame_index
        asset_type = target.asset_type
        asset_id = target.asset_id
    else:
        target = _json_object(graph.request_data.get("target_snapshot"))
        if target is None:
            return None
        kind = target.get("kind")
        series_id = target.get("series_id")
        chapter_id = target.get("chapter_id")
        stable_id = target.get("stable_storyboard_asset_id")
        frame_index = target.get("compat_frame_index")
        asset_type = target.get("asset_type")
        asset_id = target.get("asset_id")

    if not isinstance(series_id, str) or not series_id:
        return None
    if kind in {"frame_text", "canvas_frame_preview", "frame_message"}:
        if not isinstance(chapter_id, str) or not chapter_id:
            return None
        if isinstance(stable_id, str) and stable_id:
            identity = f"storyboard|{stable_id}"
        elif type(frame_index) is int and frame_index >= 0:
            identity = f"index|{frame_index}"
        else:
            return None
        scope = f"{kind}|{series_id}|chapter|{chapter_id}|{identity}"
    elif kind == "canvas_asset_image" and asset_type and asset_id:
        scope = f"{kind}|{series_id}|asset|{asset_type}|{asset_id}"
    else:
        return None
    return scope if len(scope) <= 255 else None


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    normalized = _as_utc(parsed)
    if normalized is None or normalized.utcoffset() != timezone.utc.utcoffset(normalized):
        return None
    return normalized


def _valid_observation_payload(payload: dict[str, object]) -> bool:
    kind = payload.get("kind")
    stage = payload.get("stage")
    started = payload.get("request_started")
    status = payload.get("http_status")
    code = payload.get("code")
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        return False
    if kind == "known_not_started":
        return (
            stage == "before_request"
            and started is False
            and status is None
            and code in {
                "credential_unavailable",
                "transport_unavailable",
                "invalid_frozen_request",
            }
        )
    if kind == "provider_rejected":
        return (
            stage == "request"
            and started is True
            and type(status) is int
            and 400 <= status < 500
            and code == "provider_rejected"
        )
    if kind == "outcome_unknown":
        return (
            stage in {"before_request", "request"}
            and started is (stage == "request")
            and code == "provider_outcome_unknown"
        )
    return False


def _json_object(value) -> dict[str, object] | None:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_cancel_marker(value: object) -> bool:
    return isinstance(value, str) and _CANCEL_MARKER_PATTERN.fullmatch(value) is not None


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _same_time(left: object, right: object) -> bool:
    first, second = _as_utc(left), _as_utc(right)
    return first is not None and second is not None and first == second


def _valid_datetime(value: object) -> bool:
    if not isinstance(value, datetime):
        return False
    try:
        normalized = _as_utc(value)
    except (OverflowError, ValueError):
        return False
    return normalized is not None


def _db_time(value: datetime) -> datetime:
    normalized = _as_utc(value)
    if normalized is None:
        raise ValueError("cancellation timestamps must be valid UTC datetimes")
    return normalized.replace(tzinfo=None)


def _is_stopping(signal: Event | None) -> bool:
    return signal is not None and signal.is_set()
