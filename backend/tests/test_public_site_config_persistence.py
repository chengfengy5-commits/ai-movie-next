from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    insert,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, registry, sessionmaker

from haoai_backend.public_site_config.application import (
    get_site_config,
    list_purchase_packages,
    list_system_models,
)
from haoai_backend.public_site_config.persistence import (
    SqlAlchemyPublicSiteConfigUnitOfWork,
    public_site_config_unit_of_work_factory,
)
from haoai_backend.public_site_config.tables import (
    metadata as projection_metadata,
    system_models as system_model_projection,
)


class TrackingSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


@dataclass
class PublicSiteConfigDatabase:
    engine: Engine
    physical_metadata: MetaData
    tables: dict[str, Table]
    session_factory: Any
    sessions: list[TrackingSession] = field(default_factory=list)

    def seed(self, table_name: str, *rows: dict[str, Any]) -> None:
        table = self.tables[table_name]
        with self.engine.begin() as connection:
            for row in rows:
                connection.execute(insert(table).values(**row))

    def snapshot(self) -> dict[str, tuple[dict[str, Any], ...]]:
        with self.engine.connect() as connection:
            return {
                name: tuple(
                    dict(row)
                    for row in connection.execute(select(table)).mappings()
                )
                for name, table in self.tables.items()
            }


@pytest.fixture
def public_site_config_database(tmp_path: Path):
    physical_metadata = MetaData()
    tables = {
        "system_configs": Table(
            "system_configs",
            physical_metadata,
            Column("id", String(36), primary_key=True),
            Column("muse_token", String(500)),
            Column("site_title", String(200)),
            Column("site_tagline", String(500)),
            Column("storyboard_cost_multiplier", Integer),
            Column("membership_benefits", Text),
            Column("payments_enabled", Integer),
            Column("tutorial_url", String(500)),
            Column("purchase_qr_code_url", String(500)),
            Column("purchase_contact_info", Text),
            Column("oss_access_key_id", String(200)),
            Column("oss_access_key_secret", String(200)),
        ),
        "purchase_packages": Table(
            "purchase_packages",
            physical_metadata,
            Column("id", String(36), primary_key=True),
            Column("category", String(20), nullable=False),
            Column("name", String(100), nullable=False),
            Column("price", Integer, nullable=False),
            Column("credits", Integer),
            Column("days", Integer),
            Column("sort_order", Integer, nullable=False),
            Column("is_active", Boolean, nullable=False),
            Column("created_at", DateTime),
            Column("updated_at", DateTime),
        ),
        "system_models": Table(
            "system_models",
            physical_metadata,
            Column("id", String(36), primary_key=True),
            Column("category", String(20), nullable=False),
            Column("name", String(255), nullable=False),
            Column("provider", String(50), nullable=False),
            Column("api_key", String(500), nullable=False),
            Column("model", String(255)),
            Column("base_url", String(500)),
            Column("cost_per_call", Integer, nullable=False),
            Column("description", Text),
            Column("is_active", Boolean, nullable=False),
            Column("sort_order", Integer, nullable=False),
            Column("params", Text),
            Column("created_at", DateTime),
            Column("updated_at", DateTime),
        ),
    }
    engine = create_engine(f"sqlite:///{tmp_path / 'public-site-config.sqlite'}")
    physical_metadata.create_all(engine)
    base_factory = sessionmaker(
        bind=engine,
        class_=TrackingSession,
        autoflush=True,
        expire_on_commit=False,
    )
    database = PublicSiteConfigDatabase(
        engine=engine,
        physical_metadata=physical_metadata,
        tables=tables,
        session_factory=None,
    )

    def open_session() -> TrackingSession:
        session = base_factory()
        database.sessions.append(session)
        return session

    database.session_factory = open_session
    try:
        yield database
    finally:
        engine.dispose()


