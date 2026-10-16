"""Owned transaction lifecycle for the registered single-frame review graph."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.chat_text_generation.errors import ChatTextGenerationError
from haoai_backend.chat_text_generation.persistence import (
    _begin_write,
    _check_concurrency,
    _now,
)
from haoai_backend.generation_catalog.admission import AdmissionPolicy, prepare_admission
from haoai_backend.generation_catalog.admission_types import (
    AdmissionContext,
    AdmissionPreparationFacts,
    AdmissionRefusal,
    build_admission_context,
)
from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import GenerationActor, TrustedActor
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_core.fee_phases import build_fee_phase_plan
from haoai_backend.generation_core.graph_planning import GraphItemInput, build_operation_graph_plan
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
)
from haoai_backend.generation_credentials.domain import PreparedPayloadStaged
from haoai_backend.generation_credentials.frame_review_snapshot import (
    FrameReviewSnapshot,
    build_frame_review_snapshot,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads


GRAPH_VERSION = "frame-review-v1"


@dataclass(frozen=True, slots=True)
class _StagedReview:
    response: dict[str, object]
    owner_id: str
    task_id: str
    message_id: str
    submission_id: str
    unit_id: str
    step_id: str
    request_data: str
    model_name: str
    snapshot: FrameReviewSnapshot


def admit_frame_review(
    session_factory: Callable[[], Session] | None,
    user_id: str,
    request: object,
    *,
    registry_capabilities: RegistryCapabilities,
    max_user_concurrency: int,
    id_factory: Callable[[], str] | None,
    clock: Callable[[], datetime] | None,
) -> dict[str, object]:
    """Prepare and commit one complete uncharged frame.review graph."""

    if session_factory is None:
        raise ChatTextGenerationError(503, "聊天服务暂不可用")
    if not isinstance(registry_capabilities, RegistryCapabilities):
        raise ChatTextGenerationError(503, "AI 复核准入能力尚未配置")
    try:
        payload = _request_mapping(request)
        actor = GenerationActor(
            user_id=user_id,
            trusted_account_identity=TrustedActor(user_id=user_id),
        )
        context = build_admission_context(actor, "frame.review", payload)
    except (TypeError, ValueError) as error:
        raise ChatTextGenerationError(422, "AI 复核请求无效") from error

    session: Session | None = None
    writer_closed = False
    try:
        session = session_factory()
        _begin_write(session)
        now = _now(clock)
        result = prepare_admission(
            session,
            context,
            now=_aware_utc(now),
            policy=AdmissionPolicy(max_concurrent_tasks=0),
        )
        if isinstance(result, AdmissionRefusal):
            raise ChatTextGenerationError(result.status_code, result.message)
        if (
            result.operation != "frame.review"
            or result.fingerprint != context.fingerprint
            or result.executable
            or result.reserved_amount != 0
            or result.quoted_total < 0
            or len(result.items) != 1
        ):
            raise ChatTextGenerationError(503, "AI 复核准入事实无法安全确认")

        # prepare_admission already holds the account lock in this transaction.
        _check_concurrency(session, user_id, max_user_concurrency, account_locked=True)
        staged = _stage_review(
            session,
            user_id=user_id,
            request=payload,
            context=context,
            preparation=result,
            registry_capabilities=registry_capabilities,
            id_factory=id_factory,
            now=now,
        )
        try:
            session.commit()
        except Exception:
            _rollback(session)
            session.close()
            writer_closed = True
            if _confirm_committed_graph(session_factory, staged):
                return staged.response
            raise ChatTextGenerationError(503, "提交 AI 复核任务结果无法确认")
        return staged.response
    except ChatTextGenerationError:
        if session is not None and not writer_closed:
            _rollback(session)
        raise
    except (
        GenerationError,
        SQLAlchemyError,
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
    ) as error:
        if session is not None and not writer_closed:
            _rollback(session)
        raise ChatTextGenerationError(503, "提交 AI 复核任务失败") from error
    except Exception as error:
        if session is not None and not writer_closed:
            _rollback(session)
        raise ChatTextGenerationError(503, "提交 AI 复核任务失败") from error
    finally:
        if session is not None and not writer_closed:
            session.close()


def _stage_review(
    session: Session,
    *,
    user_id: str,
    request: Mapping[str, object],
    context: AdmissionContext,
    preparation: AdmissionPreparationFacts,
    registry_capabilities: RegistryCapabilities,
    id_factory: Callable[[], str] | None,
    now: datetime,
) -> _StagedReview:
    operation_graph = "frame.review"
    ids = _new_ids(id_factory)
    submission_id = ids("submission_id")
    task_id = ids("task_id")
    unit_id = ids("unit_id")
    step_id = ids("step_id")
    attempt_id = ids("server_attempt_id")
    payload_id = ids("prepared_payload_id")
    task_message_id = ids("task_message_id")
    snapshot = build_frame_review_snapshot(
        session,
        actor_id=user_id,
        request=request,
        context=context,
        preparation=preparation,
        registry_capabilities=registry_capabilities,
        submission_id=submission_id,
        task_id=task_id,
        unit_id=unit_id,
        step_id=step_id,
        server_attempt_id=attempt_id,
        prepared_payload_id=payload_id,
        original_message_id=str(request.get("message_id") or ""),
    )
    graph = build_operation_graph_plan(
        operation_graph,
        graph_version=GRAPH_VERSION,
        items=(
            GraphItemInput(
                item_id=attempt_id,
                target_id=snapshot.payload.target_ref,
                task_id=task_id,
                unit_id=unit_id,
                step_ids=(step_id,),
                request_digest=snapshot.request_digest,
                rule_digest=snapshot.rule_digest,
                snapshot=snapshot.snapshot,
            ),
        ),
    )
    fee_plan = build_fee_phase_plan(operation_graph, graph=graph)
    if (
        fee_plan.quoted_total != preparation.quoted_total
        or fee_plan.debit_timing != "after_application_commit"
    ):
        raise ValueError("frame review must use the exact post-apply quote without admission debit")

    protected_ref = snapshot.protected_ref
    safe_request_data = _safe_request_data(
        request=request,
        item=preparation.items[0],
        snapshot=snapshot,
        unit_id=unit_id,
        submission_id=submission_id,
        attempt_id=attempt_id,
        payload_id=payload_id,
        protected_ref=protected_ref,
        snapshot_digest=graph.items[0].snapshot_digest,
        manifest_snapshot_digest=snapshot.manifest_snapshot_digest,
    )
    model_name = preparation.items[0].model.display_name
    session.add(
        AITaskRow(
            id=task_id,
            user_id=user_id,
            type=graph.tasks[0].task_type,
            status="queued",
            claimed_by=None,
            lease_until=None,
            execution_generation=0,
            claim_token=None,
            recovery_status="ready",
            billing_status="unbilled",
            user_cancelled_at=None,
            cancellation_reason=None,
            message_id=task_message_id,
            credit_cost=0,
            result=None,
            request_data=safe_request_data,
            model_name=model_name,
            progress=0,
            progress_message="任务已提交，排队等待 Worker 认领...",
            external_task_id=None,
            external_provider=None,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        TaskSubmissionRow(
            id=submission_id,
            user_id=user_id,
            operation=operation_graph,
            idempotency_key=f"frame-review:{submission_id}",
            request_digest=snapshot.request_digest,
            task_ids=[task_id],
            task_quote_id=None,
            status="accepted",
            created_at=_aware_utc(now),
        )
    )
    session.add(
        BillingUnitRow(
            id=unit_id,
            task_id=task_id,
            item_key=attempt_id,
            operation=operation_graph,
            step_graph_version=GRAPH_VERSION,
            input_digest=snapshot.request_digest,
            quoted_amount=snapshot.snapshot.server_quote,
            debited_amount=0,
            refunded_amount=0,
            billing_status="unbilled",
            result_status="pending",
            first_intent_at=None,
            deadline_at=None,
            success_confirmed_at=None,
            user_cancelled_at=None,
            cancellation_reason=None,
            quote_id=None,
            quote_unit_key=None,
            candidate_digest=None,
            created_at=_aware_utc(now),
            updated_at=_aware_utc(now),
        )
    )
    session.add(
        ExecutionStepRow(
            id=step_id,
            billing_unit_id=unit_id,
            step_key="generate",
            dependencies=[],
            input_digest=snapshot.request_digest,
            input_snapshot={
                "server_attempt_id": attempt_id,
                "prepared_payload_id": payload_id,
                "protected_ref": protected_ref,
                "snapshot_digest": graph.items[0].snapshot_digest,
                "manifest_snapshot_digest": snapshot.manifest_snapshot_digest,
            },
            required=True,
            step_order=0,
            execution_status="prepared",
            created_at=_aware_utc(now),
            updated_at=_aware_utc(now),
        )
    )
    registry_result = build_credential_registry(registry_capabilities).stage_prepared_payload(
        session,
        snapshot.payload,
        payload_id=payload_id,
    )
    if (
        not isinstance(registry_result, PreparedPayloadStaged)
        or registry_result.prepared_payload_id != payload_id
    ):
        raise ValueError("protected frame review payload could not be staged")
    session.flush()
    response = {
        "task_id": task_id,
        "model_name": model_name,
        "is_system": preparation.items[0].model.source.value == "system",
        "credit_cost": snapshot.snapshot.server_quote,
    }
    return _StagedReview(
        response=response,
        owner_id=user_id,
        task_id=task_id,
        message_id=task_message_id,
        submission_id=submission_id,
        unit_id=unit_id,
        step_id=step_id,
        request_data=safe_request_data,
        model_name=model_name,
        snapshot=snapshot,
    )


def _safe_request_data(
    *,
    request: Mapping[str, object],
    item: object,
    snapshot: FrameReviewSnapshot,
    unit_id: str,
    submission_id: str,
    attempt_id: str,
    payload_id: str,
    protected_ref: str,
    snapshot_digest: str,
    manifest_snapshot_digest: str,
) -> str:
    model = item.model
    target = snapshot.target
    return json.dumps(
        {
            "via": "registered_generation_graph",
            "mode": "frame_review",
            "operation": "frame.review",
            "submission_id": submission_id,
            "billing_unit_id": unit_id,
            "parent_attempt_id": submission_id,
            "server_attempt_id": attempt_id,
            "prepared_payload_id": payload_id,
            "protected_ref": protected_ref,
            "item_id": attempt_id,
            "unit_key": attempt_id,
            "graph_version": GRAPH_VERSION,
            "step_graph_version": GRAPH_VERSION,
            "request_digest": snapshot.request_digest,
            "rule_digest": snapshot.rule_digest,
            "snapshot_digest": snapshot_digest,
            "manifest_snapshot_digest": manifest_snapshot_digest,
            "chapter_id": target.chapter_id,
            "frame_index": target.compat_frame_index,
            "prompt_id": str(request.get("prompt_id") or ""),
            "source_message_id": str(request.get("message_id") or ""),
            "model_source": model.source.value,
            "model_id": model.catalog_id,
            "model_owner_id": "system" if model.source.value == "system" else model.owner_id,
            "model_display_name": model.display_name,
            "cost_per_call": snapshot.snapshot.server_quote,
            "enable_thinking": request.get("enable_thinking") is True,
            "target_ref": snapshot.payload.target_ref,
            "model_ref": snapshot.payload.model_ref,
            "grant_ref": snapshot.payload.grant_ref,
            "reference_ref": snapshot.snapshot.reference_snapshot_ref,
            "target_snapshot": _plain(target),
            "is_system": model.source.value == "system",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _confirm_committed_graph(
    session_factory: Callable[[], Session],
    staged: _StagedReview,
) -> bool:
    """Confirm an uncertain commit through a fresh connection; never reinsert."""

    session: Session | None = None
    try:
        session = session_factory()
        with session.no_autoflush:
            task = session.execute(
                select(AITaskRow).where(AITaskRow.id == staged.task_id)
            ).scalar_one_or_none()
            unit = session.execute(
                select(BillingUnitRow).where(BillingUnitRow.id == staged.unit_id)
            ).scalar_one_or_none()
            step = session.execute(
                select(ExecutionStepRow).where(ExecutionStepRow.id == staged.step_id)
            ).scalar_one_or_none()
            submission = session.execute(
                select(TaskSubmissionRow).where(TaskSubmissionRow.id == staged.submission_id)
            ).scalar_one_or_none()
            payload = session.execute(
                select(prepared_payloads).where(
                    prepared_payloads.c.prepared_payload_id == staged.snapshot.prepared_payload_id
                )
            ).mappings().one_or_none()
        return bool(
            task is not None
            and task.user_id == staged.owner_id
            and task.type == "ai-review"
            and task.status == "queued"
            and task.message_id == staged.message_id
            and task.credit_cost == 0
            and task.billing_status == "unbilled"
            and task.model_name == staged.model_name
            and task.request_data == staged.request_data
            and unit is not None
            and unit.task_id == staged.task_id
            and unit.operation == "frame.review"
            and unit.step_graph_version == GRAPH_VERSION
            and unit.input_digest == staged.snapshot.request_digest
            and unit.quoted_amount == staged.snapshot.snapshot.server_quote
            and unit.debited_amount == 0
            and unit.billing_status == "unbilled"
            and unit.result_status == "pending"
            and step is not None
            and step.billing_unit_id == staged.unit_id
            and step.step_key == "generate"
            and step.input_digest == staged.snapshot.request_digest
            and step.execution_status == "prepared"
            and step.input_snapshot
            == {
                "server_attempt_id": staged.snapshot.server_attempt_id,
                "prepared_payload_id": staged.snapshot.prepared_payload_id,
                "protected_ref": staged.snapshot.protected_ref,
                "snapshot_digest": json.loads(staged.request_data)["snapshot_digest"],
                "manifest_snapshot_digest": staged.snapshot.manifest_snapshot_digest,
            }
            and submission is not None
            and submission.user_id == staged.owner_id
            and submission.operation == "frame.review"
            and submission.idempotency_key == f"frame-review:{staged.submission_id}"
            and submission.request_digest == staged.snapshot.request_digest
            and submission.task_ids == [staged.task_id]
            and submission.status == "accepted"
            and payload is not None
            and payload["actor_id"] == staged.owner_id
            and payload["operation"] == "frame.review"
            and payload["prepared_payload_id"] == staged.snapshot.prepared_payload_id
            and payload["server_attempt_id"] == staged.snapshot.server_attempt_id
            and payload["context_fingerprint"] == staged.snapshot.request_digest
            and payload["binding_id"] == staged.snapshot.payload.binding_id
            and payload["binding_version"] == staged.snapshot.payload.binding_version
            and payload["principal_id"] == staged.snapshot.payload.principal_id
            and payload["schema_version"] == staged.snapshot.payload.schema_version
            and payload["projector_version"] == staged.snapshot.payload.projector_version
            and payload["reference_policy_version"]
            == staged.snapshot.payload.reference_policy_version
            and payload["target_ref"] == staged.snapshot.payload.target_ref
            and payload["model_ref"] == staged.snapshot.payload.model_ref
            and payload["grant_ref"] == staged.snapshot.payload.grant_ref
            and payload["source"] == staged.snapshot.payload.scope.source
            and payload["row_id"] == staged.snapshot.payload.scope.row_id
            and payload["owner_or_system"]
            == staged.snapshot.payload.scope.owner_or_system
            and payload["provider"] == staged.snapshot.payload.scope.provider
            and payload["encryption_key_id"]
            == staged.snapshot.payload.encryption_key_id
            and payload["command_proof"] == staged.snapshot.payload.command_proof
            and payload["ciphertext"] == staged.snapshot.payload.ciphertext
            and payload["nonce"] == staged.snapshot.payload.nonce
        )
    except Exception:
        return False
    finally:
        if session is not None:
            session.close()


def _request_mapping(request: object) -> Mapping[str, object]:
    if isinstance(request, Mapping):
        return request
    model_dump = getattr(request, "model_dump", None)
    value = model_dump() if callable(model_dump) else None
    if not isinstance(value, Mapping):
        raise TypeError("review request must be a mapping")
    return value


def _new_ids(factory: Callable[[], str] | None) -> Callable[[str], str]:
    seen: set[str] = set()

    def create(label: str) -> str:
        try:
            value = factory() if factory is not None else str(uuid4())
        except Exception as error:
            raise ChatTextGenerationError(503, "服务暂不可用") from error
        if not isinstance(value, str) or not value or len(value) > 36 or value in seen:
            raise ChatTextGenerationError(503, "服务暂不可用")
        seen.add(value)
        return value

    return create


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _rollback(session: Session) -> None:
    try:
        session.rollback()
    except Exception:
        pass


def _plain(value: object) -> object:
    from dataclasses import fields, is_dataclass
    from enum import Enum

    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value
