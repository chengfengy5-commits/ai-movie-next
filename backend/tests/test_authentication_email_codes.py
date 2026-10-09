"""Verification-code storage and use-case behavior over explicit adapters."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.email_codes import InMemoryEmailCodeStore
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata


class TestPasswordHasher:
    __test__ = False

    def hash(self, password: str) -> str:
        return f"hash:{password}"

    def verify(self, password: str, encoded: str) -> bool:
        return encoded == self.hash(password)


class TestTokenCodec:
    __test__ = False

    def encode(self, claims):
        return "unused-token"

    def decode(self, token):
        raise AssertionError("email-code tests do not decode access tokens")


def service_for(factory, store, *, sender, epoch=lambda: 1_000.0, code="123456"):
    id_counter = 0

    def next_id() -> str:
        nonlocal id_counter
        id_counter += 1
        return f"generated-id-{id_counter}"

    runtime = AuthenticationRuntime(
        session_factory=factory,
        secret_key="local-test-key",
        password_hasher=TestPasswordHasher(),
        token_codec=TestTokenCodec(),
        email_code_store=store,
        verification_email_sender=sender,
        clock=lambda: datetime(2026, 1, 1, 12),
        epoch=epoch,
        code_generator=lambda: code,
        id_factory=next_id,
    )
    return AuthenticationService(
        runtime,
        lambda: SqlAlchemyAuthenticationUnitOfWork(factory()),
    )


def database(path: Path):
    engine = create_engine(f"sqlite:///{path}", future=True)
    metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False, future=True)


def test_send_verify_and_registration_use_normalized_stored_code(tmp_path: Path) -> None:
    engine, factory = database(tmp_path / "email-code.sqlite")
    store = InMemoryEmailCodeStore()
    sent: list[tuple[str, str]] = []
    sender_results = iter([False, True])

    def sender(email: str, code: str) -> bool:
        sent.append((email, code))
        return next(sender_results)

    service = service_for(factory, store, sender=sender)
    try:
        assert service.send_code(email=" Alice@Example.Test ", username=" Alice ") == {
            "message": "验证码已发送",
            "expire_in": 300,
        }
        assert sent == [
            ("alice@example.test", "123456"),
            ("alice@example.test", "123456"),
        ]
        record = store.get("alice@example.test")
        assert record == {
            "code": "123456",
            "expires_at": 1300.0,
            "last_send": 1000.0,
            "verified": False,
        }

        assert service.verify_code(email=" ALICE@EXAMPLE.TEST ", code="123456") == {
            "message": "验证成功"
        }
        assert store.get("alice@example.test")["verified"] is True
        # A subsequent send inside the cooldown does not replace the verified code.
        with pytest.raises(AuthenticationError) as cooldown:
            service.send_code(email="alice@example.test", username="")
        assert cooldown.value.status_code == 429
        assert store.get("alice@example.test")["code"] == "123456"

        result = service.register(
            username="Alice",
            email="alice@example.test",
            password="Password123",
            email_code="123456",
        )
        assert result["email"] == "alice@example.test"
        assert store.get("alice@example.test") is None
    finally:
        engine.dispose()


def test_send_code_retries_once_and_does_not_store_failed_delivery(tmp_path: Path) -> None:
    engine, factory = database(tmp_path / "email-failure.sqlite")
    store = InMemoryEmailCodeStore()
    attempts: list[tuple[str, str]] = []

    def sender(email: str, code: str) -> bool:
        attempts.append((email, code))
        return False

    service = service_for(factory, store, sender=sender)
    try:
        with pytest.raises(AuthenticationError) as failure:
            service.send_code(email="new@example.test", username="")
        assert (failure.value.status_code, failure.value.detail) == (
            502,
            "验证码发送失败，请稍后再试",
        )
        assert attempts == [
            ("new@example.test", "123456"),
            ("new@example.test", "123456"),
        ]
        assert store.get("new@example.test") is None
    finally:
        engine.dispose()


def test_expiry_boundary_and_store_copy_semantics() -> None:
    store = InMemoryEmailCodeStore()
    original = {"code": "111111", "expires_at": 100.0, "last_send": 50.0, "verified": False}
    store.put("a@example.test", original)
    original["verified"] = True
    fetched = store.get("a@example.test")
    assert fetched is not None and fetched["verified"] is False
    fetched["verified"] = True
    assert store.get("a@example.test")["verified"] is False

    store.cleanup_expired(100.0)
    assert store.get("a@example.test") is not None
    assert store.pop("a@example.test") == {
        "code": "111111",
        "expires_at": 100.0,
        "last_send": 50.0,
        "verified": False,
    }
    assert store.pop("a@example.test") is None

    store.put("expired@example.test", {**original, "expires_at": 100.0})
    store.cleanup_expired(100.0001)
    assert store.get("expired@example.test") is None


def test_verification_expiry_uses_strict_greater_than_and_consumes_expired_record(tmp_path: Path) -> None:
    engine, factory = database(tmp_path / "email-expiry.sqlite")
    store = InMemoryEmailCodeStore()
    store.put(
        "equal@example.test",
        {"code": "123456", "expires_at": 100.0, "last_send": 1.0, "verified": False},
    )
    now = [100.0]
    service = service_for(
        factory,
        store,
        sender=lambda *_args: True,
        epoch=lambda: now[0],
    )
    try:
        assert service.verify_code(email="equal@example.test", code="123456") == {
            "message": "验证成功"
        }
        assert store.get("equal@example.test")["verified"] is True

        store.put(
            "expired@example.test",
            {"code": "123456", "expires_at": 100.0, "last_send": 1.0, "verified": False},
        )
        now[0] = 100.001
        with pytest.raises(AuthenticationError) as expired:
            service.verify_code(email="expired@example.test", code="123456")
        assert (expired.value.status_code, expired.value.detail) == (
            400,
            "验证码已过期，请重新获取",
        )
        assert store.get("expired@example.test") is None
    finally:
        engine.dispose()


def test_registration_consumes_raw_email_code_before_duplicate_account_checks(tmp_path: Path) -> None:
    engine, factory = database(tmp_path / "registration-code-order.sqlite")
    store = InMemoryEmailCodeStore()
    service = service_for(factory, store, sender=lambda *_args: True)
    try:
        service.register(
            username="alice",
            email="alice@example.test",
            password="Password123",
            email_code="",
        )
        store.put(
            "another@example.test",
            {"code": "123456", "expires_at": 1300.0, "last_send": 1000.0, "verified": True},
        )
        with pytest.raises(AuthenticationError) as duplicate:
            service.register(
                username="alice",
                email="another@example.test",
                password="Password123",
                email_code="123456",
            )
        assert (duplicate.value.status_code, duplicate.value.detail) == (
            400,
            "注册失败，请检查输入信息",
        )
        assert store.get("another@example.test") is None

        raw_email = " Raw@Example.Test "
        store.put(
            raw_email,
            {"code": "654321", "expires_at": 1300.0, "last_send": 1000.0, "verified": False},
        )
        result = service.register(
            username=" raw ",
            email=raw_email,
            password="Password123",
            email_code="654321",
        )
        assert result["username"] == " raw "
        assert result["email"] == raw_email
        assert store.get(raw_email) is None
    finally:
        engine.dispose()


def test_send_code_checks_format_and_account_occupancy_before_delivery(tmp_path: Path) -> None:
    engine, factory = database(tmp_path / "send-code-occupancy.sqlite")
    store = InMemoryEmailCodeStore()
    sent: list[tuple[str, str]] = []
    service = service_for(factory, store, sender=lambda email, code: sent.append((email, code)) or True)
    try:
        service.register(
            username="alice",
            email="alice@example.test",
            password="Password123",
            email_code="",
        )
        with pytest.raises(AuthenticationError) as bad_email:
            service.send_code(email="not-an-email", username="alice")
        assert (bad_email.value.status_code, bad_email.value.detail) == (400, "邮箱格式不正确")

        with pytest.raises(AuthenticationError) as occupied_email:
            service.send_code(email="alice@example.test", username="")
        assert (occupied_email.value.status_code, occupied_email.value.detail) == (
            400,
            "该邮箱已被注册",
        )

        with pytest.raises(AuthenticationError) as occupied_name:
            service.send_code(email="new@example.test", username=" alice ")
        assert (occupied_name.value.status_code, occupied_name.value.detail) == (
            400,
            "该用户名已被注册",
        )
        assert sent == []
        assert store.snapshot() == {}
    finally:
        engine.dispose()
