"""Typed storage boundary for caller-owned prepared payload staging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .domain import CredentialScope, PreparedPayload


class PreparedPayloadStoreError(Exception):
    """The storage adapter could not complete a prepared-payload operation."""


@dataclass(frozen=True, slots=True)
class PayloadBindingSnapshot:
    enabled: bool
    current_version: int


@dataclass(frozen=True, slots=True)
class PayloadVersionSnapshot:
    principal_id: str
    source: str
    row_id: str
    owner_or_system: str
    provider: str


@dataclass(frozen=True, slots=True)
class StoredPreparedPayload:
    prepared_payload_id: str
    command_proof: str
    context_fingerprint: str
    schema_version: str
    projector_version: str
    reference_policy_version: str
    target_ref: str
    model_ref: str
    grant_ref: str
    binding_id: str
    binding_version: int
    principal_id: str
    source: str
    row_id: str
    owner_or_system: str
    provider: str


class PreparedPayloadStore(Protocol):
    """The four reads/writes needed to stage and replay one prepared payload."""

    def lock_binding(self, scope: CredentialScope) -> PayloadBindingSnapshot | None: ...

    def read_version(self, binding_id: str, version: int) -> PayloadVersionSnapshot | None: ...

    def find_payload(
        self,
        actor_id: str,
        operation: str,
        attempt_id: str,
    ) -> StoredPreparedPayload | None: ...

    def insert_payload_if_absent(
        self,
        payload: PreparedPayload,
        *,
        payload_id: str,
        created_at: datetime,
    ) -> int: ...
