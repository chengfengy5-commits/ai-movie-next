"""Lazy adapter for the legacy Fernet key derivation and plaintext fallback."""

from __future__ import annotations

from typing import Any

from .errors import SmtpCryptoUnavailable


class LegacyFernet:
    def __init__(self, secret_key: str | None) -> None:
        self._secret_key = secret_key
        self._fernet: Any | None = None

    def _get_fernet(self) -> Any:
        if self._fernet is not None:
            return self._fernet
        if not self._secret_key:
            raise SmtpCryptoUnavailable()

        import base64
        import hashlib

        from cryptography.fernet import Fernet

        derived_key = base64.urlsafe_b64encode(
            hashlib.sha256(self._secret_key.encode("utf-8")).digest()
        )
        self._fernet = Fernet(derived_key)
        return self._fernet

    def encrypt(self, plain_text: str) -> str:
        if not plain_text:
            return ""
        token = self._get_fernet().encrypt(plain_text.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, cipher_text: str) -> str:
        if not cipher_text:
            return ""
        fernet = self._get_fernet()
        if cipher_text.startswith("gAAAAA"):
            return fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
        return cipher_text
