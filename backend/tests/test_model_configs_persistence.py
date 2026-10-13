from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import configure_mappers, sessionmaker

from haoai_backend.model_configs.application import (
    demote_system_model_to_personal,
    list_model_configs,
    promote_model_to_system,
    reorder_model_configs,
    update_model_config,
)
from haoai_backend.model_configs.domain import ModelConfigActor
from haoai_backend.model_configs.persistence import (
    SqlAlchemyModelConfigsUnitOfWork,
    model_configs_unit_of_work_factory,
)
from haoai_backend.model_configs.tables import (
    Base,
    ModelConfigRow,
    SystemModelRow,
    UserModelOrderRow,
    UserRow,
)


NOW = datetime(2026, 10, 13, 9, 30)


def make_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'model-configs.sqlite'}")


    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False, future=True)


def insert_user(session, user_id: str, *, admin: bool = False) -> None:
    session.add(
        UserRow(
            id=user_id,
            username=user_id,
            email=f"{user_id}@example.test",
            hashed_password="test-hash",
            is_superuser=admin,
            membership_type="premium",
            membership_expires_at=None,
            avatar_url=None,
            bio=None,
            created_at=NOW,
            password_updated_at=None,
        )
    )


def add_personal(
    session,
    model_id: str,
    user_id: str,
    *,
    category: str = "image",
    name: str | None = None,
    sort_order: int = 0,
    params: str | None = None,
) -> None:
    session.add(
        ModelConfigRow(
            id=model_id,
            user_id=user_id,
            category=category,
            name=name or model_id,
            provider="custom",
            api_key="key-test",
            model="model-test",
            base_url="https://provider.example.test",
            sort_order=sort_order,
            params=params,
        )
    )


def add_system(
    session,
    model_id: str,
    *,
    category: str = "image",
    name: str | None = None,
    sort_order: int = 0,
    active: bool = True,
    params: str | None = None,
) -> None:
    session.add(
        SystemModelRow(
            id=model_id,
            category=category,
            name=name or model_id,
            provider="custom",
            api_key="key-test",
            model="model-test",
            base_url="https://provider.example.test",
            cost_per_call=5,
            description="system model",
            is_active=active,
            sort_order=sort_order,
            params=params,
        )
    )


def add_order(
    session,
    order_id: str,
    user_id: str,
    model_id: str,
    category: str,
    sort_order: int,
) -> None:
    session.add(
        UserModelOrderRow(
            id=order_id,
            user_id=user_id,
            model_id=model_id,
            category=category,
            sort_order=sort_order,
        )
    )


def test_private_mappings_match_legacy_columns_and_constraints(tmp_path: Path) -> None:
    engine, sessions = make_database(tmp_path)
    session = sessions()
    try:
        assert len(UserRow.__table__.columns) == 11
        assert len(ModelConfigRow.__table__.columns) == 12
        assert len(SystemModelRow.__table__.columns) == 14
        assert len(UserModelOrderRow.__table__.columns) == 7
        assert not any(
            isinstance(constraint, UniqueConstraint)
            for constraint in UserModelOrderRow.__table__.constraints
        )
        assert not UserModelOrderRow.__table__.c.model_id.foreign_keys
        assert len(UserModelOrderRow.__table__.c.user_id.foreign_keys) == 1
        assert not hasattr(UserModelOrderRow, "user")
        configure_mappers()
        assert UserRow.model_configs.property.mapper.class_ is ModelConfigRow

        insert_user(session, "owner")
        add_personal(session, "personal", "owner")
        session.flush()
        add_order(session, "order-a", "owner", "not-a-model", "image", 0)
        add_order(session, "order-b", "owner", "not-a-model", "image", 1)
        session.commit()

        owner = session.query(UserRow).filter_by(id="owner").one()
        assert [row.id for row in owner.model_configs] == ["personal"]
        assert session.query(UserModelOrderRow).count() == 2
    finally:
        session.close()
        engine.dispose()


def test_user_foreign_keys_remain_enforced_without_a_model_id_foreign_key(
    tmp_path: Path,
) -> None:
    engine, sessions = make_database(tmp_path)
    session = sessions()
    try:
        add_personal(session, "orphan-model", "missing-user")
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        add_order(session, "orphan-order", "missing-user", "arbitrary", "image", 0)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def test_list_filtering_and_duplicate_order_rows_match_legacy_behavior(
    tmp_path: Path,
) -> None:
    engine, sessions = make_database(tmp_path)
    with sessions.begin() as session:
        insert_user(session, "member")
        insert_user(session, "admin", admin=True)
        add_personal(session, "shared", "member", name="personal", sort_order=1)
        add_personal(session, "personal-only", "member", sort_order=3)
        add_system(session, "shared", name="system", sort_order=1, active=False)
        add_system(session, "active", sort_order=5, active=True)
        session.flush()
        add_order(session, "order-1", "member", "shared", "text", 0)
        add_order(session, "order-2", "member", "shared", "video", 1)
        add_order(session, "order-3", "member", "missing", "image", 2)

    factory = model_configs_unit_of_work_factory(sessions)
    try:
        member_result = list_model_configs(
            factory,
            ModelConfigActor("member", False),
            None,
        )
        assert [row["id"] for row in member_result] == [
            "shared",
            "shared",
            "personal-only",
            "active",
        ]
        assert [row["name"] for row in member_result[:2]] == ["personal", "personal"]
        assert all(row["is_active"] is True for row in member_result)

        admin_result = list_model_configs(
            factory,
            ModelConfigActor("admin", True),
            None,
        )
        assert [row["id"] for row in admin_result] == ["shared", "active"]
        assert admin_result[0]["is_active"] is False

        session = sessions()
        try:
            system_first = SqlAlchemyModelConfigsUnitOfWork(session).find_system_model("shared")
            assert system_first is not None
            assert system_first.name == "system"
        finally:
            session.close()
    finally:
        engine.dispose()


