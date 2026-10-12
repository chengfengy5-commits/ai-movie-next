"""Typed values and explicit proof boundaries for generation credentials."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

class RegistryValueError(ValueError):
    """A registry value cannot be represented safely."""


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise RegistryValueError(f"{name} is required")
    if value != value.strip() or any(ord(char) < 32 for char in value):
        raise RegistryValueError(f"{name} is not canonical")
    return value


def _require_utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RegistryValueError(f"{name} must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    if value.utcoffset().total_seconds() != 0:
        raise RegistryValueError(f"{name} must use UTC")
    return normalized


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RegistryValueError("value is not canonical JSON") from exc


def _safe_path(value: object, name: str) -> str:
    path = _require_text(value, name)
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(char in path for char in "?#")
        or "%" in path
        or "*" in path
        or any(ord(char) < 32 for char in path)
    ):
        raise RegistryValueError(f"{name} is unsafe")
    segments = path.split("/")[1:]
    if any(segment in {".", ".."} for segment in segments):
        raise RegistryValueError(f"{name} contains a traversal segment")
    if "//" in path:
        raise RegistryValueError(f"{name} has an empty path segment")
    return path


@dataclass(frozen=True, slots=True)
class CredentialScope:
    source: Literal["system", "user"]
    row_id: str
    owner_or_system: str
    provider: str

    def __post_init__(self) -> None:
        if self.source not in {"system", "user"}:
            raise RegistryValueError("credential source is invalid")
        _require_text(self.row_id, "credential row id")
        _require_text(self.provider, "credential provider")
        _require_text(self.owner_or_system, "credential owner")
        if self.source == "system" and self.owner_or_system != "system":
            raise RegistryValueError("system credentials require the system owner")
        if self.source == "user" and self.owner_or_system == "system":
            raise RegistryValueError("user credentials require an account owner")

    def identity(self) -> dict[str, str]:
        return {
            "source": self.source,
            "row_id": self.row_id,
            "owner_or_system": self.owner_or_system,
            "provider": self.provider,
        }

    def key(self) -> tuple[str, str, str, str]:
        return (self.source, self.row_id, self.owner_or_system, self.provider)


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    provider_origin: str = field(repr=False)
    api_base_path: str = field(repr=False)
    allowed_content_paths: tuple[str, ...] = field(default=(), repr=False)
    policy_version: str = "endpoint-policy-v1"

    def __post_init__(self) -> None:
        origin = _require_text(self.provider_origin, "provider origin")
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or origin != f"{parsed.scheme}://{parsed.netloc}"
        ):
            raise RegistryValueError("provider origin must not contain a path or credentials")
        _safe_path(self.api_base_path, "API base path")
        paths = tuple(_safe_path(path, "content path") for path in self.allowed_content_paths)
        if len(paths) != len(set(paths)):
            raise RegistryValueError("content paths must be unique")
        _require_text(self.policy_version, "endpoint policy version")
        object.__setattr__(self, "allowed_content_paths", paths)

    @classmethod
    def from_base_url(
        cls,
        base_url: str,
        *,
        allowed_content_paths: tuple[str, ...] = (),
        policy_version: str = "endpoint-policy-v1",
    ) -> EndpointPolicy:
        value = _require_text(base_url, "provider base URL")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise RegistryValueError("provider base URL is unsafe")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path or "/"
        return cls(
            provider_origin=origin,
            api_base_path=_safe_path(path, "API base path"),
            allowed_content_paths=allowed_content_paths,
            policy_version=policy_version,
        )

    def identity(self) -> dict[str, object]:
        return {
            "provider_origin": self.provider_origin,
            "api_base_path": self.api_base_path,
            "allowed_content_paths": self.allowed_content_paths,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True, slots=True)
class HmacProofAuthority:
    """Root-injected HMAC authority; keys never come from environment defaults."""

    key_id: str
    key: bytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_text(self.key_id, "proof key id")
        if not isinstance(self.key, bytes) or len(self.key) < 32:
            raise RegistryValueError("proof key is unavailable")

    def sign(self, purpose: str, payload: bytes) -> str:
        _require_text(purpose, "proof purpose")
        if not isinstance(payload, bytes):
            raise RegistryValueError("proof payload must be bytes")
        message = purpose.encode("utf-8") + b"\0" + payload
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

    def verify(self, purpose: str, payload: bytes, signature: str) -> bool:
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            return False
        return hmac.compare_digest(self.sign(purpose, payload), signature)


@dataclass(frozen=True, slots=True)
class ProtectedAccountRecord:
    record_id: str
    principal_id: str
    scope: CredentialScope
    issuer_namespace: str
    stable_account_subject: str
    key_generation: str
    material_ref: str = field(repr=False)
    evidence_ref: str = field(repr=False)
    evidence_version: str
    valid_from: datetime
    valid_until: datetime | None
    revoked_at: datetime | None
    endpoint_policy: EndpointPolicy
    guard_key_id: str
    secret_consistency_guard: str = field(repr=False)
    proof_key_id: str
    proof_signature: str = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("record_id", "issuer_namespace", "stable_account_subject", "key_generation",
                     "evidence_version", "guard_key_id", "proof_key_id"):
            _require_text(getattr(self, name), name)
        for name in ("material_ref", "evidence_ref"):
            _require_text(getattr(self, name), name)
        try:
            uuid.UUID(self.principal_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise RegistryValueError("principal id must name a protected account record") from exc
        object.__setattr__(self, "valid_from", _require_utc(self.valid_from, "enrollment valid_from"))
        if self.valid_until is not None:
            object.__setattr__(self, "valid_until", _require_utc(self.valid_until, "enrollment valid_until"))
        if self.revoked_at is not None:
            object.__setattr__(self, "revoked_at", _require_utc(self.revoked_at, "enrollment revoked_at"))
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise RegistryValueError("enrollment validity window is empty")
        if not isinstance(self.scope, CredentialScope) or not isinstance(self.endpoint_policy, EndpointPolicy):
            raise RegistryValueError("protected account scope or endpoint policy is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.secret_consistency_guard):
            raise RegistryValueError("protected account guard is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.proof_signature):
            raise RegistryValueError("protected account proof is invalid")

    def proof_payload(self) -> bytes:
        return _canonical_json({
            "record_id": self.record_id,
            "principal_id": self.principal_id,
            "scope": self.scope.identity(),
            "issuer_namespace": self.issuer_namespace,
            "stable_account_subject": self.stable_account_subject,
            "key_generation": self.key_generation,
            "material_ref": self.material_ref,
            "evidence_ref": self.evidence_ref,
            "evidence_version": self.evidence_version,
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "endpoint_policy": self.endpoint_policy.identity(),
            "guard_key_id": self.guard_key_id,
            "secret_consistency_guard": self.secret_consistency_guard,
            "proof_key_id": self.proof_key_id,
        })


@dataclass(frozen=True, slots=True)
class RotationAttestation:
    proof_id: str
    original_record_id: str
    replacement_record_id: str
    binding_id: str
    predecessor_version: int
    proof_key_id: str
    proof_signature: str = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("proof_id", "original_record_id", "replacement_record_id",
                     "binding_id", "proof_key_id"):
            _require_text(getattr(self, name), name)
        if type(self.predecessor_version) is not int or self.predecessor_version < 1:
            raise RegistryValueError("rotation predecessor version is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.proof_signature):
            raise RegistryValueError("rotation proof is invalid")

    def proof_payload(self) -> bytes:
        return _canonical_json({
            "proof_id": self.proof_id,
            "original_record_id": self.original_record_id,
            "replacement_record_id": self.replacement_record_id,
            "binding_id": self.binding_id,
            "predecessor_version": self.predecessor_version,
            "proof_key_id": self.proof_key_id,
        })


@dataclass(frozen=True, slots=True)
class RootEnrollmentSource:
    """Immutable Root-bound record set; a callable or client DTO is not a source."""

    records: tuple[ProtectedAccountRecord, ...]
    authority: HmacProofAuthority
    rotation_attestations: tuple[RotationAttestation, ...] = ()

    def __post_init__(self) -> None:
        records = tuple(self.records)
        if any(not isinstance(record, ProtectedAccountRecord) for record in records):
            raise RegistryValueError("protected account record set is invalid")
        ids = [record.record_id for record in records]
        if len(ids) != len(set(ids)):
            raise RegistryValueError("protected account record ids must be unique")
        if not isinstance(self.authority, HmacProofAuthority):
            raise RegistryValueError("Root proof authority is required")
        rotations = tuple(self.rotation_attestations)
        if any(not isinstance(proof, RotationAttestation) for proof in rotations):
            raise RegistryValueError("Root rotation record set is invalid")
        rotation_ids = [proof.proof_id for proof in rotations]
        if len(rotation_ids) != len(set(rotation_ids)):
            raise RegistryValueError("Root rotation proof ids must be unique")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "rotation_attestations", rotations)

    def rotation_proof(self, proof_id: str) -> RotationAttestation | None:
        matches = [proof for proof in self.rotation_attestations if proof.proof_id == proof_id]
        return matches[0] if len(matches) == 1 else None

    @property
    def ready(self) -> bool:
        return bool(self.records) and all(
            record.proof_key_id == self.authority.key_id
            and self.authority.verify("root-enrollment-v1", record.proof_payload(), record.proof_signature)
            for record in self.records
        )

    def verify_record(self, record: ProtectedAccountRecord) -> bool:
        return (
            isinstance(record, ProtectedAccountRecord)
            and record.proof_key_id == self.authority.key_id
            and self.authority.verify("root-enrollment-v1", record.proof_payload(), record.proof_signature)
        )

    def load_account(
        self,
        scope: CredentialScope,
        key_generation: str,
        *,
        now: datetime,
    ) -> ProtectedAccountRecord | None:
        if not isinstance(scope, CredentialScope) or not self.ready:
            return None
        instant = _require_utc(now, "enrollment check time")
        matches = [
            record for record in self.records
            if record.scope == scope
            and record.key_generation == key_generation
            and self.verify_record(record)
            and record.valid_from <= instant
            and (record.valid_until is None or instant < record.valid_until)
            and (record.revoked_at is None or instant < record.revoked_at)
        ]
        return matches[0] if len(matches) == 1 else None

    def verify_rotation(
        self,
        original: ProtectedAccountRecord,
        replacement: ProtectedAccountRecord,
        proof: RotationAttestation,
        *,
        binding_id: str,
        predecessor_version: int,
        now: datetime,
    ) -> bool:
        if not all(isinstance(value, ProtectedAccountRecord) for value in (original, replacement)):
            return False
        if not isinstance(proof, RotationAttestation):
            return False
        records_valid = (
            self.verify_record(original)
            and self.load_account(replacement.scope, replacement.key_generation, now=now) == replacement
        )
        same_account = (
            original.principal_id == replacement.principal_id
            and original.scope == replacement.scope
            and original.issuer_namespace == replacement.issuer_namespace
            and original.stable_account_subject == replacement.stable_account_subject
            and original.endpoint_policy == replacement.endpoint_policy
            and original.key_generation != replacement.key_generation
        )
        proof_matches = (
            proof.original_record_id == original.record_id
            and proof.replacement_record_id == replacement.record_id
            and proof.binding_id == binding_id
            and proof.predecessor_version == predecessor_version
            and proof.proof_key_id == self.authority.key_id
            and self.authority.verify("root-rotation-v1", proof.proof_payload(), proof.proof_signature)
        )
        return records_valid and same_account and proof_matches


@dataclass(frozen=True, slots=True)
class ServerAttempt:
    attempt_id: str
    actor_id: str
    operation: Literal["register", "rotate", "replace", "disable"]
    scope: CredentialScope
    key_generation: str | None
    expected_revision: int | None
    predecessor_version: int | None
    protected_record_id: str | None
    rotation_proof_ref: str | None
    proof_key_id: str
    command_proof: str = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("attempt_id", "actor_id", "proof_key_id"):
            _require_text(getattr(self, name), name)
        if self.operation not in {"register", "rotate", "replace", "disable"}:
            raise RegistryValueError("registry operation is invalid")
        if not isinstance(self.scope, CredentialScope):
            raise RegistryValueError("registry scope is invalid")
        if self.key_generation is not None:
            _require_text(self.key_generation, "key generation")
        if self.protected_record_id is not None:
            _require_text(self.protected_record_id, "protected record id")
        if self.rotation_proof_ref is not None:
            _require_text(self.rotation_proof_ref, "rotation proof reference")
        if self.expected_revision is not None and (
            type(self.expected_revision) is not int or self.expected_revision < 1
        ):
            raise RegistryValueError("expected binding revision is invalid")
        if self.predecessor_version is not None and (
            type(self.predecessor_version) is not int or self.predecessor_version < 1
        ):
            raise RegistryValueError("predecessor version is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.command_proof):
            raise RegistryValueError("registry command proof is invalid")

    def command_payload(self) -> bytes:
        return _canonical_json({
            "attempt_id": self.attempt_id,
            "actor_id": self.actor_id,
            "operation": self.operation,
            "scope": self.scope.identity(),
            "key_generation": self.key_generation,
            "expected_revision": self.expected_revision,
            "predecessor_version": self.predecessor_version,
            "protected_record_id": self.protected_record_id,
            "rotation_proof_ref": self.rotation_proof_ref,
            "proof_key_id": self.proof_key_id,
        })

    def verify(self, authority: HmacProofAuthority) -> bool:
        return (
            isinstance(authority, HmacProofAuthority)
            and self.proof_key_id == authority.key_id
            and authority.verify("registry-attempt-v1", self.command_payload(), self.command_proof)
        )


def make_server_attempt(
    authority: HmacProofAuthority,
    *,
    attempt_id: str,
    actor_id: str,
    operation: Literal["register", "rotate", "replace", "disable"],
    scope: CredentialScope,
    key_generation: str | None = None,
    expected_revision: int | None = None,
    predecessor_version: int | None = None,
    protected_record_id: str | None = None,
    rotation_proof_ref: str | None = None,
) -> ServerAttempt:
    unsigned = ServerAttempt(
        attempt_id=attempt_id,
        actor_id=actor_id,
        operation=operation,
        scope=scope,
        key_generation=key_generation,
        expected_revision=expected_revision,
        predecessor_version=predecessor_version,
        protected_record_id=protected_record_id,
        rotation_proof_ref=rotation_proof_ref,
        proof_key_id=authority.key_id,
        command_proof="0" * 64,
    )
    return ServerAttempt(
        attempt_id=unsigned.attempt_id,
        actor_id=unsigned.actor_id,
        operation=unsigned.operation,
        scope=unsigned.scope,
        key_generation=unsigned.key_generation,
        expected_revision=unsigned.expected_revision,
        predecessor_version=unsigned.predecessor_version,
        protected_record_id=unsigned.protected_record_id,
        rotation_proof_ref=unsigned.rotation_proof_ref,
        proof_key_id=unsigned.proof_key_id,
        command_proof=authority.sign("registry-attempt-v1", unsigned.command_payload()),
    )


@dataclass(frozen=True, slots=True)
class SealedMaterial:
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    key_id: str
    algorithm_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.ciphertext, bytes) or not self.ciphertext:
            raise RegistryValueError("sealed credential is unavailable")
        if not isinstance(self.nonce, bytes) or not self.nonce:
            raise RegistryValueError("sealed credential nonce is unavailable")
        _require_text(self.key_id, "credential seal key id")
        _require_text(self.algorithm_version, "credential algorithm version")


SealFunction = Callable[[str, bytes], SealedMaterial]
UnsealFunction = Callable[[SealedMaterial, bytes], str | None]
GuardFunction = Callable[[str, bytes], str]


@dataclass(frozen=True, slots=True)
class CredentialCrypto:
    """Explicit Root-provided authenticated crypto; missing capabilities stay unavailable."""

    seal_key_id: str
    guard_key_id: str
    algorithm_version: str
    seal: SealFunction = field(repr=False, compare=False)
    unseal: UnsealFunction = field(repr=False, compare=False)
    consistency_guard: GuardFunction = field(repr=False, compare=False)
    resolve_secret_handle: Callable[[str], str | None] = field(repr=False, compare=False)
    resolve_legacy_config_value: Callable[[str], str | None] = field(repr=False, compare=False)
    self_test: Callable[[], bool] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("seal_key_id", "guard_key_id", "algorithm_version"):
            _require_text(getattr(self, name), name)
        if not all(
            callable(value)
            for value in (
                self.seal,
                self.unseal,
                self.consistency_guard,
                self.resolve_secret_handle,
                self.resolve_legacy_config_value,
                self.self_test,
            )
        ):
            raise RegistryValueError("authenticated credential crypto is unavailable")

    def ready(self) -> bool:
        try:
            return self.self_test() is True
        except Exception:
            return False

    def seal_secret(self, secret: str, associated_data: bytes) -> SealedMaterial:
        result = self.seal(secret, associated_data)
        if not isinstance(result, SealedMaterial) or result.key_id != self.seal_key_id:
            raise RegistryValueError("credential sealing is unavailable")
        return result

    def open_secret(self, sealed: SealedMaterial, associated_data: bytes) -> str | None:
        if sealed.key_id != self.seal_key_id or sealed.algorithm_version != self.algorithm_version:
            return None
        result = self.unseal(sealed, associated_data)
        return result if isinstance(result, str) and result else None

    def guard(self, secret: str, associated_data: bytes) -> str | None:
        value = self.consistency_guard(secret, associated_data)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            return None
        return value

    def resolve_material(self, material_ref: str) -> str | None:
        value = self.resolve_secret_handle(material_ref)
        return value if isinstance(value, str) and value else None

    def resolve_legacy_value(self, stored_value: str) -> str | None:
        value = self.resolve_legacy_config_value(stored_value)
        return value if isinstance(value, str) and value else None


def credential_associated_data(
    scope: CredentialScope,
    *,
    binding_id: str,
    binding_version: int,
    principal_id: str,
    key_generation: str,
    evidence_ref: str,
    purpose: str = "provider-credential-material",
) -> bytes:
    return _canonical_json({
        "purpose": purpose,
        "scope": scope.identity(),
        "binding_id": binding_id,
        "binding_version": binding_version,
        "principal_id": principal_id,
        "key_generation": key_generation,
        "evidence_ref": evidence_ref,
    })



def account_guard_associated_data(record: ProtectedAccountRecord) -> bytes:
    return _canonical_json({
        "purpose": "legacy-config-consistency-v1",
        "record_id": record.record_id,
        "scope": record.scope.identity(),
        "issuer_namespace": record.issuer_namespace,
        "key_generation": record.key_generation,
        "endpoint_policy": record.endpoint_policy.identity(),
    })


@dataclass(frozen=True, slots=True)
class RegistryRefusal:
    code: str
    safe_detail: str


@dataclass(frozen=True, slots=True)
class RegistryConflict:
    code: str
    safe_detail: str


@dataclass(frozen=True, slots=True)
class RegistrationStaged:
    attempt_id: str
    binding_id: str
    binding_version: int
    scope: CredentialScope


@dataclass(frozen=True, slots=True)
class ConfirmedRegistration:
    attempt_id: str
    actor_id: str
    operation: str
    scope: CredentialScope
    binding_id: str
    binding_version: int
    principal_id: str
    command_proof: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RegistrationCommitUnknown:
    attempt_id: str
    safe_detail: str


@dataclass(frozen=True, slots=True)
class PreparedPayload:
    actor_id: str
    operation: str
    server_attempt_id: str
    scope: CredentialScope
    binding_id: str
    binding_version: int
    principal_id: str
    context_fingerprint: str
    schema_version: str
    projector_version: str
    reference_policy_version: str
    target_ref: str
    model_ref: str
    grant_ref: str
    command_proof: str
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    encryption_key_id: str

    def __post_init__(self) -> None:
        for name in ("actor_id", "operation", "server_attempt_id", "binding_id",
                     "principal_id", "context_fingerprint", "schema_version",
                     "projector_version", "reference_policy_version", "target_ref",
                     "model_ref", "grant_ref", "encryption_key_id"):
            _require_text(getattr(self, name), name)
        if not isinstance(self.scope, CredentialScope):
            raise RegistryValueError("prepared payload scope is invalid")
        if type(self.binding_version) is not int or self.binding_version < 1:
            raise RegistryValueError("prepared payload binding version is invalid")
        if not isinstance(self.ciphertext, bytes) or not self.ciphertext:
            raise RegistryValueError("prepared payload ciphertext is unavailable")
        if not isinstance(self.nonce, bytes) or not self.nonce:
            raise RegistryValueError("prepared payload nonce is unavailable")
        if not re.fullmatch(r"[0-9a-f]{64}", self.command_proof):
            raise RegistryValueError("prepared payload command proof is invalid")


@dataclass(frozen=True, slots=True)
class PreparedPayloadStaged:
    prepared_payload_id: str
    reused: bool
