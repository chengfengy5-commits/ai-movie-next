"""Fenced persistence for one original video.single provider result."""

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
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    ExternalOperationRef,
    FrozenExecutionSnapshot,
    OwnerTaskScope,
    StoredMedia,
)
from haoai_backend.generation_credentials.video_execution_snapshot import (
    VerifiedVideoExecutionInput,
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
from .schema import GenerationResultReceiptRow


_TASK_TYPE = "video-single"
_OPERATION = "video.single"
_GRAPH_VERSION = "video-single-v1"
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
        "operation",
        "graph_version",
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
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL_MESSAGE = "视频生成结果需要恢复确认"


def _is_stopping(stop_signal: Event | None) -> bool:
    return stop_signal is not None and stop_signal.is_set()

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
TerminalKind = Literal["success", "failure", "unknown"]
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


@dataclass(frozen=True, slots=True)
class _LockedVideoGraph:
    task: Mapping[str, object]
    unit: Mapping[str, object]
    step: Mapping[str, object]
    parent: Mapping[str, object]
    submission: Mapping[str, object]
    request_data: Mapping[str, object]


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
    original_graph: _LockedVideoGraph | None = None
    refund_description: str | None = None


class SqlAlchemyVideoSingleResults:
    """Commit the result and all terminal projections under the live claim."""

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

    def settle_due(
        self, scope: OwnerTaskScope, *, stop_signal: Event | None = None
    ) -> Literal["settled", "retained", "pending"]:
        """Settle an unresolved original video.single intent at its fixed deadline."""

        if _is_stopping(stop_signal):
            return "pending"
        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != _OPERATION
            or not isinstance(scope.user_id, str)
            or not scope.user_id
            or not isinstance(scope.task_id, str)
            or not scope.task_id
        ):
            return "pending"

        expected = _due_terminal(scope)
        original_graph: _LockedVideoGraph | None = None
        try:
            with self._session_factory() as session:
                with session.begin():
                    graph = _lock_video_graph(session, scope)
                    now = self._read_clock()
                    if (
                        graph is None or now is None or _is_stopping(stop_signal)
                        or not _valid_video_single_identity(graph, scope)
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

                    expected = replace(expected, now=now, original_graph=graph)
                    expected = self._with_financial_state(
                        session, scope, graph, expected
                    )
                    if _is_stopping(stop_signal):
                        return "pending"
                    fresh_now = self._read_clock()
                    deadline_value = graph.unit.get("deadline_at")
                    if (
                        fresh_now is None
                        or not isinstance(deadline_value, datetime)
                        or _aware_utc(fresh_now) < _aware_utc(deadline_value)
                    ):
                        return "pending"
                    now = fresh_now
                    expected = replace(expected, now=now)
                    original_graph = graph
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
                            progress_message="视频生成结果未能在期限内确认",
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
                            ExecutionStepRow.id == graph.step["id"],
                            ExecutionStepRow.billing_unit_id == graph.unit["id"],
                            ExecutionStepRow.step_key == "generate",
                            ExecutionStepRow.input_digest == graph.step["input_digest"],
                            ExecutionStepRow.execution_status.in_(("submitting", "reconciling")),
                        )
                        .values(
                            execution_status="failed",
                            updated_at=_database_datetime(now),
                        )
                    )
                    if step_change.rowcount != 1:
                        raise _OwnershipLost

                    submission_table = ExternalSubmissionRow.__table__
                    external_id = graph.submission.get("external_task_id")
                    external_id_match = (
                        submission_table.c.external_task_id.is_(None)
                        if external_id is None
                        else submission_table.c.external_task_id == external_id
                    )
                    submission_change = session.execute(
                        update(ExternalSubmissionRow.__table__)
                        .where(
                            ExternalSubmissionRow.id == graph.submission["id"],
                            ExternalSubmissionRow.step_id == graph.step["id"],
                            ExternalSubmissionRow.submission_key
                            == graph.submission["submission_key"],
                            ExternalSubmissionRow.request_digest
                            == graph.submission["request_digest"],
                            ExternalSubmissionRow.submission_status.in_(
                                ("submitting", "submitted", "unknown")
                            ),
                            external_id_match,
                            ExternalSubmissionRow.submitted_at
                            == graph.submission.get("submitted_at"),
                            ExternalSubmissionRow.intent_at
                            == _database_datetime(_aware_utc(graph.submission["intent_at"])),
                            ExternalSubmissionRow.deadline_at
                            == _database_datetime(_aware_utc(graph.submission["deadline_at"])),
                        )
                        .values(
                            submission_status="unknown",
                            updated_at=_database_datetime(now),
                        )
                    )
                    if submission_change.rowcount != 1:
                        raise _OwnershipLost

                    if _is_stopping(stop_signal):
                        raise _OwnershipLost
                    if expected.refund is not None:
                        _apply_refund(session, expected.refund, now)
        except _InvalidFinancialProof:
            return "pending"
        except Exception:
            if original_graph is None:
                return "pending"

        return (
            "settled"
            if self._confirm_due(
                scope, expected, original_graph, stop_signal=stop_signal
            )
            else "pending"
        )

    def _confirm_due(
        self,
        scope: OwnerTaskScope,
        expected: _ExpectedTerminal,
        original_graph: _LockedVideoGraph | None,
        *,
        stop_signal: Event | None = None,
    ) -> bool:
        if original_graph is None or _is_stopping(stop_signal):
            return False
        try:
            with self._session_factory() as session:
                graph = _lock_video_graph(session, scope)
                return (
                    graph is not None
                    and _valid_video_single_identity(graph, scope)
                    and _same_static_graph(original_graph, graph)
                    and _is_due_terminal(graph, scope, expected.result_value)
                    and _confirm_due_finances(
                        session, graph, scope, stop_signal=stop_signal
                    )
                )
        except Exception:
            return False

    def complete(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedVideoExecutionInput,
        stored: StoredMedia,
    ) -> GenerationResultReceipt | Literal["pending", "ownership_lost"]:
        if not _valid_stored_media(stored):
            return "pending"
        digest = stored.content_digest
        result_ref = self._new_id()
        evidence_id = self._new_id()
        if result_ref is None or evidence_id is None:
            return "pending"
        now = self._read_clock()
        if now is None:
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
            content_digest=digest,
            validation_version="video-single-media-v1",
            validated_at=now,
            generation_fulfilled_at=now,
            late_disposition=None,
            policy_stop_disposition=None,
            automatic_application_allowed=False,
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
        return self._write_terminal(claim, intent, verified, expected)

    def record_submission(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedVideoExecutionInput,
        external_ref: ExternalOperationRef,
    ) -> Literal["recorded", "pending", "ownership_lost"]:
        """Persist the provider's original submitted id without changing its intent."""

        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != _OPERATION
            or not _valid_intent(intent, verified)
            or verified.task_id != claim.scope.task_id
        ):
            return "pending"
        if (
            isinstance(external_ref, ExternalOperationRef)
            and external_ref.create_outcome == "unknown"
            and external_ref.external_id is None
            and _valid_external_ref_binding(external_ref, intent, verified)
        ):
            return self.mark_unknown(claim, intent, verified)
        if not _valid_external_operation_ref(external_ref, intent):
            return "pending"

        now = self._read_clock()
        if now is None:
            return "pending"
        committed_at: datetime | None = None
        try:
            with self._session_factory() as session:
                with session.begin():
                    graph = _lock_video_graph(session, claim)
                    fresh_now = self._read_clock()
                    if (
                        graph is None
                        or fresh_now is None
                        or claim.local_lost_signal.is_set()
                        or not _claim_is_current(graph.task, claim, fresh_now)
                        or fresh_now >= intent.deadline_at
                        or not _graph_identity_matches(
                            graph, claim, intent, verified
                        )
                    ):
                        return "ownership_lost"
                    submission = graph.submission
                    if (
                        submission.get("submission_status") == "submitted"
                        and submission.get("external_task_id") == external_ref.external_id
                        and isinstance(submission.get("submitted_at"), datetime)
                    ):
                        committed_at = _aware_utc(submission["submitted_at"])
                    elif (
                        submission.get("submission_status") == "submitting"
                        and submission.get("external_task_id") is None
                        and submission.get("submitted_at") is None
                    ):
                        committed_at = fresh_now
                        changed = session.execute(
                            update(ExternalSubmissionRow.__table__)
                            .where(
                                ExternalSubmissionRow.id
                                == intent.external_submission_id,
                                ExternalSubmissionRow.step_id == intent.step_id,
                                ExternalSubmissionRow.submission_key
                                == submission.get("submission_key"),
                                ExternalSubmissionRow.provider == intent.provider,
                                ExternalSubmissionRow.model_ref == intent.model_id,
                                ExternalSubmissionRow.request_digest
                                == intent.request_digest,
                                _json_equal(
                                    session,
                                    ExternalSubmissionRow.request_snapshot,
                                    submission.get("request_snapshot"),
                                ),
                                ExternalSubmissionRow.submission_status
                                == "submitting",
                                ExternalSubmissionRow.external_task_id.is_(None),
                                ExternalSubmissionRow.submitted_at.is_(None),
                                ExternalSubmissionRow.intent_at
                                == _database_datetime(intent.first_intent_at),
                                ExternalSubmissionRow.deadline_at
                                == _database_datetime(intent.deadline_at),
                            )
                            .values(
                                external_task_id=external_ref.external_id,
                                submission_status="submitted",
                                submitted_at=_database_datetime(committed_at),
                                updated_at=_database_datetime(fresh_now),
                            )
                        )
                        if changed.rowcount != 1:
                            raise _OwnershipLost
                    else:
                        return "pending"
        except _OwnershipLost:
            return "ownership_lost"
        except Exception:
            # A lost acknowledgement is resolved by reading the same original row.
            pass

        if committed_at is None:
            return "pending"
        return self._confirm_submission(
            claim, intent, verified, external_ref.external_id, committed_at
        )

    def _confirm_submission(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedVideoExecutionInput,
        external_id: str,
        submitted_at: datetime,
    ) -> Literal["recorded", "pending", "ownership_lost"]:
        try:
            with self._session_factory() as session:
                graph = _lock_video_graph(session, claim)
                now = self._read_clock()
                if graph is None or now is None:
                    return "pending"
                if not _claim_is_current(graph.task, claim, now):
                    return "ownership_lost"
                submission = graph.submission
                return (
                    "recorded"
                    if _graph_identity_matches(graph, claim, intent, verified)
                    and submission.get("id") == intent.external_submission_id
                    and submission.get("submission_status") == "submitted"
                    and submission.get("external_task_id") == external_id
                    and _time_matches(submission.get("submitted_at"), submitted_at)
                    else "pending"
                )
        except Exception:
            return "pending"

    def fail(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedVideoExecutionInput,
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
                {"error": safe_detail, "code": code},
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
        verified: VerifiedVideoExecutionInput,
    ) -> Literal["pending", "ownership_lost"]:
        expected = self._non_result_terminal(
            claim,
            verified,
            kind="unknown",
            result_value=json.dumps(
                {"status": "reconciling", "message": _TERMINAL_MESSAGE},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        result = self._write_terminal(claim, intent, verified, expected)
        return "pending" if result is None else result

    def record_unknown_observation(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedVideoExecutionInput,
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
        evidence_key = (
            f"video-single-observation-v1:{intent.external_submission_id}"
        )
        evidence_id: str | None = None
        try:
            with self._session_factory() as session:
                with session.begin():
                    graph = _lock_video_graph(session, claim)
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
            # Resolve an uncertain insert by its original stable evidence key.
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

    def _confirm_unknown_observation(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedVideoExecutionInput,
        evidence_key: str,
        payload_digest: str,
        result_location: str | None,
        evidence_kind: str,
    ) -> Literal["recorded", "pending"]:
        try:
            with self._session_factory() as session:
                graph = _lock_video_graph(session, claim)
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

    def _non_result_terminal(
        self,
        claim: ClaimAuthority,
        verified: VerifiedVideoExecutionInput,
        *,
        kind: Literal["failure", "unknown"],
        result_value: str,
    ) -> _ExpectedTerminal:
        return _ExpectedTerminal(
            kind=kind,
            task_id=claim.scope.task_id,
            owner_id=claim.scope.user_id,
            unit_id=verified.unit_id,
            step_id="",
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

    def _write_terminal(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedVideoExecutionInput,
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
                    graph = _lock_video_graph(session, claim)
                    now = self._read_clock()
                    if (
                        graph is None
                        or now is None
                        or claim.local_lost_signal.is_set()
                        or not _claim_is_current(graph.task, claim, now)
                        or now >= intent.deadline_at
                        or not _graph_matches(graph, claim, intent, verified)
                    ):
                        return "ownership_lost"

                    expected = _with_now(expected, now)
                    expected = self._with_financial_state(
                        session, claim.scope, graph, expected
                    )
                    if expected.kind == "failure":
                        fresh_graph = _lock_video_graph(session, claim)
                        fresh_now = self._read_clock()
                        if (
                            fresh_graph is None
                            or fresh_now is None
                            or claim.local_lost_signal.is_set()
                            or fresh_now >= intent.deadline_at
                            or not _claim_is_current(
                                fresh_graph.task, claim, fresh_now
                            )
                            or not _graph_matches(
                                fresh_graph, claim, intent, verified
                            )
                            or not _same_static_graph(graph, fresh_graph)
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
                            "视频已生成" if expected.kind == "success"
                            else _TERMINAL_MESSAGE if expected.kind == "unknown"
                            else "视频生成失败"
                        ),
                        "external_provider": intent.provider,
                        "updated_at": _database_datetime(now),
                    }
                    if expected.kind == "unknown":
                        task_values["recovery_status"] = "reconciling"
                    if expected.kind == "failure":
                        task_values["billing_status"] = expected.task_billing_status
                    original_task_result = graph.task.get("result")
                    task_result_predicate = (
                        task_table.c.result.is_(None)
                        if original_task_result is None
                        else task_table.c.result == original_task_result
                    )
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
                            task_table.c.lease_until == _database_datetime(
                                _aware_utc(graph.task["lease_until"])
                            ),
                            task_table.c.lease_until > _database_datetime(now),
                            task_table.c.request_data == graph.task["request_data"],
                            task_result_predicate,
                        )
                        .values(**task_values)
                    )
                    if task_change.rowcount != 1:
                        raise _OwnershipLost
                    after_task = _lock_video_graph(session, claim)
                    if (
                        after_task is None
                        or not _graph_matches(
                            after_task, claim, intent, verified,
                            terminal=expected, task_only=True,
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
                            unit_table.c.result_status == graph.unit.get("result_status"),
                            unit_table.c.billing_status.in_(("charged", "no_charge")),
                            unit_table.c.user_cancelled_at.is_(None),
                            unit_table.c.success_confirmed_at.is_(None),
                            unit_table.c.first_intent_at == _database_datetime(
                                intent.first_intent_at
                            ),
                            unit_table.c.deadline_at == _database_datetime(intent.deadline_at),
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
                            ExecutionStepRow.id == intent.step_id,
                            ExecutionStepRow.billing_unit_id == verified.unit_id,
                            ExecutionStepRow.step_key == "generate",
                            ExecutionStepRow.required.is_(True),
                            ExecutionStepRow.step_order == graph.step["step_order"],
                            ExecutionStepRow.input_digest == intent.request_digest,
                            _json_equal(
                                session,
                                ExecutionStepRow.dependencies,
                                graph.step["dependencies"],
                            ),
                            _json_equal(
                                session,
                                ExecutionStepRow.input_snapshot,
                                graph.step["input_snapshot"],
                            ),
                            ExecutionStepRow.required == graph.step["required"],
                            ExecutionStepRow.execution_status
                            == graph.step.get("execution_status"),
                        )
                        .values(
                            execution_status=expected.step_status,
                            updated_at=_database_datetime(now),
                        )
                    )
                    if step_change.rowcount != 1:
                        raise _OwnershipLost

                    submission_table = ExternalSubmissionRow.__table__
                    external_id = graph.submission.get("external_task_id")
                    external_id_match = (
                        submission_table.c.external_task_id.is_(None)
                        if external_id is None
                        else submission_table.c.external_task_id == external_id
                    )
                    submission_change = session.execute(
                        update(ExternalSubmissionRow.__table__)
                        .where(
                            ExternalSubmissionRow.id == intent.external_submission_id,
                            ExternalSubmissionRow.step_id == intent.step_id,
                            ExternalSubmissionRow.submission_key == graph.submission["submission_key"],
                            _json_equal(
                                session,
                                ExternalSubmissionRow.request_snapshot,
                                graph.submission["request_snapshot"],
                            ),
                            ExternalSubmissionRow.provider == intent.provider,
                            ExternalSubmissionRow.model_ref == intent.model_id,
                            ExternalSubmissionRow.request_digest == intent.request_digest,
                            ExternalSubmissionRow.submission_status
                            == graph.submission.get("submission_status"),
                            external_id_match,
                            ExternalSubmissionRow.submitted_at
                            == graph.submission.get("submitted_at"),
                            ExternalSubmissionRow.intent_at == _database_datetime(
                                intent.first_intent_at
                            ),
                            ExternalSubmissionRow.deadline_at == _database_datetime(
                                intent.deadline_at
                            ),
                        )
                        .values(
                            submission_status=expected.submission_status,
                            submitted_at=graph.submission.get("submitted_at"),
                            updated_at=_database_datetime(now),
                        )
                    )
                    if submission_change.rowcount != 1:
                        raise _OwnershipLost

                    after_children = _lock_video_graph(session, claim)
                    if (
                        after_children is None
                        or not _graph_matches(
                            after_children, claim, intent, verified, terminal=expected
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
                                    f"video-single-result-v1:{intent.external_submission_id}"
                                ),
                                source="provider",
                                evidence_kind="provider_video",
                                payload_digest=expected.stored.content_digest,
                                result_location=expected.stored.locator,
                                result_digest=expected.stored.content_digest,
                                received_at=_database_datetime(now),
                                validity="valid",
                                application_status="pending",
                                applied_at=None,
                            )
                        )
                        if graph.task.get("message_id"):
                            if not _project_assistant_message(
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
            # A lost commit acknowledgement must be resolved by an independent read.
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

    def _confirm_terminal(
        self,
        expected: _ExpectedTerminal,
        intent: CommittedIntent,
        verified: VerifiedVideoExecutionInput,
        claim: ClaimAuthority,
    ) -> Literal["confirmed", "pending", "ownership_lost"]:
        try:
            with self._session_factory() as session:
                graph = _lock_video_graph(session, claim)
                if graph is None or not _graph_matches(
                    graph, claim, intent, verified, terminal=expected
                ):
                    return "pending"
                task = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == expected.task_id,
                        AITaskRow.user_id == expected.owner_id,
                        AITaskRow.type == _TASK_TYPE,
                    )
                ).mappings().one_or_none()
                unit = session.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.id == expected.unit_id,
                        BillingUnitRow.task_id == expected.task_id,
                    )
                ).mappings().one_or_none()
                step = session.execute(
                    select(ExecutionStepRow.__table__).where(
                        ExecutionStepRow.id == intent.step_id,
                        ExecutionStepRow.billing_unit_id == expected.unit_id,
                    )
                ).mappings().one_or_none()
                submission = session.execute(
                    select(ExternalSubmissionRow.__table__).where(
                        ExternalSubmissionRow.id == intent.external_submission_id,
                        ExternalSubmissionRow.step_id == intent.step_id,
                    )
                ).mappings().one_or_none()
                expected_external_id = (
                    expected.original_graph.submission.get("external_task_id")
                    if expected.original_graph is not None
                    else None
                )
                if (
                    task is None
                    or unit is None
                    or step is None
                    or submission is None
                    or task["status"] != expected.task_status
                    or task["result"] != expected.result_value
                    or task["billing_status"] != expected.task_billing_status
                    or unit["result_status"] != expected.unit_status
                    or unit["billing_status"] != expected.unit_billing_status
                    or unit["refunded_amount"] != expected.refunded_amount
                    or step["execution_status"] != expected.step_status
                    or submission["submission_status"] != expected.submission_status
                    or submission["external_task_id"] != expected_external_id
                    or not _valid_submission_state(submission)
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
                    if task.get("message_id"):
                        message = session.execute(
                            select(chat_messages.c.content).where(
                                chat_messages.c.id == task["message_id"],
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
                    else:
                        settlement = expected.refund
                        debit = session.execute(
                            select(CreditLogRow.__table__).where(
                                CreditLogRow.id == settlement.debit_id,
                                CreditLogRow.user_id == settlement.owner_id,
                                CreditLogRow.task_id == settlement.task_id,
                                CreditLogRow.billing_unit_id == settlement.unit_id,
                                CreditLogRow.type == "usage",
                                CreditLogRow.amount == -settlement.amount,
                                CreditLogRow.related_debit_id.is_(None),
                                CreditLogRow.business_key == f"debit:{settlement.unit_id}",
                            )
                        ).mappings().one_or_none()
                        refund = session.execute(
                            select(CreditLogRow.__table__).where(
                                CreditLogRow.id == settlement.refund_id,
                                CreditLogRow.user_id == settlement.owner_id,
                                CreditLogRow.task_id == settlement.task_id,
                                CreditLogRow.billing_unit_id == settlement.unit_id,
                                CreditLogRow.related_debit_id == settlement.debit_id,
                                CreditLogRow.business_key == settlement.refund_key,
                                CreditLogRow.type == "refund",
                                CreditLogRow.amount == settlement.amount,
                                CreditLogRow.balance_after == settlement.balance_after,
                            )
                        ).mappings().one_or_none()
                        balance = session.execute(
                            select(UserCreditRow.credits).where(
                                UserCreditRow.user_id == settlement.owner_id
                            )
                        ).scalar_one_or_none()
                        if debit is None or refund is None or balance != settlement.balance_after:
                            return "pending"
                if expected.kind == "unknown" and task["recovery_status"] != "reconciling":
                    return "pending"
                return "confirmed"
        except Exception:
            return "pending"

    def _with_financial_state(
        self,
        session: Session,
        scope: OwnerTaskScope,
        graph: _LockedVideoGraph,
        expected: _ExpectedTerminal,
    ) -> _ExpectedTerminal:
        unit = graph.unit
        if expected.kind != "failure":
            return replace(
                expected,
                task_billing_status=str(graph.task.get("billing_status")),
                unit_billing_status=str(unit.get("billing_status")),
                refunded_amount=int(unit.get("refunded_amount", 0)),
            )

        amount = unit.get("debited_amount")
        if (
            type(amount) is not int
            or type(unit.get("refunded_amount")) is not int
            or unit["refunded_amount"] != 0
        ):
            raise _InvalidFinancialProof
        if amount == 0:
            if unit.get("billing_status") != "no_charge" or graph.task.get(
                "billing_status"
            ) != "no_charge":
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
            or "video.single provider rejection refund",
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


def _json_equal(session: Session, column, value: object):
    if session.get_bind().dialect.name == "postgresql":
        return cast(column, JSONB) == cast(value, JSONB)
    return column == value


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


def _lock_video_graph(
    session: Session, authority: ClaimAuthority | OwnerTaskScope
) -> _LockedVideoGraph | None:
    scope = authority.scope if isinstance(authority, ClaimAuthority) else authority
    user = session.execute(
        select(UserRow.id)
        .where(UserRow.id == scope.user_id)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if user is None:
        return None
    task_row = session.execute(
        select(AITaskRow.__table__)
        .where(
            AITaskRow.id == scope.task_id,
            AITaskRow.user_id == scope.user_id,
        )
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if task_row is None:
        return None
    units = session.execute(
        select(BillingUnitRow.__table__)
        .where(BillingUnitRow.task_id == scope.task_id)
        .order_by(BillingUnitRow.id)
        .with_for_update()
    ).mappings().all()
    if len(units) != 1:
        return None
    unit = dict(units[0])
    steps = session.execute(
        select(ExecutionStepRow.__table__)
        .where(ExecutionStepRow.billing_unit_id == unit["id"])
        .order_by(ExecutionStepRow.step_order, ExecutionStepRow.id)
        .with_for_update()
    ).mappings().all()
    if len(steps) != 1:
        return None
    step = dict(steps[0])
    task = dict(task_row)
    try:
        request_data = json.loads(task["request_data"])
        parent_id = request_data.get("submission_id")
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(request_data, dict) or not isinstance(parent_id, str):
        return None
    parent = session.execute(
        select(TaskSubmissionRow.__table__)
        .where(
            TaskSubmissionRow.id == parent_id,
            TaskSubmissionRow.user_id == scope.user_id,
        )
        .with_for_update()
    ).mappings().one_or_none()
    if parent is None:
        return None
    submissions = session.execute(
        select(ExternalSubmissionRow.__table__)
        .where(ExternalSubmissionRow.step_id == step["id"])
        .order_by(ExternalSubmissionRow.id)
        .with_for_update()
    ).mappings().all()
    if len(submissions) != 1:
        return None
    return _LockedVideoGraph(
        task=task,
        unit=unit,
        step=step,
        parent=dict(parent),
        submission=dict(submissions[0]),
        request_data=request_data,
    )


_UNRESOLVED_RESULT = json.dumps(
    {"message": _TERMINAL_MESSAGE, "status": "reconciling"},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
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
                "message": "视频生成结果未能在期限内确认",
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
        refund_description="video.single fixed-deadline refund",
    )


def _valid_video_single_identity(
    graph: _LockedVideoGraph, scope: OwnerTaskScope
) -> bool:
    """Validate original request, graph, snapshot and intent independently of status."""

    task, unit, step = graph.task, graph.unit, graph.step
    parent, submission, request = graph.parent, graph.submission, graph.request_data
    step_snapshot = step.get("input_snapshot")
    intent_snapshot = submission.get("request_snapshot")
    first_intent, deadline = unit.get("first_intent_at"), unit.get("deadline_at")
    if (
        not isinstance(step_snapshot, dict)
        or set(step_snapshot) != _STEP_SNAPSHOT_KEYS
        or not isinstance(intent_snapshot, dict)
        or set(intent_snapshot) != _INTENT_SNAPSHOT_KEYS
        or not _valid_submission_state(submission)
        or not isinstance(first_intent, datetime)
        or not isinstance(deadline, datetime)
    ):
        return False
    digest = request.get("request_digest")
    unit_id, step_id, parent_id = unit.get("id"), step.get("id"), parent.get("id")
    attempt_id = request.get("server_attempt_id")
    prepared_id = request.get("prepared_payload_id")
    snapshot_digest = step_snapshot.get("snapshot_digest")
    manifest_digest = step_snapshot.get("ordered_manifest_digest")
    protected_ref = step_snapshot.get("protected_ref")
    provider, model_id = submission.get("provider"), submission.get("model_ref")
    model_source, model_owner = request.get("model_source"), request.get("model_owner_id")
    binding_ref = intent_snapshot.get("credential_binding_ref")
    if (
        not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None
        or not all(isinstance(value, str) and value for value in (
            unit_id, step_id, parent_id, attempt_id, prepared_id, protected_ref,
            provider, model_id, binding_ref,
        ))
        or not isinstance(snapshot_digest, str)
        or _DIGEST.fullmatch(snapshot_digest) is None
        or not isinstance(manifest_digest, str)
        or _DIGEST.fullmatch(manifest_digest) is None
        or model_source not in {"system", "user"}
        or (model_source == "system" and model_owner is not None)
        or (model_source == "user" and model_owner != scope.user_id)
    ):
        return False
    first_intent, deadline = _aware_utc(first_intent), _aware_utc(deadline)
    expected_snapshot = {
        "owner_id": scope.user_id, "task_id": scope.task_id,
        "unit_id": unit_id, "step_id": step_id, "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION, "attempt_id": attempt_id,
        "prepared_payload_id": prepared_id, "protected_ref": protected_ref,
        "snapshot_digest": snapshot_digest,
        "snapshot_ref": f"{_GRAPH_VERSION}:{prepared_id}:{snapshot_digest}:{manifest_digest}",
        "credential_binding_ref": binding_ref,
        "target_snapshot": request.get("target_snapshot"),
        "provider": provider, "model_id": model_id,
    }
    return all((
        task.get("id") == scope.task_id,
        task.get("user_id") == scope.user_id,
        task.get("type") == _TASK_TYPE,
        task.get("model_name") == request.get("model_display_name"),
        task.get("credit_cost") == unit.get("quoted_amount"),
        request.get("operation") == _OPERATION,
        request.get("step_graph_version") == _GRAPH_VERSION,
        request.get("submission_id") == parent_id,
        request.get("parent_attempt_id") == parent_id,
        request.get("request_digest") == unit.get("input_digest")
        == step.get("input_digest") == parent.get("request_digest")
        == submission.get("request_digest"),
        request.get("server_attempt_id") == unit.get("item_key")
        == step_snapshot.get("server_attempt_id"),
        request.get("billing_unit_id") == unit_id,
        request.get("prepared_payload_id") == step_snapshot.get("prepared_payload_id"),
        request.get("snapshot_digest") == snapshot_digest,
        request.get("protected_ref") == protected_ref,
        request.get("target_ref") == f"{protected_ref}#target",
        request.get("model_ref") == f"{protected_ref}#model",
        request.get("grant_ref") == f"{protected_ref}#grant",
        request.get("item_id") == attempt_id,
        request.get("unit_key") == unit.get("item_key") == attempt_id,
        request.get("model_id") == model_id,
        request.get("model_owner_id") == (None if model_source == "system" else scope.user_id),
        request.get("target_snapshot") == intent_snapshot.get("target_snapshot"),
        intent_snapshot == expected_snapshot,
        step.get("id") == step_id and step.get("billing_unit_id") == unit_id,
        step.get("step_key") == "generate" and step.get("required") is True,
        step.get("step_order") == 0 and step.get("dependencies") in ([], ()),
        unit.get("id") == unit_id and unit.get("task_id") == scope.task_id,
        unit.get("operation") == _OPERATION,
        unit.get("step_graph_version") == _GRAPH_VERSION,
        parent.get("id") == parent_id and parent.get("user_id") == scope.user_id,
        parent.get("operation") == _OPERATION and parent.get("request_digest") == digest,
        parent.get("task_ids") == [scope.task_id] and bool(parent.get("idempotency_key")),
        bool(submission.get("id")) and submission.get("step_id") == step_id,
        submission.get("submission_key") == f"video-single-create-v1:{unit_id}:{step_id}",
        submission.get("provider") == provider and submission.get("model_ref") == model_id,
        _time_matches(unit.get("first_intent_at"), first_intent),
        _time_matches(unit.get("deadline_at"), deadline),
        _time_matches(submission.get("intent_at"), first_intent),
        _time_matches(submission.get("deadline_at"), deadline),
        deadline == first_intent + timedelta(hours=2),
    ))


def _valid_due_graph(
    graph: _LockedVideoGraph, scope: OwnerTaskScope, now: datetime
) -> bool:
    if not _valid_video_single_identity(graph, scope):
        return False
    task = graph.task
    unit = graph.unit
    step = graph.step
    parent = graph.parent
    submission = graph.submission
    request = graph.request_data
    step_snapshot = step.get("input_snapshot")
    intent_snapshot = submission.get("request_snapshot")
    first_intent = unit.get("first_intent_at")
    deadline = unit.get("deadline_at")
    if (
        not isinstance(step_snapshot, dict)
        or set(step_snapshot) != _STEP_SNAPSHOT_KEYS
        or not isinstance(intent_snapshot, dict)
        or set(intent_snapshot) != _INTENT_SNAPSHOT_KEYS
        or not _valid_submission_state(submission)
        or not isinstance(first_intent, datetime)
        or not isinstance(deadline, datetime)
    ):
        return False
    first_intent = _aware_utc(first_intent)
    deadline = _aware_utc(deadline)
    digest = request.get("request_digest")
    prepared_id = request.get("prepared_payload_id")
    attempt_id = request.get("server_attempt_id")
    step_id = step.get("id")
    unit_id = unit.get("id")
    snapshot_digest = step_snapshot.get("snapshot_digest")
    manifest_digest = step_snapshot.get("ordered_manifest_digest")
    protected_ref = step_snapshot.get("protected_ref")
    provider = submission.get("provider")
    model_id = submission.get("model_ref")
    model_source = request.get("model_source")
    model_owner_id = request.get("model_owner_id")
    binding_ref = intent_snapshot.get("credential_binding_ref")
    if (
        not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or not isinstance(prepared_id, str)
        or not prepared_id
        or not isinstance(attempt_id, str)
        or not attempt_id
        or not isinstance(step_id, str)
        or not isinstance(unit_id, str)
        or not isinstance(snapshot_digest, str)
        or _DIGEST.fullmatch(snapshot_digest) is None
        or not isinstance(manifest_digest, str)
        or _DIGEST.fullmatch(manifest_digest) is None
        or not isinstance(protected_ref, str)
        or not protected_ref
        or not isinstance(provider, str)
        or not provider
        or not isinstance(model_id, str)
        or not model_id
        or model_source not in {"system", "user"}
        or (model_source == "system" and model_owner_id is not None)
        or (model_source == "user" and model_owner_id != scope.user_id)
        or not isinstance(binding_ref, str)
        or not binding_ref
    ):
        return False
    expected_snapshot_ref = (
        f"{_GRAPH_VERSION}:{prepared_id}:{snapshot_digest}:{manifest_digest}"
    )
    expected_snapshot = {
        "owner_id": scope.user_id,
        "task_id": scope.task_id,
        "unit_id": unit_id,
        "step_id": step_id,
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
        "attempt_id": attempt_id,
        "prepared_payload_id": prepared_id,
        "protected_ref": protected_ref,
        "snapshot_digest": snapshot_digest,
        "snapshot_ref": expected_snapshot_ref,
        "credential_binding_ref": binding_ref,
        "target_snapshot": request.get("target_snapshot"),
        "provider": provider,
        "model_id": model_id,
    }
    amount = unit.get("debited_amount")
    return all(
        (
            task.get("id") == scope.task_id,
            task.get("user_id") == scope.user_id,
            task.get("type") == _TASK_TYPE,
            task.get("status") in {"processing", "reconciling", "queued"},
            task.get("result") in (None, _UNRESOLVED_RESULT),
            task.get("user_cancelled_at") is None,
            task.get("billing_status") in {"charged", "no_charge"},
            task.get("credit_cost") == unit.get("quoted_amount"),
            task.get("model_name") == request.get("model_display_name"),
            request.get("operation") == _OPERATION,
            request.get("submission_id") == parent.get("id"),
            request.get("parent_attempt_id") == parent.get("id"),
            request.get("request_digest") == unit.get("input_digest") == step.get("input_digest") == submission.get("request_digest"),
            request.get("server_attempt_id") == unit.get("item_key") == step_snapshot.get("server_attempt_id"),
            request.get("billing_unit_id") == unit_id,
            request.get("prepared_payload_id") == step_snapshot.get("prepared_payload_id"),
            request.get("snapshot_digest") == snapshot_digest,
            request.get("protected_ref") == protected_ref,
            request.get("model_id") == model_id,
            request.get("model_source") in {"system", "user"},
            request.get("model_owner_id")
            == (None if request.get("model_source") == "system" else scope.user_id),
            request.get("target_snapshot") == intent_snapshot.get("target_snapshot"),
            intent_snapshot == expected_snapshot,
            step.get("id") == step_id,
            step.get("billing_unit_id") == unit_id,
            step.get("step_key") == "generate",
            step.get("required") is True,
            step.get("step_order") == 0,
            step.get("dependencies") in ([], ()),
            step.get("execution_status") in {"submitting", "reconciling"},
            unit.get("id") == unit_id,
            unit.get("task_id") == scope.task_id,
            unit.get("operation") == _OPERATION,
            unit.get("step_graph_version") == _GRAPH_VERSION,
            unit.get("billing_status") == task.get("billing_status"),
            unit.get("result_status") in {"pending", "unknown"},
            type(amount) is int and amount >= 0,
            unit.get("refunded_amount") == 0,
            unit.get("user_cancelled_at") is None,
            unit.get("success_confirmed_at") is None,
            parent.get("id") == request.get("submission_id"),
            parent.get("user_id") == scope.user_id,
            parent.get("operation") == _OPERATION,
            parent.get("task_ids") == [scope.task_id],
            submission.get("step_id") == step_id,
            submission.get("submission_key") == f"video-single-create-v1:{unit_id}:{step_id}",
            submission.get("provider") == provider,
            submission.get("model_ref") == model_id,
            submission.get("submission_status")
            in {"submitting", "submitted", "unknown"},
            _valid_submission_state(submission),
            _time_matches(unit.get("first_intent_at"), first_intent),
            _time_matches(submission.get("intent_at"), first_intent),
            _time_matches(submission.get("deadline_at"), deadline),
            deadline == first_intent + timedelta(hours=2),
            now >= deadline,
        )
    )


def _is_strictly_before_deadline(value: datetime, deadline: datetime) -> bool:
    return _aware_utc(value) < _aware_utc(deadline)


def _has_timely_linked_success(
    session: Session, graph: _LockedVideoGraph, scope: OwnerTaskScope
) -> bool:
    unit = graph.unit
    step = graph.step
    submission = graph.submission
    deadline_value = unit.get("deadline_at")
    if not isinstance(deadline_value, datetime):
        return False
    deadline = _aware_utc(deadline_value)
    receipts = session.execute(
        select(GenerationResultReceiptRow.__table__).where(
            GenerationResultReceiptRow.owner_id == scope.user_id,
            GenerationResultReceiptRow.task_id == scope.task_id,
            GenerationResultReceiptRow.unit_id == unit.get("id"),
            GenerationResultReceiptRow.operation == _OPERATION,
            GenerationResultReceiptRow.step_id == step.get("id"),
            GenerationResultReceiptRow.submission_id == submission.get("id"),
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
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or not isinstance(locator, str)
        or not locator
        or not _is_strictly_before_deadline(validated_at, deadline)
        or not _is_strictly_before_deadline(fulfilled_at, deadline)
        or receipt.get("selected_attempt_id") != graph.request_data.get("server_attempt_id")
        or receipt.get("validation_version") != "video-single-media-v1"
        or receipt.get("late_disposition") is not None
        or receipt.get("policy_stop_disposition") is not None
        or receipt.get("automatic_application_allowed") is not False
    ):
        return False
    evidence = session.execute(
        select(ResultEvidenceRow.__table__).where(
            ResultEvidenceRow.id == receipt.get("evidence_id"),
            ResultEvidenceRow.step_id == step.get("id"),
            ResultEvidenceRow.submission_id == submission.get("id"),
        )
    ).mappings().one_or_none()
    if (
        evidence is None
        or evidence.get("source") != "provider"
        or evidence.get("evidence_kind") != "provider_video"
        or evidence.get("evidence_key") != f"video-single-result-v1:{submission.get('id')}"
        or evidence.get("validity") != "valid"
        or evidence.get("result_digest") != digest
        or evidence.get("payload_digest") != digest
        or evidence.get("result_location") != locator
    ):
        return False
    try:
        expected_result = json.dumps(
            {
                "kind": "video",
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
    projections_match = all(
        (
            graph.task.get("status") == "completed",
            graph.task.get("result") == expected_result,
            graph.task.get("billing_status") == unit.get("billing_status"),
            graph.task.get("external_provider") == submission.get("provider"),
            unit.get("billing_status") in {"charged", "no_charge"},
            unit.get("result_status") == "succeeded",
            unit.get("refunded_amount") == 0,
            unit.get("success_confirmed_at") is not None
            and _time_matches(unit.get("success_confirmed_at"), _aware_utc(fulfilled_at)),
            step.get("execution_status") == "result_observed",
            submission.get("submission_status") == "completed",
            _valid_submission_state(submission),
        )
    )
    if not projections_match:
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


def _is_due_terminal(
    graph: _LockedVideoGraph, scope: OwnerTaskScope, result_value: str
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
            _valid_submission_state(graph.submission),
        )
    )


def _confirm_due_finances(
    session: Session,
    graph: _LockedVideoGraph,
    scope: OwnerTaskScope,
    *,
    stop_signal: Event | None = None,
) -> bool:
    unit = graph.unit
    amount = unit.get("debited_amount")
    refunded = unit.get("refunded_amount")
    if type(amount) is not int or type(refunded) is not int:
        return False
    if amount == 0:
        if (
            _is_stopping(stop_signal)
            or unit.get("billing_status") != "no_charge"
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
        len(refunds) == 1
        and refunds[0].get("user_id") == scope.user_id
        and refunds[0].get("task_id") == scope.task_id
        and refunds[0].get("billing_unit_id") == unit.get("id")
        and refunds[0].get("related_debit_id") == debit.get("id")
        and refunds[0].get("business_key") == f"refund:{unit.get('id')}"
        and refunds[0].get("type") == "refund"
        and refunds[0].get("amount") == amount
        and type(refunds[0].get("balance_after")) is int
    )


def _graph_identity_matches(
    graph: _LockedVideoGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedVideoExecutionInput,
) -> bool:
    task = graph.task
    unit = graph.unit
    step = graph.step
    parent = graph.parent
    submission = graph.submission
    request = graph.request_data
    step_snapshot = step.get("input_snapshot")
    intent_snapshot = submission.get("request_snapshot")
    binding = verified.credential_binding
    snapshot = verified.snapshot
    if (
        not isinstance(step_snapshot, dict)
        or set(step_snapshot) != _STEP_SNAPSHOT_KEYS
        or not isinstance(intent_snapshot, dict)
        or set(intent_snapshot) != _INTENT_SNAPSHOT_KEYS
        or not _valid_submission_state(submission)
    ):
        return False
    expected_snapshot = {
        "owner_id": claim.scope.user_id,
        "task_id": claim.scope.task_id,
        "unit_id": verified.unit_id,
        "step_id": intent.step_id,
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
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
    return (
        intent_snapshot == expected_snapshot
        and task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
        and task.get("model_name") == verified.task_model_name
        and request.get("model_display_name") == verified.task_model_name
        and task.get("credit_cost") == unit.get("quoted_amount") == snapshot.server_quote
        and request.get("operation") == _OPERATION
        and request.get("submission_id") == parent.get("id")
        and request.get("parent_attempt_id") == parent.get("id")
        and request.get("server_attempt_id") == verified.server_attempt_id
        and request.get("billing_unit_id") == verified.unit_id
        and request.get("prepared_payload_id") == verified.prepared_payload_id
        and request.get("protected_ref") == step_snapshot.get("protected_ref")
        and request.get("snapshot_digest") == step_snapshot.get("snapshot_digest")
        and request.get("request_digest") == intent.request_digest
        and request.get("model_id") == snapshot.model_ref.model_id
        and request.get("model_source") == binding.source
        and request.get("model_owner_id") == (
            None if binding.source == "system" else binding.owner_or_system
        )
        and request.get("target_snapshot") == intent_snapshot.get("target_snapshot")
        and step_snapshot.get("server_attempt_id") == verified.server_attempt_id
        and step_snapshot.get("prepared_payload_id") == verified.prepared_payload_id
        and step.get("id") == intent.step_id == verified.step_id
        and step.get("billing_unit_id") == verified.unit_id
        and step.get("step_key") == "generate"
        and step.get("required") is True
        and step.get("step_order") == 0
        and step.get("dependencies") in ([], ())
        and step.get("input_digest") == intent.request_digest
        and unit.get("id") == verified.unit_id
        and unit.get("task_id") == claim.scope.task_id
        and unit.get("operation") == _OPERATION
        and unit.get("step_graph_version") == _GRAPH_VERSION
        and unit.get("item_key") == verified.server_attempt_id
        and unit.get("input_digest") == intent.request_digest
        and parent.get("id") == request.get("submission_id")
        and parent.get("user_id") == claim.scope.user_id
        and parent.get("operation") == _OPERATION
        and parent.get("task_ids") == [claim.scope.task_id]
        and submission.get("id") == intent.external_submission_id
        and submission.get("step_id") == intent.step_id
        and submission.get("submission_key")
        == f"video-single-create-v1:{verified.unit_id}:{intent.step_id}"
        and submission.get("provider") == intent.provider == snapshot.model_ref.provider
        and submission.get("model_ref") == intent.model_id == snapshot.model_ref.model_id
        and submission.get("request_digest") == intent.request_digest
        and _time_matches(unit.get("first_intent_at"), intent.first_intent_at)
        and _time_matches(unit.get("deadline_at"), intent.deadline_at)
        and _time_matches(submission.get("intent_at"), intent.first_intent_at)
        and _time_matches(submission.get("deadline_at"), intent.deadline_at)
        and intent.snapshot_ref == expected_ref
        and intent.step_id == verified.step_id
        and intent.attempt_id == verified.server_attempt_id
        and binding.provider == snapshot.model_ref.provider
        and binding.row_id == snapshot.model_ref.model_id
        and binding.owner_or_system == snapshot.model_ref.owner_id
    )


def _valid_observation(
    digest: str, locator: str | None, evidence_kind: str
) -> bool:
    if _DIGEST.fullmatch(digest) is None or evidence_kind not in {
        "provider_video_bytes",
        "provider_video_locator",
    }:
        return False
    if locator is None:
        return evidence_kind == "provider_video_bytes"
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
            == f"video-single-observation-v1:{intent.external_submission_id}",
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


def _graph_matches(
    graph: _LockedVideoGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedVideoExecutionInput,
    *,
    terminal: _ExpectedTerminal | None = None,
    task_only: bool = False,
) -> bool:
    task = graph.task
    unit = graph.unit
    step = graph.step
    parent = graph.parent
    submission = graph.submission
    request = graph.request_data
    step_snapshot = step.get("input_snapshot")
    intent_snapshot = submission.get("request_snapshot")
    binding = verified.credential_binding
    snapshot: FrozenExecutionSnapshot = verified.snapshot
    if not isinstance(step_snapshot, dict) or not isinstance(intent_snapshot, dict):
        return False
    if set(step_snapshot) != _STEP_SNAPSHOT_KEYS or set(intent_snapshot) != _INTENT_SNAPSHOT_KEYS:
        return False
    expected_snapshot = {
        "owner_id": claim.scope.user_id,
        "task_id": claim.scope.task_id,
        "unit_id": verified.unit_id,
        "step_id": intent.step_id,
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
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
    if intent_snapshot != expected_snapshot:
        return False
    if (
        terminal is not None
        and terminal.original_graph is not None
        and not _same_static_graph(terminal.original_graph, graph)
    ):
        return False
    if terminal is None:
        if not _active_execution_state_matches(graph):
            return False
        expected_task_status = {"processing", "reconciling"}
        expected_task_result = task.get("result")
        expected_unit_status = unit.get("result_status")
        expected_step_status = step.get("execution_status")
        expected_submission_status = submission.get("submission_status")
        expected_task_billing = unit.get("billing_status")
        expected_unit_billing = unit.get("billing_status")
        expected_refunded_amount = unit.get("refunded_amount")
    else:
        expected_task_status = {terminal.task_status}
        expected_task_result = terminal.result_value
        original_submission = (
            terminal.original_graph.submission
            if terminal.original_graph is not None
            else submission
        )
        original_unit = (
            terminal.original_graph.unit
            if terminal.original_graph is not None
            else unit
        )
        original_step = (
            terminal.original_graph.step
            if terminal.original_graph is not None
            else step
        )
        expected_unit_status = (
            original_unit.get("result_status") if task_only else terminal.unit_status
        )
        expected_step_status = (
            original_step.get("execution_status") if task_only else terminal.step_status
        )
        expected_submission_status = (
            original_submission.get("submission_status")
            if task_only
            else terminal.submission_status
        )
        expected_task_billing = terminal.task_billing_status
        expected_unit_billing = (
            original_unit.get("billing_status")
            if task_only
            else terminal.unit_billing_status
        )
        expected_refunded_amount = (
            original_unit.get("refunded_amount")
            if task_only
            else terminal.refunded_amount
        )
    manifest_digest = step_snapshot.get("ordered_manifest_digest")
    expected_ref = (
        f"{_GRAPH_VERSION}:{verified.prepared_payload_id}:"
        f"{step_snapshot.get('snapshot_digest')}:{manifest_digest}"
    )
    expected_external_id = (
        submission.get("external_task_id")
        if terminal is None
        else original_submission.get("external_task_id")
    )
    return all(
        (
            task.get("id") == claim.scope.task_id,
            task.get("user_id") == claim.scope.user_id,
            task.get("type") == _TASK_TYPE,
            task.get("status") in expected_task_status,
            task.get("result") == expected_task_result,
            task.get("billing_status") == expected_task_billing,
            task.get("user_cancelled_at") is None,
            task.get("model_name") == verified.task_model_name,
            request.get("model_display_name") == verified.task_model_name,
            task.get("credit_cost") == unit.get("quoted_amount") == snapshot.server_quote,
            request.get("operation") == _OPERATION,
            request.get("submission_id") == parent.get("id"),
            request.get("parent_attempt_id") == parent.get("id"),
            request.get("server_attempt_id") == verified.server_attempt_id,
            request.get("billing_unit_id") == verified.unit_id,
            request.get("prepared_payload_id") == verified.prepared_payload_id,
            request.get("protected_ref") == step_snapshot.get("protected_ref"),
            request.get("snapshot_digest") == step_snapshot.get("snapshot_digest"),
            request.get("request_digest") == intent.request_digest,
            request.get("model_id") == snapshot.model_ref.model_id,
            request.get("model_source") == binding.source,
            request.get("model_owner_id") == (
                None if binding.source == "system" else binding.owner_or_system
            ),
            request.get("target_snapshot") == intent_snapshot.get("target_snapshot"),
            step_snapshot.get("server_attempt_id") == verified.server_attempt_id,
            step_snapshot.get("prepared_payload_id") == verified.prepared_payload_id,
            step.get("id") == intent.step_id == verified.step_id,
            step.get("billing_unit_id") == verified.unit_id,
            step.get("step_key") == "generate",
            step.get("required") is True,
            step.get("step_order") == 0,
            step.get("dependencies") in ([], ()),
            step.get("input_digest") == intent.request_digest,
            step.get("execution_status") == expected_step_status,
            unit.get("id") == verified.unit_id,
            unit.get("task_id") == claim.scope.task_id,
            unit.get("operation") == _OPERATION,
            unit.get("step_graph_version") == _GRAPH_VERSION,
            unit.get("item_key") == verified.server_attempt_id,
            unit.get("input_digest") == intent.request_digest,
            unit.get("billing_status") == expected_unit_billing,
            unit.get("result_status") == expected_unit_status,
            unit.get("refunded_amount") == expected_refunded_amount,
            unit.get("user_cancelled_at") is None,
            parent.get("id") == request.get("submission_id"),
            parent.get("user_id") == claim.scope.user_id,
            parent.get("operation") == _OPERATION,
            parent.get("task_ids") == [claim.scope.task_id],
            submission.get("id") == intent.external_submission_id,
            submission.get("step_id") == intent.step_id,
            submission.get("submission_key")
            == f"video-single-create-v1:{verified.unit_id}:{intent.step_id}",
            submission.get("provider") == intent.provider == snapshot.model_ref.provider,
            submission.get("model_ref") == intent.model_id == snapshot.model_ref.model_id,
            submission.get("request_digest") == intent.request_digest,
            submission.get("submission_status") == expected_submission_status
            and expected_submission_status in {"submitting", "submitted", "unknown", "completed", "failed"},
            submission.get("external_task_id") == expected_external_id,
            _valid_submission_state(submission),
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


def _active_execution_state_matches(graph: _LockedVideoGraph) -> bool:
    task = graph.task
    unit = graph.unit
    step = graph.step
    submission = graph.submission
    task_status = task.get("status")
    fresh = (
        task_status in {"processing", "reconciling"}
        and task.get("result") is None
        and unit.get("result_status") == "pending"
        and step.get("execution_status") == "submitting"
        and submission.get("submission_status") in {"submitting", "submitted"}
        and _valid_submission_state(submission)
    )
    resumable = (
        task_status in {"processing", "reconciling"}
        and task.get("result") == _UNRESOLVED_RESULT
        and unit.get("result_status") == "unknown"
        and step.get("execution_status") == "reconciling"
        and submission.get("submission_status") == "unknown"
        and _safe_text(submission.get("external_task_id"), 255)
        and isinstance(submission.get("submitted_at"), datetime)
        and _valid_submission_state(submission)
    )
    return bool(
        task.get("user_cancelled_at") is None
        and unit.get("user_cancelled_at") is None
        and unit.get("success_confirmed_at") is None
        and unit.get("billing_status") in {"charged", "no_charge"}
        and task.get("billing_status") == unit.get("billing_status")
        and unit.get("refunded_amount") == 0
        and (fresh or resumable)
    )


def _same_static_graph(
    original: _LockedVideoGraph, current: _LockedVideoGraph
) -> bool:
    mutable_columns = {
        "task": {
            "status", "result", "progress", "progress_message", "external_provider",
            "recovery_status", "billing_status", "updated_at",
        },
        "unit": {
            "result_status", "success_confirmed_at", "billing_status",
            "refunded_amount", "updated_at",
        },
        "step": {"execution_status", "updated_at"},
        "submission": {"submission_status", "submitted_at", "updated_at"},
        "parent": set(),
    }
    for name, ignored in mutable_columns.items():
        before = getattr(original, name)
        after = getattr(current, name)
        if {key: value for key, value in before.items() if key not in ignored} != {
            key: value for key, value in after.items() if key not in ignored
        }:
            return False
    return original.request_data == current.request_data


def _claim_is_current(
    task: Mapping[str, object], claim: ClaimAuthority, now: datetime
) -> bool:
    lease = task.get("lease_until")
    return (
        task.get("status") in {"processing", "reconciling"}
        and task.get("user_cancelled_at") is None
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and isinstance(lease, datetime)
        and _aware_utc(lease) > now
        and not claim.local_lost_signal.is_set()
    )


def _valid_intent(
    intent: CommittedIntent, verified: VerifiedVideoExecutionInput
) -> bool:
    return (
        type(intent) is CommittedIntent
        and type(verified) is VerifiedVideoExecutionInput
        and isinstance(verified.task_model_name, str)
        and bool(verified.task_model_name)
        and intent.step_id == verified.step_id
        and intent.attempt_id == verified.server_attempt_id
        and intent.snapshot_ref.startswith(
            f"{_GRAPH_VERSION}:{verified.prepared_payload_id}:"
        )
        and intent.provider == verified.snapshot.model_ref.provider
        and intent.model_id == verified.snapshot.model_ref.model_id
        and _DIGEST.fullmatch(intent.request_digest) is not None
        and intent.first_intent_at == intent.committed_at
        and intent.deadline_at > intent.first_intent_at
    )


def _valid_external_ref_binding(
    external_ref: object,
    intent: CommittedIntent,
    verified: VerifiedVideoExecutionInput,
) -> bool:
    return (
        type(external_ref) is ExternalOperationRef
        and _valid_intent(intent, verified)
        and external_ref.intent_id == intent.external_submission_id
        and external_ref.attempt_id == intent.attempt_id
        and external_ref.provider == intent.provider
    )


def _valid_external_operation_ref(
    external_ref: object, intent: CommittedIntent
) -> bool:
    return (
        type(external_ref) is ExternalOperationRef
        and external_ref.intent_id == intent.external_submission_id
        and external_ref.attempt_id == intent.attempt_id
        and external_ref.provider == intent.provider
        and external_ref.create_outcome == "submitted"
        and _safe_text(external_ref.external_id, 255)
    )


def _valid_submission_state(submission: Mapping[str, object]) -> bool:
    status = submission.get("submission_status")
    external_id = submission.get("external_task_id")
    submitted_at = submission.get("submitted_at")
    has_external_id = _safe_text(external_id, 255)
    has_submitted_at = isinstance(submitted_at, datetime)
    if status == "submitting":
        return external_id is None and submitted_at is None
    if status == "submitted":
        return has_external_id and has_submitted_at
    if status == "unknown":
        return (external_id is None and submitted_at is None) or (
            has_external_id and has_submitted_at
        )
    if status == "completed":
        return (external_id is None and submitted_at is None) or (
            has_external_id and has_submitted_at
        )
    if status == "failed":
        return (external_id is None and submitted_at is None) or (
            has_external_id and has_submitted_at
        )
    return False


def _project_assistant_message(session: Session, message_id: str, locator: str) -> bool:
    message = session.execute(
        select(chat_messages)
        .where(chat_messages.c.id == message_id)
        .with_for_update()
    ).mappings().one_or_none()
    if (
        message is None
        or message.get("role") != "assistant"
        or message.get("content") != "正在生成视频..."
    ):
        return False
    changed = session.execute(
        update(chat_messages)
        .where(
            chat_messages.c.id == message_id,
            chat_messages.c.role == "assistant",
            chat_messages.c.content == "正在生成视频...",
        )
        .values(content=locator)
    )
    return changed.rowcount == 1


def _encode_success_result(
    stored: StoredMedia, receipt: GenerationResultReceipt
) -> str:
    return json.dumps(
        {
            "kind": "video",
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
        and isinstance(value.content_digest, str)
        and _DIGEST.fullmatch(value.content_digest) is not None
        and isinstance(value.media_type, str)
        and value.media_type.startswith("video/")
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


def _with_now(value: _ExpectedTerminal, now: datetime) -> _ExpectedTerminal:
    return _ExpectedTerminal(
        kind=value.kind,
        task_id=value.task_id,
        owner_id=value.owner_id,
        unit_id=value.unit_id,
        step_id=value.step_id,
        submission_id=value.submission_id,
        result_value=value.result_value,
        task_status=value.task_status,
        unit_status=value.unit_status,
        step_status=value.step_status,
        submission_status=value.submission_status,
        stored=value.stored,
        receipt=(
            None
            if value.receipt is None
            else GenerationResultReceipt(
                result_ref=value.receipt.result_ref,
                owner_id=value.receipt.owner_id,
                task_id=value.receipt.task_id,
                unit_id=value.receipt.unit_id,
                operation=value.receipt.operation,
                step_id=value.receipt.step_id,
                submission_id=value.receipt.submission_id,
                evidence_id=value.receipt.evidence_id,
                selected_attempt_id=value.receipt.selected_attempt_id,
                recoverable_locator=value.receipt.recoverable_locator,
                content_digest=value.receipt.content_digest,
                validation_version=value.receipt.validation_version,
                validated_at=now,
                generation_fulfilled_at=now,
                late_disposition=value.receipt.late_disposition,
                policy_stop_disposition=value.receipt.policy_stop_disposition,
                automatic_application_allowed=value.receipt.automatic_application_allowed,
            )
        ),
        evidence_id=value.evidence_id,
        now=now,
        task_billing_status=value.task_billing_status,
        unit_billing_status=value.unit_billing_status,
        refunded_amount=value.refunded_amount,
        refund=value.refund,
        original_graph=value.original_graph,
    )


def _time_matches(value: object, expected: datetime) -> bool:
    return isinstance(value, datetime) and _aware_utc(value) == _aware_utc(expected)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


__all__ = ("SqlAlchemyVideoSingleResults",)
