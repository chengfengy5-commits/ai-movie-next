"""Real temporary-file SQLite tests for the Core persistence adapter."""

from __future__ import annotations

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from haoai_backend.personal_production.rough_cut.application import save_rough_cut
from haoai_backend.personal_production.rough_cut.domain import RoughCutUpdate, UpdateFrame
from haoai_backend.personal_production.rough_cut.errors import Conflict
from haoai_backend.personal_production.rough_cut.persistence import SqlAlchemyRoughCutUnitOfWork
from haoai_backend.personal_production.rough_cut.ports import TrustedActor
from haoai_backend.personal_production.rough_cut.tables import (
    chapters,
    rough_cut_drafts,
    users,
)
from conftest import TestDatabase, make_series_access_policy


def make_uow(db: TestDatabase) -> SqlAlchemyRoughCutUnitOfWork:
    return SqlAlchemyRoughCutUnitOfWork(
        db.session_factory(),
        make_series_access_policy(),
    )


def seed_one_frame(db: TestDatabase) -> None:
    db.seed_chapter(
        "chapter-a",
        "series-a",
        [{"storyboard": ["asset-a"], "text": "源镜头", "preview": "clip.mp4"}],
        ("asset-a",),
    )


def test_draft_survives_new_sessions_and_is_private_per_user(database: TestDatabase) -> None:
    seed_one_frame(database)
    writer = make_uow(database)
    actor_a = TrustedActor("user-a")
    chapter = writer.load_chapter("chapter-a", lock=True)
    assert chapter is not None
    writer.require_series_access(actor_a, chapter.series_id)
    writer.insert_private_draft(
        "chapter-a",
        "user-a",
        1,
        ({"asset_id": "asset-a", "included": False},),
    )
    writer.commit()
    writer.close()

    persisted = database.read_draft("chapter-a", "user-a")
    assert persisted is not None
    assert persisted["revision"] == 1
    assert persisted["frames"] == [{"asset_id": "asset-a", "included": False}]

    user_b = make_uow(database)
    assert user_b.load_private_draft("chapter-a", "user-b") is None
    user_b.close()
    user_a = make_uow(database)
    assert user_a.load_private_draft("chapter-a", "user-a").revision == 1
    user_a.close()


def test_distinct_physical_connections_read_same_revision_then_enforce_cas(database: TestDatabase) -> None:
    seed_one_frame(database)
    seed = make_uow(database)
    seed.insert_private_draft(
        "chapter-a", "user-a", 1, ({"asset_id": "asset-a", "included": True},)
    )
    seed.commit()
    seed.close()

    connection_a = database.engine.connect()
    connection_b = database.engine.connect()
    assert connection_a.connection.driver_connection is not connection_b.connection.driver_connection
    winner = SqlAlchemyRoughCutUnitOfWork(
        Session(bind=connection_a, expire_on_commit=False), make_series_access_policy()
    )
    stale = SqlAlchemyRoughCutUnitOfWork(
        Session(bind=connection_b, expire_on_commit=False), make_series_access_policy()
    )
    for unit in (winner, stale):
        chapter = unit.load_chapter("chapter-a", lock=True)
        assert chapter is not None
        unit.require_series_access(TrustedActor("user-a"), chapter.series_id)
        assert unit.load_private_draft("chapter-a", "user-a").revision == 1

    # End the stale read transaction while retaining its captured R1 expectation.
    stale.rollback()
    assert winner.update_private_draft(
        "chapter-a", "user-a", 1, 2, ({"asset_id": "asset-a", "included": False},)
    )
    winner.commit()
    assert not stale.update_private_draft(
        "chapter-a", "user-a", 1, 2, ({"asset_id": "asset-a", "included": True},)
    )
    stale.rollback()
    winner.close()
    stale.close()
    connection_a.close()
    connection_b.close()

    persisted = database.read_draft("chapter-a", "user-a")
    assert persisted is not None
    assert persisted["revision"] == 2
    assert persisted["frames"] == [{"asset_id": "asset-a", "included": False}]


def test_first_insert_competition_maps_only_private_pair_unique_constraint(database: TestDatabase) -> None:
    seed_one_frame(database)
    connection_a = database.engine.connect()
    connection_b = database.engine.connect()
    assert connection_a.connection.driver_connection is not connection_b.connection.driver_connection
    first = SqlAlchemyRoughCutUnitOfWork(
        Session(bind=connection_a, expire_on_commit=False), make_series_access_policy()
    )
    second = SqlAlchemyRoughCutUnitOfWork(
        Session(bind=connection_b, expire_on_commit=False), make_series_access_policy()
    )
    for unit in (first, second):
        chapter = unit.load_chapter("chapter-a", lock=True)
        assert chapter is not None
        unit.require_series_access(TrustedActor("user-a"), chapter.series_id)
        assert unit.load_private_draft("chapter-a", "user-a") is None
    first.rollback()
    second.rollback()
    row = ({"asset_id": "asset-a", "included": True},)

    first.insert_private_draft("chapter-a", "user-a", 1, row)
    first.commit()
    try:
        second.insert_private_draft("chapter-a", "user-a", 1, row)
        second.commit()
    except IntegrityError as exc:
        assert second.is_private_draft_unique_conflict(exc)
        second.rollback()
    else:
        raise AssertionError("duplicate private draft insert should violate its unique key")

    # The losing unit remains usable after its exact private-pair conflict.
    assert second.load_private_draft("chapter-a", "user-a").revision == 1
    first.close()
    second.close()
    connection_a.close()
    connection_b.close()


