from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haoai_backend.generation_credentials.domain import (
    CredentialScope,
    PreparedPayload,
    PreparedPayloadStaged,
    RegistryConflict,
)
from haoai_backend.generation_credentials.payload_application import stage_prepared_payload
from haoai_backend.generation_credentials.payload_ports import (
    PayloadBindingSnapshot,
    PayloadVersionSnapshot,
    PreparedPayloadStoreError,
    StoredPreparedPayload,
)


CREATED_AT = datetime(2026, 10, 27, 0, 0, tzinfo=timezone.utc)


def _payload() -> PreparedPayload:
    return PreparedPayload(
        actor_id="actor-1",
        operation="image.batch",
        server_attempt_id="attempt-1",
        scope=CredentialScope(
            source="user",
            row_id="config-1",
            owner_or_system="actor-1",
            provider="openai",
        ),
        binding_id="11111111-1111-4111-8111-111111111111",
        binding_version=3,
        principal_id="22222222-2222-4222-8222-222222222222",
        context_fingerprint="context-1",
        schema_version="schema-1",
        projector_version="projector-1",
        reference_policy_version="reference-1",
        target_ref="target-1",
        model_ref="model-1",
        grant_ref="grant-1",
        command_proof="a" * 64,
        ciphertext=b"encrypted-payload",
        nonce=b"nonce",
        encryption_key_id="key-1",
    )


def _version(payload: PreparedPayload) -> PayloadVersionSnapshot:
    return PayloadVersionSnapshot(
        principal_id=payload.principal_id,
        source=payload.scope.source,
        row_id=payload.scope.row_id,
        owner_or_system=payload.scope.owner_or_system,
        provider=payload.scope.provider,
    )


def _stored(
    payload: PreparedPayload,
    prepared_payload_id: str = "stored-payload",
) -> StoredPreparedPayload:
    return StoredPreparedPayload(
        prepared_payload_id=prepared_payload_id,
        command_proof=payload.command_proof,
        context_fingerprint=payload.context_fingerprint,
        schema_version=payload.schema_version,
        projector_version=payload.projector_version,
        reference_policy_version=payload.reference_policy_version,
        target_ref=payload.target_ref,
        model_ref=payload.model_ref,
        grant_ref=payload.grant_ref,
        binding_id=payload.binding_id,
        binding_version=payload.binding_version,
        principal_id=payload.principal_id,
        source=payload.scope.source,
        row_id=payload.scope.row_id,
        owner_or_system=payload.scope.owner_or_system,
        provider=payload.scope.provider,
    )


