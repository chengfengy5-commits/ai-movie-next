"""SQLAlchemy adapter for legacy administrator credit queries."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import CreditLogRecord, UserCreditRecord
from .ports import AdminCreditQueriesUnitOfWork
from .tables import CreditLogRow, UserCreditRow


class SqlAlchemyAdminCreditQueriesUnitOfWork(AdminCreditQueriesUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_user_credits(
        self,
        *,
        skip: int,
        limit: int,
        sort_field: str,
        sort_order: str,
    ) -> tuple[Sequence[UserCreditRecord], int]:
        query = self._session.query(UserCreditRow)
        total = query.count()
        sort_column = getattr(UserCreditRow, sort_field, UserCreditRow.id)
        ordering = desc(sort_column) if sort_order == "desc" else asc(sort_column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return tuple(_user_credit_record(row) for row in rows), total

    def get_user_credit(self, credit_id: str) -> UserCreditRecord | None:
        row = self._load_user_credit(credit_id)
        return _user_credit_record(row) if row is not None else None

    def load_user_credit_for_update(self, credit_id: str) -> UserCreditRow | None:
        return self._load_user_credit(credit_id)

    def set_user_credit_amount(self, row: UserCreditRow, credits: int) -> None:
        row.credits = credits

    def refresh_user_credit(self, row: UserCreditRow) -> UserCreditRecord:
        self._session.refresh(row)
        return _user_credit_record(row)

    def list_credit_logs(
        self,
        *,
        skip: int,
        limit: int,
        sort_field: str,
        sort_order: str,
        user_id: str | None,
    ) -> tuple[Sequence[CreditLogRecord], int]:
        query = self._session.query(CreditLogRow)
        if user_id:
            query = query.filter(CreditLogRow.user_id == user_id)
        total = query.count()
        sort_column = getattr(CreditLogRow, sort_field, CreditLogRow.id)
        ordering = desc(sort_column) if sort_order == "desc" else asc(sort_column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return tuple(_credit_log_record(row) for row in rows), total

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()

    def _load_user_credit(self, credit_id: str) -> UserCreditRow | None:
        return (
            self._session.query(UserCreditRow)
            .filter(UserCreditRow.id == credit_id)
            .first()
        )


def _user_credit_record(row: Any) -> UserCreditRecord:
    return UserCreditRecord(
        id=row.id,
        user_id=row.user_id,
        credits=row.credits,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _credit_log_record(row: Any) -> CreditLogRecord:
    return CreditLogRecord(
        id=row.id,
        user_id=row.user_id,
        amount=row.amount,
        balance_after=row.balance_after,
        type=row.type,
        description=row.description,
        task_id=row.task_id,
        created_at=row.created_at,
    )
