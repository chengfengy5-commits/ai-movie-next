"""Explicit credential enrollment, immutable versioning, and exact resolution."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import BoundCredential, CredentialBinding
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    CredentialCrypto,
    CredentialScope,
    EndpointPolicy,
    HmacProofAuthority,
    PreparedPayload,
    PreparedPayloadStaged,
    ProtectedAccountRecord,
    RegistrationCommitUnknown,
    RegistrationStaged,
    RegistryConflict,
    RegistryRefusal,
    RootEnrollmentSource,
    RotationAttestation,
    SealedMaterial,
    ServerAttempt,
    account_guard_associated_data,
    credential_associated_data,
)
from haoai_backend.generation_runtime.readiness import check_generation_readiness

from .persistence import (
    LegacyConfigFacts,
    RegistryPayloadRepository,
    RegistryReadRepository,
    credential_bindings,
    credential_principals,
    credential_versions,
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
    """Caller-transaction registry service with inert, fail-closed defaults."""

    def __init__(self, capabilities: RegistryCapabilities) -> None:
        self._capabilities = capabilities

    def stage_register_system_config(
        self,
        caller_session: Session,
        trusted_config_actor: TrustedActor,
        row_id: str,
        server_attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        if not self._valid_actor(trusted_config_actor, server_attempt):
            return RegistryRefusal("actor_untrusted", "系统模型登记身份无法确认")
        scope = server_attempt.scope
        if (
            scope.source != "system"
            or scope.owner_or_system != "system"
            or scope.row_id != row_id
            or server_attempt.operation != "register"
        ):
            return RegistryRefusal("scope_mismatch", "系统模型登记范围不匹配")
        prior = self._reconcile_prior_attempt(server_attempt)
        if prior is not None:
            return prior
        try:
            with caller_session.no_autoflush:
                repository = RegistryReadRepository(caller_session)
                replay = self._existing_attempt(repository, server_attempt)
                if replay is not None:
                    return replay
                if not self._valid_register_command(server_attempt):
                    return RegistryRefusal("scope_mismatch", "系统模型登记命令包含变更字段")
                now = self._mutation_time("系统模型登记时钟不可用")
                if isinstance(now, RegistryRefusal):
                    return now
                user = repository.lock_user(trusted_config_actor.user_id)
                if user is None or user.get("is_superuser") is not True:
                    return RegistryRefusal("admin_unavailable", "系统模型管理员权限无法确认")
                facts = repository.lock_config(scope)
                if facts is None:
                    return RegistryRefusal("config_unavailable", "指定系统模型不可用")
                return self._stage_initial(repository, facts, server_attempt, now=now)
        except SQLAlchemyError:
            return RegistryConflict("registry_stage_failed", "凭据登记暂时无法确认，请回滚当前事务")

    def stage_register_owned_config(
        self,
        caller_session: Session,
        trusted_actor: TrustedActor,
        row_id: str,
        server_attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        if not self._valid_actor(trusted_actor, server_attempt):
            return RegistryRefusal("actor_untrusted", "用户凭据登记身份无法确认")
        scope = server_attempt.scope
        if (
            scope.source != "user"
            or scope.owner_or_system != trusted_actor.user_id
            or scope.row_id != row_id
            or server_attempt.operation != "register"
        ):
            return RegistryRefusal("scope_mismatch", "用户凭据登记范围不匹配")
        prior = self._reconcile_prior_attempt(server_attempt)
        if prior is not None:
            return prior
        try:
            with caller_session.no_autoflush:
                repository = RegistryReadRepository(caller_session)
                replay = self._existing_attempt(repository, server_attempt)
                if replay is not None:
                    return replay
                if not self._valid_register_command(server_attempt):
                    return RegistryRefusal("scope_mismatch", "用户登记命令包含变更字段")
                now = self._mutation_time("凭据登记时钟不可用")
                if isinstance(now, RegistryRefusal):
                    return now
                user = repository.lock_user(trusted_actor.user_id)
                if user is None:
                    return RegistryRefusal("actor_unavailable", "用户身份无法确认")
                facts = repository.lock_config(scope)
                if facts is None:
                    return RegistryRefusal("config_unavailable", "指定用户模型不可用")
                return self._stage_initial(repository, facts, server_attempt, now=now)
        except SQLAlchemyError:
            return RegistryConflict("registry_stage_failed", "凭据登记暂时无法确认，请回滚当前事务")

    def stage_rotate_or_replace_config(
        self,
        caller_session: Session,
        trusted_actor: TrustedActor,
        scope: CredentialScope,
        expected_head_version: int,
        server_attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        if not self._valid_actor(trusted_actor, server_attempt):
            return RegistryRefusal("actor_untrusted", "凭据变更身份无法确认")
        if (
            scope != server_attempt.scope
            or server_attempt.operation not in {"rotate", "replace"}
            or server_attempt.predecessor_version != expected_head_version
        ):
            return RegistryRefusal("scope_mismatch", "凭据变更范围不匹配")
        prior = self._reconcile_prior_attempt(server_attempt)
        if prior is not None:
            return prior
        try:
            with caller_session.no_autoflush:
                repository = RegistryReadRepository(caller_session)
                replay = self._existing_attempt(repository, server_attempt)
                if replay is not None:
                    return replay
                now = self._mutation_time("凭据变更时钟不可用")
                if isinstance(now, RegistryRefusal):
                    return now
                user = repository.lock_user(trusted_actor.user_id)
                if user is None:
                    return RegistryRefusal("actor_unavailable", "用户身份无法确认")
                if scope.source == "system" and user.get("is_superuser") is not True:
                    return RegistryRefusal("admin_unavailable", "系统模型管理员权限无法确认")
                if scope.source == "user" and scope.owner_or_system != trusted_actor.user_id:
                    return RegistryRefusal("owner_mismatch", "用户模型所有权不匹配")
                facts = repository.lock_config(scope)
                binding = repository.lock_binding(scope)
                if facts is None or binding is None or not binding["enabled"]:
                    return RegistryRefusal("binding_unavailable", "凭据绑定不可用")
                current_version = int(binding["current_version"])
                current_revision = int(binding["revision"])
                if (
                    current_version != expected_head_version
                    or server_attempt.expected_revision != current_revision
                    or server_attempt.protected_record_id is None
                    or server_attempt.key_generation is None
                ):
                    return RegistryConflict("binding_changed", "凭据绑定版本已变化")
                previous = repository.read_version(binding["binding_id"], current_version)
                if previous is None:
                    return RegistryConflict("binding_history_incomplete", "凭据绑定历史无法确认")
                new_record = self._load_enrollment(scope, server_attempt.key_generation, now=now)
                if isinstance(new_record, RegistryRefusal):
                    return new_record
                if new_record.record_id != server_attempt.protected_record_id:
                    return RegistryRefusal("enrollment_mismatch", "受保护账号记录与请求不匹配")
                if not self._config_matches_record(facts, new_record):
                    return RegistryRefusal("endpoint_policy_mismatch", "模型端点与受保护策略不匹配")
                verified_secret = self._verified_config_secret(facts, new_record)
                if isinstance(verified_secret, RegistryRefusal):
                    return verified_secret
                principal_result = self._ensure_principal(
                    repository, new_record, server_attempt, now=now
                )
                if principal_result is not None:
                    return principal_result
                original_record = self._record_by_id(str(previous["record_id"]))
                if original_record is None:
                    return RegistryRefusal("original_enrollment_unavailable", "原受保护账号记录无法确认")
                next_version = current_version + 1
                rotation_proof_ref: str | None = None
                if server_attempt.operation == "rotate":
                    proof_ref = server_attempt.rotation_proof_ref
                    proof = (
                        self._capabilities.enrollment_source.rotation_proof(proof_ref)
                        if self._capabilities.enrollment_source is not None and proof_ref is not None
                        else None
                    )
                    if proof is None or not self._capabilities.enrollment_source.verify_rotation(
                        original_record,
                        new_record,
                        proof,
                        binding_id=str(binding["binding_id"]),
                        predecessor_version=current_version,
                        now=now,
                    ):
                        return RegistryRefusal("rotation_proof_unavailable", "同一账号轮换证明无法确认")
                    rotation_proof_ref = proof_ref
                else:
                    if new_record.principal_id == previous["principal_id"]:
                        return RegistryRefusal("replacement_same_principal", "同主体密钥变更必须使用轮换证明")
                    if new_record.scope != scope:
                        return RegistryRefusal("replacement_scope_mismatch", "替换账号范围不匹配")
                staged = self._stage_version(
                    repository,
                    facts,
                    new_record,
                    verified_secret,
                    server_attempt,
                    binding_id=str(binding["binding_id"]),
                    binding_version=next_version,
                    version_kind="rotation" if server_attempt.operation == "rotate" else "replacement",
                    predecessor_version=current_version,
                    rotation_proof_ref=rotation_proof_ref,
                    expected_revision=current_revision,
                    current_version=current_version,
                    now=now,
                )
                return staged
        except SQLAlchemyError:
            return RegistryConflict("registry_stage_failed", "凭据变更暂时无法确认，请回滚当前事务")

    def stage_disable_binding(
        self,
        caller_session: Session,
        trusted_actor: TrustedActor,
        scope: CredentialScope,
        expected_head_revision: int,
        server_attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        if not self._valid_actor(trusted_actor, server_attempt):
            return RegistryRefusal("actor_untrusted", "凭据停用身份无法确认")
        if (
            scope != server_attempt.scope
            or server_attempt.operation != "disable"
            or server_attempt.expected_revision != expected_head_revision
            or server_attempt.predecessor_version is None
        ):
            return RegistryRefusal("scope_mismatch", "凭据停用范围不匹配")
        prior = self._reconcile_prior_attempt(server_attempt)
        if prior is not None:
            return prior
        try:
            with caller_session.no_autoflush:
                repository = RegistryReadRepository(caller_session)
                replay = self._existing_attempt(repository, server_attempt)
                if replay is not None:
                    return replay
                now = self._mutation_time("凭据停用时钟不可用")
                if isinstance(now, RegistryRefusal):
                    return now
                user = repository.lock_user(trusted_actor.user_id)
                if user is None:
                    return RegistryRefusal("actor_unavailable", "用户身份无法确认")
                if scope.source == "system" and user.get("is_superuser") is not True:
                    return RegistryRefusal("admin_unavailable", "系统模型管理员权限无法确认")
                if scope.source == "user" and scope.owner_or_system != trusted_actor.user_id:
                    return RegistryRefusal("owner_mismatch", "用户模型所有权不匹配")
                facts = repository.lock_config(scope, require_active=False, require_api_key=False)
                binding = repository.lock_binding(scope)
                if facts is None or binding is None:
                    return RegistryRefusal("binding_unavailable", "凭据绑定不可用")
                current_version = int(binding["current_version"])
                revision = int(binding["revision"])
                if (
                    not binding["enabled"]
                    or server_attempt.expected_revision != revision
                    or server_attempt.predecessor_version != current_version
                ):
                    return RegistryConflict("binding_changed", "凭据绑定版本已变化")
                version_id = current_version + 1
                previous = repository.read_version(str(binding["binding_id"]), current_version)
                if previous is None:
                    return RegistryConflict("binding_history_incomplete", "凭据绑定历史无法确认")
                values = {
                    **previous,
                    "binding_version": version_id,
                    "version_kind": "disabled",
                    "predecessor_version": current_version,
                    "rotation_proof_ref": None,
                    "ciphertext": None,
                    "nonce": None,
                    "seal_key_id": None,
                    "seal_algorithm_version": None,
                    "guard_key_id": None,
                    "secret_consistency_guard": None,
                    "server_attempt_id": server_attempt.attempt_id,
                    "actor_id": server_attempt.actor_id,
                    "operation": server_attempt.operation,
                    "attempt_key_generation": server_attempt.key_generation,
                    "expected_revision": server_attempt.expected_revision,
                    "protected_record_id": server_attempt.protected_record_id,
                    "proof_key_id": server_attempt.proof_key_id,
                    "command_proof": server_attempt.command_proof,
                    "created_at": now,
                }
                if repository.insert_do_nothing(credential_versions, values) != 1:
                    return RegistryConflict("attempt_conflict", "凭据停用尝试已存在")
                changed = repository.cas_binding(
                    binding_id=str(binding["binding_id"]),
                    expected_revision=revision,
                    expected_version=current_version,
                    next_version=version_id,
                    enabled=False,
                    updated_at=now,
                )
                if changed != 1:
                    return RegistryConflict("binding_changed", "凭据停用版本已变化，请回滚当前事务")
                return RegistrationStaged(
                    server_attempt.attempt_id,
                    str(binding["binding_id"]),
                    version_id,
                    scope,
                )
        except SQLAlchemyError:
            return RegistryConflict("registry_stage_failed", "凭据停用暂时无法确认，请回滚当前事务")

    def find_confirmed_original_registration(
        self,
        server_attempt: ServerAttempt,
        expected_full_command: ServerAttempt,
    ) -> object:
        if (
            not self._valid_attempt(server_attempt)
            or not isinstance(expected_full_command, ServerAttempt)
            or expected_full_command.command_payload() != server_attempt.command_payload()
            or expected_full_command.command_proof != server_attempt.command_proof
            or self._capabilities.read_session_factory is None
        ):
            return RegistrationCommitUnknown(
                getattr(server_attempt, "attempt_id", "unknown"),
                "原始登记结果无法独立确认",
            )
        read_session: Session | None = None
        try:
            read_session = self._capabilities.read_session_factory()
            with read_session.no_autoflush:
                repository = RegistryReadRepository(read_session)
                row = repository.find_attempt(server_attempt.attempt_id)
                if row is None:
                    return RegistrationCommitUnknown(server_attempt.attempt_id, "原始登记尚未提交或无法确认")
                if not self._row_matches_attempt(row, server_attempt):
                    return RegistryConflict("attempt_conflict", "登记尝试已对应不同的原始命令")
                from .domain import ConfirmedRegistration

                return ConfirmedRegistration(
                    attempt_id=server_attempt.attempt_id,
                    actor_id=server_attempt.actor_id,
                    operation=server_attempt.operation,
                    scope=server_attempt.scope,
                    binding_id=str(row["binding_id"]),
                    binding_version=int(row["binding_version"]),
                    principal_id=str(row["principal_id"]),
                    command_proof=server_attempt.command_proof,
                )
        except SQLAlchemyError:
            return RegistrationCommitUnknown(server_attempt.attempt_id, "原始登记结果无法独立确认")
        finally:
            if read_session is not None:
                read_session.close()

    def resolve_original_credential(
        self,
        binding: CredentialBinding,
        purpose: Literal["create", "query", "content"],
    ) -> BoundCredential | None:
        source = self._capabilities.enrollment_source
        crypto = self._capabilities.crypto
        factory = self._capabilities.read_session_factory
        if (
            not isinstance(binding, CredentialBinding)
            or purpose not in {"create", "query", "content"}
            or source is None
            or crypto is None
            or factory is None
            or self._capabilities.db_clock is None
            or not crypto.ready()
        ):
            return None
        try:
            now = self._now()
        except Exception:
            return None
        scope = self._scope_from_binding(binding)
        if scope is None:
            return None
        captured: dict[str, object] | None = None
        read_session: Session | None = None
        try:
            read_session = factory()
            with read_session.no_autoflush:
                repository = RegistryReadRepository(read_session)
                facts = repository.lock_config(scope)
                current = repository.lock_binding(scope)
                if facts is None or current is None or not current["enabled"]:
                    return None
                head = int(current["current_version"])
                version = repository.read_version(str(current["binding_id"]), head)
                if version is None or version["version_kind"] == "disabled":
                    return None
                chain = repository.read_versions(str(current["binding_id"]))
                verified = self._verify_resolution_chain(
                    binding, scope, current, chain, now=now
                )
                if verified is None:
                    return None
                record = self._record_by_id(str(version["record_id"]))
                principal = repository.find_principal(str(version["principal_id"]))
                if (
                    record is None
                    or principal is None
                    or principal["source_state"] != "valid"
                    or principal["provider"] != scope.provider
                    or principal["owner_scope"] != scope.owner_or_system
                    or principal["issuer_namespace"] != record.issuer_namespace
                    or principal["stable_account_subject"] != record.stable_account_subject
                    or source.load_account(scope, record.key_generation, now=now) != record
                    or not self._config_matches_record(facts, record)
                ):
                    return None
                captured = {
                    "revision": int(current["revision"]),
                    "binding_id": str(current["binding_id"]),
                    "version": head,
                    "row": version,
                    "record": record,
                    "facts": facts,
                }
        except Exception:
            return None
        finally:
            if read_session is not None:
                read_session.close()
        if captured is None:
            return None
        row = captured["row"]
        record = captured["record"]
        facts = captured["facts"]
        assert isinstance(row, dict) and isinstance(record, ProtectedAccountRecord)
        assert isinstance(facts, LegacyConfigFacts)
        try:
            material = self._sealed_material(row)
            if material is None:
                return None
            associated_data = credential_associated_data(
                scope,
                binding_id=str(captured["binding_id"]),
                binding_version=int(captured["version"]),
                principal_id=str(row["principal_id"]),
                key_generation=str(row["key_generation"]),
                evidence_ref=str(row["evidence_ref"]),
                purpose="provider-credential-material",
            )
            secret = crypto.open_secret(material, associated_data)
            if secret is None:
                return None
            secret_guard = crypto.guard(secret, account_guard_associated_data(record))
            if secret_guard is None or secret_guard != row["secret_consistency_guard"]:
                return None
            current_secret = crypto.resolve_legacy_value(facts.api_key)
            if current_secret is None:
                return None
            current_guard = crypto.guard(current_secret, account_guard_associated_data(record))
            if current_guard is None or current_guard != record.secret_consistency_guard:
                return None
            if secret_guard != current_guard:
                return None
            if not self._recheck_resolution(scope, captured, record, crypto):
                return None
            return BoundCredential(
                provider=scope.provider,
                principal_id=str(row["principal_id"]),
                secret=secret,
            )
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
        now = self._mutation_time("任务数据保存时钟不可用")
        if isinstance(now, RegistryRefusal):
            return RegistryConflict(now.code, now.safe_detail)
        try:
            return RegistryPayloadRepository().stage_payload(
                caller_session,
                payload,
                payload_id=payload_id,
                created_at=now,
            )
        except SQLAlchemyError:
            return RegistryConflict("payload_stage_failed", "任务数据暂时无法保存，请回滚当前事务")

    def _stage_initial(
        self,
        repository: RegistryReadRepository,
        facts: LegacyConfigFacts,
        attempt: ServerAttempt,
        *,
        now: datetime,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        if attempt.key_generation is None or attempt.protected_record_id is None:
            return RegistryRefusal("attempt_incomplete", "登记命令缺少受保护账号引用")
        record = self._load_enrollment(facts.scope, attempt.key_generation, now=now)
        if isinstance(record, RegistryRefusal):
            return record
        if record.record_id != attempt.protected_record_id:
            return RegistryRefusal("enrollment_mismatch", "受保护账号记录与请求不匹配")
        if not self._config_matches_record(facts, record):
            return RegistryRefusal("endpoint_policy_mismatch", "模型端点与受保护策略不匹配")
        secret = self._verified_config_secret(facts, record)
        if isinstance(secret, RegistryRefusal):
            return secret
        principal_result = self._ensure_principal(repository, record, attempt, now=now)
        if principal_result is not None:
            return principal_result
        binding_id = self._new_id()
        if binding_id is None:
            return RegistryRefusal("id_unavailable", "凭据登记标识无法生成")
        values = {
            "binding_id": binding_id,
            **facts.scope.identity(),
            "revision": 1,
            "current_version": 1,
            "enabled": True,
            "created_by": attempt.actor_id,
            "created_at": now,
            "updated_at": now,
        }
        if repository.insert_do_nothing(credential_bindings, values) != 1:
            return RegistryConflict("binding_exists", "该模型已有凭据绑定")
        staged = self._stage_version(
            repository,
            facts,
            record,
            secret,
            attempt,
            binding_id=binding_id,
            binding_version=1,
            version_kind="registered",
            predecessor_version=None,
            rotation_proof_ref=None,
            expected_revision=None,
            current_version=None,
            now=now,
        )
        return staged

    def _ensure_principal(
        self,
        repository: RegistryReadRepository,
        record: ProtectedAccountRecord,
        attempt: ServerAttempt,
        *,
        now: datetime,
    ) -> RegistryRefusal | RegistryConflict | None:
        identity = repository.find_principal_identity(
            provider=record.scope.provider,
            issuer_namespace=record.issuer_namespace,
            stable_account_subject=record.stable_account_subject,
            owner_scope=record.scope.owner_or_system,
        )
        if identity is not None:
            if identity["principal_id"] != record.principal_id or identity["source_state"] != "valid":
                return RegistryRefusal("principal_conflict", "该账号身份已绑定到其他或已停用主体")
            return None
        values = {
            "principal_id": record.principal_id,
            "provider": record.scope.provider,
            "issuer_namespace": record.issuer_namespace,
            "stable_account_subject": record.stable_account_subject,
            "owner_scope": record.scope.owner_or_system,
            "record_id": record.record_id,
            "evidence_ref": record.evidence_ref,
            "evidence_version": record.evidence_version,
            "source_state": "valid",
            "created_by": attempt.actor_id,
            "created_at": now,
        }
        if repository.insert_do_nothing(credential_principals, values) == 1:
            return None
        raced = repository.find_principal_identity(
            provider=record.scope.provider,
            issuer_namespace=record.issuer_namespace,
            stable_account_subject=record.stable_account_subject,
            owner_scope=record.scope.owner_or_system,
        )
        if raced is not None and raced["principal_id"] == record.principal_id and raced["source_state"] == "valid":
            return None
        return RegistryConflict("principal_insert_raced", "账号主体登记发生冲突")

    def _stage_version(
        self,
        repository: RegistryReadRepository,
        facts: LegacyConfigFacts,
        record: ProtectedAccountRecord,
        secret: str,
        attempt: ServerAttempt,
        *,
        binding_id: str,
        binding_version: int,
        version_kind: Literal["registered", "rotation", "replacement"],
        predecessor_version: int | None,
        rotation_proof_ref: str | None,
        expected_revision: int | None,
        current_version: int | None,
        now: datetime,
    ) -> RegistrationStaged | RegistryRefusal | RegistryConflict:
        crypto = self._capabilities.crypto
        if crypto is None or not crypto.ready():
            return RegistryConflict("crypto_unavailable", "凭据加密能力无法确认")
        associated_data = credential_associated_data(
            facts.scope,
            binding_id=binding_id,
            binding_version=binding_version,
            principal_id=record.principal_id,
            key_generation=record.key_generation,
            evidence_ref=record.evidence_ref,
        )
        try:
            material = crypto.seal_secret(secret, associated_data)
            if crypto.guard(secret, account_guard_associated_data(record)) != record.secret_consistency_guard:
                return RegistryRefusal("secret_guard_mismatch", "配置密钥与受保护账号记录不一致")
        except Exception:
            return RegistryRefusal("crypto_unavailable", "凭据加密能力无法确认")
        values = self._version_values(
            facts,
            record,
            attempt,
            binding_id=binding_id,
            binding_version=binding_version,
            version_kind=version_kind,
            predecessor_version=predecessor_version,
            rotation_proof_ref=rotation_proof_ref,
            material=material,
            secret_guard=record.secret_consistency_guard,
            now=now,
        )
        if repository.insert_do_nothing(credential_versions, values) != 1:
            return RegistryConflict("attempt_conflict", "登记尝试已存在或版本发生冲突")
        if expected_revision is not None and current_version is not None:
            changed = repository.cas_binding(
                binding_id=binding_id,
                expected_revision=expected_revision,
                expected_version=current_version,
                next_version=binding_version,
                enabled=True,
                updated_at=now,
            )
            if changed != 1:
                return RegistryConflict("binding_changed", "凭据版本已变化，请回滚当前事务")
        return RegistrationStaged(attempt.attempt_id, binding_id, binding_version, facts.scope)

    def _version_values(
        self,
        facts: LegacyConfigFacts,
        record: ProtectedAccountRecord | None,
        attempt: ServerAttempt,
        *,
        binding_id: str,
        binding_version: int,
        version_kind: str,
        predecessor_version: int | None,
        rotation_proof_ref: str | None,
        material: SealedMaterial | None,
        now: datetime,
        secret_guard: str | None = None,
    ) -> dict[str, object]:
        policy = record.endpoint_policy if record is not None else None
        return {
            "binding_id": binding_id,
            "binding_version": binding_version,
            **facts.scope.identity(),
            "principal_id": record.principal_id if record is not None else "",
            "issuer_namespace": record.issuer_namespace if record is not None else "",
            "stable_account_subject": record.stable_account_subject if record is not None else "",
            "key_generation": record.key_generation if record is not None else "",
            "attempt_key_generation": attempt.key_generation,
            "record_id": record.record_id if record is not None else "disabled",
            "protected_record_id": attempt.protected_record_id,
            "evidence_ref": record.evidence_ref if record is not None else "disabled",
            "evidence_version": record.evidence_version if record is not None else "disabled",
            "endpoint_origin": policy.provider_origin if policy is not None else "https://disabled.invalid",
            "api_base_path": policy.api_base_path if policy is not None else "/",
            "allowed_content_paths": json.dumps(
                policy.allowed_content_paths if policy is not None else (),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "endpoint_policy_version": policy.policy_version if policy is not None else "disabled",
            "version_kind": version_kind,
            "predecessor_version": predecessor_version,
            "expected_revision": attempt.expected_revision,
            "rotation_proof_ref": rotation_proof_ref,
            "ciphertext": material.ciphertext if material is not None else None,
            "nonce": material.nonce if material is not None else None,
            "seal_key_id": material.key_id if material is not None else None,
            "seal_algorithm_version": material.algorithm_version if material is not None else None,
            "guard_key_id": record.guard_key_id if record is not None else None,
            "secret_consistency_guard": secret_guard if material is not None else None,
            "server_attempt_id": attempt.attempt_id,
            "actor_id": attempt.actor_id,
            "operation": attempt.operation,
            "proof_key_id": attempt.proof_key_id,
            "command_proof": attempt.command_proof,
            "created_at": now,
        }

    def _reconcile_prior_attempt(
        self,
        attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryConflict | None:
        factory = self._capabilities.read_session_factory
        if factory is None:
            return RegistryConflict("attempt_reconcile_unavailable", "原始登记结果无法独立确认")
        read_session: Session | None = None
        try:
            read_session = factory()
            with read_session.no_autoflush:
                row = RegistryReadRepository(read_session).find_attempt(attempt.attempt_id)
            if row is None:
                return None
            if not self._row_matches_attempt(row, attempt):
                return RegistryConflict("attempt_conflict", "登记尝试已用于不同的原始命令")
            return RegistrationStaged(
                attempt.attempt_id,
                str(row["binding_id"]),
                int(row["binding_version"]),
                attempt.scope,
            )
        except SQLAlchemyError:
            return RegistryConflict("attempt_reconcile_unavailable", "原始登记结果无法独立确认")
        finally:
            if read_session is not None:
                read_session.close()

    def _existing_attempt(
        self,
        repository: RegistryReadRepository,
        attempt: ServerAttempt,
    ) -> RegistrationStaged | RegistryConflict | None:
        row = repository.find_attempt(attempt.attempt_id)
        if row is None:
            return None
        if not self._row_matches_attempt(row, attempt):
            return RegistryConflict("attempt_conflict", "登记尝试已用于不同的原始命令")
        return RegistrationStaged(
            attempt.attempt_id,
            str(row["binding_id"]),
            int(row["binding_version"]),
            attempt.scope,
        )

    def _row_matches_attempt(self, row: dict[str, object], attempt: ServerAttempt) -> bool:
        return all(
            row.get(column) == expected
            for column, expected in (
                ("server_attempt_id", attempt.attempt_id),
                ("actor_id", attempt.actor_id),
                ("operation", attempt.operation),
                ("source", attempt.scope.source),
                ("row_id", attempt.scope.row_id),
                ("owner_or_system", attempt.scope.owner_or_system),
                ("provider", attempt.scope.provider),
                ("attempt_key_generation", attempt.key_generation),
                ("expected_revision", attempt.expected_revision),
                ("predecessor_version", attempt.predecessor_version),
                ("protected_record_id", attempt.protected_record_id),
                ("rotation_proof_ref", attempt.rotation_proof_ref),
                ("proof_key_id", attempt.proof_key_id),
                ("command_proof", attempt.command_proof),
            )
        )

    def _valid_actor(self, actor: object, attempt: object) -> bool:
        return (
            isinstance(actor, TrustedActor)
            and isinstance(attempt, ServerAttempt)
            and actor.user_id == attempt.actor_id
            and self._valid_attempt(attempt)
        )

    def _valid_attempt(self, attempt: object) -> bool:
        authority = self._capabilities.proof_authority
        return (
            isinstance(attempt, ServerAttempt)
            and authority is not None
            and attempt.verify(authority)
        )

    @staticmethod
    def _valid_register_command(attempt: ServerAttempt) -> bool:
        return (
            attempt.operation == "register"
            and attempt.expected_revision is None
            and attempt.predecessor_version is None
            and attempt.rotation_proof_ref is None
        )

    def _mutation_time(self, safe_detail: str) -> datetime | RegistryRefusal:
        try:
            return self._now()
        except Exception:
            return RegistryRefusal("clock_unavailable", safe_detail)

    def _load_enrollment(
        self,
        scope: CredentialScope,
        key_generation: str,
        *,
        now: datetime,
    ) -> ProtectedAccountRecord | RegistryRefusal:
        source = self._capabilities.enrollment_source
        if source is None or not source.ready:
            return RegistryRefusal("enrollment_unavailable", "受保护账号登记源不可用")
        try:
            record = source.load_account(scope, key_generation, now=now)
        except Exception:
            record = None
        if record is None:
            return RegistryRefusal("enrollment_unavailable", "精确受保护账号记录无法确认")
        return record

    def _record_by_id(self, record_id: str) -> ProtectedAccountRecord | None:
        source = self._capabilities.enrollment_source
        if source is None or not source.ready:
            return None
        matches = [record for record in source.records if record.record_id == record_id]
        if len(matches) != 1 or not source.verify_record(matches[0]):
            return None
        return matches[0]

    def _historical_rotation_is_verified(
        self,
        previous: ProtectedAccountRecord,
        replacement: ProtectedAccountRecord,
        proof: RotationAttestation | None,
        *,
        proof_ref: str,
        binding_id: str,
        predecessor_version: int,
    ) -> bool:
        source = self._capabilities.enrollment_source
        authority = self._capabilities.proof_authority
        if source is None or authority is None or not source.ready or proof is None:
            return False
        try:
            same_account = (
                previous.principal_id == replacement.principal_id
                and previous.scope == replacement.scope
                and previous.issuer_namespace == replacement.issuer_namespace
                and previous.stable_account_subject == replacement.stable_account_subject
                and previous.endpoint_policy == replacement.endpoint_policy
                and previous.key_generation != replacement.key_generation
            )
            proof_matches = (
                proof.proof_id == proof_ref
                and proof.original_record_id == previous.record_id
                and proof.replacement_record_id == replacement.record_id
                and proof.binding_id == binding_id
                and proof.predecessor_version == predecessor_version
                and proof.proof_key_id == authority.key_id
                and authority.verify(
                    "root-rotation-v1", proof.proof_payload(), proof.proof_signature
                )
            )
            return (
                source.verify_record(previous)
                and source.verify_record(replacement)
                and same_account
                and proof_matches
            )
        except Exception:
            return False

    def _verified_config_secret(
        self,
        facts: LegacyConfigFacts,
        record: ProtectedAccountRecord,
    ) -> str | RegistryRefusal:
        crypto = self._capabilities.crypto
        if (
            crypto is None
            or not crypto.ready()
            or not record.material_ref
            or record.guard_key_id != crypto.guard_key_id
        ):
            return RegistryRefusal("crypto_unavailable", "受保护密钥能力不可用")
        try:
            protected_secret = crypto.resolve_material(record.material_ref)
            configured_secret = crypto.resolve_legacy_value(facts.api_key)
            if not protected_secret or not configured_secret:
                return RegistryRefusal("secret_unavailable", "凭据密钥无法确认")
            expected_guard = record.secret_consistency_guard
            protected_guard = crypto.guard(protected_secret, account_guard_associated_data(record))
            configured_guard = crypto.guard(configured_secret, account_guard_associated_data(record))
            if protected_guard != expected_guard or configured_guard != expected_guard:
                return RegistryRefusal("secret_guard_mismatch", "配置密钥与受保护账号记录不一致")
            return protected_secret
        except Exception:
            return RegistryRefusal("crypto_unavailable", "受保护密钥能力不可用")

    def _config_matches_record(
        self,
        facts: LegacyConfigFacts,
        record: ProtectedAccountRecord,
    ) -> bool:
        if facts.scope != record.scope:
            return False
        try:
            policy = EndpointPolicy.from_base_url(
                facts.base_url or record.endpoint_policy.provider_origin,
                allowed_content_paths=record.endpoint_policy.allowed_content_paths,
                policy_version=record.endpoint_policy.policy_version,
            )
        except ValueError:
            return False
        return policy == record.endpoint_policy

    def _scope_from_binding(self, binding: CredentialBinding) -> CredentialScope | None:
        try:
            return CredentialScope(
                source=binding.source,  # type: ignore[arg-type]
                row_id=binding.row_id,
                owner_or_system=binding.owner_or_system,
                provider=binding.provider,
            )
        except (TypeError, ValueError):
            return None

    def _verify_resolution_chain(
        self,
        binding: CredentialBinding,
        scope: CredentialScope,
        current: dict[str, object],
        versions: tuple[dict[str, object], ...],
        *,
        now: datetime,
    ) -> dict[str, object] | None:
        if not versions:
            return None
        if not isinstance(binding.binding_version, str) or not re.fullmatch(
            r"[1-9][0-9]*", binding.binding_version
        ):
            return None
        origin_version = int(binding.binding_version)
        by_number = {int(row["binding_version"]): row for row in versions}
        head = int(current["current_version"])
        if (
            origin_version > head
            or 1 not in by_number
            or origin_version not in by_number
            or head not in by_number
            or len(by_number) != head
        ):
            return None
        if any(number != index for index, number in enumerate(sorted(by_number), start=1)):
            return None
        if any(not self._stored_attempt_is_verified(row) for row in versions):
            return None

        first = by_number[1]
        if (
            first["version_kind"] != "registered"
            or first["operation"] != "register"
            or first["predecessor_version"] is not None
            or first["expected_revision"] is not None
            or first["rotation_proof_ref"] is not None
        ):
            return None
        first_record = self._record_by_id(str(first["record_id"]))
        if first_record is None or not self._record_matches_version(first_record, first):
            return None

        record_by_version: dict[int, ProtectedAccountRecord] = {1: first_record}
        for number in range(2, head + 1):
            row = by_number[number]
            previous = by_number[number - 1]
            previous_record = record_by_version[number - 1]
            next_record = self._record_by_id(str(row["record_id"]))
            if (
                next_record is None
                or not self._record_matches_version(next_record, row)
                or row["predecessor_version"] != number - 1
                or row["expected_revision"] != number - 1
                or any(
                    row[column] != scope_value
                    for column, scope_value in scope.identity().items()
                )
            ):
                return None
            if row["version_kind"] == "rotation":
                source = self._capabilities.enrollment_source
                proof_ref = row["rotation_proof_ref"]
                proof = source.rotation_proof(str(proof_ref)) if source and proof_ref else None
                if (
                    row["operation"] != "rotate"
                    or row["principal_id"] != previous["principal_id"]
                    or row["endpoint_origin"] != previous["endpoint_origin"]
                    or row["api_base_path"] != previous["api_base_path"]
                    or row["allowed_content_paths"] != previous["allowed_content_paths"]
                    or row["endpoint_policy_version"] != previous["endpoint_policy_version"]
                ):
                    return None
                if number < origin_version:
                    verified = self._historical_rotation_is_verified(
                        previous_record,
                        next_record,
                        proof,
                        proof_ref=str(proof_ref),
                        binding_id=str(current["binding_id"]),
                        predecessor_version=number - 1,
                    )
                else:
                    verified = (
                        source is not None
                        and proof is not None
                        and source.verify_rotation(
                            previous_record,
                            next_record,
                            proof,
                            binding_id=str(current["binding_id"]),
                            predecessor_version=number - 1,
                            now=now,
                        )
                    )
                if not verified:
                    return None
            elif row["version_kind"] == "replacement":
                if (
                    row["operation"] != "replace"
                    or row["principal_id"] == previous["principal_id"]
                    or row["rotation_proof_ref"] is not None
                ):
                    return None
            else:
                return None
            record_by_version[number] = next_record

        original = by_number[origin_version]
        original_record = record_by_version[origin_version]
        try:
            paths = tuple(json.loads(str(original["allowed_content_paths"])))
        except (TypeError, ValueError):
            return None
        if original["version_kind"] != "registered" or original["predecessor_version"] is not None:
            if original["version_kind"] not in {"rotation", "replacement"}:
                return None
        if (
            original["source"] != scope.source
            or original["row_id"] != scope.row_id
            or original["owner_or_system"] != scope.owner_or_system
            or original["provider"] != scope.provider
            or original["principal_id"] != binding.original_principal_id
            or original["endpoint_origin"] != binding.endpoint_origin
            or paths != tuple(binding.allowed_content_paths)
            or binding.rotation_provenance != original["rotation_proof_ref"]
            or not self._record_matches_version(original_record, original)
        ):
            return None
        for number in range(origin_version + 1, head + 1):
            row = by_number[number]
            if (
                row["version_kind"] != "rotation"
                or row["principal_id"] != original["principal_id"]
            ):
                return None
        if original["version_kind"] == "registered" and origin_version != 1:
            return None
        return by_number[head]

    def _stored_attempt_is_verified(self, row: dict[str, object]) -> bool:
        authority = self._capabilities.proof_authority
        if authority is None:
            return False
        try:
            scope = CredentialScope(
                source=str(row["source"]),  # type: ignore[arg-type]
                row_id=str(row["row_id"]),
                owner_or_system=str(row["owner_or_system"]),
                provider=str(row["provider"]),
            )
            attempt = ServerAttempt(
                attempt_id=str(row["server_attempt_id"]),
                actor_id=str(row["actor_id"]),
                operation=str(row["operation"]),  # type: ignore[arg-type]
                scope=scope,
                key_generation=(
                    str(row["attempt_key_generation"])
                    if row["attempt_key_generation"] is not None
                    else None
                ),
                expected_revision=(
                    int(row["expected_revision"])
                    if row["expected_revision"] is not None
                    else None
                ),
                predecessor_version=(
                    int(row["predecessor_version"])
                    if row["predecessor_version"] is not None
                    else None
                ),
                protected_record_id=(
                    str(row["protected_record_id"])
                    if row["protected_record_id"] is not None
                    else None
                ),
                rotation_proof_ref=(
                    str(row["rotation_proof_ref"])
                    if row["rotation_proof_ref"] is not None
                    else None
                ),
                proof_key_id=str(row["proof_key_id"]),
                command_proof=str(row["command_proof"]),
            )
            return attempt.verify(authority)
        except (KeyError, TypeError, ValueError):
            return False

    def _record_matches_version(
        self,
        record: ProtectedAccountRecord,
        row: dict[str, object],
    ) -> bool:
        try:
            paths = tuple(json.loads(str(row["allowed_content_paths"])))
        except (TypeError, ValueError):
            return False
        return (
            row["record_id"] == record.record_id
            and row["principal_id"] == record.principal_id
            and row["source"] == record.scope.source
            and row["row_id"] == record.scope.row_id
            and row["owner_or_system"] == record.scope.owner_or_system
            and row["provider"] == record.scope.provider
            and row["issuer_namespace"] == record.issuer_namespace
            and row["stable_account_subject"] == record.stable_account_subject
            and row["key_generation"] == record.key_generation
            and row["evidence_ref"] == record.evidence_ref
            and row["evidence_version"] == record.evidence_version
            and row["endpoint_origin"] == record.endpoint_policy.provider_origin
            and row["api_base_path"] == record.endpoint_policy.api_base_path
            and paths == record.endpoint_policy.allowed_content_paths
            and row["endpoint_policy_version"] == record.endpoint_policy.policy_version
            and row["guard_key_id"] == record.guard_key_id
            and row["secret_consistency_guard"] == record.secret_consistency_guard
        )

    def _recheck_resolution(
        self,
        scope: CredentialScope,
        captured: dict[str, object],
        record: ProtectedAccountRecord,
        crypto: CredentialCrypto,
    ) -> bool:
        read_session: Session | None = None
        try:
            read_session = self._capabilities.read_session_factory()  # type: ignore[misc]
            with read_session.no_autoflush:
                repository = RegistryReadRepository(read_session)
                facts = repository.lock_config(scope)
                current = repository.lock_binding(scope)
                if (
                    current is None
                    or facts is None
                    or not current["enabled"]
                    or str(current["binding_id"]) != captured["binding_id"]
                    or int(current["revision"]) != captured["revision"]
                    or int(current["current_version"]) != captured["version"]
                    or not self._config_matches_record(facts, record)
                ):
                    return False
                current_secret = crypto.resolve_legacy_value(facts.api_key)
                if not current_secret:
                    return False
                guard = crypto.guard(current_secret, account_guard_associated_data(record))
                return guard == record.secret_consistency_guard
        except Exception:
            return False
        finally:
            if read_session is not None:
                read_session.close()

    def _sealed_material(self, row: dict[str, object]) -> SealedMaterial | None:
        try:
            if row["version_kind"] == "disabled":
                return None
            return SealedMaterial(
                ciphertext=bytes(row["ciphertext"]),
                nonce=bytes(row["nonce"]),
                key_id=str(row["seal_key_id"]),
                algorithm_version=str(row["seal_algorithm_version"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _now(self) -> datetime:
        if self._capabilities.db_clock is None:
            raise RuntimeError("credential registry UTC database clock is unavailable")
        value = self._capabilities.db_clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("credential registry clock must return an aware UTC instant")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("credential registry clock must return UTC")
        return value.astimezone(timezone.utc)

    def _new_id(self) -> str | None:
        factory = self._capabilities.id_factory or (lambda: str(uuid.uuid4()))
        try:
            value = factory()
            return str(uuid.UUID(value))
        except (TypeError, ValueError, AttributeError):
            return None


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

    from .schema import check_credential_registry_schema, check_legacy_projection_schema

    issues: list[str] = []
    schema_status = "not_ready"
    try:
        with caller_session.no_autoflush:
            foundation = check_generation_readiness(caller_session)
            if foundation.status != "ready":
                issues.extend(f"foundation:{issue.code}" for issue in foundation.issues)
            else:
                connection = caller_session.connection()
                own = check_credential_registry_schema(connection)
                legacy = check_legacy_projection_schema(connection)
                issues.extend(own.issues)
                issues.extend(legacy.issues)
                schema_status = "ready" if own.ready and legacy.ready else "not_ready"
    except SQLAlchemyError as exc:
        issues.append(f"schema_check_failed:{type(exc).__name__}")

    source = explicit_capabilities.enrollment_source
    enrollment_status = "not_ready"
    if source is not None and source.ready:
        enrollment_status = "ready"
    else:
        issues.append("protected_enrollment_not_configured")

    crypto = explicit_capabilities.crypto
    crypto_status = "not_ready"
    if crypto is not None and crypto.ready():
        if source is not None and source.ready and all(
            record.guard_key_id == crypto.guard_key_id
            and crypto.resolve_material(record.material_ref) is not None
            for record in source.records
        ):
            crypto_status = "ready"
        else:
            issues.append("protected_key_material_not_configured")
    else:
        issues.append("authenticated_crypto_not_configured")

    clock_status = (
        "ready" if _database_clock_is_ready(explicit_capabilities.db_clock) else "not_ready"
    )
    if clock_status != "ready":
        issues.append("aware_utc_database_clock_not_configured")
    return CredentialRegistryReadiness(
        schema_status,
        enrollment_status,
        crypto_status,
        clock_status,
        tuple(issues),
    )


def _database_clock_is_ready(clock: Callable[[], datetime] | None) -> bool:
    if clock is None:
        return False
    try:
        value = clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            return False
        return value.utcoffset() == timezone.utc.utcoffset(value)
    except Exception:
        return False


__all__ = (
    "CredentialRegistry",
    "CredentialRegistryReadiness",
    "RegistryCapabilities",
    "build_credential_registry",
    "check_credential_registry_readiness",
)
