"""Persistence ports for administrator purchase packages."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, TypeAlias

from .domain import NewPurchasePackage, PurchasePackage


class PurchasePackagesUnitOfWork(Protocol):
    def count_packages(self) -> int: ...

    def list_packages(
        self, *, sort_field: str, sort_order: str, offset: int, limit: int
    ) -> Sequence[PurchasePackage]: ...

    def load_package(self, package_id: str) -> PurchasePackage | None: ...

    def add_package(self, package: NewPurchasePackage) -> None: ...

    def update_package(self, package_id: str, changes: Mapping[str, Any]) -> bool: ...

    def delete_package(self, package_id: str) -> bool: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def refresh_package(self, package_id: str) -> PurchasePackage | None: ...

    def close(self) -> None: ...


PurchasePackagesUnitOfWorkFactory: TypeAlias = Callable[[], PurchasePackagesUnitOfWork]
PackageIdFactory: TypeAlias = Callable[[], str]
