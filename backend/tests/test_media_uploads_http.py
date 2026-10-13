from __future__ import annotations

import asyncio
import tempfile
import threading
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.datastructures import FormData, UploadFile as StarletteUploadFile
from starlette.requests import Request as StarletteRequest

from haoai_backend.generation_contracts import TransferFailure
from haoai_backend.media_uploads.application import MediaUploadApplication
from haoai_backend.media_uploads.authentication import MediaUploadActor
from haoai_backend.media_uploads.domain import MAX_RETRY_BYTES
from haoai_backend.media_uploads.http import (
    PROXY_DOWNLOAD_DISABLED_DETAIL,
    build_media_uploads_router,
)
from haoai_backend.media_uploads.ports import FetchedPublicMedia
from haoai_backend.authentication.errors import credentials_error


class RecordingStorage:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str, str, str, int]] = []

    def store_media(
        self,
        content: bytes,
        *,
        business_type: str,
        extension: str,
        content_type: str,
    ) -> str:
        self.calls.append(
            (content, business_type, extension, content_type, threading.get_ident())
        )
        return f"https://cdn.test/{business_type}/file{extension}"


class RecordingFetcher:
    def __init__(self, result: FetchedPublicMedia | TransferFailure) -> None:
        self.result = result
        self.calls: list[tuple[str, int, float]] = []

    async def fetch(self, url: str, *, max_bytes: int, timeout_seconds: float):
        self.calls.append((url, max_bytes, timeout_seconds))
        return self.result


def _app(
    *,
    resolver,
    storage: Any | None = None,
    fetcher: RecordingFetcher | None = None,
    managed_origin_map=None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_media_uploads_router(
            application=MediaUploadApplication(
                storage=storage,
                public_media_fetcher=fetcher,
                managed_origin_map=managed_origin_map,
            ),
            resolve_active_member=resolver,
        )
    )
    return app


def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    files=None,
    json_body: Any = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    authenticated: bool = True,
) -> httpx.Response:
    async def send() -> httpx.Response:
        request_headers = dict(headers or {})
        if authenticated:
            request_headers.setdefault("authorization", "Bearer test-token")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://media-uploads.test",
            trust_env=False,
        ) as client:
            request_options: dict[str, Any] = {"headers": request_headers}
            if files is not None:
                request_options["files"] = files
            if json_body is not None:
                request_options["json"] = json_body
            if content is not None:
                request_options["content"] = content
            return await client.request(method, path, **request_options)

    return asyncio.run(send())


def _member(authorization: str) -> MediaUploadActor:
    if authorization != "Bearer test-token":
        raise credentials_error()
    return MediaUploadActor(user_id="member")


