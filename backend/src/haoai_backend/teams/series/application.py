"""Team-series use cases with source-compatible transaction boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams.domain import TeamMembershipRecord, TeamRecord
from haoai_backend.teams import (
    ShareSeriesRequest,
    TeamBadRequest,
    TeamForbidden,
    TeamNotFound,
    TeamSeriesCreate,
    TeamsUnavailable,
    TransferRequest,
)
from .domain import clamp_series_page, series_list_item, total_pages
from .ports import SeriesUnitOfWork, SeriesUnitOfWorkFactory


_Result = TypeVar("_Result")


def _run(
    uow_factory: SeriesUnitOfWorkFactory | None,
    operation: Callable[[SeriesUnitOfWork], _Result],
) -> _Result:
    if uow_factory is None:
        raise TeamsUnavailable()
    uow = uow_factory()
    try:
        uow.ensure_clean()
        return operation(uow)
    except Exception:
        try:
            uow.rollback()
        except Exception:
            pass
        raise
    finally:
        uow.close()


def _require_team(uow: SeriesUnitOfWork, team_id: str) -> TeamRecord:
    team = uow.load_team(team_id)
    if team is None:
        raise TeamNotFound()
    return team


def _require_member(
    uow: SeriesUnitOfWork, team_id: str, user_id: str
) -> TeamMembershipRecord:
    membership = uow.load_membership(team_id, user_id)
    if membership is None:
        raise TeamForbidden()
    return membership


def _require_series_in_team(
    uow: SeriesUnitOfWork,
    team_id: str,
    series_id: str,
) -> Mapping[str, Any]:
    series = uow.load_series(series_id, team_id=team_id)
    if series is None:
        raise TeamNotFound("剧集不存在或不在该团队")
    return series


def list_team_series(
    uow_factory: SeriesUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    *,
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    def execute(uow: SeriesUnitOfWork) -> dict[str, Any]:
        team = _require_team(uow, team_id)
        _require_member(uow, team.id, actor.user_id)
        normalized_page, normalized_size = clamp_series_page(page, page_size)
        snapshot = uow.list_team_series_page(
            team.id, normalized_page, normalized_size
        )
        return {
            "items": [
                series_list_item(row, snapshot.users, snapshot.counts)
                for row in snapshot.rows
            ],
            "total": snapshot.total,
            "page": normalized_page,
            "page_size": normalized_size,
            "total_pages": total_pages(snapshot.total, normalized_size),
        }

    return _run(uow_factory, execute)


def create_team_series(
    uow_factory: SeriesUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    body: TeamSeriesCreate,
) -> dict[str, Any]:
    def execute(uow: SeriesUnitOfWork) -> dict[str, Any]:
        team = _require_team(uow, team_id)
        _require_member(uow, team.id, actor.user_id)
        series_id = uow.new_id()
        claimed_at = uow.now_utc_naive() if body.claim else None
        uow.stage_series_insert(
            {
                "id": series_id,
                "user_id": actor.user_id,
                "name": body.name.strip(),
                "description": body.description,
                "image_url": body.image_url,
                "style_prompt_id": body.style_prompt_id,
                "team_id": team.id,
                "claimed_by": actor.user_id if body.claim else None,
                "claimed_at": claimed_at,
                "created_at": uow.now_utc_naive(),
                "updated_at": uow.now_utc_naive(),
            },
        )
        uow.commit()
        saved = uow.refresh_series(series_id)
        if saved is None:
            raise RuntimeError("committed team series could not be read back")
        return {
            "id": saved["id"],
            "name": saved["name"],
            "team_id": saved["team_id"],
            "user_id": saved["user_id"],
            "style_prompt_id": saved["style_prompt_id"],
        }

    return _run(uow_factory, execute)


def share_series_to_team(
    uow_factory: SeriesUnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    body: ShareSeriesRequest,
) -> dict[str, Any]:
    def execute(uow: SeriesUnitOfWork) -> dict[str, Any]:
        series = uow.load_series(series_id)
        if series is None:
            raise TeamNotFound("剧集不存在")
        if series["user_id"] != actor.user_id:
            raise TeamForbidden("只能分享自己创建的剧集")
        team = _require_team(uow, body.team_id)
        _require_member(uow, body.team_id, actor.user_id)

        assigned: dict[str, Any] = {"team_id": team.id}
        if body.claim:
            assigned.update(
                claimed_by=actor.user_id,
                claimed_at=uow.now_utc_naive(),
            )
        uow.stage_series_assignment(series, assigned)
        uow.release_series_locks(series_id)
        uow.commit()

        saved_team = uow.refresh_team(team.id)
        if saved_team is None:
            raise RuntimeError("committed team could not be read back")
        return {
            "message": f"已分享到团队「{saved_team.name}」",
            "team_id": saved_team.id,
            "team_name": saved_team.name,
        }

    return _run(uow_factory, execute)


def unshare_series(
    uow_factory: SeriesUnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
) -> dict[str, str]:
    def execute(uow: SeriesUnitOfWork) -> dict[str, str]:
        series = uow.load_series(series_id)
        if series is None:
            raise TeamNotFound("剧集不存在")
        if series["user_id"] != actor.user_id:
            raise TeamForbidden("只能操作自己创建的剧集")
        uow.stage_series_assignment(series, {"team_id": None})
        uow.release_series_locks(series_id)
        uow.commit()
        return {"message": "已移出团队，恢复为个人剧集"}

    return _run(uow_factory, execute)


def claim_series(
    uow_factory: SeriesUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    series_id: str,
) -> dict[str, Any]:
    def execute(uow: SeriesUnitOfWork) -> dict[str, Any]:
        team = _require_team(uow, team_id)
        _require_member(uow, team.id, actor.user_id)
        series = _require_series_in_team(uow, team.id, series_id)
        if series["claimed_by"] and series["claimed_by"] != actor.user_id:
            raise TeamBadRequest("该剧集已被其他成员认领")

        uow.stage_series_assignment(
            series,
            {
                "claimed_by": actor.user_id,
                "claimed_at": uow.now_utc_naive(),
            },
        )
        uow.commit()
        saved = uow.refresh_series(series_id)
        if saved is None:
            raise RuntimeError("committed team series could not be read back")
        return {
            "message": "认领成功",
            "claimed_by": saved["claimed_by"],
            "claimed_at": saved["claimed_at"],
        }

    return _run(uow_factory, execute)


def unclaim_series(
    uow_factory: SeriesUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    series_id: str,
) -> dict[str, str]:
    def execute(uow: SeriesUnitOfWork) -> dict[str, str]:
        team = _require_team(uow, team_id)
        _require_member(uow, team.id, actor.user_id)
        series = _require_series_in_team(uow, team.id, series_id)
        membership = uow.load_membership(team.id, actor.user_id)
        is_owner_or_captain = (
            series["user_id"] == actor.user_id
            or (membership is not None and membership.role == "owner")
        )
        if not (
            series["claimed_by"] == actor.user_id or is_owner_or_captain
        ):
            raise TeamForbidden("无权限取消该认领")
        if not series["claimed_by"]:
            return {"message": "该剧集尚未被认领"}

        uow.stage_series_assignment(
            series,
            {"claimed_by": None, "claimed_at": None},
        )
        uow.commit()
        return {"message": "已取消认领"}

    return _run(uow_factory, execute)


def transfer_series_claim(
    uow_factory: SeriesUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    series_id: str,
    body: TransferRequest,
) -> dict[str, str]:
    def execute(uow: SeriesUnitOfWork) -> dict[str, str]:
        team = _require_team(uow, team_id)
        _require_member(uow, team.id, actor.user_id)
        series = _require_series_in_team(uow, team.id, series_id)
        membership = uow.load_membership(team.id, actor.user_id)
        is_owner_or_captain = (
            series["user_id"] == actor.user_id
            or (membership is not None and membership.role == "owner")
        )
        if not (
            series["claimed_by"] == actor.user_id or is_owner_or_captain
        ):
            raise TeamForbidden("无权限转交该剧集")
        if body.user_id == actor.user_id:
            raise TeamBadRequest("不能转交给自己")
        if uow.load_membership(team.id, body.user_id) is None:
            raise TeamBadRequest("接收人不是该团队成员")

        uow.stage_series_assignment(
            series,
            {
                "claimed_by": body.user_id,
                "claimed_at": uow.now_utc_naive(),
            },
        )
        uow.commit()
        return {"message": "已转交认领", "claimed_by": body.user_id}

    return _run(uow_factory, execute)
