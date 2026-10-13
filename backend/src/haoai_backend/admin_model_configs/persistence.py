"""Private SQLAlchemy mappings and adapter for legacy model configuration rows."""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .ports import AdminModelConfigUnitOfWork
from .tables import ModelConfigRow


class SqlAlchemyAdminModelConfigUnitOfWork(AdminModelConfigUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_model_configs(
        self,
        *,
        skip: int,
        limit: int,
        sort_field: str,
        sort_order: str,
        user_id: str | None,
    ) -> tuple[list[ModelConfigRow], int]:
        query = self._session.query(ModelConfigRow)
        if user_id:
            query = query.filter(ModelConfigRow.user_id == user_id)
        total = query.count()
        sort_column = getattr(ModelConfigRow, sort_field, ModelConfigRow.id)
        ordering = desc(sort_column) if sort_order == "desc" else asc(sort_column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return rows, total

    def get_model_config(self, model_id: str) -> ModelConfigRow | None:
        return (
            self._session.query(ModelConfigRow)
            .filter(ModelConfigRow.id == model_id)
            .first()
        )

    def add_model_config(
        self, model_id: str, values: Mapping[str, Any]
    ) -> ModelConfigRow:
        row = ModelConfigRow(id=model_id, **dict(values))
        self._session.add(row)
        return row

    def update_model_config(
        self, model_id: str, changes: Mapping[str, Any]
    ) -> ModelConfigRow | None:
        row = (
            self._session.query(ModelConfigRow)
            .filter(ModelConfigRow.id == model_id)
            .first()
        )
        if row is None:
            return None
        for key, value in changes.items():
            if hasattr(row, key) and key not in ("id", "user_id"):
                setattr(row, key, value)
        return row

    def delete_model_config(self, model_id: str) -> ModelConfigRow | None:
        row = (
            self._session.query(ModelConfigRow)
            .filter(ModelConfigRow.id == model_id)
            .first()
        )
        if row is None:
            return None
        self._session.delete(row)
        return row

    def commit(self) -> None:
        self._session.commit()

    def refresh(self, row: ModelConfigRow) -> None:
        self._session.refresh(row)

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
