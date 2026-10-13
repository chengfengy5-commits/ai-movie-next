from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet, InvalidToken

from haoai_backend.provider_quota.crypto import LegacyFernetCipher
from haoai_backend.provider_quota.errors import ProviderQuotaCryptoUnavailable


def test_cipher_reads_and_writes_the_fixed_legacy_fernet_format():
    secret_key = "provider-quota-test-secret"
    key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
    legacy = Fernet(key)
    old_token = legacy.encrypt(b"stored-provider-password").decode()
    cipher = LegacyFernetCipher(secret_key)

    assert cipher.decrypt(old_token) == "stored-provider-password"
    assert cipher.decrypt("legacy-plaintext") == "legacy-plaintext"
    new_token = cipher.encrypt("new-provider-password")
    assert new_token.startswith("gAAAAA")
    assert legacy.decrypt(new_token.encode()).decode() == "new-provider-password"
    with pytest.raises(InvalidToken):
        LegacyFernetCipher("wrong-test-secret").decrypt(old_token)


def test_empty_values_need_no_secret_and_nonempty_values_fail_closed():
    cipher = LegacyFernetCipher(None)
    assert cipher.encrypt("") == ""
    assert cipher.decrypt("") == ""
    with pytest.raises(ProviderQuotaCryptoUnavailable) as error:
        cipher.encrypt("fake-password")
    assert error.value.status_code == 503
