from __future__ import annotations

from datetime import datetime, timezone

import pytest

from haoai_backend.generation_contracts.types import (
    CommittedIntent,
    CredentialBinding,
    EndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    ModelReference,
    TargetSnapshot,
)
from haoai_backend.generation_providers.image import build_original_openai_image_provider


def _values():
    snapshot = FrozenExecutionSnapshot(
        model_ref=ModelReference("system", "openai", "model-1"),
        model_category="image",
        model_name="gpt-image-1",
        model_params={"size": "1024x1024"},
        prompt_ref=None,
        effective_prompt="frozen prompt",
        effective_context="",
        server_quote=5,
        reference_snapshot_ref="payload/references",
        target_snapshot=TargetSnapshot(kind="none"),
        credential_binding_ref="credential-binding:v1",
        grant_snapshot=FrozenGrantSnapshot("owner", None, ("admitted",)),
        adapter_config_versions=(("openai", "v1"),),
        endpoint_policy=EndpointPolicy(provider_origin="https://api.example.test"),
    )
    binding = CredentialBinding(
        source="system",
        row_id="model-1",
        owner_or_system="system",
        provider="openai",
        original_principal_id="principal-1",
        endpoint_origin="https://api.example.test",
        allowed_content_paths=(),
        binding_version="1",
    )
    intent = CommittedIntent(
        step_id="step-1",
        attempt_id="attempt-1",
        external_submission_id="submission-1",
        provider="openai",
        model_id="model-1",
        request_digest="a" * 64,
        snapshot_ref="execution-snapshot-1",
        committed_at=datetime(2026, 10, 16, tzinfo=timezone.utc),
        first_intent_at=datetime(2026, 10, 16, tzinfo=timezone.utc),
        deadline_at=datetime(2026, 10, 16, 0, 30, tzinfo=timezone.utc),
    )
    return snapshot, binding, intent


def _factory_kwargs():
    snapshot, binding, intent = _values()
    calls = {"http": 0, "credential": 0}

    def http_factory():
        calls["http"] += 1
        raise AssertionError("HTTP client is opened only by generate_sync_image")

    def resolver(_binding, _purpose):
        calls["credential"] += 1
        return None

    return {
        "snapshot": snapshot,
        "execution_snapshot_ref": "execution-snapshot-1",
        "credential_binding": binding,
        "intent": intent,
        "wire_model": snapshot.model_name,
        "http_client_factory": http_factory,
        "credential_resolver": resolver,
    }, calls


def test_public_factory_builds_original_adapter_without_opening_http() -> None:
    kwargs, calls = _factory_kwargs()
    provider = build_original_openai_image_provider(**kwargs)
    assert provider is not None
    assert calls == {"http": 0, "credential": 0}


@pytest.mark.parametrize("mismatch", ("model", "origin", "scope", "intent"))
def test_public_factory_rejects_mismatched_original_bindings(mismatch: str) -> None:
    kwargs, calls = _factory_kwargs()
    if mismatch == "model":
        kwargs["wire_model"] = "current-model"
    elif mismatch == "origin":
        kwargs["credential_binding"] = CredentialBinding(
            source="system",
            row_id="model-1",
            owner_or_system="system",
            provider="openai",
            original_principal_id="principal-1",
            endpoint_origin="https://other.example.test",
            allowed_content_paths=(),
            binding_version="1",
        )
    elif mismatch == "scope":
        kwargs["credential_binding"] = CredentialBinding(
            source="user",
            row_id="model-1",
            owner_or_system="another-owner",
            provider="openai",
            original_principal_id="principal-1",
            endpoint_origin="https://api.example.test",
            allowed_content_paths=(),
            binding_version="1",
        )
    else:
        kwargs["execution_snapshot_ref"] = "other-snapshot"

    with pytest.raises(ValueError):
        build_original_openai_image_provider(**kwargs)
    assert calls == {"http": 0, "credential": 0}
