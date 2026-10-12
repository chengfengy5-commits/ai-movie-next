"""User-card projection and profile-update use cases."""

from __future__ import annotations

from collections.abc import Collection

from haoai_backend.authentication.avatar import resolve_avatar_url

from .domain import (
    SeriesAssetCounts,
    SeriesRecord,
    TeamRecord,
    UserProfileActor,
    mask_email,
)
from .errors import UserProfileForbidden, UserProfileNotFound, UserProfilePersistenceError
from .ports import UserProfileReader, UserProfileUnitOfWork


class UserProfileApplication:
    """Coordinate user-profile rules without owning database sessions."""

    def get_user_card(
        self,
        reader: UserProfileReader,
        *,
        viewer: UserProfileActor,
        target_user_id: str,
    ) -> dict[str, object]:
        target = reader.load_user(target_user_id)
        if target is None:
            raise UserProfileNotFound("用户不存在")

        viewer_team_ids = set(reader.list_team_ids(viewer.user_id))
        if target_user_id == viewer.user_id:
            common_team_ids = viewer_team_ids
        else:
            target_team_ids = set(reader.list_team_ids(target_user_id))
            common_team_ids = viewer_team_ids & target_team_ids
            if not common_team_ids:
                raise UserProfileForbidden("无权查看该用户信息")

        teams = self._team_payloads(
            reader,
            team_ids=common_team_ids,
            target_user_id=target.id,
        )
        series, asset_totals = self._series_payloads(
            reader,
            team_ids=common_team_ids,
            target_user_id=target.id,
        )

        return {
            "user": {
                "id": target.id,
                "username": target.username,
                "avatar_url": resolve_avatar_url(target.email),
                "bio": target.bio,
                "email_masked": mask_email(target.email),
                "created_at": target.created_at,
            },
            "teams": teams,
            "series": series,
            "asset_totals": asset_totals,
        }

    def update_my_profile(
        self,
        unit_of_work: UserProfileUnitOfWork,
        *,
        actor: UserProfileActor,
        requested_bio: str | None,
    ) -> dict[str, object]:
        if requested_bio is not None and requested_bio != actor.bio:
            unit_of_work.set_bio(actor.user_id, requested_bio)

        # A successful legacy PUT commits even when it makes no bio assignment.
        unit_of_work.commit()

        current_user = unit_of_work.load_user(actor.user_id)
        if current_user is None:
            raise UserProfilePersistenceError(
                "profile user disappeared during post-commit readback"
            )

        return {
            "id": current_user.id,
            "username": current_user.username,
            "avatar_url": resolve_avatar_url(current_user.email),
            "bio": current_user.bio,
        }

    @staticmethod
    def _team_payloads(
        reader: UserProfileReader,
        *,
        team_ids: Collection[str],
        target_user_id: str,
    ) -> list[dict[str, object]]:
        teams_out: list[dict[str, object]] = []
        if not team_ids:
            return teams_out

        for team in reader.list_teams(team_ids):
            member_count = reader.count_team_members(team.id)
            membership = reader.load_team_membership(team.id, target_user_id)
            teams_out.append(
                {
                    "id": team.id,
                    "name": team.name,
                    "role": membership.role if membership is not None else "member",
                    "member_count": member_count,
                }
            )
        return teams_out

    @staticmethod
    def _series_payloads(
        reader: UserProfileReader,
        *,
        team_ids: Collection[str],
        target_user_id: str,
    ) -> tuple[list[dict[str, object]], dict[str, int]]:
        asset_totals = {
            "characters": 0,
            "scenes": 0,
            "props": 0,
            "storyboard": 0,
        }
        series_out: list[dict[str, object]] = []
        if not team_ids:
            return series_out, asset_totals

        series_records = reader.list_owned_series(target_user_id, team_ids)
        team_name_map = {team.id: team.name for team in reader.list_teams(team_ids)}
        for series_record in series_records:
            counts = reader.count_series_assets(series_record.id)
            count_values = counts.as_dict()
            for name, value in count_values.items():
                asset_totals[name] += value
            series_out.append(
                {
                    "id": series_record.id,
                    "name": series_record.name,
                    "team_id": series_record.team_id,
                    "team_name": team_name_map.get(series_record.team_id) or "",
                    "asset_counts": count_values,
                }
            )
        return series_out, asset_totals
