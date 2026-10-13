"""Pydantic request contracts copied from the existing public route."""

from __future__ import annotations

from pydantic import BaseModel


class PurchasePackageCreate(BaseModel):
    category: str
    name: str
    price: int = 0
    credits: int | None = None
    days: int | None = None
    sort_order: int = 0
    is_active: bool = True


class PurchasePackageUpdate(BaseModel):
    category: str | None = None
    name: str | None = None
    price: int | None = None
    credits: int | None = None
    days: int | None = None
    sort_order: int | None = None
    is_active: bool | None = None
