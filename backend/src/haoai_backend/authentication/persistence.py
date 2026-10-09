"""SQLAlchemy Core authentication adapter; transaction ownership stays in application."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .domain import UserCreditRecord, UserRecord, UserSessionRecord
from .ports import AuthenticationUnitOfWork
from .tables import user_credits, user_sessions, users


class SqlAlchemyAuthenticationUnitOfWork(AuthenticationUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def load_user_by_id(self, user_id: str) -> UserRecord | None:
        row = self._session.execute(select(users).where(users.c.id == user_id)).mappings().first()
        return _user(row) if row is not None else None

    def load_user_by_username(self, username: str) -> UserRecord | None:
        row = self._session.execute(
            select(users).where(users.c.username == username)
        ).mappings().first()
        return _user(row) if row is not None else None

    def load_user_by_email(self, email: str) -> UserRecord | None:
        row = self._session.execute(
            select(users).where(users.c.email == email)
        ).mappings().first()
        return _user(row) if row is not None else None

    def has_superuser(self) -> bool:
        count = self._session.execute(
            select(func.count()).select_from(users).where(users.c.is_superuser.is_(True))
        ).scalar_one()
        return count > 0

    def insert_user(self, user: UserRecord) -> UserRecord:
        self._session.execute(insert(users).values(**_user_values(user)))
        return user

    def update_password(
        self,
        user_id: str,
        hashed_password: str,
        *,
        password_updated_at: datetime | None,
        update_password_timestamp: bool,
    ) -> bool:
        values: dict[str, object] = {"hashed_password": hashed_password}
        if update_password_timestamp:
            values["password_updated_at"] = password_updated_at
        result = self._session.execute(
            update(users).where(users.c.id == user_id).values(**values)
        )
        if result.rowcount != 1:
            raise StaleDataError("authentication user changed during update")
        return True

    def list_active_sessions(self, user_id: str) -> list[UserSessionRecord]:
        rows = self._session.execute(
            select(user_sessions).where(
                user_sessions.c.user_id == user_id,
                user_sessions.c.is_active.is_(True),
                user_sessions.c.revoked_at.is_(None),
            ).order_by(user_sessions.c.last_seen_at.asc(), user_sessions.c.created_at.asc())
        ).mappings().all()
        return [_session(row) for row in rows]

    def list_sessions(self, user_id: str) -> list[UserSessionRecord]:
        rows = self._session.execute(
            select(user_sessions).where(
                user_sessions.c.user_id == user_id,
                user_sessions.c.is_active.is_(True),
                user_sessions.c.revoked_at.is_(None),
            )
        ).mappings().all()
        return [_session(row) for row in rows]

    def load_session_by_jti(self, user_id: str, jti: str) -> UserSessionRecord | None:
        row = self._session.execute(
            select(user_sessions).where(
                user_sessions.c.user_id == user_id,
                user_sessions.c.jti == jti,
            )
        ).mappings().first()
        return _session(row) if row is not None else None

    def load_session_by_id(self, user_id: str, session_id: str) -> UserSessionRecord | None:
        row = self._session.execute(
            select(user_sessions).where(
                user_sessions.c.user_id == user_id,
                user_sessions.c.id == session_id,
            )
        ).mappings().first()
        return _session(row) if row is not None else None

    def insert_session(self, session: UserSessionRecord) -> UserSessionRecord:
        self._session.execute(insert(user_sessions).values(**_session_values(session)))
        return session

    def update_session(
        self,
        session_id: str,
        *,
        is_active: bool | None,
        last_seen_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> bool:
        values: dict[str, object] = {}
        if is_active is not None:
            values["is_active"] = is_active
        if last_seen_at is not None:
            values["last_seen_at"] = last_seen_at
        if revoked_at is not None:
            values["revoked_at"] = revoked_at
        if not values:
            return True
        result = self._session.execute(
            update(user_sessions).where(user_sessions.c.id == session_id).values(**values)
        )
        if result.rowcount != 1:
            raise StaleDataError("authentication session changed during update")
        return True

    def revoke_all_sessions(self, user_id: str, revoked_at: datetime) -> None:
        self._session.execute(
            update(user_sessions)
            .where(user_sessions.c.user_id == user_id)
            .values(is_active=False, revoked_at=revoked_at)
        )

    def load_credit(self, user_id: str) -> UserCreditRecord | None:
        row = self._session.execute(
            select(user_credits).where(user_credits.c.user_id == user_id)
        ).mappings().first()
        return _credit(row) if row is not None else None

    def insert_credit(self, credit: UserCreditRecord) -> UserCreditRecord:
        self._session.execute(insert(user_credits).values(**_credit_values(credit)))
        return credit

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _user(row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        hashed_password=row["hashed_password"],
        is_superuser=bool(row["is_superuser"]),
        membership_type=row["membership_type"],
        membership_expires_at=row["membership_expires_at"],
        avatar_url=row["avatar_url"],
        bio=row["bio"],
        created_at=row["created_at"],
        password_updated_at=row["password_updated_at"],
    )


def _user_values(user: UserRecord) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "hashed_password": user.hashed_password,
        "is_superuser": user.is_superuser,
        "membership_type": user.membership_type,
        "membership_expires_at": user.membership_expires_at,
        "avatar_url": user.avatar_url,
        "bio": user.bio,
        "created_at": user.created_at,
        "password_updated_at": user.password_updated_at,
    }


def _session(row) -> UserSessionRecord:
    return UserSessionRecord(
        id=row["id"],
        user_id=row["user_id"],
        jti=row["jti"],
        user_agent=row["user_agent"],
        ip_address=row["ip_address"],
        device_name=row["device_name"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
        revoked_at=row["revoked_at"],
    )


def _session_values(session: UserSessionRecord) -> dict[str, object]:
    return {
        "id": session.id,
        "user_id": session.user_id,
        "jti": session.jti,
        "user_agent": session.user_agent,
        "ip_address": session.ip_address,
        "device_name": session.device_name,
        "is_active": session.is_active,
        "created_at": session.created_at,
        "last_seen_at": session.last_seen_at,
        "revoked_at": session.revoked_at,
    }


def _credit(row) -> UserCreditRecord:
    return UserCreditRecord(
        id=row["id"],
        user_id=row["user_id"],
        credits=int(row["credits"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _credit_values(credit: UserCreditRecord) -> dict[str, object]:
    return {
        "id": credit.id,
        "user_id": credit.user_id,
        "credits": credit.credits,
        "created_at": credit.created_at,
        "updated_at": credit.updated_at,
    }
