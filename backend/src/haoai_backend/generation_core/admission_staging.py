"""Stage first-generation media admissions in the caller's transaction.

A production producer is built from server-owned authentication, registry,
policy, version, and ID dependencies. It performs fresh admission and stages
the authenticated encrypted payload in the same Session used here. This
module persists the graph, legacy message envelope, and actual admission fee;
it never owns the Session lifecycle or exposes an execution capability.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from types import ModuleType
from typing import TypeAlias

from sqlalchemy import insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_catalog.admission_types import (
    AdmissionPreparationFacts,
    AdmissionRefusal,
    LockedTargetFacts,
    PreparedItemFacts,
)
from haoai_backend.generation_catalog.domain import ModelSelection
from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
    AdmissionFingerprint,
    FrozenExecutionSnapshot,
    TargetSnapshot,
)
from haoai_backend.generation_core.admission_receipts import (
    AdmissionConfirmed,
    AdmissionStaged,
    StagedTaskExpectation,
    verify_original_replay,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_core.fee_phases import build_fee_phase_plan
from haoai_backend.generation_core.graph_planning import (
    GraphItemInput,
    OperationGraphPlan,
    build_operation_graph_plan,
)
from haoai_backend.generation_core.unit_fees import (
    InsufficientAdmissionCredits,
    UnitFeeStagingResult,
    stage_unit_fees,
)
from haoai_backend.generation_credentials.domain import PreparedPayloadStaged
from haoai_backend.generation_runtime.registry import operation_policy


_PRODUCER_MODULE = "haoai_backend.generation_credentials.snapshot_producer"
_SUPPORTED_OPERATIONS = frozenset({"image.single", "video.single", "image.batch"})
_SUPPORTED_GRAPH_VERSIONS = {
    "image.single": "image-single-v1",
    "video.single": "video-single-v1",
    "image.batch": "image-batch-v1",
}
_UNSUPPORTED_ATTEMPT_GROUP_VERSION = "video-haoai-attempt-group-v1"
_PROJECTION_FIELDS = frozenset(
    {
        "operation",
        "submission_id",
        "mode",
        "model_id",
        "model_source",
        "model_owner_id",
        "prompt_id",
        "size",
        "chapter_id",
        "asset_type",
        "asset_id",
        "asset_name",
        "reference_images_version",
        "reference_images_count",
        "reference_images_digest",
        "prepared_payload_id",
        "target_ref",
        "model_ref",
        "grant_ref",
        "target_snapshot",
        "request_digest",
        "rule_digest",
        "protected_ref",
    }
)
_PROTECTED_PARTS = frozenset({"target", "model", "grant", "references"})


@dataclass(frozen=True, slots=True)
class MediaSnapshotProducerBinding:
    """A server-built producer and the matching server ID factory."""

    _producer: object
    _module: ModuleType
    _server_id_factory: Callable[[], str]


@dataclass(frozen=True, slots=True)
class AdmissionStageRefusal:
    """Safe staging result; callers must abort after any partial graph write."""

    status_code: int
    code: str
    message: str
    caller_must_abort: bool


AdmissionStageResult: TypeAlias = (
    AdmissionConfirmed | AdmissionStaged | AdmissionRefusal | AdmissionStageRefusal
)


def build_media_snapshot_producer(
    authentication_service: object,
    registry_capabilities: object,
    *,
    server_id_factory: Callable[[], str],
    policies: object,
    versions: object,
) -> MediaSnapshotProducerBinding:
    """Build the real producer or fail closed when its module is unavailable."""

    if not callable(server_id_factory):
        raise GenerationError("configuration_unavailable", "准入服务缺少服务器 ID 工厂")
    try:
        module = importlib.import_module(_PRODUCER_MODULE)
        builder = getattr(module, "build_media_snapshot_producer")
        producer_type = getattr(module, "MediaSnapshotProducer")
        if not callable(builder) or not isinstance(producer_type, type):
            raise AttributeError("producer module has no typed builder")
        producer = builder(
            authentication_service,
            registry_capabilities,
            server_id_factory=server_id_factory,
            policies=policies,
            versions=versions,
        )
        if type(producer) is not producer_type:
            raise TypeError("builder did not return the registered producer type")
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise GenerationError(
            "configuration_unavailable", "真实媒体准入 producer 尚未就绪"
        ) from exc
    except Exception as exc:
        raise GenerationError(
            "configuration_unavailable", "真实媒体准入 producer 尚未就绪"
        ) from exc
    return MediaSnapshotProducerBinding(
        _producer=producer,
        _module=module,
        _server_id_factory=server_id_factory,
    )


def prepare_and_stage(
    binding: MediaSnapshotProducerBinding,
    caller_session: Session,
    *,
    authorization: str,
    operation: str,
    raw_key: str,
    request: Mapping[str, object],
    now: datetime | None = None,
) -> AdmissionStageResult:
    """Run the producer then stage its complete graph and fee in the same TX.

    The caller must commit exactly once on ``AdmissionStaged``. Any result with
    ``caller_must_abort=True`` means the caller must roll back its transaction;
    this function never makes that lifecycle decision itself.
    """

    if type(binding) is not MediaSnapshotProducerBinding:
        return _stage_refusal("configuration_unavailable", "准入服务未绑定真实 producer", abort=False)
    if operation not in _SUPPORTED_OPERATIONS:
        return _stage_refusal("invalid_generation_request", "当前操作不属于媒体准入范围", abort=False)
    if not isinstance(authorization, str) or not authorization:
        return _stage_refusal("unauthenticated", "无法验证当前账号", abort=False)
    if not isinstance(raw_key, str) or not raw_key or not isinstance(request, Mapping):
        return _stage_refusal("invalid_generation_request", "媒体请求或幂等键无效", abort=False)
    if not caller_session.in_transaction():
        return _stage_refusal("configuration_unavailable", "准入必须使用调用方已开启的事务", abort=False)

    timestamp = now if now is not None else datetime.now(timezone.utc).replace(tzinfo=None)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is not None:
        return _stage_refusal("invalid_generation_request", "准入时间必须是 naive UTC", abort=False)

    try:
        with caller_session.no_autoflush:
            producer_result = binding._producer.prepare_and_stage(
                caller_session,
                authorization=authorization,
                operation=operation,
                raw_key=raw_key,
                request=request,
            )
            return _handle_producer_result(
                binding,
                caller_session,
                producer_result,
                operation=operation,
                raw_key=raw_key,
                now=timestamp,
            )
    except InsufficientAdmissionCredits:
        return _stage_refusal(
            "insufficient_credits", "积分不足，无法提交生成任务", abort=True
        )
    except GenerationError as exc:
        return _stage_refusal(exc.code, exc.safe_detail, abort=True)
    except SQLAlchemyError:
        return _stage_refusal("admission_stage_failed", "准入数据暂时无法完整写入", abort=True)
    except (TypeError, ValueError, KeyError, AttributeError):
        return _stage_refusal("admission_stage_failed", "准入快照无法安全绑定", abort=True)
    except Exception:
        return _stage_refusal("admission_stage_failed", "准入暂时无法安全完成", abort=True)


def _handle_producer_result(
    binding: MediaSnapshotProducerBinding,
    session: Session,
    result: object,
    *,
    operation: str,
    raw_key: str,
    now: datetime,
) -> AdmissionStageResult:
    module = binding._module
    replay_type = _required_type(module, "AdmissionReplay")
    refusal_type = _required_type(module, "AdmissionRefusal")
    snapshot_refusal_type = _required_type(module, "SnapshotRefusal")
    staged_type = _required_type(module, "SnapshotPayloadsStaged")

    if type(result) is replay_type:
        fingerprint = result.fingerprint
        if (
            fingerprint.operation != operation
            or not isinstance(fingerprint.actor_id, str)
            or not isinstance(fingerprint.ordered_nonsecret_payload_digest, str)
        ):
            return _stage_refusal("receipt_unverified", "原始准入身份无法核实", abort=False)
        return verify_original_replay(
            session,
            owner_id=fingerprint.actor_id,
            operation=operation,
            raw_key=raw_key,
            request_digest=fingerprint.ordered_nonsecret_payload_digest,
        )
    if type(result) is refusal_type:
        return AdmissionRefusal(
            status_code=result.status_code,
            code=result.code,
            message=result.message,
        )
    if type(result) is snapshot_refusal_type:
        return _stage_refusal(
            result.code,
            "媒体快照暂时无法安全完成",
            abort=result.caller_must_abort,
            status_code=result.status_code,
        )
    if type(result) is not staged_type:
        return _stage_refusal("configuration_unavailable", "真实准入 producer 返回类型无效", abort=True)
    if result.executable is not False:
        return _stage_refusal("configuration_unavailable", "准入暂存值不能作为执行授权", abort=True)

    staged = _build_graph_and_rows(
        binding,
        session,
        result,
        operation=operation,
        raw_key=raw_key,
        now=now,
    )
    return staged


def _build_graph_and_rows(
    binding: MediaSnapshotProducerBinding,
    session: Session,
    staged_result: object,
    *,
    operation: str,
    raw_key: str,
    now: datetime,
) -> AdmissionStageResult:
    actor_id = staged_result.actor_id
    parent_attempt_id = staged_result.parent_attempt_id
    preparation = staged_result.preparation
    if type(preparation) is not AdmissionPreparationFacts:
        return _stage_refusal("receipt_unverified", "准入 producer 的锁定 facts 类型无效", abort=True)
    fingerprint = preparation.fingerprint
    items = tuple(staged_result.items)
    if (
        staged_result.operation != operation
        or not _server_id(staged_result.parent_attempt_id)
        or not isinstance(staged_result.graph_version, str)
        or type(fingerprint) is not AdmissionFingerprint
        or preparation.operation != operation
        or fingerprint.operation != operation
        or staged_result.fingerprint != fingerprint.ordered_nonsecret_payload_digest
        or not _server_id(parent_attempt_id)
        or len(parent_attempt_id) > 36
        or not isinstance(actor_id, str)
        or not actor_id
        or actor_id != fingerprint.actor_id
        or len(items) == 0
        or len(items) > 64
        or len(preparation.items) != len(items)
        or preparation.executable is not False
        or preparation.reserved_amount != 0
        or type(preparation.quoted_total) is not int
        or not callable(staged_result.staging_session_ref)
        or staged_result.staging_session_ref() is not session
    ):
        return _stage_refusal("receipt_unverified", "准入 producer 的锁定事实不完整", abort=True)
    if operation != "image.batch" and len(items) != 1:
        return _stage_refusal("invalid_generation_request", "单项操作必须只有一个任务", abort=True)

    policy = operation_policy(operation)
    if policy is None or policy.task_type is None or policy.graph_version is None:
        return _stage_refusal("configuration_unavailable", "当前操作没有已冻结图策略", abort=True)
    graph_version = staged_result.graph_version
    if graph_version == _UNSUPPORTED_ATTEMPT_GROUP_VERSION:
        return _stage_refusal(
            "configuration_unavailable",
            "当前准入入口不支持 HaoAI 三步 attempt group 图",
            abort=True,
        )
    if (
        graph_version != _SUPPORTED_GRAPH_VERSIONS[operation]
        or policy.graph_version != graph_version
    ):
        return _stage_refusal("configuration_unavailable", "producer 图版本不属于当前支持范围", abort=True)

    seen_attempts: set[str] = set()
    seen_payload_ids: set[str] = set()
    graph_inputs: list[GraphItemInput] = []
    item_rows: list[dict[str, object]] = []
    unit_rows: list[dict[str, object]] = []
    step_rows: list[dict[str, object]] = []
    message_rows: list[dict[str, object]] = []
    expectations: list[StagedTaskExpectation] = []
    all_generated_ids = {parent_attempt_id}
    total_projection = 0

    for index, item in enumerate(items):
        if type(item.index) is not int or item.index != index:
            return _stage_refusal("invalid_generation_request", "媒体任务顺序无法核实", abort=True)
        if item.server_attempt_id in seen_attempts or not _server_id(item.server_attempt_id, maximum=128):
            return _stage_refusal("invalid_generation_request", "子 attempt 标识无效", abort=True)
        seen_attempts.add(item.server_attempt_id)
        if item.server_attempt_id in all_generated_ids:
            return _stage_refusal("invalid_generation_request", "子 attempt 与父 submission 标识冲突", abort=True)
        all_generated_ids.add(item.server_attempt_id)
        if (
            type(item.payload_staged) is not PreparedPayloadStaged
            or item.payload_staged.prepared_payload_id != item.prepared_payload_id
            or type(item.payload_staged.reused) is not bool
            or not _opaque_payload_id(item.prepared_payload_id)
        ):
            return _stage_refusal("configuration_unavailable", "加密 payload 未在当前事务完成暂存", abort=True)
        if item.prepared_payload_id in seen_payload_ids:
            return _stage_refusal("receipt_unverified", "同一加密 payload 不能绑定多个任务项目", abort=True)
        seen_payload_ids.add(item.prepared_payload_id)
        snapshot = item.snapshot
        if type(snapshot) is not FrozenExecutionSnapshot:
            return _stage_refusal("configuration_unavailable", "producer 缺少完整冻结 execution snapshot", abort=True)
        if snapshot.grant_snapshot.actor_id != actor_id or snapshot.model_category != (
            "video" if operation == "video.single" else "image"
        ):
            return _stage_refusal("membership_or_resource_forbidden", "冻结快照与准入账号或媒体类型不一致", abort=True)
        if type(item.protected_ref) is not str or not item.protected_ref:
            return _stage_refusal("configuration_unavailable", "加密 payload opaque ref 缺失", abort=True)

        projection = _projection_values(item.request_data_projection, binding._module)
        if isinstance(projection, AdmissionStageRefusal):
            return projection
        if not _preparation_item_matches(
            preparation.items[index],
            index=index,
            operation=operation,
            projection=projection,
            snapshot=snapshot,
        ):
            return _stage_refusal(
                "receipt_unverified",
                "A2 锁定项目 facts 与媒体快照投影不一致",
                abort=True,
            )
        if (
            projection["operation"] != operation
            or projection["submission_id"] != parent_attempt_id
            or projection["prepared_payload_id"] != item.prepared_payload_id
            or projection["protected_ref"] != item.protected_ref
            or projection["model_id"] != snapshot.model_ref.model_id
            or not _model_owner_matches(
                projection["model_source"],
                projection["model_owner_id"],
                snapshot.model_ref.owner_id,
                actor_id,
            )
            or projection["prompt_id"] != snapshot.prompt_ref
            or projection["request_digest"] != fingerprint.ordered_nonsecret_payload_digest
            or not isinstance(snapshot.credential_binding_ref, str)
            or not snapshot.credential_binding_ref
            or not _valid_projection_scalars(projection)
        ):
            return _stage_refusal("receipt_unverified", "原始请求投影与冻结 payload 绑定不一致", abort=True)
        references = _payload_references(item.prepared_payload_id)
        if item.protected_ref != references["base"]:
            return _stage_refusal("receipt_unverified", "opaque payload ref 与 prepared payload 不一致", abort=True)
        if snapshot.credential_binding_ref in references.values():
            return _stage_refusal("receipt_unverified", "credential binding 不能充当 payload 子引用", abort=True)
        for part, key in (("target", "target_ref"), ("model", "model_ref"), ("grant", "grant_ref")):
            if projection[key] != references[part]:
                return _stage_refusal("receipt_unverified", "payload 子引用与加密 payload 不一致", abort=True)
        if snapshot.reference_snapshot_ref != references["references"]:
            return _stage_refusal("receipt_unverified", "引用快照引用与加密 payload 不一致", abort=True)
        if projection["target_snapshot"] != snapshot.target_snapshot:
            return _stage_refusal("receipt_unverified", "目标投影与冻结目标不一致", abort=True)
        if not _batch_target_matches(operation, projection, snapshot.target_snapshot):
            return _stage_refusal("membership_or_resource_forbidden", "B1 目标无法精确绑定 A2 资产事实", abort=True)
        if type(projection["reference_images_count"]) is not int or projection["reference_images_count"] < 0:
            return _stage_refusal("invalid_generation_request", "原始引用数量无效", abort=True)
        if (
            type(projection["reference_images_version"]) is not int
            or projection["reference_images_version"] != 1
            or not _is_digest(projection["reference_images_digest"])
            or not _is_digest(projection["request_digest"])
            or not _is_digest(projection["rule_digest"])
        ):
            return _stage_refusal("invalid_generation_request", "冻结请求摘要无效", abort=True)
        quote = snapshot.server_quote
        if type(quote) is not int or quote < 0:
            return _stage_refusal("invalid_generation_request", "服务器报价不是有效整数", abort=True)
        model_display_name = preparation.items[index].model.display_name
        if not _safe_model_display_name(model_display_name):
            return _stage_refusal("receipt_unverified", "原始 UI 模型名称无法安全保存", abort=True)
        total_projection += quote

        legacy_projection = _legacy_projection_values(
            item.legacy_message_projection, binding._module, operation=operation
        )
        if isinstance(legacy_projection, AdmissionStageRefusal):
            return legacy_projection
        task_id = _new_id(binding._server_id_factory, all_generated_ids)
        unit_id = _new_id(binding._server_id_factory, all_generated_ids)
        step_id = _new_id(binding._server_id_factory, all_generated_ids)
        user_message_id: str | None = None
        assistant_message_id: str | None = None
        if operation == "image.batch":
            user_message_id = _new_id(binding._server_id_factory, all_generated_ids)
            assistant_message_id = _new_id(binding._server_id_factory, all_generated_ids)
            if (
                legacy_projection["chapter_id"] != projection["chapter_id"]
                or legacy_projection["asset_type"] != projection["asset_type"]
                or legacy_projection["asset_id"] != projection["asset_id"]
                or legacy_projection["asset_name"] != projection["asset_name"]
                or legacy_projection["model_name"] != preparation.items[index].model.display_name
                or not _valid_legacy_context(legacy_projection["user_content"], projection)
                or legacy_projection["assistant_content"] != "正在生成图片..."
            ):
                return _stage_refusal("receipt_unverified", "原始批量消息与锁定资产 facts 不一致", abort=True)

        request_digest = projection["request_digest"]
        rule_digest = projection["rule_digest"]
        target_id = _target_id(snapshot.target_snapshot, item.server_attempt_id)
        graph_inputs.append(
            GraphItemInput(
                item_id=item.server_attempt_id,
                target_id=target_id,
                task_id=task_id,
                unit_id=unit_id,
                step_ids=(step_id,),
                request_digest=request_digest,
                rule_digest=rule_digest,
                snapshot=snapshot,
                message_id=assistant_message_id,
            )
        )
        safe_request_data = _safe_request_data(
            projection,
            operation=operation,
            parent_attempt_id=parent_attempt_id,
            server_attempt_id=item.server_attempt_id,
            item_id=item.server_attempt_id,
            unit_id=unit_id,
            unit_key=item.server_attempt_id,
            model_display_name=model_display_name,
            graph_version=graph_version,
            snapshot_digest=None,
            context_message_id=user_message_id,
            context_message_sha256=(
                _sha256(legacy_projection["user_content"]) if user_message_id else None
            ),
        )
        item_rows.append(
            {
                "id": task_id,
                "user_id": actor_id,
                "type": policy.task_type,
                "status": "queued",
                "claimed_by": None,
                "lease_until": None,
                "execution_generation": 0,
                "claim_token": None,
                "recovery_status": "ready",
                "billing_status": "unbilled",
                "user_cancelled_at": None,
                "cancellation_reason": None,
                "message_id": assistant_message_id or "",
                "credit_cost": quote,
                "result": None,
                "request_data": "",
                "model_name": model_display_name,
                "progress": 0,
                "progress_message": None,
                "external_task_id": None,
                "external_provider": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        unit_rows.append(
            {
                "id": unit_id,
                "task_id": task_id,
                "item_key": item.server_attempt_id,
                "operation": operation,
                "step_graph_version": policy.graph_version,
                "input_digest": request_digest,
                "quoted_amount": quote,
                "debited_amount": 0,
                "refunded_amount": 0,
                "billing_status": "unbilled",
                "result_status": "pending",
                "first_intent_at": None,
                "deadline_at": None,
                "success_confirmed_at": None,
                "user_cancelled_at": None,
                "cancellation_reason": None,
                "quote_id": None,
                "quote_unit_key": None,
                "candidate_digest": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        step_rows.append(
            {
                "id": step_id,
                "billing_unit_id": unit_id,
                "step_key": "generate",
                "dependencies": [],
                "input_digest": request_digest,
                "input_snapshot": {
                    "server_attempt_id": item.server_attempt_id,
                    "prepared_payload_id": item.prepared_payload_id,
                    "protected_ref": item.protected_ref,
                    "snapshot_digest": "",
                },
                "required": True,
                "step_order": 0,
                "execution_status": "prepared",
                "created_at": now,
                "updated_at": now,
            }
        )
        if user_message_id is not None and assistant_message_id is not None:
            message_rows.extend(
                (
                    {
                        "id": user_message_id,
                        "chapter_id": legacy_projection["chapter_id"],
                        "frame_index": None,
                        "asset_type": legacy_projection["asset_type"],
                        "asset_id": legacy_projection["asset_id"],
                        "chat_mode": "image",
                        "role": "user",
                        "content": legacy_projection["user_content"],
                        "model_name": None,
                        "created_at": now,
                    },
                    {
                        "id": assistant_message_id,
                        "chapter_id": legacy_projection["chapter_id"],
                        "frame_index": None,
                        "asset_type": legacy_projection["asset_type"],
                        "asset_id": legacy_projection["asset_id"],
                        "chat_mode": "image",
                        "role": "assistant",
                        "content": legacy_projection["assistant_content"],
                        "model_name": legacy_projection["model_name"],
                        "created_at": now,
                    },
                )
            )
        message_ids = tuple(
            value for value in (user_message_id, assistant_message_id) if value is not None
        )
        item_rows[-1]["request_data"] = safe_request_data
        expectations.append(
            StagedTaskExpectation(
                task_id=task_id,
                unit_id=unit_id,
                step_ids=(step_id,),
                server_attempt_id=item.server_attempt_id,
                prepared_payload_id=item.prepared_payload_id,
                protected_ref=item.protected_ref,
                quoted_amount=quote,
                task_type=policy.task_type,
                model_display_name=model_display_name,
                request_data=safe_request_data,
                message_ids=message_ids,
            )
        )

    if total_projection != preparation.quoted_total:
        return _stage_refusal("receipt_unverified", "逐项报价与准入总价不一致", abort=True)
    if preparation.required_balance != total_projection:
        return _stage_refusal("receipt_unverified", "准入所需余额与逐项报价不一致", abort=True)
    try:
        graph = build_operation_graph_plan(
            operation,
            graph_version=graph_version,
            items=graph_inputs,
        )
        fee_plan = build_fee_phase_plan(operation, graph=graph)
    except GenerationError as exc:
        return _stage_refusal(exc.code, exc.safe_detail, abort=True)
    if fee_plan.quoted_total != total_projection:
        return _stage_refusal("receipt_unverified", "G1费用图与producer报价不一致", abort=True)

    updated_expectations: list[StagedTaskExpectation] = []
    for index, (row, expectation, graph_item) in enumerate(
        zip(item_rows, expectations, graph.items, strict=True)
    ):
        try:
            request_data = json.loads(row["request_data"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return _stage_refusal("invalid_generation_request", "安全任务快照无法编码", abort=True)
        request_data["snapshot_digest"] = graph_item.snapshot_digest
        row["request_data"] = _encode_json(request_data)
        updated_expectations.append(StagedTaskExpectation(
            task_id=expectation.task_id,
            unit_id=expectation.unit_id,
            step_ids=expectation.step_ids,
            server_attempt_id=expectation.server_attempt_id,
            prepared_payload_id=expectation.prepared_payload_id,
            protected_ref=expectation.protected_ref,
            quoted_amount=expectation.quoted_amount,
            task_type=expectation.task_type,
            model_display_name=expectation.model_display_name,
            request_data=row["request_data"],
            message_ids=expectation.message_ids,
        ))
        step_rows[index]["input_snapshot"]["snapshot_digest"] = graph_item.snapshot_digest
    expectations = updated_expectations

    with session.no_autoflush:
        try:
            existing_keys = session.execute(
                select(BillingUnitRow.__table__.c.id).where(
                    BillingUnitRow.__table__.c.id.in_(
                        tuple(unit.unit_id for unit in graph.units)
                    )
                )
            ).scalars().all()
            if existing_keys:
                return _stage_refusal("inflight_conflict", "计费单元标识已存在", abort=True)
            session.execute(
                insert(TaskSubmissionRow.__table__),
                [
                    {
                        "id": parent_attempt_id,
                        "user_id": actor_id,
                        "operation": operation,
                        "idempotency_key": raw_key,
                        "request_digest": fingerprint.ordered_nonsecret_payload_digest,
                        "task_ids": [task.task_id for task in graph.tasks],
                        "task_quote_id": None,
                        "status": "accepted",
                        "created_at": now,
                    }
                ],
            )
            session.execute(insert(AITaskRow.__table__), item_rows)
            session.execute(insert(BillingUnitRow.__table__), unit_rows)
            session.execute(insert(ExecutionStepRow.__table__), step_rows)
            if message_rows:
                session.execute(insert(chat_messages), message_rows)
            fee_result = stage_unit_fees(
                session,
                actor_id=actor_id,
                graph=graph,
                id_factory=binding._server_id_factory,
                now=now,
            )
        except InsufficientAdmissionCredits:
            return _stage_refusal(
                "insufficient_credits", "积分不足，无法提交生成任务", abort=True
            )
        except GenerationError as exc:
            return _stage_refusal(exc.code, exc.safe_detail, abort=True)
        except SQLAlchemyError:
            return _stage_refusal("admission_stage_failed", "准入数据暂时无法完整写入", abort=True)

    final_expectations = _with_financial_expectations(expectations, fee_result)
    return AdmissionStaged(
        owner_id=actor_id,
        operation=operation,
        submission_id=parent_attempt_id,
        parent_attempt_id=parent_attempt_id,
        graph_version=graph_version,
        raw_key=raw_key,
        request_digest=fingerprint.ordered_nonsecret_payload_digest,
        ordered_tasks=final_expectations,
        producer_binding=binding,
        producer_staged_result=staged_result,
        staging_session_identity=id(session),
    )


def _projection_values(projection: object, module: ModuleType) -> dict[str, object] | AdmissionStageRefusal:
    expected_type = _required_type(module, "RequestDataProjection")
    if type(projection) is not expected_type or not is_dataclass(projection):
        return _stage_refusal("configuration_unavailable", "producer 缺少 typed safe request projection", abort=True)
    values = {field.name: getattr(projection, field.name) for field in fields(projection)}
    if set(values) != _PROJECTION_FIELDS:
        return _stage_refusal("configuration_unavailable", "producer safe request projection 字段未冻结", abort=True)
    if not _json_safe_projection(values):
        return _stage_refusal("configuration_unavailable", "producer safe request projection 不可安全序列化", abort=True)
    return values


def _safe_model_display_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= 255
        and all(ord(character) >= 32 for character in value)
    )


def _valid_projection_scalars(values: dict[str, object]) -> bool:
    required_text = (
        "operation",
        "submission_id",
        "mode",
        "model_id",
        "model_source",
        "prepared_payload_id",
        "protected_ref",
        "target_ref",
        "model_ref",
        "grant_ref",
    )
    if any(not isinstance(values.get(name), str) or not values[name] for name in required_text):
        return False
    model_owner_id = values.get("model_owner_id")
    if model_owner_id is not None and not _server_id(model_owner_id, maximum=36):
        return False
    for name in ("prompt_id", "chapter_id", "asset_type", "asset_id", "asset_name"):
        value = values.get(name)
        if value is not None and not isinstance(value, str):
            return False
    size = values.get("size")
    if size is not None and type(size) not in {str, int, float}:
        return False
    if type(size) is float and not _json_safe_projection(size):
        return False
    return (
        type(values.get("reference_images_version")) is int
        and values["reference_images_version"] == 1
        and type(values.get("target_snapshot")) is TargetSnapshot
    )


def _model_owner_matches(
    model_source: object,
    projected_owner_id: object,
    snapshot_owner_id: object,
    actor_id: str,
) -> bool:
    if model_source == "system":
        return projected_owner_id is None and snapshot_owner_id == "system"
    if model_source == "user":
        return projected_owner_id == actor_id and snapshot_owner_id == actor_id
    return False


def _preparation_item_matches(
    facts: object,
    *,
    index: int,
    operation: str,
    projection: dict[str, object],
    snapshot: FrozenExecutionSnapshot,
) -> bool:
    if (
        type(facts) is not PreparedItemFacts
        or type(facts.model) is not ModelSelection
        or type(facts.target) is not LockedTargetFacts
        or type(facts.index) is not int
        or type(facts.quote_amount) is not int
        or facts.index != index
    ):
        return False

    model = facts.model
    target = facts.target
    target_snapshot = snapshot.target_snapshot
    effective_options = facts.model.quote.effective_parameters
    snapshot_model_name = snapshot.model_name
    # Provider defaults come from the producer's signed snapshot, never a UI label.
    wire_model_matches = (
        isinstance(snapshot_model_name, str)
        and bool(snapshot_model_name)
        and (
            model.wire_model is None
            or snapshot_model_name == model.wire_model
        )
    )
    if target.kind == "standalone":
        expected_target_kind = "none"
    elif operation == "image.batch" and target.kind == "asset_batch":
        expected_target_kind = "asset_message"
    else:
        expected_target_kind = target.kind
    prompt_id = facts.prompt.prompt_id if facts.prompt is not None else None
    return bool(
        model.catalog_id == projection["model_id"]
        and model.catalog_id == snapshot.model_ref.model_id
        and model.source.value == projection["model_source"]
        and model.category.value == snapshot.model_category
        and wire_model_matches
        and model.quote.amount == facts.quote_amount == snapshot.server_quote
        and model.quote.effective_parameters == facts.effective_parameters
        and all(
            key in facts.effective_parameters
            and facts.effective_parameters[key] == value
            and key in snapshot.model_params
            and snapshot.model_params[key] == value
            for key, value in effective_options.items()
        )
        and projection["size"] == effective_options.get("size")
        and prompt_id == projection["prompt_id"] == snapshot.prompt_ref
        and (facts.prompt is None or facts.prompt.operation == operation)
        and target_snapshot.kind == expected_target_kind
        and target_snapshot.series_id == target.series_id
        and target_snapshot.chapter_id == target.chapter_id
        and target_snapshot.asset_type == target.asset_type
        and (target_snapshot.asset_id or target_snapshot.stable_storyboard_asset_id)
        == target.asset_id
        and target_snapshot.compat_frame_index == target.frame_index
    )


def _legacy_projection_values(
    projection: object, module: ModuleType, *, operation: str
) -> dict[str, object] | AdmissionStageRefusal:
    expected_type = _required_type(module, "LegacyMessageProjection")
    if operation != "image.batch":
        if projection is not None:
            return _stage_refusal("configuration_unavailable", "单项操作不应携带批量消息投影", abort=True)
        return {
            "user_content": None,
            "assistant_content": None,
            "assistant_wire_content": None,
            "model_name": None,
            "chapter_id": None,
            "asset_type": None,
            "asset_id": None,
            "asset_name": None,
        }
    if type(projection) is not expected_type or not is_dataclass(projection):
        return _stage_refusal("configuration_unavailable", "producer 缺少 typed legacy message projection", abort=True)
    values = {field.name: getattr(projection, field.name) for field in fields(projection)}
    expected_fields = {
        "user_content",
        "assistant_content",
        "assistant_wire_content",
        "model_name",
        "chapter_id",
        "asset_type",
        "asset_id",
        "asset_name",
    }
    if set(values) != expected_fields or any(
        not isinstance(values[name], str) or not values[name]
        for name in (
            "user_content",
            "assistant_content",
            "model_name",
            "chapter_id",
            "asset_type",
            "asset_id",
            "asset_name",
        )
    ):
        return _stage_refusal("configuration_unavailable", "legacy message projection 字段无效", abort=True)
    if values["assistant_wire_content"] is not None and not isinstance(values["assistant_wire_content"], str):
        return _stage_refusal("configuration_unavailable", "legacy assistant wire content 无效", abort=True)
    return values


def _safe_request_data(
    projection: dict[str, object],
    *,
    operation: str,
    parent_attempt_id: str,
    server_attempt_id: str,
    item_id: str,
    unit_id: str,
    unit_key: str,
    model_display_name: str,
    graph_version: str,
    snapshot_digest: str | None,
    context_message_id: str | None,
    context_message_sha256: str | None,
) -> str:
    safe = {
        "via": "worker",
        "operation": operation,
        "submission_id": parent_attempt_id,
        "parent_attempt_id": parent_attempt_id,
        "server_attempt_id": server_attempt_id,
        "prepared_payload_id": projection["prepared_payload_id"],
        "protected_ref": projection["protected_ref"],
        "billing_unit_id": unit_id,
        "item_id": item_id,
        "unit_key": unit_key,
        "step_graph_version": graph_version,
        "request_digest": projection["request_digest"],
        "rule_digest": projection["rule_digest"],
        "snapshot_digest": snapshot_digest,
        "mode": projection["mode"],
        "model_id": projection["model_id"],
        "model_source": projection["model_source"],
        "model_owner_id": projection["model_owner_id"],
        "model_display_name": model_display_name,
        "prompt_id": projection["prompt_id"],
        "size": projection["size"],
        "chapter_id": projection["chapter_id"],
        "asset_type": projection["asset_type"],
        "asset_id": projection["asset_id"],
        "asset_name": projection["asset_name"],
        "reference_images_version": projection["reference_images_version"],
        "reference_images_count": projection["reference_images_count"],
        "reference_images_digest": projection["reference_images_digest"],
        "target_ref": projection["target_ref"],
        "model_ref": projection["model_ref"],
        "grant_ref": projection["grant_ref"],
        "reference_ref": _payload_references(projection["prepared_payload_id"])["references"],
        "target_snapshot": _safe_target_snapshot(projection["target_snapshot"]),
    }
    if context_message_id is not None:
        safe["context_message_id"] = context_message_id
        safe["context_message_sha256"] = context_message_sha256
    else:
        for key in ("chapter_id", "asset_type", "asset_id", "asset_name"):
            safe.pop(key, None)
    return _encode_json(safe)


def _safe_target_snapshot(value: object) -> dict[str, object]:
    if type(value) is not TargetSnapshot:
        raise ValueError("target projection must be the frozen target type")
    fields_to_keep = (
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
        "same_series_target_digest",
    )
    return {name: getattr(value, name) for name in fields_to_keep}


def _payload_references(payload_id: str) -> dict[str, str]:
    base = f"prepared-payload-v1/{len(payload_id)}:{payload_id}"
    return {"base": base, **{part: f"{base}#{part}" for part in _PROTECTED_PARTS}}


def _opaque_payload_id(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 255


def _batch_target_matches(
    operation: str, projection: dict[str, object], target: TargetSnapshot
) -> bool:
    if operation != "image.batch":
        return True
    return (
        target.kind == "asset_message"
        and target.chapter_id == projection["chapter_id"]
        and target.asset_type == projection["asset_type"]
        and (target.asset_id or target.stable_storyboard_asset_id) == projection["asset_id"]
    )


def _target_id(target: TargetSnapshot, attempt_id: str) -> str:
    return target.asset_id or target.stable_storyboard_asset_id or target.chapter_id or attempt_id


def _valid_legacy_context(content: str, projection: dict[str, object]) -> bool:
    prefix = "__CTX__:"
    if not content.startswith(prefix):
        return False
    try:
        encoded_context, raw_description = content[len(prefix) :].split("\n", 1)
        context = json.loads(encoded_context)
    except (ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(context, dict)
        and set(context) == {"modelId", "promptId", "size", "refImagesCount", "refImagesDigest"}
        and context["modelId"] == projection["model_id"]
        and context["promptId"] == (projection["prompt_id"] or "")
        and context["size"] == projection["size"]
        and type(context["refImagesCount"]) is int
        and context["refImagesCount"] == projection["reference_images_count"]
        and context["refImagesDigest"] == projection["reference_images_digest"]
        and raw_description != ""
    )


def _new_id(factory: Callable[[], str], seen: set[str]) -> str:
    value = factory()
    if not _server_id(value) or value in seen:
        raise GenerationError("invalid_generation_request", "服务器 ID 工厂返回了无效或重复标识")
    seen.add(value)
    return value


def _server_id(value: object, *, maximum: int = 36) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum
        and all(ord(character) >= 32 for character in value)
    )


def _required_type(module: ModuleType, name: str) -> type:
    value = getattr(module, name, None)
    if not isinstance(value, type):
        raise GenerationError("configuration_unavailable", "producer typed interface 尚未冻结")
    return value


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _json_safe_projection(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return value == value and abs(value) != float("inf")
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _json_safe_projection(item) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return all(_json_safe_projection(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return all(_json_safe_projection(getattr(value, field.name)) for field in fields(value))
    return False


def _encode_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if isinstance(value, TargetSnapshot):
        return _safe_target_snapshot(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, tuple):
        return list(value)
    raise TypeError("projection contains a non-JSON value")


def _with_financial_expectations(
    expectations: list[StagedTaskExpectation], fee_result: UnitFeeStagingResult
) -> tuple[StagedTaskExpectation, ...]:
    if len(expectations) != len(fee_result.units):
        raise GenerationError("batch_internal_error", "费用结果没有覆盖全部任务")
    return tuple(
        StagedTaskExpectation(
            task_id=expected.task_id,
            unit_id=expected.unit_id,
            step_ids=expected.step_ids,
            server_attempt_id=expected.server_attempt_id,
            prepared_payload_id=expected.prepared_payload_id,
            protected_ref=expected.protected_ref,
            quoted_amount=expected.quoted_amount,
            task_type=expected.task_type,
            model_display_name=expected.model_display_name,
            request_data=expected.request_data,
            message_ids=expected.message_ids,
            ledger_id=fee.ledger_id,
            business_key=fee.business_key,
            balance_after=fee.balance_after,
        )
        for expected, fee in zip(expectations, fee_result.units, strict=True)
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stage_refusal(
    code: str,
    message: str,
    *,
    abort: bool,
    status_code: int | None = None,
) -> AdmissionStageRefusal:
    status = status_code or (409 if code in {"invalid_generation_request", "inflight_conflict"} else 503)
    return AdmissionStageRefusal(
        status_code=status,
        code=code,
        message=message,
        caller_must_abort=abort,
    )


__all__ = (
    "AdmissionStageRefusal",
    "AdmissionStageResult",
    "MediaSnapshotProducerBinding",
    "build_media_snapshot_producer",
    "prepare_and_stage",
)
