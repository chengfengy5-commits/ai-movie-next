from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_credentials.application import (
    CredentialRegistry,
    CredentialRegistryReadiness,
    build_credential_registry,
    check_credential_registry_readiness,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.generation_runtime.worker_service import GenerationWorkerService
from haoai_backend.frame_review_app import create_frame_review_app

from generation_execution import test_frame_review_admission as admission


_SECRET = admission._JWT_SECRET


class _UnusedClientContext:
    async def __aenter__(self):
        raise AssertionError("provider must not be called by these gate tests")

    async def __aexit__(self, *_args):
        return False


def _runtime(session_factory, capabilities) -> GenerationRuntimeConfig:
    registry = build_credential_registry(capabilities)
    return GenerationRuntimeConfig(
        session_factory=session_factory,
        db_clock=capabilities.db_clock,
        id_factory=capabilities.id_factory,
        http_client_factory=_UnusedClientContext,
        credential_resolver=registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=lambda: object(),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: object(),
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
        shutdown_timeout_seconds=1.0,
    )


def build_fixture_app(tmp_path: Path, monkeypatch):
    engine, _legacy_app, token, capabilities, _crypto_state = admission._build_app(
        tmp_path, monkeypatch
    )
    session_factory = capabilities.read_session_factory
    assert session_factory is not None
    token_codec = JoseTokenCodec(_SECRET)
    auth_runtime = AuthenticationRuntime(
        session_factory=session_factory,
        secret_key=_SECRET,
        token_codec=token_codec,
    )
    app = create_frame_review_app(
        session_factory=session_factory,
        authentication_runtime=auth_runtime,
        registry_capabilities=capabilities,
        generation_runtime=_runtime(session_factory, capabilities),
        id_factory=capabilities.id_factory,
        clock=capabilities.db_clock,
    )
    return engine, app, token, capabilities


async def _send(app, token: str, method: str, path: str, body=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://frame-review-user-flow.test",
    ) as client:
        return await client.request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )


def test_new_frame_review_routes_share_readiness_and_worker_gate(
    tmp_path: Path, monkeypatch
) -> None:
    engine, app, token, capabilities = build_fixture_app(tmp_path, monkeypatch)
    try:
        closed = asyncio.run(
            _send(app, token, "POST", "/api/chat/frame-review", admission._review_body())
        )
        assert closed.status_code == 503
        with engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []

        async def exercise_running_app():
            async with app.router.lifespan_context(app):
                assert isinstance(app.state.frame_review_worker_service, GenerationWorkerService)
                capabilities_response = await _send(
                    app, token, "GET", "/api/chat/frame-review/capabilities"
                )
                assert capabilities_response.status_code == 200
                assert capabilities_response.json() == {
                    "operation": "frame.review",
                    "enabled": True,
                    "reason": None,
                    "supported_providers": ["openai", "deepseek", "custom"],
                }
                submitted = await _send(
                    app,
                    token,
                    "POST",
                    "/api/chat/frame-review",
                    admission._review_body(),
                )
                assert submitted.status_code == 200, submitted.text
                app.state.frame_review_worker_service.shutdown("gate test")
                stopped = await _send(
                    app, token, "GET", "/api/chat/frame-review/capabilities"
                )
                rejected_after_stop = await _send(
                    app,
                    token,
                    "POST",
                    "/api/chat/frame-review",
                    admission._review_body(),
                )
                return submitted.json(), stopped, rejected_after_stop

        receipt, stopped, rejected_after_stop = asyncio.run(exercise_running_app())
        assert receipt["task_id"]
        assert receipt["model_name"] == "Public joint-fixture model"
        assert stopped.json()["enabled"] is False
        assert stopped.json()["reason"] == "worker_unavailable"
        assert rejected_after_stop.status_code == 503
        with engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
        assert [task["id"] for task in tasks] == [receipt["task_id"]]
    finally:
        engine.dispose()


