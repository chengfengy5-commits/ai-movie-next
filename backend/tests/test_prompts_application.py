from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.prompts.application import (
    create_prompt_config,
    delete_prompt_config,
    demote_system_prompt_to_personal,
    get_prompt_config,
    list_prompt_configs,
    promote_prompt_to_system,
    reorder_prompt_configs,
    update_prompt_config,
)
from haoai_backend.prompts.domain import PromptActor, PromptCreateCommand
from haoai_backend.prompts.errors import PromptNotFound
from haoai_backend.prompts.persistence import SqlAlchemyPromptsUnitOfWork
from haoai_backend.prompts.tables import (
    metadata,
    prompt_configs,
    system_prompts,
    user_prompt_orders,
    users,
)

STAMP = datetime(2026, 10, 13, 9, 30)


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'prompts-application.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(engine)
    session_factory = sessionmaker(
        engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    with engine.begin() as connection:
        connection.execute(insert(users), [{"id": "user-a"}, {"id": "user-b"}])
    yield engine, session_factory
    engine.dispose()


def seed_personal(
    engine: Engine,
    prompt_id: str,
    user_id: str = "user-a",
    category: str = "story",
    *,
    name: str | None = None,
    sort_order: int = 0,
    system_prompt: str | None = "system",
    user_prompt: str | None = "user",
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(prompt_configs).values(
                id=prompt_id,
                user_id=user_id,
                category=category,
                name=name or prompt_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                sort_order=sort_order,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )


def seed_system(
    engine: Engine,
    prompt_id: str,
    category: str = "story",
    *,
    name: str | None = None,
    sort_order: int = 0,
    is_active: bool = True,
    system_prompt: str | None = "system",
    user_prompt: str | None = "user",
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(system_prompts).values(
                id=prompt_id,
                category=category,
                name=name or prompt_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                sort_order=sort_order,
                is_active=is_active,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )


def add_order(
    engine: Engine,
    order_id: str,
    prompt_id: str,
    *,
    user_id: str = "user-a",
    category: str = "story",
    sort_order: int = 0,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(user_prompt_orders).values(
                id=order_id,
                user_id=user_id,
                prompt_id=prompt_id,
                category=category,
                sort_order=sort_order,
            )
        )


def unit_of_work_factory(session_factory: sessionmaker[Session]):
    return lambda: SqlAlchemyPromptsUnitOfWork(session_factory())


def test_list_merges_records_and_replays_duplicate_order_ids(database) -> None:
    engine, session_factory = database
    seed_system(engine, "system-off", sort_order=0, is_active=False)
    seed_system(engine, "system-on", sort_order=1)
    seed_personal(engine, "personal-a", sort_order=0)
    seed_personal(engine, "personal-b", sort_order=2)
    seed_personal(engine, "other-user", "user-b", sort_order=0)
    add_order(engine, "order-1", "personal-b", sort_order=0)
    add_order(engine, "order-stale", "missing", sort_order=1)
    add_order(engine, "order-2", "personal-b", sort_order=2)
    add_order(engine, "order-3", "personal-a", category="other", sort_order=3)

    result = list_prompt_configs(
        unit_of_work_factory(session_factory),
        PromptActor("user-a", is_superuser=True),
        "",
    )

    assert [item["id"] for item in result] == [
        "personal-b",
        "personal-b",
        "personal-a",
        "system-off",
        "system-on",
    ]
    assert result[0]["is_system"] is False
    assert "is_active" not in result[0]
    assert result[3]["is_active"] is False
    assert result[4]["is_active"] is True


def test_category_filter_keeps_whitespace_exact(database) -> None:
    engine, session_factory = database
    seed_personal(engine, "plain", category="story")
    seed_personal(engine, "space", category=" ")
    seed_system(engine, "system-space", category=" ")
    seed_system(engine, "system-story", category="story")

    result = list_prompt_configs(
        unit_of_work_factory(session_factory),
        PromptActor("user-a", is_superuser=False),
        " ",
    )

    assert {item["id"] for item in result} == {"space", "system-space"}


def test_single_read_is_owned_personal_only_even_for_admin(database) -> None:
    engine, session_factory = database
    seed_personal(engine, "own")
    seed_personal(engine, "someone-elses", "user-b")
    factory = unit_of_work_factory(session_factory)

    assert get_prompt_config(factory, PromptActor("user-a", True), "own")["id"] == "own"
    with pytest.raises(PromptNotFound):
        get_prompt_config(factory, PromptActor("user-a", True), "someone-elses")
    with pytest.raises(PromptNotFound):
        get_prompt_config(factory, PromptActor("user-a", True), "system-only")


def test_create_ignores_requested_order_and_only_admin_creates_system_rows(database) -> None:
    engine, session_factory = database
    seed_personal(engine, "existing-personal", sort_order=6)
    seed_system(engine, "existing-system", sort_order=9)
    factory = unit_of_work_factory(session_factory)
    command = PromptCreateCommand(
        category="story",
        name="new prompt",
        system_prompt=None,
        user_prompt="body",
        is_system=True,
    )

    personal_result = create_prompt_config(
        factory,
        PromptActor("user-a", is_superuser=False),
        command,
        now=lambda: STAMP,
        id_factory=lambda: "personal-new",
    )
    system_result = create_prompt_config(
        factory,
        PromptActor("user-a", is_superuser=True),
        command,
        now=lambda: STAMP,
        id_factory=lambda: "system-new",
    )

    with engine.connect() as connection:
        personal = connection.execute(
            select(prompt_configs).where(prompt_configs.c.id == "personal-new")
        ).mappings().one()
        system = connection.execute(
            select(system_prompts).where(system_prompts.c.id == "system-new")
        ).mappings().one()
    assert personal["user_id"] == "user-a"
    assert personal["sort_order"] == 7
    assert system["sort_order"] == 10
    assert system["is_active"] is True
    assert personal_result["id"] == "personal-new"
    assert "is_active" not in personal_result
    assert system_result["id"] == "system-new"
    assert "is_active" not in system_result


def test_update_uses_personal_first_and_admin_system_fallback_preserves_none(database) -> None:
    engine, session_factory = database
    seed_personal(engine, "shared-id", name="personal")
    seed_system(engine, "shared-id", name="system")
    seed_system(engine, "disabled", is_active=False)
    factory = unit_of_work_factory(session_factory)
    admin = PromptActor("user-a", is_superuser=True)

    own_result = update_prompt_config(factory, admin, "shared-id", {"name": "changed-own"})
    system_result = update_prompt_config(
        factory,
        admin,
        "disabled",
        {"system_prompt": None},
    )

    with engine.connect() as connection:
        own_name = connection.execute(
            select(prompt_configs.c.name).where(prompt_configs.c.id == "shared-id")
        ).scalar_one()
        system_name = connection.execute(
            select(system_prompts.c.name).where(system_prompts.c.id == "shared-id")
        ).scalar_one()
        disabled = connection.execute(
            select(system_prompts).where(system_prompts.c.id == "disabled")
        ).mappings().one()
    assert own_name == "changed-own"
    assert system_name == "system"
    assert own_result["name"] == "changed-own"
    assert system_result["system_prompt"] is None
    assert "is_active" not in system_result
    assert disabled["is_active"] is False


def test_ordinary_delete_leaves_order_references_and_reorder_keeps_duplicates(database) -> None:
    engine, session_factory = database
    seed_personal(engine, "to-delete")
    add_order(engine, "existing-order", "to-delete")
    factory = unit_of_work_factory(session_factory)

    assert delete_prompt_config(factory, PromptActor("user-a", False), "to-delete") == {
        "message": "提示词配置已删除"
    }
    with engine.connect() as connection:
        remaining = connection.execute(select(user_prompt_orders.c.prompt_id)).all()
    assert remaining == [("to-delete",)]

    seed_personal(engine, "reordered")
    ids = iter(("new-order-1", "new-order-2", "new-order-3"))
    assert reorder_prompt_configs(
        factory,
        PromptActor("user-a", False),
        "story",
        ["reordered", "reordered", "stale"],
        id_factory=lambda: next(ids),
    ) == {"message": "排序已更新"}
    with engine.connect() as connection:
        orders = connection.execute(
            select(user_prompt_orders.c.id, user_prompt_orders.c.prompt_id)
            .where(user_prompt_orders.c.user_id == "user-a")
            .order_by(user_prompt_orders.c.sort_order)
        ).all()
        sort_order = connection.execute(
            select(prompt_configs.c.sort_order).where(prompt_configs.c.id == "reordered")
        ).scalar_one()
    assert orders == [
        ("new-order-1", "reordered"),
        ("new-order-2", "reordered"),
        ("new-order-3", "stale"),
    ]
    assert sort_order == 1


def test_admin_transitions_clean_all_references_in_the_same_transaction(database) -> None:
    engine, session_factory = database
    seed_personal(engine, "source-personal", "user-b")
    add_order(engine, "source-order-a", "source-personal", user_id="user-a")
    add_order(engine, "source-order-b", "source-personal", user_id="user-b")
    ids = iter(("promoted-id", "demoted-id"))
    factory = unit_of_work_factory(session_factory)
    admin = PromptActor("user-a", is_superuser=True)

    promoted = promote_prompt_to_system(
        factory,
        admin,
        "source-personal",
        now=lambda: STAMP,
        id_factory=lambda: next(ids),
    )
    assert promoted["id"] == "promoted-id"
    assert promoted["is_system"] is True
    assert set(promoted) == {
        "id",
        "user_id",
        "category",
        "name",
        "system_prompt",
        "user_prompt",
        "sort_order",
        "is_system",
        "created_at",
        "updated_at",
    }
    with engine.begin() as connection:
        connection.execute(
            insert(user_prompt_orders).values(
                id="promoted-order",
                user_id="user-b",
                prompt_id="promoted-id",
                category="story",
                sort_order=0,
            )
        )

    demoted = demote_system_prompt_to_personal(
        factory,
        admin,
        "promoted-id",
        now=lambda: STAMP,
        id_factory=lambda: next(ids),
    )

    with engine.connect() as connection:
        personal_ids = connection.execute(select(prompt_configs.c.id)).all()
        system_ids = connection.execute(select(system_prompts.c.id)).all()
        order_refs = connection.execute(select(user_prompt_orders.c.prompt_id)).all()
    assert demoted["id"] == "demoted-id"
    assert demoted["user_id"] == "user-a"
    assert demoted["is_system"] is False
    assert demoted["is_active"] is True
    assert set(demoted) == {
        "id",
        "user_id",
        "category",
        "name",
        "system_prompt",
        "user_prompt",
        "sort_order",
        "is_system",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert ("source-personal",) not in personal_ids
    assert ("demoted-id",) in personal_ids
    assert system_ids == []
    assert order_refs == []
