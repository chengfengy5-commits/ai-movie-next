"""Pure tests for the credential binding capture application service."""

from __future__ import annotations

import builtins
import importlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from haoai_backend.generation_contracts.types import CredentialBinding
from haoai_backend.generation_credentials.binding_capture_ports import (
    BindingCaptureBinding,
    BindingCapturePrincipal,
    BindingCaptureVersion,
    LegacyConfigIdentityFacts,
)
from haoai_backend.generation_credentials.domain import (
    CredentialScope,
    EndpointPolicy,
    HmacProofAuthority,
    ProtectedAccountRecord,
    RootEnrollmentSource,
    RotationAttestation,
    make_server_attempt,
)


NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
SCOPE = CredentialScope(
    source="system",
    row_id="10000000-0000-4000-8000-000000000001",
    owner_or_system="system",
    provider="openai",
)
BINDING_ID = "20000000-0000-4000-8000-000000000001"
PRINCIPAL_ID = "30000000-0000-4000-8000-000000000001"
CONTENT_PATHS = ("/v1/files/{file_id}",)


class FakeBindingCaptureStore:
    def __init__(
        self,
        *,
        config: LegacyConfigIdentityFacts | None,
        binding: BindingCaptureBinding,
        version: BindingCaptureVersion,
        versions: tuple[BindingCaptureVersion, ...],
        principal: BindingCapturePrincipal | None,
    ) -> None:
        self.config = config
        self.binding = binding
        self.version = version
        self.versions = versions
        self.principal = principal
        self.calls: list[str] = []

    def lock_config_identity(
        self,
        scope: CredentialScope,
    ) -> LegacyConfigIdentityFacts | None:
        self.calls.append("config")
        return self.config

    def lock_binding_for_capture(
        self,
        scope: CredentialScope,
    ) -> BindingCaptureBinding | None:
        self.calls.append("binding")
        return self.binding

    def read_capture_version(
        self,
        binding_id: str,
        version: int,
    ) -> BindingCaptureVersion | None:
        self.calls.append("head_version")
        assert binding_id == BINDING_ID
        assert version == 2
        return self.version

    def read_capture_versions(
        self,
        binding_id: str,
    ) -> tuple[BindingCaptureVersion, ...]:
        self.calls.append("version_chain")
        assert binding_id == BINDING_ID
        return self.versions

    def find_capture_principal(
        self,
        principal_id: str,
    ) -> BindingCapturePrincipal | None:
        self.calls.append("principal")
        assert principal_id == PRINCIPAL_ID
        return self.principal


def _signed_record(
    authority: HmacProofAuthority,
    *,
    record_id: str,
    key_generation: str,
) -> ProtectedAccountRecord:
    unsigned = ProtectedAccountRecord(
        record_id=record_id,
        principal_id=PRINCIPAL_ID,
        scope=SCOPE,
        issuer_namespace="provider-account-v1",
        stable_account_subject="account-subject-1",
        key_generation=key_generation,
        material_ref=f"vault://provider/{key_generation}",
        evidence_ref=f"evidence://{record_id}",
        evidence_version="fixture-evidence-v1",
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=1),
        revoked_at=None,
        endpoint_policy=EndpointPolicy.from_base_url(
            "https://provider.example.test/v2",
            allowed_content_paths=CONTENT_PATHS,
        ),
        guard_key_id="fixture-guard-v1",
        secret_consistency_guard="a" * 64,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    return replace(
        unsigned,
        proof_signature=authority.sign(
            "root-enrollment-v1",
            unsigned.proof_payload(),
        ),
    )


