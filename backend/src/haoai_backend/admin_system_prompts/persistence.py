"""SQLAlchemy adapter for the legacy system_prompts table."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import SystemPromptRecord
from .ports import AdminSystemPromptUnitOfWork
from .tables import SystemPromptRow, system_prompts


class SqlAlchemyAdminSystemPromptUnitOfWork(AdminSystemPromptUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_prompts(
        self,
        *,
        category: str | None,
        is_active: bool | None,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[SystemPromptRecord], int]:
        query = self._session.query(SystemPromptRow)
        if category:
            query = query.filter(SystemPromptRow.category == category)
        if is_active is not None:
            query = query.filter(SystemPromptRow.is_active == is_active)
        total = query.count()
        rows = (
            query.order_by(SystemPromptRow.sort_order)
            .offset(skip)
            .limit(limit)
            .all()
        )
        return tuple(_record(row) for row in rows), total

    def get_prompt(self, prompt_id: str) -> SystemPromptRecord | None:
        row = self._session.execute(
            select(system_prompts).where(system_prompts.c.id == prompt_id)
        ).mappings().first()
        return _record(row) if row is not None else None

    def add_prompt(self, prompt_id: str, values: Mapping[str, Any]) -> None:
        self._session.add(SystemPromptRow(id=prompt_id, **dict(values)))

    def update_prompt(self, prompt_id: str, changes: Mapping[str, Any]) -> bool:
        row = (
            self._session.query(SystemPromptRow)
            .filter(SystemPromptRow.id == prompt_id)
            .first()
        )
        if row is None:
            return False
        for field, value in changes.items():
            setattr(row, field, value)
        return True

    def delete_prompt(self, prompt_id: str) -> bool:
        row = (
            self._session.query(SystemPromptRow)
            .filter(SystemPromptRow.id == prompt_id)
            .first()
        )
        if row is None:
            return False
        self._session.delete(row)
        return True

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _record(row: Any) -> SystemPromptRecord:
    fields = (
        "id",
        "category",
        "name",
        "system_prompt",
        "user_prompt",
        "sort_order",
        "is_active",
        "created_at",
        "updated_at",
    )
    if isinstance(row, Mapping):
        values = {field: row[field] for field in fields}
    else:
        values = {field: getattr(row, field) for field in fields}
    return SystemPromptRecord(**values)
