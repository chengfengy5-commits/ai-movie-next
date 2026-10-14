"""Typed client-facing request errors for chat media admission."""

from __future__ import annotations


class ChatMediaRequestError(ValueError):
    """A safe client-visible request validation failure."""

    status_code = 422
