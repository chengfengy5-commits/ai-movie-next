"""Credential registry tests use explicit, non-production Root and crypto fixtures."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
from sqlalchemy import create_engine, func, insert, select, update
from sqlalchemy import event
from sqlalchemy.orm import Session, registry as orm_registry, sessionmaker

from haoai_backend.authentication.tables import metadata as auth_metadata, users
from haoai_backend.generation_catalog.tables import model_configs, system_models
from haoai_backend.generation_contracts.types import BoundCredential, CredentialBinding
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
    check_credential_registry_readiness,
)
from haoai_backend.generation_credentials.persistence import metadata as registry_metadata
from haoai_backend.generation_credentials.persistence import (
    CallerSessionBindingCaptureStore,
    RegistryReadRepository,
)
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    CredentialScope,
    EndpointPolicy,
    HmacProofAuthority,
    ProtectedAccountRecord,
    RegistrationCommitUnknown,
    RegistrationStaged,
    RegistryConflict,
    RegistryRefusal,
    RootEnrollmentSource,
    RotationAttestation,
    SealedMaterial,
    make_server_attempt,
    account_guard_associated_data,
)


NOW = datetime(2026, 10, 12, 4, 0, tzinfo=timezone.utc)
ACTOR_ID = "10000000-0000-4000-8000-000000000001"
MODEL_ID = "20000000-0000-4000-8000-000000000001"
PRINCIPAL_ID = "30000000-0000-4000-8000-000000000001"
REPLACEMENT_PRINCIPAL_ID = "30000000-0000-4000-8000-000000000002"
BINDING_ID = "40000000-0000-4000-8000-000000000001"
CONTENT_PATHS = ("/v1/files/{file_id}",)


@pytest.mark.parametrize("path", ["/v1", "/v2"])
def test_endpoint_policy_preserves_legacy_base_path(path: str) -> None:
    policy = EndpointPolicy.from_base_url(f"https://provider.example.test{path}")
    assert policy.provider_origin == "https://provider.example.test"
    assert policy.api_base_path == path


def test_registration_replay_precedes_current_config_and_enrollment(tmp_path: Path) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    registry = _registry(authority, (record,), crypto, session_factory)
    attempt = _register_attempt(authority, scope, record)

    with session_factory() as session:
        staged = registry.stage_register_owned_config(
            session, _actor(), MODEL_ID, attempt
        )
        assert isinstance(staged, RegistrationStaged)
        assert session.in_transaction()
        unknown = registry.find_confirmed_original_registration(attempt, attempt)
        assert isinstance(unknown, RegistrationCommitUnknown)
        assert session.commit() is None

    with engine.begin() as connection:
        connection.execute(model_configs.delete().where(model_configs.c.id == MODEL_ID))

    replay_registry = build_credential_registry(
        RegistryCapabilities(
            proof_authority=authority,
            db_clock=lambda: NOW,
            read_session_factory=session_factory,
        )
    )
    with session_factory() as session:
        replay = replay_registry.stage_register_owned_config(
            session, _actor(), MODEL_ID, attempt
        )
        assert replay == staged
        session.rollback()
    confirmed = registry.find_confirmed_original_registration(attempt, attempt)
    assert confirmed.__class__.__name__ == "ConfirmedRegistration"
    assert confirmed.attempt_id == attempt.attempt_id
    assert confirmed.binding_id == staged.binding_id
    assert confirmed.binding_version == 1

    changed_command = make_server_attempt(
        authority,
        attempt_id=attempt.attempt_id,
        actor_id=ACTOR_ID,
        operation="register",
        scope=scope,
        key_generation="different-generation",
        protected_record_id="different-record",
    )
    conflict = replay_registry.stage_register_owned_config(
        session_factory(), _actor(), MODEL_ID, changed_command
    )
    assert isinstance(conflict, RegistryConflict)
    assert conflict.code == "attempt_conflict"
    engine.dispose()


def test_stage_keeps_caller_transaction_and_does_not_autoflush(tmp_path: Path) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    service = _registry(authority, (record,), crypto, session_factory)
    attempt = _register_attempt(authority, scope, record)
    mapped = orm_registry()

    class PendingUser:
        pass

    mapped.map_imperatively(PendingUser, users)
    pending = PendingUser(
        id="10000000-0000-4000-8000-000000000098",
        username="pending",
        email="pending@example.test",
        hashed_password="fixture",
        is_superuser=False,
        membership_type="free",
        created_at=datetime(2026, 10, 12),
    )
    with session_factory() as session:
        lifecycle = {"commits": 0, "rollbacks": 0}
        event.listen(session, "after_commit", lambda _: lifecycle.__setitem__("commits", lifecycle["commits"] + 1))
        event.listen(session, "after_rollback", lambda _: lifecycle.__setitem__("rollbacks", lifecycle["rollbacks"] + 1))
        session.add(pending)
        result = service.stage_register_owned_config(session, _actor(), MODEL_ID, attempt)
        assert isinstance(result, RegistrationStaged)
        assert pending in session.new
        assert session.in_transaction()
        with engine.connect() as connection:
            pending_count = connection.execute(
                select(func.count()).select_from(users).where(users.c.id == pending.id)
            ).scalar_one()
            version_count = connection.execute(
                select(func.count()).select_from(service_table_versions())
            ).scalar_one()
        assert pending_count == 0
        assert version_count == 0
        assert lifecycle == {"commits": 0, "rollbacks": 0}
        session.rollback()
        assert lifecycle == {"commits": 0, "rollbacks": 1}
        assert not session.in_transaction()
    engine.dispose()


def test_disable_persists_an_immutable_head_and_stops_old_resolution(tmp_path: Path) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    service = _registry(authority, (record,), crypto, session_factory)
    initial = _register_attempt(authority, scope, record)
    with session_factory() as session:
        registered = service.stage_register_owned_config(session, _actor(), MODEL_ID, initial)
        assert isinstance(registered, RegistrationStaged)
        session.commit()

    disable = make_server_attempt(
        authority,
        attempt_id="disable-attempt-1",
        actor_id=ACTOR_ID,
        operation="disable",
        scope=scope,
        expected_revision=1,
        predecessor_version=1,
    )
    with session_factory() as session:
        staged = service.stage_disable_binding(session, _actor(), scope, 1, disable)
        assert isinstance(staged, RegistrationStaged)
        assert staged.binding_version == 2
        session.commit()

    with session_factory() as session:
        from haoai_backend.generation_credentials.persistence import credential_bindings, credential_versions

        binding = session.execute(
            select(credential_bindings).where(credential_bindings.c.binding_id == registered.binding_id)
        ).mappings().one()
        disabled = session.execute(
            select(credential_versions).where(
                credential_versions.c.binding_id == registered.binding_id,
                credential_versions.c.binding_version == 2,
            )
        ).mappings().one()
        assert binding["enabled"] is False
        assert binding["current_version"] == 2
        assert disabled["version_kind"] == "disabled"
        assert disabled["ciphertext"] is None
        assert disabled["server_attempt_id"] == disable.attempt_id

    old_binding = CredentialBinding(
        source="user",
        row_id=MODEL_ID,
        owner_or_system=ACTOR_ID,
        provider="openai",
        original_principal_id=PRINCIPAL_ID,
        endpoint_origin="https://provider.example.test",
        allowed_content_paths=CONTENT_PATHS,
        binding_version="1",
    )
    assert service.resolve_original_credential(old_binding, "query") is None
    engine.dispose()


def test_committed_ciphertext_corruption_refuses_resolution(tmp_path: Path) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    service = _registry(authority, (record,), crypto, session_factory)
    attempt = _register_attempt(authority, scope, record)
    with session_factory() as session:
        staged = service.stage_register_owned_config(session, _actor(), MODEL_ID, attempt)
        assert isinstance(staged, RegistrationStaged)
        session.commit()

    with engine.begin() as connection:
        connection.execute(
            update(service_table_versions())
            .where(service_table_versions().c.binding_id == staged.binding_id)
            .values(ciphertext=b"tampered")
        )
    binding = CredentialBinding(
        source="user",
        row_id=MODEL_ID,
        owner_or_system=ACTOR_ID,
        provider="openai",
        original_principal_id=PRINCIPAL_ID,
        endpoint_origin="https://provider.example.test",
        allowed_content_paths=CONTENT_PATHS,
        binding_version="1",
    )
    assert service.resolve_original_credential(binding, "query") is None
    engine.dispose()


def test_exact_user_scope_refuses_foreign_model_row(tmp_path: Path) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(
        tmp_path, config_owner="10000000-0000-4000-8000-000000000099"
    )
    service = _registry(authority, (record,), crypto, session_factory)
    attempt = _register_attempt(authority, scope, record)
    with session_factory() as session:
        result = service.stage_register_owned_config(session, _actor(), MODEL_ID, attempt)
        assert isinstance(result, RegistryRefusal)
        assert result.code == "config_unavailable"
        session.rollback()
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(service_table_versions())).scalar_one() == 0
    engine.dispose()


def test_same_principal_rotation_requires_separate_attestation(tmp_path: Path) -> None:
    engine, session_factory, scope, authority, crypto, original = _registered_fixture(tmp_path)
    service = _registry(authority, (original,), crypto, session_factory)
    initial = _register_attempt(authority, scope, original)
    with session_factory() as session:
        staged = service.stage_register_owned_config(session, _actor(), MODEL_ID, initial)
        assert isinstance(staged, RegistrationStaged)
        session.commit()

    rotated = _signed_record(
        authority,
        crypto,
        scope,
        record_id="root-record-rotation",
        principal_id=PRINCIPAL_ID,
        subject="account-subject-1",
        key_generation="generation-2",
        material_ref="vault://provider/key-2",
        secret="new-provider-secret",
        base_url="https://provider.example.test/v2",
    )
    with engine.begin() as connection:
        connection.execute(
            update(model_configs)
            .where(model_configs.c.id == MODEL_ID)
            .values(api_key="new-provider-secret")
        )
    missing_proof_registry = _registry(authority, (original, rotated), crypto, session_factory)
    missing_proof = make_server_attempt(
        authority,
        attempt_id="rotation-without-proof",
        actor_id=ACTOR_ID,
        operation="rotate",
        scope=scope,
        key_generation=rotated.key_generation,
        expected_revision=1,
        predecessor_version=1,
        protected_record_id=rotated.record_id,
    )
    with session_factory() as session:
        refused = missing_proof_registry.stage_rotate_or_replace_config(
            session, _actor(), scope, 1, missing_proof
        )
        assert isinstance(refused, RegistryRefusal)
        assert refused.code == "rotation_proof_unavailable"
        session.rollback()

    attestation = RotationAttestation(
        proof_id="rotation-proof-1",
        original_record_id=original.record_id,
        replacement_record_id=rotated.record_id,
        binding_id=staged.binding_id,
        predecessor_version=1,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    attestation = replace(
        attestation,
        proof_signature=authority.sign("root-rotation-v1", attestation.proof_payload()),
    )
    service = _registry(
        authority,
        (original, rotated),
        crypto,
        session_factory,
        rotations=(attestation,),
    )
    attempt = make_server_attempt(
        authority,
        attempt_id="rotation-with-proof",
        actor_id=ACTOR_ID,
        operation="rotate",
        scope=scope,
        key_generation=rotated.key_generation,
        expected_revision=1,
        predecessor_version=1,
        protected_record_id=rotated.record_id,
        rotation_proof_ref=attestation.proof_id,
    )
    with session_factory() as session:
        result = service.stage_rotate_or_replace_config(session, _actor(), scope, 1, attempt)
        assert isinstance(result, RegistrationStaged)
        assert result.binding_version == 2
        session.commit()

    b1_binding = CredentialBinding(
        source="user",
        row_id=MODEL_ID,
        owner_or_system=ACTOR_ID,
        provider="openai",
        original_principal_id=PRINCIPAL_ID,
        endpoint_origin="https://provider.example.test",
        allowed_content_paths=CONTENT_PATHS,
        binding_version="1",
    )
    resolved = service.resolve_original_credential(b1_binding, "query")
    assert isinstance(resolved, BoundCredential)
    assert resolved.principal_id == PRINCIPAL_ID
    assert resolved.secret == "new-provider-secret"
    assert "new-provider-secret" not in repr(resolved)

    rotated_binding = CredentialBinding(
        source="user",
        row_id=MODEL_ID,
        owner_or_system=ACTOR_ID,
        provider="openai",
        original_principal_id=PRINCIPAL_ID,
        endpoint_origin="https://provider.example.test",
        allowed_content_paths=CONTENT_PATHS,
        binding_version="2",
        rotation_provenance=attestation.proof_id,
    )
    rotated_result = service.resolve_original_credential(rotated_binding, "query")
    assert isinstance(rotated_result, BoundCredential)
    assert rotated_result.principal_id == PRINCIPAL_ID
    assert rotated_result.secret == "new-provider-secret"

    wrong_version = replace(rotated_binding, binding_version="02")
    wrong_provenance = replace(rotated_binding, rotation_provenance="other-proof")
    assert service.resolve_original_credential(wrong_version, "query") is None
    assert service.resolve_original_credential(wrong_provenance, "query") is None
    engine.dispose()


def test_current_replacement_ignores_expired_pre_origin_rotation_but_keeps_later_proof(
    tmp_path: Path,
) -> None:
    engine, session_factory, scope, authority, crypto, original = _registered_fixture(tmp_path)
    rotated = _signed_record(
        authority,
        crypto,
        scope,
        record_id="root-record-history-rotation",
        principal_id=PRINCIPAL_ID,
        subject="account-subject-1",
        key_generation="history-generation-2",
        material_ref="vault://provider/key-2",
        secret="new-provider-secret",
        base_url="https://provider.example.test/v2",
    )
    rotation_one = RotationAttestation(
        proof_id="history-rotation-proof-1",
        original_record_id=original.record_id,
        replacement_record_id=rotated.record_id,
        binding_id=BINDING_ID,
        predecessor_version=1,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    rotation_one = replace(
        rotation_one,
        proof_signature=authority.sign("root-rotation-v1", rotation_one.proof_payload()),
    )
    replacement = _signed_record(
        authority,
        crypto,
        scope,
        record_id="root-record-history-replacement",
        principal_id=REPLACEMENT_PRINCIPAL_ID,
        subject="account-subject-2",
        key_generation="history-generation-3",
        material_ref="vault://provider/replacement-key",
        secret="replacement-secret",
        base_url="https://provider.example.test/v2",
    )
    initial_service = _registry(
        authority, (original, rotated, replacement), crypto, session_factory, rotations=(rotation_one,)
    )
    with session_factory() as session:
        registered = initial_service.stage_register_owned_config(
            session, _actor(), MODEL_ID, _register_attempt(authority, scope, original)
        )
        assert isinstance(registered, RegistrationStaged)
        session.commit()

    with engine.begin() as connection:
        connection.execute(
            update(model_configs).where(model_configs.c.id == MODEL_ID).values(api_key="new-provider-secret")
        )
    rotation_attempt = make_server_attempt(
        authority,
        attempt_id="history-rotation-attempt-2",
        actor_id=ACTOR_ID,
        operation="rotate",
        scope=scope,
        key_generation=rotated.key_generation,
        expected_revision=1,
        predecessor_version=1,
        protected_record_id=rotated.record_id,
        rotation_proof_ref=rotation_one.proof_id,
    )
    with session_factory() as session:
        result = initial_service.stage_rotate_or_replace_config(
            session, _actor(), scope, 1, rotation_attempt
        )
        assert isinstance(result, RegistrationStaged)
        assert result.binding_version == 2
        session.commit()

    with engine.begin() as connection:
        connection.execute(
            update(model_configs).where(model_configs.c.id == MODEL_ID).values(api_key="replacement-secret")
        )
    replacement_attempt = make_server_attempt(
        authority,
        attempt_id="history-replacement-attempt-3",
        actor_id=ACTOR_ID,
        operation="replace",
        scope=scope,
        key_generation=replacement.key_generation,
        expected_revision=2,
        predecessor_version=2,
        protected_record_id=replacement.record_id,
    )
    with session_factory() as session:
        result = initial_service.stage_rotate_or_replace_config(
            session, _actor(), scope, 2, replacement_attempt
        )
        assert isinstance(result, RegistrationStaged)
        assert result.binding_version == 3
        session.commit()

    expired_rotation_unsigned = replace(
        rotated,
        valid_until=NOW - timedelta(seconds=1),
        proof_signature="0" * 64,
    )
    expired_rotation = replace(
        expired_rotation_unsigned,
        proof_signature=authority.sign("root-enrollment-v1", expired_rotation_unsigned.proof_payload()),
    )
    current_service = _registry(
        authority,
        (original, expired_rotation, replacement),
        crypto,
        session_factory,
        rotations=(rotation_one,),
    )
    current_binding = CredentialBinding(
        source="user",
        row_id=MODEL_ID,
        owner_or_system=ACTOR_ID,
        provider="openai",
        original_principal_id=REPLACEMENT_PRINCIPAL_ID,
        endpoint_origin="https://provider.example.test",
        allowed_content_paths=CONTENT_PATHS,
        binding_version="3",
    )

    resolved_current = current_service.resolve_original_credential(current_binding, "query")
    assert isinstance(resolved_current, BoundCredential)
    assert resolved_current.principal_id == REPLACEMENT_PRINCIPAL_ID
    assert resolved_current.secret == "replacement-secret"

    old_account_binding = replace(
        current_binding,
        original_principal_id=PRINCIPAL_ID,
        binding_version="2",
        rotation_provenance=rotation_one.proof_id,
    )
    assert current_service.resolve_original_credential(old_account_binding, "query") is None
    assert current_service.resolve_original_credential(
        replace(current_binding, binding_version="03"), "query"
    ) is None
    assert current_service.resolve_original_credential(
        replace(current_binding, rotation_provenance=rotation_one.proof_id), "query"
    ) is None
    assert current_service.resolve_original_credential(
        replace(current_binding, allowed_content_paths=("/v1/other/{file_id}",)), "query"
    ) is None

    post_origin = _signed_record(
        authority,
        crypto,
        scope,
        record_id="root-record-post-origin-rotation",
        principal_id=REPLACEMENT_PRINCIPAL_ID,
        subject="account-subject-2",
        key_generation="history-generation-4",
        material_ref="vault://provider/key-2",
        secret="new-provider-secret",
        base_url="https://provider.example.test/v2",
    )
    rotation_after_origin = RotationAttestation(
        proof_id="history-rotation-proof-after-origin",
        original_record_id=replacement.record_id,
        replacement_record_id=post_origin.record_id,
        binding_id=BINDING_ID,
        predecessor_version=3,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    rotation_after_origin = replace(
        rotation_after_origin,
        proof_signature=authority.sign(
            "root-rotation-v1", rotation_after_origin.proof_payload()
        ),
    )
    with engine.begin() as connection:
        connection.execute(
            update(model_configs).where(model_configs.c.id == MODEL_ID).values(api_key="new-provider-secret")
        )
    post_origin_service = _registry(
        authority,
        (original, rotated, replacement, post_origin),
        crypto,
        session_factory,
        rotations=(rotation_one, rotation_after_origin),
    )
    post_origin_attempt = make_server_attempt(
        authority,
        attempt_id="history-post-origin-rotation-attempt-4",
        actor_id=ACTOR_ID,
        operation="rotate",
        scope=scope,
        key_generation=post_origin.key_generation,
        expected_revision=3,
        predecessor_version=3,
        protected_record_id=post_origin.record_id,
        rotation_proof_ref=rotation_after_origin.proof_id,
    )
    with session_factory() as session:
        result = post_origin_service.stage_rotate_or_replace_config(
            session, _actor(), scope, 3, post_origin_attempt
        )
        assert isinstance(result, RegistrationStaged)
        assert result.binding_version == 4
        session.commit()

    final_records = (original, expired_rotation, replacement, post_origin)
    final_service = _registry(
        authority,
        final_records,
        crypto,
        session_factory,
        rotations=(rotation_one, rotation_after_origin),
    )
    resolved_with_required_post_origin_proof = final_service.resolve_original_credential(
        current_binding, "query"
    )
    assert isinstance(resolved_with_required_post_origin_proof, BoundCredential)
    assert resolved_with_required_post_origin_proof.secret == "new-provider-secret"
    missing_post_origin_proof_service = _registry(
        authority,
        final_records,
        crypto,
        session_factory,
        rotations=(rotation_one,),
    )
    assert missing_post_origin_proof_service.resolve_original_credential(current_binding, "query") is None
    engine.dispose()


def test_replacement_does_not_resolve_old_original_binding(tmp_path: Path) -> None:
    engine, session_factory, scope, authority, crypto, original = _registered_fixture(tmp_path)
    service = _registry(authority, (original,), crypto, session_factory)
    initial = _register_attempt(authority, scope, original)
    with session_factory() as session:
        staged = service.stage_register_owned_config(session, _actor(), MODEL_ID, initial)
        assert isinstance(staged, RegistrationStaged)
        session.commit()

    replacement = _signed_record(
        authority,
        crypto,
        scope,
        record_id="root-record-replacement",
        principal_id=REPLACEMENT_PRINCIPAL_ID,
        subject="account-subject-2",
        key_generation="replacement-generation",
        material_ref="vault://provider/replacement-key",
        secret="replacement-secret",
        base_url="https://provider.example.test/v2",
    )
    with engine.begin() as connection:
        connection.execute(
            update(model_configs)
            .where(model_configs.c.id == MODEL_ID)
            .values(api_key="replacement-secret")
        )
    service = _registry(authority, (original, replacement), crypto, session_factory)
    attempt = make_server_attempt(
        authority,
        attempt_id="replacement-attempt",
        actor_id=ACTOR_ID,
        operation="replace",
        scope=scope,
        key_generation=replacement.key_generation,
        expected_revision=1,
        predecessor_version=1,
        protected_record_id=replacement.record_id,
    )
    with session_factory() as session:
        result = service.stage_rotate_or_replace_config(session, _actor(), scope, 1, attempt)
        assert isinstance(result, RegistrationStaged)
        session.commit()

    old_binding = CredentialBinding(
        source="user",
        row_id=MODEL_ID,
        owner_or_system=ACTOR_ID,
        provider="openai",
        original_principal_id=PRINCIPAL_ID,
        endpoint_origin="https://provider.example.test",
        allowed_content_paths=CONTENT_PATHS,
        binding_version="1",
    )
    assert service.resolve_original_credential(old_binding, "query") is None

    replacement_binding = CredentialBinding(
        source="user",
        row_id=MODEL_ID,
        owner_or_system=ACTOR_ID,
        provider="openai",
        original_principal_id=REPLACEMENT_PRINCIPAL_ID,
        endpoint_origin="https://provider.example.test",
        allowed_content_paths=CONTENT_PATHS,
        binding_version="2",
    )
    resolved_replacement = service.resolve_original_credential(replacement_binding, "query")
    assert isinstance(resolved_replacement, BoundCredential)
    assert resolved_replacement.principal_id == REPLACEMENT_PRINCIPAL_ID
    assert resolved_replacement.secret == "replacement-secret"
    engine.dispose()


def test_resolution_recheck_keeps_config_before_binding_lock_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    service = _registry(authority, (record,), crypto, session_factory)
    with session_factory() as session:
        staged = service.stage_register_owned_config(
            session, _actor(), MODEL_ID, _register_attempt(authority, scope, record)
        )
        assert isinstance(staged, RegistrationStaged)
        session.commit()

    order: list[tuple[int, str]] = []
    original_lock_config = RegistryReadRepository.lock_config
    original_lock_binding = RegistryReadRepository.lock_binding

    def lock_config(self, *args, **kwargs):
        order.append((id(self._session), "config"))
        return original_lock_config(self, *args, **kwargs)

    def lock_binding(self, *args, **kwargs):
        order.append((id(self._session), "binding"))
        return original_lock_binding(self, *args, **kwargs)

    monkeypatch.setattr(RegistryReadRepository, "lock_config", lock_config)
    monkeypatch.setattr(RegistryReadRepository, "lock_binding", lock_binding)
    binding = CredentialBinding(
        source="user",
        row_id=MODEL_ID,
        owner_or_system=ACTOR_ID,
        provider="openai",
        original_principal_id=PRINCIPAL_ID,
        endpoint_origin="https://provider.example.test",
        allowed_content_paths=CONTENT_PATHS,
        binding_version="1",
    )

    resolved = service.resolve_original_credential(binding, "query")

    assert isinstance(resolved, BoundCredential)
    assert [kind for _, kind in order] == ["config", "binding", "config", "binding"]
    assert order[0][0] != order[2][0]
    engine.dispose()


@pytest.mark.parametrize("failure", ["unseal", "guard", "legacy"])
def test_resolution_crypto_failures_are_unavailable_and_close_owned_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    service = _registry(authority, (record,), crypto, session_factory)
    with session_factory() as session:
        staged = service.stage_register_owned_config(
            session, _actor(), MODEL_ID, _register_attempt(authority, scope, record)
        )
        assert isinstance(staged, RegistrationStaged)
        session.commit()

    synthetic_secret = "fixture-secret-must-not-escape"

    def fail(*_args, **_kwargs):
        raise ValueError(synthetic_secret)

    overrides = {
        "unseal": {"unseal": fail},
        "guard": {"consistency_guard": fail},
        "legacy": {"resolve_legacy_config_value": fail},
    }[failure]
    failing_crypto = replace(crypto, **overrides)
    opened_sessions: list[Session] = []
    closed_sessions: set[int] = set()
    original_close = Session.close

    def tracked_factory() -> Session:
        session = session_factory()
        opened_sessions.append(session)
        return session

    def tracked_close(session: Session) -> None:
        closed_sessions.add(id(session))
        original_close(session)

    monkeypatch.setattr(Session, "close", tracked_close)
    resolving_service = _registry(authority, (record,), failing_crypto, tracked_factory)
    binding = CredentialBinding(
        source="user",
        row_id=MODEL_ID,
        owner_or_system=ACTOR_ID,
        provider="openai",
        original_principal_id=PRINCIPAL_ID,
        endpoint_origin="https://provider.example.test",
        allowed_content_paths=CONTENT_PATHS,
        binding_version="1",
    )

    resolved = resolving_service.resolve_original_credential(binding, "query")

    assert resolved is None
    assert opened_sessions
    assert {id(session) for session in opened_sessions} <= closed_sessions
    captured = capsys.readouterr()
    assert synthetic_secret not in captured.out + captured.err
    engine.dispose()


@pytest.mark.parametrize("source", ["user", "system"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_revision", 1),
        ("predecessor_version", 1),
        ("rotation_proof_ref", "unexpected-rotation"),
    ],
)
def test_signed_register_attempt_rejects_mutation_fields_before_effects(
    tmp_path: Path,
    source: str,
    field: str,
    value: object,
) -> None:
    engine, session_factory, user_scope, authority, crypto, record = _registered_fixture(tmp_path)
    scope = user_scope if source == "user" else CredentialScope("system", "system-model", "system", "openai")
    service = _registry(authority, (record,), crypto, session_factory)
    attempt = make_server_attempt(
        authority,
        attempt_id=f"contradictory-{source}-{field}",
        actor_id=ACTOR_ID,
        operation="register",
        scope=scope,
        key_generation=record.key_generation,
        protected_record_id=record.record_id,
        **{field: value},
    )

    with session_factory() as session:
        if source == "user":
            result = service.stage_register_owned_config(session, _actor(), MODEL_ID, attempt)
        else:
            result = service.stage_register_system_config(session, _actor(), scope.row_id, attempt)
        assert isinstance(result, RegistryRefusal)
        assert result.code == "scope_mismatch"
        assert session.execute(select(func.count()).select_from(service_table_versions())).scalar_one() == 0
        session.rollback()
    engine.dispose()


@pytest.mark.parametrize(
    "clock",
    [
        None,
        lambda: datetime(2026, 10, 12, 4, 0),
        lambda: "not-a-datetime",
        lambda: (_ for _ in ()).throw(RuntimeError("fixture clock failure")),
    ],
    ids=["missing", "naive", "wrong-type", "raises"],
)
def test_invalid_clock_refuses_registration_and_marks_readiness_not_ready(
    tmp_path: Path,
    clock: Callable[[], object] | None,
) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    capabilities = _capabilities_with_clock(authority, (record,), crypto, session_factory, clock)
    service = build_credential_registry(capabilities)
    attempt = _register_attempt(authority, scope, record)

    with session_factory() as session:
        result = service.stage_register_owned_config(session, _actor(), MODEL_ID, attempt)
        assert isinstance(result, RegistryRefusal)
        assert result.code == "clock_unavailable"
        assert session.execute(select(func.count()).select_from(service_table_versions())).scalar_one() == 0
        session.rollback()
        readiness = check_credential_registry_readiness(session, capabilities)
        assert readiness.clock_status == "not_ready"
    engine.dispose()


def test_durable_attempt_replay_precedes_a_failing_clock(tmp_path: Path) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    attempt = _register_attempt(authority, scope, record)
    original = _registry(authority, (record,), crypto, session_factory)
    with session_factory() as session:
        staged = original.stage_register_owned_config(session, _actor(), MODEL_ID, attempt)
        assert isinstance(staged, RegistrationStaged)
        session.commit()

    calls: list[bool] = []

    def unavailable_clock():
        calls.append(True)
        raise RuntimeError("fixture clock unavailable")

    capabilities = _capabilities_with_clock(
        authority, (record,), crypto, session_factory, unavailable_clock
    )
    service = build_credential_registry(capabilities)
    with session_factory() as session:
        replay = service.stage_register_owned_config(session, _actor(), MODEL_ID, attempt)
        assert isinstance(replay, RegistrationStaged)
        assert replay.attempt_id == attempt.attempt_id
        assert calls == []
        session.rollback()
    engine.dispose()


def test_invalid_clock_refuses_rotation_and_disable_before_writes(tmp_path: Path) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    original_service = _registry(authority, (record,), crypto, session_factory)
    with session_factory() as session:
        staged = original_service.stage_register_owned_config(
            session, _actor(), MODEL_ID, _register_attempt(authority, scope, record)
        )
        assert isinstance(staged, RegistrationStaged)
        session.commit()

    invalid_service = build_credential_registry(
        _capabilities_with_clock(
            authority,
            (record,),
            crypto,
            session_factory,
            lambda: datetime(2026, 10, 12, 4, 0),
        )
    )
    rotation = make_server_attempt(
        authority,
        attempt_id="invalid-clock-rotation",
        actor_id=ACTOR_ID,
        operation="rotate",
        scope=scope,
        key_generation=record.key_generation,
        expected_revision=1,
        predecessor_version=1,
        protected_record_id=record.record_id,
        rotation_proof_ref="unused-rotation-proof",
    )
    disable = make_server_attempt(
        authority,
        attempt_id="invalid-clock-disable",
        actor_id=ACTOR_ID,
        operation="disable",
        scope=scope,
        expected_revision=1,
        predecessor_version=1,
    )

    with session_factory() as session:
        rotation_result = invalid_service.stage_rotate_or_replace_config(
            session, _actor(), scope, 1, rotation
        )
        try:
            disable_result = invalid_service.stage_disable_binding(
                session, _actor(), scope, 1, disable
            )
        except Exception as exc:
            disable_result = exc
        assert isinstance(rotation_result, RegistryRefusal)
        assert rotation_result.code == "clock_unavailable"
        assert isinstance(disable_result, RegistryRefusal)
        assert disable_result.code == "clock_unavailable"
        assert session.execute(select(func.count()).select_from(service_table_versions())).scalar_one() == 1
        session.rollback()
    engine.dispose()


def _capabilities_with_clock(
    authority: HmacProofAuthority,
    records: tuple[ProtectedAccountRecord, ...],
    crypto: CredentialCrypto,
    session_factory: Callable[[], Session],
    clock: Callable[[], object] | None,
) -> RegistryCapabilities:
    return RegistryCapabilities(
        proof_authority=authority,
        enrollment_source=RootEnrollmentSource(records, authority),
        crypto=crypto,
        read_session_factory=session_factory,
        db_clock=clock,  # type: ignore[arg-type]
        id_factory=lambda: BINDING_ID,
    )


def _registered_fixture(
    tmp_path: Path,
    *,
    config_owner: str = ACTOR_ID,
) -> tuple[object, Callable[[], Session], CredentialScope, HmacProofAuthority, CredentialCrypto, ProtectedAccountRecord]:
    path = tmp_path / "credential-registry.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    auth_metadata.create_all(engine, tables=[users])
    model_configs.create(engine)
    system_models.create(engine)
    registry_metadata.create_all(engine)
    SessionFactory = sessionmaker(engine, expire_on_commit=False, autoflush=True)
    with engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id=ACTOR_ID,
                username="registry-owner",
                email="registry-owner@example.test",
                hashed_password="fixture-only",
                is_superuser=True,
                membership_type="free",
                created_at=datetime(2026, 10, 12),
            )
        )
        connection.execute(
            insert(users).values(
                id="10000000-0000-4000-8000-000000000099",
                username="foreign-owner",
                email="foreign-owner@example.test",
                hashed_password="fixture-only",
                is_superuser=False,
                membership_type="free",
                created_at=datetime(2026, 10, 12),
            )
        )
        connection.execute(
            insert(model_configs).values(
                id=MODEL_ID,
                user_id=config_owner,
                category="image",
                name="fixture-model",
                provider="openai",
                api_key="provider-secret",
                model="fixture-model-v1",
                base_url="https://provider.example.test/v2",
                sort_order=1,
                params="{}",
                created_at=datetime(2026, 10, 12),
                updated_at=datetime(2026, 10, 12),
            )
        )
    scope = CredentialScope("user", MODEL_ID, ACTOR_ID, "openai")
    authority = HmacProofAuthority("root-test-key", b"p" * 32)
    crypto = _test_crypto(
        {
            "vault://provider/key-1": "provider-secret",
            "vault://provider/key-2": "new-provider-secret",
            "vault://provider/replacement-key": "replacement-secret",
        }
    )
    record = _signed_record(
        authority,
        crypto,
        scope,
        record_id="root-record-initial",
        principal_id=PRINCIPAL_ID,
        subject="account-subject-1",
        key_generation="generation-1",
        material_ref="vault://provider/key-1",
        secret="provider-secret",
        base_url="https://provider.example.test/v2",
    )
    return engine, SessionFactory, scope, authority, crypto, record


def _signed_record(
    authority: HmacProofAuthority,
    crypto: CredentialCrypto,
    scope: CredentialScope,
    *,
    record_id: str,
    principal_id: str,
    subject: str,
    key_generation: str,
    material_ref: str,
    secret: str,
    base_url: str,
) -> ProtectedAccountRecord:
    unsigned = ProtectedAccountRecord(
        record_id=record_id,
        principal_id=principal_id,
        scope=scope,
        issuer_namespace="provider-account-v1",
        stable_account_subject=subject,
        key_generation=key_generation,
        material_ref=material_ref,
        evidence_ref=f"evidence://{record_id}",
        evidence_version="fixture-evidence-v1",
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=365),
        revoked_at=None,
        endpoint_policy=EndpointPolicy.from_base_url(
            base_url,
            allowed_content_paths=CONTENT_PATHS,
            policy_version="endpoint-policy-v1",
        ),
        guard_key_id=crypto.guard_key_id,
        secret_consistency_guard="0" * 64,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    with_guard = replace(
        unsigned,
        secret_consistency_guard=crypto.guard(secret, account_guard_associated_data(unsigned)),
    )
    return replace(
        with_guard,
        proof_signature=authority.sign("root-enrollment-v1", with_guard.proof_payload()),
    )


def _test_crypto(secrets: dict[str, str]) -> CredentialCrypto:
    encryption_key = b"local-fixture-encryption-key"
    guard_key = b"local-fixture-consistency-key"
    state = {"unseal_calls": 0}

    def stream(nonce: bytes, associated_data: bytes, size: int) -> bytes:
        output = bytearray()
        counter = 0
        while len(output) < size:
            output.extend(
                hashlib.sha256(encryption_key + nonce + associated_data + counter.to_bytes(4, "big")).digest()
            )
            counter += 1
        return bytes(output[:size])

    def seal(secret: str, associated_data: bytes) -> SealedMaterial:
        nonce = hashlib.sha256(associated_data + secret.encode()).digest()[:12]
        plaintext = secret.encode()
        key_stream = stream(nonce, associated_data, len(plaintext))
        encrypted = bytes(left ^ right for left, right in zip(plaintext, key_stream))
        tag = hmac.new(encryption_key, b"tag" + nonce + associated_data + encrypted, hashlib.sha256).digest()
        return SealedMaterial(tag + encrypted, nonce, "seal-test-v1", "test-aead-v1")

    def unseal(material: SealedMaterial, associated_data: bytes) -> str | None:
        state["unseal_calls"] += 1
        if material.key_id != "seal-test-v1" or len(material.ciphertext) < 32:
            return None
        tag, encrypted = material.ciphertext[:32], material.ciphertext[32:]
        expected = hmac.new(
            encryption_key,
            b"tag" + material.nonce + associated_data + encrypted,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(tag, expected):
            return None
        key_stream = stream(material.nonce, associated_data, len(encrypted))
        return bytes(left ^ right for left, right in zip(encrypted, key_stream)).decode()

    def guard(secret: str, associated_data: bytes) -> str:
        return hmac.new(guard_key, associated_data + b"\0" + secret.encode(), hashlib.sha256).hexdigest()

    return CredentialCrypto(
        seal_key_id="seal-test-v1",
        guard_key_id="guard-test-v1",
        algorithm_version="test-aead-v1",
        seal=seal,
        unseal=unseal,
        consistency_guard=guard,
        resolve_secret_handle=lambda reference: secrets.get(reference),
        resolve_legacy_config_value=lambda stored: stored or None,
        self_test=lambda: True,
    )


def _registry(
    authority: HmacProofAuthority,
    records: tuple[ProtectedAccountRecord, ...],
    crypto: CredentialCrypto,
    session_factory: Callable[[], Session],
    *,
    rotations: tuple[RotationAttestation, ...] = (),
):
    source = RootEnrollmentSource(records, authority, rotations)
    return build_credential_registry(
        RegistryCapabilities(
            proof_authority=authority,
            enrollment_source=source,
            crypto=crypto,
            read_session_factory=session_factory,
            db_clock=lambda: NOW,
            id_factory=lambda: BINDING_ID,
        )
    )


def _register_attempt(
    authority: HmacProofAuthority,
    scope: CredentialScope,
    record: ProtectedAccountRecord,
):
    return make_server_attempt(
        authority,
        attempt_id="registration-attempt-1",
        actor_id=ACTOR_ID,
        operation="register",
        scope=scope,
        key_generation=record.key_generation,
        protected_record_id=record.record_id,
    )


def _actor():
    from haoai_backend.shared.identity import TrustedActor

    return TrustedActor(ACTOR_ID)


def service_table_versions():
    from haoai_backend.generation_credentials.persistence import credential_versions

    return credential_versions


def test_binding_capture_adapter_keeps_caller_session_and_uses_secret_free_projections(
    tmp_path: Path,
) -> None:
    engine, session_factory, scope, authority, crypto, record = _registered_fixture(tmp_path)
    registry = _registry(authority, (record,), crypto, session_factory)
    attempt = _register_attempt(authority, scope, record)
    with session_factory() as session:
        staged = registry.stage_register_owned_config(
            session,
            _actor(),
            MODEL_ID,
            attempt,
        )
        assert isinstance(staged, RegistrationStaged)
        session.commit()

    mapped = orm_registry()

    class PendingUser:
        pass

    mapped.map_imperatively(PendingUser, users)
    pending = PendingUser(
        id="10000000-0000-4000-8000-000000000097",
        username="pending-capture",
        email="pending-capture@example.test",
        hashed_password="fixture",
        is_superuser=False,
        membership_type="free",
        created_at=datetime(2026, 10, 12),
    )
    statements: list[str] = []
    events = {"flushes": 0, "commits": 0, "rollbacks": 0}

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    try:
        with session_factory() as session:
            transaction = session.begin()
            transaction_identity = session.get_transaction()
            session.add(pending)
            event.listen(
                session,
                "before_flush",
                lambda *_: events.__setitem__("flushes", events["flushes"] + 1),
            )
            event.listen(
                session,
                "after_commit",
                lambda *_: events.__setitem__("commits", events["commits"] + 1),
            )
            event.listen(
                session,
                "after_rollback",
                lambda *_: events.__setitem__("rollbacks", events["rollbacks"] + 1),
            )
            event.listen(engine, "before_cursor_execute", record_statement)
            adapter = CallerSessionBindingCaptureStore(session)

            def read_capture_rows():
                assert session.autoflush is False
                return (
                    adapter.lock_config_identity(scope),
                    adapter.lock_binding_for_capture(scope),
                    adapter.read_capture_version(BINDING_ID, 1),
                    adapter.read_capture_versions(BINDING_ID),
                    adapter.find_capture_principal(PRINCIPAL_ID),
                )

            config, binding, head, versions, principal = adapter.without_autoflush(
                read_capture_rows
            )
            event.remove(engine, "before_cursor_execute", record_statement)

            assert config is not None
            assert binding is not None
            assert head is not None
            assert versions and principal is not None
            assert session.autoflush is True
            assert pending in session.new
            assert session.get_transaction() is transaction_identity
            assert session.in_transaction()
            assert events == {"flushes": 0, "commits": 0, "rollbacks": 0}
            assert "api_key" not in " ".join(statements)
            assert "ciphertext" not in " ".join(statements)
            assert "nonce" not in " ".join(statements)
            assert "record_id" not in statements[-1]
            assert "ciphertext" not in head and "nonce" not in head
            assert "ciphertext" not in versions[0] and "nonce" not in versions[0]
            assert "record_id" not in principal
            assert "api_key" not in {item.name for item in fields(config)}

            session.rollback()
            assert events == {"flushes": 0, "commits": 0, "rollbacks": 1}
    finally:
        engine.dispose()
