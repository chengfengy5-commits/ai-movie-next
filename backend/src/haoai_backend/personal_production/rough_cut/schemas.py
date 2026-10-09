"""Strict HTTP request and response schemas for the rough-cut endpoints."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class RoughCutFrameUpdateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: StrictStr = Field(min_length=1, max_length=36)
    included: StrictBool


class RoughCutUpdateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt = Field(ge=0)
    frames: list[RoughCutFrameUpdateSchema] = Field(max_length=500)
