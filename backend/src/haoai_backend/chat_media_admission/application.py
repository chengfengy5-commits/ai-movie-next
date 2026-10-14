"""Request validation and exact legacy wire-receipt projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from haoai_backend.generation_catalog.admission_types import (
    AdmissionContextError,
    build_admission_context,
)
from haoai_backend.generation_contracts.types import (
    BatchImageReceipt,
    GenerationActor,
    SingleAdmissionReceipt,
)
from haoai_backend.shared.identity import TrustedActor

from .errors import ChatMediaRequestError


_BATCH_WIRE_TYPE_BY_ASSET_TYPE = {
    "character": "character",
    "scene": "scene",
    "prop": "prop",
    "storyboard": "storyboard",
}


def validate_admission_request(
    actor: TrustedActor,
    operation: str,
    raw_key: str | None,
    request: Mapping[str, object],
) -> None:
    """Apply the shared canonicalizer and idempotency-key rules before SQL."""

    generation_actor = GenerationActor(
        user_id=actor.user_id,
        trusted_account_identity=actor,
        new_admission_allowed=None,
    )
    try:
        build_admission_context(
            generation_actor,
            operation,
            request,
            raw_key=raw_key,
        )
    except (AdmissionContextError, TypeError, ValueError) as error:
        raise ChatMediaRequestError("请求或幂等键无效") from error


def receipt_to_wire(receipt: object) -> dict[str, Any]:
    """Project only the public fields from a durable typed admission receipt."""

    if isinstance(receipt, SingleAdmissionReceipt):
        return {
            "submission_id": receipt.submission_id,
            "operation": receipt.operation,
            "task_id": receipt.task_id,
            "message_id": receipt.message_id,
            "status": receipt.status,
            "quoted_amount": receipt.quoted_amount,
            "billing_status": receipt.billing_status,
        }
    if isinstance(receipt, BatchImageReceipt):
        tasks: list[dict[str, str]] = []
        for task in receipt.tasks:
            wire_type = _BATCH_WIRE_TYPE_BY_ASSET_TYPE.get(task.task_type)
            if wire_type is None:
                raise TypeError("unsupported batch asset type in durable receipt")
            tasks.append(
                {
                    "type": wire_type,
                    "asset_id": task.asset_id,
                    "asset_name": task.asset_name,
                    "message_id": task.message_id,
                    "task_id": task.task_id,
                }
            )
        return {
            "tasks": tasks
        }
    raise TypeError("unsupported durable chat media receipt")
