"""SQLite integration checks for the authentication Core adapter."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, event, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from haoai_backend.authentication.domain import UserCreditRecord, UserRecord, UserSessionRecord
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata, user_credits, user_sessions, users


NOW = datetime(2026, 1, 2, 3, 4, 5)


def database(path: Path):
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(engine)
    return engine


def user_record(
    *, user_id: str = "user-a", username: str = "alice", email: str = "alice@example.test"
) -> UserRecord:
    return UserRecord(
        id=user_id,
        username=username,
        email=email,
        hashed_password="encoded-password",
        is_superuser=False,
        membership_type="premium",
        membership_expires_at=None,
        avatar_url=None,
        bio="private bio",
        created_at=NOW,
        password_updated_at=None,
    )


def session_record(*, session_id: str = "session-a", jti: str = "jti-a") -> UserSessionRecord:
    return UserSessionRecord(
        id=session_id,
        user_id="user-a",
        jti=jti,
        user_agent="browser",
        ip_address="192.0.2.1",
        device_name="test device",
        is_active=True,
        created_at=NOW,
        last_seen_at=NOW,
        revoked_at=None,
    )


def test_unit_of_work_round_trips_records_and_respects_transaction_ownership(tmp_path: Path) -> None:
    engine = database(tmp_path / "auth-uow.sqlite")
    try:
        with Session(engine, future=True) as session:
            uow = SqlAlchemyAuthenticationUnitOfWork(session)
            user = user_record()
            uow.insert_user(user)
            uow.insert_session(session_record())
            uow.insert_credit(
                UserCreditRecord(
                    id="credit-a",
                    user_id=user.id,
                    credits=-3,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            assert uow.load_user_by_id(user.id) == user
            assert uow.load_user_by_username(user.username) == user
            assert uow.load_user_by_email(user.email) == user
            assert uow.has_superuser() is False
            assert uow.load_session_by_jti(user.id, "jti-a") == session_record()
            assert uow.load_session_by_id(user.id, "session-a") == session_record()
            assert uow.list_active_sessions(user.id) == [session_record()]
            assert uow.list_sessions(user.id) == [session_record()]
            assert uow.load_credit(user.id).credits == -3
            session.commit()

            uow.update_session(
                "session-a", is_active=False, last_seen_at=NOW, revoked_at=NOW
            )
            uow.update_password(
                user.id,
                "updated-hash",
                password_updated_at=NOW,
                update_password_timestamp=True,
            )
            uow.revoke_all_sessions(user.id, NOW)
            assert uow.list_active_sessions(user.id) == []
            session.rollback()

        with Session(engine, future=True) as session:
            uow = SqlAlchemyAuthenticationUnitOfWork(session)
            assert uow.load_user_by_id("user-a").hashed_password == "encoded-password"
            assert uow.load_session_by_id("user-a", "session-a").is_active is True
            assert uow.load_credit("user-a").credits == -3
    finally:
        engine.dispose()


def test_unique_keys_and_foreign_keys_are_enforced_by_sqlite(tmp_path: Path) -> None:
    engine = database(tmp_path / "auth-constraints.sqlite")
    try:
        with Session(engine, future=True) as session:
            uow = SqlAlchemyAuthenticationUnitOfWork(session)
            uow.insert_user(user_record())
            session.commit()

            with pytest.raises(IntegrityError):
                uow.insert_user(user_record(user_id="user-b", username="alice", email="other@example.test"))
            session.rollback()

            with pytest.raises(IntegrityError):
                uow.insert_user(user_record(user_id="user-c", username="carol", email="alice@example.test"))
            session.rollback()

            with pytest.raises(IntegrityError):
                session.execute(
                    insert(user_sessions).values(
                        id="orphan-session", user_id="missing-user", jti="orphan-jti",
                        user_agent=None, ip_address=None, device_name=None, is_active=True,
                        created_at=NOW, last_seen_at=NOW, revoked_at=None,
                    )
                )
            session.rollback()

            uow.insert_session(session_record())
            session.commit()
            with pytest.raises(IntegrityError):
                uow.insert_session(session_record(session_id="session-b", jti="jti-a"))
            session.rollback()

            uow.insert_credit(
                UserCreditRecord("credit-a", "user-a", 0, NOW, NOW)
            )
            session.commit()
            with pytest.raises(IntegrityError):
                uow.insert_credit(
                    UserCreditRecord("credit-b", "user-a", 0, NOW, NOW)
                )
            session.rollback()
    finally:
        engine.dispose()


def test_user_session_foreign_key_cascades_when_user_is_deleted(tmp_path: Path) -> None:
    engine = database(tmp_path / "auth-cascade.sqlite")
    try:
        with Session(engine, future=True) as session:
            session.execute(insert(users).values(
                id="user-a", username="alice", email="alice@example.test",
                hashed_password="hash", is_superuser=False, membership_type="free",
                membership_expires_at=None, avatar_url=None, bio=None,
                created_at=NOW, password_updated_at=None,
            ))
            session.execute(insert(user_sessions).values(
                id="session-a", user_id="user-a", jti="jti-a", user_agent=None,
                ip_address=None, device_name=None, is_active=True, created_at=NOW,
                last_seen_at=NOW, revoked_at=None,
            ))
            session.commit()
            session.execute(delete(users).where(users.c.id == "user-a"))
            session.commit()
            assert session.execute(select(user_sessions.c.id)).scalars().all() == []
    finally:
        engine.dispose()


def test_last_seen_touch_does_not_reactivate_a_session_offlined_on_another_connection(
    tmp_path: Path,
) -> None:
    engine = database(tmp_path / "auth-session-offline-race.sqlite")
    with Session(engine, future=True) as seed:
        seed.execute(insert(users).values(
            id="user-a", username="alice", email="alice@example.test",
            hashed_password="encoded-password", is_superuser=False,
            membership_type="premium", membership_expires_at=None,
            avatar_url=None, bio="private bio", created_at=NOW,
            password_updated_at=None,
        ))
        seed.execute(insert(user_sessions).values(
            id="session-a", user_id="user-a", jti="jti-a", user_agent=None,
            ip_address=None, device_name=None, is_active=True, created_at=NOW,
            last_seen_at=NOW, revoked_at=None,
        ))
        seed.commit()

    first_connection = engine.connect()
    second_connection = engine.connect()
    first = Session(bind=first_connection, future=True)
    second = Session(bind=second_connection, future=True)
    try:
        first_driver_connection = first_connection.connection.driver_connection
        second_driver_connection = second_connection.connection.driver_connection
        assert first_driver_connection is not second_driver_connection

        uow = SqlAlchemyAuthenticationUnitOfWork(first)
        stale_read = uow.load_session_by_id("user-a", "session-a")
        assert stale_read is not None and stale_read.is_active is True
        first.commit()

        second.execute(
            update(user_sessions)
            .where(user_sessions.c.id == "session-a")
            .values(is_active=False, revoked_at=NOW)
        )
        second.commit()

        uow.update_session(
            stale_read.id,
            is_active=None,
            last_seen_at=NOW + timedelta(minutes=1),
        )
        first.commit()

        with Session(engine, future=True) as check:
            saved = check.execute(
                select(user_sessions).where(user_sessions.c.id == "session-a")
            ).mappings().one()
        assert saved["is_active"] is False
        assert saved["revoked_at"] == NOW
        assert saved["last_seen_at"] == NOW + timedelta(minutes=1)
    finally:
        first.close()
        second.close()
        first_connection.close()
        second_connection.close()
        engine.dispose()


def test_zero_row_user_and_session_updates_raise_after_deletion_on_another_connection(
    tmp_path: Path,
) -> None:
    for target in ("session", "user"):
        engine = database(tmp_path / f"auth-{target}-delete-race.sqlite")
        with Session(engine, future=True) as seed:
            seed.execute(insert(users).values(
                id="user-a", username="alice", email="alice@example.test",
                hashed_password="encoded-password", is_superuser=False,
                membership_type="premium", membership_expires_at=None,
                avatar_url=None, bio="private bio", created_at=NOW,
                password_updated_at=None,
            ))
            if target == "session":
                seed.execute(insert(user_sessions).values(
                    id="session-a", user_id="user-a", jti="jti-a", user_agent=None,
                    ip_address=None, device_name=None, is_active=True, created_at=NOW,
                    last_seen_at=NOW, revoked_at=None,
                ))
            seed.commit()

        first_connection = engine.connect()
        second_connection = engine.connect()
        first = Session(bind=first_connection, future=True)
        second = Session(bind=second_connection, future=True)
        try:
            assert (
                first_connection.connection.driver_connection
                is not second_connection.connection.driver_connection
            )
            uow = SqlAlchemyAuthenticationUnitOfWork(first)
            if target == "session":
                assert uow.load_session_by_id("user-a", "session-a") is not None
                deletion = delete(user_sessions).where(user_sessions.c.id == "session-a")
            else:
                assert uow.load_user_by_id("user-a") is not None
                deletion = delete(users).where(users.c.id == "user-a")
            first.commit()
            second.execute(deletion)
            second.commit()

            with pytest.raises(StaleDataError):
                if target == "session":
                    uow.update_session(
                        "session-a", is_active=False, revoked_at=NOW
                    )
                else:
                    uow.update_password(
                        "user-a", "new-hash", password_updated_at=NOW,
                        update_password_timestamp=True,
                    )
            first.rollback()
        finally:
            first.close()
            second.close()
            first_connection.close()
            second_connection.close()
            engine.dispose()


def test_unique_constraints_serialize_competing_registration_jti_and_credit_inserts(
    tmp_path: Path,
) -> None:
    """Both file-backed SQLite connections observe absence before either writer commits.

    SQLite serializes the actual writes; this interleaving verifies the unique
    constraint wins after stale pre-reads without claiming PostgreSQL concurrency.
    """
    registration_engine = database(tmp_path / "auth-registration-unique-race.sqlite")
    registration_first_connection = registration_engine.connect()
    registration_second_connection = registration_engine.connect()
    registration_first = Session(bind=registration_first_connection, future=True)
    registration_second = Session(bind=registration_second_connection, future=True)
    try:
        assert (
            registration_first_connection.connection.driver_connection
            is not registration_second_connection.connection.driver_connection
        )
        first_uow = SqlAlchemyAuthenticationUnitOfWork(registration_first)
        second_uow = SqlAlchemyAuthenticationUnitOfWork(registration_second)
        assert first_uow.load_user_by_username("alice") is None
        assert first_uow.load_user_by_email("shared@example.test") is None
        assert second_uow.load_user_by_username("alice") is None
        assert second_uow.load_user_by_email("shared@example.test") is None
        registration_first.commit()
        registration_second.commit()

        winner = user_record(user_id="winner-user", username="alice", email="shared@example.test")
        first_uow.insert_user(winner)
        first_uow.insert_credit(
            UserCreditRecord("winner-credit", winner.id, 0, NOW, NOW)
        )
        first_uow.commit()

        loser = user_record(user_id="loser-user", username="bob", email="shared@example.test")
        with pytest.raises(IntegrityError):
            second_uow.insert_user(loser)
        second_uow.rollback()

        with Session(registration_engine, future=True) as check:
            assert check.execute(select(users.c.id)).scalars().all() == ["winner-user"]
            assert check.execute(select(user_credits.c.user_id)).scalars().all() == [
                "winner-user"
            ]
    finally:
        registration_first.close()
        registration_second.close()
        registration_first_connection.close()
        registration_second_connection.close()
        registration_engine.dispose()

    jti_engine = database(tmp_path / "auth-jti-unique-race.sqlite")
    with Session(jti_engine, future=True) as seed:
        seed.execute(insert(users).values(
            id="user-a", username="alice", email="alice@example.test",
            hashed_password="hash", is_superuser=False, membership_type="free",
            membership_expires_at=None, avatar_url=None, bio=None,
            created_at=NOW, password_updated_at=None,
        ))
        seed.commit()
    jti_first_connection = jti_engine.connect()
    jti_second_connection = jti_engine.connect()
    jti_first = Session(bind=jti_first_connection, future=True)
    jti_second = Session(bind=jti_second_connection, future=True)
    try:
        assert (
            jti_first_connection.connection.driver_connection
            is not jti_second_connection.connection.driver_connection
        )
        first_uow = SqlAlchemyAuthenticationUnitOfWork(jti_first)
        second_uow = SqlAlchemyAuthenticationUnitOfWork(jti_second)
        assert first_uow.load_session_by_jti("user-a", "shared-jti") is None
        assert second_uow.load_session_by_jti("user-a", "shared-jti") is None
        jti_first.commit()
        jti_second.commit()

        first_uow.insert_session(session_record(session_id="winner-session", jti="shared-jti"))
        first_uow.commit()
        with pytest.raises(IntegrityError):
            second_uow.insert_session(
                session_record(session_id="loser-session", jti="shared-jti")
            )
        second_uow.rollback()

        with Session(jti_engine, future=True) as check:
            rows = check.execute(
                select(user_sessions.c.id, user_sessions.c.jti)
            ).all()
        assert rows == [("winner-session", "shared-jti")]
    finally:
        jti_first.close()
        jti_second.close()
        jti_first_connection.close()
        jti_second_connection.close()
        jti_engine.dispose()

    credit_engine = database(tmp_path / "auth-credit-unique-race.sqlite")
    with Session(credit_engine, future=True) as seed:
        seed.execute(insert(users).values(
            id="user-a", username="alice", email="alice@example.test",
            hashed_password="hash", is_superuser=False, membership_type="free",
            membership_expires_at=None, avatar_url=None, bio=None,
            created_at=NOW, password_updated_at=None,
        ))
        seed.commit()
    credit_first_connection = credit_engine.connect()
    credit_second_connection = credit_engine.connect()
    credit_first = Session(bind=credit_first_connection, future=True)
    credit_second = Session(bind=credit_second_connection, future=True)
    try:
        assert (
            credit_first_connection.connection.driver_connection
            is not credit_second_connection.connection.driver_connection
        )
        first_uow = SqlAlchemyAuthenticationUnitOfWork(credit_first)
        second_uow = SqlAlchemyAuthenticationUnitOfWork(credit_second)
        assert first_uow.load_credit("user-a") is None
        assert second_uow.load_credit("user-a") is None
        credit_first.commit()
        credit_second.commit()

        first_uow.insert_credit(UserCreditRecord("winner-credit", "user-a", 0, NOW, NOW))
        first_uow.commit()
        with pytest.raises(IntegrityError):
            second_uow.insert_credit(
                UserCreditRecord("loser-credit", "user-a", 99, NOW, NOW)
            )
        second_uow.rollback()

        with Session(credit_engine, future=True) as check:
            rows = check.execute(
                select(user_credits.c.id, user_credits.c.credits)
            ).all()
        assert rows == [("winner-credit", 0)]
    finally:
        credit_first.close()
        credit_second.close()
        credit_first_connection.close()
        credit_second_connection.close()
        credit_engine.dispose()