def test_real_sqlite_reads_match_legacy_payloads_without_business_writes(
    public_site_config_database: PublicSiteConfigDatabase,
) -> None:
    database = public_site_config_database
    database.seed(
        "system_configs",
        {
            "id": "config",
            "muse_token": "private-config-token",
            "site_title": "自定义标题",
            "site_tagline": "短句",
            "storyboard_cost_multiplier": 3,
            "membership_benefits": "<b>会员权益</b>",
            "payments_enabled": 0,
            "tutorial_url": "/tutorial",
            "purchase_qr_code_url": "/qr.png",
            "purchase_contact_info": "联系管理员",
            "oss_access_key_id": "private-access-key",
            "oss_access_key_secret": "private-secret",
        },
    )
    database.seed(
        "purchase_packages",
        {
            "id": "package-credit",
            "category": "credits",
            "name": "积分包",
            "price": 10,
            "credits": 100,
            "days": None,
            "sort_order": 2,
            "is_active": True,
        },
        {
            "id": "package-member",
            "category": "membership",
            "name": "月卡",
            "price": 30,
            "credits": None,
            "days": 30,
            "sort_order": 1,
            "is_active": True,
        },
        {
            "id": "package-disabled",
            "category": "credits",
            "name": "隐藏包",
            "price": 1,
            "credits": 1,
            "days": None,
            "sort_order": 0,
            "is_active": False,
        },
    )
    database.seed(
        "system_models",
        {
            "id": "model-video",
            "category": "video",
            "name": "Video",
            "provider": "provider-b",
            "api_key": "secret-model-key-b",
            "model": None,
            "base_url": "https://private-b.invalid",
            "cost_per_call": 0,
            "description": None,
            "is_active": True,
            "sort_order": 1,
            "params": "secret-params-b",
        },
        {
            "id": "model-image",
            "category": "image",
            "name": "Image",
            "provider": "provider-a",
            "api_key": "secret-model-key-a",
            "model": "image-v1",
            "base_url": "https://private-a.invalid",
            "cost_per_call": 8,
            "description": "描述",
            "is_active": False,
            "sort_order": 1,
            "params": "secret-params-a",
        },
    )
    before = database.snapshot()
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement.lower())

    event.listen(database.engine, "before_cursor_execute", capture)
    try:
        factory = public_site_config_unit_of_work_factory(database.session_factory)
        config = get_site_config(factory)
        packages = list_purchase_packages(factory)
        models = list_system_models(factory)
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)

    assert config.site_title == "自定义标题"
    assert config.membership_benefits == "<b>会员权益</b>"
    assert config.payments_enabled is False
    assert config.tutorial_url == "/tutorial"
    assert [package.id for package in packages] == [
        "package-member",
        "package-credit",
    ]
    assert packages[0].credits is None
    assert packages[1].days is None
    assert [model.id for model in models] == ["model-image", "model-video"]
    assert models[0].is_active is False
    assert models[0].cost_per_call == 8
    assert models[1].model is None
    assert models[1].cost_per_call == 0
    assert models[1].description == ""
    assert all(session.close_calls == 1 for session in database.sessions)
    assert database.snapshot() == before
    assert not any(
        statement.lstrip().startswith(("insert ", "update ", "delete "))
        for statement in statements
    )
    model_selects = [
        statement
        for statement in statements
        if statement.startswith("select system_models.")
    ]
    assert len(model_selects) == 1
    assert all(
        secret_column not in model_selects[0]
        for secret_column in ("api_key", "base_url", "params")
    )
    assert "muse_token" not in " ".join(statements)
    assert "oss_access_key_secret" not in " ".join(statements)


def test_empty_database_returns_defaults_and_factory_creation_is_inert(
    public_site_config_database: PublicSiteConfigDatabase,
) -> None:
    database = public_site_config_database
    factory = public_site_config_unit_of_work_factory(database.session_factory)
    assert database.sessions == []

    config = get_site_config(factory)

    assert config.site_title == "Hao AI - 无限可能"
    assert config.site_tagline == "无限可能"
    assert config.storyboard_cost_multiplier == 2
    assert config.membership_benefits == ""
    assert config.payments_enabled is True
    assert config.tutorial_url == ""
    assert config.purchase_qr_code_url == ""
    assert config.purchase_contact_info == ""
    assert len(database.sessions) == 1
    assert database.sessions[0].close_calls == 1


def test_package_category_is_exact_and_falsey_categories_do_not_filter(
    public_site_config_database: PublicSiteConfigDatabase,
) -> None:
    database = public_site_config_database
    database.seed(
        "purchase_packages",
        {
            "id": "credits",
            "category": "credits",
            "name": "credits",
            "price": 1,
            "credits": 1,
            "days": None,
            "sort_order": 2,
            "is_active": True,
        },
        {
            "id": "membership",
            "category": "membership",
            "name": "membership",
            "price": 2,
            "credits": None,
            "days": 7,
            "sort_order": 1,
            "is_active": True,
        },
    )
    factory = public_site_config_unit_of_work_factory(database.session_factory)

    assert [item.id for item in list_purchase_packages(factory, "")] == [
        "membership",
        "credits",
    ]
    assert [
        item.id for item in list_purchase_packages(factory, "credits")
    ] == ["credits"]
    assert list_purchase_packages(factory, " credits ") == ()


def test_supplied_session_query_does_not_autoflush_or_commit_pending_state(
    public_site_config_database: PublicSiteConfigDatabase,
) -> None:
    database = public_site_config_database
    mapper_registry = registry(metadata=database.physical_metadata)
    PendingConfig = type(
        "PendingConfig",
        (),
        {"__table__": database.tables["system_configs"]},
    )
    mapper_registry.map_imperatively(PendingConfig, database.tables["system_configs"])
    session = database.session_factory()
    pending = PendingConfig(
        id="pending-config",
        site_title="pending",
        muse_token="private",
    )
    session.add(pending)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement.lower())

    event.listen(database.engine, "before_cursor_execute", capture)
    unit = SqlAlchemyPublicSiteConfigUnitOfWork(session)
    try:
        assert unit.load_site_config() is None
        assert pending in session.new
        assert session.in_transaction()
        assert not any(
            statement.lstrip().startswith(("insert ", "update ", "delete "))
            for statement in statements
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)
        session.expunge(pending)
        unit.close()

    assert session.close_calls == 1


def test_module_metadata_is_a_private_public_projection() -> None:
    assert set(projection_metadata.tables) == {
        "system_configs",
        "purchase_packages",
        "system_models",
    }
    assert set(system_model_projection.c.keys()) == {
        "id",
        "category",
        "name",
        "provider",
        "model",
        "cost_per_call",
        "description",
        "is_active",
        "sort_order",
    }
    assert not {"api_key", "base_url", "params"} & set(
        system_model_projection.c.keys()
    )
