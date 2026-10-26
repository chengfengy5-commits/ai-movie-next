from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import Column, MetaData, Table, Text, create_engine, event, insert, update
from sqlalchemy.orm import sessionmaker

from backend.server import __main__ as server_cli
from backend.server import application as host_module
from backend.server.application import ServerHostAssemblyError, create_server_host
from backend.server.browser_cors import ServerBrowserCorsApp
from backend.server.configuration import load_server_configuration
from haoai_backend.admin_system_config.tables import system_configs


DATABASE_ORIGIN = "https://db.example.test"
FALLBACK_ORIGIN = "https://fallback.example.test"
OTHER_ORIGIN = "https://other.example.test"


def _environment(tmp_path: Path, *, allowed_origins: str = "") -> dict[str, str]:
    return {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'server.sqlite'}",
        "SECRET_KEY": "synthetic-server-cors-secret",
        "ALLOWED_ORIGINS": allowed_origins,
    }


@asynccontextmanager
async def _running_asgi_app(app):
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
    if startup["type"] != "lifespan.startup.complete":
        task.cancel()
        raise AssertionError(f"ASGI startup failed: {startup.get('message', 'unknown')}")
    try:
        yield
    finally:
        await incoming.put({"type": "lifespan.shutdown"})
        shutdown = await asyncio.wait_for(outgoing.get(), timeout=2)
        assert shutdown["type"] == "lifespan.shutdown.complete"
        await asyncio.wait_for(task, timeout=2)


def test_server_host_selects_database_cors_once_and_keeps_auth_and_preflight_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked_sessions: list[dict[str, bool]] = []
    sql_statements: list[str] = []
    original_sessionmaker = host_module.sessionmaker

    def tracked_sessionmaker(*args, **kwargs):
        factory = original_sessionmaker(*args, **kwargs)

        def create_session():
            session = factory()
            original_close = session.close
            original_execute = session.execute
            tracking = {"cors_read": False, "closed": False}
            tracked_sessions.append(tracking)

            def execute(statement, *execute_args, **execute_kwargs):
                if "system_configs.allowed_origins" in str(statement):
                    tracking["cors_read"] = True
                return original_execute(statement, *execute_args, **execute_kwargs)

            def close() -> None:
                tracking["closed"] = True
                original_close()

            session.execute = execute
            session.close = close
            return session

        return create_session

    monkeypatch.setattr(host_module, "sessionmaker", tracked_sessionmaker)
    configuration = load_server_configuration(
        _environment(tmp_path, allowed_origins=FALLBACK_ORIGIN)
    )
    host = create_server_host(configuration)
    engine = host.app.state.server_engine
    system_configs.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(system_configs).values(
                id="cors-config",
                allowed_origins=DATABASE_ORIGIN,
            )
        )

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        if statement.lstrip().lower().startswith("select"):
            sql_statements.append(" ".join(statement.lower().split()))

    event.listen(engine, "before_cursor_execute", capture_sql)

    async def scenario() -> None:
        async with _running_asgi_app(host.asgi_app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=host.asgi_app,
                    raise_app_exceptions=False,
                ),
                base_url="http://backend.test",
            ) as client:
                unauthenticated = await client.get(
                    "/api/series",
                    headers={"Origin": DATABASE_ORIGIN},
                )
                assert unauthenticated.status_code == 401
                assert unauthenticated.headers["access-control-allow-origin"] == DATABASE_ORIGIN
                assert "access-control-allow-credentials" not in unauthenticated.headers

                fallback = await client.get(
                    "/api/series",
                    headers={"Origin": FALLBACK_ORIGIN},
                )
                assert fallback.status_code == 401
                assert "access-control-allow-origin" not in fallback.headers

                preflight = await client.options(
                    "/api/series",
                    headers={
                        "Origin": DATABASE_ORIGIN,
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "authorization,content-type,idempotency-key,cache-control,pragma",
                    },
                )
                assert preflight.status_code == 200
                assert preflight.headers["access-control-allow-origin"] == DATABASE_ORIGIN
                assert "access-control-allow-credentials" not in preflight.headers

                rejected_method = await client.options(
                    "/api/series",
                    headers={
                        "Origin": DATABASE_ORIGIN,
                        "Access-Control-Request-Method": "PATCH",
                    },
                )
                assert rejected_method.status_code == 400
                assert rejected_method.headers["access-control-allow-origin"] == DATABASE_ORIGIN

                with engine.begin() as connection:
                    connection.execute(
                        update(system_configs)
                        .where(system_configs.c.id == "cors-config")
                        .values(allowed_origins=OTHER_ORIGIN)
                    )
                still_selected = await client.get(
                    "/api/series",
                    headers={"Origin": DATABASE_ORIGIN},
                )
                changed_after_startup = await client.get(
                    "/api/series",
                    headers={"Origin": OTHER_ORIGIN},
                )
                assert still_selected.headers["access-control-allow-origin"] == DATABASE_ORIGIN
                assert "access-control-allow-origin" not in changed_after_startup.headers

    try:
        asyncio.run(scenario())
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)
        host.close()

    cors_sessions = [session for session in tracked_sessions if session["cors_read"]]
    cors_queries = [statement for statement in sql_statements if "from system_configs" in statement]
    assert len(cors_sessions) == 1
    assert cors_sessions[0]["closed"] is True
    assert len(cors_queries) == 1
    assert "select system_configs.allowed_origins from system_configs limit" in cors_queries[0]


