from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import httpx

from haoai_backend.app import create_app
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import StoredMedia
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_runtime.assembly import GenerationWorkerConfiguration
import test_app_chat_media_admission_integration as admission_app_module
import test_execution_snapshot_reader as snapshot_reader_module
import generation_core.test_admission_staging as app_admission_staging_module


_PNG = b"\x89PNG\r\n\x1a\n"
_PNG_BASE64 = base64.b64encode(_PNG).decode("ascii")


class _Response:
    status_code = 200
    headers = {"content-length": "0"}

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self._body


class _HttpClient:
    def __init__(self, body: bytes, calls: list[dict[str, object]]) -> None:
        self._body = body
        self._calls = calls

    async def request(self, method, url, *, headers=None, content=None, **kwargs):
        self._calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "content": content,
                "follow_redirects": kwargs.get("follow_redirects"),
            }
        )
        return _Response(self._body)


class _HttpClientContext(AbstractAsyncContextManager):
    def __init__(self, client: _HttpClient) -> None:
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Storage:
    def __init__(self, calls: list[StoredMedia]) -> None:
        self._calls = calls

    def store_bytes_or_provider_content(self, reference, object_policy):
        assert reference.kind == "bytes"
        assert reference.content is not None
        assert object_policy == {"max_bytes": 10 * 1024 * 1024}
        stored = StoredMedia(
            locator="https://managed.example.test/image.png",
            content_digest=hashlib.sha256(reference.content).hexdigest(),
            media_type=reference.media_type or "",
            size_bytes=len(reference.content),
        )
        self._calls.append(stored)
        return stored


class _UnusedFetcher:
    async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
        raise AssertionError("base64 results must not fetch public media")


def test_default_app_lifespan_does_not_start_generation_resources() -> None:
    app = create_app()

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert app.state.generation_worker_runtime is None
            assert app.state.generation_worker_service is None

    asyncio.run(run_lifespan())
    assert app.state.generation_worker_service is None


def test_http_admission_is_processed_by_server_worker_and_readable_by_task_get(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(app_admission_staging_module, "_G2_JOINT_PROVIDER", "openai")
    monkeypatch.setattr(
        app_admission_staging_module,
        "_joint_local_dummy_crypto",
        snapshot_reader_module._authenticated_joint_crypto,
    )

    provider_calls: list[dict[str, object]] = []
    stored_media: list[StoredMedia] = []
    worker_session_calls = 0
    runtime_id = 0
    original_create_app = admission_app_module.create_app

    def create_app_with_worker(**kwargs):
        nonlocal runtime_id
        admission_configuration = kwargs["chat_media_admission_configuration"]
        original_session_factory = kwargs["session_factory"]

        def worker_session_factory():
            nonlocal worker_session_calls
            worker_session_calls += 1
            return original_session_factory()

        def new_runtime_id() -> str:
            nonlocal runtime_id
            runtime_id += 1
            return f"server-worker-id-{runtime_id}"

        registry = build_credential_registry(
            admission_configuration.registry_capabilities
        )
        runtime = GenerationRuntimeConfig(
            session_factory=worker_session_factory,
            db_clock=lambda: datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc),
            id_factory=new_runtime_id,
            http_client_factory=lambda: _HttpClientContext(
                _HttpClient(
                    (
                        b'{"data":[{"b64_json":"'
                        + _PNG_BASE64.encode("ascii")
                        + b'"}]}'
                    ),
                    provider_calls,
                )
            ),
            credential_resolver=registry.resolve_original_credential,
            provider_factories=(),
            media_store_factory=lambda: _Storage(stored_media),
            local_muse_staging_dir=None,
            public_media_fetcher_factory=lambda: _UnusedFetcher(),
            bound_provider_content_fetcher_factory=lambda: object(),
            local_muse_capability_factory=lambda: object(),
            r2_storage_factory=lambda: object(),
            shutdown_timeout_seconds=2.0,
        )
        kwargs["generation_worker_configuration"] = GenerationWorkerConfiguration(
            runtime,
            admission_configuration.registry_capabilities,
        )
        return original_create_app(**kwargs)

    monkeypatch.setattr(admission_app_module, "create_app", create_app_with_worker)
    system = admission_app_module._build_system(tmp_path)

    assert worker_session_calls == 0
    assert provider_calls == []
    assert stored_media == []
    app_runtime = system.app.state.generation_worker_runtime
    assert app_runtime is not None

    async def exercise_http_and_lifespan() -> dict[str, object]:
        headers = {
            "Authorization": f"Bearer {system.token_for()}",
            "Idempotency-Key": "server-worker-image-single-01",
        }
        body = {
            "model_id": app_admission_staging_module._G2_JOINT_MODEL_ID,
            "prompt": "A quiet room at sunrise",
            "target": {"kind": "standalone"},
            "size": None,
            "reference_images": [],
        }
        async with system.app.router.lifespan_context(system.app):
            assert system.app.state.generation_worker_service is not None
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=system.app,
                    raise_app_exceptions=False,
                ),
                base_url="http://image-worker.test",
            ) as client:
                admitted = await client.post(
                    "/api/chat/image",
                    headers=headers,
                    json=body,
                )
                assert admitted.status_code == 200, admitted.text
                task_id = admitted.json()["task_id"]
                deadline = monotonic() + 5.0
                observed: dict[str, object] = {}
                while monotonic() < deadline:
                    response = await client.get(
                        "/api/chat/tasks",
                        params={"task_id": task_id},
                        headers=headers,
                    )
                    assert response.status_code == 200, response.text
                    observed = response.json()
                    if observed.get("status") == "completed":
                        break
                    await asyncio.sleep(0.025)
                assert observed.get("status") == "completed", observed
                result = observed.get("result")
                assert isinstance(result, str)
                result = json.loads(result)
                assert result.get("url") == stored_media[0].locator
                assert result.get("content_digest") == stored_media[0].content_digest
                return observed

    observed = asyncio.run(exercise_http_and_lifespan())

    assert observed["status"] == "completed"
    assert len(provider_calls) == 1
    assert provider_calls[0]["method"] == "POST"
    assert provider_calls[0]["url"].endswith("/images/generations")
    assert provider_calls[0]["follow_redirects"] is False
    assert len(stored_media) == 1
    assert worker_session_calls > 0
    shutdown = system.app.state.generation_worker_shutdown
    assert shutdown.reason == "application lifespan shutdown"
    assert shutdown.timed_out is False
    assert shutdown.unreleased_resources == ()
    system.engine.dispose()
