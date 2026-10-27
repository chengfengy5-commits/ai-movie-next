"""Core table definitions and caller-owned registry persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar, cast

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import users
from haoai_backend.generation_catalog.tables import model_configs, system_models

from .domain import CredentialScope, PreparedPayload
from .binding_capture_ports import (
    BindingCaptureBinding,
    BindingCapturePrincipal,
    BindingCaptureVersion,
    LegacyConfigIdentityFacts,
)
from .payload_application import stage_prepared_payload
from .payload_ports import (
    PayloadBindingSnapshot,
    PayloadVersionSnapshot,
    PreparedPayloadStoreError,
    StoredPreparedPayload,
)


metadata = MetaData()
_Result = TypeVar("_Result")

credential_principals = Table(
    "generation_credential_principals",
    metadata,
    Column("principal_id", String(36), primary_key=True),
    Column("provider", String(64), nullable=False),
    Column("issuer_namespace", String(255), nullable=False),
    Column("stable_account_subject", String(255), nullable=False),
    Column("owner_scope", String(128), nullable=False),
    Column("record_id", String(255), nullable=False),
    Column("evidence_ref", String(255), nullable=False),
    Column("evidence_version", String(128), nullable=False),
    Column("source_state", String(16), nullable=False),
    Column("created_by", String(36), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "provider", "issuer_namespace", "stable_account_subject", "owner_scope",
        name="uq_generation_credential_principal_identity",
    ),
    UniqueConstraint(
        "principal_id", "provider", "owner_scope",
        name="uq_generation_credential_principal_scope",
    ),
    CheckConstraint(
        "source_state IN ('valid', 'revoked')",
        name="ck_generation_credential_principal_state",
    ),
)

credential_bindings = Table(
    "generation_credential_bindings",
    metadata,
    Column("binding_id", String(36), primary_key=True),
    Column("source", String(16), nullable=False),
    Column("row_id", String(36), nullable=False),
    Column("owner_or_system", String(128), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("current_version", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("created_by", String(36), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "source", "row_id", "owner_or_system", "provider",
        name="uq_generation_credential_binding_scope",
    ),
    UniqueConstraint(
        "binding_id", "source", "row_id", "owner_or_system", "provider",
        name="uq_generation_credential_binding_full_scope",
    ),
    ForeignKeyConstraint(
        ["binding_id", "current_version"],
        ["generation_credential_versions.binding_id",
         "generation_credential_versions.binding_version"],
        name="fk_generation_credential_binding_head",
        deferrable=True,
        initially="DEFERRED",
        ondelete="RESTRICT",
    ),
    CheckConstraint("source IN ('system', 'user')", name="ck_generation_credential_binding_source"),
    CheckConstraint("revision > 0", name="ck_generation_credential_binding_revision"),
    CheckConstraint("current_version > 0", name="ck_generation_credential_binding_head_positive"),
)

credential_versions = Table(
    "generation_credential_versions",
    metadata,
    Column("binding_id", String(36), primary_key=True),
    Column("binding_version", Integer, primary_key=True),
    Column("source", String(16), nullable=False),
    Column("row_id", String(36), nullable=False),
    Column("owner_or_system", String(128), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("principal_id", String(36), nullable=False),
    Column("issuer_namespace", String(255), nullable=False),
    Column("stable_account_subject", String(255), nullable=False),
    Column("key_generation", String(128), nullable=False),
    Column("attempt_key_generation", String(128), nullable=True),
    Column("record_id", String(255), nullable=False),
    Column("protected_record_id", String(255), nullable=True),
    Column("evidence_ref", String(255), nullable=False),
    Column("evidence_version", String(128), nullable=False),
    Column("endpoint_origin", String(512), nullable=False),
    Column("api_base_path", String(1024), nullable=False),
    Column("allowed_content_paths", Text, nullable=False),
    Column("endpoint_policy_version", String(128), nullable=False),
    Column("version_kind", String(16), nullable=False),
    Column("predecessor_version", Integer, nullable=True),
    Column("expected_revision", Integer, nullable=True),
    Column("rotation_proof_ref", String(255), nullable=True),
    Column("ciphertext", LargeBinary, nullable=True),
    Column("nonce", LargeBinary, nullable=True),
    Column("seal_key_id", String(128), nullable=True),
    Column("seal_algorithm_version", String(128), nullable=True),
    Column("guard_key_id", String(128), nullable=True),
    Column("secret_consistency_guard", String(64), nullable=True),
    Column("server_attempt_id", String(128), nullable=False),
    Column("actor_id", String(36), nullable=False),
    Column("operation", String(16), nullable=False),
    Column("proof_key_id", String(128), nullable=False),
    Column("command_proof", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "binding_id", "binding_version", "principal_id",
        "source", "row_id", "owner_or_system", "provider",
        name="uq_generation_credential_version_full_scope",
    ),
    UniqueConstraint("server_attempt_id", name="uq_generation_credential_version_attempt"),
    ForeignKeyConstraint(
        ["binding_id", "source", "row_id", "owner_or_system", "provider"],
        ["generation_credential_bindings.binding_id",
         "generation_credential_bindings.source",
         "generation_credential_bindings.row_id",
         "generation_credential_bindings.owner_or_system",
         "generation_credential_bindings.provider"],
        name="fk_generation_credential_version_binding_scope",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["principal_id", "provider", "owner_or_system"],
        ["generation_credential_principals.principal_id",
         "generation_credential_principals.provider",
         "generation_credential_principals.owner_scope"],
        name="fk_generation_credential_version_principal_scope",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["binding_id", "predecessor_version"],
        ["generation_credential_versions.binding_id",
         "generation_credential_versions.binding_version"],
        name="fk_generation_credential_version_predecessor",
        ondelete="RESTRICT",
    ),
    CheckConstraint("binding_version > 0", name="ck_generation_credential_version_positive"),
    CheckConstraint(
        "predecessor_version IS NULL OR predecessor_version < binding_version",
        name="ck_generation_credential_version_predecessor_earlier",
    ),
    CheckConstraint(
        "version_kind IN ('registered', 'rotation', 'replacement', 'disabled')",
        name="ck_generation_credential_version_kind",
    ),
    CheckConstraint(
        "operation IN ('register', 'rotate', 'replace', 'disable')",
        name="ck_generation_credential_version_operation",
    ),
    CheckConstraint(
        "(version_kind = 'disabled' AND ciphertext IS NULL AND nonce IS NULL "
        "AND seal_key_id IS NULL AND seal_algorithm_version IS NULL "
        "AND guard_key_id IS NULL AND secret_consistency_guard IS NULL) OR "
        "(version_kind <> 'disabled' AND ciphertext IS NOT NULL AND nonce IS NOT NULL "
        "AND seal_key_id IS NOT NULL AND seal_algorithm_version IS NOT NULL "
        "AND guard_key_id IS NOT NULL AND secret_consistency_guard IS NOT NULL)",
        name="ck_generation_credential_version_material",
    ),
    CheckConstraint(
        "(version_kind = 'rotation' AND rotation_proof_ref IS NOT NULL) OR "
        "(version_kind <> 'rotation' AND rotation_proof_ref IS NULL)",
        name="ck_generation_credential_version_rotation_proof",
    ),
)
Index(
    "ix_generation_credential_versions_binding_principal",
    credential_versions.c.binding_id,
    credential_versions.c.principal_id,
    credential_versions.c.binding_version,
)

prepared_payloads = Table(
    "generation_prepared_payloads",
    metadata,
    Column("prepared_payload_id", String(36), primary_key=True),
    Column("actor_id", String(36), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("server_attempt_id", String(128), nullable=False),
    Column("command_proof", String(64), nullable=False),
    Column("context_fingerprint", String(128), nullable=False),
    Column("schema_version", String(128), nullable=False),
    Column("projector_version", String(128), nullable=False),
    Column("reference_policy_version", String(128), nullable=False),
    Column("target_ref", String(255), nullable=False),
    Column("model_ref", String(255), nullable=False),
    Column("grant_ref", String(255), nullable=False),
    Column("source", String(16), nullable=False),
    Column("row_id", String(36), nullable=False),
    Column("owner_or_system", String(128), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("binding_id", String(36), nullable=False),
    Column("binding_version", Integer, nullable=False),
    Column("principal_id", String(36), nullable=False),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("nonce", LargeBinary, nullable=False),
    Column("encryption_key_id", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "actor_id", "operation", "server_attempt_id",
        name="uq_generation_prepared_payload_attempt",
    ),
    ForeignKeyConstraint(
        ["binding_id", "binding_version", "principal_id",
         "source", "row_id", "owner_or_system", "provider"],
        ["generation_credential_versions.binding_id",
         "generation_credential_versions.binding_version",
         "generation_credential_versions.principal_id",
         "generation_credential_versions.source",
         "generation_credential_versions.row_id",
         "generation_credential_versions.owner_or_system",
         "generation_credential_versions.provider"],
        name="fk_generation_prepared_payload_binding_version",
        ondelete="RESTRICT",
    ),
)

credential_schema_migrations = Table(
    "generation_credential_schema_migrations",
    metadata,
    Column("version", String(128), primary_key=True),
    Column("checksum", String(64), nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("length(checksum) = 64", name="ck_generation_credential_migration_checksum"),
)

REGISTRY_TABLES = (
    credential_principals,
    credential_bindings,
    credential_versions,
    prepared_payloads,
    credential_schema_migrations,
)


@dataclass(frozen=True, slots=True)
class LegacyConfigFacts:
    scope: CredentialScope
    category: str
    provider: str
    model: str | None
    base_url: str | None = field(repr=False)
    api_key: str = field(repr=False)
    active: bool


class RegistryReadRepository:
    """Fresh Core projections only; all writes enlist in the supplied Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_user(self, user_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            select(users.c.id, users.c.is_superuser)
            .where(users.c.id == user_id)
            .with_for_update()
        ).mappings().first()
        return dict(row) if row is not None else None

    def lock_config(
        self,
        scope: CredentialScope,
        *,
        require_active: bool = True,
        require_api_key: bool = True,
    ) -> LegacyConfigFacts | None:
        if scope.source == "system":
            row = self._session.execute(
                select(
                    system_models.c.id,
                    system_models.c.category,
                    system_models.c.provider,
                    system_models.c.model,
                    system_models.c.base_url,
                    system_models.c.api_key,
                    system_models.c.is_active,
                )
                .where(system_models.c.id == scope.row_id)
                .with_for_update()
            ).mappings().first()
            if row is None or (require_active and not row["is_active"]):
                return None
            owner = "system"
            active = bool(row["is_active"])
        else:
            row = self._session.execute(
                select(
                    model_configs.c.id,
                    model_configs.c.user_id,
                    model_configs.c.category,
                    model_configs.c.provider,
                    model_configs.c.model,
                    model_configs.c.base_url,
                    model_configs.c.api_key,
                )
                .where(
                    model_configs.c.id == scope.row_id,
                    model_configs.c.user_id == scope.owner_or_system,
                )
                .with_for_update()
            ).mappings().first()
            if row is None:
                return None
            owner = str(row["user_id"])
            active = True
        if str(row["provider"]) != scope.provider or owner != scope.owner_or_system:
            return None
        api_key = row["api_key"]
        if require_api_key and (not isinstance(api_key, str) or not api_key):
            return None
        if not isinstance(api_key, str):
            api_key = ""
        return LegacyConfigFacts(
            scope=scope,
            category=str(row["category"]),
            provider=str(row["provider"]),
            model=str(row["model"]) if row["model"] is not None else None,
            base_url=str(row["base_url"]) if row["base_url"] is not None else None,
            api_key=api_key,
            active=active,
        )

    def lock_config_identity(
        self,
        scope: CredentialScope,
        *,
        require_active: bool = True,
    ) -> LegacyConfigIdentityFacts | None:
        """Lock model endpoint metadata without reading credential material."""

        if scope.source == "system":
            row = self._session.execute(
                select(
                    system_models.c.id,
                    system_models.c.category,
                    system_models.c.provider,
                    system_models.c.model,
                    system_models.c.base_url,
                    system_models.c.is_active,
                )
                .where(system_models.c.id == scope.row_id)
                .with_for_update()
            ).mappings().first()
            if row is None or (require_active and not row["is_active"]):
                return None
            owner = "system"
            active = bool(row["is_active"])
        else:
            row = self._session.execute(
                select(
                    model_configs.c.id,
                    model_configs.c.user_id,
                    model_configs.c.category,
                    model_configs.c.provider,
                    model_configs.c.model,
                    model_configs.c.base_url,
                )
                .where(
                    model_configs.c.id == scope.row_id,
                    model_configs.c.user_id == scope.owner_or_system,
                )
                .with_for_update()
            ).mappings().first()
            if row is None:
                return None
            owner = str(row["user_id"])
            active = True
        if str(row["provider"]) != scope.provider or owner != scope.owner_or_system:
            return None
        return LegacyConfigIdentityFacts(
            scope=scope,
            category=str(row["category"]),
            provider=str(row["provider"]),
            model=str(row["model"]) if row["model"] is not None else None,
            base_url=str(row["base_url"]) if row["base_url"] is not None else None,
            active=active,
        )

    def find_principal(self, principal_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            select(credential_principals).where(
                credential_principals.c.principal_id == principal_id
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    def find_principal_identity(
        self,
        *,
        provider: str,
        issuer_namespace: str,
        stable_account_subject: str,
        owner_scope: str,
    ) -> dict[str, Any] | None:
        row = self._session.execute(
            select(credential_principals).where(
                credential_principals.c.provider == provider,
                credential_principals.c.issuer_namespace == issuer_namespace,
                credential_principals.c.stable_account_subject == stable_account_subject,
                credential_principals.c.owner_scope == owner_scope,
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    def find_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            select(credential_versions)
            .where(credential_versions.c.server_attempt_id == attempt_id)
        ).mappings().first()
        return dict(row) if row is not None else None

    def lock_binding(self, scope: CredentialScope) -> dict[str, Any] | None:
        row = self._session.execute(
            select(credential_bindings)
            .where(
                credential_bindings.c.source == scope.source,
                credential_bindings.c.row_id == scope.row_id,
                credential_bindings.c.owner_or_system == scope.owner_or_system,
                credential_bindings.c.provider == scope.provider,
            )
            .with_for_update()
        ).mappings().first()
        return dict(row) if row is not None else None

    def lock_binding_for_capture(
        self,
        scope: CredentialScope,
    ) -> BindingCaptureBinding | None:
        row = self._session.execute(
            select(
                credential_bindings.c.binding_id,
                credential_bindings.c.revision,
                credential_bindings.c.current_version,
                credential_bindings.c.enabled,
            )
            .where(
                credential_bindings.c.source == scope.source,
                credential_bindings.c.row_id == scope.row_id,
                credential_bindings.c.owner_or_system == scope.owner_or_system,
                credential_bindings.c.provider == scope.provider,
            )
            .with_for_update()
        ).mappings().first()
        return cast(BindingCaptureBinding, dict(row)) if row is not None else None

    def read_version(self, binding_id: str, version: int) -> dict[str, Any] | None:
        row = self._session.execute(
            select(credential_versions).where(
                credential_versions.c.binding_id == binding_id,
                credential_versions.c.binding_version == version,
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    def read_versions(self, binding_id: str) -> tuple[dict[str, Any], ...]:
        rows = self._session.execute(
            select(credential_versions)
            .where(credential_versions.c.binding_id == binding_id)
            .order_by(credential_versions.c.binding_version)
        ).mappings().all()
        return tuple(dict(row) for row in rows)

    def read_capture_version(
        self,
        binding_id: str,
        version: int,
    ) -> BindingCaptureVersion | None:
        row = self._session.execute(
            select(*self._binding_capture_version_columns())
            .where(
                credential_versions.c.binding_id == binding_id,
                credential_versions.c.binding_version == version,
            )
        ).mappings().first()
        return cast(BindingCaptureVersion, dict(row)) if row is not None else None

    def read_capture_versions(
        self,
        binding_id: str,
    ) -> tuple[BindingCaptureVersion, ...]:
        rows = self._session.execute(
            select(*self._binding_capture_version_columns())
            .where(credential_versions.c.binding_id == binding_id)
            .order_by(credential_versions.c.binding_version)
        ).mappings().all()
        return tuple(cast(BindingCaptureVersion, dict(row)) for row in rows)

    @staticmethod
    def _binding_capture_version_columns() -> tuple[Column[Any], ...]:
        return (
            credential_versions.c.binding_id,
            credential_versions.c.binding_version,
            credential_versions.c.source,
            credential_versions.c.row_id,
            credential_versions.c.owner_or_system,
            credential_versions.c.provider,
            credential_versions.c.principal_id,
            credential_versions.c.issuer_namespace,
            credential_versions.c.stable_account_subject,
            credential_versions.c.key_generation,
            credential_versions.c.attempt_key_generation,
            credential_versions.c.record_id,
            credential_versions.c.protected_record_id,
            credential_versions.c.evidence_ref,
            credential_versions.c.evidence_version,
            credential_versions.c.endpoint_origin,
            credential_versions.c.api_base_path,
            credential_versions.c.allowed_content_paths,
            credential_versions.c.endpoint_policy_version,
            credential_versions.c.version_kind,
            credential_versions.c.predecessor_version,
            credential_versions.c.expected_revision,
            credential_versions.c.rotation_proof_ref,
            credential_versions.c.guard_key_id,
            credential_versions.c.secret_consistency_guard,
            credential_versions.c.server_attempt_id,
            credential_versions.c.actor_id,
            credential_versions.c.operation,
            credential_versions.c.proof_key_id,
            credential_versions.c.command_proof,
        )

    def find_capture_principal(
        self,
        principal_id: str,
    ) -> BindingCapturePrincipal | None:
        row = self._session.execute(
            select(
                credential_principals.c.source_state,
                credential_principals.c.provider,
                credential_principals.c.owner_scope,
                credential_principals.c.issuer_namespace,
                credential_principals.c.stable_account_subject,
            ).where(credential_principals.c.principal_id == principal_id)
        ).mappings().first()
        return cast(BindingCapturePrincipal, dict(row)) if row is not None else None

    def find_payload(self, actor_id: str, operation: str, attempt_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            select(prepared_payloads).where(
                prepared_payloads.c.actor_id == actor_id,
                prepared_payloads.c.operation == operation,
                prepared_payloads.c.server_attempt_id == attempt_id,
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    def insert_do_nothing(self, table: Table, values: dict[str, object]) -> int:
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(table).values(**values).on_conflict_do_nothing()
        elif dialect == "sqlite":
            statement = sqlite_insert(table).values(**values).on_conflict_do_nothing()
        else:
            raise RuntimeError("registry conflict-safe inserts require PostgreSQL or SQLite")
        result = self._session.execute(statement)
        return int(result.rowcount or 0)

    def cas_binding(
        self,
        *,
        binding_id: str,
        expected_revision: int,
        expected_version: int,
        next_version: int,
        enabled: bool,
        updated_at: Any,
    ) -> int:
        result = self._session.execute(
            update(credential_bindings)
            .where(
                credential_bindings.c.binding_id == binding_id,
                credential_bindings.c.revision == expected_revision,
                credential_bindings.c.current_version == expected_version,
            )
            .values(
                revision=expected_revision + 1,
                current_version=next_version,
                enabled=enabled,
                updated_at=updated_at,
            )
        )
        return int(result.rowcount or 0)

    def insert_payload(self, payload: PreparedPayload, *, payload_id: str, created_at: Any) -> int:
        values = {
            "prepared_payload_id": payload_id,
            "actor_id": payload.actor_id,
            "operation": payload.operation,
            "server_attempt_id": payload.server_attempt_id,
            "command_proof": payload.command_proof,
            "context_fingerprint": payload.context_fingerprint,
            "schema_version": payload.schema_version,
            "projector_version": payload.projector_version,
            "reference_policy_version": payload.reference_policy_version,
            "target_ref": payload.target_ref,
            "model_ref": payload.model_ref,
            "grant_ref": payload.grant_ref,
            **payload.scope.identity(),
            "binding_id": payload.binding_id,
            "binding_version": payload.binding_version,
            "principal_id": payload.principal_id,
            "ciphertext": payload.ciphertext,
            "nonce": payload.nonce,
            "encryption_key_id": payload.encryption_key_id,
            "created_at": created_at,
        }
        return self.insert_do_nothing(prepared_payloads, values)


class RegistryPayloadRepository:
    """Append encrypted payload data without granting admission authority."""

    def stage_payload(
        self,
        caller_session: Session,
        payload: PreparedPayload,
        *,
        payload_id: str,
        created_at: Any,
    ):
        from .domain import RegistryConflict

        if not isinstance(payload, PreparedPayload):
            return RegistryConflict("payload_invalid", "加密任务数据无效")
        repository = RegistryReadRepository(caller_session)
        with caller_session.no_autoflush:
            try:
                return stage_prepared_payload(
                    _CallerSessionPreparedPayloadStore(repository),
                    payload,
                    payload_id=payload_id,
                    created_at=created_at,
                )
            except PreparedPayloadStoreError:
                return RegistryConflict(
                    "payload_stage_failed",
                    "任务数据暂时无法保存，请回滚当前事务",
                )


class _CallerSessionPreparedPayloadStore:
    """Adapt existing caller-session repository projections to the narrow port."""

    def __init__(self, repository: RegistryReadRepository) -> None:
        self._repository = repository

    def lock_binding(self, scope: CredentialScope) -> PayloadBindingSnapshot | None:
        try:
            binding = self._repository.lock_binding(scope)
        except SQLAlchemyError:
            raise PreparedPayloadStoreError from None
        if binding is None:
            return None
        return PayloadBindingSnapshot(
            enabled=bool(binding["enabled"]),
            current_version=int(binding["current_version"]),
        )

    def read_version(self, binding_id: str, version: int) -> PayloadVersionSnapshot | None:
        try:
            row = self._repository.read_version(binding_id, version)
        except SQLAlchemyError:
            raise PreparedPayloadStoreError from None
        if row is None:
            return None
        return PayloadVersionSnapshot(
            principal_id=row["principal_id"],
            source=row["source"],
            row_id=row["row_id"],
            owner_or_system=row["owner_or_system"],
            provider=row["provider"],
        )

    def find_payload(
        self,
        actor_id: str,
        operation: str,
        attempt_id: str,
    ) -> StoredPreparedPayload | None:
        try:
            row = self._repository.find_payload(actor_id, operation, attempt_id)
        except SQLAlchemyError:
            raise PreparedPayloadStoreError from None
        if row is None:
            return None
        return StoredPreparedPayload(
            prepared_payload_id=row["prepared_payload_id"],
            command_proof=row["command_proof"],
            context_fingerprint=row["context_fingerprint"],
            schema_version=row["schema_version"],
            projector_version=row["projector_version"],
            reference_policy_version=row["reference_policy_version"],
            target_ref=row["target_ref"],
            model_ref=row["model_ref"],
            grant_ref=row["grant_ref"],
            binding_id=row["binding_id"],
            binding_version=row["binding_version"],
            principal_id=row["principal_id"],
            source=row["source"],
            row_id=row["row_id"],
            owner_or_system=row["owner_or_system"],
            provider=row["provider"],
        )

    def insert_payload_if_absent(
        self,
        payload: PreparedPayload,
        *,
        payload_id: str,
        created_at: Any,
    ) -> int:
        try:
            return self._repository.insert_payload(
                payload,
                payload_id=payload_id,
                created_at=created_at,
            )
        except SQLAlchemyError:
            raise PreparedPayloadStoreError from None


class CallerSessionBindingCaptureStore:
    """Adapt narrow capture reads to the original caller-owned Session."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = RegistryReadRepository(session)

    def without_autoflush(self, operation: Callable[[], _Result]) -> _Result:
        with self._session.no_autoflush:
            return operation()

    def lock_config_identity(
        self,
        scope: CredentialScope,
    ) -> LegacyConfigIdentityFacts | None:
        return self._repository.lock_config_identity(scope)

    def lock_binding_for_capture(
        self,
        scope: CredentialScope,
    ) -> BindingCaptureBinding | None:
        return self._repository.lock_binding_for_capture(scope)

    def read_capture_version(
        self,
        binding_id: str,
        version: int,
    ) -> BindingCaptureVersion | None:
        return self._repository.read_capture_version(binding_id, version)

    def read_capture_versions(
        self,
        binding_id: str,
    ) -> tuple[BindingCaptureVersion, ...]:
        return self._repository.read_capture_versions(binding_id)

    def find_capture_principal(
        self,
        principal_id: str,
    ) -> BindingCapturePrincipal | None:
        return self._repository.find_capture_principal(principal_id)
