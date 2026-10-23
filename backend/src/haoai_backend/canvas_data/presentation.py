"""Pure response projections for the chapter canvas API."""

from __future__ import annotations

from typing import Any

from .domain import CanvasDocumentRecord, canvas_save_receipt, parse_stored_document


def canvas_document_payload(
    record: CanvasDocumentRecord,
    *,
    document_json: object | None = None,
    echo_request: bool = False,
) -> dict[str, Any]:
    parsed = document_json if echo_request else parse_stored_document(record.document_json)
    return {
        "id": record.id,
        "chapter_id": record.chapter_id,
        "version": record.version,
        "document_json": parsed,
        "updated_by": record.updated_by,
        "updated_at": record.updated_at,
    }


def guarded_canvas_document_payload(record: CanvasDocumentRecord) -> dict[str, Any]:
    payload = canvas_document_payload(record)
    payload["save_receipt"] = canvas_save_receipt(record)
    return payload
