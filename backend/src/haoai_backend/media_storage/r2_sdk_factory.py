from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from .transfer_types import (
    PlannedS3Client,
    R2ClientSettings,
    S3ClientFactory,
    S3ObjectRead,
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
        config_values: dict[str, object] = {
            "signature_version": settings.signature_version,
            "retries": {
                "mode": settings.retry_mode,
                "total_max_attempts": settings.total_max_attempts,
            },
            "connect_timeout": settings.connect_timeout_seconds,
            "read_timeout": settings.read_timeout_seconds,
            "proxies": {},
        }
        if settings.operation_policy == "owned_once":
            config_values["s3"] = {"addressing_style": "path"}
        config = self._config_factory(**config_values)
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
        request_guard = None
        if settings.operation_policy == "owned_once":
            if not _supports_owned_once(client):
                try:
                    client.close()
                except BaseException as cleanup_error:
                    raise TypeError(
                        "S3 client does not support planned owned writes and failed to close"
                    ) from cleanup_error
                raise TypeError("S3 client does not support planned owned writes")
            try:
                request_guard = _install_owned_origin_guard(
                    client,
                    settings.endpoint_url,
                    settings.bucket,
                )
            except Exception as capability_error:
                try:
                    client.close()
                except BaseException as cleanup_error:
                    raise TypeError(
                        "S3 client cannot enforce the trusted origin and failed to close"
                    ) from cleanup_error
                raise TypeError(
                    "S3 client cannot enforce the trusted request origin"
                ) from capability_error
        return _Boto3S3Client(client, settings.bucket, request_guard)


def _supports_owned_once(client: object) -> bool:
    if not callable(getattr(client, "get_object", None)):
        return False
    metadata = getattr(client, "meta", None)
    service_model = getattr(metadata, "service_model", None)
    if service_model is None:
        return False
    operation_model = getattr(service_model, "operation_model", None)
    if not callable(operation_model):
        return False
    try:
        put_input = operation_model("PutObject").input_shape
        get_input = operation_model("GetObject").input_shape
    except Exception:
        return False
    put_members = getattr(put_input, "members", None)
    get_members = getattr(get_input, "members", None)
    return (
        isinstance(put_members, Mapping)
        and "IfNoneMatch" in put_members
        and {"Bucket", "Key"}.issubset(put_members)
        and isinstance(get_members, Mapping)
        and {"Bucket", "Key"}.issubset(get_members)
    )


class _OwnedRequestGuard:
    def __init__(self, endpoint_url: str, bucket: str) -> None:
        self._trusted_origin = _url_origin(endpoint_url)
        self._path_prefix = urlsplit(endpoint_url).path.rstrip("/")
        self._bucket = bucket
        self._local = threading.local()

    @contextmanager
    def expect(
        self,
        operation_name: str,
        bucket: str,
        key: str,
    ) -> Iterator[None]:
        if getattr(self._local, "expected", None) is not None:
            raise RuntimeError("planned R2 client received a nested S3 operation")
        if bucket != self._bucket:
            raise RuntimeError("planned R2 client rejected an unexpected bucket")
        expected = _ExpectedRequest(
            operation_name=operation_name,
            path=(
                f"{self._path_prefix}/{quote(bucket, safe='')}/"
                f"{quote(key, safe='/~')}"
            ),
        )
        self._local.expected = expected
        try:
            yield
        finally:
            del self._local.expected

    def before_send(
        self,
        *,
        request: object,
        event_name: str,
        **_kwargs: object,
    ) -> None:
        expected = getattr(self._local, "expected", None)
        operation_name = event_name.rsplit(".", 1)[-1]
        if (
            not isinstance(expected, _ExpectedRequest)
            or operation_name != expected.operation_name
        ):
            raise RuntimeError("planned R2 client rejected an unexpected S3 operation")
        url = getattr(request, "url", None)
        if not isinstance(url, str):
            raise RuntimeError("planned R2 client rejected an invalid request URL")
        if _url_origin(url) != self._trusted_origin:
            raise RuntimeError("planned R2 client rejected an untrusted request origin")
        parsed = urlsplit(url)
        if parsed.path != expected.path or parsed.query or parsed.fragment:
            raise RuntimeError("planned R2 client rejected a changed bucket or key")
        if expected.sent:
            raise RuntimeError("planned R2 client rejected an SDK retry")
        expected.sent = True


@dataclass(slots=True)
class _ExpectedRequest:
    operation_name: str
    path: str
    sent: bool = False


def _install_owned_origin_guard(
    client: object,
    endpoint_url: str,
    bucket: str,
) -> _OwnedRequestGuard:
    metadata = getattr(client, "meta", None)
    events = getattr(metadata, "events", None)
    register_first = getattr(events, "register_first", None)
    if not callable(register_first):
        raise TypeError("S3 client events are unavailable")
    guard = _OwnedRequestGuard(endpoint_url, bucket)
    register_first("before-send.s3", guard.before_send)
    return guard


def _url_origin(url: object) -> tuple[str, str, int]:
    if not isinstance(url, str):
        raise ValueError("S3 request URL is invalid")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("S3 request URL is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("S3 request URL is invalid")
    return (
        parsed.scheme.lower(),
        hostname.lower(),
        (
            port
            if port is not None
            else (443 if parsed.scheme.lower() == "https" else 80)
        ),
    )


class _Boto3S3Client(PlannedS3Client):
    def __init__(
        self,
        raw: object,
        bucket: str,
        request_guard: _OwnedRequestGuard | None,
    ) -> None:
        self._raw = raw
        self._bucket = bucket
        self._request_guard = request_guard

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: Any,
        ContentType: str,
        CacheControl: str,
    ) -> WriteReceipt:
        if self._request_guard is not None:
            raise TypeError("owned R2 writes require the conditional planned operation")
        if Bucket != self._bucket or not isinstance(Key, str) or not Key:
            raise ValueError("PutObject must use the configured bucket and original key")
        response = self._raw.put_object(  # type: ignore[attr-defined]
            Bucket=Bucket,
            Key=Key,
            Body=Body,
            ContentType=ContentType,
            CacheControl=CacheControl,
        )
        return _write_receipt(response, Key)

    def put_object_if_absent(
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
        request_guard = self._require_request_guard()
        try:
            with request_guard.expect("PutObject", Bucket, Key):
                response = self._raw.put_object(  # type: ignore[attr-defined]
                    Bucket=Bucket,
                    Key=Key,
                    Body=Body,
                    ContentType=ContentType,
                    CacheControl=CacheControl,
                    IfNoneMatch="*",
                )
        except BaseException as exc:
            conditional_failure = _conditional_write_failure(exc)
            if conditional_failure is None:
                raise
            return conditional_failure
        return _write_receipt(response, Key)

    def get_object_stream(self, *, Bucket: str, Key: str) -> S3ObjectRead:
        if Bucket != self._bucket or not isinstance(Key, str) or not Key:
            raise ValueError("GetObject must use the configured bucket and original key")
        request_guard = self._require_request_guard()
        with request_guard.expect("GetObject", Bucket, Key):
            response = self._raw.get_object(  # type: ignore[attr-defined]
                Bucket=Bucket,
                Key=Key,
            )
        if not isinstance(response, Mapping):
            raise TypeError("GetObject response must be a mapping")
        body = response.get("Body")
        if body is None:
            raise TypeError("GetObject response must include a body")
        metadata = response.get("ResponseMetadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        content_length = response.get("ContentLength")
        return S3ObjectRead(
            status_code=(
                metadata.get("HTTPStatusCode")
                if type(metadata.get("HTTPStatusCode")) is int
                else 0
            ),
            content_length=content_length if type(content_length) is int else None,
            content_type=(
                response.get("ContentType")
                if isinstance(response.get("ContentType"), str)
                else None
            ),
            content_range=(
                response.get("ContentRange")
                if isinstance(response.get("ContentRange"), str)
                else None
            ),
            body=body,
            etag=response.get("ETag") if isinstance(response.get("ETag"), str) else None,
            request_id=(
                metadata.get("RequestId")
                if isinstance(metadata.get("RequestId"), str)
                else None
            ),
        )

    def close(self) -> None:
        self._raw.close()  # type: ignore[attr-defined]

    def _require_request_guard(self) -> _OwnedRequestGuard:
        if self._request_guard is None:
            raise TypeError("planned R2 client requires the owned-once policy")
        return self._request_guard


def _write_receipt(response: object, key: str) -> WriteReceipt:
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
        key=key if positive else "",
        status_code=status if type(status) is int else 0,
        request_id=request_id if isinstance(request_id, str) else "",
    )


def _conditional_write_failure(error: BaseException) -> WriteReceipt | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    error_detail = response.get("Error")
    metadata = response.get("ResponseMetadata")
    error_code = (
        error_detail.get("Code") if isinstance(error_detail, Mapping) else None
    )
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
    if status == 412 or error_code in {"PreconditionFailed", "412"}:
        status_code = 412
    elif status == 409 or error_code in {"ConditionalRequestConflict", "409"}:
        status_code = 409
    else:
        return None
    request_id = metadata.get("RequestId") if isinstance(metadata, Mapping) else None
    return WriteReceipt(
        "",
        status_code,
        request_id if isinstance(request_id, str) else "",
    )
