"""Wire models matching the fixed canvas request and response DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CanvasSaveGuardRequest(BaseModel):
    mode: Literal["guarded-v1"]
    id: str | None
    version: int = Field(gt=0, strict=True)

    model_config = ConfigDict(extra="forbid", strict=True)


class CanvasDocumentUpdate(BaseModel):
    document_json: dict[str, Any]
    version: int | None = None
    save_guard: CanvasSaveGuardRequest | None = None

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def validate_guard_version(self) -> "CanvasDocumentUpdate":
        if self.save_guard is not None:
            if self.save_guard.id is None and self.save_guard.version != 1:
                raise ValueError("a new guarded document must start at version 1")
            if "version" in self.model_fields_set and self.version != self.save_guard.version:
                raise ValueError("version must match save_guard.version")
        return self

class CanvasSaveReceiptResponse(BaseModel):
    mode: Literal["guarded-v1"]
    id: str
    version: int

    model_config = ConfigDict(extra="forbid")


class CanvasDocumentResponse(BaseModel):
    id: str | None = None
    chapter_id: str
    version: int
    document_json: dict[str, Any]
    updated_by: str | None = None
    updated_at: datetime | None = None
    save_capability: Literal["guarded-v1"] | None = None
    save_receipt: CanvasSaveReceiptResponse | None = None

    model_config = ConfigDict(from_attributes=True)
