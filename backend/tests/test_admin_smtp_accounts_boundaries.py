from __future__ import annotations

import builtins
import hashlib
import base64
from datetime import date, timedelta

import pytest
from cryptography.fernet import Fernet, InvalidToken

from haoai_backend.admin_smtp_accounts.crypto import LegacyFernet
from haoai_backend.admin_smtp_accounts.daily_reset import DailyResetState
from haoai_backend.admin_smtp_accounts.errors import SmtpCryptoUnavailable


SECRET = "smtp-test-secret-that-is-not-real"


def test_cipher_is_lazy_for_empty_values_and_uses_fixed_legacy_format(monkeypatch):
    real_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == "cryptography.fernet":
            raise AssertionError("empty or missing-secret paths must not import Fernet")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    missing = LegacyFernet(None)
    assert missing.encrypt("") == ""
    assert missing.decrypt("") == ""
    with pytest.raises(SmtpCryptoUnavailable) as error:
        missing.encrypt("non-empty")
    assert error.value.status_code == 503
    with pytest.raises(SmtpCryptoUnavailable):
        missing.decrypt("legacy plaintext")

    monkeypatch.setattr(builtins, "__import__", real_import)
    derived = base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest())
    legacy_fernet = Fernet(derived)
    old_token = legacy_fernet.encrypt(b"stored-password").decode()
    codec = LegacyFernet(SECRET)
    assert codec.decrypt(old_token) == "stored-password"
    assert codec.decrypt("legacy plaintext") == "legacy plaintext"
    new_token = codec.encrypt("new-password")
    assert new_token.startswith("gAAAAA")
    assert legacy_fernet.decrypt(new_token.encode()).decode() == "new-password"
    with pytest.raises(InvalidToken):
        LegacyFernet("wrong-fake-secret").decrypt(old_token)
    with pytest.raises(InvalidToken):
        codec.decrypt("gAAAAA-invalid")


def test_local_daily_state_clears_cache_before_reset_and_does_not_replay_failure():
    current = [date(2026, 10, 12)]
    state = DailyResetState(lambda: current[0])
    state.increment_cached_count("smtp-id")
    assert state.cached_count("smtp-id") == 1
    resets = []
    state.reset_if_needed(lambda: resets.append(state.cached_count("smtp-id")))
    assert resets == []
    current[0] += timedelta(days=1)
    def failing_reset():
        resets.append(state.cached_count("smtp-id"))
        raise OSError("db unavailable")
    with pytest.raises(OSError):
        state.reset_if_needed(failing_reset)
    assert resets == [0]
    assert state.today == current[0]
    state.reset_if_needed(lambda: resets.append("unexpected replay"))
    assert resets == [0]
