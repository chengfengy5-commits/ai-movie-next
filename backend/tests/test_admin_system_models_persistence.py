from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_system_models.persistence import (
    SqlAlchemyAdminSystemModelUnitOfWork,
)
from haoai_backend.admin_system_models.tables import (
    SystemModelRow,
    metadata,
    system_models,
)


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.refresh_count = 0
        self.query_count = 0
        self.close_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()

    def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_count += 1
        super().refresh(instance, *args, **kwargs)

    def query(self, *entities: Any, **kwargs: Any):
        self.query_count += 1
        return super().query(*entities, **kwargs)

    def close(self) -> None:
        self.close_count += 1
        super().close()


def make_database(path: Path):
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(engine)
    maker = sessionmaker(engine, class_=ObservedSession, expire_on_commit=False)

    def make_session() -> ObservedSession:
        return maker()

    return engine, make_session


def values(index: int, *, api_key: str | None = None) -> dict[str, Any]:
    return {
        "category": "video" if index % 2 else "text",
        "name": f"Model {index}",
        "provider": "vendor-b" if index % 3 == 0 else "vendor-a",
        "api_key": api_key if api_key is not None else f"gAAAAA-fixture-{index}",
        "model": f"model-{index}",
        "base_url": f"https://example.test/{index}",
        "cost_per_call": index,
        "description": f"description-{index}",
        "is_active": index % 2 == 0,
        "sort_order": index,
        "params": None,
        "created_at": datetime(2026, 10, 1) + timedelta(minutes=index),
        "updated_at": datetime(2026, 10, 1) + timedelta(minutes=index),
    }


def test_private_table_maps_all_columns_and_queries_legacy_pagination_filters(
    tmp_path: Path,
) -> None:
    assert tuple(system_models.columns.keys()) == (
        "id",
        "category",
        "name",
        "provider",
        "api_key",
        "model",
        "base_url",
        "cost_per_call",
        "description",
        "is_active",
        "sort_order",
        "params",
        "created_at",
        "updated_at",
    )

    engine, make_session = make_database(tmp_path / "models.sqlite")
    try:
        with make_session() as session:
            session.add_all(
                SystemModelRow(id=f"model-{index}", **values(index))
                for index in range(11)
            )
            session.commit()
            session.commit_count = 0

        session = make_session()
        unit = SqlAlchemyAdminSystemModelUnitOfWork(session)
        page, total = unit.list_models(
            category=None,
            provider=None,
            is_active=None,
            skip=0,
            limit=10,
        )
        assert total == 11
        assert len(page) == 10
        assert page[0].sort_order == 0
        assert page[-1].sort_order == 9
        assert page[0].api_key == "gAAAAA-fixture-0"

        last_page, last_total = unit.list_models(
            category=None,
            provider=None,
            is_active=None,
            skip=10,
            limit=10,
        )
        assert last_total == 11
        assert [row.sort_order for row in last_page] == [10]

        filtered, filtered_total = unit.list_models(
            category="video",
            provider="vendor-a",
            is_active=False,
            skip=1,
            limit=1,
        )
        assert filtered_total == 3
        assert len(filtered) == 1
        assert filtered[0].id == "model-5"

        blank_filters, blank_total = unit.list_models(
            category="",
            provider="",
            is_active=False,
            skip=0,
            limit=20,
        )
        assert blank_total == 5
        assert all(row.is_active is False for row in blank_filters)
        assert session.commit_count == 0
        assert session.refresh_count == 0
        session.close()
    finally:
        engine.dispose()


def test_real_session_create_update_and_delete_have_exact_write_readback_shape(
    tmp_path: Path,
) -> None:
    engine, make_session = make_database(tmp_path / "models-crud.sqlite")
    try:
        session = make_session()
        unit = SqlAlchemyAdminSystemModelUnitOfWork(session)

        created_row = unit.add_model("created", values(20))
        unit.commit()
        created = unit.refresh_model(created_row)
        assert created.id == "created"
        assert created.api_key == "gAAAAA-fixture-20"
        assert len(created.__dataclass_fields__) == 14
        assert session.commit_count == 1
        assert session.refresh_count == 1

        row = unit.load_model_for_update("created")
        assert row is not None
        unit.set_model_values(
            row,
            {"name": "Updated", "description": None, "params": None},
        )
        unit.commit()
        updated = unit.refresh_model(row)
        assert updated.name == "Updated"
        assert updated.description is None
        assert updated.params is None
        assert session.commit_count == 2
        assert session.refresh_count == 2

        session.query_count = 0
        session.refresh_count = 0
        assert unit.delete_model("created") is True
        unit.commit()
        assert session.query_count == 1
        assert session.commit_count == 3
        assert session.refresh_count == 0
        assert unit.delete_model("created") is False
        assert session.query_count == 2
        session.close()
    finally:
        engine.dispose()
