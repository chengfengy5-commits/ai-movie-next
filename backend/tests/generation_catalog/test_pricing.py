from __future__ import annotations

import pytest

from haoai_backend.generation_catalog.domain import (
    CatalogError,
    CatalogModel,
    ModelCategory,
    ModelSource,
    QuoteRequest,
)
from haoai_backend.generation_catalog.pricing import PricingError, quote


def system_model(
    category: ModelCategory,
    *,
    base_cost: int | str | None = 11,
    params: dict[str, object] | str | None = None,
) -> CatalogModel:
    return CatalogModel(
        id="system-model",
        category=category,
        display_name="System display name",
        wire_model="wire/model-v3",
        owner_id=None,
        base_cost=base_cost,
        params=params,
        credential_configured=True,
    )


def test_fixed_quality_zero_falls_back_to_base_and_metadata_strings_are_accepted() -> None:
    model = system_model(
        ModelCategory.IMAGE,
        base_cost=17,
        params={
            "billing_mode": "fixed",
            "fixed_by_quality": {"1K": "0", "2K": "9"},
            "image_size": {
                "enabled": True,
                "default": "1K-square",
                "options": ["square", "portrait"],
            },
        },
    )

    zero_rate = quote(model, QuoteRequest("image", size="1K-square"))
    selected_rate = quote(model, QuoteRequest("image", size="2K-portrait"))

    assert zero_rate.amount == 17
    assert zero_rate.effective_parameters == {"size": "1K-square"}
    assert selected_rate.amount == 9
    assert selected_rate.effective_parameters == {"size": "2K-portrait"}


def test_video_quality_rate_zero_is_not_replaced_by_fallback() -> None:
    model = system_model(
        ModelCategory.VIDEO,
        base_cost=40,
        params={
            "billing_mode": "per_second",
            "per_second_by_quality": {"720p": "0"},
            "per_second_cost": "6",
        },
    )

    matched_zero = quote(
        model,
        QuoteRequest("video", duration=5, resolution="720p"),
    )
    unmatched_fallback = quote(
        model,
        QuoteRequest("video", duration=3, resolution="1080p"),
    )

    assert matched_zero.amount == 0
    assert matched_zero.effective_parameters == {"duration": 5, "resolution": "720p"}
    assert unmatched_fallback.amount == 18


def test_legacy_aistarslab_per_second_and_fixed_total_use_legacy_amounts() -> None:
    legacy_per_second = system_model(
        ModelCategory.VIDEO,
        base_cost=21,
        params={"aistarslab": {"billingType": "per_second", "perSecondCredits": "4"}},
    )
    fixed_total = system_model(
        ModelCategory.VIDEO,
        base_cost=21,
        params={"aistarslab": {"billingType": "fixed_total"}},
    )

    assert quote(
        legacy_per_second,
        QuoteRequest("video", duration=8),
    ).amount == 32
    assert quote(fixed_total, QuoteRequest("video")).amount == 21


def test_byok_is_zero_but_still_resolves_and_validates_effective_options() -> None:
    model = CatalogModel(
        id="owned-image",
        category="image",
        display_name="Caller image model",
        wire_model="caller-wire",
        owner_id="user-1",
        base_cost=999,
        credential_configured=True,
        params={
            "billing_mode": "fixed",
            "fixed_by_quality": {"2K": 88},
            "image_size": {
                "enabled": True,
                "default": "2K-portrait",
                "options": ["portrait"],
            },
        },
    )

    result = quote(model, QuoteRequest("image"))

    assert result.source is ModelSource.USER
    assert result.amount == 0
    assert result.effective_parameters == {"size": "2K-portrait"}


@pytest.mark.parametrize("duration", [1, 3600])
def test_video_duration_accepts_only_documented_integer_bounds(duration: int) -> None:
    model = system_model(
        ModelCategory.VIDEO,
        params={"billing_mode": "per_second", "per_second_cost": 2},
    )

    assert quote(model, QuoteRequest("video", duration=duration)).amount == 2 * duration


@pytest.mark.parametrize("duration", [0, 3601, True, 2.5, "2"])
def test_video_duration_rejects_out_of_range_and_non_integer_values(
    duration: object,
) -> None:
    model = system_model(
        ModelCategory.VIDEO,
        params={"billing_mode": "per_second", "per_second_cost": 2},
    )

    with pytest.raises(PricingError):
        quote(model, QuoteRequest("video", duration=duration))


def test_disabled_options_and_unsupported_values_are_rejected() -> None:
    model = system_model(
        ModelCategory.IMAGE,
        params={
            "billing_mode": "fixed",
            "fixed_by_quality": {"2K": 7},
            "image_size": {
                "enabled": False,
                "default": "2K-square",
                "options": ["square"],
            },
        },
    )

    with pytest.raises(PricingError, match="does not allow"):
        quote(model, QuoteRequest("image", size="2K-square"))

    enabled = system_model(
        ModelCategory.IMAGE,
        params={
            "image_size": {
                "enabled": True,
                "default": "2K-square",
                "options": ["square"],
            },
        },
    )
    assert quote(enabled, QuoteRequest("image")).effective_parameters == {
        "size": "2K-square"
    }
    with pytest.raises(PricingError, match="not supported"):
        quote(enabled, QuoteRequest("image", size="4K-wide"))


