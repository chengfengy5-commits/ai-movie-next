"""Persist one original ``image.batch`` item's result under its live claim."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy import and_, cast, insert, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_contracts.records import ApplicationRecord, GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    ApplicationOutcome,
    ClaimAuthority,
    CommittedIntent,
    FrozenExecutionSnapshot,
    GenerationActor,
    OwnerTaskScope,
    StoredMedia,
    TargetSnapshot,
)
from haoai_backend.generation_credentials.batch_execution_snapshot import (
    VerifiedImageBatchExecutionInput,
)
from haoai_backend.generation_core.admission_receipts import (
    AdmissionConfirmed,
    verify_original_replay,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation import (
    application_ref_for,
    find_application_record,
    load_owned_completed_result,
    target_matches_receipt,
    target_scope_for_snapshot,
)

from .base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    TaskSubmissionRow,
    UserCreditRow,
    UserRow,
)
from .persistence import GenerationRecordRepository
from .schema import ApplicationRecordRow, GenerationResultReceiptRow

_TASK_TYPE = "batch-image"
_OPERATION = "image.batch"
_GRAPH_VERSION = "image-batch-v1"
_ORIGINAL_DEADLINE = timedelta(hours=2)
_MAX_BATCH_ITEMS = 64
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_MESSAGE = "批量图片结果需要恢复确认"
_UNRESOLVED_RESULT = json.dumps(
    {"message": _TERMINAL_MESSAGE, "status": "reconciling"},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
_STEP_SNAPSHOT_KEYS = frozenset(
    {
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "snapshot_digest",
        "ordered_manifest_digest",
    }
)
_INTENT_SNAPSHOT_KEYS = frozenset(
    {
        "owner_id",
        "task_id",
        "unit_id",
        "step_id",
        "submission_id",
        "operation",
        "graph_version",
        "item_index",
        "ordered_count",
        "attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "snapshot_digest",
        "snapshot_ref",
        "credential_binding_ref",
        "target_snapshot",
        "provider",
        "model_id",
    }
)
_PRE_CREATE_FAILURE_CODES = frozenset(
    {
        "wrong_execution_context",
        "configuration_unavailable",
        "unsupported_provider",
        "invalid_frozen_snapshot",
        "request_body_too_large",
        "provider_transport_unavailable",
    }
)

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
TerminalKind = Literal["success", "failure", "unknown"]


@dataclass(frozen=True, slots=True)
class _LockedBatchGraph:
    parent: dict[str, object]
    ordered_task_ids: tuple[str, ...]
    tasks_by_id: dict[str, dict[str, object]]
    units_by_task: dict[str, dict[str, object]]
    steps_by_task: dict[str, dict[str, object]]
    request_data_by_task: dict[str, dict[str, object]]
    task: dict[str, object]
    unit: dict[str, object]
    step: dict[str, object]
    submission: dict[str, object]
    request_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class _RefundSettlement:
    owner_id: str
    task_id: str
    unit_id: str
    debit_id: str
    refund_id: str
    refund_key: str
    amount: int
    balance_before: int
    balance_after: int
    description: str


@dataclass(frozen=True, slots=True)
class _ExpectedTerminal:
    kind: TerminalKind
    task_id: str
    owner_id: str
    unit_id: str
    step_id: str
    submission_id: str
    result_value: str
    task_status: str
    unit_status: str
    step_status: str
    submission_status: str
    stored: StoredMedia | None
    receipt: GenerationResultReceipt | None
    evidence_id: str | None
    now: datetime
    task_billing_status: str | None = None
    unit_billing_status: str | None = None
    refunded_amount: int | None = None
    refund: _RefundSettlement | None = None
    original_graph: _LockedBatchGraph | None = None
    refund_description: str | None = None


class SqlAlchemyImageBatchResults:
    """Commit one selected child result without changing its batch siblings."""

    def __init__(
        self,
        session_factory: SessionFactory,
        db_clock: DbClock,
        id_factory: IdFactory,
    ) -> None:
        if not callable(session_factory) or not callable(db_clock) or not callable(id_factory):
            raise TypeError("result persistence dependencies must be explicit callables")
        self._session_factory = session_factory
        self._db_clock = db_clock
        self._id_factory = id_factory

    def complete(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        stored: StoredMedia,
    ) -> GenerationResultReceipt | Literal["pending", "ownership_lost"]:
        if not _valid_stored_media(stored):
            return "pending"
        result_ref = self._new_id()
        evidence_id = self._new_id()
        now = self._read_clock()
        if result_ref is None or evidence_id is None or now is None:
            return "pending"
        receipt = GenerationResultReceipt(
            result_ref=result_ref,
            owner_id=claim.scope.user_id,
            task_id=claim.scope.task_id,
            unit_id=verified.unit_id,
            operation=_OPERATION,
            step_id=intent.step_id,
            submission_id=intent.external_submission_id,
            evidence_id=evidence_id,
            selected_attempt_id=verified.server_attempt_id,
            recoverable_locator=stored.locator,
            content_digest=stored.content_digest,
            validation_version="image-batch-media-v1",
            validated_at=now,
            generation_fulfilled_at=now,
            late_disposition=None,
            policy_stop_disposition=None,
            automatic_application_allowed=True,
        )
        expected = _ExpectedTerminal(
            kind="success",
            task_id=claim.scope.task_id,
            owner_id=claim.scope.user_id,
            unit_id=verified.unit_id,
            step_id=intent.step_id,
            submission_id=intent.external_submission_id,
            result_value=_encode_success_result(stored, receipt),
            task_status="completed",
            unit_status="succeeded",
            step_status="result_observed",
            submission_status="completed",
            stored=stored,
            receipt=receipt,
            evidence_id=evidence_id,
            now=now,
        )
        result = self._write_terminal(claim, intent, verified, expected)
        if isinstance(result, GenerationResultReceipt):
            try:
                self.apply_completed(
                    claim.scope,
                    result.result_ref,
                    stop_signal=claim.local_lost_signal,
                )
            except Exception:
                # The generated result is already durable and remains successful.
                pass
        return result

    def apply_completed(
        self,
        scope: OwnerTaskScope,
        result_ref: str,
        *,
        stop_signal: Event | None = None,
    ) -> ApplicationOutcome:
        """Apply an original completed result locally without changing T1."""
        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != _OPERATION
            or not _nonempty_string(result_ref)
            or _is_stopping(stop_signal)
        ):
            return ApplicationOutcome("write_failed", result_ref, None, None, "batch result application is unavailable")

        target_scope: str | None = None
        try:
            with self._session_factory() as session:
                with session.begin():
                    if _is_stopping(stop_signal):
                        return ApplicationOutcome("write_failed", result_ref, None, None, "batch result application was stopped")
                    graph = _lock_batch_graph(session, scope)
                    if (
                        graph is None
                        or not _batch_identity_matches(graph, scope)
                        or graph.task.get("status") != "completed"
                        or graph.task.get("user_cancelled_at") is not None
                        or graph.unit.get("user_cancelled_at") is not None
                        or graph.unit.get("billing_status") not in {"charged", "no_charge"}
                        or graph.unit.get("refunded_amount") != 0
                        or not _has_timely_linked_success(session, graph, scope)
                    ):
                        return ApplicationOutcome("write_failed", result_ref, None, None, "original successful batch result is not eligible for application")

                    target = _batch_target_snapshot(graph.request_data.get("target_snapshot"))
                    if target is None:
                        return ApplicationOutcome("write_failed", result_ref, None, None, "original batch target is unavailable")
                    target_scope = target_scope_for_snapshot(target)
                    receipt = GenerationRecordRepository(session).find_result_receipt(
                        scope.user_id, scope.task_id, result_ref
                    )
                    if (
                        receipt is None
                        or receipt.operation != _OPERATION
                        or receipt.unit_id != graph.unit.get("id")
                        or receipt.step_id != graph.step.get("id")
                        or receipt.submission_id != graph.submission.get("id")
                    ):
                        return ApplicationOutcome("write_failed", result_ref, None, None, "original result receipt binding is invalid")

                    actor = GenerationActor(scope.user_id, TrustedActor(scope.user_id))
                    loaded = load_owned_completed_result(session, actor, scope.task_id, result_ref)
                    if (
                        isinstance(loaded, str)
                        or loaded != receipt
                        or not target_matches_receipt(session, receipt, target)
                    ):
                        return ApplicationOutcome("write_failed", result_ref, None, None, "original result target could not be verified")

                    original = verify_original_replay(
                        session,
                        owner_id=scope.user_id,
                        operation=_OPERATION,
                        raw_key=str(graph.parent.get("idempotency_key") or ""),
                        request_digest=str(graph.parent.get("request_digest") or ""),
                    )
                    if (
                        not isinstance(original, AdmissionConfirmed)
                        or original.operation != _OPERATION
                        or original.submission_id != graph.parent.get("id")
                        or tuple(task.task_id for task in original.receipt.tasks)
                        != graph.ordered_task_ids
                    ):
                        return ApplicationOutcome("write_failed", result_ref, None, None, "original batch admission or billing proof could not be verified")

                    existing = _read_batch_application(
                        session, receipt, target_scope
                    )
                    if existing is not None:
                        return existing
                    evidence = session.execute(
                        select(ResultEvidenceRow.__table__)
                        .where(
                            ResultEvidenceRow.__table__.c.id == receipt.evidence_id,
                            ResultEvidenceRow.__table__.c.step_id == receipt.step_id,
                            ResultEvidenceRow.__table__.c.submission_id == receipt.submission_id,
                        )
                        .with_for_update()
                    ).mappings().one_or_none()
                    if (
                        evidence is None
                        or evidence.get("validity") != "valid"
                        or evidence.get("application_status") != "pending"
                        or evidence.get("result_digest") != receipt.content_digest
                        or evidence.get("result_location") != receipt.recoverable_locator
                    ):
                        return ApplicationOutcome("write_failed", result_ref, None, None, "original result evidence is not pending application")
                    if _is_stopping(stop_signal):
                        return ApplicationOutcome("write_failed", result_ref, None, None, "batch result application was stopped")
                    application_time = self._read_clock()
                    if application_time is None:
                        return ApplicationOutcome("write_failed", result_ref, None, None, "batch result application clock is unavailable")

                    from haoai_backend.asset_data import stage_batch_asset_image_if_empty

                    outcome = stage_batch_asset_image_if_empty(
                        session, actor, receipt, target
                    )
                    if outcome.kind not in {"applied", "target_conflict", "target_deleted"}:
                        return outcome
                    application_status = (
                        "applied" if outcome.kind == "applied" else "rejected"
                    )
                    applied_at = (
                        _database_datetime(application_time)
                        if application_status == "applied"
                        else None
                    )
                    changed = session.execute(
                        update(ResultEvidenceRow.__table__)
                        .where(
                            ResultEvidenceRow.__table__.c.id == receipt.evidence_id,
                            ResultEvidenceRow.__table__.c.step_id == receipt.step_id,
                            ResultEvidenceRow.__table__.c.submission_id == receipt.submission_id,
                            ResultEvidenceRow.__table__.c.validity == "valid",
                            ResultEvidenceRow.__table__.c.application_status == "pending",
                            ResultEvidenceRow.__table__.c.result_digest == receipt.content_digest,
                            ResultEvidenceRow.__table__.c.result_location == receipt.recoverable_locator,
                        )
                        .values(
                            application_status=application_status,
                            applied_at=applied_at,
                        )
                    )
                    if changed.rowcount != 1:
                        raise RuntimeError("batch result evidence application compare-and-set lost")
            return outcome
        except Exception:
            if target_scope is not None:
                confirmed = self._confirm_batch_application(
                    scope, result_ref, target_scope
                )
                if confirmed is not None:
                    return confirmed
            return ApplicationOutcome("write_failed", result_ref, None, None, "batch result application remains pending")

    def _confirm_batch_application(
        self, scope: OwnerTaskScope, result_ref: str, target_scope: str
    ) -> ApplicationOutcome | None:
        try:
            with self._session_factory() as session:
                receipt = GenerationRecordRepository(session).find_result_receipt(
                    scope.user_id, scope.task_id, result_ref
                )
                if receipt is None or receipt.operation != _OPERATION:
                    return None
                return _read_batch_application(session, receipt, target_scope)
        except Exception:
            return None

    def fail(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        *,
        code: str,
        safe_detail: str,
        provider_request_started: bool = True,
    ) -> None | Literal["pending", "ownership_lost"]:
        if (
            type(provider_request_started) is not bool
            or not _safe_text(code, 64)
            or not _safe_text(safe_detail, 1000)
        ):
            return "pending"
        confirmed_failure = (
            provider_request_started and code == "provider_rejected"
        ) or (
            not provider_request_started and code in _PRE_CREATE_FAILURE_CODES
        )
        if not confirmed_failure:
            return "pending"
        expected = self._non_result_terminal(
            claim,
            verified,
            kind="failure",
            result_value=json.dumps(
                {"code": code, "error": safe_detail},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        return self._write_terminal(claim, intent, verified, expected)

    def mark_unknown(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
    ) -> Literal["pending", "ownership_lost"]:
        expected = self._non_result_terminal(
            claim,
            verified,
            kind="unknown",
            result_value=_UNRESOLVED_RESULT,
        )
        result = self._write_terminal(claim, intent, verified, expected)
        return "pending" if result is None else result

    def record_unknown_observation(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        *,
        payload_digest: str,
        result_location: str | None,
        evidence_kind: str,
    ) -> Literal["recorded", "pending"]:
        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != _OPERATION
            or not _valid_intent(intent, verified)
            or verified.task_id != claim.scope.task_id
            or not _valid_observation(payload_digest, result_location, evidence_kind)
        ):
            return "pending"
        evidence_key = f"image-batch-observation-v1:{intent.external_submission_id}"
        evidence_id: str | None = None
        try:
            with self._session_factory() as session:
                with session.begin():
                    graph = _lock_batch_graph(session, claim)
                    now = self._read_clock()
                    if (
                        graph is None
                        or now is None
                        or not _graph_identity_matches(graph, claim, intent, verified)
                    ):
                        return "pending"
                    existing = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.evidence_key == evidence_key
                        )
                    ).mappings().one_or_none()
                    if existing is not None:
                        if not _observation_row_matches(
                            existing,
                            intent,
                            payload_digest,
                            result_location,
                            evidence_kind,
                        ):
                            return "pending"
                    else:
                        evidence_id = self._new_id()
                        if evidence_id is None:
                            return "pending"
                        session.execute(
                            insert(ResultEvidenceRow.__table__).values(
                                id=evidence_id,
                                step_id=intent.step_id,
                                submission_id=intent.external_submission_id,
                                evidence_key=evidence_key,
                                source="provider",
                                evidence_kind=evidence_kind,
                                payload_digest=payload_digest,
                                result_location=result_location,
                                result_digest=payload_digest,
                                received_at=_database_datetime(now),
                                validity="unknown",
                                application_status="pending",
                                applied_at=None,
                            )
                        )
        except Exception:
            # Resolve an uncertain insert by its stable child-intent key.
            pass
        return self._confirm_unknown_observation(
            claim,
            intent,
            verified,
            evidence_key,
            payload_digest,
            result_location,
            evidence_kind,
        )

    def settle_due(
        self, scope: OwnerTaskScope, *, stop_signal: Event | None = None
    ) -> Literal["settled", "retained", "pending"]:
        if _is_stopping(stop_signal):
            return "pending"
        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != _OPERATION
            or not _nonempty_string(scope.user_id)
            or not _nonempty_string(scope.task_id)
        ):
            return "pending"
        expected = _due_terminal(scope)
        try:
            with self._session_factory() as session:
                with session.begin():
                    graph = _lock_batch_graph(session, scope)
                    now = self._read_clock()
                    if (
                        graph is None
                        or now is None
                        or _is_stopping(stop_signal)
                        or not _batch_identity_matches(graph, scope)
                    ):
                        return "pending"
                    if _is_due_terminal(graph, scope, expected.result_value):
                        return (
                            "settled"
                            if _confirm_due_finances(
                                session, graph, scope, stop_signal=stop_signal
                            )
                            else "pending"
                        )
                    if _has_timely_linked_success(session, graph, scope):
                        return "retained"
                    if not _valid_due_graph(graph, scope, now):
                        return "pending"

                    expected = replace(
                        expected,
                        unit_id=str(graph.unit["id"]),
                        step_id=str(graph.step["id"]),
                        submission_id=str(graph.submission["id"]),
                        now=now,
                        original_graph=graph,
                    )
                    expected = self._with_financial_state(
                        session, scope, graph, expected
                    )
                    if _is_stopping(stop_signal):
                        return "pending"
                    fresh_now = self._read_clock()
                    deadline = graph.unit.get("deadline_at")
                    if (
                        fresh_now is None
                        or not isinstance(deadline, datetime)
                        or _aware_utc(fresh_now) < _aware_utc(deadline)
                    ):
                        return "pending"
                    now = fresh_now
                    expected = _with_now(expected, now)
                    task_table = AITaskRow.__table__
                    task_change = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == scope.task_id,
                            task_table.c.user_id == scope.user_id,
                            task_table.c.type == _TASK_TYPE,
                            task_table.c.status == graph.task["status"],
                            task_table.c.status.in_(("processing", "reconciling", "queued")),
                            task_table.c.user_cancelled_at.is_(None),
                            task_table.c.request_data == graph.task["request_data"],
                            task_table.c.result == graph.task.get("result"),
                        )
                        .values(
                            status="failed",
                            result=expected.result_value,
                            progress=100,
                            progress_message="批量图片结果未能在期限内确认",
                            billing_status=expected.task_billing_status,
                            updated_at=_database_datetime(now),
                        )
                    )
                    if task_change.rowcount != 1:
                        raise _OwnershipLost

                    unit_table = BillingUnitRow.__table__
                    unit_change = session.execute(
                        update(unit_table)
                        .where(
                            unit_table.c.id == graph.unit["id"],
                            unit_table.c.task_id == scope.task_id,
                            unit_table.c.operation == _OPERATION,
                            unit_table.c.step_graph_version == _GRAPH_VERSION,
                            unit_table.c.item_key == graph.unit["item_key"],
                            unit_table.c.input_digest == graph.unit["input_digest"],
                            unit_table.c.result_status.in_(("pending", "unknown")),
                            unit_table.c.billing_status.in_(("charged", "no_charge")),
                            unit_table.c.user_cancelled_at.is_(None),
                            unit_table.c.success_confirmed_at.is_(None),
                            unit_table.c.first_intent_at
                            == _database_datetime(_aware_utc(graph.unit["first_intent_at"])),
                            unit_table.c.deadline_at
                            == _database_datetime(_aware_utc(graph.unit["deadline_at"])),
                            unit_table.c.debited_amount == graph.unit["debited_amount"],
                            unit_table.c.refunded_amount == 0,
                        )
                        .values(
                            result_status="failed",
                            billing_status=expected.unit_billing_status,
                            refunded_amount=expected.refunded_amount,
                            updated_at=_database_datetime(now),
                        )
                    )
                    if unit_change.rowcount != 1:
                        raise _OwnershipLost

                    step_change = session.execute(
                        update(ExecutionStepRow.__table__)
                        .where(
                            ExecutionStepRow.__table__.c.id == graph.step["id"],
                            ExecutionStepRow.__table__.c.billing_unit_id == graph.unit["id"],
                            ExecutionStepRow.__table__.c.execution_status.in_(
                                ("submitting", "reconciling")
                            ),
                            _json_equal(
                                session,
                                ExecutionStepRow.__table__.c.input_snapshot,
                                graph.step["input_snapshot"],
                            ),
                        )
                        .values(
                            execution_status="failed",
                            updated_at=_database_datetime(now),
                        )
                    )
                    if step_change.rowcount != 1:
                        raise _OwnershipLost

                    submission_change = session.execute(
                        update(ExternalSubmissionRow.__table__)
                        .where(
                            ExternalSubmissionRow.__table__.c.id == graph.submission["id"],
                            ExternalSubmissionRow.__table__.c.step_id == graph.step["id"],
                            ExternalSubmissionRow.__table__.c.request_digest
                            == graph.submission["request_digest"],
                            ExternalSubmissionRow.__table__.c.submission_status.in_(
                                ("submitting", "unknown")
                            ),
                            ExternalSubmissionRow.__table__.c.external_task_id.is_(None),
                            ExternalSubmissionRow.__table__.c.intent_at
                            == _database_datetime(_aware_utc(graph.submission["intent_at"])),
                            ExternalSubmissionRow.__table__.c.deadline_at
                            == _database_datetime(_aware_utc(graph.submission["deadline_at"])),
                            _json_equal(
                                session,
                                ExternalSubmissionRow.__table__.c.request_snapshot,
                                graph.submission["request_snapshot"],
                            ),
                        )
                        .values(
                            submission_status="unknown",
                            submitted_at=_database_datetime(now),
                            updated_at=_database_datetime(now),
                        )
                    )
                    if submission_change.rowcount != 1:
                        raise _OwnershipLost

                    after = _lock_batch_graph(session, scope)
                    if (
                        after is None
                        or not _graph_matches_due(after, scope, expected)
                    ):
                        raise _InvalidGraph
                    if expected.refund is not None:
                        _apply_refund(session, expected.refund, now)
        except _OwnershipLost:
            return "pending"
        except _InvalidFinancialProof:
            return "pending"
        except Exception:
            pass
        return self._confirm_due(expected, scope, stop_signal=stop_signal)

    def _write_terminal(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        expected: _ExpectedTerminal,
    ) -> GenerationResultReceipt | None | Literal["pending", "ownership_lost"]:
        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != _OPERATION
            or claim.local_lost_signal.is_set()
            or not _valid_intent(intent, verified)
            or verified.task_id != claim.scope.task_id
            or expected.owner_id != claim.scope.user_id
            or expected.task_id != claim.scope.task_id
        ):
            return "ownership_lost"
        try:
            with self._session_factory() as session:
                with session.begin():
                    graph = _lock_batch_graph(session, claim)
                    now = self._read_clock()
                    if (
                        graph is None
                        or now is None
                        or claim.local_lost_signal.is_set()
                        or not _claim_is_current(graph.task, claim, now)
                        or now >= _aware_utc(intent.deadline_at)
                        or not _graph_matches(graph, claim, intent, verified)
                    ):
                        return "ownership_lost"

                    expected = replace(expected, now=now)
                    expected = self._with_financial_state(
                        session, claim.scope, graph, expected
                    )
                    if expected.kind == "failure":
                        fresh_graph = _lock_batch_graph(session, claim)
                        fresh_now = self._read_clock()
                        if (
                            fresh_graph is None
                            or fresh_now is None
                            or claim.local_lost_signal.is_set()
                            or fresh_now >= _aware_utc(intent.deadline_at)
                            or not _claim_is_current(fresh_graph.task, claim, fresh_now)
                            or not _graph_matches(fresh_graph, claim, intent, verified)
                            or not _same_static_graph(graph, fresh_graph, claim.scope.task_id)
                        ):
                            raise _OwnershipLost
                        now = fresh_now
                        expected = _with_now(expected, now)
                    expected = replace(expected, original_graph=graph)

                    task_table = AITaskRow.__table__
                    task_values: dict[str, object] = {
                        "status": expected.task_status,
                        "result": expected.result_value,
                        "progress": 100 if expected.kind != "unknown" else 75,
                        "progress_message": (
                            "图片已生成"
                            if expected.kind == "success"
                            else _TERMINAL_MESSAGE
                            if expected.kind == "unknown"
                            else "图片生成失败"
                        ),
                        "external_provider": intent.provider,
                        "updated_at": _database_datetime(now),
                    }
                    if expected.kind == "unknown":
                        task_values["recovery_status"] = "reconciling"
                    if expected.kind == "failure":
                        task_values["billing_status"] = expected.task_billing_status
                    task_change = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == claim.scope.task_id,
                            task_table.c.user_id == claim.scope.user_id,
                            task_table.c.type == _TASK_TYPE,
                            task_table.c.status == graph.task["status"],
                            task_table.c.status.in_(("processing", "reconciling")),
                            task_table.c.claimed_by == claim.worker_instance,
                            task_table.c.claim_token == claim.token,
                            task_table.c.execution_generation == claim.execution_generation,
                            task_table.c.user_cancelled_at.is_(None),
                            task_table.c.lease_until
                            == _database_datetime(_aware_utc(graph.task["lease_until"])),
                            task_table.c.lease_until > _database_datetime(now),
                            task_table.c.request_data == graph.task["request_data"],
                            task_table.c.result == graph.task.get("result"),
                        )
                        .values(**task_values)
                    )
                    if task_change.rowcount != 1:
                        raise _OwnershipLost
                    after_task = _lock_batch_graph(session, claim)
                    if (
                        after_task is None
                        or not _graph_matches(
                            after_task,
                            claim,
                            intent,
                            verified,
                            terminal=expected,
                            task_only=True,
                        )
                    ):
                        raise _InvalidGraph

                    unit_table = BillingUnitRow.__table__
                    unit_change = session.execute(
                        update(unit_table)
                        .where(
                            unit_table.c.id == graph.unit["id"],
                            unit_table.c.task_id == claim.scope.task_id,
                            unit_table.c.operation == _OPERATION,
                            unit_table.c.step_graph_version == _GRAPH_VERSION,
                            unit_table.c.item_key == verified.server_attempt_id,
                            unit_table.c.input_digest == intent.request_digest,
                            unit_table.c.result_status.in_(("pending", "unknown")),
                            unit_table.c.billing_status.in_(("charged", "no_charge")),
                            unit_table.c.user_cancelled_at.is_(None),
                            unit_table.c.success_confirmed_at.is_(None),
                            unit_table.c.first_intent_at
                            == _database_datetime(_aware_utc(intent.first_intent_at)),
                            unit_table.c.deadline_at
                            == _database_datetime(_aware_utc(intent.deadline_at)),
                            unit_table.c.debited_amount == graph.unit["debited_amount"],
                            unit_table.c.refunded_amount == graph.unit["refunded_amount"],
                        )
                        .values(
                            result_status=expected.unit_status,
                            **(
                                {
                                    "billing_status": expected.unit_billing_status,
                                    "refunded_amount": expected.refunded_amount,
                                }
                                if expected.kind == "failure"
                                else {}
                            ),
                            success_confirmed_at=(
                                _database_datetime(now)
                                if expected.kind == "success"
                                else None
                            ),
                            updated_at=_database_datetime(now),
                        )
                    )
                    if unit_change.rowcount != 1:
                        raise _OwnershipLost

                    step_change = session.execute(
                        update(ExecutionStepRow.__table__)
                        .where(
                            ExecutionStepRow.__table__.c.id == intent.step_id,
                            ExecutionStepRow.__table__.c.billing_unit_id == verified.unit_id,
                            ExecutionStepRow.__table__.c.step_key == "generate",
                            ExecutionStepRow.__table__.c.required.is_(True),
                            ExecutionStepRow.__table__.c.step_order == graph.step["step_order"],
                            ExecutionStepRow.__table__.c.input_digest == intent.request_digest,
                            _json_equal(
                                session,
                                ExecutionStepRow.__table__.c.dependencies,
                                graph.step["dependencies"],
                            ),
                            _json_equal(
                                session,
                                ExecutionStepRow.__table__.c.input_snapshot,
                                graph.step["input_snapshot"],
                            ),
                            ExecutionStepRow.__table__.c.execution_status.in_(
                                ("submitting", "reconciling")
                            ),
                        )
                        .values(
                            execution_status=expected.step_status,
                            updated_at=_database_datetime(now),
                        )
                    )
                    if step_change.rowcount != 1:
                        raise _OwnershipLost

                    submission_change = session.execute(
                        update(ExternalSubmissionRow.__table__)
                        .where(
                            ExternalSubmissionRow.__table__.c.id
                            == intent.external_submission_id,
                            ExternalSubmissionRow.__table__.c.step_id == intent.step_id,
                            ExternalSubmissionRow.__table__.c.submission_key
                            == graph.submission["submission_key"],
                            _json_equal(
                                session,
                                ExternalSubmissionRow.__table__.c.request_snapshot,
                                graph.submission["request_snapshot"],
                            ),
                            ExternalSubmissionRow.__table__.c.provider == intent.provider,
                            ExternalSubmissionRow.__table__.c.model_ref == intent.model_id,
                            ExternalSubmissionRow.__table__.c.request_digest
                            == intent.request_digest,
                            ExternalSubmissionRow.__table__.c.submission_status.in_(
                                ("submitting", "unknown")
                            ),
                            ExternalSubmissionRow.__table__.c.external_task_id.is_(None),
                            ExternalSubmissionRow.__table__.c.intent_at
                            == _database_datetime(_aware_utc(intent.first_intent_at)),
                            ExternalSubmissionRow.__table__.c.deadline_at
                            == _database_datetime(_aware_utc(intent.deadline_at)),
                        )
                        .values(
                            submission_status=expected.submission_status,
                            submitted_at=_database_datetime(now),
                            updated_at=_database_datetime(now),
                        )
                    )
                    if submission_change.rowcount != 1:
                        raise _OwnershipLost

                    after_children = _lock_batch_graph(session, claim)
                    if (
                        after_children is None
                        or not _graph_matches(
                            after_children,
                            claim,
                            intent,
                            verified,
                            terminal=expected,
                        )
                    ):
                        raise _InvalidGraph
                    if expected.refund is not None:
                        _apply_refund(session, expected.refund, now)
                    if expected.kind == "success":
                        assert expected.stored is not None
                        assert expected.receipt is not None
                        assert expected.evidence_id is not None
                        session.execute(
                            insert(ResultEvidenceRow.__table__).values(
                                id=expected.evidence_id,
                                step_id=intent.step_id,
                                submission_id=intent.external_submission_id,
                                evidence_key=(
                                    f"image-batch-result-v1:{intent.external_submission_id}"
                                ),
                                source="provider",
                                evidence_kind="provider_image",
                                payload_digest=expected.stored.content_digest,
                                result_location=expected.stored.locator,
                                result_digest=expected.stored.content_digest,
                                received_at=_database_datetime(now),
                                validity="valid",
                                application_status="pending",
                                applied_at=None,
                            )
                        )
                        if graph.task.get("message_id") and not _project_assistant_message(
                            session,
                            str(graph.task["message_id"]),
                            expected.stored.locator,
                        ):
                            raise _InvalidGraph
                        write = GenerationRecordRepository(session).stage_record(
                            expected.receipt
                        )
                        if write.status != "created":
                            raise _InvalidGraph
        except _OwnershipLost:
            return "ownership_lost"
        except _InvalidFinancialProof:
            return "pending"
        except Exception:
            # A commit exception is resolved from the exact persisted child graph.
            pass
        confirmed = self._confirm_terminal(expected, intent, verified, claim)
        if confirmed == "ownership_lost":
            return "ownership_lost"
        if confirmed == "pending":
            return "pending"
        if expected.kind == "success":
            return expected.receipt
        if expected.kind == "failure":
            return None
        return "pending"

    def _non_result_terminal(
        self,
        claim: ClaimAuthority,
        verified: VerifiedImageBatchExecutionInput,
        *,
        kind: Literal["failure", "unknown"],
        result_value: str,
    ) -> _ExpectedTerminal:
        return _ExpectedTerminal(
            kind=kind,
            task_id=claim.scope.task_id,
            owner_id=claim.scope.user_id,
            unit_id=verified.unit_id,
            step_id=verified.step_id,
            submission_id="",
            result_value=result_value,
            task_status="failed" if kind == "failure" else "reconciling",
            unit_status="failed" if kind == "failure" else "unknown",
            step_status="failed" if kind == "failure" else "reconciling",
            submission_status="failed" if kind == "failure" else "unknown",
            stored=None,
            receipt=None,
            evidence_id=None,
            now=datetime.min.replace(tzinfo=timezone.utc),
        )

    def _confirm_terminal(
        self,
        expected: _ExpectedTerminal,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        claim: ClaimAuthority,
    ) -> Literal["confirmed", "pending", "ownership_lost"]:
        try:
            with self._session_factory() as session:
                graph = _lock_batch_graph(session, claim)
                if graph is None or not _graph_matches(
                    graph, claim, intent, verified, terminal=expected
                ):
                    return "pending"
                if expected.kind == "success":
                    evidence = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.id == expected.evidence_id,
                            ResultEvidenceRow.step_id == intent.step_id,
                            ResultEvidenceRow.submission_id
                            == intent.external_submission_id,
                            ResultEvidenceRow.validity == "valid",
                            ResultEvidenceRow.evidence_key
                            == f"image-batch-result-v1:{intent.external_submission_id}",
                            ResultEvidenceRow.result_digest
                            == expected.stored.content_digest,
                            ResultEvidenceRow.result_location == expected.stored.locator,
                        )
                    ).mappings().one_or_none()
                    receipt = GenerationRecordRepository(session).find_result_receipt(
                        expected.owner_id,
                        expected.task_id,
                        expected.receipt.result_ref,
                    )
                    if evidence is None or receipt != expected.receipt:
                        return "pending"
                    if graph.task.get("message_id"):
                        message = session.execute(
                            select(chat_messages.c.content).where(
                                chat_messages.c.id == graph.task["message_id"],
                                chat_messages.c.role == "assistant",
                            )
                        ).scalar_one_or_none()
                        if message != expected.stored.locator:
                            return "pending"
                if expected.kind == "failure":
                    if expected.refund is None:
                        refund_exists = session.execute(
                            select(CreditLogRow.id).where(
                                CreditLogRow.business_key == f"refund:{expected.unit_id}"
                            )
                        ).scalar_one_or_none()
                        if refund_exists is not None or expected.refunded_amount != 0:
                            return "pending"
                    elif not _confirm_refund(session, expected.refund):
                        return "pending"
                if (
                    expected.kind == "unknown"
                    and graph.task.get("recovery_status") != "reconciling"
                ):
                    return "pending"
                return "confirmed"
        except Exception:
            return "pending"

    def _confirm_unknown_observation(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        evidence_key: str,
        payload_digest: str,
        result_location: str | None,
        evidence_kind: str,
    ) -> Literal["recorded", "pending"]:
        try:
            with self._session_factory() as session:
                graph = _lock_batch_graph(session, claim)
                if graph is None or not _graph_identity_matches(
                    graph, claim, intent, verified
                ):
                    return "pending"
                evidence = session.execute(
                    select(ResultEvidenceRow.__table__).where(
                        ResultEvidenceRow.evidence_key == evidence_key,
                        ResultEvidenceRow.step_id == intent.step_id,
                        ResultEvidenceRow.submission_id == intent.external_submission_id,
                    )
                ).mappings().one_or_none()
                if evidence is None or not _observation_row_matches(
                    evidence,
                    intent,
                    payload_digest,
                    result_location,
                    evidence_kind,
                ):
                    return "pending"
                return "recorded"
        except Exception:
            return "pending"

    def _confirm_due(
        self,
        expected: _ExpectedTerminal,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> Literal["settled", "pending"]:
        if _is_stopping(stop_signal):
            return "pending"
        try:
            with self._session_factory() as session:
                graph = _lock_batch_graph(session, scope)
                if (
                    graph is None
                    or not _batch_identity_matches(graph, scope)
                    or not _is_due_terminal(graph, scope, expected.result_value)
                    or not _confirm_due_finances(
                        session, graph, scope, stop_signal=stop_signal
                    )
                ):
                    return "pending"
                return "settled"
        except Exception:
            return "pending"

    def _with_financial_state(
        self,
        session: Session,
        scope: OwnerTaskScope,
        graph: _LockedBatchGraph,
        expected: _ExpectedTerminal,
    ) -> _ExpectedTerminal:
        unit = graph.unit
        if expected.kind != "failure":
            amount = unit.get("debited_amount")
            refunded = unit.get("refunded_amount")
            if (
                type(amount) is not int
                or amount < 0
                or type(refunded) is not int
                or refunded != 0
                or unit.get("billing_status") not in {"charged", "no_charge"}
            ):
                raise _InvalidFinancialProof
            return replace(
                expected,
                task_billing_status=str(graph.task.get("billing_status")),
                unit_billing_status=str(unit.get("billing_status")),
                refunded_amount=refunded,
            )

        amount = unit.get("debited_amount")
        if (
            type(amount) is not int
            or type(unit.get("refunded_amount")) is not int
            or unit["refunded_amount"] != 0
        ):
            raise _InvalidFinancialProof
        if amount == 0:
            if (
                unit.get("billing_status") != "no_charge"
                or graph.task.get("billing_status") != "no_charge"
            ):
                raise _InvalidFinancialProof
            ledger_entries = session.execute(
                select(CreditLogRow.id)
                .where(
                    or_(
                        CreditLogRow.task_id == scope.task_id,
                        CreditLogRow.billing_unit_id == unit["id"],
                    )
                )
                .with_for_update()
            ).all()
            if ledger_entries:
                raise _InvalidFinancialProof
            return replace(
                expected,
                task_billing_status="no_charge",
                unit_billing_status="no_charge",
                refunded_amount=0,
            )
        if amount < 0 or unit.get("billing_status") != "charged":
            raise _InvalidFinancialProof
        wallet = session.execute(
            select(UserCreditRow.__table__)
            .where(UserCreditRow.user_id == scope.user_id)
            .with_for_update()
        ).mappings().one_or_none()
        debit = session.execute(
            select(CreditLogRow.__table__)
            .where(
                CreditLogRow.business_key == f"debit:{unit['id']}",
                CreditLogRow.user_id == scope.user_id,
                CreditLogRow.task_id == scope.task_id,
                CreditLogRow.billing_unit_id == unit["id"],
                CreditLogRow.type == "usage",
                CreditLogRow.amount == -amount,
                CreditLogRow.related_debit_id.is_(None),
            )
            .with_for_update()
        ).mappings().one_or_none()
        refund_key = f"refund:{unit['id']}"
        existing_refunds = (
            session.execute(
                select(CreditLogRow.id)
                .where(
                    or_(
                        CreditLogRow.business_key == refund_key,
                        and_(
                            CreditLogRow.type == "refund",
                            CreditLogRow.related_debit_id == debit["id"],
                        ),
                    )
                )
                .with_for_update()
            ).all()
            if debit is not None
            else []
        )
        if (
            debit is None
            or wallet is None
            or existing_refunds
            or type(wallet.get("credits")) is not int
        ):
            raise _InvalidFinancialProof
        refund_id = self._new_id()
        if refund_id is None:
            raise _InvalidFinancialProof
        settlement = _RefundSettlement(
            owner_id=scope.user_id,
            task_id=scope.task_id,
            unit_id=str(unit["id"]),
            debit_id=str(debit["id"]),
            refund_id=refund_id,
            refund_key=refund_key,
            amount=amount,
            balance_before=int(wallet["credits"]),
            balance_after=int(wallet["credits"]) + amount,
            description=expected.refund_description
            or "image.batch confirmed result failure refund",
        )
        return replace(
            expected,
            task_billing_status="refunded",
            unit_billing_status="refunded",
            refunded_amount=amount,
            refund=settlement,
        )

    def _new_id(self) -> str | None:
        try:
            value = self._id_factory()
        except Exception:
            return None
        return value if isinstance(value, str) and value else None

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


class _OwnershipLost(Exception):
    pass


class _InvalidGraph(Exception):
    pass


class _InvalidFinancialProof(Exception):
    pass


def _is_stopping(signal: Event | None) -> bool:
    return signal is not None and signal.is_set()


def _json_equal(session: Session, column, value: object):
    if session.get_bind().dialect.name == "postgresql":
        return cast(column, JSONB) == cast(value, JSONB)
    return column == value


def _lock_batch_graph(
    session: Session, authority: ClaimAuthority | OwnerTaskScope
) -> _LockedBatchGraph | None:
    scope = authority.scope if isinstance(authority, ClaimAuthority) else authority
    if type(scope) is not OwnerTaskScope or scope.operation != _OPERATION:
        return None
    task_table = AITaskRow.__table__
    parent_table = TaskSubmissionRow.__table__
    probe = session.execute(
        select(task_table).where(
            task_table.c.id == scope.task_id,
            task_table.c.user_id == scope.user_id,
            task_table.c.type == _TASK_TYPE,
        )
    ).mappings().one_or_none()
    if probe is None:
        return None
    request_data = _json_object(probe.get("request_data"))
    parent_id = None if request_data is None else request_data.get("submission_id")
    if not _nonempty_string(parent_id):
        return None
    parent_probe = session.execute(
        select(parent_table).where(
            parent_table.c.id == parent_id,
            parent_table.c.user_id == scope.user_id,
        )
    ).mappings().one_or_none()
    if parent_probe is None:
        return None
    ordered_task_ids = _ordered_task_ids(parent_probe.get("task_ids"))
    if ordered_task_ids is None or scope.task_id not in ordered_task_ids:
        return None

    user = session.execute(
        select(UserRow.__table__.c.id)
        .where(UserRow.__table__.c.id == scope.user_id)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if user is None:
        return None
    task_rows = session.execute(
        select(task_table)
        .where(
            task_table.c.id.in_(ordered_task_ids),
            task_table.c.user_id == scope.user_id,
            task_table.c.type == _TASK_TYPE,
        )
        .order_by(task_table.c.id)
        .with_for_update(skip_locked=True)
    ).mappings().all()
    if len(task_rows) != len(ordered_task_ids):
        return None
    tasks_by_id = {row["id"]: dict(row) for row in task_rows}
    if set(tasks_by_id) != set(ordered_task_ids):
        return None
    selected_probe = tasks_by_id.get(scope.task_id)
    if selected_probe is None or selected_probe.get("request_data") != probe.get("request_data"):
        return None

    unit_table = BillingUnitRow.__table__
    unit_rows = session.execute(
        select(unit_table)
        .where(unit_table.c.task_id.in_(ordered_task_ids))
        .order_by(unit_table.c.task_id, unit_table.c.id)
        .with_for_update()
    ).mappings().all()
    if len(unit_rows) != len(ordered_task_ids):
        return None
    units_by_task: dict[str, dict[str, object]] = {}
    for row in unit_rows:
        task_id = row["task_id"]
        if task_id in units_by_task:
            return None
        units_by_task[task_id] = dict(row)
    if set(units_by_task) != set(ordered_task_ids):
        return None

    unit_ids = tuple(units_by_task[task_id]["id"] for task_id in ordered_task_ids)
    step_table = ExecutionStepRow.__table__
    step_rows = session.execute(
        select(step_table)
        .where(step_table.c.billing_unit_id.in_(unit_ids))
        .order_by(step_table.c.billing_unit_id, step_table.c.step_order, step_table.c.id)
        .with_for_update()
    ).mappings().all()
    if len(step_rows) != len(ordered_task_ids):
        return None
    steps_by_unit: dict[str, dict[str, object]] = {}
    for row in step_rows:
        unit_id = row["billing_unit_id"]
        if unit_id in steps_by_unit:
            return None
        steps_by_unit[unit_id] = dict(row)
    if set(steps_by_unit) != set(unit_ids):
        return None
    steps_by_task = {
        task_id: steps_by_unit[units_by_task[task_id]["id"]]
        for task_id in ordered_task_ids
    }

    parent_row = session.execute(
        select(parent_table)
        .where(
            parent_table.c.id == parent_id,
            parent_table.c.user_id == scope.user_id,
        )
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if parent_row is None:
        return None
    parent = dict(parent_row)
    if (
        parent.get("id") != parent_id
        or parent.get("user_id") != scope.user_id
        or parent.get("operation") != _OPERATION
        or parent.get("status") != "accepted"
        or parent.get("task_ids") != list(ordered_task_ids)
        or not _nonempty_string(parent.get("idempotency_key"))
        or not _is_digest(parent.get("request_digest"))
    ):
        return None

    request_data_by_task: dict[str, dict[str, object]] = {}
    for index, task_id in enumerate(ordered_task_ids):
        task = tasks_by_id[task_id]
        unit = units_by_task[task_id]
        step = steps_by_task[task_id]
        request = _json_object(task.get("request_data"))
        snapshot = step.get("input_snapshot")
        if (
            request is None
            or not isinstance(snapshot, dict)
            or set(snapshot) != _STEP_SNAPSHOT_KEYS
            or not isinstance(task.get("message_id"), str)
            or not task.get("message_id")
            or type(task.get("credit_cost")) is not int
            or task.get("credit_cost") != unit.get("quoted_amount")
            or unit.get("task_id") != task_id
            or unit.get("operation") != _OPERATION
            or unit.get("step_graph_version") != _GRAPH_VERSION
            or unit.get("item_key") != f"{parent_id}:{index}"
            or unit.get("input_digest") != parent.get("request_digest")
            or step.get("billing_unit_id") != unit.get("id")
            or step.get("step_key") != "generate"
            or step.get("required") is not True
            or type(step.get("step_order")) is not int
            or step.get("step_order") != 0
            or step.get("dependencies") not in ([], ())
            or step.get("input_digest") != parent.get("request_digest")
            or request.get("operation") != _OPERATION
            or request.get("submission_id") != parent_id
            or request.get("parent_attempt_id") != parent_id
            or request.get("server_attempt_id") != f"{parent_id}:{index}"
            or request.get("billing_unit_id") != unit.get("id")
            or request.get("request_digest") != parent.get("request_digest")
            or request.get("step_graph_version") != _GRAPH_VERSION
            or request.get("prepared_payload_id") != snapshot.get("prepared_payload_id")
            or request.get("protected_ref") != snapshot.get("protected_ref")
            or request.get("snapshot_digest") != snapshot.get("snapshot_digest")
        ):
            return None
        request_data_by_task[task_id] = request

    selected_unit = units_by_task[scope.task_id]
    selected_step = steps_by_task[scope.task_id]
    submissions = session.execute(
        select(ExternalSubmissionRow.__table__)
        .where(ExternalSubmissionRow.__table__.c.step_id == selected_step["id"])
        .order_by(ExternalSubmissionRow.__table__.c.id)
        .with_for_update()
    ).mappings().all()
    if len(submissions) != 1:
        return None
    selected_submission = dict(submissions[0])
    return _LockedBatchGraph(
        parent=parent,
        ordered_task_ids=ordered_task_ids,
        tasks_by_id=tasks_by_id,
        units_by_task=units_by_task,
        steps_by_task=steps_by_task,
        request_data_by_task=request_data_by_task,
        task=tasks_by_id[scope.task_id],
        unit=selected_unit,
        step=selected_step,
        submission=selected_submission,
        request_data=request_data_by_task[scope.task_id],
    )


def _batch_identity_matches(
    graph: _LockedBatchGraph, scope: OwnerTaskScope
) -> bool:
    """Validate the ordered parent graph and the selected child's durable intent."""

    parent = graph.parent
    ordered_task_ids = graph.ordered_task_ids
    if (
        type(scope) is not OwnerTaskScope
        or scope.operation != _OPERATION
        or not _nonempty_string(scope.user_id)
        or not _nonempty_string(scope.task_id)
        or scope.task_id not in ordered_task_ids
        or not 1 <= len(ordered_task_ids) <= _MAX_BATCH_ITEMS
        or len(set(ordered_task_ids)) != len(ordered_task_ids)
        or parent.get("user_id") != scope.user_id
        or parent.get("operation") != _OPERATION
        or parent.get("status") != "accepted"
        or parent.get("task_ids") != list(ordered_task_ids)
        or not _nonempty_string(parent.get("idempotency_key"))
        or not _is_digest(parent.get("request_digest"))
    ):
        return False

    parent_id = parent.get("id")
    request_digest = parent.get("request_digest")
    for index, task_id in enumerate(ordered_task_ids):
        task = graph.tasks_by_id.get(task_id)
        unit = graph.units_by_task.get(task_id)
        step = graph.steps_by_task.get(task_id)
        request = graph.request_data_by_task.get(task_id)
        if task is None or unit is None or step is None or request is None:
            return False
        step_snapshot = step.get("input_snapshot")
        if (
            not isinstance(step_snapshot, dict)
            or set(step_snapshot) != _STEP_SNAPSHOT_KEYS
            or task.get("id") != task_id
            or task.get("user_id") != scope.user_id
            or task.get("type") != _TASK_TYPE
            or not _nonempty_string(task.get("message_id"))
            or type(task.get("credit_cost")) is not int
            or task.get("credit_cost") != unit.get("quoted_amount")
            or unit.get("id") != graph.units_by_task[task_id].get("id")
            or unit.get("task_id") != task_id
            or unit.get("operation") != _OPERATION
            or unit.get("step_graph_version") != _GRAPH_VERSION
            or unit.get("item_key") != f"{parent_id}:{index}"
            or unit.get("input_digest") != request_digest
            or step.get("billing_unit_id") != unit.get("id")
            or step.get("step_key") != "generate"
            or step.get("required") is not True
            or type(step.get("step_order")) is not int
            or step.get("step_order") != 0
            or step.get("dependencies") not in ([], ())
            or step.get("input_digest") != request_digest
            or request.get("operation") != _OPERATION
            or request.get("submission_id") != parent_id
            or request.get("parent_attempt_id") != parent_id
            or request.get("server_attempt_id") != f"{parent_id}:{index}"
            or request.get("billing_unit_id") != unit.get("id")
            or request.get("request_digest") != request_digest
            or request.get("step_graph_version") != _GRAPH_VERSION
            or request.get("prepared_payload_id")
            != step_snapshot.get("prepared_payload_id")
            or request.get("protected_ref") != step_snapshot.get("protected_ref")
            or request.get("snapshot_digest") != step_snapshot.get("snapshot_digest")
            or not _is_digest(step_snapshot.get("snapshot_digest"))
            or not _is_digest(step_snapshot.get("ordered_manifest_digest"))
        ):
            return False

    task = graph.task
    unit = graph.unit
    step = graph.step
    request = graph.request_data
    submission = graph.submission
    step_snapshot = step.get("input_snapshot")
    intent_snapshot = submission.get("request_snapshot")
    index = ordered_task_ids.index(scope.task_id)
    first_intent = unit.get("first_intent_at")
    deadline = unit.get("deadline_at")
    if (
        not isinstance(step_snapshot, dict)
        or not isinstance(intent_snapshot, dict)
        or set(intent_snapshot) != _INTENT_SNAPSHOT_KEYS
        or not isinstance(first_intent, datetime)
        or not isinstance(deadline, datetime)
    ):
        return False
    prepared_id = request.get("prepared_payload_id")
    snapshot_digest = request.get("snapshot_digest")
    manifest_digest = step_snapshot.get("ordered_manifest_digest")
    protected_ref = request.get("protected_ref")
    provider = submission.get("provider")
    model_id = submission.get("model_ref")
    binding_ref = intent_snapshot.get("credential_binding_ref")
    step_id = step.get("id")
    unit_id = unit.get("id")
    submission_id = submission.get("id")
    attempt_id = request.get("server_attempt_id")
    if not all(
        _nonempty_string(value)
        for value in (
            prepared_id,
            snapshot_digest,
            manifest_digest,
            protected_ref,
            provider,
            model_id,
            binding_ref,
            step_id,
            unit_id,
            submission_id,
            attempt_id,
        )
    ):
        return False
    snapshot_ref = (
        f"{_GRAPH_VERSION}:{prepared_id}:{snapshot_digest}:{manifest_digest}"
    )
    expected_snapshot = {
        "owner_id": scope.user_id,
        "task_id": scope.task_id,
        "unit_id": unit_id,
        "step_id": step_id,
        "submission_id": parent_id,
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
        "item_index": index,
        "ordered_count": len(ordered_task_ids),
        "attempt_id": attempt_id,
        "prepared_payload_id": prepared_id,
        "protected_ref": protected_ref,
        "snapshot_digest": snapshot_digest,
        "snapshot_ref": snapshot_ref,
        "credential_binding_ref": binding_ref,
        "target_snapshot": request.get("target_snapshot"),
        "provider": provider,
        "model_id": model_id,
    }
    first_intent_utc = _aware_utc(first_intent)
    deadline_utc = _aware_utc(deadline)
    return all(
        (
            task.get("id") == scope.task_id,
            task.get("user_id") == scope.user_id,
            task.get("type") == _TASK_TYPE,
            task.get("model_name") == request.get("model_display_name"),
            unit.get("id") == unit_id,
            unit.get("task_id") == scope.task_id,
            unit.get("operation") == _OPERATION,
            unit.get("step_graph_version") == _GRAPH_VERSION,
            unit.get("item_key") == attempt_id,
            unit.get("input_digest") == request_digest,
            step.get("id") == step_id,
            step.get("billing_unit_id") == unit_id,
            step.get("input_digest") == request_digest,
            request.get("target_ref") == f"{protected_ref}#target",
            request.get("model_ref") == f"{protected_ref}#model",
            request.get("grant_ref") == f"{protected_ref}#grant",
            submission.get("id") == submission_id,
            submission.get("step_id") == step_id,
            submission.get("submission_key")
            == f"image-batch-create-v1:{unit_id}:{step_id}",
            submission.get("provider") == provider,
            submission.get("model_ref") == model_id,
            submission.get("request_digest") == request_digest,
            submission.get("external_task_id") is None,
            submission.get("submitted_at") is None
            or submission.get("submission_status") != "submitting",
            intent_snapshot == expected_snapshot,
            _time_matches(unit.get("first_intent_at"), first_intent_utc),
            _time_matches(unit.get("deadline_at"), deadline_utc),
            _time_matches(submission.get("intent_at"), first_intent_utc),
            _time_matches(submission.get("deadline_at"), deadline_utc),
            deadline_utc == first_intent_utc + _ORIGINAL_DEADLINE,
        )
    )