def _version_row(
    authority: HmacProofAuthority,
    record: ProtectedAccountRecord,
    *,
    number: int,
    operation: str,
    rotation_proof_ref: str | None,
) -> BindingCaptureVersion:
    attempt = make_server_attempt(
        authority,
        attempt_id=f"capture-attempt-{number}",
        actor_id="40000000-0000-4000-8000-000000000001",
        operation=operation,  # type: ignore[arg-type]
        scope=SCOPE,
        key_generation=record.key_generation,
        expected_revision=number - 1 if number > 1 else None,
        predecessor_version=number - 1 if number > 1 else None,
        protected_record_id=record.record_id,
        rotation_proof_ref=rotation_proof_ref,
    )
    return {
        "binding_id": BINDING_ID,
        "binding_version": number,
        "source": SCOPE.source,
        "row_id": SCOPE.row_id,
        "owner_or_system": SCOPE.owner_or_system,
        "provider": SCOPE.provider,
        "principal_id": record.principal_id,
        "issuer_namespace": record.issuer_namespace,
        "stable_account_subject": record.stable_account_subject,
        "key_generation": record.key_generation,
        "attempt_key_generation": attempt.key_generation,
        "record_id": record.record_id,
        "protected_record_id": attempt.protected_record_id,
        "evidence_ref": record.evidence_ref,
        "evidence_version": record.evidence_version,
        "endpoint_origin": record.endpoint_policy.provider_origin,
        "api_base_path": record.endpoint_policy.api_base_path,
        "allowed_content_paths": json.dumps(list(CONTENT_PATHS)),
        "endpoint_policy_version": record.endpoint_policy.policy_version,
        "version_kind": "registered" if number == 1 else "rotation",
        "predecessor_version": attempt.predecessor_version,
        "expected_revision": attempt.expected_revision,
        "rotation_proof_ref": rotation_proof_ref,
        "guard_key_id": record.guard_key_id,
        "secret_consistency_guard": record.secret_consistency_guard,
        "server_attempt_id": attempt.attempt_id,
        "actor_id": attempt.actor_id,
        "operation": attempt.operation,
        "proof_key_id": attempt.proof_key_id,
        "command_proof": attempt.command_proof,
    }


