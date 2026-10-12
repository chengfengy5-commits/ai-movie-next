"""Pure plans for the five frozen generation fee phases.

A fee plan records server quotes and timing instructions. It does not report a
debit, waiver, receipt, balance, or committed billing outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal

from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import ChargePhase, WorkPhase
from haoai_backend.generation_core.graph_planning import OperationGraphPlan
from haoai_backend.generation_runtime.registry import operation_policy


StreamDisposition = Literal["debit", "skipped", "byok", "zero"]
DebitTiming = Literal[
    "before_optional_task_binding",
    "before_task_commit",
    "after_all_items_validate_before_commit",
    "after_application_commit",
    "after_generation_before_apply",
    "balance_check_only",
    "none",
]
_CONTINUATION_ORDER: dict[str, tuple[WorkPhase, ...]] = {
    "frame.review": ("delivery_pending", "fee_pending", "finalize_pending", "done"),
    "frame.optimize": ("fee_pending", "delivery_pending", "finalize_pending", "done"),
}
_OPERATION_TIMINGS: dict[str, DebitTiming] = {
    "image.single": "before_task_commit",
    "video.single": "before_task_commit",
    "image.batch": "after_all_items_validate_before_commit",
    "frame.review": "after_application_commit",
    "frame.optimize": "after_generation_before_apply",
}


@dataclass(frozen=True, slots=True)
class StreamFeePlanInput:
    """Server-authored stream quote facts; no task or unit is implied."""

    server_attempt_id: str
    server_quote: int
    disposition: StreamDisposition
    message_id: str | None = None


@dataclass(frozen=True, slots=True)
class FeeQuoteLine:
    order: int
    item_id: str
    task_id: str | None
    unit_id: str | None
    quoted_amount: int


@dataclass(frozen=True, slots=True)
class FeePhasePlan:
    operation: str
    charge_phase: ChargePhase
    debit_timing: DebitTiming
    quote_lines: tuple[FeeQuoteLine, ...]
    quoted_total: int
    continuation_order: tuple[WorkPhase, ...]
    local_continuation_policy: str | None
    taskless: bool
    stream_disposition: StreamDisposition | None
    stream_attempt_id: str | None
    optional_message_id: str | None
    plan_digest: str


def build_fee_phase_plan(
    operation: str,
    *,
    graph: OperationGraphPlan | None = None,
    stream: StreamFeePlanInput | None = None,
) -> FeePhasePlan:
    """Select the immutable timing plan for one registered operation."""

    policy = operation_policy(operation) if isinstance(operation, str) else None
    if policy is None:
        raise _invalid("unsupported operation fee policy")

    if operation == "text.stream":
        if graph is not None or stream is None or policy.graph_version is not None:
            raise _invalid("stream fees require a taskless stream plan input")
        _require_identifier(stream.server_attempt_id, "stream attempt id")
        if stream.message_id is not None:
            _require_identifier(stream.message_id, "optional message id")
        quote = _quote(stream.server_quote)
        if not isinstance(stream.disposition, str) or stream.disposition not in {
            "debit",
            "skipped",
            "byok",
            "zero",
        }:
            raise _invalid("unsupported stream fee disposition")
        if stream.disposition == "debit" and quote <= 0:
            raise _invalid("a stream debit plan requires a positive server quote")
        if stream.disposition in {"byok", "zero"} and quote != 0:
            raise _invalid("BYOK and zero-cost stream plans require a zero server quote")
        timing: DebitTiming = {
            "debit": "before_optional_task_binding",
            "skipped": "balance_check_only",
            "byok": "none",
            "zero": "none",
        }[stream.disposition]
        lines = (
            FeeQuoteLine(
                order=0,
                item_id=stream.server_attempt_id,
                task_id=None,
                unit_id=None,
                quoted_amount=quote,
            ),
        )
        plan_payload = {
            "operation": operation,
            "charge_phase": policy.charge_phase,
            "debit_timing": timing,
            "server_attempt_id": stream.server_attempt_id,
            "server_quote": quote,
            "disposition": stream.disposition,
            "message_id": stream.message_id,
            "taskless": True,
        }
        return FeePhasePlan(
            operation=operation,
            charge_phase=policy.charge_phase,
            debit_timing=timing,
            quote_lines=lines,
            quoted_total=quote,
            continuation_order=(),
            local_continuation_policy=None,
            taskless=True,
            stream_disposition=stream.disposition,
            stream_attempt_id=stream.server_attempt_id,
            optional_message_id=stream.message_id,
            plan_digest=_digest_value(plan_payload),
        )

    if stream is not None or graph is None:
        raise _invalid("registered task operations require an immutable graph plan")
    if (
        graph.operation != operation
        or graph.charge_phase != policy.charge_phase
        or graph.policy != policy
    ):
        raise _invalid("graph operation does not match the selected fee policy")
    if graph.graph_version != policy.graph_version and not (
        operation == "video.single" and graph.graph_version == "video-haoai-attempt-group-v1"
    ):
        raise _invalid("graph version does not match the selected fee policy")
    if not graph.items or not graph.units or len(graph.items) != len(graph.units):
        raise _invalid("graph plan has no complete per-item quote lines")

    unit_by_item = {unit.item_id: unit for unit in graph.units}
    if len(unit_by_item) != len(graph.units):
        raise _invalid("graph plan repeats a billing item")
    lines: list[FeeQuoteLine] = []
    for item in graph.items:
        unit = unit_by_item.get(item.item_id)
        if unit is None or unit.task_id != item.task_id or unit.unit_id != item.unit_id:
            raise _invalid("graph plan item and unit bindings do not match")
        quote = _quote(item.snapshot.server_quote)
        if type(unit.quoted_amount) is not int or unit.quoted_amount != quote:
            raise _invalid("unit quote differs from its frozen server snapshot")
        lines.append(
            FeeQuoteLine(
                order=item.order,
                item_id=item.item_id,
                task_id=item.task_id,
                unit_id=item.unit_id,
                quoted_amount=quote,
            )
        )
    if [line.order for line in lines] != list(range(len(lines))):
        raise _invalid("fee items must preserve the graph's original order")

    timing = _OPERATION_TIMINGS.get(operation)
    if timing is None:
        raise _invalid("registered operation has no frozen fee timing")
    phase_order = _CONTINUATION_ORDER.get(operation, ())
    plan_payload = {
        "operation": operation,
        "graph_version": graph.graph_version,
        "charge_phase": policy.charge_phase,
        "debit_timing": timing,
        "quote_lines": [
            {
                "order": line.order,
                "item_id": line.item_id,
                "task_id": line.task_id,
                "unit_id": line.unit_id,
                "quoted_amount": line.quoted_amount,
            }
            for line in lines
        ],
        "continuation_order": phase_order,
        "local_continuation_policy": policy.local_continuation_policy,
    }
    return FeePhasePlan(
        operation=operation,
        charge_phase=policy.charge_phase,
        debit_timing=timing,
        quote_lines=tuple(lines),
        quoted_total=sum(line.quoted_amount for line in lines),
        continuation_order=phase_order,
        local_continuation_policy=policy.local_continuation_policy,
        taskless=False,
        stream_disposition=None,
        stream_attempt_id=None,
        optional_message_id=None,
        plan_digest=_digest_value(plan_payload),
    )


def _quote(value: object) -> int:
    if type(value) is not int or value < 0:
        raise _invalid("server quote must be a nonnegative integer")
    return value


def _require_identifier(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
        or any(ord(character) < 32 for character in value)
    ):
        raise _invalid(f"{label} must be a stable nonblank string")


def _digest_value(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return sha256(encoded).hexdigest()


def _invalid(detail: str) -> GenerationError:
    return GenerationError("invalid_generation_request", detail)


__all__ = (
    "FeePhasePlan",
    "FeeQuoteLine",
    "StreamFeePlanInput",
    "build_fee_phase_plan",
)
