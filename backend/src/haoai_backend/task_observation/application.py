"""Read-only task-observation use cases and cancellation orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams.policy import has_team_permission

from .domain import TaskRecord
from .errors import (
    TaskObservationBadRequest,
    TaskObservationConflict,
    TaskObservationForbidden,
    TaskObservationNotFound,
    TaskObservationUnavailable,
)
from .payload import (
    build_ai_review_counts,
    build_ai_review_status,
    build_batch_optimize_status,
    build_running_batch_optimize,
    build_task_list_payload,
    build_task_request,
    find_running_batch_optimize,
)
from .ports import (
    CancellationSignalRegistry,
    TaskCancellationWriter,
    TaskObservationUnitOfWork,
    TaskRow,
    UnitOfWorkFactory,
)
from .receipts import build_submission_receipt, build_task_receipt

T = TypeVar("T")
TaskValue = Mapping[str, Any] | TaskRecord


def _open_uow(factory: UnitOfWorkFactory | None) -> TaskObservationUnitOfWork:
    if factory is None:
        raise TaskObservationUnavailable()
    return factory()


def _run_read_only(
    factory: UnitOfWorkFactory | None,
    operation: Callable[[TaskObservationUnitOfWork], T],
) -> T:
    uow = _open_uow(factory)
    try:
        return operation(uow)
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


def _value(row: TaskValue, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def get_task_receipt(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Read an owned task using the source's mutually exclusive selectors."""

    def operation(uow: TaskObservationUnitOfWork) -> dict[str, Any]:
        if task_id is not None and message_id is not None:
            raise TaskObservationBadRequest("task_id 与 message_id 只能提供一个")

        if task_id is not None:
            if not task_id.strip():
                raise TaskObservationBadRequest("task_id 不能为空")
            task = uow.load_owned_task(actor.user_id, task_id)
            if task is None:
                raise TaskObservationNotFound("任务不存在")
            return build_task_receipt(task, uow)

        if message_id is None or not message_id.strip():
            raise TaskObservationBadRequest("必须提供 task_id 或 message_id")

        matches = uow.find_owned_by_message(actor.user_id, message_id, limit=2)
        if len(matches) > 1:
            raise TaskObservationConflict("message_id 对应多个任务，请改用 task_id 查询")
        if not matches:
            return {"status": None, "result": None}
        return build_task_receipt(matches[0], uow)

    return _run_read_only(factory, operation)


def get_submission_receipt(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    operation: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Read a stored submission receipt without replaying submission work."""

    return _run_read_only(
        factory,
        lambda uow: build_submission_receipt(
            actor.user_id,
            operation,
            idempotency_key,
            uow,
            uow,
        ),
    )


def _require_task_request_access(
    uow: TaskObservationUnitOfWork,
    actor: TrustedActor,
    task: TaskValue,
    team_id: str | None,
) -> None:
    if _value(task, "user_id") == actor.user_id:
        return
    if uow.load_sql_superuser(actor.user_id):
        return

    if team_id:
        caller_membership = uow.load_membership(team_id, actor.user_id)
        if caller_membership is None:
            raise TaskObservationForbidden("你不是该团队成员")

        permission_membership = uow.load_membership(team_id, actor.user_id)
        if permission_membership is None or not has_team_permission(
            _value(permission_membership, "role"),
            _value(permission_membership, "permissions"),
            "view_tasks",
        ):
            raise TaskObservationForbidden("没有执行该操作的权限")

        task_owner = _value(task, "user_id")
        if uow.load_membership(team_id, task_owner) is not None:
            return

    raise TaskObservationForbidden("无权查看该任务的请求体")


def get_task_request(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str,
    team_id: str | None = None,
) -> dict[str, Any]:
    """Read task request data after the global task-existence check."""

    def operation(uow: TaskObservationUnitOfWork) -> dict[str, Any]:
        task = uow.load_task(task_id)
        if task is None:
            raise TaskObservationNotFound("任务不存在")
        _require_task_request_access(uow, actor, task, team_id)
        return build_task_request(task)

    return _run_read_only(factory, operation)


def list_tasks(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Return one source-compatible page of owned task and context data."""
    return _run_read_only(
        factory,
        lambda uow: build_task_list_payload(actor.user_id, page, page_size, uow),
    )


def get_ai_review_counts(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    message_id: str,
) -> dict[str, Any]:
    """Count owned review candidates after preserving the source JSON recheck."""

    def operation(uow: TaskObservationUnitOfWork) -> dict[str, Any]:
        candidates: Sequence[TaskRow | TaskRecord]
        if not message_id:
            candidates = ()
        else:
            candidates = uow.list_ai_review_count_candidates(actor.user_id, message_id)
        return build_ai_review_counts(message_id, candidates)

    return _run_read_only(factory, operation)


def get_ai_review_status(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str,
) -> dict[str, Any]:
    """Return the owned task's review fields without imposing a type gate."""

    def operation(uow: TaskObservationUnitOfWork) -> dict[str, Any]:
        task = uow.load_owned_task(actor.user_id, task_id)
        if task is None:
            raise TaskObservationNotFound("任务不存在")
        return build_ai_review_status(task)

    return _run_read_only(factory, operation)


def get_running_batch_optimize(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
) -> dict[str, Any]:
    """Find the first owned running optimize candidate for a chapter."""

    def operation(uow: TaskObservationUnitOfWork) -> dict[str, Any]:
        candidates = uow.list_running_batch_optimize_candidates(actor.user_id)
        return build_running_batch_optimize(
            find_running_batch_optimize(chapter_id, candidates)
        )

    return _run_read_only(factory, operation)


def get_batch_optimize_status(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str,
) -> dict[str, Any]:
    """Return the owned task's batch status without a type gate."""

    def operation(uow: TaskObservationUnitOfWork) -> dict[str, Any]:
        task = uow.load_owned_task(actor.user_id, task_id)
        if task is None:
            raise TaskObservationNotFound("任务不存在")
        return build_batch_optimize_status(task)

    return _run_read_only(factory, operation)


def request_batch_optimize_cancel(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str,
    signal_registry: CancellationSignalRegistry | None,
    cancellation_writer: TaskCancellationWriter | None,
) -> dict[str, Any]:
    """Signal the local worker before requesting the independent status write."""
    if factory is None:
        raise TaskObservationUnavailable()
    if cancellation_writer is None:
        raise TaskObservationUnavailable("任务取消服务尚未接线")

    def operation(uow: TaskObservationUnitOfWork) -> dict[str, Any]:
        task = uow.load_owned_task(actor.user_id, task_id)
        if task is None:
            raise TaskObservationNotFound("任务不存在")

        if _value(task, "status") in ("queued", "processing"):
            signal = signal_registry.get(task_id) if signal_registry is not None else None
            if signal:
                signal.set()
            cancellation_writer.request_cancel(task_id)

        return {"task_id": _value(task, "id"), "status": "cancelling"}

    return _run_read_only(factory, operation)