def _valid_intent(
    intent: CommittedIntent, verified: VerifiedImageBatchExecutionInput
) -> bool:
    if type(intent) is not CommittedIntent or type(verified) is not VerifiedImageBatchExecutionInput:
        return False
    return all(
        (
            _nonempty_string(verified.task_id),
            _nonempty_string(verified.unit_id),
            _nonempty_string(verified.step_id),
            _nonempty_string(verified.submission_id),
            _nonempty_string(verified.server_attempt_id),
            _nonempty_string(verified.prepared_payload_id),
            _nonempty_string(verified.task_model_name),
            type(verified.item_index) is int,
            type(verified.ordered_count) is int,
            0 <= verified.item_index < verified.ordered_count <= _MAX_BATCH_ITEMS,
            intent.step_id == verified.step_id,
            intent.attempt_id == verified.server_attempt_id,
            intent.snapshot_ref.startswith(
                f"{_GRAPH_VERSION}:{verified.prepared_payload_id}:"
            ),
            intent.provider == verified.snapshot.model_ref.provider,
            intent.model_id == verified.snapshot.model_ref.model_id,
            _is_digest(intent.request_digest),
            intent.first_intent_at == intent.committed_at,
            isinstance(intent.first_intent_at, datetime),
            isinstance(intent.deadline_at, datetime),
            _aware_utc(intent.deadline_at)
            == _aware_utc(intent.first_intent_at) + _ORIGINAL_DEADLINE,
        )
    )


