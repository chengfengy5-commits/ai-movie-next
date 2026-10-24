"""Ports for the bounded SMTP delivery path."""

from __future__ import annotations

from typing import Protocol

from .domain import SmtpAccount, StoredSmtpAccount


class SmtpDeliveryRepository(Protocol):
    def list_active_accounts(self) -> list[StoredSmtpAccount]: ...

    def increment_daily_sent(self, account_id: str) -> None: ...

    def load_frontend_url(self) -> str | None: ...


class MailTransport(Protocol):
    def send(
        self,
        account: SmtpAccount,
        *,
        recipient: str,
        subject: str,
        html_body: str,
    ) -> None: ...
