from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import AdminTaskDetail, AdminTaskFilters, AdminTaskListItem
from .ports import AdminTaskUnitOfWork, AdminTaskUnitOfWorkFactory
from .tables import AdminTask


def _list_item(row: Any) -> AdminTaskListItem:
    return AdminTaskListItem(
        id=row.id,
        user_id=row.user_id,
        type=row.type,
        status=row.status,
        credit_cost=row.credit_cost,
        message_id=row.message_id,
        model_name=row.model_name or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _detail(row: Any) -> AdminTaskDetail:
    return AdminTaskDetail(
        id=row.id,
        user_id=row.user_id,
        type=row.type,
        status=row.status,
        message_id=row.message_id,
        request_data=row.request_data,
        result=row.result,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyAdminTaskUnitOfWork(AdminTaskUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _filtered_query(self, filters: AdminTaskFilters) -> Any:
        query = self._session.query(AdminTask)
        if filters.user_id:
            query = query.filter(AdminTask.user_id == filters.user_id)
        if filters.task_type:
            query = query.filter(AdminTask.type == filters.task_type)
        if filters.status:
            query = query.filter(AdminTask.status == filters.status)
        if filters.model_name:
            query = query.filter(AdminTask.model_name.ilike(f"%{filters.model_name}%"))
        return query

    def count_tasks(self, filters: AdminTaskFilters) -> int:
        return int(self._filtered_query(filters).count())

    def list_tasks(
        self,
        filters: AdminTaskFilters,
        *,
        sort_field: str,
        sort_order: str,
        offset: int,
        limit: int,
    ) -> Sequence[AdminTaskListItem]:
        column = getattr(AdminTask, sort_field, AdminTask.id)
        direction = desc(column) if sort_order == "desc" else asc(column)
        rows = (
            self._filtered_query(filters)
            .order_by(direction)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return tuple(_list_item(row) for row in rows)

    def load_task_by_id(self, task_id: str) -> AdminTaskDetail | None:
        row = self._session.query(AdminTask).filter(AdminTask.id == task_id).first()
        return _detail(row) if row is not None else None

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def admin_task_unit_of_work_factory(
    session_factory: Callable[[], Session],
) -> AdminTaskUnitOfWorkFactory:
    def create_unit_of_work() -> AdminTaskUnitOfWork:
        return SqlAlchemyAdminTaskUnitOfWork(session_factory())

    return create_unit_of_work
