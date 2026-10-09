"""Response conversion kept separate from reference-scanning rules."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .domain import AssetKind
from .schemas import ASSET_RESPONSE_MODELS, StoryboardAssetResponse


def asset_response(kind: AssetKind, row: Mapping[str, Any] | object) -> dict[str, Any]:
    model = ASSET_RESPONSE_MODELS[kind].model_validate(row, from_attributes=True)
    return model.model_dump(mode="python")


def storyboard_asset_response(row: Mapping[str, Any] | object) -> dict[str, Any]:
    model = StoryboardAssetResponse.model_validate(row, from_attributes=True)
    return model.model_dump(mode="python")


def parse_chapter_content_for_response(value: object) -> object:
    """Mirror the legacy route's truthy-string JSON decoding behavior."""
    if not isinstance(value, str) or not value:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []


def response_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="python")
