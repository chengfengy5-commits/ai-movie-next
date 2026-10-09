"""Source-compatible task and idempotency receipt projections."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .errors import (
    TaskObservationBadRequest,
    TaskObservationIntegrityError,
    TaskObservationNotFound,
)
from .ports import TaskObservationReader, TaskReceiptReader, TaskRow


def _value(row: Mapping[str, Any] | object, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _isoformat(value: Any) -> str | None:
    return value.isoformat() if value else None


def build_task_receipt(
    task: Mapping[str, Any] | object,
    reader: TaskReceiptReader,
) -> dict[str, Any]:
    """Project the task and its chronologically first billing unit."""
    task_id = _value(task, "id")
    unit = reader.first_billing_unit(task_id)
    if unit is None:
        billing_status = _value(task, "billing_status")
        quoted_amount = _value(task, "credit_cost")
    else:
        billing_status = _value(unit, "billing_status")
        quoted_amount = _value(unit, "quoted_amount")

    return {
        "id": task_id,
        "type": _value(task, "type"),
        "message_id": _value(task, "message_id") or None,
        "status": _value(task, "status"),
        "result": _value(task, "result"),
        "billing_status": billing_status,
        "quoted_amount": quoted_amount,
        "progress": _value(task, "progress") or 0,
        "progress_message": _value(task, "progress_message"),
        "external_task_id": _value(task, "external_task_id"),
        "external_provider": _value(task, "external_provider"),
        "created_at": _isoformat(_value(task, "created_at")),
        "updated_at": _isoformat(_value(task, "updated_at")),
    }


def _validate_submission_key(operation: str, raw_key: str | None) -> str:
    if operation not in {"image.single", "video.single"}:
        raise TaskObservationBadRequest("不支持的任务操作")
    if raw_key is None or not raw_key.strip() or len(raw_key) > 255:
        raise TaskObservationBadRequest("Idempotency-Key 必须为 1 到 255 个非空白字符")
    return raw_key


def build_submission_receipt(
    actor_id: str,
    operation: str,
    raw_key: str | None,
    observation_reader: TaskObservationReader,
    receipt_reader: TaskReceiptReader,
) -> dict[str, Any]:
    """Read one accepted submission, requiring exactly one owned task and unit."""
    idempotency_key = _validate_submission_key(operation, raw_key)
    submission = receipt_reader.find_submission(actor_id, operation, idempotency_key)
    if submission is None:
        raise TaskObservationNotFound("未找到该幂等键对应的任务提交")

    task_ids = _value(submission, "task_ids")
    task_ids = task_ids if isinstance(task_ids, list) else []
    if len(task_ids) != 1:
        raise TaskObservationIntegrityError("提交记录与任务集合不一致")

    owner_id = _value(submission, "user_id")
    task = observation_reader.load_owned_task(owner_id, task_ids[0])
    if task is None:
        raise TaskObservationIntegrityError("提交任务记录不存在")

    task_id = _value(task, "id")
    unit = receipt_reader.unique_billing_unit(task_id)
    if unit is None:
        raise TaskObservationIntegrityError("任务计费单元记录不存在")

    return {
        "submission_id": _value(submission, "id"),
        "operation": _value(submission, "operation"),
        "task_id": task_id,
        "message_id": _value(task, "message_id") or None,
        "status": _value(task, "status"),
        "quoted_amount": _value(unit, "quoted_amount"),
        "billing_status": _value(unit, "billing_status"),
    }
