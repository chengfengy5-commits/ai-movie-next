"""SQLAlchemy adapter for the existing SMTP and system-config tables."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.admin_smtp_accounts.ports import DailyReset
from haoai_backend.admin_smtp_accounts.tables import smtp_accounts
from haoai_backend.admin_system_config.tables import system_configs

from .domain import StoredSmtpAccount

SessionFactory = Callable[[], Session]


class SqlAlchemySmtpDeliveryRepository:
    def __init__(
        self,
        session_factory: SessionFactory,
        daily_reset: DailyReset,
    ) -> None:
        self._session_factory = session_factory
        self._daily_reset = daily_reset

    def list_active_accounts(self) -> list[StoredSmtpAccount]:
        self._daily_reset.reset_if_needed()
        session = self._session_factory()
        try:
            rows = session.execute(
                select(
                    smtp_accounts.c.id,
                    smtp_accounts.c.host,
                    smtp_accounts.c.port,
                    smtp_accounts.c.user,
                    smtp_accounts.c.password,
                    smtp_accounts.c.daily_sent,
                ).where(smtp_accounts.c.is_active == 1)
            ).mappings()
            return [
                StoredSmtpAccount(
                    id=str(row["id"]),
                    host=str(row["host"] or ""),
                    port=int(row["port"]),
                    username=str(row["user"] or ""),
                    encrypted_password=str(row["password"] or ""),
                    daily_sent=int(row["daily_sent"] or 0),
                )
                for row in rows
            ]
        finally:
            session.close()

    def increment_daily_sent(self, account_id: str) -> None:
        self._daily_reset.reset_if_needed()
        session = self._session_factory()
        try:
            result = session.execute(
                update(smtp_accounts)
                .where(smtp_accounts.c.id == account_id)
                .values(daily_sent=smtp_accounts.c.daily_sent + 1)
            )
            if result.rowcount != 1:
                raise RuntimeError("smtp account no longer exists")
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def load_frontend_url(self) -> str | None:
        session = self._session_factory()
        try:
            return session.execute(
                select(system_configs.c.frontend_url).limit(1)
            ).scalar_one_or_none()
        finally:
            session.close()
