"""Wire formatting for server-sent storyboard save events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def encode_sse(event: Mapping[str, Any]) -> str:
    return f"data: {json.dumps(dict(event), ensure_ascii=False)}\n\n"
