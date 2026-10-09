"""Series access use case, independent of HTTP and persistence frameworks."""

from __future__ import annotations

from .domain import ActorIdentity, can_enter_claimed_series
from .errors import SeriesAccessDenied, SeriesNotFound
from .ports import SeriesAccessReader

CLAIMED_SERIES_DENIED = "该剧集已由「{name}」负责制作，暂不可进入"
SERIES_ACCESS_DENIED = "无权访问该剧集"
SERIES_NOT_FOUND = "剧集不存在"


def require_series_access(
    reader: SeriesAccessReader,
    actor: ActorIdentity,
    series_id: str,
) -> None:
    series = reader.load_series(series_id)
    if series is None:
        raise SeriesNotFound(SERIES_NOT_FOUND)

    if series.team_id and series.claimed_by and series.claimed_by != actor.user_id:
        claim_membership = reader.load_team_membership(series.team_id, actor.user_id)
        if not can_enter_claimed_series(claim_membership):
            claimed_name = reader.load_username(series.claimed_by) or ""
            raise SeriesAccessDenied(CLAIMED_SERIES_DENIED.format(name=claimed_name))

    if series.user_id == actor.user_id:
        return

    if series.team_id:
        # This is deliberately a fresh query after the claim gate. Membership
        # can change between the two legacy decision phases.
        final_membership = reader.load_team_membership(series.team_id, actor.user_id)
        if final_membership is not None:
            return

    raise SeriesAccessDenied(SERIES_ACCESS_DENIED)