def test_registered_post_preserves_real_admission_refusal_without_writes(
    tmp_path: Path, monkeypatch
) -> None:
    engine, app, token, _capabilities = build_fixture_app(tmp_path, monkeypatch)
    body = admission._review_body()
    body["frame_index"] = 99
    try:
        async def exercise_running_app():
            async with app.router.lifespan_context(app):
                return await _send(app, token, "POST", "/api/chat/frame-review", body)

        response = asyncio.run(exercise_running_app())
        assert response.status_code == 422
        assert response.json() == {"detail": "分镜索引超出当前章节范围"}
        with engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(BillingUnitRow.__table__)).all() == []
            assert connection.execute(select(ExecutionStepRow.__table__)).all() == []
            assert connection.execute(select(TaskSubmissionRow.__table__)).all() == []
            assert connection.execute(select(prepared_payloads)).all() == []
    finally:
        engine.dispose()


def test_missing_proof_authority_disables_inert_but_running_review_worker(
    tmp_path: Path, monkeypatch
) -> None:
    engine, _existing_app, token, capabilities = build_fixture_app(tmp_path, monkeypatch)
    session_factory = capabilities.read_session_factory
    assert session_factory is not None
    missing_proof = replace(capabilities, proof_authority=None)
    session = session_factory()
    try:
        readiness = check_credential_registry_readiness(session, missing_proof)
        assert readiness.ready
    finally:
        session.rollback()
        session.close()

    auth_runtime = AuthenticationRuntime(
        session_factory=session_factory,
        secret_key=_SECRET,
        token_codec=JoseTokenCodec(_SECRET),
    )
    app = create_frame_review_app(
        session_factory=session_factory,
        authentication_runtime=auth_runtime,
        registry_capabilities=missing_proof,
        generation_runtime=_runtime(session_factory, missing_proof),
        id_factory=missing_proof.id_factory,
        clock=missing_proof.db_clock,
    )
    admitted: list[object] = []

    def record_admission(*args, **kwargs):
        admitted.append((args, kwargs))
        raise AssertionError("closed capability gate must stop before admission")

    monkeypatch.setattr("haoai_backend.frame_review_http.admit_frame_review", record_admission)
    try:
        async def exercise_running_app():
            async with app.router.lifespan_context(app):
                worker = app.state.frame_review_worker_service
                assert isinstance(worker, GenerationWorkerService)
                assert worker.is_running
                assert app.state.frame_review_worker_runtime.run_claimed_once("ai-review") == "pending"
                capabilities_response = await _send(
                    app, token, "GET", "/api/chat/frame-review/capabilities"
                )
                submitted = await _send(
                    app,
                    token,
                    "POST",
                    "/api/chat/frame-review",
                    admission._review_body(),
                )
                return capabilities_response, submitted

        capabilities_response, submitted = asyncio.run(exercise_running_app())
        assert capabilities_response.status_code == 200
        assert capabilities_response.json()["enabled"] is False
        assert capabilities_response.json()["reason"] == "not_ready"
        assert submitted.status_code == 503
        assert admitted == []
        with engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(BillingUnitRow.__table__)).all() == []
            assert connection.execute(select(ExecutionStepRow.__table__)).all() == []
            assert connection.execute(select(TaskSubmissionRow.__table__)).all() == []
            assert connection.execute(select(prepared_payloads)).all() == []
    finally:
        engine.dispose()