def _graph_matches(
    graph: _LockedBatchGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedImageBatchExecutionInput,
    *,
    terminal: _ExpectedTerminal | None = None,
    task_only: bool = False,
) -> bool:
    if not _graph_identity_matches(graph, claim, intent, verified):
        return False

    task, unit, step, submission = (
        graph.task,
        graph.unit,
        graph.step,
        graph.submission,
    )
    if terminal is None:
        expected_task_status = {"processing", "reconciling"}
        expected_task_result: object = None
        expected_unit_status = "pending"
        expected_step_status = "submitting"
        expected_submission_status = "submitting"
        expected_task_billing = unit.get("billing_status")
        expected_unit_billing = unit.get("billing_status")
        expected_refunded_amount = unit.get("refunded_amount")
    else:
        expected_task_status = {terminal.task_status}
        expected_task_result = terminal.result_value
        expected_unit_status = "pending" if task_only else terminal.unit_status
        expected_step_status = "submitting" if task_only else terminal.step_status
        expected_submission_status = (
            "submitting" if task_only else terminal.submission_status
        )
        expected_task_billing = (
            task.get("billing_status")
            if task_only and terminal.kind != "failure"
            else terminal.task_billing_status
        )
        expected_unit_billing = (
            unit.get("billing_status")
            if task_only or terminal.kind != "failure"
            else terminal.unit_billing_status
        )
        expected_refunded_amount = (
            unit.get("refunded_amount")
            if task_only or terminal.kind != "failure"
            else terminal.refunded_amount
        )
        if (
            terminal.original_graph is not None
            and not _same_static_graph(
                terminal.original_graph, graph, claim.scope.task_id
            )
        ):
            return False
        expected_progress = 75 if terminal.kind == "unknown" else 100
        expected_message = (
            _TERMINAL_MESSAGE
            if terminal.kind == "unknown"
            else "图片已生成"
            if terminal.kind == "success"
            else "图片生成失败"
        )
        if (
            task.get("progress") != expected_progress
            or task.get("progress_message") != expected_message
            or task.get("external_provider") != intent.provider
            or (
                terminal.kind == "unknown"
                and task.get("recovery_status") != "reconciling"
            )
        ):
            return False

    matches = all(
        (
            task.get("status") in expected_task_status,
            task.get("result") == expected_task_result,
            task.get("billing_status") == expected_task_billing,
            task.get("user_cancelled_at") is None,
            unit.get("billing_status") == expected_unit_billing,
            unit.get("result_status") == expected_unit_status,
            unit.get("refunded_amount") == expected_refunded_amount,
            unit.get("user_cancelled_at") is None,
            step.get("execution_status") == expected_step_status,
            submission.get("submission_status") == expected_submission_status,
            submission.get("external_task_id") is None,
        )
    )
    if not matches:
        return False
    if terminal is None:
        return unit.get("success_confirmed_at") is None and unit.get("refunded_amount") == 0
    if task_only:
        return unit.get("success_confirmed_at") is None
    if not _time_matches(submission.get("submitted_at"), terminal.now):
        return False
    if terminal.kind == "success":
        return (
            terminal.receipt is not None
            and unit.get("refunded_amount") == 0
            and _time_matches(
                unit.get("success_confirmed_at"),
                terminal.receipt.generation_fulfilled_at,
            )
        )
    return unit.get("success_confirmed_at") is None


