"""Framework-independent query ports for public configuration reads."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypeAlias

from .domain import PurchasePackage, SiteConfigRecord, SystemModelRecord


class PublicSiteConfigUnitOfWork(Protocol):
    """Read-only queries backed by one explicitly owned session."""

    def load_site_config(self) -> SiteConfigRecord | None: ...

    def list_purchase_packages(
        self,
        category: str | None,
    ) -> Sequence[PurchasePackage]: ...

    def list_system_models(self) -> Sequence[SystemModelRecord]: ...

    def close(self) -> None: ...


PublicSiteConfigUnitOfWorkFactory: TypeAlias = Callable[
    [], PublicSiteConfigUnitOfWork
]
