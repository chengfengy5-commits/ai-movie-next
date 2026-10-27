"""SQLAlchemy-free registry rules exercised against explicit fake stores."""

from __future__ import annotations

import builtins
import hashlib
import hmac
import importlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from haoai_backend.generation_contracts.types import CredentialBinding
from haoai_backend.generation_credentials import registry_application
from haoai_backend.generation_credentials.domain import (
    CredentialScope,
    EndpointPolicy,
    HmacProofAuthority,
    ProtectedAccountRecord,
    RegistrationStaged,
    RotationAttestation,
    RootEnrollmentSource,
    SealedMaterial,
    account_guard_associated_data,
    make_server_attempt,
)
from haoai_backend.generation_credentials.registry_application import (
    RegistryApplication,
)
from haoai_backend.generation_credentials.registry_ports import LegacyConfigFacts, RegistryStoreError
from haoai_backend.shared.identity import TrustedActor


NOW = datetime(2026, 10, 12, 4, 0, tzinfo=timezone.utc)
ACTOR_ID = "10000000-0000-4000-8000-000000000001"
MODEL_ID = "20000000-0000-4000-8000-000000000001"
PRINCIPAL_ID = "30000000-0000-4000-8000-000000000001"
REPLACEMENT_PRINCIPAL_ID = "30000000-0000-4000-8000-000000000002"
BINDING_ID = "40000000-0000-4000-8000-000000000001"


