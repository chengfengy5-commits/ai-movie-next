"""Unauthenticated FastAPI adapter for public configuration reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from haoai_backend.shared.errors import BusinessError

from . import application
from .domain import PublicSiteConfig, PublicSystemModel, PurchasePackage
from .ports import PublicSiteConfigUnitOfWorkFactory


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def _invoke(call: Callable[..., Any], *args: Any) -> Any:
    try:
        return call(*args)
    except BusinessError as exc:
        raise _http_error(exc) from exc


def _site_config_payload(config: PublicSiteConfig) -> dict[str, Any]:
    return {
        "site_title": config.site_title,
        "site_tagline": config.site_tagline,
        "storyboard_cost_multiplier": config.storyboard_cost_multiplier,
        "membership_benefits": config.membership_benefits,
        "payments_enabled": config.payments_enabled,
        "tutorial_url": config.tutorial_url,
        "purchase_qr_code_url": config.purchase_qr_code_url,
        "purchase_contact_info": config.purchase_contact_info,
    }


def _purchase_package_payload(package: PurchasePackage) -> dict[str, Any]:
    return {
        "id": package.id,
        "category": package.category,
        "name": package.name,
        "price": package.price,
        "credits": package.credits,
        "days": package.days,
        "sort_order": package.sort_order,
    }


def _system_model_payload(model: PublicSystemModel) -> dict[str, Any]:
    return {
        "id": model.id,
        "category": model.category,
        "name": model.name,
        "provider": model.provider,
        "model": model.model,
        "cost_per_call": model.cost_per_call,
        "description": model.description,
        "is_active": model.is_active,
        "sort_order": model.sort_order,
    }


def build_public_site_config_router(
    *,
    uow_factory: PublicSiteConfigUnitOfWorkFactory | None,
) -> APIRouter:
    """Build standalone routes; the application decides where to include them."""

    router = APIRouter(tags=["public-site-config"])

    @router.get("/api/site-config")
    def get_site_config() -> dict[str, Any]:
        config = _invoke(application.get_site_config, uow_factory)
        return _site_config_payload(config)

    @router.get("/api/purchase-packages")
    def get_purchase_packages(
        category: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        packages = _invoke(application.list_purchase_packages, uow_factory, category)
        return [_purchase_package_payload(package) for package in packages]

    @router.get("/api/system-models")
    def get_system_models() -> list[dict[str, Any]]:
        models = _invoke(application.list_system_models, uow_factory)
        return [_system_model_payload(model) for model in models]

    return router
