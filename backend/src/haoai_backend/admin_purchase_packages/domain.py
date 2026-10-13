"""Framework-independent purchase-package records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AdminPurchasePackagesActor:
    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class PurchasePackage:
    id: str
    category: str
    name: str
    price: int
    credits: int | None
    days: int | None
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewPurchasePackage:
    id: str
    category: str
    name: str
    price: int
    credits: int | None
    days: int | None
    sort_order: int
    is_active: bool


@dataclass(frozen=True, slots=True)
class PurchasePackagePage:
    data: tuple[PurchasePackage, ...]
    total: int
