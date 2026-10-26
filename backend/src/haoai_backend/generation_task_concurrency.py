"""Shared user task-capacity policy for registered generation operations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import AITaskRow


SOURCE_WORKER_TYPES: Final[tuple[str, ...]] = (
    "batch-optimize",
    "batch-image",
    "image-single",
    "video-single",
    "ai-review",
)
IN_FLIGHT_STATES: Final[tuple[str, ...]] = (
    "queued",
    "processing",
    "cancelling",
    "reconciling",
)

_TASK_BATCH_OPTIMIZE = "batch-optimize"
_TASK_BATCH_IMAGE = "batch-image"
_TASK_IMAGE_SINGLE = "image-single"
_TASK_VIDEO_SINGLE = "video-single"
_TASK_AI_REVIEW = "ai-review"


@dataclass(frozen=True, slots=True)
class _OperationTaskPolicy:
    task_type: str
    cardinality: Literal["single", "per_item", "shared"]


_OPERATION_TASK_POLICIES: Final[Mapping[str, _OperationTaskPolicy]] = MappingProxyType(
    {
        "image.single": _OperationTaskPolicy(_TASK_IMAGE_SINGLE, "single"),
        "video.single": _OperationTaskPolicy(_TASK_VIDEO_SINGLE, "single"),
        "image.batch": _OperationTaskPolicy(_TASK_BATCH_IMAGE, "per_item"),
        "frame.review": _OperationTaskPolicy(_TASK_AI_REVIEW, "single"),
        "frame.optimize": _OperationTaskPolicy(_TASK_BATCH_OPTIMIZE, "shared"),
    }
)
OPERATION_TASK_TYPES: Final[Mapping[str, str]] = MappingProxyType(
    {operation: policy.task_type for operation, policy in _OPERATION_TASK_POLICIES.items()}
)


@dataclass(frozen=True, slots=True)
class TaskShape:
    task_type: str
    task_count: int


def task_type_for_operation(operation: str) -> str | None:
    policy = _OPERATION_TASK_POLICIES.get(operation)
    return policy.task_type if policy is not None else None


def task_shape_for_operation(operation: str, item_count: int) -> TaskShape | None:
    """Return the only supported task type/cardinality for a frozen graph."""

    if type(item_count) is not int or item_count < 1:
        return None
    policy = _OPERATION_TASK_POLICIES.get(operation)
    if policy is None:
        return None
    if policy.cardinality == "single":
        return TaskShape(policy.task_type, 1) if item_count == 1 else None
    if policy.cardinality == "per_item":
        return TaskShape(policy.task_type, item_count)
    return TaskShape(policy.task_type, 1)


def graph_tasks_match_shape(
    operation: str,
    item_count: int,
    tasks: Sequence[object],
) -> bool:
    shape = task_shape_for_operation(operation, item_count)
    if shape is None or len(tasks) != shape.task_count:
        return False
    return all(getattr(task, "task_type", None) == shape.task_type for task in tasks)


def count_user_in_flight_tasks(session: Session, user_id: str) -> int:
    """Count task rows across the shared five-type/four-state policy."""

    return int(
        session.execute(
            select(func.count())
            .select_from(AITaskRow)
            .where(
                AITaskRow.user_id == user_id,
                AITaskRow.type.in_(SOURCE_WORKER_TYPES),
                AITaskRow.status.in_(IN_FLIGHT_STATES),
                AITaskRow.billing_status != "refunded",
            )
        ).scalar_one()
    )


def session_uses_fresh_postgres_statements(session: Session) -> bool:
    """Fail closed unless PostgreSQL admissions see each statement's latest commit."""

    try:
        if session.get_bind().dialect.name != "postgresql":
            return True
        isolation_level = session.connection().get_isolation_level()
    except Exception:
        return False
    return (
        isinstance(isolation_level, str)
        and isolation_level.replace("_", " ").strip().casefold() == "read committed"
    )


__all__ = (
    "IN_FLIGHT_STATES",
    "OPERATION_TASK_TYPES",
    "SOURCE_WORKER_TYPES",
    "TaskShape",
    "count_user_in_flight_tasks",
    "graph_tasks_match_shape",
    "session_uses_fresh_postgres_statements",
    "task_shape_for_operation",
    "task_type_for_operation",
)
