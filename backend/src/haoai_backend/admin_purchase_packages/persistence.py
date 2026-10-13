"""SQLAlchemy adapter preserving the legacy query and flush semantics."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import NewPurchasePackage, PurchasePackage
from .ports import PurchasePackagesUnitOfWork
from .tables import PurchasePackageRow


def _record(row: PurchasePackageRow) -> PurchasePackage:
    return PurchasePackage(
        id=row.id,
        category=row.category,
        name=row.name,
        price=row.price,
        credits=row.credits,
        days=row.days,
        sort_order=row.sort_order,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyPurchasePackagesUnitOfWork(PurchasePackagesUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session
        self._loaded: dict[str, PurchasePackageRow] = {}

    def count_packages(self) -> int:
        return int(self._session.query(PurchasePackageRow).count())

    def list_packages(
        self, *, sort_field: str, sort_order: str, offset: int, limit: int
    ) -> tuple[PurchasePackage, ...]:
        column = getattr(PurchasePackageRow, sort_field, PurchasePackageRow.id)
        direction = desc(column) if sort_order == "desc" else asc(column)
        rows = (
            self._session.query(PurchasePackageRow)
            .order_by(direction)
            .offset(offset)
            .limit(limit)
            .all()
        )
        for row in rows:
            self._loaded[row.id] = row
        return tuple(_record(row) for row in rows)

    def load_package(self, package_id: str) -> PurchasePackage | None:
        row = (
            self._session.query(PurchasePackageRow)
            .filter(PurchasePackageRow.id == package_id)
            .first()
        )
        if row is None:
            return None
        self._loaded[row.id] = row
        return _record(row)

    def add_package(self, package: NewPurchasePackage) -> None:
        row = PurchasePackageRow(
            id=package.id,
            category=package.category,
            name=package.name,
            price=package.price,
            credits=package.credits,
            days=package.days,
            sort_order=package.sort_order,
            is_active=package.is_active,
        )
        self._loaded[row.id] = row
        self._session.add(row)

    def update_package(self, package_id: str, changes: Mapping[str, Any]) -> bool:
        row = (
            self._session.query(PurchasePackageRow)
            .filter(PurchasePackageRow.id == package_id)
            .first()
        )
        if row is None:
            return False
        self._loaded[row.id] = row
        for key, value in changes.items():
            setattr(row, key, value)
        return True

    def delete_package(self, package_id: str) -> bool:
        row = (
            self._session.query(PurchasePackageRow)
            .filter(PurchasePackageRow.id == package_id)
            .first()
        )
        if row is None:
            return False
        self._loaded[row.id] = row
        self._session.delete(row)
        return True

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def refresh_package(self, package_id: str) -> PurchasePackage | None:
        row = self._loaded.get(package_id)
        if row is None:
            return None
        self._session.refresh(row)
        return _record(row)

    def close(self) -> None:
        self._session.close()


def purchase_packages_uow_factory(
    session_factory: Callable[[], Session],
) -> Callable[[], SqlAlchemyPurchasePackagesUnitOfWork]:
    def create() -> SqlAlchemyPurchasePackagesUnitOfWork:
        return SqlAlchemyPurchasePackagesUnitOfWork(session_factory())

    return create