def _capture_case(
    *,
    principal: BindingCapturePrincipal | None = None,
    rotation_proof: bool = True,
    revision: object = 2,
    current_version: object = 2,
    config: LegacyConfigIdentityFacts | None = None,
):
    authority = HmacProofAuthority("root-test-key", b"fixture-proof-key-32-bytes-long!!")
    original = _signed_record(
        authority,
        record_id="capture-record-1",
        key_generation="generation-1",
    )
    current_record = _signed_record(
        authority,
        record_id="capture-record-2",
        key_generation="generation-2",
    )
    proof = RotationAttestation(
        proof_id="capture-rotation-proof-2",
        original_record_id=original.record_id,
        replacement_record_id=current_record.record_id,
        binding_id=BINDING_ID,
        predecessor_version=1,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    proof = replace(
        proof,
        proof_signature=authority.sign("root-rotation-v1", proof.proof_payload()),
    )
    source = RootEnrollmentSource(
        (original, current_record),
        authority,
        (proof,) if rotation_proof else (),
    )
    versions = (
        _version_row(
            authority,
            original,
            number=1,
            operation="register",
            rotation_proof_ref=None,
        ),
        _version_row(
            authority,
            current_record,
            number=2,
            operation="rotate",
            rotation_proof_ref=proof.proof_id,
        ),
    )
    if config is None:
        config = LegacyConfigIdentityFacts(
            scope=SCOPE,
            category="image",
            provider=SCOPE.provider,
            model="fixture-model",
            base_url="https://provider.example.test/v2",
            active=True,
        )
    if principal is None:
        principal = {
            "source_state": "valid",
            "provider": SCOPE.provider,
            "owner_scope": SCOPE.owner_or_system,
            "issuer_namespace": current_record.issuer_namespace,
            "stable_account_subject": current_record.stable_account_subject,
            "record_id": original.record_id,
        }
    store = FakeBindingCaptureStore(
        config=config,
        binding={
            "binding_id": BINDING_ID,
            "revision": revision,
            "current_version": current_version,
            "enabled": True,
        },
        version=versions[1],
        versions=versions,
        principal=principal,
    )
    return authority, source, store


def _service(source: RootEnrollmentSource, authority: HmacProofAuthority):
    module = importlib.import_module(
        "haoai_backend.generation_credentials.binding_capture_application"
    )
    return module.CredentialBindingCaptureApplication(source, authority)


def test_capture_accepts_real_rotation_proofs_and_keeps_original_principal_record_id() -> None:
    authority, source, store = _capture_case()

    captured = _service(source, authority).capture(store, SCOPE, now=NOW)

    assert captured is not None
    assert captured.binding.binding_version == "2"
    assert captured.binding.rotation_provenance == "capture-rotation-proof-2"
    assert captured.binding.original_principal_id == PRINCIPAL_ID
    assert store.calls == [
        "config",
        "binding",
        "head_version",
        "version_chain",
        "principal",
    ]


@pytest.mark.parametrize(
    ("principal", "rotation_proof"),
    [
        (
            {
                "source_state": "valid",
                "provider": SCOPE.provider,
                "owner_scope": SCOPE.owner_or_system,
                "issuer_namespace": "provider-account-v1",
                "stable_account_subject": "wrong-subject",
            },
            True,
        ),
        (
            {
                "source_state": "revoked",
                "provider": SCOPE.provider,
                "owner_scope": SCOPE.owner_or_system,
                "issuer_namespace": "provider-account-v1",
                "stable_account_subject": "account-subject-1",
            },
            True,
        ),
        (None, False),
    ],
)
def test_capture_rejects_invalid_principal_or_rotation_evidence(
    principal: BindingCapturePrincipal | None,
    rotation_proof: bool,
) -> None:
    authority, source, store = _capture_case(
        principal=principal,
        rotation_proof=rotation_proof,
    )

    captured = _service(source, authority).capture(store, SCOPE, now=NOW)

    assert captured is None


@pytest.mark.parametrize(
    ("revision", "current_version"),
    [(True, 2), (2, True)],
)
def test_capture_requires_exact_integer_binding_counters(
    revision: object,
    current_version: object,
) -> None:
    authority, source, store = _capture_case(
        revision=revision,
        current_version=current_version,
    )

    assert _service(source, authority).capture(store, SCOPE, now=NOW) is None
    assert store.calls == ["config", "binding"]


def test_binding_is_locked_even_when_config_identity_is_missing() -> None:
    authority, source, store = _capture_case(config=None)
    store.config = None

    assert _service(source, authority).capture(store, SCOPE, now=NOW) is None
    assert store.calls == ["config", "binding"]


@pytest.mark.parametrize("mismatch", ["scope", "endpoint"])
def test_capture_rejects_config_scope_or_endpoint_mismatch(mismatch: str) -> None:
    authority, source, store = _capture_case()
    assert store.config is not None
    if mismatch == "scope":
        store.config = replace(
            store.config,
            scope=CredentialScope(
                source="system",
                row_id="10000000-0000-4000-8000-000000000002",
                owner_or_system="system",
                provider=SCOPE.provider,
            ),
        )
    else:
        store.config = replace(
            store.config,
            base_url="https://provider.example.test/v3",
        )

    captured = _service(source, authority).capture(store, SCOPE, now=NOW)

    assert captured is None


def test_capture_executes_when_sqlalchemy_imports_are_blocked(monkeypatch) -> None:
    authority, source, store = _capture_case()
    original_import = builtins.__import__

    def reject_sqlalchemy(name, *args, **kwargs):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise AssertionError("pure capture application imported SQLAlchemy")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_sqlalchemy)
    module = importlib.import_module(
        "haoai_backend.generation_credentials.binding_capture_application"
    )
    module = importlib.reload(module)

    captured = module.CredentialBindingCaptureApplication(source, authority).capture(
        store,
        SCOPE,
        now=NOW,
    )

    assert captured is not None
