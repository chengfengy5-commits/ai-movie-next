"""SQLite tests for legacy card queries and bio-only update persistence."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, event, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import users
from haoai_backend.user_profiles.application import UserProfileApplication
from haoai_backend.user_profiles.authentication import UserProfileActor
from haoai_backend.user_profiles.errors import UserProfileForbidden
from haoai_backend.user_profiles.persistence import SqlAlchemyUserProfileUnitOfWork
from haoai_backend.user_profiles.tables import (
    characters,
    metadata as user_profiles_metadata,
    props,
    scenes,
    series,
    storyboard_assets,
    team_members,
    teams,
)


NOW = datetime(2026, 10, 13, 12, 0, 0)


class ObservedSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()


def create_database(path: Path):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}", future=True)
    authentication_metadata.create_all(engine)
    user_profiles_metadata.create_all(engine)
    maker = sessionmaker(
        engine,
        class_=ObservedSession,
        expire_on_commit=False,
        future=True,
    )
    return engine, maker


def insert_user(
    session: Session,
    user_id: str,
    *,
    username: str | None = None,
    email: str | None = None,
    bio: str | None = None,
) -> None:
    session.execute(
        insert(users).values(
            id=user_id,
            username=username or f"{user_id}-name",
            email=email or f"{user_id}@example.test",
            hashed_password="unused",
            is_superuser=False,
            membership_type="premium",
            membership_expires_at=None,
            avatar_url="stored-avatar",
            bio=bio,
            created_at=NOW,
            password_updated_at=NOW,
        )
    )


def test_sqlite_card_scope_counts_and_second_team_name_query(tmp_path: Path) -> None:
    engine, maker = create_database(tmp_path / "user-card.sqlite")
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _params, _context, _many: statements.append(
            statement.upper()
        ),
    )
    try:
        with maker() as session:
            insert_user(
                session,
                "viewer",
                username="viewer",
                email="viewer@example.test",
            )
            insert_user(
                session,
                "target",
                username="target",
                email="123456@QQ.COM",
                bio="target bio",
            )
            insert_user(session, "observer")
            insert_user(session, "outsider")
            session.execute(insert(teams).values([
                {"id": "team-a", "name": "A"},
                {"id": "team-b", "name": "B"},
                {"id": "team-c", "name": "C"},
            ]))
            session.execute(insert(team_members).values([
                {"id": "m-viewer", "team_id": "team-a", "user_id": "viewer", "role": "owner"},
                {"id": "m-target", "team_id": "team-a", "user_id": "target", "role": "admin"},
                {"id": "m-observer", "team_id": "team-a", "user_id": "observer", "role": "member"},
                {"id": "m-viewer-b", "team_id": "team-b", "user_id": "viewer", "role": "member"},
                {"id": "m-target-c", "team_id": "team-c", "user_id": "target", "role": "member"},
            ]))
            session.execute(insert(series).values([
                {"id": "shared", "user_id": "target", "name": "Shared", "team_id": "team-a"},
                {"id": "outside", "user_id": "target", "name": "Outside", "team_id": "team-c"},
                {"id": "personal", "user_id": "target", "name": "Personal", "team_id": None},
                {"id": "viewer-owned", "user_id": "viewer", "name": "Other owner", "team_id": "team-a"},
            ]))
            session.execute(insert(characters).values([
                {"id": "character-1", "series_id": "shared"},
                {"id": "character-2", "series_id": "shared"},
            ]))
            session.execute(insert(scenes).values({"id": "scene-1", "series_id": "shared"}))
            session.execute(insert(props).values({"id": "prop-1", "series_id": "shared"}))
            session.execute(insert(storyboard_assets).values([
                {"id": "frame-1", "series_id": "shared"},
                {"id": "frame-2", "series_id": "shared"},
                {"id": "frame-3", "series_id": "shared"},
            ]))
            session.commit()
        statements.clear()

        with maker() as session:
            card = UserProfileApplication().get_user_card(
                SqlAlchemyUserProfileUnitOfWork(session),
                viewer=UserProfileActor("viewer", "snapshot", "v@e.test", None),
                target_user_id="target",
            )
            assert session.commit_count == 0

        assert card["user"]["email_masked"] == "1***6@QQ.COM"
        assert card["user"]["avatar_url"] == (
            "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100"
        )
        assert card["teams"] == [
            {"id": "team-a", "name": "A", "role": "admin", "member_count": 3}
        ]
        assert card["series"] == [
            {
                "id": "shared",
                "name": "Shared",
                "team_id": "team-a",
                "team_name": "A",
                "asset_counts": {
                    "characters": 2,
                    "scenes": 1,
                    "props": 1,
                    "storyboard": 3,
                },
            }
        ]
        assert card["asset_totals"] == {
            "characters": 2,
            "scenes": 1,
            "props": 1,
            "storyboard": 3,
        }
        assert sum("FROM TEAMS" in statement for statement in statements) == 2
        assert not any("ORDER BY" in statement for statement in statements)
        assert not any(
            statement.lstrip().startswith(("INSERT ", "UPDATE ", "DELETE "))
            for statement in statements
        )

        with maker() as session:
            denied = UserProfileActor("viewer", "viewer", "v@e.test", None)
            try:
                UserProfileApplication().get_user_card(
                    SqlAlchemyUserProfileUnitOfWork(session),
                    viewer=denied,
                    target_user_id="outsider",
                )
            except UserProfileForbidden:
                pass
            else:
                raise AssertionError("a user without a shared team must be denied")
    finally:
        engine.dispose()


def test_bio_snapshot_comparison_netdirty_and_postcommit_sql_readback(tmp_path: Path) -> None:
    engine, maker = create_database(tmp_path / "user-profile-update.sqlite")
    updates: list[str] = []

    def collect_update(_conn, _cursor, statement, _params, _context, _many) -> None:
        if statement.lstrip().upper().startswith("UPDATE USERS"):
            updates.append(statement)

    event.listen(engine, "before_cursor_execute", collect_update)
    try:
        with maker() as session:
            insert_user(
                session,
                "viewer",
                username="database-name",
                email="database@example.test",
                bio="database-bio",
            )
            session.commit()

        application = UserProfileApplication()

        # The request matches the auth snapshot, so a concurrent database value survives.
        session = maker()
        first = application.update_my_profile(
            SqlAlchemyUserProfileUnitOfWork(session),
            actor=UserProfileActor("viewer", "old-name", "old@example.test", "snapshot-a"),
            requested_bio="snapshot-a",
        )
        assert session.commit_count == 1
        assert updates == []
        assert first["bio"] == "database-bio"
        session.close()

        # Omitted and explicit null bio follow the same no-assignment path.
        session = maker()
        null_bio = application.update_my_profile(
            SqlAlchemyUserProfileUnitOfWork(session),
            actor=UserProfileActor("viewer", "old-name", "old@example.test", "snapshot-a"),
            requested_bio=None,
        )
        assert session.commit_count == 1
        assert updates == []
        assert null_bio["bio"] == "database-bio"
        session.close()

        # A request that differs from the snapshot but already matches the current row
        # exercises SQLAlchemy's normal net-dirty check.
        session = maker()
        second = application.update_my_profile(
            SqlAlchemyUserProfileUnitOfWork(session),
            actor=UserProfileActor("viewer", "old-name", "old@example.test", "snapshot-a"),
            requested_bio="database-bio",
        )
        assert session.commit_count == 1
        assert updates == []
        assert second["bio"] == "database-bio"
        session.close()

        # A real change updates only bio and returns fresh DB username/email/bio.
        with maker() as session:
            session.execute(
                update(users)
                .where(users.c.id == "viewer")
                .values(username="renamed-in-db", email="123456@qq.com")
            )
            session.commit()

        updates.clear()
        session = maker()
        changed = application.update_my_profile(
            SqlAlchemyUserProfileUnitOfWork(session),
            actor=UserProfileActor("viewer", "old-name", "old@example.test", "snapshot-a"),
            requested_bio="new bio",
        )
        assert session.commit_count == 1
        assert len(updates) == 1
        assert "SET BIO=?" in updates[0].upper()
        assert "USERNAME" not in updates[0].upper()
        assert "EMAIL" not in updates[0].upper()
        assert changed == {
            "id": "viewer",
            "username": "renamed-in-db",
            "avatar_url": "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100",
            "bio": "new bio",
        }
        session.close()

        with maker() as session:
            row = session.execute(
                select(users.c.bio).where(users.c.id == "viewer")
            ).scalar_one()
            assert row == "new bio"
    finally:
        engine.dispose()


def test_lost_commit_ack_is_not_replayed_and_missing_readback_is_not_fabricated(
    tmp_path: Path,
) -> None:
    engine, maker = create_database(tmp_path / "user-profile-failures.sqlite")
    with maker() as session:
        insert_user(session, "viewer", bio="old")
        session.commit()

    class CommittingButLosingAckSession(ObservedSession):
        def commit(self) -> None:
            super().commit()
            raise RuntimeError("commit acknowledgment lost")

    lost_ack_maker = sessionmaker(
        engine,
        class_=CommittingButLosingAckSession,
        expire_on_commit=False,
        future=True,
    )
    lost_ack_session = lost_ack_maker()
    try:
        try:
            UserProfileApplication().update_my_profile(
                SqlAlchemyUserProfileUnitOfWork(lost_ack_session),
                actor=UserProfileActor("viewer", "snapshot", "old@example.test", "old"),
                requested_bio="committed once",
            )
        except RuntimeError as error:
            assert "acknowledgment lost" in str(error)
        else:
            raise AssertionError("the lost commit acknowledgment must propagate")

        assert lost_ack_session.commit_count == 1
        with Session(engine, future=True) as session:
            assert session.execute(
                select(users.c.bio).where(users.c.id == "viewer")
            ).scalar_one() == "committed once"
    finally:
        lost_ack_session.close()

    class DeleteAfterCommitSession(ObservedSession):
        def commit(self) -> None:
            super().commit()
            with engine.begin() as connection:
                connection.execute(delete(users).where(users.c.id == "viewer"))

    delete_after_commit_maker = sessionmaker(
        engine,
        class_=DeleteAfterCommitSession,
        expire_on_commit=False,
        future=True,
    )
    deleted_session = delete_after_commit_maker()
    try:
        try:
            UserProfileApplication().update_my_profile(
                SqlAlchemyUserProfileUnitOfWork(deleted_session),
                actor=UserProfileActor(
                    "viewer", "snapshot", "old@example.test", "committed once"
                ),
                requested_bio="updated before removal",
            )
        except Exception as error:
            from haoai_backend.user_profiles.errors import UserProfilePersistenceError

            assert isinstance(error, UserProfilePersistenceError)
            assert "post-commit readback" in str(error)
        else:
            raise AssertionError("a missing post-commit row must not return a DTO")

        assert deleted_session.commit_count == 1
        with Session(engine, future=True) as session:
            assert session.execute(
                select(users.c.id).where(users.c.id == "viewer")
            ).scalar_one_or_none() is None
    finally:
        deleted_session.close()
        engine.dispose()