def _graph_identity_matches(
    graph: _LockedBatchGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedImageBatchExecutionInput,
) -> bool:
    """Check the original durable item identity without requiring a live claim."""

    if (
        type(claim) is not ClaimAuthority
        or not _valid_intent(intent, verified)
        or verified.task_id != claim.scope.task_id
        or verified.submission_id != graph.parent.get("id")
        or not _batch_identity_matches(graph, claim.scope)
    ):
        return False
    task, unit, step, request = (
        graph.task,
        graph.unit,
        graph.step,
        graph.request_data,
    )
    submission = graph.submission
    step_snapshot = step.get("input_snapshot")
    intent_snapshot = submission.get("request_snapshot")
    snapshot = verified.snapshot
    binding = verified.credential_binding
    if not isinstance(step_snapshot, dict) or not isinstance(intent_snapshot, dict):
        return False
    expected_intent_snapshot = {
        "owner_id": claim.scope.user_id,
        "task_id": verified.task_id,
        "unit_id": verified.unit_id,
        "step_id": verified.step_id,
        "submission_id": verified.submission_id,
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
        "item_index": verified.item_index,
        "ordered_count": verified.ordered_count,
        "attempt_id": verified.server_attempt_id,
        "prepared_payload_id": verified.prepared_payload_id,
        "protected_ref": step_snapshot.get("protected_ref"),
        "snapshot_digest": step_snapshot.get("snapshot_digest"),
        "snapshot_ref": intent.snapshot_ref,
        "credential_binding_ref": snapshot.credential_binding_ref,
        "target_snapshot": request.get("target_snapshot"),
        "provider": snapshot.model_ref.provider,
        "model_id": snapshot.model_ref.model_id,
    }
    expected_ref = (
        f"{_GRAPH_VERSION}:{verified.prepared_payload_id}:"
        f"{step_snapshot.get('snapshot_digest')}:"
        f"{step_snapshot.get('ordered_manifest_digest')}"
    )
    return all(
        (
            intent_snapshot == expected_intent_snapshot,
            task.get("id") == claim.scope.task_id,
            task.get("user_id") == claim.scope.user_id,
            task.get("type") == _TASK_TYPE,
            task.get("model_name") == verified.task_model_name,
            request.get("model_display_name") == verified.task_model_name,
            task.get("credit_cost") == unit.get("quoted_amount") == snapshot.server_quote,
            request.get("operation") == _OPERATION,
            request.get("submission_id") == graph.parent.get("id"),
            request.get("parent_attempt_id") == graph.parent.get("id"),
            request.get("server_attempt_id") == verified.server_attempt_id,
            request.get("billing_unit_id") == verified.unit_id,
            request.get("prepared_payload_id") == verified.prepared_payload_id,
            request.get("protected_ref") == step_snapshot.get("protected_ref"),
            request.get("snapshot_digest") == step_snapshot.get("snapshot_digest"),
            request.get("request_digest") == intent.request_digest,
            request.get("model_id") == snapshot.model_ref.model_id,
            request.get("model_source") == binding.source,
            request.get("model_owner_id")
            == (None if binding.source == "system" else binding.owner_or_system),
            request.get("target_snapshot") == expected_intent_snapshot["target_snapshot"],
            verified.item_index == graph.ordered_task_ids.index(claim.scope.task_id),
            verified.ordered_count == len(graph.ordered_task_ids),
            step.get("id") == intent.step_id == verified.step_id,
            step.get("billing_unit_id") == verified.unit_id,
            step.get("step_key") == "generate",
            step.get("required") is True,
            step.get("step_order") == 0,
            step.get("dependencies") in ([], ()),
            step.get("input_digest") == intent.request_digest,
            unit.get("id") == verified.unit_id,
            unit.get("task_id") == claim.scope.task_id,
            unit.get("operation") == _OPERATION,
            unit.get("step_graph_version") == _GRAPH_VERSION,
            unit.get("item_key") == verified.server_attempt_id,
            unit.get("input_digest") == intent.request_digest,
            graph.parent.get("id") == request.get("submission_id"),
            graph.parent.get("user_id") == claim.scope.user_id,
            graph.parent.get("operation") == _OPERATION,
            graph.parent.get("task_ids") == list(graph.ordered_task_ids),
            submission.get("id") == intent.external_submission_id,
            submission.get("step_id") == intent.step_id,
            submission.get("submission_key")
            == f"image-batch-create-v1:{verified.unit_id}:{intent.step_id}",
            submission.get("provider") == intent.provider == snapshot.model_ref.provider,
            submission.get("model_ref") == intent.model_id == snapshot.model_ref.model_id,
            submission.get("request_digest") == intent.request_digest,
            _time_matches(unit.get("first_intent_at"), intent.first_intent_at),
            _time_matches(unit.get("deadline_at"), intent.deadline_at),
            _time_matches(submission.get("intent_at"), intent.first_intent_at),
            _time_matches(submission.get("deadline_at"), intent.deadline_at),
            intent.snapshot_ref == expected_ref,
            intent.step_id == verified.step_id,
            intent.attempt_id == verified.server_attempt_id,
            binding.provider == snapshot.model_ref.provider,
            binding.row_id == snapshot.model_ref.model_id,
            binding.owner_or_system == snapshot.model_ref.owner_id,
        )
    )
