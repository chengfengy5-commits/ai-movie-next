"""Transaction-owning application services for generation-history routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from .errors import HistoryConflict, HistoryUnavailable
from .persistence import adopt_generation_history, read_generation_history
from .schemas import GenerationHistoryAdoptRequest

SessionFactory = Callable[[], Session]


def get_generation_history(
    session_factory: SessionFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    *,
    target_kind: str,
    frame_index: int,
    storyboard_asset_id: str | None,
    chat_mode: str,
) -> dict[str, Any]:
    session = _open_session(session_factory)
    try:
        payload = read_generation_history(
            session,
            actor,
            chapter_id,
            target_kind=target_kind,
            frame_index=frame_index,
            storyboard_asset_id=storyboard_asset_id,
            chat_mode=chat_mode,
        )
        # History reads intentionally end without committing business state.
        session.rollback()
        return payload
    except BusinessError:
        _rollback_quietly(session)
        raise
    except Exception as exc:
        _rollback_quietly(session)
        raise HistoryUnavailable() from exc
    finally:
        _close_quietly(session)


def adopt_generation_history_item(
    session_factory: SessionFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    request: GenerationHistoryAdoptRequest,
) -> dict[str, Any]:
    session = _open_session(session_factory)
    try:
        payload = adopt_generation_history(session, actor, chapter_id, request)
        session.commit()
        return payload
    except BusinessError:
        _rollback_quietly(session)
        raise
    except IntegrityError as exc:
        _rollback_quietly(session)
        raise HistoryConflict() from exc
    except Exception as exc:
        # A commit failure can leave the caller unsure whether the database
        # accepted the transaction. The client must reconcile with a new GET.
        _rollback_quietly(session)
        raise HistoryUnavailable() from exc
    finally:
        _close_quietly(session)


def _open_session(session_factory: SessionFactory | None) -> Session:
    if session_factory is None:
        raise HistoryUnavailable()
    try:
        session = session_factory()
    except Exception as exc:
        raise HistoryUnavailable() from exc
    if not isinstance(session, Session):
        _close_quietly(session)
        raise HistoryUnavailable()
    return session


def _rollback_quietly(session: Session) -> None:
    try:
        session.rollback()
    except Exception:
        pass


def _close_quietly(session: object) -> None:
    try:
        close = getattr(session, "close")
        close()
    except Exception:
        pass
