"""Private SQLAlchemy mappings and adapter for legacy redemption-code rows."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import NewRedemptionCode, RedemptionCode
from .ports import AdminRedemptionCodesUnitOfWork
from .tables import RedemptionCodeRow


def _record(row: RedemptionCodeRow) -> RedemptionCode:
    return RedemptionCode(
        id=row.id,
        code=row.code,
        type=row.type,
        value=row.value,
        is_used=row.is_used,
        used_by=row.used_by,
        used_at=row.used_at,
        created_by=row.created_by,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


class SqlAlchemyAdminRedemptionCodesUnitOfWork(AdminRedemptionCodesUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session
        self._added: dict[str, RedemptionCodeRow] = {}

    def list_codes(
        self,
        *,
        code: str | None,
        code_type: str | None,
        is_used: str | None,
        value: int | None,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[tuple[RedemptionCode, ...], int]:
        query = self._session.query(RedemptionCodeRow)
        if code:
            query = query.filter(RedemptionCodeRow.code.like(f"%{code}%"))
        if code_type:
            query = query.filter(RedemptionCodeRow.type == code_type)
        if is_used is not None:
            bool_value = is_used.lower() in ("true", "1", "yes")
            query = query.filter(RedemptionCodeRow.is_used == bool_value)
        if value is not None:
            query = query.filter(RedemptionCodeRow.value == value)

        total = query.count()
        column = getattr(RedemptionCodeRow, sort_field, RedemptionCodeRow.id)
        ordering = desc(column) if sort_order == "desc" else asc(column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return tuple(_record(row) for row in rows), total

    def load_code(self, code_id: str) -> RedemptionCode | None:
        row = (
            self._session.query(RedemptionCodeRow)
            .filter(RedemptionCodeRow.id == code_id)
            .first()
        )
        return None if row is None else _record(row)

    def code_exists(self, code: str) -> bool:
        return (
            self._session.query(RedemptionCodeRow)
            .filter(RedemptionCodeRow.code == code)
            .first()
            is not None
        )

    def add_code(self, code: NewRedemptionCode) -> None:
        row = RedemptionCodeRow(
            id=code.id,
            code=code.code,
            type=code.type,
            value=code.value,
            is_used=False,
            created_by=code.created_by,
            expires_at=code.expires_at,
        )
        self._added[row.id] = row
        self._session.add(row)

    def delete_code(self, code_id: str) -> bool:
        row = (
            self._session.query(RedemptionCodeRow)
            .filter(RedemptionCodeRow.id == code_id)
            .first()
        )
        if row is None:
            return False
        self._session.delete(row)
        return True

    def commit(self) -> None:
        self._session.commit()

    def refresh_code(self, code_id: str) -> RedemptionCode | None:
        row = self._added.get(code_id)
        if row is None:
            return None
        self._session.refresh(row)
        return _record(row)

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def admin_redemption_codes_uow_factory(
    session_factory: Callable[[], Session],
) -> Callable[[], SqlAlchemyAdminRedemptionCodesUnitOfWork]:
    def create() -> SqlAlchemyAdminRedemptionCodesUnitOfWork:
        return SqlAlchemyAdminRedemptionCodesUnitOfWork(session_factory())

    return create
