"""SQLAlchemy adapter for provider-quota account transactions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session, registry

from .domain import ProviderQuotaAccountRecord
from .ports import ProviderQuotaUnitOfWork
from .tables import metadata, provider_quota_accounts

_mapper_registry = registry(metadata=metadata)


class ProviderQuotaAccountRow:
    pass


_mapper_registry.map_imperatively(ProviderQuotaAccountRow, provider_quota_accounts)


def _record(row: Any) -> ProviderQuotaAccountRecord:
    return ProviderQuotaAccountRecord(
        id=row.id,
        name=row.name,
        provider_type=row.provider_type,
        base_url=row.base_url,
        username=row.username,
        password=row.password,
        is_active=row.is_active,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyProviderQuotaUnitOfWork(ProviderQuotaUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_accounts(self) -> list[ProviderQuotaAccountRecord]:
        rows = (
            self._session.query(ProviderQuotaAccountRow)
            .order_by(
                provider_quota_accounts.c.sort_order.asc(),
                provider_quota_accounts.c.created_at.asc(),
            )
            .all()
        )
        return [_record(row) for row in rows]

    def get_account(self, account_id: str) -> ProviderQuotaAccountRow | None:
        return (
            self._session.query(ProviderQuotaAccountRow)
            .filter(provider_quota_accounts.c.id == account_id)
            .first()
        )

    def add_account(
        self,
        account_id: str,
        values: Mapping[str, Any],
    ) -> ProviderQuotaAccountRow:
        row = ProviderQuotaAccountRow()
        row.id = account_id
        for field, value in values.items():
            setattr(row, field, value)
        self._session.add(row)
        return row

    def set_account_value(
        self,
        account: ProviderQuotaAccountRow,
        field: str,
        value: Any,
    ) -> None:
        setattr(account, field, value)

    def delete_account(self, account: ProviderQuotaAccountRow) -> None:
        self._session.delete(account)

    def commit(self) -> None:
        self._session.commit()

    def refresh(self, account: ProviderQuotaAccountRow) -> ProviderQuotaAccountRow:
        self._session.refresh(account)
        return account

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
