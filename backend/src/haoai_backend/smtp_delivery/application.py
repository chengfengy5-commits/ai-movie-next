"""Select current SMTP configuration and deliver authentication mail."""

from __future__ import annotations

import html
import logging

from haoai_backend.admin_smtp_accounts.ports import Cipher

from .domain import (
    SmtpAccount,
    SmtpDeliveryRejected,
    SmtpDeliveryUnknown,
    SmtpDeliveryUnavailable,
    StoredSmtpAccount,
    valid_frontend_url,
)
from .ports import MailTransport, SmtpDeliveryRepository

logger = logging.getLogger(__name__)


class SmtpDeliveryService:
    def __init__(
        self,
        repository: SmtpDeliveryRepository | None,
        transport: MailTransport,
        cipher: Cipher,
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._cipher = cipher

    def frontend_base_url(self) -> str | None:
        if self._repository is None:
            return None
        try:
            value = self._repository.load_frontend_url()
        except Exception:
            logger.warning("smtp_delivery_frontend_url_unavailable")
            return None
        return valid_frontend_url(value)

    def ensure_sender_ready(self) -> None:
        self._load_sender()

    def send_verification_code(self, recipient: str, code: str) -> bool:
        escaped_code = html.escape(code)
        body = (
            '<div style="max-width:400px;margin:0 auto;padding:20px;'
            'font-family:Arial,sans-serif;background:#fff">'
            '<p style="color:#666;font-size:14px;text-align:center">'
            "你的注册验证码</p>"
            '<div style="background:#f5f5f5;border-radius:8px;padding:24px;'
            'text-align:center">'
            f'<span style="font-size:36px;font-weight:bold;letter-spacing:8px">{escaped_code}</span>'
            "</div>"
            '<p style="color:#999;font-size:12px;text-align:center">'
            "验证码 5 分钟内有效，请勿泄露给他人</p></div>"
        )
        return self.send(
            recipient,
            subject="Hao AI - 邮箱验证码",
            html_body=body,
        )

    def send(
        self,
        recipient: str,
        *,
        subject: str,
        html_body: str,
    ) -> bool:
        account = self._load_sender()

        try:
            self._transport.send(
                account,
                recipient=recipient,
                subject=subject,
                html_body=html_body,
            )
        except SmtpDeliveryRejected:
            logger.warning("smtp_delivery_not_accepted")
            return False
        except SmtpDeliveryUnknown:
            logger.warning("smtp_delivery_outcome_unknown")
            raise
        except Exception:
            logger.warning("smtp_delivery_transport_error")
            raise SmtpDeliveryUnknown() from None

        repository = self._repository
        assert repository is not None
        try:
            repository.increment_daily_sent(account.id)
        except Exception:
            logger.warning("smtp_delivery_counter_update_failed")
        return True

    def _load_sender(self) -> SmtpAccount:
        repository = self._repository
        if repository is None:
            raise SmtpDeliveryUnavailable()
        try:
            accounts = repository.list_active_accounts()
        except Exception:
            logger.warning("smtp_delivery_accounts_unavailable")
            raise SmtpDeliveryUnavailable() from None
        if not accounts:
            raise SmtpDeliveryUnavailable()

        stored = min(accounts, key=lambda account: (account.daily_sent, account.id))
        account = self._decrypt_account(stored)
        if account is None:
            raise SmtpDeliveryUnavailable()
        return account

    def _decrypt_account(self, stored: StoredSmtpAccount) -> SmtpAccount | None:
        if not stored.host or not stored.username or not 0 < stored.port <= 65535:
            return None
        try:
            password = self._cipher.decrypt(stored.encrypted_password)
        except Exception:
            logger.warning("smtp_delivery_credentials_unavailable")
            return None
        if not password:
            return None
        return SmtpAccount(
            id=stored.id,
            host=stored.host,
            port=stored.port,
            username=stored.username,
            password=password,
        )
