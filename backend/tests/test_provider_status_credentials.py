"""Explicit SECRET_KEY derivation and per-service Fernet cache behavior."""

from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet, InvalidToken

from haoai_backend.provider_status.credentials import CredentialDecryptor


def derive_fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def test_plaintext_key_still_constructs_and_caches_fernet() -> None:
    decryptor = CredentialDecryptor("explicit-secret")

    assert decryptor.decrypt("legacy-plaintext") == "legacy-plaintext"
    first = decryptor._fernet
    assert first is not None
    assert decryptor.decrypt("another-plaintext") == "another-plaintext"
    assert decryptor._fernet is first


def test_fernet_prefixed_key_decrypts_with_explicit_secret() -> None:
    secret_key = "explicit-secret"
    token = derive_fernet(secret_key).encrypt("provider-key".encode("utf-8")).decode("utf-8")

    assert token.startswith("gAAAAA")
    assert CredentialDecryptor(secret_key).decrypt(token) == "provider-key"


def test_empty_key_needs_no_secret_and_returns_empty() -> None:
    decryptor = CredentialDecryptor(None)

    assert decryptor.decrypt(None) == ""
    assert decryptor.decrypt("") == ""
    assert decryptor._fernet is None


def test_nonempty_key_without_secret_fails_before_poll() -> None:
    with pytest.raises(AttributeError):
        CredentialDecryptor(None).decrypt("plaintext")


def test_invalid_token_and_invalid_utf8_are_not_normalized() -> None:
    secret_key = "explicit-secret"
    fernet = derive_fernet(secret_key)
    valid = fernet.encrypt(b"provider-key").decode("utf-8")
    changed = valid[:-1] + ("A" if valid[-1] != "A" else "B")

    with pytest.raises(InvalidToken):
        CredentialDecryptor(secret_key).decrypt(changed)

    invalid_utf8 = fernet.encrypt(b"\xff").decode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        CredentialDecryptor(secret_key).decrypt(invalid_utf8)


def test_fernet_cache_is_owned_by_each_decryptor() -> None:
    first = CredentialDecryptor("same-secret")
    second = CredentialDecryptor("same-secret")

    first.decrypt("plain")
    second.decrypt("plain")

    assert first._fernet is not None
    assert second._fernet is not None
    assert first._fernet is not second._fernet
