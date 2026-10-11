"""Stable internal generation error vocabulary.

HTTP adapters keep their existing response shapes and perform any mapping.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

GenerationErrorCode: TypeAlias = Literal[
    "unauthenticated",
    "membership_or_resource_forbidden",
    "invalid_generation_request",
    "key_digest_conflict",
    "inflight_conflict",
    "admission_commit_unknown",
    "batch_internal_error",
    "ownership_lost",
    "provider_create_unknown",
    "evidence_conflict_or_invalid",
    "target_conflict_deleted_stale",
    "configuration_unavailable",
    "continuation_phase_or_fence_conflict",
    "stream_commit_or_start_unknown",
    "auth_unavailable_12_policy_stop",
    "poll_reservation_conflict_or_budget_exhausted",
]


class GenerationError(Exception):
    """A safe internal classification with no implicit HTTP status mapping."""

    def __init__(self, code: GenerationErrorCode, safe_detail: str) -> None:
        super().__init__(safe_detail)
        self.code = code
        self.safe_detail = safe_detail


__all__ = ("GenerationErrorCode", "GenerationError")
