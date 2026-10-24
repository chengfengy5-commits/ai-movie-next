"""Standard-library SMTP transport with explicit delivery outcome boundaries."""

from __future__ import annotations

import re
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.policy import SMTP

from .domain import SmtpAccount, SmtpDeliveryRejected, SmtpDeliveryUnknown

DEFAULT_TIMEOUT_SECONDS = 10.0
_SMTP_ACCEPTED = 250
_SMTP_DATA_READY = 354


class StdlibSmtpTransport:
    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        ssl_context_factory: Callable[[], ssl.SSLContext] = ssl.create_default_context,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._ssl_context_factory = ssl_context_factory

    def send(
        self,
        account: SmtpAccount,
        *,
        recipient: str,
        subject: str,
        html_body: str,
    ) -> None:
        try:
            message = EmailMessage(policy=SMTP)
            message["From"] = account.username
            message["To"] = recipient
            message["Subject"] = subject
            message.set_content(html_body, subtype="html", charset="utf-8")
            payload = message.as_bytes(policy=SMTP)
            if not payload.endswith(b"\r\n"):
                payload += b"\r\n"
            context = self._ssl_context_factory()
        except Exception:
            raise SmtpDeliveryRejected() from None

        smtp: smtplib.SMTP | None = None
        data_started = False
        accepted = False
        try:
            if account.port == 465:
                smtp = smtplib.SMTP_SSL(
                    account.host,
                    account.port,
                    timeout=self._timeout_seconds,
                    context=context,
                )
            else:
                smtp = smtplib.SMTP(
                    account.host,
                    account.port,
                    timeout=self._timeout_seconds,
                )

            code, _ = smtp.ehlo()
            if code != _SMTP_ACCEPTED:
                raise SmtpDeliveryRejected()

            if account.port != 465:
                if not smtp.has_extn("starttls"):
                    raise SmtpDeliveryRejected()
                smtp.starttls(context=context)
                code, _ = smtp.ehlo()
                if code != _SMTP_ACCEPTED:
                    raise SmtpDeliveryRejected()

            smtp.login(account.username, account.password)

            code, _ = smtp.mail(account.username)
            if code != _SMTP_ACCEPTED:
                raise SmtpDeliveryRejected()

            code, _ = smtp.rcpt(recipient)
            if code not in {250, 251}:
                raise SmtpDeliveryRejected()

            smtp.putcmd("DATA")
            code, _ = smtp.getreply()
            if code != _SMTP_DATA_READY:
                raise SmtpDeliveryRejected()

            data_started = True
            smtp.send(_dot_stuff(payload) + b".\r\n")
            code, _ = smtp.getreply()
            if 400 <= code <= 599:
                raise SmtpDeliveryRejected()
            if code != _SMTP_ACCEPTED:
                raise SmtpDeliveryUnknown()
            accepted = True
        except SmtpDeliveryRejected:
            raise
        except Exception:
            if data_started:
                raise SmtpDeliveryUnknown() from None
            raise SmtpDeliveryRejected() from None
        finally:
            if smtp is not None:
                try:
                    if accepted:
                        smtp.quit()
                    else:
                        smtp.close()
                except Exception:
                    try:
                        smtp.close()
                    except Exception:
                        pass


def _dot_stuff(payload: bytes) -> bytes:
    return re.sub(br"(?m)^\.", b"..", payload)
