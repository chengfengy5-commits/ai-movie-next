"""Controlled-task checks shared by generic status and progress updates."""

from __future__ import annotations

from haoai_backend.generic_tasks.domain import GenericTaskRecord
from haoai_backend.generic_tasks.errors import GenericTaskError
from haoai_backend.generic_tasks.ports import GenericTaskUnitOfWork

CONTROLLED_TASK_TYPES = frozenset(
    {"batch-optimize", "batch-image", "image-single", "video-single", "ai-review"}
)
_CONTROLLED_TASK_DETAIL = "该任务由受控执行流程管理，请刷新任务状态"


def reject_controlled_task_update(
    unit_of_work: GenericTaskUnitOfWork,
    task: GenericTaskRecord,
    actor_id: str,
) -> None:
    """Reject updates if any of the three source ownership signals applies."""

    if task.type in CONTROLLED_TASK_TYPES:
        raise GenericTaskError(409, _CONTROLLED_TASK_DETAIL)

    if unit_of_work.has_billing_unit(task.id):
        raise GenericTaskError(409, _CONTROLLED_TASK_DETAIL)

    for submission in unit_of_work.list_submissions_for_actor(actor_id):
        if isinstance(submission.task_ids, list) and task.id in submission.task_ids:
            raise GenericTaskError(409, _CONTROLLED_TASK_DETAIL)
