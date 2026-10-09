"""Read-only SQLAlchemy Core adapter for series access."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import SeriesAccessRecord, TeamMembership
from .ports import SeriesAccessReader
from .tables import series, team_members, users


class SqlAlchemySeriesAccessReader(SeriesAccessReader):
    """Queries current access records through the caller's existing Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load_series(self, series_id: str) -> SeriesAccessRecord | None:
        row = self._session.execute(
            select(
                series.c.id,
                series.c.user_id,
                series.c.team_id,
                series.c.claimed_by,
            ).where(series.c.id == series_id)
        ).mappings().first()
        if row is None:
            return None
        return SeriesAccessRecord(
            id=row["id"],
            user_id=row["user_id"],
            team_id=row["team_id"],
            claimed_by=row["claimed_by"],
        )

    def load_team_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembership | None:
        row = self._session.execute(
            select(team_members.c.role, team_members.c.permissions).where(
                team_members.c.team_id == team_id,
                team_members.c.user_id == user_id,
            )
        ).mappings().first()
        if row is None:
            return None
        return TeamMembership(role=row["role"], permissions=row["permissions"])

    def load_username(self, user_id: str) -> str | None:
        return self._session.execute(
            select(users.c.username).where(users.c.id == user_id)
        ).scalar_one_or_none()