def test_unconfigured_composition_reports_safe_disabled_capability(
    tmp_path: Path, monkeypatch
) -> None:
    engine, _existing_app, token, capabilities = build_fixture_app(tmp_path, monkeypatch)
    session_factory = capabilities.read_session_factory
    assert session_factory is not None
    auth_runtime = AuthenticationRuntime(
        session_factory=session_factory,
        secret_key=_SECRET,
        token_codec=JoseTokenCodec(_SECRET),
    )
    app = create_frame_review_app(
        session_factory=session_factory,
        authentication_runtime=auth_runtime,
        registry_capabilities=capabilities,
        generation_runtime=_runtime(session_factory, capabilities),
        enabled=False,
    )
    try:
        response = asyncio.run(
            _send(app, token, "GET", "/api/chat/frame-review/capabilities")
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False
        assert response.json()["reason"] == "not_configured"
    finally:
        engine.dispose()


def test_readiness_failure_closes_both_routes_before_admission(
    tmp_path: Path, monkeypatch
) -> None:
    engine, app, token, _capabilities = build_fixture_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "haoai_backend.frame_review_http.check_credential_registry_readiness",
        lambda *_args: CredentialRegistryReadiness(
            "not_ready", "not_ready", "not_ready", "not_ready", ("test",)
        ),
    )
    try:
        async def exercise_closed_gate():
            async with app.router.lifespan_context(app):
                capabilities_response = await _send(
                    app, token, "GET", "/api/chat/frame-review/capabilities"
                )
                submitted = await _send(
                    app,
                    token,
                    "POST",
                    "/api/chat/frame-review",
                    admission._review_body(),
                )
                return capabilities_response, submitted

        capabilities_response, submitted = asyncio.run(exercise_closed_gate())
        assert capabilities_response.status_code == 200
        assert capabilities_response.json()["enabled"] is False
        assert capabilities_response.json()["reason"] == "not_ready"
        assert submitted.status_code == 503
        with engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
    finally:
        engine.dispose()


def test_registered_composition_requires_one_shared_session_factory(
    tmp_path: Path, monkeypatch
) -> None:
    engine, existing_app, _token, capabilities = build_fixture_app(tmp_path, monkeypatch)
    del existing_app
    try:
        session_factory = capabilities.read_session_factory
        assert session_factory is not None
        separate_factory = lambda: session_factory()
        auth_runtime = AuthenticationRuntime(
            session_factory=session_factory,
            secret_key=_SECRET,
            token_codec=JoseTokenCodec(_SECRET),
        )
        with pytest.raises(ValueError, match="same session factory"):
            create_frame_review_app(
                session_factory=separate_factory,
                authentication_runtime=auth_runtime,
                registry_capabilities=capabilities,
                generation_runtime=_runtime(session_factory, capabilities),
            )
    finally:
        engine.dispose()


def test_registered_composition_rejects_foreign_clock_before_building_registry(
    tmp_path: Path, monkeypatch
) -> None:
    engine, _existing_app, _token, capabilities = build_fixture_app(tmp_path, monkeypatch)
    session_factory = capabilities.read_session_factory
    assert session_factory is not None
    future_now = capabilities.db_clock() + timedelta(days=2)
    foreign_clock_calls: list[datetime] = []

    def foreign_clock() -> datetime:
        foreign_clock_calls.append(future_now)
        return future_now

    auth_runtime = AuthenticationRuntime(
        session_factory=session_factory,
        secret_key=_SECRET,
        token_codec=JoseTokenCodec(_SECRET),
    )
    generation_runtime = _runtime(session_factory, capabilities)
    registry_builds: list[object] = []
    original_registry_builder = build_credential_registry

    def record_registry_build(*args, **kwargs):
        registry_builds.append((args, kwargs))
        return original_registry_builder(*args, **kwargs)

    monkeypatch.setattr(
        "haoai_backend.frame_review_app.build_credential_registry",
        record_registry_build,
    )
    try:
        with pytest.raises(ValueError, match="explicit clock"):
            create_frame_review_app(
                session_factory=session_factory,
                authentication_runtime=auth_runtime,
                registry_capabilities=capabilities,
                generation_runtime=generation_runtime,
                id_factory=capabilities.id_factory,
                clock=foreign_clock,
            )
        assert foreign_clock_calls == []
        assert registry_builds == []
    finally:
        engine.dispose()
