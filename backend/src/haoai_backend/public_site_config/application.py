"""Use cases for the unauthenticated public site configuration reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .domain import (
    PublicSiteConfig,
    PublicSystemModel,
    PurchasePackage,
    SiteConfigRecord,
    SystemModelRecord,
)
from .errors import PublicSiteConfigError
from .ports import PublicSiteConfigUnitOfWork, PublicSiteConfigUnitOfWorkFactory

T = TypeVar("T")

_DEFAULT_SITE_TITLE = "Hao AI - 无限可能"
_DEFAULT_SITE_TAGLINE = "无限可能"


def _read_only(
    factory: PublicSiteConfigUnitOfWorkFactory | None,
    operation: Callable[[PublicSiteConfigUnitOfWork], T],
) -> T:
    if factory is None:
        raise PublicSiteConfigError(503, "公开网站配置服务尚未接线")

    unit_of_work = factory()
    try:
        return operation(unit_of_work)
    finally:
        unit_of_work.close()


def get_site_config(
    factory: PublicSiteConfigUnitOfWorkFactory | None,
) -> PublicSiteConfig:
    def operation(unit_of_work: PublicSiteConfigUnitOfWork) -> PublicSiteConfig:
        record = unit_of_work.load_site_config()
        if record is None:
            return PublicSiteConfig(
                site_title=_DEFAULT_SITE_TITLE,
                site_tagline=_DEFAULT_SITE_TAGLINE,
                storyboard_cost_multiplier=2,
                membership_benefits="",
                payments_enabled=True,
                tutorial_url="",
                purchase_qr_code_url="",
                purchase_contact_info="",
            )
        return _public_site_config(record)

    return _read_only(factory, operation)


def _public_site_config(record: SiteConfigRecord) -> PublicSiteConfig:
    return PublicSiteConfig(
        site_title=record.site_title or _DEFAULT_SITE_TITLE,
        site_tagline=record.site_tagline or _DEFAULT_SITE_TAGLINE,
        storyboard_cost_multiplier=record.storyboard_cost_multiplier or 2,
        membership_benefits=record.membership_benefits or "",
        payments_enabled=bool(record.payments_enabled),
        tutorial_url=record.tutorial_url or "",
        purchase_qr_code_url=record.purchase_qr_code_url or "",
        purchase_contact_info=record.purchase_contact_info or "",
    )


def list_purchase_packages(
    factory: PublicSiteConfigUnitOfWorkFactory | None,
    category: str | None = None,
) -> tuple[PurchasePackage, ...]:
    return _read_only(
        factory,
        lambda unit_of_work: tuple(unit_of_work.list_purchase_packages(category)),
    )


def list_system_models(
    factory: PublicSiteConfigUnitOfWorkFactory | None,
) -> tuple[PublicSystemModel, ...]:
    return _read_only(factory, _load_public_system_models)


def _load_public_system_models(
    unit_of_work: PublicSiteConfigUnitOfWork,
) -> tuple[PublicSystemModel, ...]:
    return tuple(
        _public_system_model(record)
        for record in unit_of_work.list_system_models()
    )


def _public_system_model(record: SystemModelRecord) -> PublicSystemModel:
    return PublicSystemModel(
        id=record.id,
        category=record.category,
        name=record.name,
        provider=record.provider,
        model=record.model,
        cost_per_call=record.cost_per_call or 0,
        description=record.description or "",
        is_active=bool(record.is_active),
        sort_order=record.sort_order or 0,
    )
