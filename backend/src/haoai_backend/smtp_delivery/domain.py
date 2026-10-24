"""SMTP delivery records, outcomes, and configuration rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class StoredSmtpAccount:
    id: str
    host: str
    port: int
    username: str
    encrypted_password: str = field(repr=False)
    daily_sent: int = 0


@dataclass(frozen=True, slots=True)
class SmtpAccount:
    id: str
    host: str
    port: int
    username: str
    password: str = field(repr=False)


class SmtpDeliveryError(Exception):
    """Base type for delivery outcomes that callers may handle safely."""


class SmtpDeliveryUnavailable(SmtpDeliveryError):
    """Required local SMTP configuration is unavailable."""


class SmtpDeliveryRejected(SmtpDeliveryError):
    """The SMTP peer explicitly declined delivery before accepting DATA."""


class SmtpDeliveryUnknown(SmtpDeliveryError):
    """The SMTP peer may have accepted DATA, but its final reply was not read."""


def valid_frontend_url(value: object) -> str | None:
    """Return a safe HTTP(S) base URL from the legacy system-config value."""

    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        return None
    if "?" in value or "#" in value or "\\" in value:
        return None

    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        port = parsed.port
        if port == 0:
            return None
    except ValueError:
        return None

    return value.rstrip("/")
