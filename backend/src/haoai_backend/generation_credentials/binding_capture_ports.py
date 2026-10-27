"""SQLAlchemy-free contracts for capturing registered credential bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

from haoai_backend.generation_contracts.types import CredentialBinding

from .domain import CredentialScope, EndpointPolicy


@dataclass(frozen=True, slots=True)
class LegacyConfigIdentityFacts:
    """Endpoint identity read without selecting credential material."""

    scope: CredentialScope
    category: str
    provider: str
    model: str | None
    base_url: str | None
    active: bool


class ConfigEndpointIdentity(Protocol):
    scope: CredentialScope
    base_url: str | None


class BindingCaptureBinding(TypedDict):
    binding_id: object
    revision: object
    current_version: object
    enabled: object


class BindingCaptureVersion(TypedDict):
    binding_id: object
    binding_version: object
    source: object
    row_id: object
    owner_or_system: object
    provider: object
    principal_id: object
    issuer_namespace: object
    stable_account_subject: object
    key_generation: object
    attempt_key_generation: object
    record_id: object
    protected_record_id: object
    evidence_ref: object
    evidence_version: object
    endpoint_origin: object
    api_base_path: object
    allowed_content_paths: object
    endpoint_policy_version: object
    version_kind: object
    predecessor_version: object
    expected_revision: object
    rotation_proof_ref: object
    guard_key_id: object
    secret_consistency_guard: object
    server_attempt_id: object
    actor_id: object
    operation: object
    proof_key_id: object
    command_proof: object


class BindingCapturePrincipal(TypedDict):
    source_state: object
    provider: object
    owner_scope: object
    issuer_namespace: object
    stable_account_subject: object


@dataclass(frozen=True, slots=True)
class CapturedCredentialBinding:
    """Verified original principal and endpoint metadata, without key resolution."""

    binding_id: str
    binding: CredentialBinding
    endpoint_policy: EndpointPolicy


class BindingCaptureReadStore(Protocol):
    """Narrow read port used by the SQLAlchemy-free capture application."""

    def lock_config_identity(
        self,
        scope: CredentialScope,
    ) -> LegacyConfigIdentityFacts | None: ...

    def lock_binding_for_capture(
        self,
        scope: CredentialScope,
    ) -> BindingCaptureBinding | None: ...

    def read_capture_version(
        self,
        binding_id: str,
        version: int,
    ) -> BindingCaptureVersion | None: ...

    def read_capture_versions(
        self,
        binding_id: str,
    ) -> tuple[BindingCaptureVersion, ...]: ...

    def find_capture_principal(
        self,
        principal_id: str,
    ) -> BindingCapturePrincipal | None: ...
