from __future__ import annotations

from datetime import datetime

from haoai_backend.generic_tasks.domain import GenericTaskDraft
from haoai_backend.generic_tasks.persistence import (
    SqlAlchemyGenericTaskUnitOfWork,
    create_generic_task_uow_factory,
)
from generic_tasks_support import ACTOR, NOW, observe_sql, owner_database, generic_uow_factory


def test_factory_is_inert_without_session_factory_and_binds_supplied_session(
    owner_database,
) -> None:
    assert create_generic_task_uow_factory(None) is None
    factory = generic_uow_factory(owner_database)
    unit = factory()
    try:
        assert isinstance(unit, SqlAlchemyGenericTaskUnitOfWork)
    finally:
        unit.close()
    assert owner_database.session_factory.created[-1].close_calls == 1


def test_stage_add_and_following_select_do_not_flush_task_early(owner_database) -> None:
    observation, stop = observe_sql(owner_database)
    unit = generic_uow_factory(owner_database)()
    try:
        unit.stage_processing_task(
            GenericTaskDraft(
                id="g-stage-only",
                user_id=ACTOR.user_id,
                type="image",
                status="processing",
                credit_cost=0,
                message_id="",
                request_data="{}",
                model_name=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        wallet = unit.lock_wallet(ACTOR.user_id)
        assert wallet is not None
        statements = [statement.lower() for statement in observation.statements]
        assert any("from user_credits" in sql for sql in statements)
        assert not any(sql.startswith("insert into ai_tasks") for sql in statements)
        unit.rollback()
    finally:
        unit.close()
        stop()
    assert owner_database.engine.dialect.name == "sqlite"
    assert owner_database.session_factory.created[-1].close_calls == 1


def test_read_after_commit_reuses_session_and_reads_by_primary_key(owner_database) -> None:
    from generic_tasks_support import seed_task

    seed_task(owner_database, "g-pk-readback")
    observation, stop = observe_sql(owner_database)
    unit = generic_uow_factory(owner_database)()
    try:
        loaded = unit.load_owned_task_first(ACTOR.user_id, "g-pk-readback")
        assert loaded is not None
        unit.commit()
        record = unit.read_task_after_commit("g-pk-readback")
        assert record.id == "g-pk-readback"
    finally:
        unit.close()
        stop()

    selects = [
        statement.lower()
        for statement in observation.statements
        if statement.lower().startswith("select") and "from ai_tasks" in statement.lower()
    ]
    assert len(selects) == 2
    assert "user_id" in selects[0]
    assert "where ai_tasks.id = ?" in selects[-1]
    readback_filter = selects[-1].split("where", 1)[1]
    assert "user_id" not in readback_filter
    assert owner_database.session_factory.created[-1].close_calls == 1
    assert isinstance(NOW, datetime)
