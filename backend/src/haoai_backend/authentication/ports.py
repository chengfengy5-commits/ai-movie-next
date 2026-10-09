"""Small ports used by authentication use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from .domain import UserCreditRecord, UserRecord, UserSessionRecord


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password: str, encoded: str) -> bool: ...


class TokenCodec(Protocol):
    def encode(self, claims: Mapping[str, Any]) -> str: ...

    def decode(self, token: str) -> dict[str, Any]: ...


class AuthenticationUnitOfWork(Protocol):
    def load_user_by_id(self, user_id: str) -> UserRecord | None: ...

    def load_user_by_username(self, username: str) -> UserRecord | None: ...

    def load_user_by_email(self, email: str) -> UserRecord | None: ...

    def has_superuser(self) -> bool: ...

    def insert_user(self, user: UserRecord) -> UserRecord: ...

    def update_password(
        self,
        user_id: str,
        hashed_password: str,
        *,
        password_updated_at: datetime | None,
        update_password_timestamp: bool,
    ) -> bool: ...

    def list_active_sessions(self, user_id: str) -> list[UserSessionRecord]: ...

    def list_sessions(self, user_id: str) -> list[UserSessionRecord]: ...

    def load_session_by_jti(self, user_id: str, jti: str) -> UserSessionRecord | None: ...

    def load_session_by_id(self, user_id: str, session_id: str) -> UserSessionRecord | None: ...

    def insert_session(self, session: UserSessionRecord) -> UserSessionRecord: ...

    def update_session(
        self,
        session_id: str,
        *,
        is_active: bool | None,
        last_seen_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> bool: ...

    def revoke_all_sessions(self, user_id: str, revoked_at: datetime) -> None: ...

    def load_credit(self, user_id: str) -> UserCreditRecord | None: ...

    def insert_credit(self, credit: UserCreditRecord) -> UserCreditRecord: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AuthenticationUnitOfWork]


class EmailCodeStore(Protocol):
    def cleanup_expired(self, now_timestamp: float) -> None: ...

    def get(self, email: str) -> dict[str, Any] | None: ...

    def put(self, email: str, record: dict[str, Any]) -> None: ...

    def pop(self, email: str) -> dict[str, Any] | None: ...

    def mark_verified(self, email: str) -> bool: ...


class FixedWindowLimiter(Protocol):
    def check(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        now: float,
    ) -> int | None: ...


class AuthenticationRuntimePort(Protocol):
    """Explicit configuration and adapters consumed by application use cases."""

    secret_key: str | None
    verification_email_sender: Callable[[str, str], bool] | None
    password_reset_sender: Callable[[str, str, str], bool] | None
    email_code_store: EmailCodeStore | None
    rate_limiter: FixedWindowLimiter | None
    clock: Callable[[], datetime]
    epoch: Callable[[], float]
    code_generator: Callable[[], str]
    id_factory: Callable[[], str]
    algorithm: str
    access_token_ttl_minutes: int

    def get_password_hasher(self) -> PasswordHasher: ...

    def get_token_codec(self) -> TokenCodec | None: ...

    def frontend_base_url(self) -> str | None: ...
