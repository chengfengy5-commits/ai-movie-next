"""Pure server-owned task markers shared with generic task write guards."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

FUSED_SERVER_CONTROL_MARKER = "fused_generation_runtime_v1"
_MARKER_FIELD = "server_controlled_execution"


def is_server_controlled_fused_task(task_type: object, request_data: Any) -> bool:
    """Recognize only the fixed marker written by the fused task producer."""

    if task_type != "fused":
        return False
    if isinstance(request_data, str):
        try:
            request_data = json.loads(request_data)
        except (TypeError, ValueError):
            return False
    return (
        isinstance(request_data, Mapping)
        and request_data.get(_MARKER_FIELD) == FUSED_SERVER_CONTROL_MARKER
    )