def test_revision_check_constraint_rejects_zero(database: TestDatabase) -> None:
    seed_one_frame(database)
    with pytest.raises(IntegrityError):
        with database.session_factory.begin() as session:
            session.execute(
                insert(rough_cut_drafts).values(
                    id="bad-revision",
                    chapter_id="chapter-a",
                    user_id="user-a",
                    revision=0,
                    frames=[],
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
    assert database.read_draft("chapter-a", "user-a") is None


def test_unrelated_integrity_error_is_not_treated_as_revision_conflict(database: TestDatabase) -> None:
    seed_one_frame(database)
    unit = make_uow(database)
    try:
        with unit._session.begin_nested():
            unit._session.execute(
                insert(rough_cut_drafts).values(
                    id="bad-check",
                    chapter_id="chapter-a",
                    user_id="user-a",
                    revision=0,
                    frames=[],
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
    except IntegrityError as exc:
        assert not unit.is_private_draft_unique_conflict(exc)
    else:
        raise AssertionError("revision check constraint should reject zero")
    finally:
        unit.rollback()
        unit.close()


def test_user_and_chapter_deletion_cascade_to_rough_cut_rows(database: TestDatabase) -> None:
    seed_one_frame(database)
    unit = make_uow(database)
    unit.insert_private_draft("chapter-a", "user-a", 1, ({"asset_id": "asset-a", "included": True},))
    unit.commit()
    unit.close()

    with database.engine.begin() as connection:
        connection.execute(delete(users).where(users.c.id == "user-a"))
    assert database.read_draft("chapter-a", "user-a") is None

    database.seed_user("user-a")
    unit = make_uow(database)
    unit.insert_private_draft("chapter-a", "user-a", 1, ({"asset_id": "asset-a", "included": True},))
    unit.commit()
    unit.close()
    with database.engine.begin() as connection:
        connection.execute(delete(chapters).where(chapters.c.id == "chapter-a"))
    assert database.read_draft("chapter-a", "user-a") is None


def test_actual_chapter_lock_statement_compiles_for_postgresql(database: TestDatabase) -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session as SessionClass

    seed_one_frame(database)
    observed = []

    def capture_statement(execute_state) -> None:
        if execute_state.is_select:
            observed.append(execute_state.statement)

    event.listen(SessionClass, "do_orm_execute", capture_statement)
    unit = make_uow(database)
    try:
        assert unit.load_chapter("chapter-a", lock=True) is not None
    finally:
        unit.close()
        event.remove(SessionClass, "do_orm_execute", capture_statement)

    chapter_lookup = next(
        statement
        for statement in observed
        if "chapters" in str(statement)
    )
    compiled = str(chapter_lookup.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled


def test_foreign_keys_reject_missing_chapter_or_user(database: TestDatabase) -> None:
    from datetime import datetime, timezone
    import pytest

    timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
    with pytest.raises(IntegrityError):
        with database.engine.begin() as connection:
            connection.execute(
                insert(rough_cut_drafts).values(
                    id="missing-chapter",
                    chapter_id="missing",
                    user_id="user-a",
                    revision=1,
                    frames=[],
                    updated_at=timestamp,
                )
            )
    with pytest.raises(IntegrityError):
        with database.engine.begin() as connection:
            connection.execute(
                insert(rough_cut_drafts).values(
                    id="missing-user",
                    chapter_id="chapter-a",
                    user_id="missing",
                    revision=1,
                    frames=[],
                    updated_at=timestamp,
                )
            )


def test_real_precommit_failure_rolls_back_insert_and_does_not_retry(database: TestDatabase) -> None:
    seed_one_frame(database)

    class FailBeforeCommit(SqlAlchemyRoughCutUnitOfWork):
        def __init__(self, session, policy):
            super().__init__(session, policy)
            self.commit_calls = 0

        def commit(self) -> None:
            self.commit_calls += 1
            raise RuntimeError("precommit storage failure")

    instances = []

    def factory():
        unit = FailBeforeCommit(database.session_factory(), make_series_access_policy())
        instances.append(unit)
        return unit

    import pytest
    with pytest.raises(RuntimeError, match="precommit storage failure"):
        save_rough_cut(
            factory,
            TrustedActor("user-a"),
            "chapter-a",
            RoughCutUpdate(0, (UpdateFrame("asset-a", True),)),
        )

    assert len(instances) == 1
    assert instances[0].commit_calls == 1
    assert instances[0]._session.is_active is True
    assert database.read_draft("chapter-a", "user-a") is None


def test_commit_confirmation_failure_is_not_reported_and_can_be_read_back(database: TestDatabase) -> None:
    seed_one_frame(database)

    class CommitThenLoseConfirmation(SqlAlchemyRoughCutUnitOfWork):
        def commit(self) -> None:
            super().commit()
            raise RuntimeError("commit acknowledgement lost")

    def uow_factory() -> SqlAlchemyRoughCutUnitOfWork:
        return CommitThenLoseConfirmation(database.session_factory(), make_series_access_policy())

    try:
        save_rough_cut(
            uow_factory,
            TrustedActor("user-a"),
            "chapter-a",
            RoughCutUpdate(0, (UpdateFrame("asset-a", False),)),
        )
    except RuntimeError as exc:
        assert str(exc) == "commit acknowledgement lost"
    else:
        raise AssertionError("an uncertain commit must not be reported as success")

    confirmed = database.read_draft("chapter-a", "user-a")
    assert confirmed is not None
    assert confirmed["revision"] == 1
    assert confirmed["frames"] == [{"asset_id": "asset-a", "included": False}]
