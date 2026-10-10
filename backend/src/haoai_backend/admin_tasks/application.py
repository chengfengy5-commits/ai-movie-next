from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .domain import AdminTaskDetail, AdminTaskFilters, AdminTaskPage
from .errors import AdminTaskError
from .ports import AdminTaskUnitOfWork, AdminTaskUnitOfWorkFactory

T = TypeVar("T")


def _read_only(
    factory: AdminTaskUnitOfWorkFactory | None,
    operation: Callable[[AdminTaskUnitOfWork], T],
) -> T:
    if factory is None:
        raise AdminTaskError(503, "管理员任务服务尚未接线")

    unit_of_work = factory()
    try:
        return operation(unit_of_work)
    finally:
        try:
            unit_of_work.rollback()
        finally:
            unit_of_work.close()


def list_tasks(
    factory: AdminTaskUnitOfWorkFactory | None,
    filters: AdminTaskFilters,
    *,
    skip: int = 0,
    limit: int = 10,
    sort_field: str = "created_at",
    sort_order: str = "desc",
) -> AdminTaskPage:
    def operation(unit_of_work: AdminTaskUnitOfWork) -> AdminTaskPage:
        total = unit_of_work.count_tasks(filters)
        items = unit_of_work.list_tasks(
            filters,
            sort_field=sort_field,
            sort_order=sort_order,
            offset=skip,
            limit=limit,
        )
        return AdminTaskPage(data=tuple(items), total=total)

    return _read_only(factory, operation)


def get_task(
    factory: AdminTaskUnitOfWorkFactory | None,
    task_id: str,
) -> AdminTaskDetail:
    def operation(unit_of_work: AdminTaskUnitOfWork) -> AdminTaskDetail:
        task = unit_of_work.load_task_by_id(task_id)
        if task is None:
            raise AdminTaskError(404, "任务不存在")
        return task

    return _read_only(factory, operation)
