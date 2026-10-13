"""FastAPI routes for the purchase-package administrator boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import resolve_admin_actor
from .domain import AdminPurchasePackagesActor, PurchasePackage
from .errors import PurchasePackagesUnavailable
from .ports import PurchasePackagesUnitOfWorkFactory
from .schemas import PurchasePackageCreate, PurchasePackageUpdate


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def _payload(package: PurchasePackage) -> dict[str, Any]:
    return {
        "id": package.id,
        "category": package.category,
        "name": package.name,
        "price": package.price,
        "credits": package.credits,
        "days": package.days,
        "sort_order": package.sort_order,
        "is_active": package.is_active,
        "created_at": package.created_at,
        "updated_at": package.updated_at,
    }


def build_admin_purchase_packages_router(
    *,
    authentication_service: AuthenticationService | None,
    uow_factory: PurchasePackagesUnitOfWorkFactory | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin-purchase-packages"])

    async def require_admin(
        authorization: str | None = Header(default=None),
    ) -> AdminPurchasePackagesActor:
        if authentication_service is None or uow_factory is None:
            raise _http_error(PurchasePackagesUnavailable())
        try:
            actor = await run_in_threadpool(
                resolve_admin_actor, authentication_service, authorization or ""
            )
        except BusinessError as exc:
            raise _http_error(exc) from exc
        if not isinstance(actor, AdminPurchasePackagesActor) or not actor.user_id or not actor.is_superuser:
            raise HTTPException(status_code=503, detail="管理员身份端口未正确接线")
        return actor

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if uow_factory is None:
            raise _http_error(PurchasePackagesUnavailable())
        try:
            return call(uow_factory, *args, **kwargs)
        except BusinessError as exc:
            raise _http_error(exc) from exc

    @router.get("/purchase-packages")
    def list_purchase_packages(
        sort_field: str = "sort_order",
        sort_order: str = "asc",
        skip: int = 0,
        limit: int = 50,
        _actor: AdminPurchasePackagesActor = Depends(require_admin),
    ) -> dict[str, Any]:
        page = invoke(
            application.list_packages,
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {"data": [_payload(item) for item in page.data], "total": page.total}

    @router.get("/purchase-packages/{pkg_id}")
    def get_purchase_package(
        pkg_id: str,
        _actor: AdminPurchasePackagesActor = Depends(require_admin),
    ) -> dict[str, Any]:
        return _payload(invoke(application.get_package, pkg_id))

    @router.post("/purchase-packages")
    def create_purchase_package(
        body: PurchasePackageCreate,
        _actor: AdminPurchasePackagesActor = Depends(require_admin),
    ) -> dict[str, Any]:
        return _payload(invoke(application.create_package, **body.model_dump()))

    @router.put("/purchase-packages/{pkg_id}")
    def update_purchase_package(
        pkg_id: str,
        body: PurchasePackageUpdate,
        _actor: AdminPurchasePackagesActor = Depends(require_admin),
    ) -> dict[str, Any]:
        return _payload(
            invoke(
                application.update_package,
                pkg_id,
                body.model_dump(exclude_unset=True),
            )
        )

    @router.delete("/purchase-packages/{pkg_id}")
    def delete_purchase_package(
        pkg_id: str,
        _actor: AdminPurchasePackagesActor = Depends(require_admin),
    ) -> dict[str, str]:
        return invoke(application.delete_package, pkg_id)

    return router
