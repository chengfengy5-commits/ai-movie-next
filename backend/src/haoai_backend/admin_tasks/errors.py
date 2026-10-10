"""Expected failures exposed by administrator task queries."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AdminTaskError(BusinessError):
    """An administrator-task failure with a stable HTTP status and detail."""
