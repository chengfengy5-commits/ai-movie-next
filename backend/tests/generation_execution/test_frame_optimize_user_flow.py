"""Public entry and real SQLite worker path for registered frame.optimize."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import user_sessions
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_core.base_schema import AITaskRow, BillingUnitRow
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_credentials.domain import CredentialCrypto
from haoai_backend.generation_runtime.frame_optimize_assembly import (
    FrameOptimizeWorkerConfiguration,
)
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from generation_core.test_admission_staging import _G2_JOINT_ACTOR_ID, _G2_JOINT_NOW
from generation_execution.test_frame_optimize_admission import (
    _build_optimize_case,
)


_JWT_SECRET = "frame-optimize-runtime-test-only"
_SESSION_ID = "frame-optimize-runtime-session"
_TOKEN_ID = "frame-optimize-runtime-token"


class _Response:
    status_code = 200
    headers = {"content-length": "67"}

    async def iter_bytes(self, *, chunk_size: int):
        yield json.dumps(
            {"choices": [{"message": {"content": "优化后的真实分镜文本"}}]},
            ensure_ascii=False,
        ).encode("utf-8")


class _Client:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def request(self, method, url, *, headers, content, timeout_seconds, follow_redirects):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "content": content,
                "timeout_seconds": timeout_seconds,
                "follow_redirects": follow_redirects,
            }
        )
        return _Response()


class _ClientContext:
    def __init__(self, client: _Client) -> None:
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return False


def test_public_route_runs_real_producer_and_registered_worker(tmp_path) -> None:
    engine, _producer_sessions, capabilities, _crypto_state, _ids, request = (
        _build_optimize_case(tmp_path, frame_count=1, request_indexes=(0,))
    )
    session_factory = capabilities.read_session_factory
    assert session_factory is not None
    authentication_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    with engine.begin() as connection:
        asset_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(storyboard_assets)")).mappings()
        }
        if "created_at" not in asset_columns:
            connection.execute(text("ALTER TABLE storyboard_assets ADD COLUMN created_at DATETIME"))
    stamp = _G2_JOINT_NOW.replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            user_sessions.insert().values(
                id=_SESSION_ID,
                user_id=_G2_JOINT_ACTOR_ID,
                jti=_TOKEN_ID,
                user_agent="synthetic frame optimize test",
                ip_address="127.0.0.1",
                device_name="sqlite fixture",
                is_active=True,
                created_at=stamp,
                last_seen_at=stamp,
                revoked_at=None,
            )
        )

    token_codec = JoseTokenCodec(_JWT_SECRET)
    authentication_runtime = AuthenticationRuntime(
        session_factory=session_factory,
        secret_key=_JWT_SECRET,
        token_codec=token_codec,
    )
    registry = build_credential_registry(capabilities)
    client = _Client()
    runtime = GenerationRuntimeConfig(
        session_factory=session_factory,
        db_clock=capabilities.db_clock,
        id_factory=capabilities.id_factory,
        http_client_factory=lambda: _ClientContext(client),
        credential_resolver=registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=lambda: object(),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: object(),
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
        shutdown_timeout_seconds=2.0,
    )
    app = create_app(
        session_factory=session_factory,
        authentication_runtime=authentication_runtime,
        frame_optimize_worker_configuration=FrameOptimizeWorkerConfiguration(
            runtime=runtime,
            registry_capabilities=capabilities,
            max_user_concurrency=8,
            enabled=True,
        ),
    )
    token = token_codec.encode(
        {
            "sub": _G2_JOINT_ACTOR_ID,
            "jti": _TOKEN_ID,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )

    async def exercise_app() -> dict[str, object]:
        async with app.router.lifespan_context(app):
            assert app.state.frame_optimize_worker_service.is_running
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://frame-optimize-runtime.test",
            ) as http:
                headers = {"Authorization": f"Bearer {token}"}
                capabilities_response = await http.get(
                    "/api/chat/frame-optimize/capabilities", headers=headers
                )
                assert capabilities_response.status_code == 200
                assert capabilities_response.json() == {
                    "operation": "frame.optimize",
                    "enabled": True,
                    "reason": None,
                    "supported_providers": ["openai", "deepseek", "custom"],
                }
                response = await http.post(
                    "/api/chat/frame-optimize", headers=headers, json=request
                )
                assert response.status_code == 200, response.text
                receipt = response.json()
                deadline = asyncio.get_running_loop().time() + 8
                while asyncio.get_running_loop().time() < deadline:
                    with session_factory() as session:
                        task = session.get(AITaskRow, receipt["task_id"])
                        if task is not None and task.status in {"completed", "failed"}:
                            return {
                                "receipt": receipt,
                                "status": task.status,
                                "result": task.result,
                            }
                    await asyncio.sleep(0.05)
                pytest.fail("registered worker did not reach a terminal task status")
        raise AssertionError("application lifespan returned before task observation")

    try:
        result = asyncio.run(exercise_app())
        assert result["status"] == "completed"
        with session_factory() as session:
            task = session.get(AITaskRow, result["receipt"]["task_id"])
            units = session.execute(
                select(BillingUnitRow).where(BillingUnitRow.task_id == task.id)
            ).scalars().all()
            assert task is not None
            assert len(units) == 1
            assert units[0].result_status == "succeeded"
            assert units[0].billing_status == "charged"
            assert units[0].debited_amount == units[0].quoted_amount
        assert len(client.requests) == 1
        call = client.requests[0]
        assert call["method"] == "POST"
        assert call["url"] == "https://provider.example.test/v2/chat/completions"
        assert call["timeout_seconds"] == 300.0
        assert call["follow_redirects"] is False
        payload = json.loads(call["content"])
        assert payload["messages"][1]["content"].endswith("requested frame 0")
        assert "优化后的真实分镜文本" in (result["result"] or "")
    finally:
        engine.dispose()


def test_app_rejects_frame_optimize_session_mismatch_before_composition(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _producer_sessions, capabilities, _crypto_state, _ids, _request = (
        _build_optimize_case(tmp_path, frame_count=1, request_indexes=(0,))
    )
    app_session_factory = capabilities.read_session_factory
    assert app_session_factory is not None
    authentication_session_factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        future=True,
    )
    assert authentication_session_factory is not app_session_factory

    registry = build_credential_registry(capabilities)
    runtime = GenerationRuntimeConfig(
        session_factory=app_session_factory,
        db_clock=capabilities.db_clock,
        id_factory=capabilities.id_factory,
        http_client_factory=lambda: _ClientContext(_Client()),
        credential_resolver=registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=lambda: object(),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: object(),
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
        shutdown_timeout_seconds=2.0,
    )
    optimize_configuration = FrameOptimizeWorkerConfiguration(
        runtime=runtime,
        registry_capabilities=capabilities,
        max_user_concurrency=4,
        enabled=True,
    )
    authentication_runtime = AuthenticationRuntime(
        session_factory=authentication_session_factory,
        secret_key=_JWT_SECRET,
        token_codec=JoseTokenCodec(_JWT_SECRET),
    )

    from haoai_backend import app as app_module

    worker_builds: list[object] = []
    cancellation_builds: list[object] = []
    router_configurations: list[object] = []
    statements: list[str] = []
    material_resolutions: list[str] = []
    original_worker_builder = app_module.build_frame_optimize_worker_runtime
    original_cancellation_factory = app_module.SqlAlchemyFrameOptimizeCancellation
    original_router_builder = app_module.build_frame_optimize_router
    original_material_resolver = CredentialCrypto.resolve_material

    def track_worker_builder(configuration):
        worker_builds.append(configuration)
        return original_worker_builder(configuration)

    def track_cancellation_factory(*args, **kwargs):
        cancellation_builds.append((args, kwargs))
        return original_cancellation_factory(*args, **kwargs)

    def track_router_builder(*args, **kwargs):
        router_configurations.append(kwargs.get("configuration"))
        return original_router_builder(*args, **kwargs)

    def track_material_resolution(self, material_ref: str):
        material_resolutions.append(material_ref)
        return original_material_resolver(self, material_ref)

    monkeypatch.setattr(
        app_module,
        "build_frame_optimize_worker_runtime",
        track_worker_builder,
    )
    monkeypatch.setattr(
        app_module,
        "SqlAlchemyFrameOptimizeCancellation",
        track_cancellation_factory,
    )
    monkeypatch.setattr(
        app_module,
        "build_frame_optimize_router",
        track_router_builder,
    )
    monkeypatch.setattr(
        CredentialCrypto,
        "resolve_material",
        track_material_resolution,
    )
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _connection, _cursor, statement, *_args: statements.append(statement),
    )
    before_threads = {thread.ident for thread in threading.enumerate()}

    try:
        error = None
        try:
            create_app(
                session_factory=app_session_factory,
                authentication_runtime=authentication_runtime,
                frame_optimize_worker_configuration=optimize_configuration,
            )
        except ValueError as exc:
            error = exc

        assert error is not None, (
            "create_app accepted distinct authentication and frame.optimize Session factories; "
            f"worker_builds={len(worker_builds)}, "
            f"cancellation_builds={len(cancellation_builds)}, "
            f"router_configurations={len(router_configurations)}"
        )
        assert "frame.optimize" in str(error)
        assert worker_builds == []
        assert cancellation_builds == []
        assert router_configurations == []
        assert statements == []
        assert material_resolutions == []
        assert {thread.ident for thread in threading.enumerate()} == before_threads
    finally:
        engine.dispose()
