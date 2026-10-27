from __future__ import annotations

from contextlib import nullcontext

import pytest
from sqlalchemy.exc import SQLAlchemyError

from haoai_backend.generation_credentials.domain import (
    CredentialScope,
    PreparedPayload,
    RegistryConflict,
)
from haoai_backend.generation_credentials.persistence import (
    RegistryPayloadRepository,
    RegistryReadRepository,
)


class CallerSession:
    @property
    def no_autoflush(self):
        return nullcontext()


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


def test_sqlalchemy_failure_maps_to_the_original_safe_registry_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_lock_binding(repository, scope):
        raise SQLAlchemyError("database details must not escape")

    monkeypatch.setattr(RegistryReadRepository, "lock_binding", fail_to_lock_binding)
    result = RegistryPayloadRepository().stage_payload(
        CallerSession(),
        _payload(),
        payload_id="new-payload",
        created_at=None,
    )

    assert result == RegistryConflict(
        "payload_stage_failed",
        "任务数据暂时无法保存，请回滚当前事务",
    )
    assert "database details" not in result.safe_detail
