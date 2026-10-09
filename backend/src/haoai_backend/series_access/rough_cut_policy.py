"""Explicit adapter from series access decisions to the rough-cut port."""

from __future__ import annotations

from sqlalchemy.orm import Session

from haoai_backend.personal_production.rough_cut.errors import Forbidden, NotFound
from haoai_backend.personal_production.rough_cut.ports import TrustedActor

from .application import require_series_access
from .domain import ActorIdentity
from .errors import SeriesAccessDenied, SeriesNotFound
from .persistence import SqlAlchemySeriesAccessReader


def require_rough_cut_series_access(
    session: Session,
    actor: TrustedActor,
    series_id: str,
) -> None:
    reader = SqlAlchemySeriesAccessReader(session)
    identity = ActorIdentity(user_id=actor.user_id)
    try:
        require_series_access(reader, identity, series_id)
    except SeriesNotFound as exc:
        raise NotFound("剧集不存在") from exc
    except SeriesAccessDenied as exc:
        raise Forbidden(exc.detail) from exc
