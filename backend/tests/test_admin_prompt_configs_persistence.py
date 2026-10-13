from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.admin_prompt_configs.domain import create_values
from haoai_backend.admin_prompt_configs.persistence import (
    SqlAlchemyAdminPromptConfigsUnitOfWork,
)
from haoai_backend.admin_prompt_configs.tables import (
    Base,
    PromptConfigRow,
    UserRow,
    metadata,
)


NOW = datetime(2026, 10, 13, 9, 30)


def new_database(path: Path) -> tuple[object, Session]:
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    session.add_all(
        [
            UserRow(
                id=user_id,
                username=user_id,
                email=f"{user_id}@example.test",
                hashed_password="unused",
                is_superuser=False,
                membership_type="free",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=NOW,
                password_updated_at=None,
            )
            for user_id in ("owner-a", "owner-b")
        ]
    )
    session.commit()
    return engine, session


def seed_prompts(session: Session, count: int = 11) -> None:
    session.add_all(
        [
            PromptConfigRow(
                id=f"prompt-{index:02d}",
                user_id="owner-a" if index % 2 == 0 else "owner-b",
                category="text" if index % 3 else "image",
                name=f"Prompt {index:02d}",
                system_prompt=f"system-{index:02d}",
                user_prompt=None if index == 4 else f"user-{index:02d}",
                sort_order=index,
                created_at=NOW + timedelta(minutes=index),
                updated_at=NOW + timedelta(minutes=index),
            )
            for index in range(count)
        ]
    )
    session.commit()


def test_default_page_counts_all_rows_and_truthy_owner_filter_is_exact(tmp_path: Path) -> None:
    engine, session = new_database(tmp_path / "prompt-config-list.sqlite")
    try:
        seed_prompts(session)
        uow = SqlAlchemyAdminPromptConfigsUnitOfWork(session)
        records, total = uow.list_prompt_configs(
            user_id=None,
            sort_field="created_at",
            sort_order="desc",
            skip=0,
            limit=10,
        )
        assert total == 11
        assert len(records) == 10
        assert records[0].id == "prompt-10"
        assert records[0].updated_at == NOW + timedelta(minutes=10)
        assert records[0].user_id == "owner-a"
        assert set(records[4].__dataclass_fields__) == {
            "id", "user_id", "category", "name", "system_prompt", "user_prompt",
            "sort_order", "created_at", "updated_at",
        }

        filtered, filtered_total = uow.list_prompt_configs(
            user_id="owner-b",
            sort_field="id",
            sort_order="asc",
            skip=0,
            limit=100,
        )
        assert filtered_total == 5
        assert {record.user_id for record in filtered} == {"owner-b"}
        unfiltered, unfiltered_total = uow.list_prompt_configs(
            user_id="",
            sort_field="id",
            sort_order="asc",
            skip=0,
            limit=100,
        )
        assert unfiltered_total == 11
        assert len(unfiltered) == 11
        assert not session.new
    finally:
        session.close()
        engine.dispose()


def test_raw_sort_fallback_case_rule_and_private_orm_metadata(tmp_path: Path) -> None:
    engine, session = new_database(tmp_path / "prompt-config-sort.sqlite")
    try:
        seed_prompts(session)
        assert PromptConfigRow.__tablename__ == "prompt_configs"
        assert PromptConfigRow.metadata is metadata
        assert PromptConfigRow.registry is Base.registry
        assert set(PromptConfigRow.__table__.columns.keys()) == {
            "id", "user_id", "category", "name", "system_prompt", "user_prompt",
            "sort_order", "created_at", "updated_at",
        }
        assert PromptConfigRow.user.property.mapper.class_ is UserRow
        assert set(UserRow.__table__.columns.keys()) == {
            "id", "username", "email", "hashed_password", "is_superuser",
            "membership_type", "membership_expires_at", "avatar_url", "bio",
            "created_at", "password_updated_at",
        }
        uow = SqlAlchemyAdminPromptConfigsUnitOfWork(session)

        by_updated, total = uow.list_prompt_configs(
            user_id=None,
            sort_field="updated_at",
            sort_order="DESC",
            skip=0,
            limit=3,
        )
        assert total == 11
        assert [row.id for row in by_updated] == ["prompt-00", "prompt-01", "prompt-02"]

        fallback, _ = uow.list_prompt_configs(
            user_id=None,
            sort_field="not_a_model_attribute",
            sort_order="asc",
            skip=0,
            limit=3,
        )
        assert [row.id for row in fallback] == ["prompt-00", "prompt-01", "prompt-02"]

        with pytest.raises(ArgumentError):
            uow.list_prompt_configs(
                user_id=None,
                sort_field="metadata",
                sort_order="asc",
                skip=0,
                limit=1,
            )
    finally:
        session.close()
        engine.dispose()


