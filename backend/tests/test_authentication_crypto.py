"""Real cryptographic adapters are lazy and use explicit configuration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.passwords import BcryptPasswordHasher
from haoai_backend.authentication.tokens import ExpiredTokenError, JoseTokenCodec, TokenDecodeError


def test_bcrypt_adapter_hashes_and_verifies_real_passwords() -> None:
    hasher = BcryptPasswordHasher()
    encoded = hasher.hash("RealPassword123")
    assert encoded.startswith(("$2a$", "$2b$", "$2y$"))
    assert hasher.verify("RealPassword123", encoded)
    assert not hasher.verify("wrong", encoded)
    with pytest.raises(ValueError, match="not a valid bcrypt hash"):
        hasher.verify("password", "not-a-bcrypt-hash")


def test_jose_codec_signs_verifies_rejects_tampering_and_expiry() -> None:
    codec = JoseTokenCodec("temporary-test-secret", algorithm="HS512")
    token = codec.encode({"sub": "user-1", "exp": datetime.now(timezone.utc) + timedelta(minutes=1)})
    assert codec.decode(token)["sub"] == "user-1"
    with pytest.raises(TokenDecodeError):
        JoseTokenCodec("different-secret", algorithm="HS512").decode(token)
    with pytest.raises(ExpiredTokenError):
        codec.decode(codec.encode({"sub": "user-1", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}))


def test_runtime_algorithm_ttl_and_legacy_password_version_default_are_explicit() -> None:
    fixed_now = datetime.now(timezone.utc).replace(microsecond=0)
    runtime = AuthenticationRuntime(
        secret_key="explicit-test-secret",
        algorithm="HS512",
        access_token_ttl_minutes=7,
        clock=lambda: fixed_now,
        id_factory=lambda: "generated-jti",
    )
    service = AuthenticationService(runtime)

    token = service.create_access_token({"sub": "legacy-user"})
    claims = JoseTokenCodec("explicit-test-secret", algorithm="HS512").decode(token)

    assert claims["sub"] == "legacy-user"
    assert claims["jti"] == "generated-jti"
    assert claims["password_version"] == "legacy"
    assert datetime.fromtimestamp(claims["exp"], timezone.utc) == fixed_now + timedelta(minutes=7)


def test_runtime_defaults_keep_explicit_hs256_and_fourteen_day_access_ttl() -> None:
    fixed_now = datetime.now(timezone.utc).replace(microsecond=0)
    runtime = AuthenticationRuntime(
        secret_key="default-test-secret",
        clock=lambda: fixed_now,
        id_factory=lambda: "default-jti",
    )
    assert runtime.algorithm == "HS256"
    assert runtime.access_token_ttl_minutes == 14 * 24 * 60
    service = AuthenticationService(runtime)
    claims = JoseTokenCodec("default-test-secret", algorithm="HS256").decode(
        service.create_access_token({"sub": "user-1"})
    )
    assert claims["jti"] == "default-jti"
    assert claims["password_version"] == "legacy"
    assert datetime.fromtimestamp(claims["exp"], timezone.utc) == fixed_now + timedelta(days=14)


def test_runtime_token_codec_remains_unconfigured_without_an_explicit_secret() -> None:
    assert AuthenticationRuntime().get_token_codec() is None
