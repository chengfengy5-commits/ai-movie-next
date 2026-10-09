"""Wire models matching the fixed canvas request and response DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class CanvasDocumentUpdate(BaseModel):
    document_json: dict[str, Any]
    version: int | None = None

    model_config = ConfigDict(extra="ignore")


class CanvasDocumentResponse(BaseModel):
    id: str | None = None
    chapter_id: str
    version: int
    document_json: dict[str, Any]
    updated_by: str | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
