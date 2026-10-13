"""R2 readiness uses fake SDK objects and explicit test-only credentials."""

from __future__ import annotations

import asyncio
import builtins
import threading
from typing import Any

import pytest

from haoai_backend.media_storage.transfer_types import SecretValue
from haoai_backend.runtime_status.application import ReadinessGate
from haoai_backend.runtime_status.configuration import RuntimeStatusConfiguration
from haoai_backend.runtime_status.r2_readiness import (
    Boto3R2HeadBucketClientFactory,
    R2HeadBucketProbe,
    R2HeadBucketSettings,
    build_boto3_r2_head_bucket_factory,
)


class FakeSecretHandle:
    def __init__(self, value: str, events: list[tuple[str, int]]) -> None:
        self._value = value
        self._events = events
        self.reveal_count = 0

    def reveal(self) -> SecretValue:
        self.reveal_count += 1
        self._events.append(("reveal", threading.get_ident()))
        return SecretValue(self._value)


class FakeClient:
    def __init__(
        self,
        events: list[tuple[str, int]],
        *,
        error: Exception | None = None,
    ) -> None:
        self._events = events
        self._error = error
        self.bucket: str | None = None

    def head_bucket(self, *, Bucket: str) -> None:
        self.bucket = Bucket
        self._events.append(("head_bucket", threading.get_ident()))
        if self._error is not None:
            raise self._error

    def close(self) -> None:
        self._events.append(("close", threading.get_ident()))


class FakeSession:
    def __init__(self, client: FakeClient, events: list[tuple[str, int]]) -> None:
        self._client = client
        self._events = events

    def client(self, service: str, *, endpoint_url: str, config: object) -> FakeClient:
        self._events.append(("client", threading.get_ident()))
        assert service == "s3"
        assert endpoint_url == "https://account.r2.cloudflarestorage.com"
        assert config is not None
        return self._client


def _settings() -> R2HeadBucketSettings:
    return R2HeadBucketSettings(
        endpoint_url="https://account.r2.cloudflarestorage.com",
        bucket="haoai-test-bucket",
        region="auto",
        connect_timeout_seconds=1.25,
        read_timeout_seconds=2.5,
        total_max_attempts=3,
    )


def test_production_r2_factory_defers_optional_sdk_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def deny_sdk_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "boto3" or name.startswith("botocore"):
            raise AssertionError("factory construction must not import the SDK")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny_sdk_import)

    factory = build_boto3_r2_head_bucket_factory()

    assert isinstance(factory, Boto3R2HeadBucketClientFactory)


def test_r2_factory_and_probe_are_lazy_and_create_client_only_off_loop() -> None:
    events: list[tuple[str, int]] = []
    client = FakeClient(events)
    session = FakeSession(client, events)
    config_calls: list[dict[str, object]] = []

    def config_factory(**kwargs: object) -> str:
        config_calls.append(dict(kwargs))
        events.append(("config", threading.get_ident()))
        return "fake-botocore-config"

    def session_factory(**kwargs: object) -> FakeSession:
        events.append(("session", threading.get_ident()))
        assert kwargs == {
            "aws_access_key_id": "test-access-key",
            "aws_secret_access_key": "test-secret-key",
            "region_name": "auto",
        }
        return session

    access_key = FakeSecretHandle("test-access-key", events)
    secret_key = FakeSecretHandle("test-secret-key", events)
    factory = Boto3R2HeadBucketClientFactory(
        session_factory=session_factory,
        config_factory=config_factory,
    )
    probe = R2HeadBucketProbe(
        _settings(),
        access_key=access_key,
        secret_key=secret_key,
        client_factory=factory,
    )

    assert events == []
    assert access_key.reveal_count == secret_key.reveal_count == 0

    loop_thread = threading.get_ident()
    asyncio.run(probe())

    assert client.bucket == "haoai-test-bucket"
    assert access_key.reveal_count == secret_key.reveal_count == 1
    assert config_calls == [
        {
            "signature_version": "s3v4",
            "retries": {"mode": "standard", "total_max_attempts": 3},
            "connect_timeout": 1.25,
            "read_timeout": 2.5,
            "proxies": {},
        }
    ]
    assert [name for name, _thread in events] == [
        "reveal",
        "reveal",
        "config",
        "session",
        "client",
        "head_bucket",
        "close",
    ]
    assert all(thread_id != loop_thread for _name, thread_id in events)


def test_r2_client_is_closed_when_head_bucket_fails() -> None:
    events: list[tuple[str, int]] = []
    client = FakeClient(events, error=RuntimeError("fake provider error"))
    factory = Boto3R2HeadBucketClientFactory(
        session_factory=lambda **_kwargs: FakeSession(client, events),
        config_factory=lambda **_kwargs: object(),
    )
    probe = R2HeadBucketProbe(
        _settings(),
        access_key=FakeSecretHandle("test-access", events),
        secret_key=FakeSecretHandle("test-secret", events),
        client_factory=factory,
    )

    with pytest.raises(RuntimeError, match="fake provider error"):
        asyncio.run(probe())

    assert [name for name, _thread in events][-2:] == ["head_bucket", "close"]


def test_readiness_timeout_defers_close_until_sdk_call_returns() -> None:
    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class BlockingClient(FakeClient):
        def head_bucket(self, *, Bucket: str) -> None:
            del Bucket
            started.set()
            release.wait(timeout=2)

        def close(self) -> None:
            closed.set()

    client = BlockingClient([])
    probe = R2HeadBucketProbe(
        _settings(),
        access_key=FakeSecretHandle("test-access", []),
        secret_key=FakeSecretHandle("test-secret", []),
        client_factory=Boto3R2HeadBucketClientFactory(
            session_factory=lambda **_kwargs: FakeSession(client, []),
            config_factory=lambda **_kwargs: object(),
        ),
    )
    gate = ReadinessGate(
        RuntimeStatusConfiguration(
            service_name="test",
            version="test",
            readiness_initialized=True,
            object_storage_configured=True,
            probe_timeout_seconds=0.15,
            ready_cache_seconds=1,
            initial_failure_backoff_seconds=1,
            max_failure_backoff_seconds=2,
        ),
        database_probe=lambda: None,
        object_storage_probe=probe,
    )

    async def check_gate():
        result = await gate.check()
        assert started.is_set()
        assert not closed.is_set()
        release.set()
        assert await asyncio.to_thread(closed.wait, 1)
        return result

    result = asyncio.run(check_gate())

    assert result.ready is False
    assert result.reason == "object_storage"
