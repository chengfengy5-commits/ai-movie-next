"""Create and query canonical target projections for committed results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.canvas_targets import (
    CANVAS_ASSET_TARGET,
    CANVAS_FRAME_TARGET,
    CanvasTargetParseError,
    canonical_target_snapshot_digest,
    canvas_target_identity,
    target_snapshot_source,
)
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
)

from .schema import GenerationResultTargetRow

ProjectionStatus = Literal["project", "ignored_noncanvas", "invalid_binding"]


@dataclass(frozen=True, slots=True)
class CanvasResultTargetProjection:
    status: ProjectionStatus
    row: GenerationResultTargetRow | None = None
    detail: str | None = None


def prepare_canvas_result_target_projection(
    session: Session,
    receipt: GenerationResultReceipt,
) -> CanvasResultTargetProjection:
    """Validate frozen original facts and prepare one receipt projection.

    This function only reads caller-session rows. Its caller stages the receipt
    and projection together in the same transaction after a positive result.
    """

    task = session.get(AITaskRow, receipt.task_id)
    step = session.get(ExecutionStepRow, receipt.step_id)
    submission = session.get(ExternalSubmissionRow, receipt.submission_id)
    if (
        task is None
        or task.id != receipt.task_id
        or task.user_id != receipt.owner_id
        or step is None
        or step.id != receipt.step_id
        or submission is None
        or submission.id != receipt.submission_id
    ):
        return _invalid("original target sources are not bound to the receipt")

    sources = (
        task.request_data,
        step.input_snapshot,
        submission.request_snapshot,
    )
    parsed = tuple(target_snapshot_source(source) for source in sources)
    targets = tuple(target for _present, target, _hint in parsed if target is not None)
    has_canvas_hint = any(hint for _present, _target, hint in parsed)
    has_malformed_source = any(
        present and target is None
        for present, target, _hint in parsed
    )

    if not targets and not has_canvas_hint:
        return CanvasResultTargetProjection("ignored_noncanvas")
    if has_canvas_hint and has_malformed_source:
        return _invalid("a stored Canvas target source is malformed")
    if not targets:
        return _invalid("a stored Canvas target could not be reconstructed")

    target = targets[0]
    task_target = parsed[0][1]
    if task_target is None or task_target.kind not in {
        CANVAS_FRAME_TARGET,
        CANVAS_ASSET_TARGET,
    }:
        return _invalid("task request does not retain the original Canvas target")
    if any(candidate != target for candidate in targets[1:]):
        return _invalid("stored original Canvas target sources disagree")
    if receipt.operation not in {"image.single", "image.batch"}:
        return _invalid("Canvas target is bound to an unsupported generation operation")

    try:
        target_identity = canvas_target_identity(target)
        target_digest = canonical_target_snapshot_digest(target)
    except (CanvasTargetParseError, TypeError, ValueError):
        return _invalid("stored Canvas target identity is invalid")

    return CanvasResultTargetProjection(
        "project",
        GenerationResultTargetRow(
            result_ref=receipt.result_ref,
            owner_id=receipt.owner_id,
            task_id=receipt.task_id,
            series_id=target.series_id,
            chapter_id=target.chapter_id,
            target_kind=target.kind,
            target_identity=target_identity,
            target_snapshot_digest=target_digest,
            generation_fulfilled_at=receipt.generation_fulfilled_at,
        ),
    )


def _invalid(detail: str) -> CanvasResultTargetProjection:
    return CanvasResultTargetProjection("invalid_binding", detail=detail)


__all__ = (
    "CanvasResultTargetProjection",
    "prepare_canvas_result_target_projection",
)