def _same_static_graph(
    original: _LockedBatchGraph,
    current: _LockedBatchGraph,
    selected_task_id: str | None = None,
) -> bool:
    if (
        original.parent != current.parent
        or original.ordered_task_ids != current.ordered_task_ids
        or original.request_data_by_task != current.request_data_by_task
    ):
        return False
    mutable_task = {
        "status",
        "result",
        "progress",
        "progress_message",
        "external_provider",
        "recovery_status",
        "billing_status",
        "updated_at",
    }
    mutable_unit = {
        "result_status",
        "success_confirmed_at",
        "billing_status",
        "refunded_amount",
        "updated_at",
    }
    mutable_step = {"execution_status", "updated_at"}
    for task_id in original.ordered_task_ids:
        pairs = (
            (original.tasks_by_id[task_id], current.tasks_by_id[task_id], mutable_task),
            (original.units_by_task[task_id], current.units_by_task[task_id], mutable_unit),
            (original.steps_by_task[task_id], current.steps_by_task[task_id], mutable_step),
        )
        for before, after, mutable in pairs:
            ignored = mutable if task_id == selected_task_id else set()
            if {k: v for k, v in before.items() if k not in ignored} != {
                k: v for k, v in after.items() if k not in ignored
            }:
                return False
    if selected_task_id is None:
        return original.submission == current.submission
    submission_mutable = {"submission_status", "submitted_at", "updated_at"}
    return {
        k: v for k, v in original.submission.items() if k not in submission_mutable
    } == {
        k: v for k, v in current.submission.items() if k not in submission_mutable
    }


