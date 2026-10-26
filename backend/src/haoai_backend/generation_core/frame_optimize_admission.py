"""Transactional producer for registered frame.optimize admission graphs."""

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
from haoai_backend.chat_text_generation.persistence import _begin_write, _now
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
from haoai_backend.generation_credentials.frame_optimize_snapshot import (
    FrameOptimizeItemIdentity,
    FrameOptimizeProtectedBatch,
    FrameOptimizeSnapshotCandidate,
    build_frame_optimize_snapshot_candidates,
    protect_frame_optimize_batch,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads


GRAPH_VERSION = "frame-optimize-v1"
_OPERATION = "frame.optimize"


@dataclass(frozen=True, slots=True)
class _StagedFrameOptimize:
    response: dict[str, object]
    owner_id: str
    task_id: str
    task_message_id: str
    task_model_name: str
    submission_id: str
    request_data: str
    graph_digest: str
    candidates: tuple[FrameOptimizeSnapshotCandidate, ...]
    protected: FrameOptimizeProtectedBatch


def admit_frame_optimize(
    session_factory: Callable[[], Session] | None,
    user_id: str,
    request: object,
    *,
    registry_capabilities: RegistryCapabilities,
    max_user_concurrency: int,
    id_factory: Callable[[], str] | None,
    clock: Callable[[], datetime] | None,
) -> dict[str, object]:
    """Prepare and commit a complete, uncharged frame.optimize batch graph."""

    if session_factory is None:
        raise ChatTextGenerationError(503, "聊天服务暂不可用")
    if not isinstance(registry_capabilities, RegistryCapabilities):
        raise ChatTextGenerationError(503, "分镜优化准入能力尚未配置")
    if type(max_user_concurrency) is not int:
        raise ChatTextGenerationError(503, "分镜优化并发策略无法确认")
    try:
        payload = _request_mapping(request)
        actor = GenerationActor(
            user_id=user_id,
            trusted_account_identity=TrustedActor(user_id=user_id),
        )
        context = build_admission_context(actor, _OPERATION, payload)
    except (TypeError, ValueError) as error:
        raise ChatTextGenerationError(422, "分镜优化请求无效") from error

    session: Session | None = None
    writer_closed = False
    try:
        session = session_factory()
        _begin_write(session)
        now = _now(clock)
        preparation = prepare_admission(
            session,
            context,
            now=_aware_utc(now),
            policy=AdmissionPolicy(max_concurrent_tasks=max_user_concurrency),
        )
        if isinstance(preparation, AdmissionRefusal):
            raise ChatTextGenerationError(preparation.status_code, preparation.message)
        if not _valid_preparation(context, preparation):
            raise ChatTextGenerationError(503, "分镜优化准入事实无法安全确认")

        staged = _stage_frame_optimize(
            session,
            user_id=user_id,
            request=payload,
            context=context,
            preparation=preparation,
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
            raise ChatTextGenerationError(503, "提交分镜优化任务结果无法确认")
        return staged.response
    except ChatTextGenerationError:
        if session is not None and not writer_closed:
            _rollback(session)
        raise
    except (GenerationError, SQLAlchemyError, TypeError, ValueError, KeyError, AttributeError) as error:
        if session is not None and not writer_closed:
            _rollback(session)
        raise ChatTextGenerationError(503, "提交分镜优化任务失败") from error
    except Exception as error:
        if session is not None and not writer_closed:
            _rollback(session)
        raise ChatTextGenerationError(503, "提交分镜优化任务失败") from error
    finally:
        if session is not None and not writer_closed:
            session.close()


def _stage_frame_optimize(
    session: Session,
    *,
    user_id: str,
    request: Mapping[str, object],
    context: AdmissionContext,
    preparation: AdmissionPreparationFacts,
    registry_capabilities: RegistryCapabilities,
    id_factory: Callable[[], str] | None,
    now: datetime,
) -> _StagedFrameOptimize:
    ids = _new_ids(id_factory)
    submission_id = ids("submission_id")
    task_id = ids("task_id")
    task_message_id = ids("task_message_id")
    identities = tuple(
        FrameOptimizeItemIdentity(
            item_id=ids("server_attempt_id"),
            unit_id=ids("unit_id"),
            step_id=ids("step_id"),
            payload_id=ids("prepared_payload_id"),
        )
        for _ in preparation.items
    )
    candidates = build_frame_optimize_snapshot_candidates(
        session,
        actor_id=user_id,
        request=request,
        context=context,
        preparation=preparation,
        registry_capabilities=registry_capabilities,
        submission_id=submission_id,
        task_id=task_id,
        identities=identities,
    )
    graph = build_operation_graph_plan(
        _OPERATION,
        graph_version=GRAPH_VERSION,
        items=tuple(
            GraphItemInput(
                item_id=candidate.identity.item_id,
                target_id=f"prepared-payload-v1/{len(candidate.identity.payload_id)}:{candidate.identity.payload_id}#target",
                task_id=task_id,
                unit_id=candidate.identity.unit_id,
                step_ids=(candidate.identity.step_id,),
                request_digest=candidate.request_digest,
                rule_digest=candidate.rule_digest,
                snapshot=candidate.snapshot,
            )
            for candidate in candidates
        ),
    )
    fee_plan = build_fee_phase_plan(_OPERATION, graph=graph)
    if (
        fee_plan.quoted_total != preparation.quoted_total
        or fee_plan.debit_timing != "after_generation_before_apply"
        or fee_plan.charge_phase != "pre_apply"
        or preparation.phase != "pre_apply"
        or preparation.reserved_amount != 0
    ):
        raise ValueError("frame.optimize must preserve its per-output pre-apply fee plan")
    protected = protect_frame_optimize_batch(
        candidates,
        owner_id=user_id,
        submission_id=submission_id,
        task_id=task_id,
        graph=graph,
        context=context,
        registry_capabilities=registry_capabilities,
    )
    request_data = _safe_request_data(
        request=request,
        preparation=preparation,
        candidates=candidates,
        protected=protected,
        submission_id=submission_id,
        task_id=task_id,
        graph_digest=graph.plan_digest,
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
            request_data=request_data,
            model_name=model_name,
            progress=0,
            progress_message="任务已提交，排队等待 Worker 认领...",
            external_task_id=None,
            external_provider=None,
            created_at=_aware_utc(now),
            updated_at=_aware_utc(now),
        )
    )
    session.add(
        TaskSubmissionRow(
            id=submission_id,
            user_id=user_id,
            operation=_OPERATION,
            idempotency_key=f"frame-optimize:{submission_id}",
            request_digest=context.fingerprint.ordered_nonsecret_payload_digest,
            task_ids=[task_id],
            task_quote_id=None,
            status="accepted",
            created_at=_aware_utc(now),
        )
    )

    candidate_by_id = {item.identity.item_id: item for item in candidates}
    payload_by_id = {item.candidate.identity.item_id: item for item in protected.items}
    for unit in graph.units:
        candidate = candidate_by_id[unit.item_id]
        session.add(
            BillingUnitRow(
                id=unit.unit_id,
                task_id=unit.task_id,
                item_key=unit.item_id,
                operation=_OPERATION,
                step_graph_version=GRAPH_VERSION,
                input_digest=candidate.request_digest,
                quoted_amount=unit.quoted_amount,
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
    for step in graph.steps:
        candidate = candidate_by_id[step.item_id]
        protected_item = payload_by_id[step.item_id]
        session.add(
            ExecutionStepRow(
                id=step.step_id,
                billing_unit_id=step.unit_id,
                step_key=step.step_key,
                dependencies=list(step.dependencies),
                input_digest=step.request_digest,
                input_snapshot={
                    "item_order": candidate.order,
                    "frame_index": candidate.frame_index,
                    "server_attempt_id": candidate.identity.item_id,
                    "prepared_payload_id": candidate.identity.payload_id,
                    "protected_ref": f"prepared-payload-v1/{len(candidate.identity.payload_id)}:{candidate.identity.payload_id}",
                    "snapshot_digest": protected_item.snapshot_digest,
                    "manifest_snapshot_digest": protected_item.manifest_snapshot_digest,
                },
                required=step.required,
                step_order=step.order,
                execution_status="prepared",
                created_at=_aware_utc(now),
                updated_at=_aware_utc(now),
            )
        )

    registry = build_credential_registry(registry_capabilities)
    for protected_item in protected.items:
        payload_id = protected_item.candidate.identity.payload_id
        result = registry.stage_prepared_payload(
            session,
            protected_item.payload,
            payload_id=payload_id,
        )
        if not isinstance(result, PreparedPayloadStaged) or result.prepared_payload_id != payload_id:
            raise ValueError("protected frame.optimize payload could not be staged")
    session.flush()
    response = {
        "task_id": task_id,
        "chapter_id": str(request["chapter_id"]),
        "frame_count": len(candidates),
    }
    return _StagedFrameOptimize(
        response=response,
        owner_id=user_id,
        task_id=task_id,
        task_message_id=task_message_id,
        task_model_name=model_name,
        submission_id=submission_id,
        request_data=request_data,
        graph_digest=graph.plan_digest,
        candidates=candidates,
        protected=protected,
    )


def _valid_preparation(
    context: AdmissionContext,
    preparation: AdmissionPreparationFacts,
) -> bool:
    frames = context.canonical_request.get("frames")
    return bool(
        preparation.operation == _OPERATION
        and preparation.fingerprint == context.fingerprint
        and preparation.phase == "pre_apply"
        and not preparation.executable
        and preparation.reserved_amount == 0
        and isinstance(frames, (tuple, list))
        and 1 <= len(preparation.items) == len(frames) <= 30
        and preparation.required_balance
        == max((item.quote_amount for item in preparation.items), default=0)
        and preparation.quoted_total == sum(item.quote_amount for item in preparation.items)
    )


def _safe_request_data(
    *,
    request: Mapping[str, object],
    preparation: AdmissionPreparationFacts,
    candidates: tuple[FrameOptimizeSnapshotCandidate, ...],
    protected: FrameOptimizeProtectedBatch,
    submission_id: str,
    task_id: str,
    graph_digest: str,
) -> str:
    model = preparation.items[0].model
    items = []
    for candidate, protected_item in zip(candidates, protected.items, strict=True):
        identity = candidate.identity
        items.append(
            {
                "order": candidate.order,
                "frame_index": candidate.frame_index,
                "item_id": identity.item_id,
                "unit_id": identity.unit_id,
                "step_id": identity.step_id,
                "prepared_payload_id": identity.payload_id,
                "protected_ref": f"prepared-payload-v1/{len(identity.payload_id)}:{identity.payload_id}",
                "target_ref": protected_item.target_ref,
                "model_ref": protected_item.model_ref,
                "grant_ref": protected_item.grant_ref,
                "request_digest": protected_item.candidate.request_digest,
                "rule_digest": protected_item.candidate.rule_digest,
                "snapshot_digest": protected_item.snapshot_digest,
                "manifest_snapshot_digest": protected_item.manifest_snapshot_digest,
                "quoted_amount": protected_item.candidate.snapshot.server_quote,
            }
        )
    return json.dumps(
        {
            "via": "registered_generation_graph",
            "mode": "frame_optimize",
            "operation": _OPERATION,
            "graph_version": GRAPH_VERSION,
            "submission_id": submission_id,
            "task_id": task_id,
            "request_digest": candidates[0].request_digest,
            "graph_digest": graph_digest,
            "manifest_snapshot_digest": protected.manifest_snapshot_digest,
            "chapter_id": str(request["chapter_id"]),
            "frame_count": len(items),
            "model_source": model.source.value,
            "model_id": model.catalog_id,
            "model_owner_id": candidates[0].scope.owner_or_system,
            "model_display_name": model.display_name,
            "enable_thinking": request.get("enable_thinking") is True,
            "concurrency": request.get("concurrency", 5),
            "items": items,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _confirm_committed_graph(
    session_factory: Callable[[], Session],
    staged: _StagedFrameOptimize,
) -> bool:
    """Confirm every staged graph row and protected ciphertext in a fresh session."""

    session: Session | None = None
    try:
        session = session_factory()
        task = session.execute(
            select(AITaskRow).where(AITaskRow.id == staged.task_id)
        ).scalar_one_or_none()
        submission = session.execute(
            select(TaskSubmissionRow).where(TaskSubmissionRow.id == staged.submission_id)
        ).scalar_one_or_none()
        units = session.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == staged.task_id)
        ).scalars().all()
        unit_ids = [candidate.identity.unit_id for candidate in staged.candidates]
        steps = session.execute(
            select(ExecutionStepRow).where(ExecutionStepRow.billing_unit_id.in_(unit_ids))
        ).scalars().all()
        payload_ids = [candidate.identity.payload_id for candidate in staged.candidates]
        payload_rows = session.execute(
            select(prepared_payloads).where(
                prepared_payloads.c.prepared_payload_id.in_(payload_ids)
            )
        ).mappings().all()
        if (
            task is None
            or task.user_id != staged.owner_id
            or task.type != "batch-optimize"
            or task.message_id != staged.task_message_id
            or task.model_name != staged.task_model_name
            or task.request_data != staged.request_data
            or submission is None
            or submission.user_id != staged.owner_id
            or submission.operation != _OPERATION
            or submission.idempotency_key != f"frame-optimize:{staged.submission_id}"
            or submission.request_digest != staged.candidates[0].request_digest
            or submission.task_ids != [staged.task_id]
            or len(units) != len(staged.candidates)
            or len(steps) != len(staged.candidates)
            or len(payload_rows) != len(staged.candidates)
        ):
            return False

        unit_by_id = {unit.id: unit for unit in units}
        step_by_id = {step.id: step for step in steps}
        payload_by_id = {row["prepared_payload_id"]: dict(row) for row in payload_rows}
        if len(unit_by_id) != len(staged.candidates) or len(step_by_id) != len(staged.candidates):
            return False
        for protected_item in staged.protected.items:
            candidate = protected_item.candidate
            identity = candidate.identity
            unit = unit_by_id.get(identity.unit_id)
            step = step_by_id.get(identity.step_id)
            payload = payload_by_id.get(identity.payload_id)
            if (
                unit is None
                or unit.task_id != staged.task_id
                or unit.item_key != identity.item_id
                or unit.operation != _OPERATION
                or unit.step_graph_version != GRAPH_VERSION
                or unit.input_digest != candidate.request_digest
                or unit.quoted_amount != candidate.snapshot.server_quote
                or step is None
                or step.billing_unit_id != identity.unit_id
                or step.step_key != "generate"
                or step.input_digest != candidate.request_digest
                or step.dependencies != []
                or step.required is not True
                or step.step_order != candidate.order
                or step.input_snapshot
                != {
                    "item_order": candidate.order,
                    "frame_index": candidate.frame_index,
                    "server_attempt_id": identity.item_id,
                    "prepared_payload_id": identity.payload_id,
                    "protected_ref": f"prepared-payload-v1/{len(identity.payload_id)}:{identity.payload_id}",
                    "snapshot_digest": protected_item.snapshot_digest,
                    "manifest_snapshot_digest": protected_item.manifest_snapshot_digest,
                }
                or payload is None
                or not _payload_row_matches(payload, protected_item)
            ):
                return False
        return True
    except Exception:
        return False
    finally:
        if session is not None:
            session.close()


def _payload_row_matches(row: Mapping[str, object], item: object) -> bool:
    payload = item.payload
    candidate = item.candidate
    scope = payload.scope
    expected = {
        "actor_id": payload.actor_id,
        "operation": payload.operation,
        "server_attempt_id": payload.server_attempt_id,
        "command_proof": payload.command_proof,
        "context_fingerprint": payload.context_fingerprint,
        "schema_version": payload.schema_version,
        "projector_version": payload.projector_version,
        "reference_policy_version": payload.reference_policy_version,
        "target_ref": payload.target_ref,
        "model_ref": payload.model_ref,
        "grant_ref": payload.grant_ref,
        "source": scope.source,
        "row_id": scope.row_id,
        "owner_or_system": scope.owner_or_system,
        "provider": scope.provider,
        "binding_id": payload.binding_id,
        "binding_version": payload.binding_version,
        "principal_id": payload.principal_id,
        "ciphertext": payload.ciphertext,
        "nonce": payload.nonce,
        "encryption_key_id": payload.encryption_key_id,
        "prepared_payload_id": candidate.identity.payload_id,
    }
    return all(row.get(key) == value for key, value in expected.items())


def _request_mapping(request: object) -> Mapping[str, object]:
    if isinstance(request, Mapping):
        return request
    model_dump = getattr(request, "model_dump", None)
    value = model_dump() if callable(model_dump) else None
    if not isinstance(value, Mapping):
        raise TypeError("optimize request must be a mapping")
    return value


def _new_ids(factory: Callable[[], str] | None) -> Callable[[str], str]:
    seen: set[str] = set()

    def create(label: str) -> str:
        del label
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


__all__ = ["admit_frame_optimize"]
