"""Lazy adapter for the legacy SECRET_KEY-derived Fernet format."""

from __future__ import annotations

from typing import Any

from .errors import ProviderQuotaCryptoUnavailable


class LegacyFernetCipher:
    """Use explicit secret input; preserve Fernet and legacy plaintext rows."""

    def __init__(self, secret_key: str | None) -> None:
        self._secret_key = secret_key
        self._fernet: Any | None = None

    def _get_fernet(self) -> Any:
        if self._fernet is not None:
            return self._fernet
        if not self._secret_key:
            raise ProviderQuotaCryptoUnavailable()

        import base64
        import hashlib

        from cryptography.fernet import Fernet

        key = base64.urlsafe_b64encode(
            hashlib.sha256(self._secret_key.encode("utf-8")).digest()
        )
        self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, plain_text: str) -> str:
        if not plain_text:
            return ""
        return self._get_fernet().encrypt(plain_text.encode("utf-8")).decode("utf-8")

    def decrypt(self, cipher_text: str) -> str:
        if not cipher_text:
            return ""
        fernet = self._get_fernet()
        if cipher_text.startswith("gAAAAA"):
            return fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
        return cipher_text
