from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .transfer_types import (
    R2ClientSettings,
    S3Client,
    S3ClientFactory,
    SecretValue,
    WriteReceipt,
)


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


class Boto3S3ClientFactory(S3ClientFactory):
    """Build an S3 client only from explicit constructors and secret handles."""

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
        settings: R2ClientSettings,
        access_key: SecretValue,
        secret_key: SecretValue,
    ) -> S3Client:
        if not isinstance(settings, R2ClientSettings):
            raise TypeError("an explicit R2 client settings value is required")
        if not isinstance(access_key, SecretValue) or not isinstance(
            secret_key, SecretValue
        ):
            raise TypeError("explicit resolved secret values are required")
        config = self._config_factory(
            signature_version=settings.signature_version,
            retries={
                "mode": settings.retry_mode,
                "total_max_attempts": settings.total_max_attempts,
            },
            connect_timeout=settings.connect_timeout_seconds,
            read_timeout=settings.read_timeout_seconds,
            proxies={},
        )
        session = self._session_factory(
            aws_access_key_id=access_key.value,
            aws_secret_access_key=secret_key.value,
            region_name=settings.region,
        )
        client = session.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            config=config,
        )
        if not callable(getattr(client, "put_object", None)) or not callable(
            getattr(client, "close", None)
        ):
            raise TypeError("S3 client must expose put_object and close")
        return _Boto3S3Client(client, settings.bucket)


class _Boto3S3Client(S3Client):
    def __init__(self, raw: object, bucket: str) -> None:
        self._raw = raw
        self._bucket = bucket

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: Any,
        ContentType: str,
        CacheControl: str,
    ) -> WriteReceipt:
        if Bucket != self._bucket or not isinstance(Key, str) or not Key:
            raise ValueError("PutObject must use the configured bucket and original key")
        response = self._raw.put_object(  # type: ignore[attr-defined]
            Bucket=Bucket,
            Key=Key,
            Body=Body,
            ContentType=ContentType,
            CacheControl=CacheControl,
        )
        if not isinstance(response, Mapping):
            return WriteReceipt("", 0, "")
        metadata = response.get("ResponseMetadata")
        if not isinstance(metadata, Mapping):
            return WriteReceipt("", 0, "")
        status = metadata.get("HTTPStatusCode")
        request_id = metadata.get("RequestId")
        positive = (
            type(status) is int
            and status == 200
            and isinstance(request_id, str)
            and bool(request_id)
        )
        return WriteReceipt(
            key=Key if positive else "",
            status_code=status if type(status) is int else 0,
            request_id=request_id if isinstance(request_id, str) else "",
        )

    def close(self) -> None:
        self._raw.close()  # type: ignore[attr-defined]
