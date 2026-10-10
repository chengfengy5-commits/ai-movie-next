"""Expected failures exposed by generic task use cases."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class GenericTaskError(BusinessError):
    """A generic-task failure with the source-compatible HTTP status and detail."""
