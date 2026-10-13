"""Lazy, read-only R2 head-bucket readiness probe."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from haoai_backend.media_storage.transfer_types import SecretHandle, SecretValue


_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")


@dataclass(frozen=True, slots=True)
class R2HeadBucketSettings:
    """Non-secret values needed for one R2 HeadBucket call."""

    endpoint_url: str
    bucket: str
    region: str = "auto"
    connect_timeout_seconds: float = 3.0
    read_timeout_seconds: float = 3.0
    total_max_attempts: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint_url, str):
            raise ValueError("endpoint_url must be an HTTP(S) origin")
        parsed = urlsplit(self.endpoint_url)
        try:
            parsed.port
        except ValueError:
            raise ValueError("endpoint_url has an invalid port") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "endpoint_url must be an HTTP(S) origin without credentials"
            )
        if (
            not isinstance(self.bucket, str)
            or not _BUCKET_NAME.fullmatch(self.bucket)
            or ".." in self.bucket
        ):
            raise ValueError("bucket is invalid")
        if not isinstance(self.region, str) or not self.region.strip():
            raise ValueError("region must be non-empty")
        for value in (self.connect_timeout_seconds, self.read_timeout_seconds):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("R2 timeouts must be positive")
        if type(self.total_max_attempts) is not int or self.total_max_attempts < 1:
            raise ValueError("total_max_attempts must be positive")


class HeadBucketClient(Protocol):
    def head_bucket(self, *, Bucket: str) -> Any: ...
    def close(self) -> None: ...


class HeadBucketClientFactory(Protocol):
    def create(
        self,
        settings: R2HeadBucketSettings,
        access_key: SecretHandle,
        secret_key: SecretHandle,
    ) -> HeadBucketClient: ...


class ConfigFactory(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


class SessionFactory(Protocol):
    def __call__(
        self,
        *,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        region_name: str,
    ) -> object: ...


class Boto3R2HeadBucketClientFactory:
    """Create a narrowly scoped boto3 client only when the probe is invoked."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        config_factory: ConfigFactory,
    ) -> None:
        if not callable(session_factory) or not callable(config_factory):
            raise TypeError("explicit session and config factories are required")
        self._session_factory = session_factory
        self._config_factory = config_factory

    def create(
        self,
        settings: R2HeadBucketSettings,
        access_key: SecretHandle,
        secret_key: SecretHandle,
    ) -> HeadBucketClient:
        if not isinstance(settings, R2HeadBucketSettings):
            raise TypeError("explicit R2 head-bucket settings are required")
        access_value = access_key.reveal()
        secret_value = secret_key.reveal()
        if not isinstance(access_value, SecretValue) or not isinstance(
            secret_value, SecretValue
        ):
            raise TypeError("credential handles must return SecretValue")

        config = self._config_factory(
            signature_version="s3v4",
            retries={
                "mode": "standard",
                "total_max_attempts": settings.total_max_attempts,
            },
            connect_timeout=settings.connect_timeout_seconds,
            read_timeout=settings.read_timeout_seconds,
            proxies={},
        )
        session = self._session_factory(
            aws_access_key_id=access_value.value,
            aws_secret_access_key=secret_value.value,
            region_name=settings.region,
        )
        client = session.client(  # type: ignore[attr-defined]
            "s3",
            endpoint_url=settings.endpoint_url,
            config=config,
        )
        if not callable(getattr(client, "head_bucket", None)) or not callable(
            getattr(client, "close", None)
        ):
            close = getattr(client, "close", None)
            if callable(close):
                close()
            raise TypeError("R2 client must expose head_bucket and close")
        return client


class R2HeadBucketProbe:
    """Own a short-lived SDK client and perform its sync call off-loop."""

    def __init__(
        self,
        settings: R2HeadBucketSettings,
        *,
        access_key: SecretHandle,
        secret_key: SecretHandle,
        client_factory: HeadBucketClientFactory,
    ) -> None:
        self._settings = settings
        self._access_key = access_key
        self._secret_key = secret_key
        self._client_factory = client_factory

    async def __call__(self) -> None:
        await asyncio.to_thread(self._head_bucket)

    def _head_bucket(self) -> None:
        client = self._client_factory.create(
            self._settings,
            self._access_key,
            self._secret_key,
        )
        try:
            client.head_bucket(Bucket=self._settings.bucket)
        finally:
            client.close()


def build_boto3_r2_head_bucket_factory() -> Boto3R2HeadBucketClientFactory:
    """Defer boto3 imports and client construction until a probe is invoked."""

    def session_factory(**kwargs: object) -> object:
        import boto3

        return boto3.Session(**kwargs)

    def config_factory(**kwargs: object) -> object:
        from botocore.config import Config

        return Config(**kwargs)

    return Boto3R2HeadBucketClientFactory(
        session_factory=session_factory,
        config_factory=config_factory,
    )


__all__ = (
    "Boto3R2HeadBucketClientFactory",
    "HeadBucketClient",
    "HeadBucketClientFactory",
    "R2HeadBucketProbe",
    "R2HeadBucketSettings",
    "build_boto3_r2_head_bucket_factory",
)
