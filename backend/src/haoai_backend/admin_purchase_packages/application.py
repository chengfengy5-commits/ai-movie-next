"""Purchase-package use cases with explicit transaction ownership."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar
from uuid import uuid4

from .domain import NewPurchasePackage, PurchasePackage, PurchasePackagePage
from .errors import PurchasePackageNotFound
from .ports import (
    PackageIdFactory,
    PurchasePackagesUnitOfWork,
    PurchasePackagesUnitOfWorkFactory,
)

_Result = TypeVar("_Result")


def _with_unit_of_work(
    factory: PurchasePackagesUnitOfWorkFactory,
    operation: Callable[[PurchasePackagesUnitOfWork], _Result],
) -> _Result:
    unit_of_work = factory()
    try:
        result = operation(unit_of_work)
    except BaseException:
        try:
            unit_of_work.rollback()
        except BaseException:
            pass
        try:
            unit_of_work.close()
        except BaseException:
            pass
        raise

    unit_of_work.close()
    return result


def list_packages(
    uow_factory: PurchasePackagesUnitOfWorkFactory,
    *,
    sort_field: str = "sort_order",
    sort_order: str = "asc",
    skip: int = 0,
    limit: int = 50,
) -> PurchasePackagePage:
    def list_with(unit_of_work: PurchasePackagesUnitOfWork) -> PurchasePackagePage:
        total = unit_of_work.count_packages()
        data = unit_of_work.list_packages(
            sort_field=sort_field,
            sort_order=sort_order,
            offset=skip,
            limit=limit,
        )
        return PurchasePackagePage(tuple(data), total)

    return _with_unit_of_work(uow_factory, list_with)


def get_package(
    uow_factory: PurchasePackagesUnitOfWorkFactory, package_id: str
) -> PurchasePackage:
    def get_with(unit_of_work: PurchasePackagesUnitOfWork) -> PurchasePackage:
        package = unit_of_work.load_package(package_id)
        if package is None:
            raise PurchasePackageNotFound()
        return package

    return _with_unit_of_work(uow_factory, get_with)


def create_package(
    uow_factory: PurchasePackagesUnitOfWorkFactory,
    *,
    category: str,
    name: str,
    price: int,
    credits: int | None,
    days: int | None,
    sort_order: int,
    is_active: bool,
    id_factory: PackageIdFactory = lambda: str(uuid4()),
) -> PurchasePackage:
    def create_with(unit_of_work: PurchasePackagesUnitOfWork) -> PurchasePackage:
        package_id = id_factory()
        unit_of_work.add_package(
            NewPurchasePackage(
                id=package_id,
                category=category,
                name=name,
                price=price,
                credits=credits,
                days=days,
                sort_order=sort_order,
                is_active=is_active,
            )
        )
        unit_of_work.commit()
        package = unit_of_work.refresh_package(package_id)
        if package is None:
            raise RuntimeError("committed purchase package could not be refreshed")
        return package

    return _with_unit_of_work(uow_factory, create_with)


def update_package(
    uow_factory: PurchasePackagesUnitOfWorkFactory,
    package_id: str,
    changes: Mapping[str, Any],
) -> PurchasePackage:
    def update_with(unit_of_work: PurchasePackagesUnitOfWork) -> PurchasePackage:
        if not unit_of_work.update_package(package_id, changes):
            raise PurchasePackageNotFound()
        unit_of_work.commit()
        package = unit_of_work.refresh_package(package_id)
        if package is None:
            raise RuntimeError("committed purchase package could not be refreshed")
        return package

    return _with_unit_of_work(uow_factory, update_with)


def delete_package(
    uow_factory: PurchasePackagesUnitOfWorkFactory, package_id: str
) -> dict[str, str]:
    def delete_with(unit_of_work: PurchasePackagesUnitOfWork) -> dict[str, str]:
        if not unit_of_work.delete_package(package_id):
            raise PurchasePackageNotFound()
        unit_of_work.commit()
        return {"message": "删除成功"}

    return _with_unit_of_work(uow_factory, delete_with)
