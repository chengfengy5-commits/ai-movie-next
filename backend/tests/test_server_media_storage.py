"""Host composition and upload-route checks using local SQLite and a fake SDK."""

from __future__ import annotations

import asyncio
import importlib.util
import socket
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, insert
from sqlalchemy.engine import Engine

from backend.server import __main__ as server_cli
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
    assert retry_response.status_code == 502
    assert retry_response.json()["detail"] == "下载失败"
    assert len(sdk_state["clients"][0].calls) == 1

    async def shutdown() -> None:
        async with host.app.router.lifespan_context(host.app):
            pass

    asyncio.run(shutdown())
    host.close()
    assert engine.pool is not original_pool
    assert sdk_state["clients"][0].close_calls == 1


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


def test_cli_passes_the_composed_app_and_closes_after_runner_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    original_create_host = server_cli.create_server_host

    def observe_create_host(configuration: Any) -> Any:
        host = original_create_host(configuration)
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
    assert seen["app"] is created[0].app
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

    def observe_create_host(configuration: Any) -> Any:
        host = original_create_host(configuration)
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


def test_cli_surfaces_lifespan_cleanup_failure_swallowed_by_uvicorn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    sdk_session, sdk_config, sdk_state = _sdk_factories(close_failures=50)
    original_create_host = server_cli.create_server_host

    def create_host_with_pending_cleanup(configuration: Any) -> Any:
        host = original_create_host(
            configuration,
            sdk_factories=(sdk_session, sdk_config),
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
            try:
                async with app.router.lifespan_context(app):
                    pass
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
