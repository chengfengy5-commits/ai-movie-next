"""Fenced persistence for one original image.single provider result."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
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
    FrozenExecutionSnapshot,
    StoredMedia,
)
from haoai_backend.generation_credentials.execution_snapshot import (
    VerifiedImageExecutionInput,
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


_TASK_TYPE = "image-single"
_OPERATION = "image.single"
_GRAPH_VERSION = "image-single-v1"
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
_TERMINAL_MESSAGE = "图片生成结果需要恢复确认"

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
class _LockedImageGraph:
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
    original_graph: _LockedImageGraph | None = None


class SqlAlchemyImageSingleResults:
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

    def complete(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageExecutionInput,
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
            validation_version="image-single-media-v1",
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
        return self._write_terminal(claim, intent, verified, expected)

    def fail(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageExecutionInput,
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
        verified: VerifiedImageExecutionInput,
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
        verified: VerifiedImageExecutionInput,
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
            f"image-single-observation-v1:{intent.external_submission_id}"
        )
        evidence_id: str | None = None
        try:
            with self._session_factory() as session:
                with session.begin():
                    graph = _lock_image_graph(session, claim)
                    now = self._read_clock()
                    if graph is None or now is None or not _graph_identity_matches(
                        graph, claim, intent, verified
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
        verified: VerifiedImageExecutionInput,
        evidence_key: str,
        payload_digest: str,
        result_location: str | None,
        evidence_kind: str,
    ) -> Literal["recorded", "pending"]:
        try:
            with self._session_factory() as session:
                graph = _lock_image_graph(session, claim)
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
        verified: VerifiedImageExecutionInput,
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
        verified: VerifiedImageExecutionInput,
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
                    graph = _lock_image_graph(session, claim)
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
                        session, claim, graph, expected
                    )
                    if expected.kind == "failure":
                        fresh_graph = _lock_image_graph(session, claim)
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
                            "图片已生成" if expected.kind == "success"
                            else _TERMINAL_MESSAGE if expected.kind == "unknown"
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
                            task_table.c.lease_until == _database_datetime(
                                _aware_utc(graph.task["lease_until"])
                            ),
                            task_table.c.lease_until > _database_datetime(now),
                            task_table.c.request_data == graph.task["request_data"],
                            task_table.c.result.is_(None),
                        )
                        .values(**task_values)
                    )
                    if task_change.rowcount != 1:
                        raise _OwnershipLost
                    after_task = _lock_image_graph(session, claim)
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
                            unit_table.c.result_status == "pending",
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
                            ExecutionStepRow.execution_status == "submitting",
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
                            ExternalSubmissionRow.submission_status == "submitting",
                            ExternalSubmissionRow.external_task_id.is_(None),
                            ExternalSubmissionRow.intent_at == _database_datetime(
                                intent.first_intent_at
                            ),
                            ExternalSubmissionRow.deadline_at == _database_datetime(
                                intent.deadline_at
                            ),
                        )
                        .values(
                            submission_status=expected.submission_status,
                            submitted_at=_database_datetime(now),
                            updated_at=_database_datetime(now),
                        )
                    )
                    if submission_change.rowcount != 1:
                        raise _OwnershipLost

                    after_children = _lock_image_graph(session, claim)
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
                                    f"image-single-result-v1:{intent.external_submission_id}"
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
        verified: VerifiedImageExecutionInput,
        claim: ClaimAuthority,
    ) -> Literal["confirmed", "pending", "ownership_lost"]:
        try:
            with self._session_factory() as session:
                graph = _lock_image_graph(session, claim)
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
                    or submission["external_task_id"] is not None
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
        claim: ClaimAuthority,
        graph: _LockedImageGraph,
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
                        CreditLogRow.task_id == claim.scope.task_id,
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
            .where(UserCreditRow.user_id == claim.scope.user_id)
            .with_for_update()
        ).mappings().one_or_none()
        debit = session.execute(
            select(CreditLogRow.__table__)
            .where(
                CreditLogRow.business_key == f"debit:{unit['id']}",
                CreditLogRow.user_id == claim.scope.user_id,
                CreditLogRow.task_id == claim.scope.task_id,
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
            owner_id=claim.scope.user_id,
            task_id=claim.scope.task_id,
            unit_id=str(unit["id"]),
            debit_id=str(debit["id"]),
            refund_id=refund_id,
            refund_key=refund_key,
            amount=amount,
            balance_before=int(wallet["credits"]),
            balance_after=int(wallet["credits"]) + amount,
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
            description="image.single provider rejection refund",
            task_id=settlement.task_id,
            business_key=settlement.refund_key,
            billing_unit_id=settlement.unit_id,
            related_debit_id=settlement.debit_id,
            created_at=_database_datetime(now),
        )
    )


def _lock_image_graph(session: Session, claim: ClaimAuthority) -> _LockedImageGraph | None:
    user = session.execute(
        select(UserRow.id)
        .where(UserRow.id == claim.scope.user_id)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if user is None:
        return None
    task_row = session.execute(
        select(AITaskRow.__table__)
        .where(
            AITaskRow.id == claim.scope.task_id,
            AITaskRow.user_id == claim.scope.user_id,
        )
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if task_row is None:
        return None
    units = session.execute(
        select(BillingUnitRow.__table__)
        .where(BillingUnitRow.task_id == claim.scope.task_id)
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
            TaskSubmissionRow.user_id == claim.scope.user_id,
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
    return _LockedImageGraph(
        task=task,
        unit=unit,
        step=step,
        parent=dict(parent),
        submission=dict(submissions[0]),
        request_data=request_data,
    )


def _graph_identity_matches(
    graph: _LockedImageGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedImageExecutionInput,
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
        == f"image-single-create-v1:{verified.unit_id}:{intent.step_id}"
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
            == f"image-single-observation-v1:{intent.external_submission_id}",
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
    graph: _LockedImageGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedImageExecutionInput,
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
        expected_task_status = {"processing", "reconciling"}
        expected_task_result = None
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
        expected_submission_status = "submitting" if task_only else terminal.submission_status
        expected_task_billing = terminal.task_billing_status
        expected_unit_billing = (
            graph.unit.get("billing_status") if task_only else terminal.unit_billing_status
        )
        expected_refunded_amount = (
            graph.unit.get("refunded_amount") if task_only else terminal.refunded_amount
        )
    manifest_digest = step_snapshot.get("ordered_manifest_digest")
    expected_ref = (
        f"{_GRAPH_VERSION}:{verified.prepared_payload_id}:"
        f"{step_snapshot.get('snapshot_digest')}:{manifest_digest}"
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
            == f"image-single-create-v1:{verified.unit_id}:{intent.step_id}",
            submission.get("provider") == intent.provider == snapshot.model_ref.provider,
            submission.get("model_ref") == intent.model_id == snapshot.model_ref.model_id,
            submission.get("request_digest") == intent.request_digest,
            submission.get("submission_status") == expected_submission_status,
            submission.get("external_task_id") is None,
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
    original: _LockedImageGraph, current: _LockedImageGraph
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
    intent: CommittedIntent, verified: VerifiedImageExecutionInput
) -> bool:
    return (
        type(intent) is CommittedIntent
        and type(verified) is VerifiedImageExecutionInput
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


def _project_assistant_message(session: Session, message_id: str, locator: str) -> bool:
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
        and isinstance(value.content_digest, str)
        and _DIGEST.fullmatch(value.content_digest) is not None
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


__all__ = ("SqlAlchemyImageSingleResults",)
