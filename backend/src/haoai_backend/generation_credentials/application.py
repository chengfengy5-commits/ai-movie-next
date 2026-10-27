"""Caller-session compatibility facade for credential registry rules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import BoundCredential, CredentialBinding
from haoai_backend.shared.identity import TrustedActor

from .binding_capture_application import CredentialBindingCaptureApplication
from .binding_capture_ports import CapturedCredentialBinding
from .domain import (
    CredentialCrypto,
    CredentialScope,
    HmacProofAuthority,
    PreparedPayload,
    PreparedPayloadStaged,
    RegistrationStaged,
    RegistryConflict,
    RegistryRefusal,
    RootEnrollmentSource,
    ServerAttempt,
)
from .payload_ports import PreparedPayloadStoreError
from .persistence import (
    CallerSessionBindingCaptureStore,
    CallerSessionRegistryMutationStore,
    CallerSessionRegistrySchemaProbe,
    IndependentRegistryReadScopeFactory,
    RegistryPayloadRepository,
)
from .registry_application import (
    RegistryApplication,
    check_registry_readiness,
)


@dataclass(frozen=True, slots=True)
class RegistryCapabilities:
    """Root-bound capabilities; accepting this value does not prove readiness."""

    proof_authority: HmacProofAuthority | None = None
    enrollment_source: RootEnrollmentSource | None = None
    crypto: CredentialCrypto | None = None
    read_session_factory: Callable[[], Session] | None = None
    db_clock: Callable[[], datetime] | None = None
    id_factory: Callable[[], str] | None = None

    @classmethod
    def unconfigured(cls) -> RegistryCapabilities:
        return cls()


@dataclass(frozen=True, slots=True)
class CredentialRegistryReadiness:
    schema_status: str
    enrollment_status: str
    crypto_status: str
    clock_status: str
    issues: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return all(
            status == "ready"
            for status in (
                self.schema_status,
                self.enrollment_status,
                self.crypto_status,
                self.clock_status,
            )
        )


class CredentialRegistry:
    """Public Session adapter that delegates decisions to the pure registry application."""

    def __init__(self, capabilities: RegistryCapabilities) -> None:
        self._capabilities = capabilities
        read_scope_factory = (
            IndependentRegistryReadScopeFactory(capabilities.read_session_factory)
            if capabilities.read_session_factory is not None
            else None
        )
        self._registry_application = RegistryApplication(
            capabilities,
            read_scope_factory,
        )

    def stage_register_system_config(
        self,
        caller_session: Session,
        trusted_config_actor: TrustedActor,
        row_id: str,
        server_attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        store = CallerSessionRegistryMutationStore(caller_session)
        return self._registry_application.stage_register_system_config(
            store,
            trusted_config_actor,
            row_id,
            server_attempt,
        )

    def stage_register_owned_config(
        self,
        caller_session: Session,
        trusted_actor: TrustedActor,
        row_id: str,
        server_attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        store = CallerSessionRegistryMutationStore(caller_session)
        return self._registry_application.stage_register_owned_config(
            store,
            trusted_actor,
            row_id,
            server_attempt,
        )

    def stage_rotate_or_replace_config(
        self,
        caller_session: Session,
        trusted_actor: TrustedActor,
        scope: CredentialScope,
        expected_head_version: int,
        server_attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        store = CallerSessionRegistryMutationStore(caller_session)
        return self._registry_application.stage_rotate_or_replace_config(
            store,
            trusted_actor,
            scope,
            expected_head_version,
            server_attempt,
        )

    def stage_disable_binding(
        self,
        caller_session: Session,
        trusted_actor: TrustedActor,
        scope: CredentialScope,
        expected_head_revision: int,
        server_attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        store = CallerSessionRegistryMutationStore(caller_session)
        return self._registry_application.stage_disable_binding(
            store,
            trusted_actor,
            scope,
            expected_head_revision,
            server_attempt,
        )

    def find_confirmed_original_registration(
        self,
        server_attempt: ServerAttempt,
        expected_full_command: ServerAttempt,
    ) -> object:
        return self._registry_application.find_confirmed_original_registration(
            server_attempt,
            expected_full_command,
        )

    def resolve_original_credential(
        self,
        binding: CredentialBinding,
        purpose: Literal["create", "query", "content"],
    ) -> BoundCredential | None:
        return self._registry_application.resolve_original_credential(binding, purpose)

    def capture_original_binding(
        self,
        caller_session: Session,
        scope: CredentialScope,
    ) -> CapturedCredentialBinding | None:
        """Freeze the exact registered principal without resolving its secret."""

        source = self._capabilities.enrollment_source
        authority = self._capabilities.proof_authority
        if (
            not isinstance(caller_session, Session)
            or not isinstance(scope, CredentialScope)
            or source is None
            or not source.ready
            or authority is None
        ):
            return None
        try:
            now = self._registry_application._now()
            store = CallerSessionBindingCaptureStore(caller_session)
            service = CredentialBindingCaptureApplication(source, authority)
            return store.without_autoflush(lambda: service.capture(store, scope, now=now))
        except Exception:
            return None

    def stage_prepared_payload(
        self,
        caller_session: Session,
        payload: PreparedPayload,
        *,
        payload_id: str,
    ) -> PreparedPayloadStaged | RegistryConflict:
        if not isinstance(payload, PreparedPayload):
            return RegistryConflict("payload_unavailable", "任务数据保存能力无法确认")
        try:
            now = self._registry_application._now()
        except Exception:
            return RegistryConflict("clock_unavailable", "任务数据保存时钟不可用")
        try:
            return RegistryPayloadRepository().stage_payload(
                caller_session,
                payload,
                payload_id=payload_id,
                created_at=now,
            )
        except (SQLAlchemyError, PreparedPayloadStoreError):
            return RegistryConflict("payload_stage_failed", "任务数据暂时无法保存，请回滚当前事务")


def build_credential_registry(
    capabilities: RegistryCapabilities | None = None,
) -> CredentialRegistry:
    """Build an inert service; explicit protected dependencies are never discovered."""

    return CredentialRegistry(capabilities or RegistryCapabilities.unconfigured())


def check_credential_registry_readiness(
    caller_session: Session,
    explicit_capabilities: RegistryCapabilities,
) -> CredentialRegistryReadiness:
    """Check B2 first, then current CP schema and explicit protected capabilities."""

    result = check_registry_readiness(
        caller_session,
        explicit_capabilities,
        CallerSessionRegistrySchemaProbe(),
    )
    return CredentialRegistryReadiness(
        result.schema_status,
        result.enrollment_status,
        result.crypto_status,
        result.clock_status,
        result.issues,
    )


__all__ = (
    "CredentialRegistry",
    "CredentialRegistryReadiness",
    "RegistryCapabilities",
    "build_credential_registry",
    "check_credential_registry_readiness",
)