def test_create_preserves_explicit_nulls_for_real_sqlalchemy_defaults(tmp_path: Path) -> None:
    engine, session = new_database(tmp_path / "prompt-config-defaults.sqlite")
    try:
        values = create_values(
            {
                "category": "text",
                "name": None,
                "system_prompt": None,
                "user_prompt": None,
                "sort_order": None,
                "id": "client-id",
                "user_id": "owner-b",
                "created_at": NOW - timedelta(days=3),
            }
        )
        assert values == {
            "category": "text",
            "name": None,
            "system_prompt": None,
            "user_prompt": None,
            "sort_order": None,
        }
        uow = SqlAlchemyAdminPromptConfigsUnitOfWork(session)
        uow.add_prompt_config("server-id", "owner-a", values)
        uow.commit()
        record = uow.refresh_prompt_config("server-id")
        assert record is not None
        assert record.id == "server-id"
        assert record.user_id == "owner-a"
        assert record.name == "提示词"
        assert record.sort_order == 0
        assert record.system_prompt is None and record.user_prompt is None
        assert record.created_at is not None and record.updated_at is not None

        uow.add_prompt_config(
            "null-category",
            "owner-a",
            create_values({"category": None}),
        )
        with pytest.raises(IntegrityError):
            uow.commit()
        uow.rollback()
    finally:
        session.close()
        engine.dispose()


def test_dynamic_mapped_updates_delete_and_invalid_relationship_assignment(tmp_path: Path) -> None:
    engine, session = new_database(tmp_path / "prompt-config-update.sqlite")
    try:
        seed_prompts(session, count=1)
        uow = SqlAlchemyAdminPromptConfigsUnitOfWork(session)
        row = session.query(PromptConfigRow).filter(PromptConfigRow.id == "prompt-00").first()
        assert row is not None
        old_created_at = NOW - timedelta(days=2)
        target_user = session.query(UserRow).filter(UserRow.id == "owner-b").first()
        assert target_user is not None
        identity_key = inspect(row).identity_key
        assert session.identity_map.get(identity_key) is row

        assert uow.update_prompt_config("prompt-00", {"category": None})
        with pytest.raises(IntegrityError):
            uow.commit()
        uow.rollback()
        assert session.query(PromptConfigRow).filter(
            PromptConfigRow.id == "prompt-00"
        ).first() is row

        assert uow.update_prompt_config(
            "prompt-00",
            {
                "id": "client-id",
                "user_id": "owner-b",
                "category": "updated",
                "name": "Renamed",
                "system_prompt": None,
                "user_prompt": "new user prompt",
                "sort_order": 19,
                "created_at": old_created_at,
                "updated_at": NOW,
                "metadata": "instance metadata",
                "registry": "instance registry",
                "__table__": "instance table",
                "unknown_field": "ignored",
            },
        )
        assert session.identity_map.get(identity_key) is row
        uow.commit()
        updated = uow.refresh_prompt_config("prompt-00")
        assert updated is not None
        assert updated.id == "prompt-00"
        assert updated.user_id == "owner-a"
        assert updated.category == "updated"
        assert updated.name == "Renamed"
        assert updated.system_prompt is None
        assert updated.user_prompt == "new user prompt"
        assert updated.sort_order == 19
        assert updated.created_at == old_created_at
        assert updated.updated_at != NOW
        assert row.metadata == "instance metadata"
        assert row.registry == "instance registry"
        assert row.__table__ == "instance table"

        # The old hasattr(row, key) loop exposed the complete owner relationship too.
        assert uow.update_prompt_config("prompt-00", {"user": target_user})
        uow.commit()
        changed_owner = uow.refresh_prompt_config("prompt-00")
        assert changed_owner is not None and changed_owner.user_id == "owner-b"

        assert uow.update_prompt_config("prompt-00", {"user": None})
        with pytest.raises(IntegrityError):
            uow.commit()
        uow.rollback()

        with pytest.raises(AttributeError):
            uow.update_prompt_config("prompt-00", {"user": {"id": "owner-a"}})

        assert uow.delete_prompt_config("prompt-00")
        uow.commit()
        assert uow.get_prompt_config("prompt-00") is None
        assert not uow.delete_prompt_config("prompt-00")
    finally:
        session.close()
        engine.dispose()
