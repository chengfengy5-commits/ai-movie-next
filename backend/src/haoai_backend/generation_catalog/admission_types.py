"""Immutable, non-authoritative values for locked admission preparation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

from haoai_backend.generation_contracts.types import (
    AdmissionFingerprint,
    BatchImageReceipt,
    FrameQueuedReceipt,
    GenerationActor,
    RawAdmissionKey,
    TrustedActor,
    SingleAdmissionReceipt,
)

from .domain import ModelSelection, PromptOperation, PromptSelection


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]
CONTEXT_SCHEMA_VERSION = "legacy-admission-context-v1"
MEDIA_KEY_OPERATIONS = frozenset({"image.single", "video.single", "image.batch"})
MAX_FIELD_CHARS = 255
MAX_PROMPT_CHARS = 16_000
MAX_TEXT_TOTAL_CHARS = 256_000
MAX_REFERENCE_COUNT = 256
MAX_REFERENCE_CHARS = 8 * 1024 * 1024
MAX_REFERENCE_TOTAL_BYTES = 32 * 1024 * 1024


class AdmissionOperation(StrEnum):
    IMAGE_SINGLE = "image.single"
    VIDEO_SINGLE = "video.single"
    IMAGE_BATCH = "image.batch"
    FRAME_REVIEW = "frame.review"
    FRAME_OPTIMIZE = "frame.optimize"
    TEXT_STREAM = "text.stream"


class AdmissionContextError(ValueError):
    """The server request context is incomplete or cannot be canonicalized."""


@dataclass(frozen=True, slots=True)
class AdmissionContext:
    """Full immutable request context and its server-derived fingerprint.

    ``actor`` must come from the account-only authentication boundary. A Python
    value constructor alone does not authenticate a caller.
    """

    actor: GenerationActor
    operation: str
    raw_key: RawAdmissionKey | None = field(default=None, repr=False)
    request: Mapping[str, JSONValue] = field(default_factory=dict, repr=False)
    canonical_request: Mapping[str, JSONValue] = field(default_factory=dict, repr=False)
    fingerprint: AdmissionFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request", _freeze_object(self.request))
        object.__setattr__(self, "canonical_request", _freeze_object(self.canonical_request))

    def validate(self) -> None:
        """Recompute every client-influenced value before touching the database."""

        if not isinstance(self.actor, GenerationActor):
            raise AdmissionContextError("trusted server actor is required")
        identity = self.actor.trusted_account_identity
        if self.actor.new_admission_allowed is not None:
            raise AdmissionContextError("new-admission permission must be resolved from the locked account")
        if not isinstance(identity, TrustedActor) or not isinstance(identity.user_id, str) or not identity.user_id:
            raise AdmissionContextError("trusted account identity is invalid")
        if self.actor.user_id != identity.user_id:
            raise AdmissionContextError("actor identity does not match the trusted account")
        try:
            operation = AdmissionOperation(self.operation).value
        except (TypeError, ValueError) as exc:
            raise AdmissionContextError("unsupported admission operation") from exc
        if operation != self.operation:
            raise AdmissionContextError("operation is not canonical")
        if operation in MEDIA_KEY_OPERATIONS:
            if not isinstance(self.raw_key, RawAdmissionKey):
                raise AdmissionContextError("Idempotency-Key is required")
            _validate_raw_key(self.raw_key.raw)
        elif self.raw_key is not None:
            raise AdmissionContextError("this operation does not accept a client idempotency key")

        canonical = canonicalize_request(operation, self.request)
        if _freeze_object(canonical) != self.canonical_request:
            raise AdmissionContextError("canonical request context changed")
        expected = _fingerprint(self.actor.user_id, operation, canonical)
        if self.fingerprint != expected:
            raise AdmissionContextError("request fingerprint does not match the server context")


def build_admission_context(
    actor: GenerationActor,
    operation: str | AdmissionOperation,
    request: Mapping[str, object],
    *,
    raw_key: str | RawAdmissionKey | None = None,
) -> AdmissionContext:
    """Build an immutable context at a trusted server boundary."""

    if not isinstance(actor, GenerationActor):
        raise AdmissionContextError("trusted server actor is required")
    try:
        operation_value = AdmissionOperation(operation).value
    except (TypeError, ValueError) as exc:
        raise AdmissionContextError("unsupported admission operation") from exc
    _require_trusted_actor(actor)
    if operation_value in MEDIA_KEY_OPERATIONS:
        if isinstance(raw_key, RawAdmissionKey):
            key = raw_key
        elif isinstance(raw_key, str):
            _validate_raw_key(raw_key)
            key = RawAdmissionKey(raw=raw_key)
        else:
            raise AdmissionContextError("Idempotency-Key is required")
    elif raw_key is not None:
        raise AdmissionContextError("this operation does not accept a client idempotency key")
    else:
        key = None

    request_copy = _freeze_object(request)
    canonical = canonicalize_request(operation_value, request_copy)
    context = AdmissionContext(
        actor=actor,
        operation=operation_value,
        raw_key=key,
        request=request_copy,
        canonical_request=canonical,
        fingerprint=_fingerprint(actor.user_id, operation_value, canonical),
    )
    context.validate()
    return context


def canonicalize_request(
    operation: str | AdmissionOperation,
    request: Mapping[str, object],
) -> dict[str, object]:
    """Apply the approved operation-specific legacy normalization rules."""

    try:
        operation_value = AdmissionOperation(operation).value
    except (TypeError, ValueError) as exc:
        raise AdmissionContextError("unsupported admission operation") from exc
    if not isinstance(request, Mapping):
        raise AdmissionContextError("request must be an object")
    _reject_trusted_fields(request)
    payload = dict(request)

    if operation_value in {"image.single", "video.single"}:
        return _canonical_single(operation_value, payload)
    if operation_value == "image.batch":
        return _canonical_batch(payload)
    if operation_value == "frame.review":
        return _canonical_review(payload)
    if operation_value == "frame.optimize":
        return _canonical_optimize(payload)

    # The stream request model remains an application-boundary concern. Preserve
    # every JSON field, including an unapplied prompt_id, in its digest.
    if "model_id" in payload:
        payload["model_id"] = _string(payload["model_id"], "model_id", trim=True)
    canonical: dict[str, object] = {"operation": operation_value, **payload}
    _require_json_size(canonical)
    return canonical


def _canonical_single(operation: str, request: dict[str, object]) -> dict[str, object]:
    allowed = {
        "model_id", "prompt", "target", "size", "reference_image", "reference_images",
        "duration", "resolution", "reference_audios", "reference_videos",
    }
    if operation == "video.single":
        allowed.remove("reference_image")
    _reject_unknown_keys(request, allowed)
    model_id = _string(request.get("model_id"), "model_id", trim=True)
    prompt = _string(request.get("prompt"), "prompt", trim=True)
    if not model_id or len(model_id) > MAX_FIELD_CHARS:
        raise AdmissionContextError("model_id is invalid")
    if not prompt or len(prompt) > MAX_TEXT_TOTAL_CHARS:
        raise AdmissionContextError("prompt is invalid")
    target = _canonical_target(request.get("target"))
    size = _optional_string(request.get("size"), "size", trim=True)
    if size is not None and len(size) > MAX_FIELD_CHARS:
        raise AdmissionContextError("size is invalid")

    raw_image_values = request.get("reference_images", [])
    if raw_image_values is None:
        raw_image_values = []
    if not isinstance(raw_image_values, (list, tuple)):
        raise AdmissionContextError("reference_images must be a list")
    if operation == "image.single" and not raw_image_values and request.get("reference_image"):
        raw_image_values = [request["reference_image"]]
    images = _normalize_references(raw_image_values, "image", 64)
    refs: dict[str, list[str]] = {"reference_images": images}
    duration: int | None = None
    resolution: str | None = None
    if operation == "video.single":
        raw_duration = request.get("duration")
        if raw_duration is not None and type(raw_duration) is not int:
            raise AdmissionContextError("duration must be an integer")
        duration = raw_duration
        resolution = _optional_string(request.get("resolution"), "resolution", trim=True)
        if resolution is not None and len(resolution) > MAX_FIELD_CHARS:
            raise AdmissionContextError("resolution is invalid")
        raw_audios = request.get("reference_audios", [])
        raw_videos = request.get("reference_videos", [])
        refs["reference_audios"] = _normalize_references(
            [] if raw_audios is None else raw_audios, "audio", 5
        )
        refs["reference_videos"] = _normalize_references(
            [] if raw_videos is None else raw_videos, "video", 5
        )
    elif any(field_name in request for field_name in ("duration", "resolution", "reference_audios", "reference_videos")):
        raise AdmissionContextError("video options are not allowed for image generation")

    text_total = len(model_id) + len(prompt) + (len(size) if size else 0)
    if text_total > MAX_TEXT_TOTAL_CHARS:
        raise AdmissionContextError("request text exceeds the supported limit")
    body: dict[str, object] = {
        "operation": operation,
        "model_id": model_id,
        "prompt": prompt,
        "target": target,
        "size": size,
        "reference_images": refs["reference_images"],
    }
    if operation == "video.single":
        body.update(
            {
                "duration": duration,
                "resolution": resolution,
                "reference_audios": refs["reference_audios"],
                "reference_videos": refs["reference_videos"],
            }
        )
    _require_reference_bytes(refs)
    _require_json_size(body)
    return body


def _validate_batch_raw_limits(request: Mapping[str, object]) -> None:
    chapter_id = request.get("chapter_id")
    text_total = len(chapter_id) if isinstance(chapter_id, str) else 0
    if isinstance(chapter_id, str) and len(chapter_id) > MAX_FIELD_CHARS:
        raise AdmissionContextError("batch chapter_id exceeds the supported length")

    assets = request.get("assets")
    if not isinstance(assets, (list, tuple)) or not assets:
        raise AdmissionContextError("batch assets must be a non-empty list")
    if len(assets) > 64:
        raise AdmissionContextError("batch asset count exceeds 64")

    reference_count = 0
    reference_bytes = 0
    allowed_asset_fields = {
        "type", "id", "name", "description", "model_id", "prompt_id", "size", "reference_images"
    }
    for asset in assets:
        values = _mapping(asset, "batch asset")
        _reject_unknown_keys(values, allowed_asset_fields)
        for field_name in ("type", "id", "name", "model_id", "prompt_id", "size"):
            value = values.get(field_name, "") if field_name != "size" else values.get("size")
            if not isinstance(value, str):
                continue
            if len(value) > MAX_FIELD_CHARS:
                raise AdmissionContextError(f"batch {field_name} exceeds the supported length")
            text_total += len(value)

        description = values.get("description", "")
        if isinstance(description, str):
            if len(description) > MAX_PROMPT_CHARS:
                raise AdmissionContextError("batch item description exceeds 16000 characters")
            text_total += len(description)

        references = values.get("reference_images")
        if references is None:
            continue
        if not isinstance(references, (list, tuple)):
            continue
        if len(references) > 64:
            raise AdmissionContextError("reference_images exceed the safe count")
        reference_count += len(references)
        if reference_count > MAX_REFERENCE_COUNT:
            raise AdmissionContextError("batch reference count exceeds 256")
        for reference in references:
            if not isinstance(reference, str):
                continue
            byte_length = len(reference.encode("utf-8"))
            if byte_length > MAX_REFERENCE_CHARS:
                raise AdmissionContextError("one reference exceeds 8 MiB")
            reference_bytes += byte_length
            if reference_bytes > MAX_REFERENCE_TOTAL_BYTES:
                raise AdmissionContextError("batch reference data exceeds 32 MiB")

    if text_total > MAX_TEXT_TOTAL_CHARS:
        raise AdmissionContextError("batch text exceeds 256000 characters")


def _canonical_batch(request: dict[str, object]) -> dict[str, object]:
    _reject_unknown_keys(request, {"chapter_id", "assets"})
    _validate_batch_raw_limits(request)
    chapter_id = _string(request.get("chapter_id"), "chapter_id", trim=True)
    if not chapter_id or len(chapter_id) > MAX_FIELD_CHARS:
        raise AdmissionContextError("chapter_id is invalid")
    assets = request.get("assets")
    if not isinstance(assets, (list, tuple)) or not assets:
        raise AdmissionContextError("batch assets must be a non-empty list")
    if len(assets) > 64:
        raise AdmissionContextError("batch asset count exceeds 64")

    canonical_assets: list[dict[str, object]] = []
    seen_targets: set[tuple[str, str]] = set()
    text_total = len(chapter_id)
    reference_count = 0
    reference_bytes = 0
    allowed_asset_fields = {
        "type", "id", "name", "description", "model_id", "prompt_id", "size", "reference_images"
    }
    for asset in assets:
        values = _mapping(asset, "batch asset")
        _reject_unknown_keys(values, allowed_asset_fields)
        asset_type = _string(values.get("type"), "asset.type", trim=True)
        asset_id = _string(values.get("id"), "asset.id", trim=True)
        if not asset_type or not asset_id:
            raise AdmissionContextError("batch asset type and id are required")
        target = (asset_type, asset_id)
        if target in seen_targets:
            raise AdmissionContextError("batch contains a duplicate target")
        seen_targets.add(target)

        item = {
            "type": asset_type,
            "id": asset_id,
            "name": _string(values.get("name", ""), "asset.name", trim=True),
            "description": _string(values.get("description", ""), "asset.description", trim=True),
            "model_id": _string(values.get("model_id", ""), "asset.model_id", trim=True),
            "prompt_id": _string(values.get("prompt_id", ""), "asset.prompt_id", trim=True),
            "size": (
                None
                if values.get("size") is None
                else _string(values.get("size"), "asset.size", trim=True)
            ),
            "reference_images": _normalize_references(
                [] if values.get("reference_images") is None else values.get("reference_images"),
                "image", 64,
            ),
        }
        for field_name in ("type", "id", "name", "model_id", "prompt_id", "size"):
            value = item[field_name]
            if value is not None and len(value) > MAX_FIELD_CHARS:
                raise AdmissionContextError(f"batch {field_name} exceeds the supported length")
            if value:
                text_total += len(value)
        if len(item["description"]) > MAX_PROMPT_CHARS:
            raise AdmissionContextError("batch item description exceeds 16000 characters")
        text_total += len(item["description"])
        refs = item["reference_images"]
        reference_count += len(refs)
        reference_bytes += sum(len(value.encode("utf-8")) for value in refs)
        if reference_count > MAX_REFERENCE_COUNT:
            raise AdmissionContextError("batch reference count exceeds 256")
        if reference_bytes > MAX_REFERENCE_TOTAL_BYTES:
            raise AdmissionContextError("batch reference data exceeds 32 MiB")
        canonical_assets.append(item)
    if text_total > MAX_TEXT_TOTAL_CHARS:
        raise AdmissionContextError("batch text exceeds 256000 characters")
    result = {"operation": "image.batch", "chapter_id": chapter_id, "assets": canonical_assets}
    _require_json_size(result)
    return result


def _canonical_review(request: dict[str, object]) -> dict[str, object]:
    allowed = {
        "chapter_id", "frame_index", "prompt_id", "model_id", "message_id",
        "error_reason", "asset_context", "prev_context", "enable_thinking",
    }
    _reject_unknown_keys(request, allowed)
    chapter_id = _string(request.get("chapter_id"), "chapter_id")
    frame_index = _integer(request.get("frame_index"), "frame_index", minimum=0)
    for label, value in (("chapter_id", chapter_id), ("prompt_id", request.get("prompt_id", "")), ("model_id", request.get("model_id", "")), ("message_id", request.get("message_id", ""))):
        if len(_string(value, label)) > MAX_FIELD_CHARS:
            raise AdmissionContextError(f"{label} exceeds 255 characters")
    body = {
        "operation": "frame.review",
        "chapter_id": chapter_id,
        "frame_index": frame_index,
        "prompt_id": _string(request.get("prompt_id", ""), "prompt_id"),
        "model_id": _string(request.get("model_id", ""), "model_id", trim=True),
        "message_id": _string(request.get("message_id", ""), "message_id"),
        "error_reason": _string(request.get("error_reason", ""), "error_reason"),
        "asset_context": _string(request.get("asset_context", ""), "asset_context"),
        "prev_context": _string(request.get("prev_context", ""), "prev_context"),
        "enable_thinking": _boolean(request.get("enable_thinking", False), "enable_thinking"),
    }
    if _text_total(body) > MAX_TEXT_TOTAL_CHARS:
        raise AdmissionContextError("request text exceeds 256000 characters")
    _require_json_size(body)
    return body


def _canonical_optimize(request: dict[str, object]) -> dict[str, object]:
    allowed = {
        "chapter_id", "model_id", "prompt_id", "enable_thinking", "concurrency",
        "chapter_texts", "frames",
    }
    _reject_unknown_keys(request, allowed)
    chapter_id = _string(request.get("chapter_id"), "chapter_id")
    model_id = _string(request.get("model_id"), "model_id", trim=True)
    if not chapter_id or not model_id:
        raise AdmissionContextError("chapter_id and model_id are required")
    if any(len(value) > MAX_FIELD_CHARS for value in (chapter_id, model_id)):
        raise AdmissionContextError("optimize identity field exceeds 255 characters")
    if len(_string(request.get("prompt_id", ""), "prompt_id")) > MAX_FIELD_CHARS:
        raise AdmissionContextError("prompt_id exceeds 255 characters")
    concurrency = _integer(request.get("concurrency", 5), "concurrency", minimum=3, maximum=10)
    chapter_texts_raw = request.get("chapter_texts") or []
    if not isinstance(chapter_texts_raw, (list, tuple)):
        raise AdmissionContextError("chapter_texts must be an ordered list")
    chapter_texts = [_string(value, "chapter_texts entry") for value in chapter_texts_raw]
    frames_raw = request.get("frames")
    if not isinstance(frames_raw, (list, tuple)) or not 1 <= len(frames_raw) <= 30:
        raise AdmissionContextError("optimize frame count must be from 1 to 30")
    frames: list[dict[str, object]] = []
    seen_indices: set[int] = set()
    for value in frames_raw:
        frame = _mapping(value, "optimize frame")
        _reject_unknown_keys(frame, {"index", "text", "asset_context"})
        index = _integer(frame.get("index"), "frame.index", minimum=0)
        if index in seen_indices:
            raise AdmissionContextError("optimize frames contain a duplicate index")
        seen_indices.add(index)
        text = _string(frame.get("text"), "frame.text")
        asset_context = _string(frame.get("asset_context", ""), "frame.asset_context")
        if len(text) > MAX_PROMPT_CHARS or len(asset_context) > MAX_PROMPT_CHARS:
            raise AdmissionContextError("optimize frame text exceeds 16000 characters")
        frames.append({"index": index, "text": text, "asset_context": asset_context})
    body = {
        "operation": "frame.optimize",
        "chapter_id": chapter_id,
        "model_id": model_id,
        "prompt_id": _string(request.get("prompt_id", ""), "prompt_id"),
        "enable_thinking": _boolean(request.get("enable_thinking", False), "enable_thinking"),
        "concurrency": concurrency,
        "chapter_texts": chapter_texts,
        "frames": frames,
    }
    if _text_total(body) > MAX_TEXT_TOTAL_CHARS:
        raise AdmissionContextError("request text exceeds 256000 characters")
    _require_json_size(body)
    return body


def _canonical_target(value: object) -> dict[str, object]:
    target = _mapping(value, "target")
    kind = _string(target.get("kind"), "target.kind")
    if kind == "standalone":
        _reject_unknown_keys(target, {"kind"})
        return {"kind": kind}
    if kind == "frame_message":
        _reject_unknown_keys(target, {"kind", "chapter_id", "storyboard_asset_id"})
        return {
            "kind": kind,
            "chapter_id": _bounded_id(target.get("chapter_id"), "target.chapter_id"),
            "storyboard_asset_id": _bounded_id(target.get("storyboard_asset_id"), "target.storyboard_asset_id"),
        }
    if kind == "asset_message":
        _reject_unknown_keys(target, {"kind", "chapter_id", "asset_type", "asset_id"})
        asset_type = _string(target.get("asset_type"), "target.asset_type")
        if asset_type not in {"character", "scene", "prop", "storyboard"}:
            raise AdmissionContextError("target asset_type is unsupported")
        return {
            "kind": kind,
            "chapter_id": _bounded_id(target.get("chapter_id"), "target.chapter_id"),
            "asset_type": asset_type,
            "asset_id": _bounded_id(target.get("asset_id"), "target.asset_id"),
        }
    if kind == "canvas_apply":
        _reject_unknown_keys(target, {"kind", "destination"})
        destination = _mapping(target.get("destination"), "target.destination")
        destination_kind = _string(destination.get("kind"), "target.destination.kind")
        if destination_kind == "frame_preview":
            if "identity_mode" in destination:
                _reject_unknown_keys(
                    destination,
                    {
                        "kind",
                        "chapter_id",
                        "identity_mode",
                        "frame_index",
                        "expected_chapter_digest",
                    },
                )
                identity_mode = _string(
                    destination.get("identity_mode"),
                    "target.destination.identity_mode",
                )
                if identity_mode != "strict-no-id-v1":
                    raise AdmissionContextError(
                        "canvas frame identity mode is unsupported"
                    )
                frame_index = _integer(
                    destination.get("frame_index"),
                    "target.destination.frame_index",
                    minimum=0,
                )
                expected_chapter_digest = _string(
                    destination.get("expected_chapter_digest"),
                    "target.destination.expected_chapter_digest",
                )
                if not _is_lower_sha256(expected_chapter_digest):
                    raise AdmissionContextError(
                        "expected chapter digest must be lowercase SHA-256"
                    )
                canonical_destination = {
                    "kind": destination_kind,
                    "chapter_id": _bounded_id(
                        destination.get("chapter_id"), "target.chapter_id"
                    ),
                    "identity_mode": identity_mode,
                    "frame_index": frame_index,
                    "expected_chapter_digest": expected_chapter_digest,
                }
            else:
                _reject_unknown_keys(
                    destination, {"kind", "chapter_id", "storyboard_asset_id"}
                )
                canonical_destination = {
                    "kind": destination_kind,
                    "chapter_id": _bounded_id(
                        destination.get("chapter_id"), "target.chapter_id"
                    ),
                    "storyboard_asset_id": _bounded_id(
                        destination.get("storyboard_asset_id"),
                        "target.storyboard_asset_id",
                    ),
                }
        elif destination_kind == "asset_image":
            _reject_unknown_keys(destination, {"kind", "chapter_id", "asset_type", "asset_id"})
            asset_type = _string(destination.get("asset_type"), "target.asset_type")
            if asset_type not in {"character", "scene", "prop"}:
                raise AdmissionContextError("canvas target asset_type is unsupported")
            canonical_destination = {
                "kind": destination_kind,
                "chapter_id": _bounded_id(destination.get("chapter_id"), "target.chapter_id"),
                "asset_type": asset_type,
                "asset_id": _bounded_id(destination.get("asset_id"), "target.asset_id"),
            }
        else:
            raise AdmissionContextError("canvas target kind is unsupported")
        return {"kind": kind, "destination": canonical_destination}
    raise AdmissionContextError("target kind is unsupported")


def _normalize_references(values: object, label: str, ceiling: int) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise AdmissionContextError(f"reference_{label}s must be a list")
    if len(values) > ceiling:
        raise AdmissionContextError(f"reference_{label}s exceed the safe count")
    result: list[str] = []
    for value in values:
        reference = _string(value, f"reference_{label}", trim=True)
        if not reference:
            raise AdmissionContextError(f"reference_{label} must not be blank")
        byte_length = len(reference.encode("utf-8"))
        if byte_length > MAX_REFERENCE_CHARS:
            raise AdmissionContextError("one reference exceeds 8 MiB")
        result.append(reference)
    return result


def _require_reference_bytes(references: Mapping[str, list[str]]) -> None:
    values = [value for group in references.values() for value in group]
    if len(values) > MAX_REFERENCE_COUNT:
        raise AdmissionContextError("reference count exceeds 256")
    if sum(len(value.encode("utf-8")) for value in values) > MAX_REFERENCE_TOTAL_BYTES:
        raise AdmissionContextError("reference data exceeds 32 MiB")


def _text_total(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return sum(len(key) + _text_total(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return sum(_text_total(item) for item in value)
    return 0


def _check_text_fields(value: object, limit: int) -> None:
    if isinstance(value, str):
        if len(value) > limit:
            raise AdmissionContextError("request text exceeds the supported limit")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if len(key) > MAX_FIELD_CHARS:
                raise AdmissionContextError("request field name exceeds the supported limit")
            _check_text_fields(item, limit)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _check_text_fields(item, limit)


def _require_json_size(value: object) -> None:
    try:
        encoded = json.dumps(
            _plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdmissionContextError("request must be finite JSON") from exc
    if len(encoded) > MAX_TEXT_TOTAL_CHARS + MAX_REFERENCE_TOTAL_BYTES:
        raise AdmissionContextError("request exceeds the supported encoded size")


def _fingerprint(actor_id: str, operation: str, canonical: Mapping[str, object]) -> AdmissionFingerprint:
    return AdmissionFingerprint(
        schema_version=CONTEXT_SCHEMA_VERSION,
        ordered_nonsecret_payload_digest=_digest(canonical),
        actor_id=actor_id,
        operation=operation,
    )


def _digest(value: object) -> str:
    canonical = json.dumps(
        _plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _freeze_object(value: object) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise AdmissionContextError("request context must be an object")
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise AdmissionContextError("request context must be an object")
    return frozen


def _freeze_json(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AdmissionContextError("request contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AdmissionContextError("request keys must be strings")
            result[key] = _freeze_json(item)
        return MappingProxyType(result)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise AdmissionContextError("request contains a non-JSON value")


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _require_trusted_actor(actor: GenerationActor) -> None:
    identity = actor.trusted_account_identity
    if actor.new_admission_allowed is not None:
        raise AdmissionContextError("new-admission permission must be resolved from the locked account")
    if not isinstance(identity.user_id, str) or not identity.user_id or actor.user_id != identity.user_id:
        raise AdmissionContextError("actor identity does not match the trusted account")


def _validate_raw_key(raw: object) -> None:
    if not isinstance(raw, str) or not raw or not raw.strip() or len(raw) > MAX_FIELD_CHARS:
        raise AdmissionContextError("Idempotency-Key must contain 1 to 255 nonblank characters")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise AdmissionContextError(f"{label} must be an object")
    return dict(value)


def _reject_unknown_keys(values: Mapping[str, object], allowed: set[str]) -> None:
    unknown = set(values) - allowed
    if unknown:
        raise AdmissionContextError("request contains unsupported fields")


def _reject_trusted_fields(value: object) -> None:
    reserved = {
        "actor", "actor_id", "trusted_actor", "trusted_actor_id", "trusted_account_identity",
        "is_system", "new_admission_allowed", "config_owner", "config_owner_id", "principal",
        "original_principal_id", "credential_binding_ref", "fingerprint", "digest",
        "skip_credit_check", "skip_charge", "stream_fee_policy", "charge_phase",
    }
    if isinstance(value, Mapping):
        if any(key in reserved for key in value if isinstance(key, str)):
            raise AdmissionContextError("request cannot supply trusted admission facts")
        for item in value.values():
            _reject_trusted_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_trusted_fields(item)


def _string(value: object, label: str, *, trim: bool = False) -> str:
    if not isinstance(value, str):
        raise AdmissionContextError(f"{label} must be a string")
    return value.strip() if trim else value


def _optional_string(value: object, label: str, *, trim: bool = False) -> str | None:
    if value is None:
        return None
    result = _string(value, label, trim=trim)
    return result or None if trim else result


def _bounded_id(value: object, label: str) -> str:
    result = _string(value, label)
    if not result or len(result) > 36:
        raise AdmissionContextError(f"{label} is invalid")
    return result


def _integer(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise AdmissionContextError(f"{label} is outside the supported range")
    return value


def _is_lower_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise AdmissionContextError(f"{label} must be a boolean")
    return value


@dataclass(frozen=True, slots=True)
class LockedTargetFacts:
    kind: str
    series_id: str | None = None
    chapter_id: str | None = None
    asset_type: str | None = None
    asset_id: str | None = None
    frame_index: int | None = None
    chapter_digest: str | None = None
    asset_digest: str | None = None
    identity_mode: str | None = None
    expected_chapter_digest: str | None = None
    canonical_chapter_digest: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedPromptFacts:
    prompt_id: str
    operation: PromptOperation
    source: str
    category: str
    digest: str


def prompt_facts(selection: PromptSelection | None) -> PreparedPromptFacts | None:
    if selection is None:
        return None
    return PreparedPromptFacts(
        prompt_id=selection.prompt_id,
        operation=selection.operation,
        source=selection.source.value,
        category=selection.category,
        digest=_digest(
            {"system_prompt": selection.system_prompt, "user_prompt": selection.user_prompt}
        ),
    )


@dataclass(frozen=True, slots=True)
class PreparedItemFacts:
    index: int
    model: ModelSelection
    credential_configured: bool
    target: LockedTargetFacts
    prompt: PreparedPromptFacts | None
    quote_amount: int
    prompt_digest: str | None = None
    effective_parameters: Mapping[str, JSONValue] = field(default_factory=dict)
    reference_counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_parameters", _freeze_object(self.effective_parameters))


@dataclass(frozen=True, slots=True)
class AdmissionPreparationFacts:
    operation: str
    fingerprint: AdmissionFingerprint
    phase: str
    items: tuple[PreparedItemFacts, ...]
    quoted_total: int
    required_balance: int
    available_balance: int
    reserved_amount: int = 0
    credential_binding_status: str = "binding_unverified"
    executable: bool = False


@dataclass(frozen=True, slots=True)
class AdmissionReplay:
    fingerprint: AdmissionFingerprint
    receipt: SingleAdmissionReceipt | BatchImageReceipt | FrameQueuedReceipt


@dataclass(frozen=True, slots=True)
class AdmissionRefusal:
    status_code: int
    code: str
    message: str


AdmissionResult: TypeAlias = AdmissionPreparationFacts | AdmissionReplay | AdmissionRefusal
