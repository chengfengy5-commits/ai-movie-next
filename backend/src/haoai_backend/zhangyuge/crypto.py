"""Lazy adapter for the legacy SECRET_KEY-derived Fernet format."""

from __future__ import annotations

import base64
import hashlib

from .errors import ZhangyugeCryptoUnavailable

_CIPHER_PREFIX = "gAAAAA"


class LegacyFernet:
    """Use an explicitly supplied secret; construction reads no environment."""

    def __init__(self, secret_key: str | None) -> None:
        self._secret_key = secret_key
        self._fernet = None

    def _get_fernet(self):
        if self._fernet is not None:
            return self._fernet
        if not self._secret_key:
            raise ZhangyugeCryptoUnavailable()

        from cryptography.fernet import Fernet

        key_bytes = hashlib.sha256(self._secret_key.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
        return self._fernet

    def encrypt(self, plain_text: str | None) -> str:
        if not plain_text:
            return ""
        token = self._get_fernet().encrypt(plain_text.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, cipher_text: str | None) -> str:
        if not cipher_text:
            return ""
        fernet = self._get_fernet()
        if cipher_text.startswith(_CIPHER_PREFIX):
            return fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
        return cipher_text
