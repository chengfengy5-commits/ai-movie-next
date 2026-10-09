"""Explicit-secret JWT adapter; python-jose is imported only on use."""

from __future__ import annotations

from typing import Any, Mapping


class TokenDecodeError(Exception):
    """A token could not be decoded or validated."""


class ExpiredTokenError(TokenDecodeError):
    """A cryptographically valid token has expired."""


class JoseTokenCodec:
    def __init__(self, secret_key: str, algorithm: str = "HS256") -> None:
        self._secret_key = secret_key
        self._algorithm = algorithm

    def encode(self, claims: Mapping[str, Any]) -> str:
        from jose import jwt

        return jwt.encode(dict(claims), self._secret_key, algorithm=self._algorithm)

    def decode(self, token: str) -> dict[str, Any]:
        from jose import JWTError, jwt

        try:
            payload = jwt.decode(token, self._secret_key, algorithms=[self._algorithm])
        except jwt.ExpiredSignatureError as exc:
            raise ExpiredTokenError() from exc
        except JWTError as exc:
            raise TokenDecodeError() from exc
        if not isinstance(payload, dict):
            raise TokenDecodeError()
        return payload