class FakePayloadStore:
    def __init__(
        self,
        payload: PreparedPayload,
        *,
        existing: StoredPreparedPayload | None = None,
        find_results: list[StoredPreparedPayload | None] | None = None,
        insert_result: int = 1,
        fail_at: str | None = None,
    ) -> None:
        self.binding: PayloadBindingSnapshot | None = PayloadBindingSnapshot(
            enabled=True,
            current_version=payload.binding_version,
        )
        self.version: PayloadVersionSnapshot | None = _version(payload)
        self.find_results = find_results if find_results is not None else [existing]
        self.insert_result = insert_result
        self.fail_at = fail_at
        self.calls: list[str] = []

    def _called(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_at == name:
            raise PreparedPayloadStoreError

    def lock_binding(self, scope: CredentialScope) -> PayloadBindingSnapshot | None:
        self._called("lock_binding")
        return self.binding

    def read_version(self, binding_id: str, version: int) -> PayloadVersionSnapshot | None:
        self._called("read_version")
        return self.version

    def find_payload(
        self,
        actor_id: str,
        operation: str,
        attempt_id: str,
    ) -> StoredPreparedPayload | None:
        self._called("find_payload")
        return self.find_results.pop(0) if self.find_results else None

    def insert_payload_if_absent(
        self,
        payload: PreparedPayload,
        *,
        payload_id: str,
        created_at: datetime,
    ) -> int:
        self._called("insert_payload_if_absent")
        return self.insert_result


def _stage(store: FakePayloadStore, payload: PreparedPayload | object | None = None):
    return stage_prepared_payload(
        store,
        payload if payload is not None else _payload(),  # type: ignore[arg-type]
        payload_id="new-payload",
        created_at=CREATED_AT,
    )


def test_service_and_package_import_and_run_with_sqlalchemy_import_blocked() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src"
    script = r'''
import builtins
real_import = builtins.__import__
def reject_sqlalchemy(name, *args, **kwargs):
    if name == "sqlalchemy" or name.startswith("sqlalchemy."):
        raise AssertionError("SQLAlchemy import attempted")
    return real_import(name, *args, **kwargs)
builtins.__import__ = reject_sqlalchemy

from haoai_backend.generation_credentials.domain import CredentialScope, PreparedPayload
from haoai_backend.generation_credentials.payload_application import stage_prepared_payload
from haoai_backend.generation_credentials.payload_ports import (
    PayloadBindingSnapshot,
    PayloadVersionSnapshot,
)

class Store:
    def __init__(self):
        self.calls = []
    def lock_binding(self, scope):
        self.calls.append("lock_binding")
        return PayloadBindingSnapshot(True, 1)
    def read_version(self, binding_id, version):
        self.calls.append("read_version")
        return PayloadVersionSnapshot("principal", "user", "row", "actor", "provider")
    def find_payload(self, actor_id, operation, attempt_id):
        self.calls.append("find_payload")
        return None
    def insert_payload_if_absent(self, payload, *, payload_id, created_at):
        self.calls.append("insert_payload_if_absent")
        return 1

payload = PreparedPayload(
    actor_id="actor", operation="op", server_attempt_id="attempt",
    scope=CredentialScope("user", "row", "actor", "provider"),
    binding_id="binding", binding_version=1,
    principal_id="principal", context_fingerprint="context",
    schema_version="schema", projector_version="projector",
    reference_policy_version="reference", target_ref="target",
    model_ref="model", grant_ref="grant", command_proof="a" * 64,
    ciphertext=b"ciphertext", nonce=b"nonce", encryption_key_id="key",
)
store = Store()
result = stage_prepared_payload(store, payload, payload_id="payload", created_at=None)
assert result.prepared_payload_id == "payload" and not result.reused
assert store.calls == ["lock_binding", "read_version", "find_payload", "insert_payload_if_absent"]
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(source_root), environment.get("PYTHONPATH")))
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr


def test_store_call_order_and_new_payload_result() -> None:
    payload = _payload()
    store = FakePayloadStore(payload)

    result = _stage(store, payload)

    assert result == PreparedPayloadStaged("new-payload", reused=False)
    assert store.calls == [
        "lock_binding",
        "read_version",
        "find_payload",
        "insert_payload_if_absent",
    ]


def test_exact_replay_returns_existing_id_without_inserting() -> None:
    payload = _payload()
    store = FakePayloadStore(payload, existing=_stored(payload, "existing-id"))
    encrypted_copy = replace(
        payload,
        ciphertext=b"different-encrypted-copy",
        nonce=b"different-nonce",
        encryption_key_id="rotated-key",
    )

    result = _stage(store, encrypted_copy)

    assert result == PreparedPayloadStaged("existing-id", reused=True)
    assert store.calls == ["lock_binding", "read_version", "find_payload"]


@pytest.mark.parametrize(
    "field",
    [
        "command_proof",
        "context_fingerprint",
        "schema_version",
        "projector_version",
        "reference_policy_version",
        "target_ref",
        "model_ref",
        "grant_ref",
        "binding_id",
        "binding_version",
        "principal_id",
        "source",
        "row_id",
        "owner_or_system",
        "provider",
    ],
)
def test_replay_conflicts_on_each_of_the_fifteen_original_fields(field: str) -> None:
    payload = _payload()
    replay_values: dict[str, object] = {
        "command_proof": "b" * 64,
        "context_fingerprint": "context-other",
        "schema_version": "schema-other",
        "projector_version": "projector-other",
        "reference_policy_version": "reference-other",
        "target_ref": "target-other",
        "model_ref": "model-other",
        "grant_ref": "grant-other",
        "binding_id": "33333333-3333-4333-8333-333333333333",
        "binding_version": 4,
        "principal_id": "44444444-4444-4444-8444-444444444444",
        "source": "system",
        "row_id": "row-other",
        "owner_or_system": "actor-other",
        "provider": "provider-other",
    }
    existing = replace(_stored(payload), **{field: replay_values[field]})
    store = FakePayloadStore(payload, existing=existing)
    result = _stage(store, payload)

    assert result == RegistryConflict("payload_attempt_conflict", "任务数据尝试已用于不同内容")
    assert store.calls == ["lock_binding", "read_version", "find_payload"]


@pytest.mark.parametrize(
    ("binding", "version", "expected_code", "expected_calls"),
    [
        (None, None, "payload_binding_changed", ["lock_binding"]),
        (PayloadBindingSnapshot(False, 3), None, "payload_binding_changed", ["lock_binding"]),
        (PayloadBindingSnapshot(True, 2), None, "payload_binding_changed", ["lock_binding"]),
        (
            PayloadBindingSnapshot(True, 3),
            PayloadVersionSnapshot("wrong-principal", "user", "config-1", "actor-1", "openai"),
            "payload_binding_mismatch",
            ["lock_binding", "read_version"],
        ),
    ],
)
def test_binding_and_version_refusals_stop_before_payload_lookup(
    binding: PayloadBindingSnapshot | None,
    version: PayloadVersionSnapshot | None,
    expected_code: str,
    expected_calls: list[str],
) -> None:
    payload = _payload()
    store = FakePayloadStore(payload)
    store.binding = binding
    store.version = version

    result = _stage(store, payload)

    assert isinstance(result, RegistryConflict)
    assert result.code == expected_code
    assert store.calls == expected_calls


def test_insert_race_rereads_and_reuses_exact_concurrent_payload() -> None:
    payload = _payload()
    store = FakePayloadStore(
        payload,
        find_results=[None, _stored(payload, "concurrent-id")],
        insert_result=0,
    )

    result = _stage(store, payload)

    assert result == PreparedPayloadStaged("concurrent-id", reused=True)
    assert store.calls == [
        "lock_binding",
        "read_version",
        "find_payload",
        "insert_payload_if_absent",
        "find_payload",
    ]


def test_insert_race_without_visible_row_returns_original_conflict() -> None:
    payload = _payload()
    store = FakePayloadStore(payload, find_results=[None, None], insert_result=0)

    result = _stage(store, payload)

    assert result == RegistryConflict("payload_insert_raced", "加密任务数据暂时无法确认")
    assert store.calls[-1] == "find_payload"


@pytest.mark.parametrize(
    "fail_at",
    ["lock_binding", "read_version", "find_payload", "insert_payload_if_absent"],
)
def test_storage_errors_cross_the_port_as_a_dedicated_error(fail_at: str) -> None:
    payload = _payload()
    store = FakePayloadStore(payload, fail_at=fail_at)

    with pytest.raises(PreparedPayloadStoreError):
        _stage(store, payload)