class FakeCrypto:
    guard_key_id = "guard-test-v1"

    def __init__(self, secrets: dict[str, str], events: list[str]) -> None:
        self.secrets = secrets
        self.events = events

    def ready(self) -> bool:
        return True

    def resolve_material(self, reference: str) -> str | None:
        return self.secrets.get(reference)

    @staticmethod
    def resolve_legacy_value(value: str) -> str | None:
        return value or None

    @staticmethod
    def guard(secret: str, associated_data: bytes) -> str:
        return hmac.new(b"g" * 32, associated_data + b"\0" + secret.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def seal_secret(secret: str, associated_data: bytes) -> SealedMaterial:
        return SealedMaterial(secret.encode(), associated_data[:12] or b"nonce", "seal-test-v1", "test-aead-v1")

    def open_secret(self, material: SealedMaterial, associated_data: bytes) -> str | None:
        self.events.append("crypto.open")
        if material.nonce != associated_data[:12] or material.key_id != "seal-test-v1":
            return None
        return material.ciphertext.decode()


class MemoryDatabase:
    def __init__(self, scope: CredentialScope, config: LegacyConfigFacts) -> None:
        self.scope = scope
        self.config = config
        self.users = {ACTOR_ID: {"is_superuser": True}}
        self.principals: dict[tuple[str, str, str, str], dict[str, object]] = {}
        self.bindings: dict[tuple[str, str, str, str], dict[str, object]] = {}
        self.versions: dict[str, list[dict[str, object]]] = {}
        self.attempts: dict[str, dict[str, object]] = {}
        self.events: list[str] = []
        self.read_scope_number = 0


class MemoryStore:
    def __init__(self, database: MemoryDatabase, label: str) -> None:
        self.database = database
        self.label = label

    @contextmanager
    def without_autoflush(self) -> Iterator[None]:
        yield

    def lock_user(self, user_id: str) -> dict[str, object] | None:
        self.database.events.append(f"{self.label}.user")
        return self.database.users.get(user_id)

    def lock_config(
        self,
        scope: CredentialScope,
        *,
        require_active: bool = True,
        require_api_key: bool = True,
    ) -> LegacyConfigFacts | None:
        del require_active, require_api_key
        self.database.events.append(f"{self.label}.config")
        return self.database.config if self.database.config.scope == scope else None

    def find_attempt(self, attempt_id: str) -> dict[str, object] | None:
        self.database.events.append(f"{self.label}.attempt")
        return self.database.attempts.get(attempt_id)

    def lock_binding(self, scope: CredentialScope) -> dict[str, object] | None:
        self.database.events.append(f"{self.label}.binding")
        return self.database.bindings.get(scope.key())

    def read_version(self, binding_id: str, version: int) -> dict[str, object] | None:
        self.database.events.append(f"{self.label}.version")
        return next(
            (row for row in self.database.versions.get(binding_id, ()) if row["binding_version"] == version),
            None,
        )

    def read_versions(self, binding_id: str) -> tuple[dict[str, object], ...]:
        self.database.events.append(f"{self.label}.versions")
        return tuple(self.database.versions.get(binding_id, ()))

    def find_principal(self, principal_id: str) -> dict[str, object] | None:
        self.database.events.append(f"{self.label}.principal")
        return next(
            (row for row in self.database.principals.values() if row["principal_id"] == principal_id),
            None,
        )

    def find_principal_identity(
        self,
        *,
        provider: str,
        issuer_namespace: str,
        stable_account_subject: str,
        owner_scope: str,
    ) -> dict[str, object] | None:
        self.database.events.append(f"{self.label}.principal_identity")
        return self.database.principals.get(
            (provider, issuer_namespace, stable_account_subject, owner_scope)
        )

    def insert_principal_if_absent(self, values: dict[str, object]) -> int:
        self.database.events.append(f"{self.label}.principal_insert")
        identity = (
            str(values["provider"]),
            str(values["issuer_namespace"]),
            str(values["stable_account_subject"]),
            str(values["owner_scope"]),
        )
        if identity in self.database.principals:
            return 0
        self.database.principals[identity] = dict(values)
        return 1

    def insert_binding_if_absent(self, values: dict[str, object]) -> int:
        self.database.events.append(f"{self.label}.binding_insert")
        scope_key = (
            str(values["source"]), str(values["row_id"]),
            str(values["owner_or_system"]), str(values["provider"]),
        )
        if scope_key in self.database.bindings:
            return 0
        self.database.bindings[scope_key] = dict(values)
        self.database.versions[str(values["binding_id"])] = []
        return 1

    def insert_version_if_absent(self, values: dict[str, object]) -> int:
        self.database.events.append(f"{self.label}.version_insert")
        attempt_id = str(values["server_attempt_id"])
        rows = self.database.versions[str(values["binding_id"])]
        if attempt_id in self.database.attempts or any(
            row["binding_version"] == values["binding_version"] for row in rows
        ):
            return 0
        row = dict(values)
        rows.append(row)
        self.database.attempts[attempt_id] = row
        return 1

    def cas_binding(
        self,
        *,
        binding_id: str,
        expected_revision: int,
        expected_version: int,
        next_version: int,
        enabled: bool,
        updated_at: object,
    ) -> int:
        self.database.events.append(f"{self.label}.cas")
        binding = next(
            (row for row in self.database.bindings.values() if row["binding_id"] == binding_id),
            None,
        )
        if (
            binding is None
            or binding["revision"] != expected_revision
            or binding["current_version"] != expected_version
        ):
            return 0
        binding.update(
            revision=expected_revision + 1,
            current_version=next_version,
            enabled=enabled,
            updated_at=updated_at,
        )
        return 1


def _read_scope_factory(database: MemoryDatabase):
    @contextmanager
    def open_scope() -> Iterator[MemoryStore]:
        database.read_scope_number += 1
        label = f"read{database.read_scope_number}"
        database.events.append(f"{label}.open")
        try:
            yield MemoryStore(database, label)
        finally:
            database.events.append(f"{label}.close")

    return open_scope


def _record(
    authority: HmacProofAuthority,
    crypto: FakeCrypto,
    scope: CredentialScope,
    *,
    record_id: str,
    principal_id: str,
    subject: str,
    key_generation: str,
    material_ref: str,
    secret: str,
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
        endpoint_policy=EndpointPolicy.from_base_url("https://provider.example.test/v2"),
        guard_key_id=crypto.guard_key_id,
        secret_consistency_guard="0" * 64,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    guarded = replace(
        unsigned,
        secret_consistency_guard=crypto.guard(secret, account_guard_associated_data(unsigned)),
    )
    return replace(
        guarded,
        proof_signature=authority.sign("root-enrollment-v1", guarded.proof_payload()),
    )


def _rotation(
    authority: HmacProofAuthority,
    original: ProtectedAccountRecord,
    replacement: ProtectedAccountRecord,
) -> RotationAttestation:
    unsigned = RotationAttestation(
        proof_id="rotation-proof-1",
        original_record_id=original.record_id,
        replacement_record_id=replacement.record_id,
        binding_id=BINDING_ID,
        predecessor_version=1,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    return replace(
        unsigned,
        proof_signature=authority.sign("root-rotation-v1", unsigned.proof_payload()),
    )


def _attempt(
    authority: HmacProofAuthority,
    scope: CredentialScope,
    *,
    attempt_id: str,
    operation: str,
    record: ProtectedAccountRecord | None = None,
    revision: int | None = None,
    predecessor: int | None = None,
    rotation_ref: str | None = None,
):
    return make_server_attempt(
        authority,
        attempt_id=attempt_id,
        actor_id=ACTOR_ID,
        operation=operation,  # type: ignore[arg-type]
        scope=scope,
        key_generation=record.key_generation if record else None,
        expected_revision=revision,
        predecessor_version=predecessor,
        protected_record_id=record.record_id if record else None,
        rotation_proof_ref=rotation_ref,
    )


def test_registry_rules_run_without_sqlalchemy_import_and_keep_store_boundaries() -> None:
    original_import = builtins.__import__

    def reject_sqlalchemy(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise AssertionError("registry application imported SQLAlchemy")
        return original_import(name, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(builtins, "__import__", reject_sqlalchemy)
        service_module = importlib.reload(registry_application)

        authority = HmacProofAuthority("root-test-key", b"p" * 32)
        events: list[str] = []
        crypto = FakeCrypto(
            {
                "vault://key-1": "secret-1",
                "vault://key-2": "secret-2",
                "vault://key-3": "secret-3",
            },
            events,
        )
        scope = CredentialScope("user", MODEL_ID, ACTOR_ID, "openai")
        initial = _record(
            authority, crypto, scope, record_id="record-1", principal_id=PRINCIPAL_ID,
            subject="subject-1", key_generation="generation-1", material_ref="vault://key-1",
            secret="secret-1",
        )
        rotated = _record(
            authority, crypto, scope, record_id="record-2", principal_id=PRINCIPAL_ID,
            subject="subject-1", key_generation="generation-2", material_ref="vault://key-2",
            secret="secret-2",
        )
        replacement = _record(
            authority, crypto, scope, record_id="record-3", principal_id=REPLACEMENT_PRINCIPAL_ID,
            subject="subject-2", key_generation="generation-3", material_ref="vault://key-3",
            secret="secret-3",
        )
        rotation = _rotation(authority, initial, rotated)
        enrollment = RootEnrollmentSource((initial, rotated, replacement), authority, (rotation,))
        clock_state = {"available": True, "calls": 0}

        def clock() -> datetime:
            clock_state["calls"] += 1
            if not clock_state["available"]:
                raise RuntimeError("clock unavailable")
            return NOW

        capabilities = SimpleNamespace(
            proof_authority=authority,
            enrollment_source=enrollment,
            crypto=crypto,
            read_session_factory=lambda: object(),
            db_clock=clock,
            id_factory=lambda: BINDING_ID,
        )
        config = LegacyConfigFacts(
            scope, "image", "openai", "fixture-model", "https://provider.example.test/v2",
            "secret-1", True,
        )
        database = MemoryDatabase(scope, config)
        database.events = events
        service = service_module.RegistryApplication(capabilities, _read_scope_factory(database))
        caller = MemoryStore(database, "caller")
        actor = TrustedActor(ACTOR_ID)

        register = _attempt(authority, scope, attempt_id="register-1", operation="register", record=initial)
        registered = service.stage_register_owned_config(caller, actor, MODEL_ID, register)
        assert isinstance(registered, RegistrationStaged)
        assert database.events[:9] == [
            "read1.open", "read1.attempt", "read1.close", "caller.attempt", "caller.user",
            "caller.config", "caller.principal_identity", "caller.principal_insert",
            "caller.binding_insert",
        ]
        assert "caller.binding" not in database.events
        assert database.events[9] == "caller.version_insert"

        binding = CredentialBinding(
            source="user", row_id=MODEL_ID, owner_or_system=ACTOR_ID, provider="openai",
            original_principal_id=PRINCIPAL_ID,
            endpoint_origin="https://provider.example.test",
            allowed_content_paths=(), binding_version="1",
        )
        read_event_start = len(database.events)
        resolved = service.resolve_original_credential(binding, "query")
        assert resolved is not None and resolved.secret == "secret-1"
        reads = database.events[read_event_start:]
        assert reads.index("read2.config") < reads.index("read2.binding")
        assert reads.index("read2.close") < reads.index("crypto.open") < reads.index("read3.open")
        assert reads.index("read3.config") < reads.index("read3.binding")
        assert reads[-1] == "read3.close"

        replay_event_start = len(database.events)
        clock_calls_before_replay = clock_state["calls"]
        clock_state["available"] = False
        replay = service.stage_register_owned_config(caller, actor, MODEL_ID, register)
        assert replay == registered
        assert clock_state["calls"] == clock_calls_before_replay
        assert database.events[replay_event_start:] == [
            "read4.open", "read4.attempt", "read4.close",
        ]
        clock_state["available"] = True

        database.config = replace(config, api_key="secret-2")
        rotate = _attempt(
            authority, scope, attempt_id="rotate-1", operation="rotate", record=rotated,
            revision=1, predecessor=1, rotation_ref=rotation.proof_id,
        )
        rotated_result = service.stage_rotate_or_replace_config(caller, actor, scope, 1, rotate)
        assert isinstance(rotated_result, RegistrationStaged)
        assert rotated_result.binding_version == 2
        principal = next(iter(database.principals.values()))
        assert principal["record_id"] == initial.record_id

        database.config = replace(config, api_key="secret-3")
        replace_attempt = _attempt(
            authority, scope, attempt_id="replace-1", operation="replace", record=replacement,
            revision=2, predecessor=2,
        )
        replaced = service.stage_rotate_or_replace_config(
            caller, actor, scope, 2, replace_attempt
        )
        assert isinstance(replaced, RegistrationStaged)
        assert replaced.binding_version == 3

        disable = _attempt(
            authority, scope, attempt_id="disable-1", operation="disable",
            revision=3, predecessor=3,
        )
        disabled = service.stage_disable_binding(caller, actor, scope, 3, disable)
        assert isinstance(disabled, RegistrationStaged)
        assert disabled.binding_version == 4
        binding_row = database.bindings[scope.key()]
        assert binding_row["current_version"] == 4
        assert binding_row["enabled"] is False
        assert [row["version_kind"] for row in database.versions[BINDING_ID]] == [
            "registered", "rotation", "replacement", "disabled",
        ]


def test_registry_store_error_maps_to_existing_conflict_without_broad_catch() -> None:
    authority = HmacProofAuthority("root-test-key", b"p" * 32)
    scope = CredentialScope("user", MODEL_ID, ACTOR_ID, "openai")
    config = LegacyConfigFacts(scope, "image", "openai", None, None, "secret", True)
    database = MemoryDatabase(scope, config)
    initial = FakeCrypto({}, [])
    record = _record(
        authority, initial, scope, record_id="record-1", principal_id=PRINCIPAL_ID,
        subject="subject-1", key_generation="generation-1", material_ref="vault://key",
        secret="secret",
    )
    source = RootEnrollmentSource((record,), authority)
    capabilities = SimpleNamespace(
        proof_authority=authority,
        enrollment_source=source,
        crypto=initial,
        read_session_factory=lambda: object(),
        db_clock=lambda: NOW,
    )
    service = RegistryApplication(capabilities, _read_scope_factory(database))

    class FailingStore(MemoryStore):
        def lock_config(self, *args: Any, **kwargs: Any) -> LegacyConfigFacts:
            raise RegistryStoreError("storage unavailable")

    result = service.stage_register_owned_config(
        FailingStore(database, "caller"),
        TrustedActor(ACTOR_ID),
        MODEL_ID,
        _attempt(authority, scope, attempt_id="register-fails", operation="register", record=record),
    )
    assert result.__class__.__name__ == "RegistryConflict"
    assert result.code == "registry_stage_failed"


def test_sqlalchemy_adapter_maps_only_database_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    from haoai_backend.generation_credentials.persistence import (
        CallerSessionRegistryMutationStore,
        RegistryReadRepository,
    )

    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        store = CallerSessionRegistryMutationStore(session)

        def database_error(self: object, attempt_id: str) -> None:
            raise SQLAlchemyError(attempt_id)

        monkeypatch.setattr(RegistryReadRepository, "find_attempt", database_error)
        with pytest.raises(RegistryStoreError):
            store.find_attempt("db-error")

        def application_error(self: object, attempt_id: str) -> None:
            raise ValueError(attempt_id)

        monkeypatch.setattr(RegistryReadRepository, "find_attempt", application_error)
        with pytest.raises(ValueError, match="application-error"):
            store.find_attempt("application-error")
    engine.dispose()
