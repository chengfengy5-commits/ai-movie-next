from __future__ import annotations

import base64
import hashlib
import os

import pytest

from haoai_backend.zhangyuge.crypto import LegacyFernet
from haoai_backend.zhangyuge.errors import ZhangyugeCryptoUnavailable


def test_legacy_fernet_reads_legacy_tokens_and_writes_compatible_ciphertext() -> None:
    from cryptography.fernet import Fernet, InvalidToken

    secret = "zhangyuge-fake-test-secret"
    derived = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    old_codec = Fernet(derived)
    codec = LegacyFernet(secret)

    legacy_token = old_codec.encrypt(b"fake-legacy-pat").decode("utf-8")
    new_token = codec.encrypt("fake-new-pat")

    assert codec.decrypt(legacy_token) == "fake-legacy-pat"
    assert new_token.startswith("gAAAAA")
    assert old_codec.decrypt(new_token.encode("utf-8")) == b"fake-new-pat"
    with pytest.raises(InvalidToken):
        codec.decrypt("gAAAAA-invalid-fake-token")
    assert codec.decrypt("legacy-plaintext") == "legacy-plaintext"


def test_empty_values_do_not_require_a_secret_key() -> None:
    codec = LegacyFernet(None)
    assert codec.encrypt("") == ""
    assert codec.encrypt(None) == ""
    assert codec.decrypt("") == ""
    assert codec.decrypt(None) == ""
    with pytest.raises(ZhangyugeCryptoUnavailable):
        codec.encrypt("fake-pat")
    with pytest.raises(ZhangyugeCryptoUnavailable):
        codec.decrypt("legacy-plaintext")


def test_new_crypto_code_does_not_read_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_environment_access(*_args, **_kwargs):
        raise AssertionError("the cipher must use the supplied fake secret only")

    with monkeypatch.context() as patch:
        patch.setattr(os, "getenv", forbidden_environment_access)
        assert LegacyFernet("fake-secret").encrypt("fake-pat")
