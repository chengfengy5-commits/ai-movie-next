"""Durable receipts for staged media admissions and unknown commits.

The staged value records expectations from one caller transaction. Only a
separate durable read can turn that value into a confirmed wire receipt. This
module never commits, rolls back, closes, or starts a transaction for callers.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import ModuleType
from typing import Literal, TypeAlias

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_catalog.admission_types import (
    AdmissionPreparationFacts,
    AdmissionRefusal,
)
from haoai_backend.generation_contracts.types import AdmissionFingerprint
from haoai_backend.generation_contracts.types import (
    BatchImageReceipt,
    BatchImageTaskReceipt,
    SingleAdmissionReceipt,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
AdmissionWireReceipt: TypeAlias = SingleAdmissionReceipt | BatchImageReceipt
_PRODUCER_MODULE = "haoai_backend.generation_credentials.snapshot_producer"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_GRAPH_VERSIONS = {
    "image.single": "image-single-v1",
    "video.single": "video-single-v1",
    "image.batch": "image-batch-v1",
}
_TASK_TYPES_BY_OPERATION = {
    "image.single": "image-single",
    "video.single": "video-single",
    "image.batch": "batch-image",
}
_REFERENCE_IMAGES_VERSION = 1
_TASK_STATES = frozenset(
    {"queued", "processing", "cancelling", "reconciling", "completed", "failed"}
)
_UNIT_RESULT_STATES = frozenset({"pending", "partial", "succeeded", "failed", "unknown"})
_UNIT_BILLING_STATES = frozenset({"unbilled", "charged", "refunded", "no_charge"})
_TASK_BILLING_STATES = frozenset(
    {"legacy_unreconciled", "unbilled", "charged", "partially_refunded", "refunded", "no_charge"}
)
_STEP_EXECUTION_STATES = frozenset(
    {
        "prepared",
        "submitting",
        "submitted",
        "result_observed",
        "applied",
        "failed",
        "reconciling",
        "deterministic_empty",
    }
)
_SAFE_REQUEST_DATA_KEYS = frozenset(
    {
        "via",
        "mode",
        "operation",
        "submission_id",
        "billing_unit_id",
        "parent_attempt_id",
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "item_id",
        "unit_key",
        "step_graph_version",
        "request_digest",
        "rule_digest",
        "snapshot_digest",
        "chapter_id",
        "asset_type",
        "asset_id",
        "asset_name",
        "context_message_id",
        "context_message_sha256",
        "model_source",
        "model_id",
        "model_owner_id",
        "model_display_name",
        "prompt_id",
        "size",
        "reference_images_version",
        "reference_images_count",
        "reference_images_digest",
        "target_ref",
        "model_ref",
        "grant_ref",
        "reference_ref",
        "target_snapshot",
    }
)
_REQUIRED_BASE_REQUEST_DATA_KEYS = frozenset(
    {
        "via",
        "mode",
        "operation",
        "submission_id",
        "billing_unit_id",
        "parent_attempt_id",
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "item_id",
        "unit_key",
        "step_graph_version",
        "request_digest",
        "rule_digest",
        "snapshot_digest",
        "model_source",
        "model_id",
        "model_owner_id",
        "model_display_name",
        "prompt_id",
        "size",
        "reference_images_version",
        "reference_images_count",
        "reference_images_digest",
        "target_ref",
        "model_ref",
        "grant_ref",
        "reference_ref",
        "target_snapshot",
    }
)
_BATCH_REQUEST_DATA_KEYS = frozenset(
    {
        "chapter_id",
        "asset_type",
        "asset_id",
        "asset_name",
        "context_message_id",
        "context_message_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class StagedTaskExpectation:
    """Expected persisted bindings for one graph item and its messages."""

    task_id: str
    unit_id: str
    step_ids: tuple[str, ...]
    server_attempt_id: str
    prepared_payload_id: str
    protected_ref: str
    quoted_amount: int
    task_type: str
    model_display_name: str
    request_data: str = field(repr=False)
    message_ids: tuple[str, ...] = ()
    ledger_id: str | None = None
    business_key: str | None = None
    balance_after: int | None = None


@dataclass(frozen=True, slots=True)
class AdmissionStaged:
    """Rows staged in one caller transaction; this is not a receipt."""

    owner_id: str
    operation: str
    submission_id: str
    parent_attempt_id: str
    graph_version: str
    raw_key: str = field(repr=False)
    request_digest: str = field(repr=False)
    ordered_tasks: tuple[StagedTaskExpectation, ...] = field(repr=False)
    producer_binding: object = field(repr=False)
    producer_staged_result: object = field(repr=False)
    staging_session_identity: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class AdmissionCommitUnknown:
    """Original admission identity retained while a commit outcome is unknown."""

    staged: AdmissionStaged = field(repr=False)


@dataclass(frozen=True, slots=True)
class AdmissionConfirmed:
    """Wire receipt reconstructed from a complete durable database read."""

    submission_id: str
    operation: str
    receipt: AdmissionWireReceipt
    verified_at: datetime
    source: Literal["durable_lookup", "original_replay"]


AdmissionReadResult = AdmissionConfirmed | AdmissionRefusal | AdmissionCommitUnknown


def retain_unknown_commit(staged: AdmissionStaged) -> AdmissionCommitUnknown:
    """Keep the original key, parent attempt, and IDs after a commit error."""

    return AdmissionCommitUnknown(staged=staged)


def verify_original_replay(
    session: Session,
    *,
    owner_id: str,
    operation: str,
    raw_key: str,
    request_digest: str,
) -> AdmissionReadResult:
    """Verify an original replay without consulting current membership or catalog."""

    return _lookup(
        session,
        owner_id=owner_id,
        operation=operation,
        raw_key=raw_key,
        request_digest=request_digest,
        expected=None,
        source="original_replay",
    )


def lookup_staged_admission(
    read_session: Session, staged: AdmissionStaged
) -> AdmissionReadResult:
    """Confirm committed rows using a clean Session independent of the writer."""

    if id(read_session) == staged.staging_session_identity:
        return _refusal("receipt_unverified", "准入回执必须通过独立数据库会话核实")
    if read_session.in_transaction() or read_session.new or read_session.dirty or read_session.deleted:
        return _refusal("receipt_unverified", "准入回执读取会话必须是独立且未使用的会话")
    return _lookup(
        read_session,
        owner_id=staged.owner_id,
        operation=staged.operation,
        raw_key=staged.raw_key,
        request_digest=staged.request_digest,
        expected=staged,
        source="durable_lookup",
    )


def lookup_unknown_commit(
    read_session: Session, unknown: AdmissionCommitUnknown
) -> AdmissionReadResult:
    """Resolve an unknown commit without creating a new key, attempt, or debit."""

    return lookup_staged_admission(read_session, unknown.staged)


def _lookup(
    session: Session,
    *,
    owner_id: str,
    operation: str,
    raw_key: str,
    request_digest: str,
    expected: AdmissionStaged | None,
    source: Literal["durable_lookup", "original_replay"],
) -> AdmissionReadResult:
    if operation not in {"image.single", "video.single", "image.batch"}:
        return _refusal("receipt_unverified", "准入回执操作类型无效")
    if (
        not isinstance(owner_id, str)
        or not owner_id
        or not isinstance(raw_key, str)
        or not raw_key
        or not isinstance(request_digest, str)
        or not _DIGEST.fullmatch(request_digest)
    ):
        return _refusal("receipt_unverified", "准入回执查找范围无效")

    submission_table = TaskSubmissionRow.__table__
    task_table = AITaskRow.__table__
    unit_table = BillingUnitRow.__table__
    step_table = ExecutionStepRow.__table__
    log_table = CreditLogRow.__table__

    try:
        with session.no_autoflush:
            submission = session.execute(
                select(
                    submission_table.c.id,
                    submission_table.c.user_id,
                    submission_table.c.operation,
                    submission_table.c.idempotency_key,
                    submission_table.c.request_digest,
                    submission_table.c.task_ids,
                    submission_table.c.status,
                ).where(
                    submission_table.c.user_id == owner_id,
                    submission_table.c.operation == operation,
                    submission_table.c.idempotency_key == raw_key,
                )
            ).mappings().one_or_none()
            if submission is None:
                if expected is not None:
                    return AdmissionCommitUnknown(expected)
                return _refusal("receipt_missing", "原始准入回执不存在")
            if submission["request_digest"] != request_digest:
                return _refusal("key_digest_conflict", "该幂等键已用于不同请求")
            if (
                submission["user_id"] != owner_id
                or submission["operation"] != operation
                or submission["idempotency_key"] != raw_key
                or not _nonblank(submission["status"], maximum=24)
            ):
                return _refusal("receipt_unverified", "原始提交绑定无法核实")

            task_ids = submission["task_ids"]
            if (
                not isinstance(task_ids, list)
                or not task_ids
                or len(task_ids) > 64
                or any(not isinstance(value, str) or not value for value in task_ids)
                or len(set(task_ids)) != len(task_ids)
                or (operation != "image.batch" and len(task_ids) != 1)
            ):
                return _refusal("receipt_unverified", "原始提交中的任务顺序无效")
            if expected is not None:
                if (
                    submission["id"] != expected.submission_id
                    or submission["id"] != expected.parent_attempt_id
                    or expected.submission_id != expected.parent_attempt_id
                    or not _nonblank(expected.graph_version, maximum=64)
                    or tuple(task.task_id for task in expected.ordered_tasks) != tuple(task_ids)
                ):
                    return _refusal("receipt_unverified", "原始提交与暂存任务集合不一致")

            task_rows = session.execute(
                select(
                    task_table.c.id,
                    task_table.c.user_id,
                    task_table.c.type,
                    task_table.c.status,
                    task_table.c.message_id,
                    task_table.c.credit_cost,
                    task_table.c.billing_status,
                    task_table.c.request_data,
                    task_table.c.model_name,
                ).where(
                    task_table.c.id.in_(task_ids),
                    task_table.c.user_id == owner_id,
                )
            ).mappings().all()
            task_by_id = {row["id"]: row for row in task_rows}
            if len(task_by_id) != len(task_ids):
                return _refusal("receipt_unverified", "原始任务图不完整")

            units_by_task: dict[str, object] = {}
            ordered_unit_ids: list[str] = []
            decoded_data: dict[str, dict[str, object]] = {}
            task_types: set[str] = set()
            seen_child_attempt_ids: set[str] = set()
            seen_prepared_payload_ids: set[str] = set()
            graph_version = expected.graph_version if expected is not None else None
            for position, task_id in enumerate(task_ids):
                task = task_by_id[task_id]
                if (
                    task["user_id"] != owner_id
                    or not _nonblank(task["type"], maximum=20)
                    or task["type"] != _TASK_TYPES_BY_OPERATION[operation]
                    or task["status"] not in _TASK_STATES
                    or not _nonblank(task["model_name"], maximum=255)
                ):
                    return _refusal("receipt_unverified", "原始任务字段无效")
                task_types.add(task["type"])
                try:
                    request_data = json.loads(task["request_data"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    return _refusal("receipt_unverified", "原始任务请求快照无效")
                if (
                    not isinstance(request_data, dict)
                    or set(request_data) - _SAFE_REQUEST_DATA_KEYS
                    or not _REQUIRED_BASE_REQUEST_DATA_KEYS <= set(request_data)
                ):
                    return _refusal("receipt_unverified", "原始任务公开快照包含未受保护字段")
                if (
                    request_data.get("operation") != operation
                    or request_data.get("submission_id") != submission["id"]
                    or request_data.get("parent_attempt_id") != submission["id"]
                    or request_data.get("via") != "worker"
                    or not _nonblank(request_data.get("mode"), maximum=128)
                    or not _nonblank(request_data.get("parent_attempt_id"), maximum=36)
                    or not _nonblank(request_data.get("server_attempt_id"), maximum=128)
                    or not _opaque_payload_id(request_data.get("prepared_payload_id"))
                    or request_data.get("server_attempt_id") != request_data.get("item_id")
                    or request_data.get("server_attempt_id") != request_data.get("unit_key")
                    or request_data.get("request_digest") != request_digest
                    or not _nonblank(request_data.get("model_display_name"), maximum=255)
                    or request_data.get("model_display_name") != task["model_name"]
                    or not _opaque_payload_ref(request_data.get("protected_ref"))
                    or not _nonblank(request_data.get("item_id"), maximum=255)
                    or not _nonblank(request_data.get("unit_key"), maximum=255)
                    or not _nonblank(request_data.get("step_graph_version"), maximum=64)
                    or not _is_digest(request_data.get("request_digest"))
                    or not _is_digest(request_data.get("rule_digest"))
                    or not _is_digest(request_data.get("snapshot_digest"))
                    or not _nonblank(request_data.get("model_id"), maximum=255)
                    or not _nonblank(request_data.get("model_source"), maximum=64)
                    or not _valid_model_owner(
                        request_data.get("model_source"),
                        request_data.get("model_owner_id"),
                        owner_id,
                    )
                    or (
                        request_data.get("prompt_id") is not None
                        and not _nonblank(request_data.get("prompt_id"), maximum=255)
                    )
                    or type(request_data.get("reference_images_count")) is not int
                    or request_data["reference_images_count"] < 0
                    or type(request_data.get("reference_images_version")) is not int
                    or request_data["reference_images_version"] != _REFERENCE_IMAGES_VERSION
                    or not _is_digest(request_data.get("reference_images_digest"))
                    or not _valid_payload_references(request_data)
                ):
                    return _refusal("receipt_unverified", "原始任务与受保护输入关联无效")
                child_attempt_id = request_data["server_attempt_id"]
                prepared_payload_id = request_data["prepared_payload_id"]
                if (
                    child_attempt_id in seen_child_attempt_ids
                    or prepared_payload_id in seen_prepared_payload_ids
                ):
                    return _refusal("receipt_unverified", "原始提交中的子 attempt 或 payload 被重复使用")
                seen_child_attempt_ids.add(child_attempt_id)
                seen_prepared_payload_ids.add(prepared_payload_id)
                if graph_version is None:
                    graph_version = request_data["step_graph_version"]
                elif request_data["step_graph_version"] != graph_version:
                    return _refusal("receipt_unverified", "原始任务混用了不同图版本")
                if operation == "image.batch":
                    if not _BATCH_REQUEST_DATA_KEYS <= set(request_data) or any(
                        not _nonblank(request_data.get(key), maximum=255)
                        for key in (
                            "chapter_id",
                            "asset_type",
                            "asset_id",
                            "asset_name",
                            "context_message_id",
                        )
                    ) or not _is_digest(request_data.get("context_message_sha256")):
                        return _refusal("receipt_unverified", "批量任务原始资产回执字段无效")
                    target_snapshot = request_data.get("target_snapshot")
                    if (
                        not isinstance(target_snapshot, dict)
                        or target_snapshot.get("kind") != "asset_message"
                        or target_snapshot.get("chapter_id") != request_data["chapter_id"]
                        or target_snapshot.get("asset_type") != request_data["asset_type"]
                        or (target_snapshot.get("asset_id") or target_snapshot.get("stable_storyboard_asset_id"))
                        != request_data["asset_id"]
                    ):
                        return _refusal("receipt_unverified", "批量目标与原始资产回执字段不一致")
                elif set(request_data) & _BATCH_REQUEST_DATA_KEYS:
                    return _refusal("receipt_unverified", "单项任务公开快照字段无效")

                if expected is not None:
                    wanted = expected.ordered_tasks[position]
                    try:
                        wanted_data = json.loads(wanted.request_data)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        return _refusal("receipt_unverified", "暂存请求快照无效")
                    if (
                        task_id != wanted.task_id
                        or request_data != wanted_data
                        or task["type"] != wanted.task_type
                        or task["model_name"] != wanted.model_display_name
                        or request_data["model_display_name"] != wanted.model_display_name
                        or request_data["parent_attempt_id"] != expected.parent_attempt_id
                        or request_data["server_attempt_id"] != wanted.server_attempt_id
                        or request_data["prepared_payload_id"] != wanted.prepared_payload_id
                        or request_data["protected_ref"] != wanted.protected_ref
                    ):
                        return _refusal("receipt_unverified", "原始任务与暂存字段不一致")

                unit_rows = session.execute(
                    select(
                        unit_table.c.id,
                        unit_table.c.task_id,
                        unit_table.c.item_key,
                        unit_table.c.operation,
                        unit_table.c.step_graph_version,
                        unit_table.c.input_digest,
                        unit_table.c.quoted_amount,
                        unit_table.c.debited_amount,
                        unit_table.c.refunded_amount,
                        unit_table.c.billing_status,
                        unit_table.c.result_status,
                    ).where(unit_table.c.task_id == task_id)
                ).mappings().all()
                if len(unit_rows) != 1:
                    return _refusal("receipt_unverified", "原始任务计费单元不存在或不唯一")
                unit = unit_rows[0]
                if (
                    unit["task_id"] != task_id
                    or unit["item_key"] != request_data["unit_key"]
                    or unit["operation"] != operation
                    or unit["step_graph_version"] != graph_version
                    or not _is_digest(unit["input_digest"])
                    or type(unit["quoted_amount"]) is not int
                    or unit["quoted_amount"] < 0
                    or type(unit["debited_amount"]) is not int
                    or type(unit["refunded_amount"]) is not int
                    or unit["result_status"] not in _UNIT_RESULT_STATES
                    or unit["billing_status"] not in _UNIT_BILLING_STATES
                    or unit["input_digest"] != request_data["request_digest"]
                    or unit["input_digest"] != request_digest
                ):
                    return _refusal("receipt_unverified", "原始计费单元字段无效")
                if task["credit_cost"] != unit["quoted_amount"]:
                    return _refusal("receipt_unverified", "任务报价与计费单元不一致")
                if request_data.get("billing_unit_id") != unit["id"]:
                    return _refusal("receipt_unverified", "任务与计费单元关联无效")
                if task["billing_status"] not in _TASK_BILLING_STATES:
                    return _refusal("receipt_unverified", "任务聚合财务状态无效")
                if unit["quoted_amount"] == 0:
                    if (
                        unit["billing_status"] != "no_charge"
                        or unit["debited_amount"] != 0
                        or unit["refunded_amount"] != 0
                        or task["billing_status"] != "no_charge"
                    ):
                        return _refusal("receipt_unverified", "零价任务财务状态无效")
                else:
                    if unit["debited_amount"] != unit["quoted_amount"]:
                        return _refusal("receipt_unverified", "正价任务原始扣款金额无效")
                    refund_total = unit["refunded_amount"]
                    if refund_total < 0 or refund_total > unit["debited_amount"]:
                        return _refusal("receipt_unverified", "正价任务累计退款金额无效")
                    expected_unit_status = (
                        "refunded" if refund_total == unit["debited_amount"] else "charged"
                    )
                    expected_task_status = (
                        "refunded"
                        if refund_total == unit["debited_amount"]
                        else "partially_refunded"
                        if refund_total > 0
                        else "charged"
                    )
                    if (
                        unit["billing_status"] != expected_unit_status
                        or task["billing_status"] != expected_task_status
                    ):
                        return _refusal("receipt_unverified", "正价任务财务状态与原扣款或退款不一致")
                if expected is not None and unit["quoted_amount"] != expected.ordered_tasks[position].quoted_amount:
                    return _refusal("receipt_unverified", "原始报价与暂存报价不一致")

                step_rows = session.execute(
                    select(
                        step_table.c.id,
                        step_table.c.billing_unit_id,
                        step_table.c.step_key,
                        step_table.c.dependencies,
                        step_table.c.input_digest,
                        step_table.c.input_snapshot,
                        step_table.c.required,
                        step_table.c.step_order,
                        step_table.c.execution_status,
                    )
                    .where(step_table.c.billing_unit_id == unit["id"])
                    .order_by(step_table.c.step_order, step_table.c.id)
                ).mappings().all()
                wanted_step_ids = (
                    expected.ordered_tasks[position].step_ids if expected is not None else None
                )
                if (
                    len(step_rows) != 1
                    or (wanted_step_ids is not None and tuple(row["id"] for row in step_rows) != wanted_step_ids)
                ):
                    return _refusal("receipt_unverified", "原始任务执行步骤不完整")
                step = step_rows[0]
                snapshot = step["input_snapshot"]
                if isinstance(snapshot, str):
                    try:
                        snapshot = json.loads(snapshot)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        return _refusal("receipt_unverified", "执行步骤快照无效")
                if (
                    step["billing_unit_id"] != unit["id"]
                    or step["step_key"] != "generate"
                    or step["dependencies"] not in ([], ())
                    or step["step_order"] != 0
                    or step["required"] is not True
                    or step["execution_status"] not in _STEP_EXECUTION_STATES
                    or step["input_digest"] != request_data["request_digest"]
                    or step["input_digest"] != request_digest
                    or not isinstance(snapshot, dict)
                    or set(snapshot)
                    != {"server_attempt_id", "prepared_payload_id", "protected_ref", "snapshot_digest"}
                    or snapshot.get("server_attempt_id") != request_data["server_attempt_id"]
                    or snapshot.get("prepared_payload_id") != request_data["prepared_payload_id"]
                    or snapshot.get("protected_ref") != request_data["protected_ref"]
                    or snapshot.get("snapshot_digest") != request_data["snapshot_digest"]
                ):
                    return _refusal("receipt_unverified", "受保护输入与执行步骤关联无效")

                task_message_id = task["message_id"] or None
                if operation == "image.batch":
                    if task_message_id is None:
                        return _refusal("receipt_unverified", "批量任务缺少助手消息")
                    message_ids = (request_data["context_message_id"], task_message_id)
                elif task_message_id is None:
                    message_ids = ()
                else:
                    message_ids = (task_message_id,)
                if expected is not None and message_ids != expected.ordered_tasks[position].message_ids:
                    return _refusal("receipt_unverified", "原始聊天消息与暂存任务不一致")
                if not _verify_messages(session, operation, task, request_data, message_ids):
                    return _refusal("receipt_unverified", "原始聊天消息不完整")

                units_by_task[task_id] = unit
                ordered_unit_ids.append(unit["id"])
                decoded_data[task_id] = request_data

            if graph_version != _SUPPORTED_GRAPH_VERSIONS[operation]:
                return _refusal("receipt_unverified", "原始任务图版本不属于已支持的冻结版本")
            if len(task_types) != 1:
                return _refusal("receipt_unverified", "原始任务集合混用了不同任务类型")

            debit_keys = tuple(f"debit:{unit_id}" for unit_id in ordered_unit_ids)
            ledger_rows = session.execute(
                select(*_credit_log_columns(log_table)).where(
                    log_table.c.billing_unit_id.in_(ordered_unit_ids)
                    | log_table.c.task_id.in_(task_ids)
                    | log_table.c.business_key.in_(debit_keys)
                )
            ).mappings().all()
            rows_by_id = {row["id"]: row for row in ledger_rows}
            debit_by_unit: dict[str, object] = {}
            for unit in units_by_task.values():
                if unit["quoted_amount"] == 0:
                    continue
                expected_key = f"debit:{unit['id']}"
                matching_debits = [
                    row for row in rows_by_id.values() if row["business_key"] == expected_key
                ]
                if len(matching_debits) != 1:
                    return _refusal("receipt_unverified", "正价任务原始扣款流水不存在或不唯一")
                debit_by_unit[unit["id"]] = matching_debits[0]

            debit_ids = tuple(row["id"] for row in debit_by_unit.values())
            if debit_ids:
                refund_rows = session.execute(
                    select(*_credit_log_columns(log_table)).where(
                        log_table.c.related_debit_id.in_(debit_ids)
                    )
                ).mappings().all()
                rows_by_id.update({row["id"]: row for row in refund_rows})

            rows = tuple(rows_by_id.values())
            rows_by_unit: dict[str, list[object]] = {}
            for row in rows:
                if row["billing_unit_id"] in ordered_unit_ids:
                    rows_by_unit.setdefault(row["billing_unit_id"], []).append(row)
                if row["business_key"] in debit_keys and row["billing_unit_id"] not in ordered_unit_ids:
                    return _refusal("receipt_unverified", "扣款业务键绑定到其他计费单元")

            ordered_positive_balances: list[tuple[int, int]] = []
            for position, task_id in enumerate(task_ids):
                task = task_by_id[task_id]
                unit = units_by_task[task_id]
                unit_logs = rows_by_unit.get(unit["id"], [])
                amount = unit["quoted_amount"]
                task_logs = [
                    row
                    for row in rows
                    if row["task_id"] == task_id
                    or row["billing_unit_id"] == unit["id"]
                    or row["business_key"] == f"debit:{unit['id']}"
                ]
                if amount == 0:
                    if task_logs or unit_logs:
                        return _refusal("receipt_unverified", "零价任务存在扣款流水")
                    continue
                expected_key = f"debit:{unit['id']}"
                log = debit_by_unit.get(unit["id"])
                if log is None:
                    return _refusal("receipt_unverified", "正价任务原始扣款流水缺失")
                if (
                    log["user_id"] != owner_id
                    or log["task_id"] != task_id
                    or log["billing_unit_id"] != unit["id"]
                    or log["business_key"] != expected_key
                    or log["amount"] != -amount
                    or type(log["balance_after"]) is not int
                    or log["balance_after"] < 0
                    or log["type"] != "usage"
                    or log["related_debit_id"] is not None
                ):
                    return _refusal("receipt_unverified", "正价任务扣款流水与财务状态不一致")

                refund_total = 0
                seen_refund_keys: set[str] = set()
                original_debit_and_refunds = [
                    row
                    for row in rows
                    if row["id"] == log["id"] or row["related_debit_id"] == log["id"]
                ]
                for row in original_debit_and_refunds:
                    if row["id"] == log["id"]:
                        continue
                    if (
                        row["type"] != "refund"
                        or type(row["amount"]) is not int
                        or row["amount"] <= 0
                        or row["related_debit_id"] != log["id"]
                        or row["user_id"] != owner_id
                        or row["task_id"] != task_id
                        or row["billing_unit_id"] != unit["id"]
                        or not _nonblank(row["business_key"], maximum=255)
                        or row["business_key"] in seen_refund_keys
                        or type(row["balance_after"]) is not int
                        or row["balance_after"] < 0
                    ):
                        return _refusal("receipt_unverified", "退款流水没有精确关联到原始扣款")
                    seen_refund_keys.add(row["business_key"])
                    refund_total += row["amount"]
                if refund_total != unit["refunded_amount"] or refund_total > amount:
                    return _refusal("receipt_unverified", "累计退款与原始扣款金额不一致")

                for row in unit_logs:
                    if row["id"] == log["id"]:
                        continue
                    if row["type"] != "refund" or row["related_debit_id"] != log["id"]:
                        return _refusal("receipt_unverified", "计费单元存在额外或无关流水")
                for row in task_logs:
                    if row["id"] == log["id"] or row["related_debit_id"] == log["id"]:
                        continue
                    return _refusal("receipt_unverified", "任务存在额外或无关财务流水")
                if expected is not None:
                    wanted = expected.ordered_tasks[position]
                    if (
                        log["id"] != wanted.ledger_id
                        or log["business_key"] != wanted.business_key
                        or log["balance_after"] != wanted.balance_after
                    ):
                        return _refusal("receipt_unverified", "扣款流水与暂存财务结果不一致")
                ordered_positive_balances.append((amount, log["balance_after"]))
            for (_, prior_balance), (next_amount, next_balance) in zip(
                ordered_positive_balances, ordered_positive_balances[1:]
            ):
                if prior_balance - next_amount != next_balance:
                    return _refusal("receipt_unverified", "批量扣款余额顺序无法核实")

            durable_parent_attempt = _parent_attempt_id(decoded_data, task_ids)
            if (
                durable_parent_attempt is None
                or durable_parent_attempt != submission["id"]
                or (
                    expected is not None
                    and expected.parent_attempt_id != durable_parent_attempt
                )
            ):
                return _refusal("receipt_unverified", "原始提交与暂存 attempt 不一致")

            if expected is not None and not _verify_staged_payloads(session, expected):
                return AdmissionCommitUnknown(expected)

            receipt = _wire_receipt(
                submission_id=submission["id"],
                operation=operation,
                ordered_task_ids=task_ids,
                task_by_id=task_by_id,
                unit_by_task=units_by_task,
                request_data_by_task=decoded_data,
            )
            return AdmissionConfirmed(
                submission_id=submission["id"],
                operation=operation,
                receipt=receipt,
                verified_at=datetime.now(timezone.utc),
                source=source,
            )
    except (SQLAlchemyError, TypeError, ValueError, KeyError, AttributeError):
        # Malformed or temporarily unreadable evidence never confirms a receipt.
        if expected is not None:
            return AdmissionCommitUnknown(expected)
        return _refusal("receipt_unverified", "原始准入回执暂时无法完整核实")


def _credit_log_columns(log_table: object) -> tuple[object, ...]:
    return (
        log_table.c.id,
        log_table.c.user_id,
        log_table.c.task_id,
        log_table.c.billing_unit_id,
        log_table.c.business_key,
        log_table.c.amount,
        log_table.c.balance_after,
        log_table.c.type,
        log_table.c.related_debit_id,
    )


def _verify_staged_payloads(session: Session, expected: AdmissionStaged) -> bool:
    """Verify the exact producer result after the durable SQL graph checks."""

    binding = expected.producer_binding
    staged_result = expected.producer_staged_result
    try:
        module = getattr(binding, "_module")
        producer = getattr(binding, "_producer")
        if not isinstance(module, ModuleType) or module.__name__ != _PRODUCER_MODULE:
            return False
        producer_type = getattr(module, "MediaSnapshotProducer")
        staged_type = getattr(module, "SnapshotPayloadsStaged")
        verification_type = getattr(module, "PayloadVerificationResult")
        if type(producer) is not producer_type or type(staged_result) is not staged_type:
            return False
        preparation = staged_result.preparation
        if type(preparation) is not AdmissionPreparationFacts:
            return False
        fingerprint = preparation.fingerprint
        if (
            type(fingerprint) is not AdmissionFingerprint
            or staged_result.executable is not False
            or staged_result.parent_attempt_id != expected.parent_attempt_id
            or staged_result.actor_id != expected.owner_id
            or staged_result.operation != expected.operation
            or staged_result.graph_version != expected.graph_version
            or fingerprint.operation != expected.operation
            or fingerprint.actor_id != expected.owner_id
            or fingerprint.ordered_nonsecret_payload_digest != expected.request_digest
            or staged_result.fingerprint != expected.request_digest
        ):
            return False
        payload_items = tuple(staged_result.items)
        if len(payload_items) != len(expected.ordered_tasks):
            return False
        for index, (item, task) in enumerate(zip(payload_items, expected.ordered_tasks, strict=True)):
            if (
                item.index != index
                or item.server_attempt_id != task.server_attempt_id
                or item.prepared_payload_id != task.prepared_payload_id
                or item.protected_ref != task.protected_ref
            ):
                return False

        verifier = getattr(producer, "verify_staged_payloads")
        if not callable(verifier):
            return False
        verification = verifier(session, staged_result)
        return (
            type(verification) is verification_type
            and verification.status == "verified"
            and type(verification.item_count) is int
            and verification.item_count == len(payload_items)
        )
    except Exception:
        return False


def _verify_messages(
    session: Session,
    operation: str,
    task: object,
    request_data: dict[str, object],
    message_ids: tuple[str, ...],
) -> bool:
    if not message_ids:
        return operation != "image.batch"
    rows = session.execute(
        select(
            chat_messages.c.id,
            chat_messages.c.chapter_id,
            chat_messages.c.asset_type,
            chat_messages.c.asset_id,
            chat_messages.c.chat_mode,
            chat_messages.c.role,
            chat_messages.c.content,
            chat_messages.c.model_name,
        ).where(chat_messages.c.id.in_(message_ids))
    ).mappings().all()
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(message_ids):
        return False
    assistant_id = task["message_id"] or None
    if assistant_id is not None:
        assistant = by_id.get(assistant_id)
        if (
            assistant is None
            or assistant["role"] != "assistant"
            or assistant["chat_mode"] != ("video" if operation == "video.single" else "image")
            or assistant["model_name"] != request_data["model_display_name"]
        ):
            return False
    if operation != "image.batch":
        return True
    user_message = by_id.get(request_data["context_message_id"])
    assistant = by_id.get(assistant_id)
    return bool(
        user_message is not None
        and assistant is not None
        and user_message["role"] == "user"
        and assistant["role"] == "assistant"
        and user_message["chapter_id"] == request_data["chapter_id"]
        and assistant["chapter_id"] == request_data["chapter_id"]
        and user_message["asset_type"] == request_data["asset_type"]
        and assistant["asset_type"] == request_data["asset_type"]
        and user_message["asset_id"] == request_data["asset_id"]
        and assistant["asset_id"] == request_data["asset_id"]
        and user_message["chat_mode"] == "image"
        and assistant["chat_mode"] == "image"
        and isinstance(user_message["content"], str)
        and user_message["content"].startswith("__CTX__:")
        and user_message["content"].count("\n") >= 1
        and _sha256(user_message["content"]) == request_data["context_message_sha256"]
    )


def _wire_receipt(
    *,
    submission_id: str,
    operation: str,
    ordered_task_ids: list[str],
    task_by_id: dict[str, object],
    unit_by_task: dict[str, object],
    request_data_by_task: dict[str, dict[str, object]],
) -> AdmissionWireReceipt:
    if operation == "image.batch":
        return BatchImageReceipt(
            tasks=tuple(
                BatchImageTaskReceipt(
                    task_type=request_data_by_task[task_id]["asset_type"],
                    asset_id=request_data_by_task[task_id]["asset_id"],
                    asset_name=request_data_by_task[task_id]["asset_name"],
                    message_id=task_by_id[task_id]["message_id"],
                    task_id=task_id,
                )
                for task_id in ordered_task_ids
            )
        )
    task_id = ordered_task_ids[0]
    task = task_by_id[task_id]
    unit = unit_by_task[task_id]
    return SingleAdmissionReceipt(
        submission_id=submission_id,
        operation=operation,
        task_id=task_id,
        message_id=task["message_id"] or None,
        status=task["status"],
        quoted_amount=unit["quoted_amount"],
        billing_status=unit["billing_status"],
    )


def _parent_attempt_id(
    request_data_by_task: dict[str, dict[str, object]], ordered_task_ids: list[str]
) -> str | None:
    values = {
        request_data_by_task[task_id].get("parent_attempt_id")
        for task_id in ordered_task_ids
    }
    return next(iter(values)) if len(values) == 1 else None


def _nonblank(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum
        and all(ord(character) >= 32 for character in value)
    )


def _valid_model_owner(
    model_source: object, model_owner_id: object, actor_id: str
) -> bool:
    if model_source == "system":
        return model_owner_id is None
    if model_source == "user":
        return (
            _nonblank(model_owner_id, maximum=36)
            and model_owner_id == actor_id
        )
    return False


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _valid_payload_references(request_data: dict[str, object]) -> bool:
    payload_id = request_data.get("prepared_payload_id")
    if not _opaque_payload_id(payload_id):
        return False
    base = f"prepared-payload-v1/{len(payload_id)}:{payload_id}"
    return (
        request_data.get("protected_ref") == base
        and request_data.get("target_ref") == f"{base}#target"
        and request_data.get("model_ref") == f"{base}#model"
        and request_data.get("grant_ref") == f"{base}#grant"
        and request_data.get("reference_ref") == f"{base}#references"
    )


def _opaque_payload_id(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 255


def _opaque_payload_ref(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 512


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _refusal(code: str, message: str) -> AdmissionRefusal:
    status = 409 if code in {"key_digest_conflict", "receipt_unverified", "receipt_missing"} else 503
    return AdmissionRefusal(status_code=status, code=code, message=message)


__all__ = (
    "AdmissionCommitUnknown",
    "AdmissionConfirmed",
    "AdmissionReadResult",
    "AdmissionStaged",
    "StagedTaskExpectation",
    "lookup_staged_admission",
    "lookup_unknown_commit",
    "retain_unknown_commit",
    "verify_original_replay",
)
