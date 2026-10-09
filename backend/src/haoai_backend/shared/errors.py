"""Common business failures that HTTP adapters can translate."""

from __future__ import annotations


class BusinessError(Exception):
    """An expected business failure with a stable HTTP status and detail."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
