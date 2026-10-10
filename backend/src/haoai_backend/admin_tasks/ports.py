"""Framework-free query ports for administrator task reads."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypeAlias

from .domain import AdminTaskDetail, AdminTaskFilters, AdminTaskListItem


class AdminTaskUnitOfWork(Protocol):
    """Read-only task queries; the application counts before listing and never commits."""

    def count_tasks(self, filters: AdminTaskFilters) -> int: ...

    def list_tasks(
        self,
        filters: AdminTaskFilters,
        *,
        sort_field: str,
        sort_order: str,
        offset: int,
        limit: int,
    ) -> Sequence[AdminTaskListItem]: ...

    def load_task_by_id(self, task_id: str) -> AdminTaskDetail | None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


AdminTaskUnitOfWorkFactory: TypeAlias = Callable[[], AdminTaskUnitOfWork]
