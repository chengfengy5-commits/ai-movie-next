"""SQLAlchemy adapter for SMTP-account transactions and daily-count reset."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session, registry

from .domain import SmtpAccountRecord
from .ports import AdminSmtpAccountUnitOfWork
from .tables import metadata, smtp_accounts

_mapper_registry = registry(metadata=metadata)


class SmtpAccountRow:
    pass


_mapper_registry.map_imperatively(SmtpAccountRow, smtp_accounts)


def _record(row: Any) -> SmtpAccountRecord:
    return SmtpAccountRecord(
        id=row.id,
        host=row.host,
        port=row.port,
        user=row.user,
        password=row.password,
        daily_sent=row.daily_sent,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyAdminSmtpAccountUnitOfWork(AdminSmtpAccountUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_accounts(self) -> list[SmtpAccountRecord]:
        rows = (
            self._session.query(SmtpAccountRow)
            .order_by(smtp_accounts.c.created_at.desc())
            .all()
        )
        return [_record(row) for row in rows]

    def get_account(self, account_id: str) -> SmtpAccountRow | None:
        return (
            self._session.query(SmtpAccountRow)
            .filter(smtp_accounts.c.id == account_id)
            .first()
        )

    def add_account(self, account_id: str, values: Mapping[str, Any]) -> SmtpAccountRow:
        row = SmtpAccountRow()
        row.id = account_id
        for field, value in values.items():
            setattr(row, field, value)
        self._session.add(row)
        return row

    def set_account_value(self, account: SmtpAccountRow, field: str, value: Any) -> None:
        setattr(account, field, value)

    def delete_account(self, account: SmtpAccountRow) -> None:
        self._session.delete(account)

    def commit(self) -> None:
        self._session.commit()

    def refresh(self, account: SmtpAccountRow) -> SmtpAccountRow:
        self._session.refresh(account)
        return account

    def reset_all_daily_counts(self) -> None:
        self._session.query(SmtpAccountRow).update({"daily_sent": 0})

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
