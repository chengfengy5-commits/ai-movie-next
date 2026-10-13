"""SQLAlchemy unit of work for Zhangyuge's legacy credential fields."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from .domain import SystemModelCredential
from .ports import ZhangyugeUnitOfWork
from .tables import SystemConfigRow, SystemModelRow


class SqlAlchemyZhangyugeUnitOfWork(ZhangyugeUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def active_model_credentials(self) -> Sequence[SystemModelCredential]:
        rows = (
            self._session.query(SystemModelRow)
            .filter(
                SystemModelRow.provider == "zhangyuge",
                SystemModelRow.is_active == True,  # noqa: E712
            )
            .all()
        )
        return tuple(
            SystemModelCredential(api_key=row.api_key, base_url=row.base_url)
            for row in rows
        )

    def first_system_config(self) -> SystemConfigRow | None:
        return self._session.query(SystemConfigRow).first()

    def add_system_config(self, config_id: str) -> SystemConfigRow:
        row = SystemConfigRow(id=config_id)
        self._session.add(row)
        return row

    def set_billing_pat(self, config: object, ciphertext: str) -> None:
        setattr(config, "zhangyuge_billing_pat", ciphertext)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