def test_server_cors_fallback_invalid_database_policy_and_late_timeout_are_fail_closed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()

    @app.get("/unauthorized")
    async def unauthorized():
        raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/explode")
    async def explode():
        raise RuntimeError("synthetic failure")

    metadata = MetaData()
    config_table = Table(
        "system_configs",
        metadata,
        Column("allowed_origins", Text),
    )

    def app_for_value(value: str | None, *, create_table: bool = True):
        engine = create_engine(f"sqlite:///{tmp_path / f'{len(list(tmp_path.iterdir()))}.sqlite'}")
        if create_table:
            metadata.create_all(engine)
            if value is not None:
                with engine.begin() as connection:
                    connection.execute(insert(config_table).values(allowed_origins=value))
        return engine, ServerBrowserCorsApp(
            app,
            session_factory=sessionmaker(engine, expire_on_commit=False),
            fallback_origins=(FALLBACK_ORIGIN,),
            read_timeout_seconds=0.05,
        )

    async def requests_for(cors_app, origin: str) -> tuple[httpx.Response, httpx.Response]:
        async with _running_asgi_app(cors_app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=cors_app, raise_app_exceptions=False),
                base_url="http://backend.test",
            ) as client:
                return (
                    await client.get("/unauthorized", headers={"Origin": origin}),
                    await client.get("/explode", headers={"Origin": origin}),
                )

    missing_engine, missing_app = app_for_value(None, create_table=False)
    try:
        missing_response, _ = asyncio.run(requests_for(missing_app, FALLBACK_ORIGIN))
        assert missing_response.status_code == 401
        assert missing_response.headers["access-control-allow-origin"] == FALLBACK_ORIGIN
    finally:
        missing_engine.dispose()

    invalid_engine, invalid_app = app_for_value(
        f"{DATABASE_ORIGIN},https://user:synthetic-secret@bad.example.test"
    )
    try:
        with caplog.at_level(logging.ERROR):
            invalid_responses = asyncio.run(requests_for(invalid_app, FALLBACK_ORIGIN))
        assert invalid_responses[0].status_code == 401
        assert "access-control-allow-origin" not in invalid_responses[0].headers
        assert "synthetic-secret" not in caplog.text
        assert "Invalid database ALLOWED_ORIGINS" in caplog.text
    finally:
        invalid_engine.dispose()

    separator_engine, separator_app = app_for_value(", ,")
    try:
        with caplog.at_level(logging.ERROR):
            separator_response, _ = asyncio.run(
                requests_for(separator_app, FALLBACK_ORIGIN)
            )
        assert separator_response.status_code == 401
        assert "access-control-allow-origin" not in separator_response.headers
        assert "Invalid database ALLOWED_ORIGINS" in caplog.text
    finally:
        separator_engine.dispose()

    read_started = threading.Event()
    release_read = threading.Event()
    session_closed = threading.Event()

    class SlowSession:
        def execute(self, _statement):
            read_started.set()
            release_read.wait(timeout=2)
            return SimpleNamespace(scalar_one_or_none=lambda: DATABASE_ORIGIN)

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            session_closed.set()

    timed_app = ServerBrowserCorsApp(
        app,
        session_factory=SlowSession,
        fallback_origins=(FALLBACK_ORIGIN,),
        read_timeout_seconds=0.01,
    )

    async def timeout_scenario() -> tuple[
        httpx.Response,
        httpx.Response,
        httpx.Response,
    ]:
        async with _running_asgi_app(timed_app):
            assert read_started.is_set()
            release_read.set()
            for _ in range(100):
                if timed_app.pending_read_count == 0 and session_closed.is_set():
                    break
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=timed_app, raise_app_exceptions=False),
                base_url="http://backend.test",
            ) as client:
                fallback = await client.get(
                    "/unauthorized",
                    headers={"Origin": FALLBACK_ORIGIN},
                )
                late_database_origin = await client.get(
                    "/explode",
                    headers={"Origin": DATABASE_ORIGIN},
                )
                fallback_error = await client.get(
                    "/explode",
                    headers={"Origin": FALLBACK_ORIGIN},
                )
                return fallback, late_database_origin, fallback_error

    fallback_response, late_response, fallback_error_response = asyncio.run(
        timeout_scenario()
    )
    assert fallback_response.status_code == 401
    assert fallback_response.headers["access-control-allow-origin"] == FALLBACK_ORIGIN
    assert late_response.status_code == 500
    assert "access-control-allow-origin" not in late_response.headers
    assert fallback_error_response.status_code == 500
    assert (
        fallback_error_response.headers["access-control-allow-origin"]
        == FALLBACK_ORIGIN
    )
    assert session_closed.is_set()
    assert timed_app.pending_read_count == 0


def test_server_cli_runs_asgi_app_and_preserves_fastapi_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_environment = _environment(
        tmp_path / "invalid",
        allowed_origins="javascript:synthetic-secret",
    )
    with pytest.raises(ServerHostAssemblyError) as invalid_fallback:
        create_server_host(load_server_configuration(invalid_environment))
    assert "synthetic-secret" not in str(invalid_fallback.value)

    environment = _environment(tmp_path, allowed_origins=FALLBACK_ORIGIN)
    host = create_server_host(load_server_configuration(environment))
    captured: list[object] = []

    def fake_create_host(_configuration, *, worker_environment=None):
        assert worker_environment is environment
        return host

    def runner(asgi_app, **_kwargs) -> None:
        captured.append(asgi_app)
        assert asgi_app is host.asgi_app
        assert host.app.state.server_engine is not None

    monkeypatch.setattr(server_cli, "create_server_host", fake_create_host)
    server_cli.main(environment, runner=runner)

    assert captured == [host.asgi_app]
