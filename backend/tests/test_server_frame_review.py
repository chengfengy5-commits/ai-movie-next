"""Standard server-host wiring for the opt-in frame.review family."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from backend.server.application import ServerHostAssemblyError, create_server_host
from backend.server.configuration import load_server_configuration
from backend.server.credentials import provision_credential_bundle
from haoai_backend.chat_text_generation.ports import (
    ChatTextGenerationConfiguration,
    FrameReviewAdmissionConfiguration,
)
from haoai_backend.generation_runtime.frame_review_assembly import (
    FrameReviewWorkerConfiguration,
)
from haoai_backend.generation_runtime.worker_capacity import WorkerCapacityBootstrap
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.frame_review_cancellation import (
    FrameReviewCancellationFacade,
)


def _write_owner_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, json.dumps(value, separators=(",", ":")).encode())
    finally:
        os.close(descriptor)


def _review_bundle(tmp_path: Path) -> Path:
    encode = lambda value: base64.b64encode(value).decode("ascii")
    input_path = tmp_path / "review-root-input.json"
    bundle_path = tmp_path / "review-credentials.json"
    _write_owner_json(
        input_path,
        {
            "format": "haoai-root-enrollment-input-v1",
            "proof_key": {"key_id": "proof", "key_base64": encode(b"p" * 32)},
            "guard_key": {"key_id": "guard", "key_base64": encode(b"g" * 32)},
            "aead_key": {
                "key_id": "aead",
                "algorithm_version": "aes-256-gcm-v1",
                "key_base64": encode(b"a" * 32),
            },
            "records": [
                {
                    "record_id": "synthetic-review-record",
                    "principal_id": "30000000-0000-4000-8000-000000000001",
                    "scope": {
                        "source": "user",
                        "row_id": "20000000-0000-4000-8000-000000000001",
                        "owner_or_system": "10000000-0000-4000-8000-000000000001",
                        "provider": "openai",
                    },
                    "issuer_namespace": "provider-account-v1",
                    "stable_account_subject": "synthetic-review-account",
                    "key_generation": "generation-1",
                    "material_ref": "synthetic://review/provider-account",
                    "provider_secret": "synthetic-provider-secret",
                    "evidence_ref": "synthetic-evidence://review",
                    "evidence_version": "synthetic-v1",
                    "valid_from": "2026-01-01T00:00:00+00:00",
                    "valid_until": None,
                    "revoked_at": None,
                    "endpoint_policy": {
                        "provider_origin": "https://provider.example.test",
                        "api_base_path": "/v2",
                        "allowed_content_paths": [],
                        "policy_version": "endpoint-v1",
                    },
                }
            ],
        },
    )
    assert provision_credential_bundle(input_path, bundle_path) == 1
    return bundle_path


def _environment(tmp_path: Path, **values: str) -> dict[str, str]:
    return {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'server.sqlite'}",
        "SECRET_KEY": "synthetic-server-secret-for-frame-review",
        **values,
    }


def _request(app: FastAPI, method: str, path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(
                method, path, headers={"authorization": "Bearer synthetic"}
            )

    return asyncio.run(run())


def test_server_host_keeps_review_disabled_and_mounts_not_configured_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.server import frame_review as frame_review_host
    from haoai_backend import frame_review_http

    def should_not_load(*_args, **_kwargs):
        pytest.fail("disabled frame.review must not load credentials")

    monkeypatch.setattr(frame_review_host, "load_registry_capabilities", should_not_load)
    monkeypatch.setattr(
        frame_review_http,
        "resolve_active_actor",
        lambda _service, _authorization: TrustedActor("synthetic-user"),
    )
    missing_bundle = tmp_path / "unused-review-bundle.json"
    configuration = load_server_configuration(
        _environment(
            tmp_path,
            FRAME_REVIEW_ENABLED="false",
            FRAME_REVIEW_CREDENTIALS_FILE=str(missing_bundle),
        )
    )
    host = create_server_host(configuration)
    try:
        chat_configuration = host.app.state.server_frame_review_configuration
        assert chat_configuration.enabled is False
        assert chat_configuration.worker_configuration is None
        assert host.app.state.frame_review_worker_runtime is None
        assert host.app.state.worker_execution_capacity is None
        paths = [route.path for route in host.app.routes]
        assert paths.count("/api/chat/frame-review/capabilities") == 1
        assert paths.count("/api/chat/frame-review") == 1
        capability = _request(
            host.app, "GET", "/api/chat/frame-review/capabilities"
        )
        assert capability.status_code == 200
        assert capability.json()["reason"] == "not_configured"
        assert capability.json()["enabled"] is False
        assert _request(host.app, "POST", "/api/chat/frame-review").status_code == 503
    finally:
        host.close()


def test_server_host_composes_review_route_admission_worker_capacity_and_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haoai_backend import app as app_module
    from haoai_backend import frame_review_http

    bundle_path = _review_bundle(tmp_path)
    configuration = load_server_configuration(
        _environment(
            tmp_path,
            FRAME_REVIEW_ENABLED="true",
            FRAME_REVIEW_CREDENTIALS_FILE=str(bundle_path),
            AI_USER_MAX_CONCURRENT="3",
        )
    )
    monkeypatch.setattr(
        frame_review_http,
        "resolve_active_actor",
        lambda _service, _authorization: TrustedActor("synthetic-user"),
    )
    monkeypatch.setattr(frame_review_http, "_registry_is_ready", lambda *_args: True)

    captured: dict[str, object] = {}
    original_create_app = app_module.create_app
    original_observation_router = app_module.build_task_observation_router

    def capture_create_app(**kwargs: object):
        captured.update(kwargs)
        return original_create_app(**kwargs)

    def capture_observation_router(**kwargs: object):
        captured["frame_review_cancellation"] = kwargs["frame_review_cancellation"]
        return original_observation_router(**kwargs)

    monkeypatch.setattr(app_module, "create_app", capture_create_app)
    monkeypatch.setattr(
        app_module, "build_task_observation_router", capture_observation_router
    )
    host = create_server_host(configuration)
    try:
        review_configuration = host.app.state.server_frame_review_configuration
        assert review_configuration.enabled is True
        worker_configuration = captured["frame_review_worker_configuration"]
        assert isinstance(worker_configuration, FrameReviewWorkerConfiguration)
        chat_configuration = captured["chat_text_generation_configuration"]
        assert isinstance(chat_configuration, ChatTextGenerationConfiguration)
        assert chat_configuration.runtime is None
        assert isinstance(
            chat_configuration.frame_review_admission,
            FrameReviewAdmissionConfiguration,
        )
        capabilities = worker_configuration.registry_capabilities
        assert chat_configuration.frame_review_admission.registry_capabilities is capabilities
        assert review_configuration.registry_capabilities is capabilities
        authentication_runtime = captured["authentication_runtime"]
        assert authentication_runtime.session_factory is host.session_factory
        assert capabilities.read_session_factory is host.session_factory
        assert worker_configuration.runtime.session_factory is host.session_factory
        assert worker_configuration.runtime.db_clock is capabilities.db_clock
        assert worker_configuration.runtime.id_factory is capabilities.id_factory
        assert chat_configuration.clock is capabilities.db_clock
        assert chat_configuration.server_id_factory is capabilities.id_factory
        assert isinstance(captured["frame_review_cancellation"], FrameReviewCancellationFacade)
        cancellation = captured["frame_review_cancellation"].cancellation
        assert cancellation.session_factory is host.session_factory
        assert cancellation.db_clock is capabilities.db_clock
        assert cancellation.id_factory is capabilities.id_factory
        assert cancellation.crypto is capabilities.crypto
        assert cancellation.proof_authority is capabilities.proof_authority

        worker_runtime = host.app.state.frame_review_worker_runtime
        assert worker_runtime is not None
        assert worker_runtime._registered_factory("ai-review") is not None
        assert host.app.state.frame_review_worker_service is None
        capacity = host.app.state.worker_execution_capacity
        assert capacity is not None
        assert worker_runtime._config.worker_capacity is capacity
        assert isinstance(
            captured["worker_capacity_bootstrap"], WorkerCapacityBootstrap
        )
        assert sum(route.path == "/api/chat/frame-review" for route in host.app.routes) == 1
        assert sum(
            route.path == "/api/chat/frame-review/capabilities"
            for route in host.app.routes
        ) == 1

        monkeypatch.setattr(frame_review_http, "_registry_is_ready", lambda *_args: False)
        not_ready = _request(
            host.app, "GET", "/api/chat/frame-review/capabilities"
        )
        assert not_ready.status_code == 200
        assert not_ready.json()["reason"] == "not_ready"

        monkeypatch.setattr(frame_review_http, "_registry_is_ready", lambda *_args: True)
        capability = _request(
            host.app, "GET", "/api/chat/frame-review/capabilities"
        )
        assert capability.status_code == 200
        assert capability.json()["reason"] == "worker_unavailable"
        assert capability.json()["enabled"] is False
        assert _request(host.app, "POST", "/api/chat/frame-review").status_code == 503
    finally:
        host.close()


def test_enabled_review_rejects_missing_bundle_without_leaking_path_or_secret(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "private-review-secret-bundle.json"
    configuration = load_server_configuration(
        _environment(
            tmp_path,
            FRAME_REVIEW_ENABLED="true",
            FRAME_REVIEW_CREDENTIALS_FILE=str(bundle_path),
        )
    )
    with pytest.raises(ServerHostAssemblyError) as caught:
        create_server_host(configuration)
    assert str(bundle_path) not in str(caught.value)
    assert "synthetic-server-secret" not in str(caught.value)
