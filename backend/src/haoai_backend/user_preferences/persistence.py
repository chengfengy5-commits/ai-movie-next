"""SQLAlchemy ORM adapter over the supplied request Session."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .ports import PreferenceRecord
from .tables import UserPreference


class SqlAlchemyUserPreferencesUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_preferences(self, user_id: str) -> list[PreferenceRecord]:
        return (
            self._session.query(UserPreference)
            .filter(UserPreference.user_id == user_id)
            .all()
        )

    def find_preference(self, user_id: str, key: str) -> PreferenceRecord | None:
        return (
            self._session.query(UserPreference)
            .filter(
                UserPreference.user_id == user_id,
                UserPreference.key == key,
            )
            .first()
        )

    def add_preference(
        self,
        *,
        preference_id: str,
        user_id: str,
        key: str,
        value: str,
        updated_at: datetime,
    ) -> None:
        row = UserPreference()
        row.id = preference_id
        row.user_id = user_id
        row.key = key
        row.value = value
        row.updated_at = updated_at
        self._session.add(row)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
