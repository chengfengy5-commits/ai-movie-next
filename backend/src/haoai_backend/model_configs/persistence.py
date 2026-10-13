"""SQLAlchemy adapter for the fixed legacy model-configuration tables."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from .domain import ModelConfigRecord, ModelOrderRecord
from .ports import ModelConfigsUnitOfWorkFactory
from .tables import ModelConfigRow, SystemModelRow, UserModelOrderRow


class SqlAlchemyModelConfigsUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._personal_rows: dict[tuple[str, str], ModelConfigRow] = {}
        self._system_rows: dict[str, SystemModelRow] = {}

    def list_personal_models(
        self, user_id: str, category: str | None
    ) -> Sequence[ModelConfigRecord]:
        query = self._session.query(ModelConfigRow).filter(
            ModelConfigRow.user_id == user_id
        )
        if category:
            query = query.filter(ModelConfigRow.category == category)
        rows = query.order_by(ModelConfigRow.sort_order).all()
        return tuple(_personal_record(row) for row in rows)

    def list_system_models(
        self, category: str | None, *, include_inactive: bool
    ) -> Sequence[ModelConfigRecord]:
        query = self._session.query(SystemModelRow)
        if not include_inactive:
            query = query.filter(SystemModelRow.is_active == True)  # noqa: E712
        if category:
            query = query.filter(SystemModelRow.category == category)
        rows = query.order_by(SystemModelRow.sort_order).all()
        return tuple(_system_record(row) for row in rows)

    def list_model_orders(
        self, user_id: str, category: str | None
    ) -> Sequence[ModelOrderRecord]:
        query = self._session.query(UserModelOrderRow).filter(
            UserModelOrderRow.user_id == user_id
        )
        if category:
            query = query.filter(UserModelOrderRow.category == category)
        rows = query.order_by(UserModelOrderRow.sort_order).all()
        return tuple(
            ModelOrderRecord(
                id=row.id,
                user_id=row.user_id,
                model_id=row.model_id,
                category=row.category,
                sort_order=row.sort_order,
            )
            for row in rows
        )

    def find_personal_model(
        self, model_id: str, user_id: str
    ) -> ModelConfigRecord | None:
        row = (
            self._session.query(ModelConfigRow)
            .filter(
                ModelConfigRow.id == model_id,
                ModelConfigRow.user_id == user_id,
            )
            .first()
        )
        if row is not None:
            self._personal_rows[(model_id, user_id)] = row
        return _personal_record(row) if row is not None else None

    def find_any_personal_model(self, model_id: str) -> ModelConfigRecord | None:
        row = (
            self._session.query(ModelConfigRow)
            .filter(ModelConfigRow.id == model_id)
            .first()
        )
        if row is not None:
            self._personal_rows[(model_id, row.user_id)] = row
        return _personal_record(row) if row is not None else None

    def find_system_model(self, model_id: str) -> ModelConfigRecord | None:
        row = (
            self._session.query(SystemModelRow)
            .filter(SystemModelRow.id == model_id)
            .first()
        )
        if row is not None:
            self._system_rows[model_id] = row
        return _system_record(row) if row is not None else None

    def max_personal_sort_order(self, category: str, user_id: str) -> int | None:
        return (
            self._session.query(func.max(ModelConfigRow.sort_order))
            .filter(
                ModelConfigRow.category == category,
                ModelConfigRow.user_id == user_id,
            )
            .scalar()
        )

    def max_system_sort_order(self, category: str) -> int | None:
        return (
            self._session.query(func.max(SystemModelRow.sort_order))
            .filter(SystemModelRow.category == category)
            .scalar()
        )

    def add_personal_model(self, model: ModelConfigRecord) -> None:
        row = ModelConfigRow(
            id=model.id,
            user_id=model.user_id,
            category=model.category,
            name=model.name,
            provider=model.provider,
            api_key=model.api_key,
            model=model.model,
            base_url=model.base_url,
            sort_order=model.sort_order,
            params=model.params,
        )
        self._personal_rows[(model.id, model.user_id)] = row
        self._session.add(row)

    def add_system_model(self, model: ModelConfigRecord) -> None:
        row = SystemModelRow(
            id=model.id,
            category=model.category,
            name=model.name,
            provider=model.provider,
            api_key=model.api_key,
            model=model.model,
            base_url=model.base_url,
            cost_per_call=(
                0 if model.cost_per_call is None else model.cost_per_call
            ),
            description=model.description,
            is_active=True if model.is_active is None else model.is_active,
            sort_order=model.sort_order,
            params=model.params,
        )
        self._system_rows[model.id] = row
        self._session.add(row)

    def update_personal_model(
        self, model_id: str, user_id: str, changes: Mapping[str, object]
    ) -> ModelConfigRecord | None:
        row = (
            self._session.query(ModelConfigRow)
            .filter(
                ModelConfigRow.id == model_id,
                ModelConfigRow.user_id == user_id,
            )
            .first()
        )
        if row is None:
            return None
        self._personal_rows[(model_id, user_id)] = row
        for field, value in changes.items():
            setattr(row, field, value)
        return _personal_record(row)

    def update_system_model(
        self, model_id: str, changes: Mapping[str, object]
    ) -> ModelConfigRecord | None:
        row = (
            self._session.query(SystemModelRow)
            .filter(SystemModelRow.id == model_id)
            .first()
        )
        if row is None:
            return None
        self._system_rows[model_id] = row
        for field, value in changes.items():
            setattr(row, field, value)
        return _system_record(row)

    def set_personal_params(
        self, model_id: str, user_id: str, params: str | None
    ) -> None:
        row = self._personal_rows.get((model_id, user_id))
        if row is None:
            raise RuntimeError("个人模型更新必须复用已读取的 ORM 对象")
        row.params = params

    def set_system_params(self, model_id: str, params: str | None) -> None:
        row = self._system_rows.get(model_id)
        if row is None:
            raise RuntimeError("系统模型更新必须复用已读取的 ORM 对象")
        row.params = params

    def delete_personal_model(self, model_id: str, user_id: str) -> bool:
        row = (
            self._session.query(ModelConfigRow)
            .filter(
                ModelConfigRow.id == model_id,
                ModelConfigRow.user_id == user_id,
            )
            .first()
        )
        if row is None:
            return False
        self._session.delete(row)
        return True

    def delete_any_personal_model(self, model_id: str) -> bool:
        row = next(
            (
                candidate
                for (candidate_id, _user_id), candidate in self._personal_rows.items()
                if candidate_id == model_id
            ),
            None,
        )
        if row is None:
            row = (
                self._session.query(ModelConfigRow)
                .filter(ModelConfigRow.id == model_id)
                .first()
            )
        if row is None:
            return False
        self._personal_rows[(model_id, row.user_id)] = row
        self._session.delete(row)
        return True

    def delete_system_model(self, model_id: str) -> bool:
        row = self._system_rows.get(model_id)
        if row is None:
            row = (
                self._session.query(SystemModelRow)
                .filter(SystemModelRow.id == model_id)
                .first()
            )
        if row is None:
            return False
        self._system_rows[model_id] = row
        self._session.delete(row)
        return True

    def update_personal_sort_order(
        self, model_id: str, category: str, user_id: str, sort_order: int
    ) -> None:
        self._session.query(ModelConfigRow).filter(
            ModelConfigRow.id == model_id,
            ModelConfigRow.category == category,
            ModelConfigRow.user_id == user_id,
        ).update({"sort_order": sort_order})

    def delete_model_orders(self, user_id: str, category: str) -> None:
        self._session.query(UserModelOrderRow).filter(
            UserModelOrderRow.user_id == user_id,
            UserModelOrderRow.category == category,
        ).delete()

    def delete_all_model_order_references(self, model_id: str) -> None:
        self._session.query(UserModelOrderRow).filter(
            UserModelOrderRow.model_id == model_id
        ).delete()

    def add_model_order(self, order: ModelOrderRecord) -> None:
        self._session.add(
            UserModelOrderRow(
                id=order.id,
                user_id=order.user_id,
                model_id=order.model_id,
                category=order.category,
                sort_order=order.sort_order,
            )
        )

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def refresh_personal_model(
        self, model_id: str, user_id: str
    ) -> ModelConfigRecord | None:
        row = self._personal_rows.get((model_id, user_id))
        if row is None:
            return None
        self._session.refresh(row)
        return _personal_record(row)

    def refresh_system_model(self, model_id: str) -> ModelConfigRecord | None:
        row = self._system_rows.get(model_id)
        if row is None:
            return None
        self._session.refresh(row)
        return _system_record(row)

    def close(self) -> None:
        self._session.close()


def model_configs_unit_of_work_factory(
    session_factory: Callable[[], Session],
) -> ModelConfigsUnitOfWorkFactory:
    """Bind the adapter without opening a database session."""

    def create_unit_of_work() -> SqlAlchemyModelConfigsUnitOfWork:
        return SqlAlchemyModelConfigsUnitOfWork(session_factory())

    return create_unit_of_work


def _personal_record(row: Any) -> ModelConfigRecord:
    transient_values = {
        name: getattr(row, name)
        for name in ("cost_per_call", "is_active")
        if hasattr(row, name)
    }
    return ModelConfigRecord(
        id=row.id,
        user_id=row.user_id,
        category=row.category,
        name=row.name,
        provider=row.provider,
        api_key=row.api_key,
        model=row.model,
        base_url=row.base_url,
        sort_order=row.sort_order,
        params=row.params,
        created_at=row.created_at,
        updated_at=row.updated_at,
        is_system=False,
        transient_values=transient_values,
    )


def _system_record(row: Any) -> ModelConfigRecord:
    return ModelConfigRecord(
        id=row.id,
        user_id="system",
        category=row.category,
        name=row.name,
        provider=row.provider,
        api_key=row.api_key,
        model=row.model,
        base_url=row.base_url,
        sort_order=row.sort_order,
        params=row.params,
        created_at=row.created_at,
        updated_at=row.updated_at,
        is_system=True,
        cost_per_call=row.cost_per_call,
        description=row.description,
        is_active=bool(row.is_active),
    )