@pytest.mark.parametrize(
    "params",
    [
        {"billing_mode": []},
        {"billing_mode": "unknown"},
        {"billing_mode": "fixed", "fixed_by_quality": {"1K": True}},
        {"billing_mode": "per_second", "per_second_cost": -1},
        {"aistarslab": []},
        {"billing_mode": "per_second", "aistarslab": {"billingType": "fixed_total"}},
        {"max_reference_images": {"enabled": "yes"}},
        {"max_reference_images": {"default": 65}},
    ],
)
def test_malformed_billing_or_reference_metadata_fails_closed(
    params: dict[str, object],
) -> None:
    with pytest.raises(PricingError):
        quote(system_model(ModelCategory.VIDEO, params=params), QuoteRequest("video"))


def test_malformed_json_params_are_rejected() -> None:
    with pytest.raises(PricingError, match="valid JSON"):
        quote(system_model(ModelCategory.IMAGE, params="{"), QuoteRequest("image"))


def test_text_system_quote_uses_catalog_cost_and_text_byok_remains_free() -> None:
    system = system_model(ModelCategory.TEXT, base_cost="13")
    byok = CatalogModel(
        id="owned-text",
        category="text",
        display_name="Owned text",
        wire_model="owned-wire",
        owner_id="user-1",
        base_cost=800,
        credential_configured=True,
    )

    assert quote(system, QuoteRequest("text")).amount == 13
    assert quote(byok, QuoteRequest("text")).amount == 0


@pytest.mark.parametrize(
    ("category", "options"),
    [
        (ModelCategory.IMAGE, {"size": "https://provider.example/model"}),
        (ModelCategory.VIDEO, {"resolution": "https://provider.example/model"}),
    ],
)
def test_unconfigured_generation_options_cannot_smuggle_raw_endpoints(
    category: ModelCategory, options: dict[str, str]
) -> None:
    with pytest.raises(PricingError, match="invalid"):
        quote(system_model(category), QuoteRequest(category, **options))


@pytest.mark.parametrize(
    ("category", "owner_id", "raw_params"),
    [
        (category, owner_id, raw_params)
        for category in (ModelCategory.IMAGE, ModelCategory.VIDEO)
        for owner_id in (None, "user-1")
        for raw_params in (
            '{"execution_hint":{"temperature":1e309}}',
            '{"execution_hint":[{"temperature":-1e309}]}',
        )
    ],
)
def test_non_finite_json_exponents_are_rejected_before_any_quote(
    category: ModelCategory, owner_id: str | None, raw_params: str
) -> None:
    model = CatalogModel(
        id="catalog-model",
        category=category,
        display_name="Catalog model",
        wire_model="wire-model",
        owner_id=owner_id,
        base_cost=11,
        params=raw_params,
        credential_configured=True,
    )

    with pytest.raises(PricingError, match="finite JSON"):
        quote(model, QuoteRequest(category))


def test_finite_nested_json_decimal_nonpricing_metadata_remains_accepted() -> None:
    model = system_model(
        ModelCategory.IMAGE,
        base_cost=11,
        params=(
            '{"execution_hint":{"temperature":0.75,'
            '"range":[-1.25,1e-200]}}'
        ),
    )

    result = quote(model, QuoteRequest(ModelCategory.IMAGE))

    assert result.amount == 11
    assert result.effective_parameters == {}


def test_quote_repr_does_not_expose_secret_metadata_or_raw_endpoint() -> None:
    model = system_model(
        ModelCategory.IMAGE,
        params={
            "billing_mode": "fixed",
            "fixed_by_quality": {"1K": 4},
            "api_key": "secret-token-for-test",
            "headers": {"Authorization": "Bearer private-header"},
            "base_url": "https://credential.example/private",
        },
    )

    result = quote(model, QuoteRequest("image", size="1K-square"))
    rendered = repr(result)

    assert result.amount == 4
    assert "secret-token-for-test" not in rendered
    assert "private-header" not in rendered
    assert "credential.example" not in rendered


def test_catalog_model_defensively_freezes_nested_pricing_metadata() -> None:
    options = ["square"]
    params: dict[str, object] = {
        "image_size": {"enabled": True, "options": options, "default": "square"}
    }
    model = system_model(ModelCategory.IMAGE, params=params)

    options.append("portrait")
    assert model.params["image_size"]["options"] == ("square",)
    with pytest.raises(TypeError):
        model.params["image_size"]["enabled"] = False


def test_catalog_identity_rejects_a_raw_endpoint_value() -> None:
    with pytest.raises(CatalogError, match="endpoint URL"):
        CatalogModel(
            id="https://provider.example/model",
            category=ModelCategory.IMAGE,
            display_name="Public image model",
            wire_model="safe-wire-name",
            owner_id=None,
            base_cost=1,
        )
