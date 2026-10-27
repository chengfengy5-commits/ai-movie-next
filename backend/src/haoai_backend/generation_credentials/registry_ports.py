"""SQLAlchemy-free storage contracts for credential registry rules."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from collections.abc import Callable
from datetime import datetime
from typing import Any, Mapping, Protocol

from .domain import CredentialCrypto, CredentialScope, HmacProofAuthority, RootEnrollmentSource


class RegistryStoreError(Exception):
    """A registry storage operation could not be confirmed."""


@dataclass(frozen=True, slots=True)
class LegacyConfigFacts:
    """Caller-owned model configuration projection used by registry rules."""

    scope: CredentialScope
    category: str
    provider: str
    model: str | None
    base_url: str | None = field(repr=False)
    api_key: str = field(repr=False)
    active: bool


@dataclass(frozen=True, slots=True)
class RegistrySchemaProbeResult:
    status: str
    issues: tuple[str, ...] = ()


class RegistryReadStore(Protocol):
    """Read-only projections required for reconciliation and credential resolution."""

    def without_autoflush(self) -> AbstractContextManager[None]: ...

    def lock_config(
        self,
        scope: CredentialScope,
        *,
        require_active: bool = True,
        require_api_key: bool = True,
    ) -> LegacyConfigFacts | None: ...

    def find_attempt(self, attempt_id: str) -> Mapping[str, Any] | None: ...

    def lock_binding(self, scope: CredentialScope) -> Mapping[str, Any] | None: ...

    def read_version(
        self,
        binding_id: str,
        version: int,
    ) -> Mapping[str, Any] | None: ...

    def read_versions(self, binding_id: str) -> tuple[Mapping[str, Any], ...]: ...

    def find_principal(self, principal_id: str) -> Mapping[str, Any] | None: ...


class RegistryMutationStore(RegistryReadStore, Protocol):
    """Named caller-session operations used by registry mutation rules."""

    def lock_user(self, user_id: str) -> Mapping[str, Any] | None: ...

    def find_principal_identity(
        self,
        *,
        provider: str,
        issuer_namespace: str,
        stable_account_subject: str,
        owner_scope: str,
    ) -> Mapping[str, Any] | None: ...

    def insert_principal_if_absent(self, values: dict[str, object]) -> int: ...

    def insert_binding_if_absent(self, values: dict[str, object]) -> int: ...

    def insert_version_if_absent(self, values: dict[str, object]) -> int: ...

    def cas_binding(
        self,
        *,
        binding_id: str,
        expected_revision: int,
        expected_version: int,
        next_version: int,
        enabled: bool,
        updated_at: object,
    ) -> int: ...


class RegistryReadScopeFactory(Protocol):
    """Open and own one independent read-only scope per invocation."""

    def __call__(self) -> AbstractContextManager[RegistryReadStore]: ...


class RegistrySchemaProbe(Protocol):
    def check(self, caller_context: object) -> RegistrySchemaProbeResult: ...


class RegistryCapabilitySet(Protocol):
    proof_authority: HmacProofAuthority | None
    enrollment_source: RootEnrollmentSource | None
    crypto: CredentialCrypto | None
    read_session_factory: Callable[[], object] | None
    db_clock: Callable[[], datetime] | None
    id_factory: Callable[[], str] | None
