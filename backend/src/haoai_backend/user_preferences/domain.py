"""Pure JSON projection helpers for stored user preferences."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Protocol


class PreferenceValue(Protocol):
    key: str
    value: str


def decode_preferences(rows: Iterable[PreferenceValue]) -> dict[str, Any]:
    """Return values as JSON when possible, keeping the original text on parse errors."""
    result: dict[str, Any] = {}
    for row in rows:
        try:
            result[row.key] = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            result[row.key] = row.value
    return result


def encode_preference(value: Any) -> str:
    """Use the legacy JSON formatting and Unicode defaults."""
    return json.dumps(value, ensure_ascii=False)