def _claim_is_current(
    task: Mapping[str, object], claim: ClaimAuthority, now: datetime
) -> bool:
    lease = task.get("lease_until")
    return all(
        (
            task.get("id") == claim.scope.task_id,
            task.get("user_id") == claim.scope.user_id,
            task.get("type") == _TASK_TYPE,
            claim.scope.operation == _OPERATION,
            task.get("status") in {"processing", "reconciling"},
            task.get("user_cancelled_at") is None,
            task.get("claimed_by") == claim.worker_instance,
            task.get("claim_token") == claim.token,
            task.get("execution_generation") == claim.execution_generation,
            isinstance(lease, datetime),
            isinstance(lease, datetime) and _aware_utc(lease) > now,
            not claim.local_lost_signal.is_set(),
        )
    )


def _valid_due_graph(
    graph: _LockedBatchGraph, scope: OwnerTaskScope, now: datetime
) -> bool:
    if not _batch_identity_matches(graph, scope):
        return False
    task, unit, step, submission = (
        graph.task,
        graph.unit,
        graph.step,
        graph.submission,
    )
    deadline = unit.get("deadline_at")
    amount = unit.get("debited_amount")
    return all(
        (
            task.get("status") in {"processing", "reconciling", "queued"},
            task.get("result") in (None, _UNRESOLVED_RESULT),
            task.get("user_cancelled_at") is None,
            task.get("billing_status") in {"charged", "no_charge"},
            unit.get("billing_status") == task.get("billing_status"),
            unit.get("result_status") in {"pending", "unknown"},
            type(amount) is int and amount >= 0,
            unit.get("refunded_amount") == 0,
            unit.get("user_cancelled_at") is None,
            unit.get("success_confirmed_at") is None,
            step.get("execution_status") in {"submitting", "reconciling"},
            submission.get("submission_status") in {"submitting", "unknown"},
            submission.get("external_task_id") is None,
            isinstance(deadline, datetime),
            isinstance(deadline, datetime) and _aware_utc(now) >= _aware_utc(deadline),
        )
    )


