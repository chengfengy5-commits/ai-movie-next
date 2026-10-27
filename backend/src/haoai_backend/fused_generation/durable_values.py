"""Encrypted, versioned fused-submission payloads and durable proof inputs."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from haoai_backend.generation_contracts.types import CredentialBinding
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
    SealedMaterial,
)

from .domain import (
    FusedFrameTarget,
    FusedGenerationInlineImage,
    FusedGenerationOutput,
    FusedServerImageModel,
    PreparedFusedGeneration,
)

_SNAPSHOT_VERSION = 1
_SNAPSHOT_PROOF_PURPOSE = "fused-generation-snapshot-v1"


def seal_json_value(
    value: object,
    *,
    crypto: CredentialCrypto,
    submission_id: str,
    owner_id: str,
    request_digest: str,
    task_id: str,
    binding: CredentialBinding,
    purpose: str,
) -> tuple[str, str, str, str]:
    plaintext = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    sealed = crypto.seal_secret(
        plaintext,
        _associated_data(
            submission_id=submission_id,
            owner_id=owner_id,
            request_digest=request_digest,
            task_id=task_id,
            binding=binding,
            purpose=purpose,
        ),
    )
    return (
        base64.b64encode(sealed.ciphertext).decode("ascii"),
        base64.b64encode(sealed.nonce).decode("ascii"),
        sealed.key_id,
        sealed.algorithm_version,
    )


def open_json_value(
    ciphertext: object,
    nonce: object,
    key_id: object,
    algorithm_version: object,
    *,
    crypto: CredentialCrypto,
    submission_id: str,
    owner_id: str,
    request_digest: str,
    task_id: str,
    binding: CredentialBinding,
    purpose: str,
) -> object | None:
    if any(type(value) is not str or not value for value in (ciphertext, nonce, key_id, algorithm_version)):
        return None
    try:
        sealed = SealedMaterial(
            ciphertext=base64.b64decode(ciphertext, validate=True),
            nonce=base64.b64decode(nonce, validate=True),
            key_id=key_id,
            algorithm_version=algorithm_version,
        )
        plaintext = crypto.open_secret(
            sealed,
            _associated_data(
                submission_id=submission_id,
                owner_id=owner_id,
                request_digest=request_digest,
                task_id=task_id,
                binding=binding,
                purpose=purpose,
            ),
        )
        if plaintext is None:
            return None
        return json.loads(plaintext)
    except Exception:
        return None


def prepared_to_document(prepared: PreparedFusedGeneration) -> dict[str, object]:
    model = prepared.server_model
    return {
        "version": _SNAPSHOT_VERSION,
        "task_id": prepared.task_id,
        "message_id": prepared.message_id,
        "user_id": prepared.user_id,
        "series_id": prepared.series_id,
        "chapter_id": prepared.chapter_id,
        "member_type": prepared.member_type,
        "members": [dict(member) for member in prepared.members],
        "name": prepared.name,
        "size": prepared.size,
        "prompt": prepared.prompt,
        "reference_images": list(prepared.reference_images),
        "model_id": prepared.model_id,
        "model_name": prepared.model_name,
        "credit_cost": prepared.credit_cost,
        "server_model": {
            "id": model.id,
            "name": model.name,
            "cost_per_call": model.cost_per_call,
            "provider": model.provider,
            "wire_model": model.wire_model,
            "base_url": model.base_url,
            "params": model.params,
            "is_system": model.is_system,
        },
        "target_frame": (
            {
                "chapter_id": prepared.target_frame.chapter_id,
                "frame_index": prepared.target_frame.frame_index,
                "identity": prepared.target_frame.identity,
            }
            if prepared.target_frame is not None
            else None
        ),
        "submission_id": prepared.submission_id,
        "idempotency_key": prepared.idempotency_key,
        "request_digest": prepared.request_digest,
        "credential_binding": _binding_document(prepared.credential_binding),
    }


def prepared_from_document(value: object) -> PreparedFusedGeneration | None:
    if not isinstance(value, Mapping) or value.get("version") != _SNAPSHOT_VERSION:
        return None
    try:
        model_value = _mapping(value["server_model"])
        target_value = value["target_frame"]
        target = None
        if target_value is not None:
            target_data = _mapping(target_value)
            target = FusedFrameTarget(
                chapter_id=_text(target_data["chapter_id"]),
                frame_index=_integer(target_data["frame_index"]),
                identity=_text(target_data["identity"]),
            )
        binding_value = value["credential_binding"]
        binding = _binding_from_document(binding_value)
        members_value = value["members"]
        reference_images_value = value["reference_images"]
        if not isinstance(members_value, list) or not isinstance(reference_images_value, list):
            return None
        members = tuple(_mapping(item) for item in members_value)
        reference_images = tuple(_text(item) for item in reference_images_value)
        model = FusedServerImageModel(
            id=_text(model_value["id"]),
            name=_text(model_value["name"]),
            cost_per_call=_integer(model_value["cost_per_call"]),
            provider=_text(model_value["provider"]),
            wire_model=_optional_text(model_value["wire_model"]),
            api_key="",
            base_url=_optional_text(model_value["base_url"]),
            params=_optional_text(model_value["params"]),
            is_system=_boolean(model_value["is_system"]),
        )
        return PreparedFusedGeneration(
            task_id=_text(value["task_id"]),
            message_id=_optional_text(value["message_id"]),
            user_id=_text(value["user_id"]),
            series_id=_text(value["series_id"]),
            chapter_id=_optional_text(value["chapter_id"]),
            member_type=_text(value["member_type"]),
            members=members,
            name=_text(value["name"]),
            size=_optional_text(value["size"]),
            prompt=_text(value["prompt"]),
            reference_images=reference_images,
            model_id=_text(value["model_id"]),
            model_name=_text(value["model_name"]),
            credit_cost=_integer(value["credit_cost"]),
            server_model=model,
            target_frame=target,
            submission_id=_text(value["submission_id"]),
            idempotency_key=_optional_text(value["idempotency_key"]),
            request_digest=_text(value["request_digest"]),
            credential_binding=binding,
        )
    except (KeyError, TypeError, ValueError):
        return None


def snapshot_proof_payload(
    snapshot_digest: str,
    *,
    submission_id: str,
    owner_id: str,
    request_digest: str,
    task_id: str,
    binding: CredentialBinding,
) -> bytes:
    document = {
        "version": _SNAPSHOT_VERSION,
        "purpose": _SNAPSHOT_PROOF_PURPOSE,
        "snapshot_digest": snapshot_digest,
        "submission_id": submission_id,
        "owner_id": owner_id,
        "request_digest": request_digest,
        "task_id": task_id,
        "binding": _binding_document(binding),
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_snapshot_proof(
    authority: HmacProofAuthority,
    signature: object,
    key_id: object,
    snapshot_digest: str,
    *,
    submission_id: str,
    owner_id: str,
    request_digest: str,
    task_id: str,
    binding: CredentialBinding,
) -> bool:
    return (
        type(signature) is str
        and key_id == authority.key_id
        and authority.verify(
            _SNAPSHOT_PROOF_PURPOSE,
            snapshot_proof_payload(
                snapshot_digest,
                submission_id=submission_id,
                owner_id=owner_id,
                request_digest=request_digest,
                task_id=task_id,
                binding=binding,
            ),
            signature,
        )
    )


def json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def encode_result(output: FusedGenerationOutput | FusedGenerationInlineImage) -> tuple[dict[str, str], str]:
    if type(output) is FusedGenerationOutput:
        value = {"kind": "url", "value": output.image_url}
        return value, json_digest(value)
    value = {
        "kind": "inline",
        "media_type": output.media_type,
        "content": base64.b64encode(output.content).decode("ascii"),
    }
    return value, hashlib.sha256(output.content).hexdigest()


def decode_result(value: object) -> FusedGenerationOutput | FusedGenerationInlineImage | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("kind") == "url" and type(value.get("value")) is str:
        return FusedGenerationOutput(value["value"])
    if value.get("kind") == "inline":
        try:
            content = base64.b64decode(_text(value["content"]), validate=True)
            return FusedGenerationInlineImage(
                content=content,
                media_type=_text(value["media_type"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


def result_digest(output: FusedGenerationOutput | FusedGenerationInlineImage) -> str:
    _document, digest = encode_result(output)
    return digest


def _associated_data(
    *,
    submission_id: str,
    owner_id: str,
    request_digest: str,
    task_id: str,
    binding: CredentialBinding,
    purpose: str,
) -> bytes:
    return json.dumps(
        {
            "purpose": purpose,
            "version": _SNAPSHOT_VERSION,
            "submission_id": submission_id,
            "actor_id": owner_id,
            "request_digest": request_digest,
            "binding_id": _binding_identity(binding),
            "binding_version": binding.binding_version,
            "principal_id": binding.original_principal_id,
            "task_id": task_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _binding_document(binding: CredentialBinding | None) -> dict[str, object] | None:
    if binding is None:
        return None
    return {
        "source": binding.source,
        "row_id": binding.row_id,
        "owner_or_system": binding.owner_or_system,
        "provider": binding.provider,
        "original_principal_id": binding.original_principal_id,
        "endpoint_origin": binding.endpoint_origin,
        "allowed_content_paths": list(binding.allowed_content_paths),
        "binding_version": binding.binding_version,
        "rotation_provenance": binding.rotation_provenance,
    }


def _binding_from_document(value: object) -> CredentialBinding:
    raw = _mapping(value)
    paths = raw["allowed_content_paths"]
    if not isinstance(paths, list):
        raise TypeError("binding paths must be a list")
    return CredentialBinding(
        source=_text(raw["source"]),
        row_id=_text(raw["row_id"]),
        owner_or_system=_text(raw["owner_or_system"]),
        provider=_text(raw["provider"]),
        original_principal_id=_text(raw["original_principal_id"]),
        endpoint_origin=_text(raw["endpoint_origin"]),
        allowed_content_paths=tuple(_text(path) for path in paths),
        binding_version=_text(raw["binding_version"]),
        rotation_provenance=_optional_text(raw["rotation_provenance"]),
    )


def _binding_identity(binding: CredentialBinding) -> dict[str, str]:
    return {
        "source": binding.source,
        "row_id": binding.row_id,
        "owner_or_system": binding.owner_or_system,
        "provider": binding.provider,
    }


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("expected object")
    return value


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise TypeError("expected non-empty string")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value)


def _integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError("expected integer")
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise TypeError("expected boolean")
    return value
