"""SQLAlchemy read adapter for public site configuration."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import PurchasePackage, SiteConfigRecord, SystemModelRecord
from .ports import PublicSiteConfigUnitOfWorkFactory
from .tables import purchase_packages, system_configs, system_models


class SqlAlchemyPublicSiteConfigUnitOfWork:
    """Run only the three public projections against an owned SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load_site_config(self) -> SiteConfigRecord | None:
        statement = (
            select(
                system_configs.c.site_title,
                system_configs.c.site_tagline,
                system_configs.c.storyboard_cost_multiplier,
                system_configs.c.membership_benefits,
                system_configs.c.payments_enabled,
                system_configs.c.tutorial_url,
                system_configs.c.purchase_qr_code_url,
                system_configs.c.purchase_contact_info,
            )
            .limit(1)
        )
        with self._session.no_autoflush:
            row = self._session.execute(statement).first()
        if row is None:
            return None

        values = row._mapping
        return SiteConfigRecord(
            site_title=values["site_title"],
            site_tagline=values["site_tagline"],
            storyboard_cost_multiplier=values["storyboard_cost_multiplier"],
            membership_benefits=values["membership_benefits"],
            payments_enabled=values["payments_enabled"],
            tutorial_url=values["tutorial_url"],
            purchase_qr_code_url=values["purchase_qr_code_url"],
            purchase_contact_info=values["purchase_contact_info"],
        )

    def list_purchase_packages(
        self,
        category: str | None,
    ) -> Sequence[PurchasePackage]:
        statement = (
            select(
                purchase_packages.c.id,
                purchase_packages.c.category,
                purchase_packages.c.name,
                purchase_packages.c.price,
                purchase_packages.c.credits,
                purchase_packages.c.days,
                purchase_packages.c.sort_order,
            )
            .where(purchase_packages.c.is_active == True)  # noqa: E712
        )
        if category:
            statement = statement.where(purchase_packages.c.category == category)
        statement = statement.order_by(purchase_packages.c.sort_order)

        with self._session.no_autoflush:
            rows = self._session.execute(statement).all()
        return tuple(
            PurchasePackage(
                id=row.id,
                category=row.category,
                name=row.name,
                price=row.price,
                credits=row.credits,
                days=row.days,
                sort_order=row.sort_order,
            )
            for row in rows
        )

    def list_system_models(self) -> Sequence[SystemModelRecord]:
        statement = (
            select(
                system_models.c.id,
                system_models.c.category,
                system_models.c.name,
                system_models.c.provider,
                system_models.c.model,
                system_models.c.cost_per_call,
                system_models.c.description,
                system_models.c.is_active,
                system_models.c.sort_order,
            )
            .order_by(system_models.c.sort_order, system_models.c.category)
        )
        with self._session.no_autoflush:
            rows = self._session.execute(statement).all()
        return tuple(
            SystemModelRecord(
                id=row.id,
                category=row.category,
                name=row.name,
                provider=row.provider,
                model=row.model,
                cost_per_call=row.cost_per_call,
                description=row.description,
                is_active=row.is_active,
                sort_order=row.sort_order,
            )
            for row in rows
        )

    def close(self) -> None:
        self._session.close()


def public_site_config_unit_of_work_factory(
    session_factory: Callable[[], Session],
) -> PublicSiteConfigUnitOfWorkFactory:
    """Bind the module to a supplied session factory without opening a session."""

    def create_unit_of_work() -> SqlAlchemyPublicSiteConfigUnitOfWork:
        return SqlAlchemyPublicSiteConfigUnitOfWork(session_factory())

    return create_unit_of_work
