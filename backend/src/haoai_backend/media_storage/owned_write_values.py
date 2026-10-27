from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlsplit

from haoai_backend.generation_contracts import StoredMedia


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[^\x00-\x20\x7f]{1,256}\Z")
_SLOT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_WRITE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")


@dataclass(frozen=True, slots=True)
class OwnedObjectPlan:
    """Persistable facts for one server-planned object; this is not write authority."""

    write_id: str = field(repr=False)
    owner_ref: str = field(repr=False)
    submission_ref: str = field(repr=False)
    slot: str
    provider_result_digest: str = field(repr=False)
    material_sha256: str = field(repr=False)
    media_type: str
    size_bytes: int
    config_fingerprint: str = field(repr=False)
    config_version: int
    bucket: str = field(repr=False)
    object_key: str = field(repr=False)
    public_base: str = field(repr=False)
    plan_version: int = 1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.write_id, str)
            or not _WRITE_ID_RE.fullmatch(self.write_id)
        ):
            raise ValueError("owned object write id is invalid")
        for value in (self.owner_ref, self.submission_ref):
            if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                raise ValueError("owned object owner reference is invalid")
        if not isinstance(self.slot, str) or not _SLOT_RE.fullmatch(self.slot):
            raise ValueError("owned object slot is invalid")
        if not isinstance(self.provider_result_digest, str) or not _SHA256_RE.fullmatch(
            self.provider_result_digest
        ):
            raise ValueError("provider result digest must be a lowercase SHA256")
        if not isinstance(self.material_sha256, str) or not _SHA256_RE.fullmatch(
            self.material_sha256
        ):
            raise ValueError("material digest must be a lowercase SHA256")
        if (
            not isinstance(self.media_type, str)
            or self.media_type != self.media_type.lower()
            or ";" in self.media_type
            or self.media_type.count("/") != 1
        ):
            raise ValueError("owned object media type must be canonical")
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise ValueError("owned object size must be a positive integer")
        if (
            not isinstance(self.config_fingerprint, str)
            or not _SHA256_RE.fullmatch(self.config_fingerprint)
        ):
            raise ValueError("R2 config fingerprint must be a lowercase SHA256")
        if type(self.config_version) is not int or self.config_version < 1:
            raise ValueError("R2 config version must be positive")
        if not isinstance(self.bucket, str) or not _BUCKET_RE.fullmatch(self.bucket):
            raise ValueError("owned object bucket is invalid")
        if (
            not isinstance(self.object_key, str)
            or not self.object_key
            or self.object_key.startswith("/")
            or "\\" in self.object_key
            or any(part in {"", ".", ".."} for part in self.object_key.split("/"))
        ):
            raise ValueError("owned object key is invalid")
        try:
            parsed = urlsplit(self.public_base)
        except (TypeError, ValueError, UnicodeError):
            raise ValueError("owned object public base is invalid") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("owned object public base is invalid")
        if type(self.plan_version) is not int or self.plan_version != 1:
            raise ValueError("owned object plan version is unsupported")


@dataclass(frozen=True, slots=True)
class OwnedObjectMaterial:
    """Actual bytes paired with their immutable plan, kept out of repr and logs."""

    plan: OwnedObjectPlan
    content: bytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.plan, OwnedObjectPlan) or not isinstance(self.content, bytes):
            raise ValueError("owned object material requires a plan and immutable bytes")
        if len(self.content) != self.plan.size_bytes:
            raise ValueError("owned object material size does not match its plan")
        if hashlib.sha256(self.content).hexdigest() != self.plan.material_sha256:
            raise ValueError("owned object material digest does not match its plan")


class OwnedObjectWritePermit(Protocol):
    """One-call permission issued only after a durable first-write claim is confirmed."""

    def claim(self, plan: OwnedObjectPlan) -> bool: ...


@dataclass(frozen=True, slots=True)
class OwnedObjectPutResult:
    status: Literal["created", "already_exists", "unknown"]
    request_id: str | None = field(default=None, repr=False)
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class OwnedObjectVerification:
    status: Literal["verified", "not_found_pending", "content_conflict", "unavailable"]
    stored_media: StoredMedia | None = None
    observed_sha256: str | None = None
    observed_size_bytes: int | None = None
    request_id: str | None = field(default=None, repr=False)
    safe_detail: str = ""

    def __post_init__(self) -> None:
        if self.status == "verified":
            if self.stored_media is None:
                raise ValueError("verified media requires an actual stored-media receipt")
            if (
                self.observed_sha256 != self.stored_media.content_digest
                or self.observed_size_bytes != self.stored_media.size_bytes
            ):
                raise ValueError("verified media facts must match the streamed read")
        elif self.stored_media is not None:
            raise ValueError("unverified object outcomes cannot expose stored media")
