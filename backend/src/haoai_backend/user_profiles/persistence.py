"""SQLAlchemy adapter for user-profile reads and the bio-only write."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session, registry

from haoai_backend.authentication.tables import users

from .domain import SeriesAssetCounts, SeriesRecord, TeamMembership, TeamRecord, UserProfileRecord
from .errors import UserProfilePersistenceError
from .ports import UserProfileUnitOfWork
from .tables import characters, props, scenes, series, storyboard_assets, team_members, teams


class _ProfileUser:
    pass


_profile_mapper_registry = registry()
_profile_mapper_registry.map_imperatively(_ProfileUser, users)


class SqlAlchemyUserProfileUnitOfWork(UserProfileUnitOfWork):
    """Run profile queries through the request-scoped business Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load_user(self, user_id: str) -> UserProfileRecord | None:
        with self._session.no_autoflush:
            row = self._session.execute(
                select(
                    users.c.id,
                    users.c.username,
                    users.c.email,
                    users.c.bio,
                    users.c.created_at,
                ).where(users.c.id == user_id)
            ).mappings().first()
        if row is None:
            return None
        return UserProfileRecord(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            bio=row["bio"],
            created_at=row["created_at"],
        )

    def list_team_ids(self, user_id: str) -> list[str]:
        with self._session.no_autoflush:
            values = self._session.execute(
                select(team_members.c.team_id).where(team_members.c.user_id == user_id)
            ).scalars().all()
        return list(values)

    def list_teams(self, team_ids: Collection[str]) -> list[TeamRecord]:
        if not team_ids:
            return []
        with self._session.no_autoflush:
            rows = self._session.execute(
                select(teams.c.id, teams.c.name).where(teams.c.id.in_(team_ids))
            ).mappings().all()
        return [TeamRecord(id=row["id"], name=row["name"]) for row in rows]

    def count_team_members(self, team_id: str) -> int:
        with self._session.no_autoflush:
            return int(
                self._session.execute(
                    select(func.count())
                    .select_from(team_members)
                    .where(team_members.c.team_id == team_id)
                ).scalar_one()
            )

    def load_team_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembership | None:
        with self._session.no_autoflush:
            row = self._session.execute(
                select(team_members.c.role).where(
                    team_members.c.team_id == team_id,
                    team_members.c.user_id == user_id,
                )
            ).first()
        if row is None:
            return None
        return TeamMembership(role=row[0])

    def list_owned_series(
        self,
        user_id: str,
        team_ids: Collection[str],
    ) -> list[SeriesRecord]:
        if not team_ids:
            return []
        with self._session.no_autoflush:
            rows = self._session.execute(
                select(series.c.id, series.c.name, series.c.team_id).where(
                    series.c.user_id == user_id,
                    series.c.team_id.in_(team_ids),
                )
            ).mappings().all()
        return [
            SeriesRecord(
                id=row["id"],
                name=row["name"],
                team_id=row["team_id"],
            )
            for row in rows
        ]

    def count_series_assets(self, series_id: str) -> SeriesAssetCounts:
        return SeriesAssetCounts(
            characters=self._count_assets(characters, series_id),
            scenes=self._count_assets(scenes, series_id),
            props=self._count_assets(props, series_id),
            storyboard=self._count_assets(storyboard_assets, series_id),
        )

    def set_bio(self, user_id: str, bio: str) -> None:
        with self._session.no_autoflush:
            user = self._session.get(_ProfileUser, user_id)
        if user is None:
            raise UserProfilePersistenceError(
                "profile user disappeared before bio assignment"
            )
        user.bio = bio

    def commit(self) -> None:
        self._session.commit()

    def _count_assets(self, table: Table, series_id: str) -> int:
        with self._session.no_autoflush:
            return int(
                self._session.execute(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.series_id == series_id)
                ).scalar_one()
            )