def test_authentication_precedes_malformed_upload_and_file_validation() -> None:
    events: list[str] = []

    def reject(_authorization: str) -> MediaUploadActor:
        events.append("auth")
        raise credentials_error()

    app = _app(resolver=reject)
    response = _request(
        app,
        "POST",
        "/api/upload/image",
        content=b"malformed multipart body",
        headers={"content-type": "multipart/form-data"},
        authenticated=False,
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert events == ["auth"]


def test_authentication_precedes_retry_json_validation() -> None:
    events: list[str] = []

    def reject(_authorization: str) -> MediaUploadActor:
        events.append("auth")
        raise credentials_error()

    response = _request(
        _app(resolver=reject),
        "POST",
        "/api/upload/retry-oss",
        content=b"{",
        headers={"content-type": "application/json"},
        authenticated=False,
    )

    assert response.status_code == 401
    assert events == ["auth"]


def test_member_auth_closes_before_upload_and_sdk_work_runs_off_loop() -> None:
    events: list[str] = []
    storage = RecordingStorage()
    loop_thread = threading.get_ident()

    def resolver(_authorization: str) -> MediaUploadActor:
        events.append("auth-closed")
        return MediaUploadActor(user_id="member")

    original_store = storage.store_media

    def observed_store(*args, **kwargs):
        events.append("store")
        return original_store(*args, **kwargs)

    storage.store_media = observed_store  # type: ignore[method-assign]
    response = _request(
        _app(resolver=resolver, storage=storage),
        "POST",
        "/api/upload/image",
        files={"file": ("cover.jpeg", b"\xff\xd8\xffpayload", "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json() == {"url": "https://cdn.test/upload/file.jpg"}
    assert events == ["auth-closed", "store"]
    assert storage.calls[0][1:4] == ("upload", ".jpg", "image/jpeg")
    assert storage.calls[0][4] != loop_thread


def test_upload_magic_and_extension_errors_keep_the_legacy_http_shape() -> None:
    response = _request(
        _app(resolver=_member),
        "POST",
        "/api/upload/image",
        files={"file": ("cover.jpg", b"not a jpeg", "image/jpeg")},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "文件内容与扩展名不匹配，无法识别为有效的图片文件"
    }


def test_missing_storage_configuration_fails_as_a_gateway_error() -> None:
    response = _request(
        _app(resolver=_member),
        "POST",
        "/api/upload/audio",
        files={"file": ("voice.mp3", b"audio", "audio/mpeg")},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "云端上传失败"}


def test_missing_file_is_validated_only_after_authentication() -> None:
    app = _app(resolver=_member)
    response = _request(
        app,
        "POST",
        "/api/upload/audio",
        files={},
    )

    assert response.status_code == 422


def test_proxy_download_is_always_disabled_after_member_auth() -> None:
    storage = RecordingStorage()
    fetcher = RecordingFetcher(FetchedPublicMedia(b"unused", "video/mp4"))
    response = _request(
        _app(resolver=_member, storage=storage, fetcher=fetcher),
        "GET",
        "/api/chat/proxy-download?url=https://public.example/video.mp4",
    )

    assert response.status_code == 403
    assert response.json() == {"detail": PROXY_DOWNLOAD_DISABLED_DETAIL}
    assert storage.calls == []
    assert fetcher.calls == []


def test_managed_retry_url_returns_the_original_without_fetch_or_upload() -> None:
    original = "https://legacy-media.test/video.mp4?token=kept"
    storage = RecordingStorage()
    fetcher = RecordingFetcher(FetchedPublicMedia(b"unused", "video/mp4"))
    mapping = {
        ("https", "legacy-media.test", 443): ("https", "cdn.test", 443),
        ("https", "cdn.test", 443): ("https", "cdn.test", 443),
    }
    response = _request(
        _app(
            resolver=_member,
            storage=storage,
            fetcher=fetcher,
            managed_origin_map=mapping,
        ),
        "POST",
        "/api/upload/retry-oss",
        json_body={"url": original},
    )

    assert response.status_code == 200
    assert response.json() == {"url": original}
    assert fetcher.calls == []
    assert storage.calls == []


@pytest.mark.parametrize(
    ("failure", "status", "detail"),
    [
        (TransferFailure("unsafe_url", "URL 未通过安全检查"), 400, "URL 未通过安全检查"),
        (TransferFailure("reference_too_large", "超出限制"), 413, "文件大小超过限制（最大 50MB）"),
        (TransferFailure("http_status", "重定向未跟随"), 502, "下载失败"),
    ],
)
def test_retry_transfer_failures_map_to_legacy_http_layers(
    failure: TransferFailure,
    status: int,
    detail: str,
) -> None:
    fetcher = RecordingFetcher(failure)
    response = _request(
        _app(resolver=_member, storage=RecordingStorage(), fetcher=fetcher),
        "POST",
        "/api/upload/retry-oss",
        json_body={"url": "https://public.example/clip.mp4"},
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert fetcher.calls[0][1] == MAX_RETRY_BYTES


def test_retry_rejects_unsupported_extension_before_fetching() -> None:
    fetcher = RecordingFetcher(FetchedPublicMedia(b"audio", "audio/mpeg"))
    response = _request(
        _app(resolver=_member, storage=RecordingStorage(), fetcher=fetcher),
        "POST",
        "/api/upload/retry-oss",
        json_body={"url": "https://public.example/clip.wma"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "仅支持图片、音频和视频格式"}
    assert fetcher.calls == []


def test_retry_without_suffix_uses_the_legacy_mp4_default() -> None:
    storage = RecordingStorage()
    fetcher = RecordingFetcher(FetchedPublicMedia(b"video", "video/mp4"))
    response = _request(
        _app(resolver=_member, storage=storage, fetcher=fetcher),
        "POST",
        "/api/upload/retry-oss",
        json_body={"url": "https://public.example/temporary"},
    )

    assert response.status_code == 200
    assert fetcher.calls[0][0] == "https://public.example/temporary"
    assert storage.calls[0][1:4] == ("gen", ".mp4", "video/mp4")


def test_empty_retry_url_preserves_the_legacy_client_error() -> None:
    response = _request(
        _app(resolver=_member),
        "POST",
        "/api/upload/retry-oss",
        json_body={"url": "  "},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "URL 不能为空"}


def test_missing_auth_resolver_fails_closed_before_body_access() -> None:
    response = _request(
        _app(resolver=None),
        "POST",
        "/api/upload/retry-oss",
        content=b"not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务尚未接线"}


class BlockingStorage:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()

    def store_media(
        self,
        content: bytes,
        *,
        business_type: str,
        extension: str,
        content_type: str,
    ) -> str:
        del content, business_type, extension, content_type
        self.started.set()
        try:
            if not self.release.wait(timeout=2):
                raise TimeoutError("synthetic worker release timed out")
            return "https://cdn.test/audio/finished.mp3"
        finally:
            self.closed.set()


def test_cancelled_upload_waits_for_owned_off_loop_storage_cleanup() -> None:
    storage = BlockingStorage()
    application = MediaUploadApplication(storage=storage, public_media_fetcher=None)

    async def cancel_owned_upload() -> None:
        task = asyncio.create_task(application.upload("audio", "voice.mp3", b"audio"))
        assert await asyncio.to_thread(storage.started.wait, 1)
        task.cancel()
        storage.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_owned_upload())
    assert storage.closed.is_set()


class ObservableUploadFile(StarletteUploadFile):
    """A real UploadFile whose close window and underlying file are observable."""

    def __init__(
        self,
        filename: str,
        content: bytes,
        *,
        close_gate: asyncio.Event | None = None,
        close_error: OSError | None = None,
    ) -> None:
        file = tempfile.SpooledTemporaryFile()
        file.write(content)
        file.seek(0)
        super().__init__(file=file, filename=filename)
        self.close_gate = close_gate or asyncio.Event()
        if close_gate is None:
            self.close_gate.set()
        self.close_entered = asyncio.Event()
        self.close_calls = 0
        self.close_error = close_error

    async def close(self) -> None:
        self.close_calls += 1
        self.close_entered.set()
        if self.close_error is not None:
            raise self.close_error
        try:
            await asyncio.wait_for(self.close_gate.wait(), timeout=2)
        finally:
            await super().close()


def _return_controlled_form(monkeypatch, form: FormData) -> None:
    async def return_form(request: StarletteRequest, *args, **kwargs) -> FormData:
        request._form = form
        return form

    monkeypatch.setattr(StarletteRequest, "form", return_form)


def test_http_cleanup_drains_single_cancel_across_all_form_files(monkeypatch) -> None:
    async def exercise() -> None:
        close_gate = asyncio.Event()
        first = ObservableUploadFile("voice.mp3", b"audio", close_gate=close_gate)
        second = ObservableUploadFile("attachment.mp3", b"extra")
        _return_controlled_form(
            monkeypatch, FormData([("file", first), ("attachment", second)])
        )
        auth_events: list[str] = []

        def resolver(_authorization: str) -> MediaUploadActor:
            auth_events.append("authenticated")
            return MediaUploadActor(user_id="member")

        storage = RecordingStorage()
        app = _app(resolver=resolver, storage=storage)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://media-uploads.test",
            headers={"authorization": "Bearer test-token"},
            trust_env=False,
        ) as client:
            task = asyncio.create_task(
                client.post("/api/upload/audio", content=b"controlled request body")
            )
            try:
                await asyncio.wait_for(first.close_entered.wait(), timeout=2)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                close_gate.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=2)

                assert auth_events == ["authenticated"]
                assert len(storage.calls) == 1
                assert first.close_calls == 1
                assert first.file.closed
                assert second.close_calls == 1
                assert second.file.closed
            finally:
                close_gate.set()
                if not task.done():
                    task.cancel()
                    await asyncio.wait_for(
                        asyncio.gather(task, return_exceptions=True), timeout=2
                    )
                # Test teardown runs only after the product assertions; it never
                # supplies a close that could make the route appear successful.
                first.file.close()
                second.file.close()

    asyncio.run(exercise())


def test_http_cleanup_drains_repeated_cancel_after_storage_worker(monkeypatch) -> None:
    async def exercise() -> None:
        close_gate = asyncio.Event()
        first = ObservableUploadFile("voice.mp3", b"audio", close_gate=close_gate)
        second = ObservableUploadFile("attachment.mp3", b"extra")
        _return_controlled_form(
            monkeypatch, FormData([("file", first), ("attachment", second)])
        )
        storage = BlockingStorage()
        app = _app(resolver=_member, storage=storage)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://media-uploads.test",
            headers={"authorization": "Bearer test-token"},
            trust_env=False,
        ) as client:
            task = asyncio.create_task(
                client.post("/api/upload/audio", content=b"controlled request body")
            )
            try:
                started = await asyncio.wait_for(
                    asyncio.to_thread(storage.started.wait, 2), timeout=2
                )
                assert started
                task.cancel()
                storage.release.set()
                await asyncio.wait_for(first.close_entered.wait(), timeout=2)

                assert storage.closed.is_set()
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                close_gate.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=2)

                assert first.close_calls == 1
                assert first.file.closed
                assert second.close_calls == 1
                assert second.file.closed
            finally:
                storage.release.set()
                close_gate.set()
                if not task.done():
                    task.cancel()
                    await asyncio.wait_for(
                        asyncio.gather(task, return_exceptions=True), timeout=2
                    )
                # Test teardown runs only after the product assertions.
                first.file.close()
                second.file.close()

    asyncio.run(exercise())


def test_http_cleanup_closes_later_files_and_preserves_first_close_error(
    monkeypatch,
) -> None:
    async def exercise() -> None:
        failure = OSError("synthetic UploadFile close failure")
        first = ObservableUploadFile("voice.mp3", b"audio", close_error=failure)
        second = ObservableUploadFile("attachment.mp3", b"extra")
        _return_controlled_form(
            monkeypatch, FormData([("file", first), ("attachment", second)])
        )
        app = _app(resolver=_member, storage=RecordingStorage())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://media-uploads.test",
            headers={"authorization": "Bearer test-token"},
            trust_env=False,
        ) as client:
            try:
                with pytest.raises(
                    OSError, match="synthetic UploadFile close failure"
                ) as raised:
                    await client.post(
                        "/api/upload/audio", content=b"controlled request body"
                    )

                assert raised.value is failure
                assert first.close_calls == 1
                assert not first.file.closed
                assert second.close_calls == 1
                assert second.file.closed
            finally:
                # Preserve the failing resource's observed state through all
                # assertions; close raw temporary handles only in test teardown.
                first.file.close()
                second.file.close()

    asyncio.run(exercise())
