"""SQLAlchemy adapter for legacy administrator user operations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import UserRecord
from .ports import AdminUsersUnitOfWork
from .tables import (
    AssetDuplicateExclusionRow,
    ChapterRow,
    ChatMessageRow,
    CharacterRow,
    CreditLogRow,
    ModelConfigRow,
    PropRow,
    PromptConfigRow,
    SceneRow,
    SeriesRow,
    StoryboardAssetRow,
    UserCreditRow,
    UserRow,
)


class SqlAlchemyAdminUsersUnitOfWork(AdminUsersUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def username_exists(self, username: str) -> bool:
        return (
            self._session.query(UserRow)
            .filter(UserRow.username == username)
            .first()
            is not None
        )

    def email_exists(self, email: str) -> bool:
        return (
            self._session.query(UserRow)
            .filter(UserRow.email == email)
            .first()
            is not None
        )

    def create_user(
        self,
        *,
        username: str,
        email: str,
        hashed_password: str,
        is_superuser: bool,
        membership_type: str,
        membership_expires_at: Any,
    ) -> UserRow:
        row = UserRow(
            id=str(uuid4()),
            username=username,
            email=email,
            hashed_password=hashed_password,
            is_superuser=is_superuser,
            membership_type=membership_type,
            membership_expires_at=membership_expires_at,
        )
        self._session.add(row)
        return row

    def create_user_credit(self, user_id: str, credits: int) -> UserCreditRow:
        row = UserCreditRow(id=str(uuid4()), user_id=user_id, credits=credits)
        self._session.add(row)
        return row

    def refresh_user(self, row: UserRow) -> UserRecord:
        self._session.refresh(row)
        return _user_record(row)

    def list_users(
        self,
        *,
        skip: int,
        limit: int,
        sort_field: str,
        sort_order: str,
        username: str | None,
        email: str | None,
        membership_type: str | None,
        is_superuser: bool | None,
    ) -> tuple[Sequence[UserRecord], int]:
        query = self._session.query(UserRow)
        if username:
            query = query.filter(UserRow.username.ilike(f"%{username}%"))
        if email:
            query = query.filter(UserRow.email.ilike(f"%{email}%"))
        if membership_type:
            query = query.filter(UserRow.membership_type == membership_type)
        if is_superuser is not None:
            query = query.filter(UserRow.is_superuser == is_superuser)

        total = query.count()
        sort_column = getattr(UserRow, sort_field, UserRow.id)
        ordering = desc(sort_column) if sort_order == "desc" else asc(sort_column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        records: list[UserRecord] = []
        for row in rows:
            credit = (
                self._session.query(UserCreditRow)
                .filter(UserCreditRow.user_id == row.id)
                .first()
            )
            records.append(_user_record(row, credits=credit.credits if credit else 0))
        return tuple(records), total

    def get_user_record(self, user_id: str) -> UserRecord | None:
        row = self._load_user(user_id)
        if row is None:
            return None
        credit = (
            self._session.query(UserCreditRow)
            .filter(UserCreditRow.user_id == user_id)
            .first()
        )
        return _user_record(row, credits=credit.credits if credit else 0)

    def load_user(self, user_id: str) -> UserRow | None:
        return self._load_user(user_id)

    def set_user_field(self, row: UserRow, field: str, value: Any) -> None:
        setattr(row, field, value)

    def get_user_credit(self, user_id: str) -> UserCreditRow | None:
        return (
            self._session.query(UserCreditRow)
            .filter(UserCreditRow.user_id == user_id)
            .first()
        )

    def set_credit_amount(self, row: UserCreditRow, credits: int) -> None:
        row.credits = credits

    def delete_related_user_data(self, user_id: str) -> None:
        self._session.query(CreditLogRow).filter(CreditLogRow.user_id == user_id).delete()
        self._session.query(UserCreditRow).filter(UserCreditRow.user_id == user_id).delete()
        self._session.query(PromptConfigRow).filter(PromptConfigRow.user_id == user_id).delete()
        self._session.query(ModelConfigRow).filter(ModelConfigRow.user_id == user_id).delete()

        series_rows = (
            self._session.query(SeriesRow)
            .filter(SeriesRow.user_id == user_id)
            .all()
        )
        for series_row in series_rows:
            chapter_ids = [
                result[0]
                for result in self._session.query(ChapterRow.id)
                .filter(ChapterRow.series_id == series_row.id)
                .all()
            ]
            if chapter_ids:
                (
                    self._session.query(ChatMessageRow)
                    .filter(ChatMessageRow.chapter_id.in_(chapter_ids))
                    .delete(synchronize_session=False)
                )
                (
                    self._session.query(StoryboardAssetRow)
                    .filter(StoryboardAssetRow.chapter_id.in_(chapter_ids))
                    .delete(synchronize_session=False)
                )
                (
                    self._session.query(ChapterRow)
                    .filter(ChapterRow.series_id == series_row.id)
                    .delete()
                )
            (
                self._session.query(StoryboardAssetRow)
                .filter(StoryboardAssetRow.series_id == series_row.id)
                .delete()
            )
            self._session.query(PropRow).filter(PropRow.series_id == series_row.id).delete()
            self._session.query(SceneRow).filter(SceneRow.series_id == series_row.id).delete()
            self._session.query(CharacterRow).filter(CharacterRow.series_id == series_row.id).delete()
            (
                self._session.query(AssetDuplicateExclusionRow)
                .filter(AssetDuplicateExclusionRow.series_id == series_row.id)
                .delete(synchronize_session=False)
            )
        self._session.query(SeriesRow).filter(SeriesRow.user_id == user_id).delete()

    def delete_user(self, row: UserRow) -> None:
        self._session.delete(row)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()

    def _load_user(self, user_id: str) -> UserRow | None:
        return (
            self._session.query(UserRow)
            .filter(UserRow.id == user_id)
            .first()
        )


def _user_record(row: Any, *, credits: int = 0) -> UserRecord:
    return UserRecord(
        id=row.id,
        username=row.username,
        email=row.email,
        is_superuser=row.is_superuser,
        membership_type=row.membership_type,
        membership_expires_at=row.membership_expires_at,
        credits=credits,
        created_at=row.created_at,
    )
