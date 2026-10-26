"""Host composition and upload-route checks using local SQLite and a fake SDK."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import uuid
import zlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, insert
from sqlalchemy.engine import Engine

from backend.server import __main__ as server_cli
from backend.server import application as server_application
from backend.server.application import _OwnedResources, create_server_host
from backend.server.application import ServerHostAssemblyError
from backend.server.configuration import load_server_configuration
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.public_site_config.tables import (
    metadata as public_config_metadata,
    system_configs,
)


_SECRET = "synthetic-server-secret-for-tests"
_PNG = b"\x89PNG\r\n\x1a\nsynthetic-image-data"


def _environment(tmp_path: Path, *, storage: bool) -> dict[str, str]:
    values = {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'host.sqlite'}",
        "SECRET_KEY": _SECRET,
    }
    if storage:
        transfer_dir = tmp_path / "transfer"
        transfer_dir.mkdir()
        values.update(
            {
                "OBJECT_STORAGE_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
                "OBJECT_STORAGE_ACCESS_KEY_ID": "synthetic-access-key",
                "OBJECT_STORAGE_SECRET_ACCESS_KEY": "synthetic-r2-secret",
                "OBJECT_STORAGE_BUCKET": "haoai-media",
                "OBJECT_STORAGE_PUBLIC_BASE_URL": "https://cdn.example.test/media",
                "TRANSFER_TMP_DIR": str(transfer_dir),
            }
        )
    return values


def _seed_authenticated_user(engine: Engine) -> str:
    authentication_metadata.create_all(engine)
    public_config_metadata.create_all(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            {
                "id": "user-server-test",
                "username": "server-test",
                "email": "server-test@example.test",
                "hashed_password": "synthetic-only",
                "is_superuser": False,
                "membership_type": "premium",
                "membership_expires_at": None,
                "avatar_url": None,
                "bio": None,
                "created_at": now - timedelta(days=1),
                "password_updated_at": now - timedelta(days=1),
            },
        )
        connection.execute(
            insert(user_sessions),
            {
                "id": "session-server-test",
                "user_id": "user-server-test",
                "jti": "jti-server-test",
                "user_agent": "local test",
                "ip_address": "127.0.0.1",
                "device_name": "test",
                "is_active": True,
                "created_at": now - timedelta(minutes=5),
                "last_seen_at": now,
                "revoked_at": None,
            },
        )
        connection.execute(
            insert(system_configs),
            {
                "id": "site-config-server-test",
                "site_title": "Synthetic site",
                "site_tagline": "",
                "storyboard_cost_multiplier": 1,
                "membership_benefits": "[]",
                "payments_enabled": 0,
                "tutorial_url": None,
                "purchase_qr_code_url": None,
                "purchase_contact_info": None,
            },
        )

    token = JoseTokenCodec(_SECRET).encode(
        {
            "sub": "user-server-test",
            "jti": "jti-server-test",
            "exp": (now + timedelta(hours=1)).replace(tzinfo=timezone.utc),
        }
    )
    return token


class _FakeS3Client:
    def __init__(self, *, close_failures: int = 0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.head_bucket_calls: list[str] = []
        self.close_calls = 0
        self.close_failures = close_failures

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(
            {
                **kwargs,
                "body_bytes": kwargs["Body"].read(),
            }
        )
        return {
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "synthetic-request-id",
            }
        }

    def head_bucket(self, *, Bucket: str) -> None:
        self.head_bucket_calls.append(Bucket)

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls <= self.close_failures:
            raise OSError("synthetic close failure")


class _FakeBotoSession:
    def __init__(self, state: dict[str, Any], **credentials: object) -> None:
        self.state = state
        self.state["credentials"].append(credentials)

    def client(self, service: str, **kwargs: object) -> _FakeS3Client:
        assert service == "s3"
        self.state["client_options"].append(kwargs)
        client = _FakeS3Client(close_failures=self.state["close_failures"])
        self.state["clients"].append(client)
        return client


def _sdk_factories(
    *, close_failures: int = 0
) -> tuple[Any, Any, dict[str, Any]]:
    state: dict[str, Any] = {
        "close_failures": close_failures,
        "credentials": [],
        "client_options": [],
        "config_options": [],
        "clients": [],
    }

    def session_factory(**kwargs: object) -> _FakeBotoSession:
        return _FakeBotoSession(state, **kwargs)

    def config_factory(**kwargs: object) -> object:
        state["config_options"].append(kwargs)
        return kwargs

    return session_factory, config_factory, state


def _prepared_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return (
            struct.pack(">I", len(data))
            + payload
            + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
        + chunk(b"IEND", b"")
    )


def _provision_image_single_bundle(tmp_path: Path) -> Path:
    from backend.server.credentials import provision_credential_bundle

    encode = lambda value: base64.b64encode(value).decode("ascii")
    source = {
        "format": "haoai-root-enrollment-input-v1",
        "proof_key": {"key_id": "image-host-proof-v1", "key_base64": encode(b"p" * 32)},
        "guard_key": {"key_id": "image-host-guard-v1", "key_base64": encode(b"g" * 32)},
        "aead_key": {
            "key_id": "image-host-aead-v1",
            "algorithm_version": "aes-256-gcm-v1",
            "key_base64": encode(b"a" * 32),
        },
        "records": [
            {
                "record_id": "image-host-record-1",
                "principal_id": "30000000-0000-4000-8000-000000000001",
                "scope": {
                    "source": "user",
                    "row_id": "20000000-0000-4000-8000-000000000001",
                    "owner_or_system": "10000000-0000-4000-8000-000000000001",
                    "provider": "openai",
                },
                "issuer_namespace": "image-host-provider-account-v1",
                "stable_account_subject": "synthetic-image-host-subject",
                "key_generation": "image-host-generation-1",
                "material_ref": "synthetic://image-host/provider",
                "provider_secret": "synthetic-image-host-provider-secret",
                "evidence_ref": "synthetic-evidence://image-host/record-1",
                "evidence_version": "image-host-evidence-v1",
                "valid_from": "2026-01-01T00:00:00+00:00",
                "valid_until": None,
                "revoked_at": None,
                "endpoint_policy": {
                    "provider_origin": "https://provider.example.test",
                    "api_base_path": "/v2",
                    "allowed_content_paths": [],
                    "policy_version": "image-host-endpoint-v1",
                },
            }
        ],
    }
    input_path = tmp_path / "image-host-root-input.json"
    bundle_path = tmp_path / "image-host-root-bundle.json"
    descriptor = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(
            descriptor,
            json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode(),
        )
    finally:
        os.close(descriptor)
    assert provision_credential_bundle(input_path, bundle_path) == 1
    return bundle_path


def _image_single_configuration(tmp_path: Path):
    bundle_path = _provision_image_single_bundle(tmp_path)
    environment = _environment(tmp_path, storage=True)
    environment.update(
        {
            "IMAGE_SINGLE_ENABLED": "true",
            "IMAGE_SINGLE_CREDENTIALS_FILE": str(bundle_path),
        }
    )
    return load_server_configuration(environment)


class _FakePublicResponse:
    def __init__(self, library: _ControlledPublicHttpLibrary) -> None:
        self.status = 200
        self.headers = {
            "content-type": library.content_type,
            "content-length": str(len(library.content)),
        }
        self._library = library
        self.close_calls = 0

    def iter_chunks(self, chunk_size: int):
        assert chunk_size > 0

        async def chunks():
            yield self._library.content

        return chunks()

    async def close(self) -> None:
        self.close_calls += 1


class _FakePublicConnector:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _FakePublicSession:
    def __init__(
        self,
        library: _ControlledPublicHttpLibrary,
        connector: _FakePublicConnector,
    ) -> None:
        self._library = library
        self.connector = connector
        self.close_calls = 0

    async def get(
        self,
        target: Any,
        *,
        headers: Any,
        timeout_seconds: float,
        allow_redirects: bool,
    ) -> _FakePublicResponse:
        assert headers == {"Host": urlsplit(target.raw_url).netloc}
        assert allow_redirects is False
        self._library.begin_get(target.raw_url, timeout_seconds)
        try:
            while self._library.blocked and not self._library.release.wait(0):
                await asyncio.sleep(0.005)
            response = _FakePublicResponse(self._library)
            self._library.responses.append(response)
            return response
        finally:
            self._library.end_get()

    async def close(self) -> None:
        self.close_calls += 1


class _ControlledPublicHttpLibrary:
    def __init__(self, content: bytes, *, content_type: str = "image/png") -> None:
        from haoai_backend.media_storage.pinned_http import EncodedRequestTarget

        self._target_type = EncodedRequestTarget
        self.content = content
        self.content_type = content_type
        self.blocked = True
        self.release = threading.Event()
        self.first_started = threading.Event()
        self.two_started = threading.Event()
        self._lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0
        self.get_calls: list[tuple[str, float, int]] = []
        self.connectors: list[_FakePublicConnector] = []
        self.sessions: list[_FakePublicSession] = []
        self.responses: list[_FakePublicResponse] = []

    def encoded_url(self, raw_url: str, *, preserve_encoded: bool):
        assert preserve_encoded is True
        parsed = urlsplit(raw_url)
        return self._target_type(
            raw_url=raw_url,
            authority=parsed.netloc,
            tls_hostname=parsed.hostname or "",
            encoded=True,
        )

    def connector(
        self,
        resolver: Any,
        *,
        tls_hostname: str,
        verify_tls: bool,
        use_dns_cache: bool,
    ) -> _FakePublicConnector:
        assert tls_hostname
        assert verify_tls is True
        assert use_dns_cache is False
        connector = _FakePublicConnector()
        self.connectors.append(connector)
        return connector

    def session(
        self,
        connector: _FakePublicConnector,
        *,
        trust_env: bool,
        cookies_enabled: bool,
        auto_decompress: bool,
        connector_owned_by_session: bool,
    ) -> _FakePublicSession:
        assert trust_env is False
        assert cookies_enabled is False
        assert auto_decompress is True
        assert connector_owned_by_session is False
        session = _FakePublicSession(self, connector)
        self.sessions.append(session)
        return session

    def begin_get(self, url: str, timeout_seconds: float) -> None:
        with self._lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.get_calls.append((url, timeout_seconds, threading.get_ident()))
            self.first_started.set()
            if self.active >= 2:
                self.two_started.set()

    def end_get(self) -> None:
        with self._lock:
            self.active -= 1


def _apply_generation_schema(engine: Engine) -> None:
    from haoai_backend.generation_core.base_schema import FOUNDATION_TABLES
    from haoai_backend.generation_core.schema import FoundationBase

    migration_directory = Path(__file__).resolve().parents[1] / "migrations"
    runner_path = migration_directory / "run_generation_migrations.py"
    spec = importlib.util.spec_from_file_location(
        "_server_host_generation_migration_runner", runner_path
    )
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)

    FoundationBase.metadata.create_all(engine, tables=list(FOUNDATION_TABLES))
    runner.apply_generation_migrations(engine, migration_directory)


def _request_upload(app: Any, token: str) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://server-host.test",
        ) as client:
            return await client.post(
                "/api/upload/image",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": ("scene.png", _PNG, "image/png")},
            )

    return asyncio.run(send())


@pytest.mark.parametrize(
    ("extension", "response_type", "stored_type"),
    [
        (".png", "application/octet-stream", "image/png"),
        (".mp3", "audio/mpeg", "audio/mpeg"),
        (".mp4", "application/octet-stream", "video/mp4"),
    ],
)
def test_retry_oss_upload_host_pins_external_fetch_and_short_circuits_managed_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
    response_type: str,
    stored_type: str,
) -> None:
    public_content = b"synthetic retry media bytes"
    library = _ControlledPublicHttpLibrary(
        public_content,
        content_type=response_type,
    )
    library.blocked = False
    dns_calls: list[tuple[str, int, int]] = []

    async def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        family: int,
        type: int,
    ):
        assert family == socket.AF_UNSPEC
        assert type == socket.SOCK_STREAM
        dns_calls.append((host, port, threading.get_ident()))
        return (
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            ),
        )

    monkeypatch.setattr(
        server_application,
        "_build_retry_public_media_http_library",
        lambda: library,
        raising=False,
    )
    monkeypatch.setattr(
        server_application,
        "_resolve_public_media_address",
        fake_getaddrinfo,
        raising=False,
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: pytest.fail(
            "retry must use the injected DNS resolver"
        ),
    )

    configuration = load_server_configuration(_environment(tmp_path, storage=True))
    sdk_session, sdk_config, sdk_state = _sdk_factories()
    host = create_server_host(
        configuration,
        sdk_factories=(sdk_session, sdk_config),
    )
    token = _seed_authenticated_user(host.app.state.server_engine)
    external_url = f"https://media.example.test/output{extension}"

    async def exercise_route() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=host.app, raise_app_exceptions=False),
            base_url="http://server-host.test",
        ) as client:
            headers = {"Authorization": f"Bearer {token}"}
            external = await client.post(
                "/api/upload/retry-oss",
                headers=headers,
                json={"url": external_url},
            )
            assert external.status_code == 200
            managed_url = external.json()["url"]
            managed_replay = await client.post(
                "/api/upload/retry-oss",
                headers=headers,
                json={"url": managed_url},
            )
            unsafe = await client.post(
                "/api/upload/retry-oss",
                headers=headers,
                json={"url": "http://127.0.0.1/private.mp4"},
            )
            return external, managed_replay, unsafe

    try:
        external, managed_replay, unsafe = asyncio.run(exercise_route())

        assert external.status_code == 200
        managed_url = external.json()["url"]
        assert managed_url.startswith("https://cdn.example.test/media/")
        assert managed_replay.status_code == 200
        assert managed_replay.json() == {"url": managed_url}
        assert unsafe.status_code == 400
        assert unsafe.json() == {"detail": "公网媒体地址未通过安全检查"}

        assert [(host, port) for host, port, _thread_id in dns_calls] == [
            ("media.example.test", 443)
        ]
        assert [url for url, _timeout, _thread_id in library.get_calls] == [
            external_url
        ]
        assert [timeout for _url, timeout, _thread_id in library.get_calls] == [600]
        assert library.responses[0].close_calls == 1
        assert library.sessions[0].close_calls == 1
        assert library.connectors[0].close_calls == 1
        assert len(sdk_state["clients"]) == 1
        assert len(sdk_state["clients"][0].calls) == 1
        stored_call = sdk_state["clients"][0].calls[0]
        assert stored_call["Key"].endswith(extension)
        assert stored_call["ContentType"] == stored_type
        assert stored_call["body_bytes"] == public_content
    finally:
        host.close()

    assert sdk_state["clients"][0].close_calls == 1


def test_host_wires_authenticated_upload_and_business_routes_to_one_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haoai_backend.app as app_module
    import backend.server.application as host_module

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: pytest.fail(
            "host configuration and assembly must not perform DNS"
        ),
    )

    arguments: dict[str, Any] = {}
    original_create_app = app_module.create_app

    def observe_create_app(**kwargs: Any) -> Any:
        arguments.update(kwargs)
        return original_create_app(**kwargs)

    monkeypatch.setattr(app_module, "create_app", observe_create_app)
    engine_connections: list[str] = []
    original_create_engine = host_module._create_engine

    def observe_engine(database_url: str) -> Engine:
        engine = original_create_engine(database_url)
        event.listen(
            engine,
            "connect",
            lambda *_args: engine_connections.append("connected"),
        )
        return engine

    monkeypatch.setattr(host_module, "_create_engine", observe_engine)
    configuration = load_server_configuration(_environment(tmp_path, storage=True))
    sdk_session, sdk_config, sdk_state = _sdk_factories()
    host = create_server_host(configuration, sdk_factories=(sdk_session, sdk_config))
    engine = host.app.state.server_engine
    original_pool = engine.pool

    assert engine_connections == []
    assert sdk_state["credentials"] == []
    assert sdk_state["clients"] == []
    assert arguments["session_factory"] is host.session_factory
    assert arguments["authentication_runtime"].session_factory is host.session_factory
    assert _SECRET not in repr(arguments["authentication_runtime"])

    async def check_readiness() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=host.app),
            base_url="http://server-host.test",
        ) as client:
            return await client.get("/ready")

    readiness_response = asyncio.run(check_readiness())
    assert readiness_response.status_code == 503
    assert readiness_response.json() == {"status": "not_ready"}
    assert sdk_state["clients"] == []
    assert engine_connections == ["connected"]

    token = _seed_authenticated_user(engine)
    response = _request_upload(host.app, token)

    assert response.status_code == 200
    payload = response.json()
    assert payload["url"].startswith("https://cdn.example.test/media/")
    assert len(sdk_state["clients"]) == 1
    assert len(sdk_state["clients"][0].calls) == 1
    call = sdk_state["clients"][0].calls[0]
    assert call["Bucket"] == "haoai-media"
    assert call["Key"].endswith(".png")
    assert call["ContentType"] == "image/png"
    assert call["CacheControl"] == "public, max-age=31536000, immutable"
    assert call["body_bytes"] == _PNG
    assert sdk_state["credentials"] == [
        {
            "aws_access_key_id": "synthetic-access-key",
            "aws_secret_access_key": "synthetic-r2-secret",
            "region_name": "auto",
        }
    ]
    assert sdk_state["client_options"][0]["endpoint_url"] == (
        "https://acct.r2.cloudflarestorage.com"
    )

    async def read_public_routes() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=host.app),
            base_url="http://server-host.test",
        ) as client:
            config_response = await client.get("/api/site-config")
            retry_response = await client.post(
                "/api/upload/retry-oss",
                headers={"Authorization": f"Bearer {token}"},
                json={"url": payload["url"]},
            )
            return config_response, retry_response

    config_response, retry_response = asyncio.run(read_public_routes())
    assert config_response.status_code == 200
    assert config_response.json()["site_title"] == "Synthetic site"
    assert retry_response.status_code == 200
    assert retry_response.json() == {"url": payload["url"]}
    assert len(sdk_state["clients"][0].calls) == 1

    async def shutdown() -> None:
        async with host.app.router.lifespan_context(host.app):
            pass

    asyncio.run(shutdown())
    host.close()
    assert engine.pool is not original_pool
    assert sdk_state["clients"][0].close_calls == 1


def test_image_single_media_store_uses_owned_runtime_and_generation_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = _image_single_configuration(tmp_path)
    sdk_session, sdk_config, sdk_state = _sdk_factories()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: pytest.fail(
            "image.single host media writes must not use real DNS"
        ),
    )
    host = create_server_host(
        configuration,
        sdk_factories=(sdk_session, sdk_config),
    )
    image_host = host.app.state.server_image_generation_configuration
    media_runtime = image_host.media_runtime
    worker_runtime = image_host.generation_worker_configuration.runtime
    prepared_png = _prepared_png()
    from haoai_backend.generation_contracts.types import ReferenceInput, StoredMedia

    assert media_runtime is not None
    assert media_runtime.state == "new"
    assert sdk_state["clients"] == []

    try:
        store = worker_runtime.media_store_factory()
        stored = store.store_bytes_or_provider_content(
            ReferenceInput(
                kind="bytes",
                content=prepared_png,
                media_type="image/png",
            ),
            {"max_bytes": len(prepared_png)},
        )
        assert isinstance(stored, StoredMedia)
        assert stored.content_digest == hashlib.sha256(prepared_png).hexdigest()
        assert stored.media_type == "image/png"
        assert stored.size_bytes == len(prepared_png)
        assert stored.locator.startswith("https://cdn.example.test/media/")
        assert media_runtime.state == "running"
        assert len(sdk_state["clients"]) == 1
        assert len(sdk_state["clients"][0].calls) == 1
        call = sdk_state["clients"][0].calls[0]
        assert call["Bucket"] == "haoai-media"
        assert call["ContentType"] == "image/png"
        assert call["CacheControl"] == "public, max-age=31536000, immutable"
        assert call["body_bytes"] == prepared_png
        key = call["Key"]
        assert key is not None
        year, month, day, business_type, filename = key.split("/")
        assert year.isdecimal() and len(year) == 4
        assert month.isdecimal() and len(month) == 2
        assert day.isdecimal() and len(day) == 2
        assert business_type == "generation"
        assert filename.endswith(".png")
        assert str(uuid.UUID(filename[:-4])) == filename[:-4]
        assert stored.locator == f"https://cdn.example.test/media/{key}"
    finally:
        host.close()

    assert media_runtime.state == "closed"
    owned_thread = media_runtime._thread
    owned_executor = media_runtime._executor
    assert owned_thread is not None and not owned_thread.is_alive()
    assert owned_executor is not None and owned_executor._shutdown is True
    assert all(not thread.is_alive() for thread in owned_executor._threads)
    assert sdk_state["clients"][0].close_calls == 1


def test_image_single_public_fetch_uses_owned_loop_shared_slots_and_safe_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haoai_backend.generation_contracts.types import (
        PreparedReference,
        ReferenceInput,
        TransferFailure,
    )
    from haoai_backend.media_storage.transfer_types import (
        MAX_TRANSFER_BYTES,
        MAX_TRANSFER_SECONDS,
    )
    from haoai_backend.media_storage.owned_media_runtime import OwnedMediaRuntime
    import backend.server.image_generation as image_generation_module

    prepared_png = _prepared_png()
    library = _ControlledPublicHttpLibrary(prepared_png)
    dns_observations: list[tuple[str, int, int]] = []
    third_request_waiting_for_slot = threading.Event()
    semaphore_capacities: list[int] = []
    observed_semaphores: set[int] = set()
    original_get_services = OwnedMediaRuntime._get_services

    def observe_owned_services(runtime: OwnedMediaRuntime):
        services = original_get_services(runtime)
        semaphore = services.shared_slots
        if id(semaphore) not in observed_semaphores:
            observed_semaphores.add(id(semaphore))
            semaphore_capacities.append(semaphore._value)
            original_acquire = semaphore.acquire

            async def observe_acquire() -> bool:
                if semaphore._value == 0:
                    third_request_waiting_for_slot.set()
                return await original_acquire()

            semaphore.acquire = observe_acquire  # type: ignore[method-assign]
        return services

    monkeypatch.setattr(
        OwnedMediaRuntime,
        "_get_services",
        observe_owned_services,
    )

    async def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        family: int,
        type: int,
    ):
        assert family == socket.AF_UNSPEC
        assert type == socket.SOCK_STREAM
        dns_observations.append(
            (host, threading.get_ident(), id(asyncio.get_running_loop()))
        )
        return (
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            ),
        )

    monkeypatch.setattr(image_generation_module, "_build_http_library", lambda: library)
    monkeypatch.setattr(image_generation_module, "_system_getaddrinfo", fake_getaddrinfo)
    configuration = _image_single_configuration(tmp_path)
    sdk_session, sdk_config, _sdk_state = _sdk_factories()
    host = create_server_host(
        configuration,
        sdk_factories=(sdk_session, sdk_config),
    )
    image_host = host.app.state.server_image_generation_configuration
    media_runtime = image_host.media_runtime
    fetcher = (
        image_host.generation_worker_configuration.runtime.public_media_fetcher_factory()
    )
    caller_thread = threading.get_ident()

    assert media_runtime is not None
    assert media_runtime.state == "new"

    async def fetch_three() -> tuple[object, ...]:
        for invalid_max_bytes, invalid_timeout in (
            (MAX_TRANSFER_BYTES + 1, 2),
            (1024, MAX_TRANSFER_SECONDS + 1),
        ):
            invalid_limits = await fetcher.fetch_public(
                ReferenceInput(
                    kind="public_http",
                    locator="https://image.example.test/large.png",
                ),
                max_bytes=invalid_max_bytes,
                timeout_seconds=invalid_timeout,
            )
            assert isinstance(invalid_limits, TransferFailure)
            assert invalid_limits.code == "invalid_limits"
        assert media_runtime.state == "new"

        tasks = [
            asyncio.create_task(
                fetcher.fetch_public(
                    ReferenceInput(
                        kind="public_http",
                        locator=f"https://image-{index}.example.test/output.png",
                    ),
                    max_bytes=1024,
                    timeout_seconds=5,
                )
            )
            for index in range(3)
        ]
        assert await asyncio.to_thread(library.two_started.wait, 2)
        assert await asyncio.to_thread(third_request_waiting_for_slot.wait, 2)
        assert semaphore_capacities == [2]
        assert library.active == 2
        assert len(library.get_calls) == 2
        assert len(dns_observations) == 2
        library.release.set()
        results = await asyncio.gather(*tasks)
        assert all(isinstance(result, PreparedReference) for result in results)
        assert all(result.content == prepared_png for result in results)
        assert library.maximum_active == 2
        assert library.active == 0
        assert len(library.get_calls) == 3

        runtime_thread_id = media_runtime.owner_thread_id
        runtime_loop_id = id(media_runtime._loop)
        assert runtime_thread_id is not None
        assert {thread_id for _url, _timeout, thread_id in library.get_calls} == {
            runtime_thread_id
        }
        assert runtime_thread_id != caller_thread
        assert {thread_id for _host, thread_id, _loop_id in dns_observations} == {
            runtime_thread_id
        }
        assert {loop_id for _host, _thread_id, loop_id in dns_observations} == {
            runtime_loop_id
        }
        assert {timeout for _url, timeout, _thread_id in library.get_calls} == {5}

        get_count_before_private = len(library.get_calls)
        dns_count_before_private = len(dns_observations)
        private_result = await fetcher.fetch_public(
            ReferenceInput(kind="public_http", locator="http://127.0.0.1/private.png"),
            max_bytes=1024,
            timeout_seconds=1,
        )
        assert isinstance(private_result, TransferFailure)
        assert private_result.code == "unsafe_url"
        assert len(library.get_calls) == get_count_before_private
        assert len(dns_observations) == dns_count_before_private

        limited_result = await fetcher.fetch_public(
            ReferenceInput(
                kind="public_http",
                locator="https://limited.example.test/image.png",
            ),
            max_bytes=32,
            timeout_seconds=1,
        )
        assert isinstance(limited_result, TransferFailure)
        assert limited_result.code == "reference_too_large"

        library.release.clear()
        library.first_started.clear()
        timeout_task = asyncio.create_task(
            fetcher.fetch_public(
                ReferenceInput(
                    kind="public_http",
                    locator="https://timeout.example.test/slow.png",
                ),
                max_bytes=1024,
                timeout_seconds=0.25,
            )
        )
        assert await asyncio.to_thread(library.first_started.wait, 2)
        timeout_result = await timeout_task
        assert isinstance(timeout_result, TransferFailure)
        assert timeout_result.code == "timeout"
        assert library.active == 0
        assert library.get_calls[-1][1] == 0.25
        assert library.sessions[-1].close_calls == 1
        assert library.connectors[-1].close_calls == 1
        return tuple(results) + (private_result, limited_result, timeout_result)

    try:
        asyncio.run(fetch_three())
    finally:
        library.release.set()
        host.close()

    assert media_runtime.state == "closed"
    assert all(session.close_calls == 1 for session in library.sessions)
    assert all(connector.close_calls == 1 for connector in library.connectors)
    assert media_runtime._thread is not None and not media_runtime._thread.is_alive()
    assert media_runtime._executor is not None
    assert media_runtime._executor._shutdown is True


def test_image_single_public_fetch_cancellation_waits_for_owned_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haoai_backend.generation_contracts.types import ReferenceInput
    import backend.server.image_generation as image_generation_module

    library = _ControlledPublicHttpLibrary(_prepared_png())

    async def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        family: int,
        type: int,
    ):
        assert family == socket.AF_UNSPEC
        assert type == socket.SOCK_STREAM
        return (
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            ),
        )

    monkeypatch.setattr(image_generation_module, "_build_http_library", lambda: library)
    monkeypatch.setattr(image_generation_module, "_system_getaddrinfo", fake_getaddrinfo)
    configuration = _image_single_configuration(tmp_path)
    sdk_session, sdk_config, _sdk_state = _sdk_factories()
    host = create_server_host(
        configuration,
        sdk_factories=(sdk_session, sdk_config),
    )
    image_host = host.app.state.server_image_generation_configuration
    media_runtime = image_host.media_runtime
    fetcher = (
        image_host.generation_worker_configuration.runtime.public_media_fetcher_factory()
    )

    async def cancel_and_wait() -> bool:
        fetch = asyncio.create_task(
            fetcher.fetch_public(
                ReferenceInput(
                    kind="public_http",
                    locator="https://cancel.example.test/image.png",
                ),
                max_bytes=1024,
                timeout_seconds=5,
            )
        )
        assert await asyncio.to_thread(library.first_started.wait, 2)
        fetch.cancel()
        await asyncio.sleep(0.05)
        remained_pending_for_cleanup = not fetch.done()
        library.release.set()
        with pytest.raises(asyncio.CancelledError):
            await fetch
        return remained_pending_for_cleanup

    try:
        assert media_runtime is not None
        assert asyncio.run(cancel_and_wait()) is True
        assert library.active == 0
        assert library.sessions[-1].close_calls == 1
        assert library.connectors[-1].close_calls == 1
    finally:
        library.release.set()
        host.close()

    assert media_runtime.state == "closed"
    assert media_runtime._thread is not None and not media_runtime._thread.is_alive()
    assert media_runtime._executor is not None
    assert media_runtime._executor._shutdown is True


def test_unconfigured_storage_returns_existing_upload_failure_without_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = load_server_configuration(_environment(tmp_path, storage=False))
    monkeypatch.setattr(
        "backend.server.application._load_sdk_factories",
        lambda: pytest.fail("SDK must not load when storage is absent"),
    )
    host = create_server_host(configuration)
    token = _seed_authenticated_user(host.app.state.server_engine)

    response = _request_upload(host.app, token)

    assert response.status_code == 502
    assert response.json()["detail"] == "云端上传失败"
    host.close()


def test_configured_storage_without_sdk_fails_before_app_startup_and_disposes_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.server.application as host_module

    configuration = load_server_configuration(_environment(tmp_path, storage=True))
    captured: list[tuple[Engine, Any]] = []
    original_create_engine = host_module._create_engine

    def observe_engine(database_url: str) -> Engine:
        engine = original_create_engine(database_url)
        captured.append((engine, engine.pool))
        return engine

    def missing_sdk() -> Any:
        raise ServerHostAssemblyError("R2 is configured but its SDK is unavailable")

    monkeypatch.setattr(host_module, "_create_engine", observe_engine)
    monkeypatch.setattr(host_module, "_load_sdk_factories", missing_sdk)

    with pytest.raises(ServerHostAssemblyError, match="SDK is unavailable"):
        create_server_host(configuration)

    assert len(captured) == 1
    assert captured[0][0].pool is not captured[0][1]


def test_shutdown_retries_only_pending_close_and_never_repeats_put(
    tmp_path: Path,
) -> None:
    configuration = load_server_configuration(_environment(tmp_path, storage=True))
    sdk_session, sdk_config, sdk_state = _sdk_factories(close_failures=1)
    host = create_server_host(configuration, sdk_factories=(sdk_session, sdk_config))
    engine = host.app.state.server_engine
    original_pool = engine.pool
    token = _seed_authenticated_user(engine)

    response = _request_upload(host.app, token)

    assert response.status_code == 502
    assert response.json()["detail"] == "云端上传失败"
    assert len(sdk_state["clients"][0].calls) == 1
    assert sdk_state["clients"][0].close_calls == 1

    async def shutdown() -> None:
        async with host.app.router.lifespan_context(host.app):
            pass

    asyncio.run(shutdown())
    host.close()

    assert sdk_state["clients"][0].close_calls == 2
    assert len(sdk_state["clients"][0].calls) == 1
    assert engine.pool is not original_pool


def test_cli_passes_the_serving_app_and_closes_after_runner_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    original_create_host = server_cli.create_server_host

    def observe_create_host(configuration: Any, **kwargs: Any) -> Any:
        host = original_create_host(configuration, **kwargs)
        created.append(host)
        return host

    monkeypatch.setattr(server_cli, "create_server_host", observe_create_host)
    seen: dict[str, Any] = {}

    def runner(app: Any, **kwargs: Any) -> None:
        seen["app"] = app
        seen["kwargs"] = kwargs
        seen["pool"] = created[0].app.state.server_engine.pool
        raise RuntimeError("synthetic runner failure")

    with pytest.raises(RuntimeError, match="synthetic runner failure"):
        server_cli.main(_environment(tmp_path, storage=False), runner=runner)

    assert len(created) == 1
    assert seen["app"] is created[0].asgi_app
    assert seen["kwargs"] == {
        "host": "127.0.0.1",
        "port": 8000,
        "workers": 1,
        "reload": False,
        "lifespan": "on",
    }
    assert created[0].app.state.server_engine.pool is not seen["pool"]


def test_cli_closes_host_if_uvicorn_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    original_pools: list[Any] = []
    original_create_host = server_cli.create_server_host

    def observe_create_host(configuration: Any, **kwargs: Any) -> Any:
        host = original_create_host(configuration, **kwargs)
        created.append(host)
        original_pools.append(host.app.state.server_engine.pool)
        return host

    def missing_runner() -> Any:
        raise RuntimeError("uvicorn is unavailable")

    monkeypatch.setattr(server_cli, "create_server_host", observe_create_host)
    monkeypatch.setattr(server_cli, "_load_uvicorn_runner", missing_runner)

    with pytest.raises(RuntimeError, match="uvicorn is unavailable"):
        server_cli.main(_environment(tmp_path, storage=False))

    assert len(created) == 1
    assert created[0].app.state.server_engine.pool is not original_pools[0]


def test_lifespan_startup_failure_still_disposes_the_host_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haoai_backend.app as app_module

    @asynccontextmanager
    async def failed_startup(_app: Any):
        raise RuntimeError("synthetic application startup failure")
        yield

    app = FastAPI()
    app.router.lifespan_context = failed_startup
    monkeypatch.setattr(app_module, "create_app", lambda **_kwargs: app)

    host = create_server_host(
        load_server_configuration(_environment(tmp_path, storage=False))
    )
    engine = host.app.state.server_engine
    original_pool = engine.pool

    async def start() -> None:
        async with host.app.router.lifespan_context(host.app):
            pass

    with pytest.raises(RuntimeError, match="synthetic application startup failure"):
        asyncio.run(start())

    assert engine.pool is not original_pool
    host.close()


def test_lifespan_shutdown_and_resource_cleanup_failures_are_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haoai_backend.app as app_module

    original_create_app = app_module.create_app

    @asynccontextmanager
    async def failed_shutdown(app: Any):
        yield app
        raise RuntimeError("synthetic application shutdown failure")

    def create_app_with_failed_shutdown(**kwargs: Any) -> Any:
        app = original_create_app(**kwargs)
        app.router.lifespan_context = failed_shutdown
        return app

    monkeypatch.setattr(app_module, "create_app", create_app_with_failed_shutdown)
    configuration = load_server_configuration(_environment(tmp_path, storage=True))
    sdk_session, sdk_config, sdk_state = _sdk_factories(close_failures=3)
    host = create_server_host(configuration, sdk_factories=(sdk_session, sdk_config))
    engine = host.app.state.server_engine
    original_pool = engine.pool
    token = _seed_authenticated_user(engine)

    response = _request_upload(host.app, token)

    assert response.status_code == 502
    client = sdk_state["clients"][0]
    assert len(client.calls) == 1
    dispose_calls = 0
    original_dispose = engine.dispose

    def fail_first_dispose() -> None:
        nonlocal dispose_calls
        dispose_calls += 1
        if dispose_calls == 1:
            raise OSError("synthetic engine dispose failure")
        original_dispose()

    monkeypatch.setattr(engine, "dispose", fail_first_dispose)

    async def shutdown() -> None:
        async with host.app.router.lifespan_context(host.app):
            pass

    with pytest.raises(BaseExceptionGroup) as raised:
        asyncio.run(shutdown())

    assert [str(error) for error in raised.value.exceptions] == [
        "synthetic application shutdown failure",
        "server resource cleanup did not complete",
    ]
    assert client.close_calls == 2
    assert dispose_calls == 1
    assert engine.pool is original_pool
    assert len(client.calls) == 1

    with pytest.raises(RuntimeError, match="server resource cleanup did not complete"):
        host.close()

    assert client.close_calls == 3
    assert dispose_calls == 2
    assert engine.pool is not original_pool
    assert len(client.calls) == 1

    client.close_failures = client.close_calls
    host.close()
    host.close()

    assert client.close_calls == 4
    assert dispose_calls == 2
    assert len(client.calls) == 1


def test_concurrent_close_waits_for_cleanup_and_does_not_report_false_success() -> None:
    first_cleanup_started = threading.Event()
    finish_first_cleanup = threading.Event()
    second_call_started = threading.Event()
    second_call_returned = threading.Event()
    cleanup_calls = 0
    dispose_calls = 0
    results: dict[str, BaseException | None] = {}

    class _Storage:
        def retry_pending_cleanup(self) -> tuple[bool, ...]:
            nonlocal cleanup_calls
            cleanup_calls += 1
            if cleanup_calls == 1:
                first_cleanup_started.set()
                if not finish_first_cleanup.wait(timeout=2):
                    raise TimeoutError("test cleanup release was not signaled")
                return (False,)
            return (True,)

    class _Engine:
        def dispose(self) -> None:
            nonlocal dispose_calls
            dispose_calls += 1

    resources = _OwnedResources(_Engine(), _Storage())  # type: ignore[arg-type]

    def close_as(name: str, started: threading.Event | None = None) -> None:
        if started is not None:
            started.set()
        try:
            resources.close()
        except BaseException as error:
            results[name] = error
        else:
            results[name] = None
            if name == "second":
                second_call_returned.set()

    first = threading.Thread(target=close_as, args=("first",))
    second = threading.Thread(
        target=close_as,
        args=("second", second_call_started),
    )
    first.start()
    try:
        assert first_cleanup_started.wait(timeout=2)
        second.start()
        assert second_call_started.wait(timeout=2)
        assert not second_call_returned.wait(timeout=0.1)
    finally:
        finish_first_cleanup.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert isinstance(results["first"], RuntimeError)
    assert results["second"] is None
    assert cleanup_calls == 2
    assert dispose_calls == 1


def test_owned_resources_close_media_runtime_then_storage_then_engine_once() -> None:
    events: list[str] = []

    class _MediaRuntime:
        def close(self) -> None:
            events.append("media")

    class _Storage:
        def retry_pending_cleanup(self) -> tuple[bool, ...]:
            events.append("storage")
            return (True,)

    class _Engine:
        def dispose(self) -> None:
            events.append("engine")

    resources = _OwnedResources(  # type: ignore[arg-type]
        _Engine(),
        _Storage(),
        _MediaRuntime(),  # type: ignore[arg-type]
    )

    resources.close()
    resources.close()

    assert events == ["media", "storage", "engine"]


def test_owned_resources_reports_media_cleanup_failure_after_other_cleanup() -> None:
    events: list[str] = []

    class _MediaRuntime:
        attempts = 0

        def close(self) -> None:
            self.attempts += 1
            events.append("media")
            if self.attempts == 1:
                raise RuntimeError("synthetic cleanup failure")

    class _Storage:
        def retry_pending_cleanup(self) -> tuple[bool, ...]:
            events.append("storage")
            return (True,)

    class _Engine:
        def dispose(self) -> None:
            events.append("engine")

    resources = _OwnedResources(  # type: ignore[arg-type]
        _Engine(),
        _Storage(),
        _MediaRuntime(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="cleanup did not complete"):
        resources.close()
    resources.close()

    assert events == [
        "media",
        "storage",
        "engine",
        "media",
    ]


def test_cli_surfaces_lifespan_cleanup_failure_swallowed_by_uvicorn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    sdk_session, sdk_config, sdk_state = _sdk_factories(close_failures=50)
    original_create_host = server_cli.create_server_host

    def create_host_with_pending_cleanup(configuration: Any, **kwargs: Any) -> Any:
        host = original_create_host(
            configuration,
            sdk_factories=(sdk_session, sdk_config),
            **kwargs,
        )
        token = _seed_authenticated_user(host.app.state.server_engine)
        response = _request_upload(host.app, token)
        assert response.status_code == 502
        created.append(host)
        return host

    monkeypatch.setattr(
        server_cli,
        "create_server_host",
        create_host_with_pending_cleanup,
    )
    runner_state: dict[str, BaseException | None] = {}

    def uvicorn_like_runner(app: Any, **_kwargs: Any) -> None:
        async def serve_lifespan() -> None:
            incoming: asyncio.Queue[dict[str, str]] = asyncio.Queue()
            outgoing: asyncio.Queue[dict[str, str]] = asyncio.Queue()

            async def receive() -> dict[str, str]:
                return await incoming.get()

            async def send(message: dict[str, str]) -> None:
                await outgoing.put(message)

            task = asyncio.create_task(
                app(
                    {"type": "lifespan", "asgi": {"version": "3.0"}},
                    receive,
                    send,
                )
            )
            await incoming.put({"type": "lifespan.startup"})
            startup = await asyncio.wait_for(outgoing.get(), timeout=2)
            assert startup["type"] == "lifespan.startup.complete"
            await incoming.put({"type": "lifespan.shutdown"})
            shutdown = await asyncio.wait_for(outgoing.get(), timeout=2)
            if shutdown["type"] == "lifespan.shutdown.failed":
                runner_state["swallowed"] = RuntimeError(
                    shutdown.get("message", "server resource cleanup failed")
                )
            try:
                await asyncio.wait_for(task, timeout=2)
            except BaseException as error:
                # Uvicorn 0.24 logs an ASGI lifespan shutdown failure and returns.
                runner_state["swallowed"] = error

        asyncio.run(serve_lifespan())

    try:
        with pytest.raises(
            RuntimeError,
            match="server resource cleanup did not complete",
        ):
            server_cli.main(
                _environment(tmp_path, storage=True),
                runner=uvicorn_like_runner,
            )
    finally:
        if created:
            client = sdk_state["clients"][0]
            client.close_failures = client.close_calls
            created[0].close()

    assert len(created) == 1
    assert isinstance(runner_state["swallowed"], RuntimeError)
    assert len(sdk_state["clients"][0].calls) == 1
    assert sdk_state["clients"][0].close_calls == 4


def test_readiness_runs_the_existing_database_and_head_bucket_probes(
    tmp_path: Path,
) -> None:
    configuration = load_server_configuration(_environment(tmp_path, storage=True))
    sdk_session, sdk_config, sdk_state = _sdk_factories()
    host = create_server_host(configuration, sdk_factories=(sdk_session, sdk_config))
    engine = host.app.state.server_engine
    _apply_generation_schema(engine)

    async def check_readiness() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=host.app),
            base_url="http://server-host.test",
        ) as client:
            return await client.get("/ready")

    try:
        response = asyncio.run(check_readiness())
    finally:
        host.close()

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert len(sdk_state["clients"]) == 1
    assert sdk_state["clients"][0].head_bucket_calls == ["haoai-media"]
    assert sdk_state["clients"][0].calls == []
    assert sdk_state["clients"][0].close_calls == 1


def test_importing_server_modules_does_not_open_runtime_resources() -> None:
    script = r"""
import importlib.abc
import socket
import sqlite3
import sys

import sqlalchemy
from sqlalchemy.engine import Engine

def fail(*_args, **_kwargs):
    raise AssertionError("server module import attempted external I/O")

class BlockRuntimeSDKs(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "boto3" or fullname.startswith("boto3."):
            raise AssertionError("server module import loaded boto3")
        if fullname == "botocore" or fullname.startswith("botocore."):
            raise AssertionError("server module import loaded botocore")
        return None

sys.meta_path.insert(0, BlockRuntimeSDKs())
socket.getaddrinfo = fail
sqlite3.connect = fail
sqlalchemy.create_engine = fail
Engine.connect = fail

import backend.server.__main__
import backend.server.application
import backend.server.configuration

assert "boto3" not in sys.modules
assert "botocore" not in sys.modules
print("clean")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-s", "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean"
