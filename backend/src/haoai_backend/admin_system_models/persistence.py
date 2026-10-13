"""SQLAlchemy adapter for the fixed legacy system_models table."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from .domain import SystemModelRecord
from .ports import AdminSystemModelUnitOfWork
from .tables import SystemModelRow


class SqlAlchemyAdminSystemModelUnitOfWork(AdminSystemModelUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_models(
        self,
        *,
        category: str | None,
        provider: str | None,
        is_active: bool | None,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[SystemModelRecord], int]:
        query = self._session.query(SystemModelRow)
        if category:
            query = query.filter(SystemModelRow.category == category)
        if provider:
            query = query.filter(SystemModelRow.provider == provider)
        if is_active is not None:
            query = query.filter(SystemModelRow.is_active == is_active)
        total = query.count()
        rows = (
            query.order_by(SystemModelRow.sort_order.asc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return tuple(_record(row) for row in rows), total

    def get_model(self, model_id: str) -> SystemModelRecord | None:
        row = (
            self._session.query(SystemModelRow)
            .filter(SystemModelRow.id == model_id)
            .first()
        )
        return _record(row) if row is not None else None

    def add_model(self, model_id: str, values: Mapping[str, Any]) -> object:
        row = SystemModelRow(id=model_id, **dict(values))
        self._session.add(row)
        return row

    def load_model_for_update(self, model_id: str) -> object | None:
        return (
            self._session.query(SystemModelRow)
            .filter(SystemModelRow.id == model_id)
            .first()
        )

    def set_model_values(self, model: object, changes: Mapping[str, Any]) -> None:
        for field, value in changes.items():
            setattr(model, field, value)

    def refresh_model(self, model: object) -> SystemModelRecord:
        self._session.refresh(model)
        return _record(model)

    def delete_model(self, model_id: str) -> bool:
        row = (
            self._session.query(SystemModelRow)
            .filter(SystemModelRow.id == model_id)
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


def _record(row: Any) -> SystemModelRecord:
    fields = (
        "id",
        "category",
        "name",
        "provider",
        "api_key",
        "model",
        "base_url",
        "cost_per_call",
        "description",
        "is_active",
        "sort_order",
        "params",
        "created_at",
        "updated_at",
    )
    if isinstance(row, Mapping):
        values = {field: row[field] for field in fields}
    else:
        values = {field: getattr(row, field) for field in fields}
    return SystemModelRecord(**values)
