"""Explicit, per-service decryption for configured provider API keys."""

from __future__ import annotations

import base64
import hashlib
from typing import Any


class CredentialDecryptor:
    """Mirror the legacy Fernet format without reading process configuration."""

    def __init__(self, secret_key: str | None) -> None:
        self._secret_key = secret_key
        self._fernet: Any | None = None

    def decrypt(self, api_key: str | None) -> str:
        if not api_key:
            return ""

        fernet = self._get_fernet()
        if api_key.startswith("gAAAAA"):
            return fernet.decrypt(api_key.encode("utf-8")).decode("utf-8")
        return api_key

    def _get_fernet(self) -> Any:
        if self._fernet is None:
            from cryptography.fernet import Fernet

            key_bytes = hashlib.sha256(self._secret_key.encode("utf-8")).digest()
            self._fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
        return self._fernet
