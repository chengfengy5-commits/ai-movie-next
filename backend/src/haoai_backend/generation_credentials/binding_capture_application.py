"""Pure application rules for capturing a verified registered credential binding."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from haoai_backend.generation_contracts.types import CredentialBinding

from .binding_capture_ports import (
    BindingCaptureReadStore,
    CapturedCredentialBinding,
    ConfigEndpointIdentity,
)
from .domain import (
    CredentialScope,
    EndpointPolicy,
    HmacProofAuthority,
    ProtectedAccountRecord,
    RootEnrollmentSource,
    RotationAttestation,
    ServerAttempt,
)


def record_by_id(
    source: RootEnrollmentSource | None,
    record_id: str,
) -> ProtectedAccountRecord | None:
    if source is None or not source.ready:
        return None
    matches = [record for record in source.records if record.record_id == record_id]
    if len(matches) != 1 or not source.verify_record(matches[0]):
        return None
    return matches[0]


def historical_rotation_is_verified(
    source: RootEnrollmentSource | None,
    authority: HmacProofAuthority | None,
    previous: ProtectedAccountRecord,
    replacement: ProtectedAccountRecord,
    proof: RotationAttestation | None,
    *,
    proof_ref: str,
    binding_id: str,
    predecessor_version: int,
) -> bool:
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


def config_matches_record(
    facts: ConfigEndpointIdentity,
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


def stored_attempt_is_verified(
    row: Mapping[str, object],
    authority: HmacProofAuthority | None,
) -> bool:
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


def record_matches_version(
    record: ProtectedAccountRecord,
    row: Mapping[str, object],
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


def verify_resolution_chain(
    binding: CredentialBinding,
    scope: CredentialScope,
    current: Mapping[str, object],
    versions: tuple[Mapping[str, object], ...],
    *,
    now: datetime,
    source: RootEnrollmentSource | None,
    proof_authority: HmacProofAuthority | None,
) -> Mapping[str, object] | None:
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
    if any(not stored_attempt_is_verified(row, proof_authority) for row in versions):
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
    first_record = record_by_id(source, str(first["record_id"]))
    if first_record is None or not record_matches_version(first_record, first):
        return None

    record_by_version: dict[int, ProtectedAccountRecord] = {1: first_record}
    for number in range(2, head + 1):
        row = by_number[number]
        previous = by_number[number - 1]
        previous_record = record_by_version[number - 1]
        next_record = record_by_id(source, str(row["record_id"]))
        if (
            next_record is None
            or not record_matches_version(next_record, row)
            or row["predecessor_version"] != number - 1
            or row["expected_revision"] != number - 1
            or any(
                row[column] != scope_value
                for column, scope_value in scope.identity().items()
            )
        ):
            return None
        if row["version_kind"] == "rotation":
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
                verified = historical_rotation_is_verified(
                    source,
                    proof_authority,
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
        or not record_matches_version(original_record, original)
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


@dataclass(frozen=True, slots=True)
class CredentialBindingCaptureApplication:
    """Capture binding metadata using a caller-provided store and Root evidence."""

    source: RootEnrollmentSource
    proof_authority: HmacProofAuthority

    def capture(
        self,
        store: BindingCaptureReadStore,
        scope: CredentialScope,
        *,
        now: datetime,
    ) -> CapturedCredentialBinding | None:
        config = store.lock_config_identity(scope)
        current = store.lock_binding_for_capture(scope)
        if (
            config is None
            or current is None
            or not current["enabled"]
            or type(current["revision"]) is not int
            or type(current["current_version"]) is not int
        ):
            return None

        version_number = int(current["current_version"])
        binding_id = str(current["binding_id"])
        version = store.read_capture_version(binding_id, version_number)
        if (
            version is None
            or version["binding_id"] != binding_id
            or version["binding_version"] != version_number
            or version["version_kind"] == "disabled"
        ):
            return None
        allowed_paths_value = json.loads(str(version["allowed_content_paths"]))
        if not isinstance(allowed_paths_value, list) or any(
            not isinstance(path, str) for path in allowed_paths_value
        ):
            return None
        allowed_paths = tuple(allowed_paths_value)
        binding = CredentialBinding(
            source=scope.source,
            row_id=scope.row_id,
            owner_or_system=scope.owner_or_system,
            provider=scope.provider,
            original_principal_id=str(version["principal_id"]),
            endpoint_origin=str(version["endpoint_origin"]),
            allowed_content_paths=allowed_paths,
            binding_version=str(version_number),
            rotation_provenance=(
                str(version["rotation_proof_ref"])
                if version["rotation_proof_ref"] is not None
                else None
            ),
        )
        head = verify_resolution_chain(
            binding,
            scope,
            current,
            store.read_capture_versions(binding_id),
            now=now,
            source=self.source,
            proof_authority=self.proof_authority,
        )
        if head is None or int(head["binding_version"]) != version_number:
            return None

        record = record_by_id(self.source, str(head["record_id"]))
        principal = store.find_capture_principal(str(head["principal_id"]))
        if (
            record is None
            or principal is None
            or principal["source_state"] != "valid"
            or principal["provider"] != scope.provider
            or principal["owner_scope"] != scope.owner_or_system
            or principal["issuer_namespace"] != record.issuer_namespace
            or principal["stable_account_subject"] != record.stable_account_subject
            or self.source.load_account(scope, record.key_generation, now=now) != record
        ):
            return None

        if not config_matches_record(config, record):
            return None
        configured_base_url = config.base_url or (
            record.endpoint_policy.provider_origin + record.endpoint_policy.api_base_path
        )
        endpoint_policy = EndpointPolicy.from_base_url(
            configured_base_url,
            allowed_content_paths=allowed_paths,
            policy_version=str(version["endpoint_policy_version"]),
        )
        if endpoint_policy != record.endpoint_policy:
            return None
        return CapturedCredentialBinding(
            binding_id=binding_id,
            binding=binding,
            endpoint_policy=endpoint_policy,
        )
