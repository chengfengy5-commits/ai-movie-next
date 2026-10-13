from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.admin_purchase_packages.domain import NewPurchasePackage
from haoai_backend.admin_purchase_packages.persistence import (
    SqlAlchemyPurchasePackagesUnitOfWork,
)
from haoai_backend.admin_purchase_packages.tables import PurchasePackageRow


def make_session() -> tuple[Session, list[str]]:
    engine = create_engine("sqlite://")
    PurchasePackageRow.metadata.create_all(engine)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    return Session(engine), statements


def new_package(package_id: str, *, sort_order: int, name: str) -> NewPurchasePackage:
    return NewPurchasePackage(
        id=package_id,
        category="custom",
        name=name,
        price=-5,
        credits=None,
        days=None,
        sort_order=sort_order,
        is_active=True,
    )


def test_private_orm_defaults_all_fields_raw_sort_and_count_before_list() -> None:
    session, statements = make_session()
    try:
        uow = SqlAlchemyPurchasePackagesUnitOfWork(session)
        uow.add_package(new_package("b", sort_order=1, name="Bee"))
        uow.add_package(new_package("a", sort_order=9, name="Aye"))
        uow.commit()
        statements.clear()

        page_count = uow.count_packages()
        listed = uow.list_packages(
            sort_field="unknown_field", sort_order="asc", offset=0, limit=50
        )

        assert page_count == 2
        assert [row.id for row in listed] == ["a", "b"]
        assert listed[0].price == -5
        assert listed[0].category == "custom"
        assert listed[0].credits is None and listed[0].days is None
        assert listed[0].created_at is not None and listed[0].updated_at is not None
        assert "count(" in statements[0].lower()
        assert "order by purchase_packages.id asc" in statements[1].lower()

        descending = uow.list_packages(
            sort_field="sort_order", sort_order="desc", offset=0, limit=50
        )
        assert [row.id for row in descending] == ["a", "b"]
        legacy_other_case = uow.list_packages(
            sort_field="sort_order", sort_order="DESC", offset=0, limit=50
        )
        assert [row.id for row in legacy_other_case] == ["b", "a"]
    finally:
        session.close()


def test_mapped_non_column_attributes_keep_legacy_query_failure() -> None:
    session, _ = make_session()
    try:
        uow = SqlAlchemyPurchasePackagesUnitOfWork(session)
        for field in ("metadata", "registry"):
            with pytest.raises(ArgumentError):
                uow.list_packages(
                    sort_field=field, sort_order="asc", offset=0, limit=50
                )
    finally:
        session.close()


def test_net_dirty_controls_onupdate_and_explicit_none_hits_database_constraint() -> None:
    session, statements = make_session()
    try:
        uow = SqlAlchemyPurchasePackagesUnitOfWork(session)
        uow.add_package(new_package("same", sort_order=1, name="Same"))
        uow.commit()
        original = uow.load_package("same")
        assert original is not None
        original_updated_at = original.updated_at
        statements.clear()

        assert uow.update_package("same", {"name": "Same"})
        uow.commit()
        unchanged = uow.refresh_package("same")
        assert unchanged is not None
        assert unchanged.updated_at == original_updated_at
        assert not any(statement.lstrip().lower().startswith("update") for statement in statements)

        statements.clear()
        assert uow.update_package("same", {"name": "Changed"})
        uow.commit()
        changed = uow.refresh_package("same")
        assert changed is not None and changed.name == "Changed"
        assert any(statement.lstrip().lower().startswith("update") for statement in statements)
        assert any("updated_at" in statement.lower() for statement in statements if statement.lstrip().lower().startswith("update"))

        assert uow.update_package("same", {"name": None})
        with pytest.raises(IntegrityError):
            uow.commit()
        session.rollback()
    finally:
        session.close()
