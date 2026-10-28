"""Storage projections and canonical helpers for original chapter receipts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Column, DateTime, Index, MetaData, SmallInteger, String, Table, Text


metadata = MetaData()

schema_migrations = Table(
    "continuous_chapter_operation_schema_migrations",
    metadata,
    Column("version", String(64), primary_key=True),
    Column("checksum", String(64), nullable=False),
    Column("applied_at", DateTime, nullable=False),
)

operation_receipts = Table(
    "continuous_chapter_operation_receipts",
    metadata,
    Column("owner_id", String(36), primary_key=True),
    Column("operation_id", String(36), primary_key=True),
    Column("receipt_version", SmallInteger, nullable=False),
    Column("editor_session_id", String(36), nullable=False),
    Column("series_id", String(36), nullable=False),
    Column("chapter_id", String(36), nullable=False),
    Column("command_kind", String(32), nullable=False),
    Column("request_body_sha256", String(64), nullable=False),
    Column("source_snapshot_digest", String(64), nullable=False),
    Column("target_draft_id", Text, nullable=True),
    Column("target_manifest_digest", String(64), nullable=True),
    Column("outcome", String(16), nullable=False),
    Column("http_status", SmallInteger, nullable=False),
    Column("result_identity", Text, nullable=False),
    Column("canonical_response", Text, nullable=False),
    Column("result_digest", String(64), nullable=False),
    Column("committed_at", DateTime, nullable=False),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint("receipt_version = 1", name="ck_continuous_chapter_operation_receipt_version"),
    CheckConstraint("outcome IN ('committed', 'rejected')", name="ck_continuous_chapter_operation_outcome"),
    CheckConstraint("http_status >= 200 AND http_status < 500", name="ck_continuous_chapter_operation_http_status"),
)

Index(
    "ix_continuous_chapter_operation_target",
    operation_receipts.c.owner_id,
    operation_receipts.c.series_id,
    operation_receipts.c.chapter_id,
    operation_receipts.c.created_at,
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def continuous_source_snapshot_binding(request_body_sha256: str) -> str:
    """Bind the source snapshot through the exact UTF-8 operation body bytes."""

    if len(request_body_sha256) != 64 or any(character not in "0123456789abcdef" for character in request_body_sha256):
        raise ValueError("request_body_sha256 must be lowercase SHA-256 hex")
    return hashlib.sha256(
        b"haoai-continuous-source-v1\x00" + request_body_sha256.encode("ascii")
    ).hexdigest()


def parse_json_object(value: str) -> dict[str, Any]:
    parsed: Any = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("receipt JSON must contain an object")
    return parsed


def receipt_record(
    *,
    owner_id: str,
    operation_id: str,
    editor_session_id: str,
    series_id: str,
    chapter_id: str,
    command_kind: str,
    request_body_sha256: str,
    source_snapshot_digest: str,
    target_draft_id: str | None,
    target_manifest_digest: str | None,
    outcome: str,
    http_status: int,
    result_identity: dict[str, Any],
    canonical_response: dict[str, Any],
    committed_at: datetime,
) -> dict[str, Any]:
    result_identity_json = canonical_json(result_identity)
    response_json = canonical_json(canonical_response)
    return {
        "owner_id": owner_id,
        "operation_id": operation_id,
        "receipt_version": 1,
        "editor_session_id": editor_session_id,
        "series_id": series_id,
        "chapter_id": chapter_id,
        "command_kind": command_kind,
        "request_body_sha256": request_body_sha256,
        "source_snapshot_digest": source_snapshot_digest,
        "target_draft_id": target_draft_id,
        "target_manifest_digest": target_manifest_digest,
        "outcome": outcome,
        "http_status": http_status,
        "result_identity": result_identity_json,
        "canonical_response": response_json,
        "result_digest": sha256_text(response_json),
        "committed_at": committed_at,
        "created_at": committed_at,
    }
