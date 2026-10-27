"""SQLAlchemy-free rules for staging and replaying prepared payloads."""

from __future__ import annotations

from datetime import datetime

from .domain import PreparedPayload, PreparedPayloadStaged, RegistryConflict
from .payload_ports import PreparedPayloadStore, StoredPreparedPayload


def stage_prepared_payload(
    store: PreparedPayloadStore,
    payload: PreparedPayload,
    *,
    payload_id: str,
    created_at: datetime,
) -> PreparedPayloadStaged | RegistryConflict:
    if not isinstance(payload, PreparedPayload):
        return RegistryConflict("payload_invalid", "加密任务数据无效")

    binding = store.lock_binding(payload.scope)
    if (
        binding is None
        or not binding.enabled
        or binding.current_version != payload.binding_version
    ):
        return RegistryConflict("payload_binding_changed", "凭据绑定已变化")

    version = store.read_version(payload.binding_id, payload.binding_version)
    if (
        version is None
        or version.principal_id != payload.principal_id
        or version.source != payload.scope.source
        or version.row_id != payload.scope.row_id
        or version.owner_or_system != payload.scope.owner_or_system
        or version.provider != payload.scope.provider
    ):
        return RegistryConflict("payload_binding_mismatch", "凭据绑定无法确认")

    existing = store.find_payload(payload.actor_id, payload.operation, payload.server_attempt_id)
    if existing is not None:
        return _payload_replay(existing, payload)

    inserted = store.insert_payload_if_absent(payload, payload_id=payload_id, created_at=created_at)
    if inserted == 1:
        return PreparedPayloadStaged(payload_id, reused=False)

    raced = store.find_payload(payload.actor_id, payload.operation, payload.server_attempt_id)
    if raced is None:
        return RegistryConflict("payload_insert_raced", "加密任务数据暂时无法确认")
    return _payload_replay(raced, payload)


def _payload_replay(
    existing: StoredPreparedPayload,
    payload: PreparedPayload,
) -> PreparedPayloadStaged | RegistryConflict:
    equal = all(
        stored == candidate
        for stored, candidate in (
            (existing.command_proof, payload.command_proof),
            (existing.context_fingerprint, payload.context_fingerprint),
            (existing.schema_version, payload.schema_version),
            (existing.projector_version, payload.projector_version),
            (existing.reference_policy_version, payload.reference_policy_version),
            (existing.target_ref, payload.target_ref),
            (existing.model_ref, payload.model_ref),
            (existing.grant_ref, payload.grant_ref),
            (existing.binding_id, payload.binding_id),
            (existing.binding_version, payload.binding_version),
            (existing.principal_id, payload.principal_id),
            (existing.source, payload.scope.source),
            (existing.row_id, payload.scope.row_id),
            (existing.owner_or_system, payload.scope.owner_or_system),
            (existing.provider, payload.scope.provider),
        )
    )
    if not equal:
        return RegistryConflict("payload_attempt_conflict", "任务数据尝试已用于不同内容")
    return PreparedPayloadStaged(existing.prepared_payload_id, reused=True)
