"""Load owner-protected Root enrollment bundles for the server host."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from haoai_backend.admin_system_models.crypto import LegacyFernet
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    CredentialScope,
    EndpointPolicy,
    HmacProofAuthority,
    ProtectedAccountRecord,
    RootEnrollmentSource,
    RotationAttestation,
    SealedMaterial,
    account_guard_associated_data,
)

_INPUT_FORMAT = "haoai-root-enrollment-input-v1"
_BUNDLE_FORMAT = "haoai-credential-bundle-v1"
_AEAD_VERSION = "aes-256-gcm-v1"
_MAX_FILE_BYTES = 2 * 1024 * 1024


class CredentialBundleError(ValueError):
    """A protected credential input is unavailable or invalid."""


@dataclass(frozen=True, slots=True, repr=False)
class RootCredentialResources:
    proof_authority: HmacProofAuthority
    enrollment_source: RootEnrollmentSource
    crypto: CredentialCrypto

    def __repr__(self) -> str:
        return "RootCredentialResources(<redacted>)"


def load_root_credential_resources(
    path: str | Path,
    *,
    legacy_secret_key: str,
) -> RootCredentialResources:
    """Read and validate a signed Root bundle without resolving provider material."""
    payload = _read_protected_json(path)
    return _decode_bundle(payload, legacy_secret_key=legacy_secret_key)


def load_registry_capabilities(
    path: str | Path,
    *,
    legacy_secret_key: str,
    session_factory: Callable[[], Session],
    db_clock: Callable[[], datetime],
    id_factory: Callable[[], str],
) -> RegistryCapabilities:
    resources = load_root_credential_resources(
        path,
        legacy_secret_key=legacy_secret_key,
    )
    if not all(callable(value) for value in (session_factory, db_clock, id_factory)):
        raise CredentialBundleError("credential registry host dependencies are unavailable")
    return RegistryCapabilities(
        proof_authority=resources.proof_authority,
        enrollment_source=resources.enrollment_source,
        crypto=resources.crypto,
        read_session_factory=session_factory,
        db_clock=db_clock,
        id_factory=id_factory,
    )


def provision_credential_bundle(
    input_path: str | Path,
    output_path: str | Path,
) -> int:
    """Seal explicitly supplied enrollment records into a new mode-0600 bundle."""
    try:
        source = _read_protected_json(input_path)
        bundle = _provision_payload(source)
        encoded = _canonical_json(bundle)
        _decode_bundle(bundle, legacy_secret_key="")
        _write_new_protected_file(output_path, encoded)
    except CredentialBundleError:
        raise
    except Exception:
        raise CredentialBundleError("credential input could not be provisioned") from None
    records = bundle.get("records")
    return len(records) if isinstance(records, list) else 0


def _provision_payload(source: dict[str, object]) -> dict[str, object]:
    required = {"format", "proof_key", "guard_key", "aead_key", "records"}
    if frozenset(source) not in {
        frozenset(required),
        frozenset(required | {"rotation_attestations"}),
    }:
        raise CredentialBundleError("credential input fields are invalid")
    if source.get("format") != _INPUT_FORMAT:
        raise CredentialBundleError("credential input format is unsupported")

    proof_key_id, proof_key = _decode_key(source["proof_key"], name="proof key")
    guard_key_id, guard_key = _decode_key(source["guard_key"], name="guard key")
    aead_key_id, aead_key = _decode_aead_key(source["aead_key"])
    if len(aead_key) != 32 or min(len(proof_key), len(guard_key)) < 32:
        raise CredentialBundleError("credential key material is invalid")
    if (
        len({proof_key, guard_key, aead_key}) != 3
        or len({proof_key_id, guard_key_id, aead_key_id}) != 3
    ):
        raise CredentialBundleError("credential keys must be independent")

    authority = HmacProofAuthority(proof_key_id, proof_key)
    records_value = source.get("records")
    if not isinstance(records_value, list) or not records_value:
        raise CredentialBundleError("at least one protected account record is required")

    records: list[ProtectedAccountRecord] = []
    materials: dict[str, dict[str, str]] = {}
    record_ids: set[str] = set()
    material_refs: set[str] = set()
    for entry in records_value:
        raw = _mapping(entry)
        expected = {
            "record_id",
            "principal_id",
            "scope",
            "issuer_namespace",
            "stable_account_subject",
            "key_generation",
            "material_ref",
            "provider_secret",
            "evidence_ref",
            "evidence_version",
            "valid_from",
            "valid_until",
            "revoked_at",
            "endpoint_policy",
        }
        _require_keys(raw, expected)
        record_id = _text(raw["record_id"], "record id")
        principal_id = _canonical_uuid(raw["principal_id"], "principal id")
        material_ref = _text(raw["material_ref"], "material reference")
        secret = raw["provider_secret"]
        if not isinstance(secret, str) or not secret.strip():
            raise CredentialBundleError("provider material is invalid")
        if record_id in record_ids or material_ref in material_refs:
            raise CredentialBundleError("credential record identities must be unique")
        record_ids.add(record_id)
        material_refs.add(material_ref)

        scope = _scope(raw["scope"])
        endpoint = _endpoint(raw["endpoint_policy"])
        candidate = ProtectedAccountRecord(
            record_id=record_id,
            principal_id=principal_id,
            scope=scope,
            issuer_namespace=_text(raw["issuer_namespace"], "issuer namespace"),
            stable_account_subject=_text(raw["stable_account_subject"], "account subject"),
            key_generation=_text(raw["key_generation"], "key generation"),
            material_ref=material_ref,
            evidence_ref=_text(raw["evidence_ref"], "evidence reference"),
            evidence_version=_text(raw["evidence_version"], "evidence version"),
            valid_from=_datetime(raw["valid_from"], "valid from"),
            valid_until=_optional_datetime(raw["valid_until"], "valid until"),
            revoked_at=_optional_datetime(raw["revoked_at"], "revoked at"),
            endpoint_policy=endpoint,
            guard_key_id=guard_key_id,
            secret_consistency_guard="0" * 64,
            proof_key_id=proof_key_id,
            proof_signature="0" * 64,
        )
        guard = _consistency_guard(guard_key, secret, account_guard_associated_data(candidate))
        guarded = replace(candidate, secret_consistency_guard=guard)
        record = replace(
            guarded,
            proof_signature=authority.sign("root-enrollment-v1", guarded.proof_payload()),
        )
        sealed = _seal_secret(aead_key, secret, _material_aad(material_ref), aead_key_id)
        records.append(record)
        materials[material_ref] = _sealed_to_mapping(sealed)

    rotations_value = source.get("rotation_attestations", [])
    if not isinstance(rotations_value, list):
        raise CredentialBundleError("Root rotation records are invalid")
    rotations = [_rotation_mapping(_rotation(item)) for item in rotations_value]
    bundle: dict[str, object] = {
        "format": _BUNDLE_FORMAT,
        "proof_key": {"key_id": proof_key_id, "key_base64": _encode_key(proof_key)},
        "guard_key": {"key_id": guard_key_id, "key_base64": _encode_key(guard_key)},
        "aead_key": {
            "key_id": aead_key_id,
            "algorithm_version": _AEAD_VERSION,
            "key_base64": _encode_key(aead_key),
        },
        "records": [_record_mapping(record) for record in records],
        "rotation_attestations": rotations,
        "materials": materials,
    }
    return bundle


def _decode_bundle(payload: dict[str, object], *, legacy_secret_key: str) -> RootCredentialResources:
    try:
        _require_keys(
            payload,
            {
                "format",
                "proof_key",
                "guard_key",
                "aead_key",
                "records",
                "rotation_attestations",
                "materials",
            },
        )
        if payload["format"] != _BUNDLE_FORMAT:
            raise CredentialBundleError("credential bundle format is unsupported")

        proof_key_id, proof_key = _decode_key(payload["proof_key"], name="proof key")
        guard_key_id, guard_key = _decode_key(payload["guard_key"], name="guard key")
        aead_key_id, aead_key = _decode_aead_key(payload["aead_key"])
        aead_data = _mapping(payload["aead_key"])
        if (
            len(aead_key) != 32
            or min(len(proof_key), len(guard_key)) < 32
            or len({proof_key, guard_key, aead_key}) != 3
            or len({proof_key_id, guard_key_id, aead_key_id}) != 3
            or aead_data.get("algorithm_version") != _AEAD_VERSION
        ):
            raise CredentialBundleError("credential key material is invalid")

        authority = HmacProofAuthority(proof_key_id, proof_key)
        records_value = payload["records"]
        if not isinstance(records_value, list) or not records_value:
            raise CredentialBundleError("protected account records are unavailable")
        records = tuple(_record(item) for item in records_value)
        if any(
            record.proof_key_id != proof_key_id or record.guard_key_id != guard_key_id
            for record in records
        ):
            raise CredentialBundleError("protected account key binding is invalid")
        source = RootEnrollmentSource(records, authority, tuple(
            _rotation(item) for item in _sequence(payload["rotation_attestations"])
        ))
        if not source.ready:
            raise CredentialBundleError("Root account signatures are invalid")
        for rotation in source.rotation_attestations:
            if (
                rotation.proof_key_id != proof_key_id
                or rotation.original_record_id not in {record.record_id for record in records}
                or rotation.replacement_record_id not in {record.record_id for record in records}
                or not authority.verify(
                    "root-rotation-v1", rotation.proof_payload(), rotation.proof_signature
                )
            ):
                raise CredentialBundleError("Root rotation signatures are invalid")

        raw_materials = _mapping(payload["materials"])
        materials: dict[str, SealedMaterial] = {}
        for reference, value in raw_materials.items():
            if not isinstance(reference, str) or not reference.strip():
                raise CredentialBundleError("credential material reference is invalid")
            material_data = _mapping(value)
            _require_keys(material_data, {"ciphertext_base64", "nonce_base64", "key_id", "algorithm_version"})
            sealed = SealedMaterial(
                ciphertext=_decode_base64(material_data["ciphertext_base64"]),
                nonce=_decode_base64(material_data["nonce_base64"]),
                key_id=_text(material_data["key_id"], "material key id"),
                algorithm_version=_text(material_data["algorithm_version"], "material algorithm"),
            )
            if sealed.key_id != aead_key_id or sealed.algorithm_version != _AEAD_VERSION or len(sealed.nonce) != 12:
                raise CredentialBundleError("credential material metadata is invalid")
            materials[reference] = sealed
        if {record.material_ref for record in records} != set(materials):
            raise CredentialBundleError("protected account material is missing")

        legacy = LegacyFernet(legacy_secret_key)

        def resolve_material(reference: str) -> str | None:
            sealed = materials.get(reference)
            if sealed is None:
                return None
            return _unseal_secret(aead_key, sealed, _material_aad(reference))

        def resolve_legacy(stored_value: str) -> str | None:
            try:
                value = legacy.decrypt(stored_value)
            except Exception:
                return None
            return value if isinstance(value, str) and value else None

        crypto = CredentialCrypto(
            seal_key_id=aead_key_id,
            guard_key_id=guard_key_id,
            algorithm_version=_AEAD_VERSION,
            seal=lambda secret, aad: _seal_secret(aead_key, secret, aad, aead_key_id),
            unseal=lambda sealed, aad: _unseal_secret(aead_key, sealed, aad),
            consistency_guard=lambda secret, aad: _consistency_guard(guard_key, secret, aad),
            resolve_secret_handle=resolve_material,
            resolve_legacy_config_value=resolve_legacy,
            self_test=lambda: _crypto_self_test(
                aead_key, guard_key, aead_key_id
            ),
        )
        if not crypto.ready():
            raise CredentialBundleError("authenticated credential crypto is unavailable")
        return RootCredentialResources(authority, source, crypto)
    except CredentialBundleError:
        raise
    except Exception:
        raise CredentialBundleError("credential bundle is invalid") from None


def _record(value: object) -> ProtectedAccountRecord:
    data = _mapping(value)
    _require_keys(data, {
        "record_id", "principal_id", "scope", "issuer_namespace", "stable_account_subject",
        "key_generation", "material_ref", "evidence_ref", "evidence_version", "valid_from",
        "valid_until", "revoked_at", "endpoint_policy", "guard_key_id",
        "secret_consistency_guard", "proof_key_id", "proof_signature",
    })
    return ProtectedAccountRecord(
        record_id=_text(data["record_id"], "record id"),
        principal_id=_canonical_uuid(data["principal_id"], "principal id"),
        scope=_scope(data["scope"]),
        issuer_namespace=_text(data["issuer_namespace"], "issuer namespace"),
        stable_account_subject=_text(data["stable_account_subject"], "account subject"),
        key_generation=_text(data["key_generation"], "key generation"),
        material_ref=_text(data["material_ref"], "material reference"),
        evidence_ref=_text(data["evidence_ref"], "evidence reference"),
        evidence_version=_text(data["evidence_version"], "evidence version"),
        valid_from=_datetime(data["valid_from"], "valid from"),
        valid_until=_optional_datetime(data["valid_until"], "valid until"),
        revoked_at=_optional_datetime(data["revoked_at"], "revoked at"),
        endpoint_policy=_endpoint(data["endpoint_policy"]),
        guard_key_id=_text(data["guard_key_id"], "guard key id"),
        secret_consistency_guard=_text(data["secret_consistency_guard"], "secret guard"),
        proof_key_id=_text(data["proof_key_id"], "proof key id"),
        proof_signature=_text(data["proof_signature"], "proof signature"),
    )


def _record_mapping(record: ProtectedAccountRecord) -> dict[str, object]:
    return {
        "record_id": record.record_id,
        "principal_id": record.principal_id,
        "scope": record.scope.identity(),
        "issuer_namespace": record.issuer_namespace,
        "stable_account_subject": record.stable_account_subject,
        "key_generation": record.key_generation,
        "material_ref": record.material_ref,
        "evidence_ref": record.evidence_ref,
        "evidence_version": record.evidence_version,
        "valid_from": record.valid_from.isoformat(),
        "valid_until": record.valid_until.isoformat() if record.valid_until else None,
        "revoked_at": record.revoked_at.isoformat() if record.revoked_at else None,
        "endpoint_policy": record.endpoint_policy.identity(),
        "guard_key_id": record.guard_key_id,
        "secret_consistency_guard": record.secret_consistency_guard,
        "proof_key_id": record.proof_key_id,
        "proof_signature": record.proof_signature,
    }


def _scope(value: object) -> CredentialScope:
    data = _mapping(value)
    _require_keys(data, {"source", "row_id", "owner_or_system", "provider"})
    source = _text(data["source"], "scope source")
    row_id = _canonical_uuid(data["row_id"], "scope row id")
    owner = _text(data["owner_or_system"], "scope owner")
    if source == "user":
        owner = _canonical_uuid(owner, "scope owner")
    return CredentialScope(
        source=source,  # type: ignore[arg-type]
        row_id=row_id,
        owner_or_system=owner,
        provider=_text(data["provider"], "scope provider"),
    )


def _endpoint(value: object) -> EndpointPolicy:
    data = _mapping(value)
    _require_keys(data, {"provider_origin", "api_base_path", "allowed_content_paths", "policy_version"})
    paths = data["allowed_content_paths"]
    if not isinstance(paths, (list, tuple)) or any(
        not isinstance(path, str) for path in paths
    ):
        raise CredentialBundleError("endpoint policy is invalid")
    return EndpointPolicy(
        provider_origin=_text(data["provider_origin"], "provider origin"),
        api_base_path=_text(data["api_base_path"], "API base path"),
        allowed_content_paths=tuple(paths),
        policy_version=_text(data["policy_version"], "endpoint policy version"),
    )


def _rotation(value: object) -> RotationAttestation:
    data = _mapping(value)
    _require_keys(data, {
        "proof_id", "original_record_id", "replacement_record_id", "binding_id",
        "predecessor_version", "proof_key_id", "proof_signature",
    })
    predecessor_version = data["predecessor_version"]
    if type(predecessor_version) is not int:
        raise CredentialBundleError("rotation predecessor version is invalid")
    return RotationAttestation(
        proof_id=_text(data["proof_id"], "rotation proof id"),
        original_record_id=_text(data["original_record_id"], "original record id"),
        replacement_record_id=_text(data["replacement_record_id"], "replacement record id"),
        binding_id=_text(data["binding_id"], "rotation binding id"),
        predecessor_version=predecessor_version,
        proof_key_id=_text(data["proof_key_id"], "rotation proof key id"),
        proof_signature=_text(data["proof_signature"], "rotation signature"),
    )


def _rotation_mapping(rotation: RotationAttestation) -> dict[str, object]:
    return {
        "proof_id": rotation.proof_id,
        "original_record_id": rotation.original_record_id,
        "replacement_record_id": rotation.replacement_record_id,
        "binding_id": rotation.binding_id,
        "predecessor_version": rotation.predecessor_version,
        "proof_key_id": rotation.proof_key_id,
        "proof_signature": rotation.proof_signature,
    }


def _decode_key(value: object, *, name: str) -> tuple[str, bytes]:
    data = _mapping(value)
    _require_keys(data, {"key_id", "key_base64"})
    return _text(data["key_id"], f"{name} id"), _decode_base64(data["key_base64"])


def _decode_aead_key(value: object) -> tuple[str, bytes]:
    data = _mapping(value)
    _require_keys(data, {"key_id", "key_base64", "algorithm_version"})
    if data.get("algorithm_version") != _AEAD_VERSION:
        raise CredentialBundleError("AEAD algorithm is unsupported")
    return _text(data["key_id"], "AEAD key id"), _decode_base64(data["key_base64"])


def _seal_secret(key: bytes, secret: str, associated_data: bytes, key_id: str) -> SealedMaterial:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), associated_data)
    return SealedMaterial(ciphertext, nonce, key_id, _AEAD_VERSION)


def _unseal_secret(key: bytes, sealed: SealedMaterial, associated_data: bytes) -> str | None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if sealed.algorithm_version != _AEAD_VERSION or len(sealed.nonce) != 12:
        return None
    try:
        raw = AESGCM(key).decrypt(sealed.nonce, sealed.ciphertext, associated_data)
        value = raw.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError):
        return None
    return value if value else None


def _consistency_guard(key: bytes, secret: str, associated_data: bytes) -> str:
    message = b"credential-consistency-v1\0" + associated_data + b"\0" + secret.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _material_aad(reference: str) -> bytes:
    return _canonical_json({"purpose": "root-enrollment-material-v1", "material_ref": reference})


def _crypto_self_test(key: bytes, guard_key: bytes, key_id: str) -> bool:
    try:
        aad = b"credential-crypto-self-test-v1"
        material = _seal_secret(key, "self-test", aad, key_id)
        if _unseal_secret(key, material, aad) != "self-test":
            return False
        if _unseal_secret(key, material, aad + b"-wrong") is not None:
            return False
        altered = replace(material, ciphertext=material.ciphertext[:-1] + bytes([material.ciphertext[-1] ^ 1]))
        if _unseal_secret(key, altered, aad) is not None:
            return False
        return _consistency_guard(guard_key, "self-test", aad) != _consistency_guard(
            guard_key, "self-test", aad + b"-wrong"
        )
    except Exception:
        return False


def _read_protected_json(path: str | Path) -> dict[str, object]:
    try:
        absolute = Path(path)
        if not absolute.is_absolute():
            raise CredentialBundleError("credential file path must be absolute")
        fd = os.open(
            absolute,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except CredentialBundleError:
        raise
    except Exception:
        raise CredentialBundleError("credential file is unavailable") from None
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or mode & 0o077
            or mode & ~0o600
            or not mode & 0o400
            or info.st_size > _MAX_FILE_BYTES
        ):
            raise CredentialBundleError("credential file permissions or size are unsafe")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, _MAX_FILE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_FILE_BYTES:
                raise CredentialBundleError("credential file is too large")
            chunks.append(chunk)
    except CredentialBundleError:
        raise
    except Exception:
        raise CredentialBundleError("credential file could not be read safely") from None
    finally:
        os.close(fd)
    try:
        payload = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except Exception:
        raise CredentialBundleError("credential JSON is invalid") from None
    if not isinstance(payload, dict):
        raise CredentialBundleError("credential JSON must be an object")
    return payload


def _write_new_protected_file(path: str | Path, content: bytes) -> None:
    absolute = Path(path)
    if not absolute.is_absolute():
        raise CredentialBundleError("output credential path must be absolute")
    try:
        fd = os.open(
            absolute,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        raise CredentialBundleError("credential output already exists") from None
    except Exception:
        raise CredentialBundleError("credential output is unavailable") from None
    identity: tuple[int, int] | None = None
    succeeded = False
    try:
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
        ):
            raise CredentialBundleError("credential output permissions are unsafe")
        os.fchmod(fd, 0o600)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            raise CredentialBundleError("credential output permissions are unsafe")
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise CredentialBundleError("credential output could not be written")
            view = view[written:]
        os.fsync(fd)
        succeeded = True
    except CredentialBundleError:
        raise
    except Exception:
        raise CredentialBundleError("credential output could not be written") from None
    finally:
        os.close(fd)
        if not succeeded and identity is not None:
            try:
                current = os.stat(absolute, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == identity:
                    os.unlink(absolute)
            except OSError:
                pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON constant: {value}")


def _require_keys(value: dict[str, object], required: set[str]) -> None:
    if set(value) != required:
        raise CredentialBundleError("credential object fields are invalid")


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CredentialBundleError("credential object is invalid")
    return value


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise CredentialBundleError("credential record list is invalid")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CredentialBundleError(f"{name} is invalid")
    return value


def _decode_base64(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise CredentialBundleError("credential encoding is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise CredentialBundleError("credential encoding is invalid") from None
    if base64.b64encode(decoded).decode("ascii") != value:
        raise CredentialBundleError("credential encoding is not canonical")
    return decoded


def _canonical_uuid(value: object, name: str) -> str:
    text = _text(value, name)
    try:
        normalized = str(uuid.UUID(text))
    except (ValueError, AttributeError):
        raise CredentialBundleError(f"{name} must be a canonical UUID") from None
    if text != normalized:
        raise CredentialBundleError(f"{name} must be a canonical UUID")
    return text


def _encode_key(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _sealed_to_mapping(value: SealedMaterial) -> dict[str, str]:
    return {
        "ciphertext_base64": _encode_key(value.ciphertext),
        "nonce_base64": _encode_key(value.nonce),
        "key_id": value.key_id,
        "algorithm_version": value.algorithm_version,
    }


def _datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise CredentialBundleError(f"{name} is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise CredentialBundleError(f"{name} is invalid") from None
    if result.tzinfo is None or result.utcoffset() != timezone.utc.utcoffset(result):
        raise CredentialBundleError(f"{name} must be UTC")
    return result.astimezone(timezone.utc)


def _optional_datetime(value: object, name: str) -> datetime | None:
    return None if value is None else _datetime(value, name)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise CredentialBundleError("credential data is not serializable") from None


__all__ = (
    "CredentialBundleError",
    "RootCredentialResources",
    "load_registry_capabilities",
    "load_root_credential_resources",
    "provision_credential_bundle",
)
