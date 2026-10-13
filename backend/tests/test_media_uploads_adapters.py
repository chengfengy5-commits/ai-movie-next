from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from haoai_backend.media_storage.transfer_types import (
    R2Configuration,
    SecretValue,
    WriteReceipt,
)
from haoai_backend.media_uploads.adapters import (
    PinnedPublicMediaFetchAdapter,
    R2MediaUploadStorage,
    managed_origin_map_from_configuration,
)


class Secret:
    def __init__(self, value: str) -> None:
        self.value = value

    def reveal(self) -> SecretValue:
        return SecretValue(self.value)


class FakeS3Client:
    def __init__(self, *, fail_put: bool = False, fail_close: bool = False) -> None:
        self.fail_put = fail_put
        self.fail_close = fail_close
        self.calls: list[dict[str, Any]] = []
        self.close_calls = 0

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_put:
            raise OSError("synthetic unknown acknowledgement")
        return WriteReceipt(kwargs["Key"], 200, "request-1")

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise OSError("synthetic close failure")


class FakeS3Factory:
    def __init__(self, client: FakeS3Client) -> None:
        self.client = client
        self.calls: list[tuple[object, object, object]] = []

    def create(self, settings, access_key, secret_key):
        self.calls.append((settings, access_key, secret_key))
        return self.client


def _configuration(tmp_path: Path) -> R2Configuration:
    return R2Configuration(
        endpoint_url="https://account.r2.cloudflarestorage.com",
        bucket="media-bucket",
        public_base_url="https://cdn.test",
        transfer_tmp_dir=tmp_path,
        business_type="unused-global-business",
        environment="test",
        connect_timeout_seconds=2,
        read_timeout_seconds=5,
        managed_origin_map=(("https://legacy.test", "https://cdn.test"),),
        legacy_public_origins=(("https", "older.test", 443),),
    )


def test_r2_adapter_is_lazy_and_preserves_key_prefix_and_jpeg_mapping(
    tmp_path: Path,
) -> None:
    client = FakeS3Client()
    factory = FakeS3Factory(client)
    storage = R2MediaUploadStorage(
        configuration=_configuration(tmp_path),
        access_key=Secret("access"),
        secret_key=Secret("secret"),
        client_factory=factory,
        clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
        id_factory=lambda: "fixed-id",
    )
    assert factory.calls == []

    url = storage.store_media(
        b"\xff\xd8\xffpayload",
        business_type="upload",
        extension=".jpg",
        content_type="image/jpeg",
    )

    assert url == "https://cdn.test/2026/10/14/upload/fixed-id.jpg"
    assert len(factory.calls) == 1
    assert client.calls[0]["Key"] == "2026/10/14/upload/fixed-id.jpg"
    assert client.calls[0]["ContentType"] == "image/jpeg"
    assert client.calls[0]["Body"].closed
    assert client.close_calls == 1


def test_private_adapter_stores_wma_with_its_explicit_mime_and_key(
    tmp_path: Path,
) -> None:
    client = FakeS3Client()
    storage = R2MediaUploadStorage(
        configuration=_configuration(tmp_path),
        access_key=Secret("access"),
        secret_key=Secret("secret"),
        client_factory=FakeS3Factory(client),
        clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
        id_factory=lambda: "voice-id",
    )

    url = storage.store_media(
        b"synthetic WMA bytes",
        business_type="audio",
        extension=".wma",
        content_type="audio/x-ms-wma",
    )

    assert url.endswith("/2026/10/14/audio/voice-id.wma")
    assert client.calls[0]["ContentType"] == "audio/x-ms-wma"


def test_unknown_put_acknowledgement_is_not_retried_and_resources_close(
    tmp_path: Path,
) -> None:
    client = FakeS3Client(fail_put=True)
    factory = FakeS3Factory(client)
    storage = R2MediaUploadStorage(
        configuration=_configuration(tmp_path),
        access_key=Secret("access"),
        secret_key=Secret("secret"),
        client_factory=factory,
    )

    with pytest.raises(RuntimeError, match="outcome is unverified"):
        storage.store_media(
            b"audio",
            business_type="audio",
            extension=".mp3",
            content_type="audio/mpeg",
        )

    assert len(factory.calls) == 1
    assert client.close_calls == 1
    assert client.calls[0]["Body"].closed


def test_failed_close_is_retained_for_owned_cleanup_retry(tmp_path: Path) -> None:
    client = FakeS3Client(fail_close=True)
    storage = R2MediaUploadStorage(
        configuration=_configuration(tmp_path),
        access_key=Secret("access"),
        secret_key=Secret("secret"),
        client_factory=FakeS3Factory(client),
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        storage.store_media(
            b"audio",
            business_type="audio",
            extension=".mp3",
            content_type="audio/mpeg",
        )

    assert storage.pending_cleanup_count == 1
    assert storage.retry_pending_cleanup() == (False,)


def test_managed_origins_are_derived_only_from_explicit_configuration(
    tmp_path: Path,
) -> None:
    mapping = managed_origin_map_from_configuration(_configuration(tmp_path))

    assert mapping["https://legacy.test"] == "https://cdn.test"
    assert mapping[("https", "older.test", 443)] == (
        "https",
        "cdn.test",
        443,
    )
    assert mapping[("https", "cdn.test", 443)] == (
        "https",
        "cdn.test",
        443,
    )
    assert managed_origin_map_from_configuration(None) == {}


def test_pinned_fetch_adapter_forwards_bounds_and_returns_typed_bytes() -> None:
    class FakePinnedFetcher:
        async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
            assert reference.kind == "public_http"
            assert reference.locator == "https://public.example/media.mp4"
            assert max_bytes == 1234
            assert timeout_seconds == 9
            from haoai_backend.generation_contracts import PreparedReference

            return PreparedReference(
                kind="bytes",
                locator=None,
                content=b"video",
                media_type="video/mp4",
            )

    import asyncio

    result = asyncio.run(
        PinnedPublicMediaFetchAdapter(FakePinnedFetcher()).fetch(  # type: ignore[arg-type]
            "https://public.example/media.mp4",
            max_bytes=1234,
            timeout_seconds=9,
        )
    )

    assert result.content == b"video"
    assert result.content_type == "video/mp4"
