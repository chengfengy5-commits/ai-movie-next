from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from haoai_backend.public_site_config import application
from haoai_backend.public_site_config.domain import (
    PublicSiteConfig,
    PublicSystemModel,
    PurchasePackage,
    SiteConfigRecord,
    SystemModelRecord,
)
from haoai_backend.public_site_config.errors import PublicSiteConfigError


@dataclass
class RecordingUnitOfWork:
    site_config: SiteConfigRecord | None = None
    packages: tuple[PurchasePackage, ...] = ()
    models: tuple[SystemModelRecord, ...] = ()
    categories: list[str | None] = field(default_factory=list)
    close_calls: int = 0

    def load_site_config(self) -> SiteConfigRecord | None:
        return self.site_config

    def list_purchase_packages(
        self,
        category: str | None,
    ) -> tuple[PurchasePackage, ...]:
        self.categories.append(category)
        return self.packages

    def list_system_models(self) -> tuple[SystemModelRecord, ...]:
        return self.models

    def close(self) -> None:
        self.close_calls += 1


def test_site_config_uses_fixed_defaults_when_no_row_exists() -> None:
    unit = RecordingUnitOfWork()

    result = application.get_site_config(lambda: unit)

    assert result == PublicSiteConfig(
        site_title="Hao AI - 无限可能",
        site_tagline="无限可能",
        storyboard_cost_multiplier=2,
        membership_benefits="",
        payments_enabled=True,
        tutorial_url="",
        purchase_qr_code_url="",
        purchase_contact_info="",
    )
    assert unit.close_calls == 1


def test_site_config_applies_falsey_fallbacks_but_preserves_disabled_payments() -> None:
    unit = RecordingUnitOfWork(
        site_config=SiteConfigRecord(
            site_title=None,
            site_tagline="",
            storyboard_cost_multiplier=0,
            membership_benefits=None,
            payments_enabled=0,
            tutorial_url="",
            purchase_qr_code_url=None,
            purchase_contact_info="",
        )
    )

    result = application.get_site_config(lambda: unit)

    assert result.site_title == "Hao AI - 无限可能"
    assert result.site_tagline == "无限可能"
    assert result.storyboard_cost_multiplier == 2
    assert result.membership_benefits == ""
    assert result.payments_enabled is False
    assert result.tutorial_url == ""
    assert result.purchase_qr_code_url == ""
    assert result.purchase_contact_info == ""
    assert unit.close_calls == 1


def test_purchase_packages_preserve_nullable_values_and_forward_raw_category() -> None:
    package = PurchasePackage(
        id="package-1",
        category="credits",
        name="100 积分",
        price=10,
        credits=None,
        days=None,
        sort_order=0,
    )
    unit = RecordingUnitOfWork(packages=(package,))

    result = application.list_purchase_packages(lambda: unit, " credits ")

    assert result == (package,)
    assert unit.categories == [" credits "]
    assert unit.close_calls == 1


def test_models_include_inactive_rows_and_apply_only_legacy_falsey_defaults() -> None:
    unit = RecordingUnitOfWork(
        models=(
            SystemModelRecord(
                id="model-1",
                category="image",
                name="Image",
                provider="provider",
                model=None,
                cost_per_call=None,
                description=None,
                is_active=False,
                sort_order=None,
            ),
        )
    )

    result = application.list_system_models(lambda: unit)

    assert result == (
        PublicSystemModel(
            id="model-1",
            category="image",
            name="Image",
            provider="provider",
            model=None,
            cost_per_call=0,
            description="",
            is_active=False,
            sort_order=0,
        ),
    )
    assert unit.close_calls == 1


def test_missing_unit_of_work_fails_before_database_access() -> None:
    with pytest.raises(PublicSiteConfigError) as failure:
        application.get_site_config(None)

    assert failure.value.status_code == 503
