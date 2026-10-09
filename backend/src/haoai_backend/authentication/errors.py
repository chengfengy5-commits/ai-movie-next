"""Authentication-specific failures that can cross the existing business adapters."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AuthenticationError(BusinessError):
    """Expected authentication failure with an optional bearer challenge."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        authenticate: bool = False,
    ) -> None:
        super().__init__(status_code, detail)
        self.headers = {"WWW-Authenticate": "Bearer"} if authenticate else None


def unavailable() -> AuthenticationError:
    return AuthenticationError(503, "认证服务尚未接线")


def credentials_error() -> AuthenticationError:
    return AuthenticationError(401, "无法验证凭据", authenticate=True)