def test_reorder_persists_duplicate_and_missing_ids_without_new_constraints(
    tmp_path: Path,
) -> None:
    engine, sessions = make_database(tmp_path)
    with sessions.begin() as session:
        insert_user(session, "member")
        add_personal(session, "a", "member", sort_order=10)
        add_personal(session, "b", "member", sort_order=20)

    try:
        response = reorder_model_configs(
            model_configs_unit_of_work_factory(sessions),
            ModelConfigActor("member", False),
            "image",
            ["a", "missing", "a"],
            id_factory=iter(["o1", "o2", "o3"]).__next__,
        )
        assert response == {"message": "排序已更新"}

        session = sessions()
        try:
            assert session.query(ModelConfigRow).filter_by(id="a").one().sort_order == 2
            assert [
                (row.model_id, row.sort_order)
                for row in session.query(UserModelOrderRow)
                .filter_by(user_id="member", category="image")
                .order_by(UserModelOrderRow.sort_order)
            ] == [("a", 0), ("missing", 1), ("a", 2)]
        finally:
            session.close()

        listed = list_model_configs(
            model_configs_unit_of_work_factory(sessions),
            ModelConfigActor("member", False),
            "image",
        )
        assert [row["id"] for row in listed] == ["a", "a", "b"]
    finally:
        engine.dispose()


def test_update_keeps_personal_transient_response_fields_out_of_storage(
    tmp_path: Path,
) -> None:
    engine, sessions = make_database(tmp_path)
    with sessions.begin() as session:
        insert_user(session, "member")
        add_personal(session, "personal", "member")

    statements: list[str] = []

    def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        result = update_model_config(
            model_configs_unit_of_work_factory(sessions),
            ModelConfigActor("member", False),
            "personal",
            {"cost_per_call": 13, "is_active": False},
        )
        assert result["cost_per_call"] == 13
        assert result["is_active"] is False
        model_selects = [
            statement
            for statement in statements
            if statement.lstrip().lower().startswith("select")
            and "model_configs" in statement.lower()
        ]
        assert len(model_selects) == 2

        session = sessions()
        try:
            row = session.query(ModelConfigRow).filter_by(id="personal").one()
            assert not hasattr(row, "cost_per_call")
            assert not hasattr(row, "is_active")
            assert row.params is None
        finally:
            session.close()
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
        engine.dispose()


def test_promote_and_demote_clear_order_references_for_every_user(
    tmp_path: Path,
) -> None:
    engine, sessions = make_database(tmp_path)
    with sessions.begin() as session:
        insert_user(session, "owner")
        insert_user(session, "admin", admin=True)
        add_personal(session, "personal", "owner", params='{"custom":true}')
        add_system(session, "system", params='{"shared":true}')
        session.flush()
        add_order(session, "owner-order", "owner", "personal", "image", 0)
        add_order(session, "member-order", "owner", "system", "image", 0)
        add_order(session, "admin-order", "admin", "system", "text", 0)

    factory = model_configs_unit_of_work_factory(sessions)
    statements: list[str] = []

    def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        promoted = promote_model_to_system(
            factory,
            ModelConfigActor("admin", True),
            "personal",
            id_factory=lambda: "promoted",
        )
        promote_select_count = sum(
            statement.lstrip().lower().startswith("select")
            for statement in statements
        )
        statements.clear()
        assert promoted["is_system"] is True

        session = sessions()
        try:
            assert session.query(ModelConfigRow).filter_by(id="personal").first() is None
            promoted_row = session.query(SystemModelRow).filter_by(id="promoted").one()
            assert promoted_row.params == '{"custom":true}'
            assert (
                session.query(UserModelOrderRow)
                .filter_by(model_id="personal")
                .count()
                == 0
            )
        finally:
            session.close()

        statements.clear()
        demoted = demote_system_model_to_personal(
            factory,
            ModelConfigActor("admin", True),
            "system",
            id_factory=lambda: "demoted",
        )
        demote_select_count = sum(
            statement.lstrip().lower().startswith("select")
            for statement in statements
        )
        assert promote_select_count == 3
        assert demote_select_count == 3
        assert demoted["user_id"] == "admin"
        assert demoted["is_system"] is False

        session = sessions()
        try:
            assert session.query(SystemModelRow).filter_by(id="system").first() is None
            personal = session.query(ModelConfigRow).filter_by(id="demoted").one()
            assert personal.user_id == "admin"
            assert personal.params == '{"shared":true}'
            assert session.query(UserModelOrderRow).filter_by(model_id="system").count() == 0
        finally:
            session.close()
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
        engine.dispose()
