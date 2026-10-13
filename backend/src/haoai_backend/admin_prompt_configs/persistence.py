"""SQLAlchemy adapter preserving the fixed legacy PromptConfig behavior."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import PromptConfigRecord
from .ports import AdminPromptConfigsUnitOfWork
from .tables import PromptConfigRow


class SqlAlchemyAdminPromptConfigsUnitOfWork(AdminPromptConfigsUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session
        self._tracked: dict[str, PromptConfigRow] = {}

    def list_prompt_configs(
        self,
        *,
        user_id: str | None,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[PromptConfigRecord], int]:
        query = self._session.query(PromptConfigRow)
        if user_id:
            query = query.filter(PromptConfigRow.user_id == user_id)
        total = query.count()
        column = getattr(PromptConfigRow, sort_field, PromptConfigRow.id)
        ordering = desc(column) if sort_order == "desc" else asc(column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return tuple(_record(row) for row in rows), total

    def get_prompt_config(self, prompt_id: str) -> PromptConfigRecord | None:
        row = (
            self._session.query(PromptConfigRow)
            .filter(PromptConfigRow.id == prompt_id)
            .first()
        )
        if row is None:
            return None
        self._tracked[prompt_id] = row
        return _record(row)

    def add_prompt_config(
        self, prompt_id: str, user_id: str, values: Mapping[str, Any]
    ) -> None:
        row = PromptConfigRow(id=prompt_id, user_id=user_id, **dict(values))
        self._session.add(row)
        self._tracked[prompt_id] = row

    def update_prompt_config(
        self, prompt_id: str, changes: Mapping[str, Any]
    ) -> bool:
        row = (
            self._session.query(PromptConfigRow)
            .filter(PromptConfigRow.id == prompt_id)
            .first()
        )
        if row is None:
            return False
        for key, value in changes.items():
            if hasattr(row, key) and key not in ("id", "user_id"):
                setattr(row, key, value)
        self._tracked[prompt_id] = row
        return True

    def delete_prompt_config(self, prompt_id: str) -> bool:
        row = (
            self._session.query(PromptConfigRow)
            .filter(PromptConfigRow.id == prompt_id)
            .first()
        )
        if row is None:
            return False
        self._session.delete(row)
        return True

    def commit(self) -> None:
        self._session.commit()

    def refresh_prompt_config(self, prompt_id: str) -> PromptConfigRecord | None:
        row = self._tracked.get(prompt_id)
        if row is None:
            return None
        self._session.refresh(row)
        return _record(row)

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _record(row: Any) -> PromptConfigRecord:
    return PromptConfigRecord(
        id=row.id,
        user_id=row.user_id,
        category=row.category,
        name=row.name,
        system_prompt=row.system_prompt,
        user_prompt=row.user_prompt,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
