"""Values used by the public site configuration read model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SiteConfigRecord:
    """Publicly readable values loaded from the singleton configuration row."""

    site_title: str | None
    site_tagline: str | None
    storyboard_cost_multiplier: int | None
    membership_benefits: str | None
    payments_enabled: bool | int | None
    tutorial_url: str | None
    purchase_qr_code_url: str | None
    purchase_contact_info: str | None


@dataclass(frozen=True, slots=True)
class PublicSiteConfig:
    """The exact public payload returned by the site configuration endpoint."""

    site_title: str
    site_tagline: str
    storyboard_cost_multiplier: int
    membership_benefits: str
    payments_enabled: bool
    tutorial_url: str
    purchase_qr_code_url: str
    purchase_contact_info: str


@dataclass(frozen=True, slots=True)
class PurchasePackage:
    """A public purchase package, retaining nullable legacy values."""

    id: str
    category: str
    name: str
    price: int
    credits: int | None
    days: int | None
    sort_order: int | None


@dataclass(frozen=True, slots=True)
class SystemModelRecord:
    """The public columns read from a system model row."""

    id: str
    category: str
    name: str
    provider: str
    model: str | None
    cost_per_call: int | None
    description: str | None
    is_active: bool | int | None
    sort_order: int | None


@dataclass(frozen=True, slots=True)
class PublicSystemModel:
    """A public system model with the legacy falsey-value defaults applied."""

    id: str
    category: str
    name: str
    provider: str
    model: str | None
    cost_per_call: int
    description: str
    is_active: bool
    sort_order: int