def _due_terminal(scope: OwnerTaskScope) -> _ExpectedTerminal:
    return _ExpectedTerminal(
        kind="failure",
        task_id=scope.task_id,
        owner_id=scope.user_id,
        unit_id="",
        step_id="",
        submission_id="",
        result_value=json.dumps(
            {
                "code": "result_unconfirmed",
                "message": "批量图片结果未能在期限内确认",
                "status": "failed",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        task_status="failed",
        unit_status="failed",
        step_status="failed",
        submission_status="unknown",
        stored=None,
        receipt=None,
        evidence_id=None,
        now=datetime.min.replace(tzinfo=timezone.utc),
        refund_description="image.batch fixed-deadline refund",
    )


def _is_due_terminal(
    graph: _LockedBatchGraph, scope: OwnerTaskScope, result_value: str
) -> bool:
    return all(
        (
            graph.task.get("id") == scope.task_id,
            graph.task.get("user_id") == scope.user_id,
            graph.task.get("type") == _TASK_TYPE,
            graph.task.get("status") == "failed",
            graph.task.get("result") == result_value,
            graph.unit.get("task_id") == scope.task_id,
            graph.unit.get("operation") == _OPERATION,
            graph.unit.get("result_status") == "failed",
            graph.step.get("execution_status") == "failed",
            graph.submission.get("submission_status") == "unknown",
            graph.submission.get("external_task_id") is None,
            isinstance(graph.submission.get("submitted_at"), datetime),
        )
    )


def _graph_matches_due(
    graph: _LockedBatchGraph,
    scope: OwnerTaskScope,
    expected: _ExpectedTerminal,
) -> bool:
    if (
        not _batch_identity_matches(graph, scope)
        or not _is_due_terminal(graph, scope, expected.result_value)
        or expected.original_graph is None
        or not _same_static_graph(expected.original_graph, graph, scope.task_id)
    ):
        return False
    return all(
        (
            graph.task.get("billing_status") == expected.task_billing_status,
            graph.unit.get("billing_status") == expected.unit_billing_status,
            graph.unit.get("refunded_amount") == expected.refunded_amount,
        )
    )


def _has_timely_linked_success(
    session: Session, graph: _LockedBatchGraph, scope: OwnerTaskScope
) -> bool:
    deadline_value = graph.unit.get("deadline_at")
    if not isinstance(deadline_value, datetime):
        return False
    deadline = _aware_utc(deadline_value)
    receipts = session.execute(
        select(GenerationResultReceiptRow.__table__).where(
            GenerationResultReceiptRow.owner_id == scope.user_id,
            GenerationResultReceiptRow.task_id == scope.task_id,
            GenerationResultReceiptRow.unit_id == graph.unit.get("id"),
            GenerationResultReceiptRow.operation == _OPERATION,
            GenerationResultReceiptRow.step_id == graph.step.get("id"),
            GenerationResultReceiptRow.submission_id == graph.submission.get("id"),
        )
    ).mappings().all()
    if len(receipts) != 1:
        return False
    receipt = receipts[0]
    validated_at = receipt.get("validated_at")
    fulfilled_at = receipt.get("generation_fulfilled_at")
    digest = receipt.get("content_digest")
    locator = receipt.get("recoverable_locator")
    if (
        not isinstance(validated_at, datetime)
        or not isinstance(fulfilled_at, datetime)
        or not _is_digest(digest)
        or not _nonempty_string(locator)
        or _aware_utc(validated_at) >= deadline
        or _aware_utc(fulfilled_at) >= deadline
        or receipt.get("selected_attempt_id")
        != graph.request_data.get("server_attempt_id")
        or receipt.get("validation_version") != "image-batch-media-v1"
        or receipt.get("late_disposition") is not None
        or receipt.get("policy_stop_disposition") is not None
        or receipt.get("automatic_application_allowed") is not True
    ):
        return False
    evidence = session.execute(
        select(ResultEvidenceRow.__table__).where(
            ResultEvidenceRow.id == receipt.get("evidence_id"),
            ResultEvidenceRow.step_id == graph.step.get("id"),
            ResultEvidenceRow.submission_id == graph.submission.get("id"),
        )
    ).mappings().one_or_none()
    if (
        evidence is None
        or evidence.get("source") != "provider"
        or evidence.get("evidence_kind") != "provider_image"
        or evidence.get("evidence_key")
        != f"image-batch-result-v1:{graph.submission.get('id')}"
        or evidence.get("validity") != "valid"
        or evidence.get("result_digest") != digest
        or evidence.get("payload_digest") != digest
        or evidence.get("result_location") != locator
    ):
        return False
    try:
        expected_result = json.dumps(
            {
                "kind": "image",
                "url": locator,
                "result_ref": receipt["result_ref"],
                "content_digest": digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (KeyError, TypeError, ValueError):
        return False
    if not all(
        (
            graph.task.get("status") == "completed",
            graph.task.get("result") == expected_result,
            graph.task.get("billing_status") == graph.unit.get("billing_status"),
            graph.task.get("external_provider") == graph.submission.get("provider"),
            graph.unit.get("billing_status") in {"charged", "no_charge"},
            graph.unit.get("result_status") == "succeeded",
            graph.unit.get("refunded_amount") == 0,
            _time_matches(graph.unit.get("success_confirmed_at"), fulfilled_at),
            graph.step.get("execution_status") == "result_observed",
            graph.submission.get("submission_status") == "completed",
            graph.submission.get("external_task_id") is None,
        )
    ):
        return False
    message_id = graph.task.get("message_id")
    if not message_id:
        return True
    message = session.execute(
        select(chat_messages.c.content).where(
            chat_messages.c.id == message_id,
            chat_messages.c.role == "assistant",
        )
    ).scalar_one_or_none()
    return message == locator


def _batch_target_snapshot(value: object) -> TargetSnapshot | None:
    fields = {
        "kind",
        "chapter_id",
        "series_id",
        "stable_storyboard_asset_id",
        "asset_type",
        "asset_id",
        "compat_frame_index",
        "expected_text_digest",
        "media_identity",
        "content_digest",
        "notes_revision",
        "full_frame_digest",
        "full_chapter_digest",
        "expected_asset_image_url",
        "same_series_target_digest",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        return None
    if (
        value.get("kind") != "asset_message"
        or any(
            not _nonempty_string(value.get(name))
            for name in ("chapter_id", "series_id", "asset_type", "asset_id")
        )
        or value.get("asset_type") not in {"character", "scene", "prop", "storyboard"}
    ):
        return None
    optional_strings = fields - {
        "kind",
        "chapter_id",
        "series_id",
        "asset_type",
        "asset_id",
        "compat_frame_index",
        "notes_revision",
    }
    if any(
        item is not None and not isinstance(item, str)
        for key in optional_strings
        if (item := value.get(key)) is not None
    ):
        return None
    if value.get("compat_frame_index") is not None or value.get("notes_revision") is not None:
        return None
    try:
        return TargetSnapshot(**dict(value))
    except (TypeError, ValueError):
        return None


def _read_batch_application(
    session: Session,
    receipt: GenerationResultReceipt,
    target_scope: str,
) -> ApplicationOutcome | None:
    probe = session.execute(
        select(ApplicationRecordRow)
        .where(
            ApplicationRecordRow.result_ref == receipt.result_ref,
            ApplicationRecordRow.target_scope == target_scope,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if probe is None:
        return None
    record = find_application_record(
        session,
        receipt.owner_id,
        receipt.task_id,
        receipt.result_ref,
        target_scope,
    )
    if record is None or not _batch_application_record_matches(
        record, receipt, target_scope
    ):
        return ApplicationOutcome(
            "write_failed", receipt.result_ref, None, None,
            "existing batch application receipt could not be verified",
        )
    evidence = session.execute(
        select(
            ResultEvidenceRow.__table__.c.application_status,
            ResultEvidenceRow.__table__.c.applied_at,
        ).where(
            ResultEvidenceRow.__table__.c.id == receipt.evidence_id,
            ResultEvidenceRow.__table__.c.step_id == receipt.step_id,
            ResultEvidenceRow.__table__.c.submission_id == receipt.submission_id,
            ResultEvidenceRow.__table__.c.validity == "valid",
        )
    ).mappings().one_or_none()
    expected_status = "applied" if record.delivery_status == "applied" else "rejected"
    if (
        evidence is None
        or evidence.get("application_status") != expected_status
        or (expected_status == "applied") != (evidence.get("applied_at") is not None)
    ):
        return ApplicationOutcome(
            "write_failed", receipt.result_ref, None, None,
            "existing batch application evidence could not be verified",
        )
    if record.delivery_status == "applied":
        return ApplicationOutcome(
            "already_applied", receipt.result_ref, record.application_ref,
            record.after_target_digest,
        )
    return ApplicationOutcome(
        record.delivery_status, receipt.result_ref, record.application_ref,
        record.after_target_digest,
    )


def _batch_application_record_matches(
    record: ApplicationRecord,
    receipt: GenerationResultReceipt,
    target_scope: str,
) -> bool:
    if not all(
        (
            record.application_ref == application_ref_for(receipt.result_ref, target_scope),
            record.result_ref == receipt.result_ref,
            record.owner_id == receipt.owner_id,
            record.task_id == receipt.task_id,
            record.unit_id == receipt.unit_id,
            record.target_scope == target_scope,
            record.result_digest == receipt.content_digest,
            record.committed_at is not None,
        )
    ):
        return False
    if record.delivery_status == "applied":
        return _is_digest(record.before_target_digest) and _is_digest(
            record.after_target_digest
        )
    if record.delivery_status in {"target_conflict", "target_deleted"}:
        return (
            record.before_target_digest == record.after_target_digest
            and (
                record.before_target_digest is None
                or _is_digest(record.before_target_digest)
            )
        )
    return False


def _confirm_due_finances(
    session: Session,
    graph: _LockedBatchGraph,
    scope: OwnerTaskScope,
    *,
    stop_signal: Event | None = None,
) -> bool:
    unit = graph.unit
    amount = unit.get("debited_amount")
    refunded = unit.get("refunded_amount")
    if type(amount) is not int or type(refunded) is not int or _is_stopping(stop_signal):
        return False
    if amount == 0:
        if (
            unit.get("billing_status") != "no_charge"
            or graph.task.get("billing_status") != "no_charge"
            or refunded != 0
        ):
            return False
        entries = session.execute(
            select(CreditLogRow.id).where(
                or_(
                    CreditLogRow.task_id == scope.task_id,
                    CreditLogRow.billing_unit_id == unit.get("id"),
                )
            )
        ).all()
        return not _is_stopping(stop_signal) and not entries
    if (
        amount < 0
        or refunded != amount
        or unit.get("billing_status") != "refunded"
        or graph.task.get("billing_status") != "refunded"
        or _is_stopping(stop_signal)
    ):
        return False
    debit = session.execute(
        select(CreditLogRow.__table__).where(
            CreditLogRow.business_key == f"debit:{unit.get('id')}",
            CreditLogRow.user_id == scope.user_id,
            CreditLogRow.task_id == scope.task_id,
            CreditLogRow.billing_unit_id == unit.get("id"),
            CreditLogRow.type == "usage",
            CreditLogRow.amount == -amount,
            CreditLogRow.related_debit_id.is_(None),
        )
    ).mappings().one_or_none()
    if debit is None:
        return False
    refunds = session.execute(
        select(CreditLogRow.__table__).where(
            or_(
                CreditLogRow.business_key == f"refund:{unit.get('id')}",
                and_(
                    CreditLogRow.type == "refund",
                    CreditLogRow.related_debit_id == debit.get("id"),
                ),
            )
        )
    ).mappings().all()
    return (
        not _is_stopping(stop_signal)
        and len(refunds) == 1
        and refunds[0].get("user_id") == scope.user_id
        and refunds[0].get("task_id") == scope.task_id
        and refunds[0].get("billing_unit_id") == unit.get("id")
        and refunds[0].get("related_debit_id") == debit.get("id")
        and refunds[0].get("business_key") == f"refund:{unit.get('id')}"
        and refunds[0].get("type") == "refund"
        and refunds[0].get("amount") == amount
        and type(refunds[0].get("balance_after")) is int
    )


def _apply_refund(
    session: Session, settlement: _RefundSettlement, now: datetime
) -> None:
    wallet_change = session.execute(
        update(UserCreditRow.__table__)
        .where(
            UserCreditRow.user_id == settlement.owner_id,
            UserCreditRow.credits == settlement.balance_before,
        )
        .values(
            credits=settlement.balance_after,
            updated_at=_database_datetime(now),
        )
    )
    if wallet_change.rowcount != 1:
        raise _InvalidFinancialProof
    session.execute(
        insert(CreditLogRow.__table__).values(
            id=settlement.refund_id,
            user_id=settlement.owner_id,
            amount=settlement.amount,
            balance_after=settlement.balance_after,
            type="refund",
            description=settlement.description,
            task_id=settlement.task_id,
            business_key=settlement.refund_key,
            billing_unit_id=settlement.unit_id,
            related_debit_id=settlement.debit_id,
            created_at=_database_datetime(now),
        )
    )


def _confirm_refund(session: Session, settlement: _RefundSettlement) -> bool:
    debit = session.execute(
        select(CreditLogRow.__table__).where(
            CreditLogRow.id == settlement.debit_id,
            CreditLogRow.user_id == settlement.owner_id,
            CreditLogRow.task_id == settlement.task_id,
            CreditLogRow.billing_unit_id == settlement.unit_id,
            CreditLogRow.type == "usage",
            CreditLogRow.amount == -settlement.amount,
            CreditLogRow.business_key == f"debit:{settlement.unit_id}",
            CreditLogRow.related_debit_id.is_(None),
        )
    ).mappings().one_or_none()
    refund = session.execute(
        select(CreditLogRow.__table__).where(
            CreditLogRow.id == settlement.refund_id,
            CreditLogRow.business_key == settlement.refund_key,
            CreditLogRow.related_debit_id == settlement.debit_id,
        )
    ).mappings().one_or_none()
    return bool(
        debit is not None
        and refund is not None
        and refund.get("user_id") == settlement.owner_id
        and refund.get("task_id") == settlement.task_id
        and refund.get("billing_unit_id") == settlement.unit_id
        and refund.get("type") == "refund"
        and refund.get("amount") == settlement.amount
        and refund.get("balance_after") == settlement.balance_after
    )


def _valid_observation(
    digest: str, locator: str | None, evidence_kind: str
) -> bool:
    if not _is_digest(digest) or evidence_kind not in {
        "provider_image_bytes",
        "provider_image_locator",
    }:
        return False
    if locator is None:
        return evidence_kind == "provider_image_bytes"
    if not isinstance(locator, str) or len(locator) > 2048 or not _safe_text(locator, 2048):
        return False
    try:
        parsed = urlsplit(locator)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _observation_row_matches(
    row: Mapping[str, object],
    intent: CommittedIntent,
    digest: str,
    locator: str | None,
    evidence_kind: str,
) -> bool:
    return all(
        (
            row.get("step_id") == intent.step_id,
            row.get("submission_id") == intent.external_submission_id,
            row.get("evidence_key")
            == f"image-batch-observation-v1:{intent.external_submission_id}",
            row.get("source") == "provider",
            row.get("evidence_kind") == evidence_kind,
            row.get("payload_digest") == digest,
            row.get("result_digest") == digest,
            row.get("result_location") == locator,
            row.get("validity") == "unknown",
            row.get("application_status") == "pending",
            row.get("applied_at") is None,
        )
    )


def _project_assistant_message(
    session: Session, message_id: str, locator: str
) -> bool:
    message = session.execute(
        select(chat_messages)
        .where(chat_messages.c.id == message_id)
        .with_for_update()
    ).mappings().one_or_none()
    if (
        message is None
        or message.get("role") != "assistant"
        or message.get("content") != "正在生成图片..."
    ):
        return False
    changed = session.execute(
        update(chat_messages)
        .where(
            chat_messages.c.id == message_id,
            chat_messages.c.role == "assistant",
            chat_messages.c.content == "正在生成图片...",
        )
        .values(content=locator)
    )
    return changed.rowcount == 1


def _encode_success_result(
    stored: StoredMedia, receipt: GenerationResultReceipt
) -> str:
    return json.dumps(
        {
            "kind": "image",
            "url": stored.locator,
            "result_ref": receipt.result_ref,
            "content_digest": stored.content_digest,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _valid_stored_media(value: object) -> bool:
    return (
        type(value) is StoredMedia
        and isinstance(value.locator, str)
        and bool(value.locator.strip())
        and _is_digest(value.content_digest)
        and isinstance(value.media_type, str)
        and value.media_type.startswith("image/")
        and type(value.size_bytes) is int
        and value.size_bytes > 0
    )


def _safe_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and not any(ord(character) < 32 for character in value)
    )


def _json_object(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _ordered_task_ids(value: object) -> tuple[str, ...] | None:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= _MAX_BATCH_ITEMS
        or any(not _nonempty_string(item) for item in value)
        or len(set(value)) != len(value)
    ):
        return None
    return tuple(value)


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _with_now(value: _ExpectedTerminal, now: datetime) -> _ExpectedTerminal:
    receipt = value.receipt
    if receipt is not None:
        receipt = GenerationResultReceipt(
            result_ref=receipt.result_ref,
            owner_id=receipt.owner_id,
            task_id=receipt.task_id,
            unit_id=receipt.unit_id,
            operation=receipt.operation,
            step_id=receipt.step_id,
            submission_id=receipt.submission_id,
            evidence_id=receipt.evidence_id,
            selected_attempt_id=receipt.selected_attempt_id,
            recoverable_locator=receipt.recoverable_locator,
            content_digest=receipt.content_digest,
            validation_version=receipt.validation_version,
            validated_at=now,
            generation_fulfilled_at=now,
            late_disposition=receipt.late_disposition,
            policy_stop_disposition=receipt.policy_stop_disposition,
            automatic_application_allowed=receipt.automatic_application_allowed,
        )
    return replace(value, now=now, receipt=receipt)


def _time_matches(value: object, expected: datetime) -> bool:
    return isinstance(value, datetime) and _aware_utc(value) == _aware_utc(expected)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


__all__ = ("SqlAlchemyImageBatchResults",)
