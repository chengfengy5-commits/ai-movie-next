"""Explicit runtime adapters; importing this module performs no configuration I/O."""

from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .email_codes import InMemoryEmailCodeStore
from .passwords import BcryptPasswordHasher
from .ports import EmailCodeStore, FixedWindowLimiter, PasswordHasher, TokenCodec
from .rate_limit import InMemoryFixedWindowLimiter
from .tokens import JoseTokenCodec


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _generate_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _new_id() -> str:
    from uuid import uuid4

    return str(uuid4())


@dataclass(slots=True)
class AuthenticationRuntime:
    """All credentials and external effects are provided by the app caller."""

    session_factory: Callable[[], Any] | None = None
    secret_key: str | None = None
    algorithm: str = "HS256"
    access_token_ttl_minutes: int = 14 * 24 * 60
    verification_email_sender: Callable[[str, str], bool] | None = None
    password_reset_sender: Callable[[str, str, str], bool] | None = None
    frontend_url: str | Callable[[], str] | None = None
    password_hasher: PasswordHasher | None = None
    token_codec: TokenCodec | None = None
    email_code_store: EmailCodeStore | None = field(default_factory=InMemoryEmailCodeStore)
    rate_limiter: FixedWindowLimiter | None = field(default_factory=InMemoryFixedWindowLimiter)
    clock: Callable[[], datetime] = _utcnow
    epoch: Callable[[], float] = time.time
    code_generator: Callable[[], str] = _generate_code
    id_factory: Callable[[], str] = _new_id

    def get_password_hasher(self) -> PasswordHasher:
        if self.password_hasher is None:
            self.password_hasher = BcryptPasswordHasher()
        return self.password_hasher

    def get_token_codec(self) -> TokenCodec | None:
        if self.token_codec is None and self.secret_key:
            self.token_codec = JoseTokenCodec(self.secret_key, algorithm=self.algorithm)
        return self.token_codec

    def frontend_base_url(self) -> str | None:
        value = self.frontend_url
        return value() if callable(value) else value
