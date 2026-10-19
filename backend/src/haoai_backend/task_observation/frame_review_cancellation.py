"""Explicit adapter for owner-scoped registered frame.review cancellation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import OwnerTaskScope
from haoai_backend.generation_core.frame_review_cancellation import (
    CancelOutcome,
    SqlAlchemyFrameReviewCancellation,
)
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
)
from haoai_backend.shared.identity import TrustedActor

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]


@dataclass(slots=True)
class FrameReviewCancellationFacade:
    """Keep active-user authority separate from persisted recovery scope."""

    cancellation: SqlAlchemyFrameReviewCancellation

    def cancel_owned(self, actor: TrustedActor, task_id: str) -> CancelOutcome:
        if type(actor) is not TrustedActor or not isinstance(actor.user_id, str) or not actor.user_id:
            return "pending"
        return self.cancellation.cancel_owned(actor.user_id, task_id)

    def consume_persisted(self, scope: OwnerTaskScope, *, stop_signal=None) -> CancelOutcome:
        return self.cancellation.consume_persisted(scope, stop_signal=stop_signal)


def build_frame_review_cancellation_facade(
    *,
    session_factory: SessionFactory | None,
    registry_capabilities: RegistryCapabilities | None,
    db_clock: DbClock | None = None,
    id_factory: Callable[[], str] | None = None,
) -> FrameReviewCancellationFacade | None:
    """Build only from a complete, explicitly supplied persistence capability set."""

    if (
        not callable(session_factory)
        or not isinstance(registry_capabilities, RegistryCapabilities)
        or not callable(registry_capabilities.read_session_factory)
        or not isinstance(registry_capabilities.crypto, CredentialCrypto)
        or not isinstance(registry_capabilities.proof_authority, HmacProofAuthority)
    ):
        return None
    clock = db_clock or registry_capabilities.db_clock
    identifiers = id_factory or registry_capabilities.id_factory
    if not callable(clock) or not callable(identifiers):
        return None
    return FrameReviewCancellationFacade(
        SqlAlchemyFrameReviewCancellation(
            session_factory=session_factory,
            db_clock=clock,
            id_factory=identifiers,
            crypto=registry_capabilities.crypto,
            proof_authority=registry_capabilities.proof_authority,
        )
    )


__all__ = (
    "FrameReviewCancellationFacade",
    "build_frame_review_cancellation_facade",
)
