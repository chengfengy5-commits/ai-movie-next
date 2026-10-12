"""SQLAlchemy adapter for prompt configuration and ordering."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from .domain import PromptOrderRecord, PromptRecord
from .ports import PromptsUnitOfWorkFactory
from .tables import PromptConfigRow, SystemPromptRow, UserPromptOrderRow


class SqlAlchemyPromptsUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_personal_prompts(
        self, user_id: str, category: str | None
    ) -> Sequence[PromptRecord]:
        query = self._session.query(PromptConfigRow).filter(PromptConfigRow.user_id == user_id)
        if category:
            query = query.filter(PromptConfigRow.category == category)
        rows = query.order_by(PromptConfigRow.sort_order).all()
        return tuple(_personal_record(row) for row in rows)

    def list_system_prompts(
        self, category: str | None, *, include_inactive: bool
    ) -> Sequence[PromptRecord]:
        query = self._session.query(SystemPromptRow)
        if not include_inactive:
            query = query.filter(SystemPromptRow.is_active == True)  # noqa: E712
        if category:
            query = query.filter(SystemPromptRow.category == category)
        rows = query.order_by(SystemPromptRow.sort_order).all()
        return tuple(_system_record(row) for row in rows)

    def list_prompt_orders(
        self, user_id: str, category: str | None
    ) -> Sequence[PromptOrderRecord]:
        query = self._session.query(UserPromptOrderRow).filter(
            UserPromptOrderRow.user_id == user_id
        )
        if category:
            query = query.filter(UserPromptOrderRow.category == category)
        rows = query.order_by(UserPromptOrderRow.sort_order).all()
        return tuple(PromptOrderRecord(row.id, row.prompt_id, row.sort_order) for row in rows)

    def find_personal_prompt(self, prompt_id: str, user_id: str) -> PromptRecord | None:
        row = (
            self._session.query(PromptConfigRow)
            .filter(PromptConfigRow.id == prompt_id, PromptConfigRow.user_id == user_id)
            .first()
        )
        return _personal_record(row) if row is not None else None

    def find_any_personal_prompt(self, prompt_id: str) -> PromptRecord | None:
        row = self._session.query(PromptConfigRow).filter(PromptConfigRow.id == prompt_id).first()
        return _personal_record(row) if row is not None else None

    def find_system_prompt(self, prompt_id: str) -> PromptRecord | None:
        row = self._session.query(SystemPromptRow).filter(SystemPromptRow.id == prompt_id).first()
        return _system_record(row) if row is not None else None

    def max_personal_sort_order(self, category: str, user_id: str) -> int | None:
        return (
            self._session.query(func.max(PromptConfigRow.sort_order))
            .filter(
                PromptConfigRow.category == category,
                PromptConfigRow.user_id == user_id,
            )
            .scalar()
        )

    def max_system_sort_order(self, category: str) -> int | None:
        return (
            self._session.query(func.max(SystemPromptRow.sort_order))
            .filter(SystemPromptRow.category == category)
            .scalar()
        )

    def add_personal_prompt(self, prompt: PromptRecord) -> None:
        self._session.add(
            PromptConfigRow(
                id=prompt.id,
                user_id=prompt.user_id,
                category=prompt.category,
                name=prompt.name,
                system_prompt=prompt.system_prompt,
                user_prompt=prompt.user_prompt,
                sort_order=prompt.sort_order,
                created_at=prompt.created_at,
                updated_at=prompt.updated_at,
            )
        )

    def add_system_prompt(self, prompt: PromptRecord) -> None:
        self._session.add(
            SystemPromptRow(
                id=prompt.id,
                category=prompt.category,
                name=prompt.name,
                system_prompt=prompt.system_prompt,
                user_prompt=prompt.user_prompt,
                sort_order=prompt.sort_order,
                is_active=True if prompt.is_active is None else prompt.is_active,
                created_at=prompt.created_at,
                updated_at=prompt.updated_at,
            )
        )

    def update_personal_prompt(
        self, prompt_id: str, user_id: str, changes: Mapping[str, object]
    ) -> PromptRecord | None:
        row = (
            self._session.query(PromptConfigRow)
            .filter(PromptConfigRow.id == prompt_id, PromptConfigRow.user_id == user_id)
            .first()
        )
        if row is None:
            return None
        for field, value in changes.items():
            setattr(row, field, value)
        return _personal_record(row)

    def update_system_prompt(
        self, prompt_id: str, changes: Mapping[str, object]
    ) -> PromptRecord | None:
        row = self._session.query(SystemPromptRow).filter(SystemPromptRow.id == prompt_id).first()
        if row is None:
            return None
        for field, value in changes.items():
            setattr(row, field, value)
        return _system_record(row)

    def delete_personal_prompt(self, prompt_id: str, user_id: str) -> bool:
        row = (
            self._session.query(PromptConfigRow)
            .filter(PromptConfigRow.id == prompt_id, PromptConfigRow.user_id == user_id)
            .first()
        )
        if row is None:
            return False
        self._session.delete(row)
        return True

    def delete_any_personal_prompt(self, prompt_id: str) -> bool:
        row = self._session.query(PromptConfigRow).filter(PromptConfigRow.id == prompt_id).first()
        if row is None:
            return False
        self._session.delete(row)
        return True

    def delete_system_prompt(self, prompt_id: str) -> bool:
        row = self._session.query(SystemPromptRow).filter(SystemPromptRow.id == prompt_id).first()
        if row is None:
            return False
        self._session.delete(row)
        return True

    def update_personal_sort_order(
        self, prompt_id: str, category: str, user_id: str, sort_order: int
    ) -> None:
        self._session.query(PromptConfigRow).filter(
            PromptConfigRow.id == prompt_id,
            PromptConfigRow.category == category,
            PromptConfigRow.user_id == user_id,
        ).update({"sort_order": sort_order})

    def delete_prompt_orders(self, user_id: str, category: str) -> None:
        self._session.query(UserPromptOrderRow).filter(
            UserPromptOrderRow.user_id == user_id,
            UserPromptOrderRow.category == category,
        ).delete(synchronize_session=False)

    def delete_all_prompt_order_references(self, prompt_id: str) -> None:
        self._session.query(UserPromptOrderRow).filter(
            UserPromptOrderRow.prompt_id == prompt_id
        ).delete(synchronize_session=False)

    def add_prompt_order(self, order: PromptOrderRecord, *, user_id: str, category: str) -> None:
        self._session.add(
            UserPromptOrderRow(
                id=order.id,
                user_id=user_id,
                prompt_id=order.prompt_id,
                category=category,
                sort_order=order.sort_order,
            )
        )

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def refresh_personal_prompt(self, prompt_id: str, user_id: str) -> PromptRecord | None:
        row = (
            self._session.query(PromptConfigRow)
            .populate_existing()
            .filter(PromptConfigRow.id == prompt_id, PromptConfigRow.user_id == user_id)
            .first()
        )
        return _personal_record(row) if row is not None else None

    def refresh_system_prompt(self, prompt_id: str) -> PromptRecord | None:
        row = (
            self._session.query(SystemPromptRow)
            .populate_existing()
            .filter(SystemPromptRow.id == prompt_id)
            .first()
        )
        return _system_record(row) if row is not None else None

    def close(self) -> None:
        self._session.close()


def prompts_unit_of_work_factory(
    session_factory: Callable[[], Session],
) -> PromptsUnitOfWorkFactory:
    """Bind the adapter to a supplied session factory without opening a session."""

    def create_unit_of_work() -> SqlAlchemyPromptsUnitOfWork:
        return SqlAlchemyPromptsUnitOfWork(session_factory())

    return create_unit_of_work


def _personal_record(row: Any) -> PromptRecord:
    return PromptRecord(
        id=row.id,
        user_id=row.user_id,
        category=row.category,
        name=row.name,
        system_prompt=row.system_prompt,
        user_prompt=row.user_prompt,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
        is_system=False,
        is_active=getattr(row, "is_active", None),
    )


def _system_record(row: Any) -> PromptRecord:
    return PromptRecord(
        id=row.id,
        user_id="system",
        category=row.category,
        name=row.name,
        system_prompt=row.system_prompt,
        user_prompt=row.user_prompt,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
        is_system=True,
        is_active=bool(row.is_active),
    )
