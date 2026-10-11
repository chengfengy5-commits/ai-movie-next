"""Pure integer server-side pricing for image, video, and text models."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from .domain import (
    CatalogError,
    CatalogModel,
    ModelCategory,
    ModelSource,
    QuoteRequest,
    ServerQuote,
)


class PricingError(CatalogError):
    """Persisted pricing metadata or requested options are invalid."""


_QUALITY_PREFIX = re.compile(r"^(1K|2K|4K)(?:-|$)")
_NONNEGATIVE_INTEGER = re.compile(r"^[0-9]+$")
_PRICING_RATE_KEYS = frozenset({"fixed_by_quality", "per_second_by_quality"})


def quote(model: CatalogModel, request: QuoteRequest) -> ServerQuote:
    """Return a server-derived quote and the validated effective options.

    The caller supplies a catalog value, never a client price. User-owned
    models are BYOK and quote zero only after their credential-presence fact
    has been verified by the selection boundary.
    """
    if model.category != request.category:
        raise PricingError("model and quote categories do not match")

    source = ModelSource.USER if model.owner_id is not None else ModelSource.SYSTEM
    if source is ModelSource.USER and not model.credential_configured:
        raise PricingError("user model has no configured credential")

    if request.category is ModelCategory.TEXT:
        amount = 0 if source is ModelSource.USER else _credit_value(
            0 if model.base_cost is None else model.base_cost, "cost_per_call"
        )
        return ServerQuote(
            category=request.category,
            source=source,
            amount=amount,
            effective_parameters={},
        )

    params = _parse_params(model.params)
    _validate_billing_configuration(params)
    _validate_category_billing(request.category, params)
    _validate_reference_configuration(params)
    effective = _effective_generation_parameters(params, request)

    if source is ModelSource.USER:
        amount = 0
    else:
        amount = _system_price(model.base_cost, params, request.category, effective)

    return ServerQuote(
        category=request.category,
        source=source,
        amount=amount,
        effective_parameters=effective,
    )


def _parse_params(raw: Mapping[str, object] | str | None) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw, parse_constant=_reject_json_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PricingError("model params must be a valid JSON object") from exc
        value = _copy_json(parsed, "model params")
    else:
        value = _copy_json(raw, "model params")
    if not isinstance(value, dict):
        raise PricingError("model params must be a JSON object")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON constant: {value}")


def _copy_json(value: object, field_name: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PricingError(f"{field_name} must contain finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PricingError(f"{field_name} keys must be strings")
            copied[key] = _copy_json(item, field_name)
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_json(item, field_name) for item in value]
    raise PricingError(f"{field_name} contains a non-JSON value")


def _validate_billing_configuration(params: dict[str, Any]) -> None:
    billing_mode = params.get("billing_mode")
    if billing_mode is not None and not isinstance(billing_mode, str):
        raise PricingError("model billing_mode is invalid")
    if billing_mode is not None and billing_mode not in {"fixed", "per_second"}:
        raise PricingError("model billing_mode must be fixed or per_second")

    for field_name in _PRICING_RATE_KEYS:
        rates = params.get(field_name)
        if rates is not None and not isinstance(rates, dict):
            raise PricingError(f"model {field_name} must be an object")
        if isinstance(rates, dict):
            for quality, rate in rates.items():
                if (
                    not isinstance(quality, str)
                    or not quality.strip()
                    or len(quality) > 32
                ):
                    raise PricingError(f"model {field_name} quality key is invalid")
                _credit_value(rate, f"{field_name}.{quality}")

    if "per_second_cost" in params:
        _credit_value(params["per_second_cost"], "per_second_cost")

    legacy = params.get("aistarslab")
    if legacy is None:
        return
    if not isinstance(legacy, dict):
        raise PricingError("model aistarslab must be an object")
    billing_type = legacy.get("billingType")
    if billing_type is not None and not isinstance(billing_type, str):
        raise PricingError("model aistarslab.billingType is invalid")
    if billing_type is not None and billing_type not in {
        "fixed",
        "fixed_total",
        "per_second",
    }:
        raise PricingError("model aistarslab.billingType is unsupported")
    if billing_type == "per_second" and "perSecondCredits" not in legacy:
        raise PricingError("model aistarslab.perSecondCredits is required")
    if "perSecondCredits" in legacy:
        _credit_value(legacy["perSecondCredits"], "aistarslab.perSecondCredits")


def _validate_reference_configuration(params: dict[str, Any]) -> None:
    config = params.get("max_reference_images")
    if config is None:
        return
    if not isinstance(config, dict):
        raise PricingError("model max_reference_images must be an object")
    enabled = config.get("enabled", True)
    if not isinstance(enabled, bool):
        raise PricingError("model max_reference_images.enabled must be a boolean")
    if "default" in config:
        default = config["default"]
        if type(default) is not int or not 1 <= default <= 64:
            raise PricingError("model max_reference_images.default is invalid")
    options = config.get("options")
    if options is not None:
        if (
            not isinstance(options, list)
            or not options
            or any(type(item) is not int or not 1 <= item <= 64 for item in options)
        ):
            raise PricingError("model max_reference_images.options is invalid")
        if "default" in config and config["default"] not in options:
            raise PricingError("model max_reference_images.default must be an option")


def _validate_category_billing(
    category: ModelCategory, params: dict[str, Any]
) -> None:
    billing_mode = params.get("billing_mode")
    legacy = params.get("aistarslab")
    legacy_type = legacy.get("billingType") if isinstance(legacy, dict) else None

    if category is ModelCategory.IMAGE:
        if billing_mode == "per_second" or legacy_type == "per_second":
            raise PricingError("image models cannot use per-second billing")
        return
    if category is ModelCategory.VIDEO:
        if billing_mode == "fixed" and legacy_type == "per_second":
            raise PricingError("video billing metadata conflicts")
        if billing_mode == "per_second" and legacy_type in {"fixed", "fixed_total"}:
            raise PricingError("video billing metadata conflicts")
        return
    raise PricingError("pricing is defined only for image and video models")


def _effective_generation_parameters(
    params: dict[str, Any], request: QuoteRequest
) -> dict[str, object]:
    effective: dict[str, object] = {}
    size = _effective_config_value(params, "image_size", request.size)
    duration = _effective_config_value(params, "duration", request.duration)
    resolution = _effective_config_value(params, "resolution", request.resolution)

    if size is not None:
        if not isinstance(size, str):
            raise PricingError("generation size must be a string")
        size = size.strip()
        if not size or len(size) > 100 or "://" in size:
            raise PricingError("generation size is invalid")
        effective["size"] = size
    if duration is not None:
        if type(duration) is not int or not 1 <= duration <= 3600:
            raise PricingError("video duration must be an integer from 1 to 3600")
        effective["duration"] = duration
    if resolution is not None:
        if not isinstance(resolution, str):
            raise PricingError("video resolution must be a string")
        resolution = resolution.strip()
        if not resolution or len(resolution) > 32 or "://" in resolution:
            raise PricingError("video resolution is invalid")
        effective["resolution"] = resolution

    if request.category is ModelCategory.IMAGE and size:
        options = _options(params.get("image_size"))
        if options and not _matches_option(size, options):
            raise PricingError("requested image size is not supported")
    if request.category is ModelCategory.VIDEO:
        duration_options = _options(params.get("duration"))
        if duration is not None and duration_options and duration not in duration_options:
            raise PricingError("requested video duration is not supported")
        resolution_options = _options(params.get("resolution"))
        if (
            resolution is not None
            and resolution_options
            and resolution not in resolution_options
        ):
            raise PricingError("requested video resolution is not supported")
        size_options = _options(params.get("image_size"))
        if size is not None and size_options and not _matches_option(size, size_options):
            raise PricingError("requested video aspect ratio is not supported")
    return effective


def _effective_config_value(
    params: dict[str, Any], field_name: str, requested: object
) -> object:
    config = params.get(field_name)
    if config is None:
        return requested
    if not isinstance(config, dict):
        raise PricingError(f"model {field_name} configuration must be an object")
    enabled = config.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise PricingError(f"model {field_name}.enabled must be a boolean")
    options = config.get("options")
    if options is not None and (
        not isinstance(options, list)
        or not options
        or any(not _safe_config_scalar(item) for item in options)
    ):
        raise PricingError(f"model {field_name}.options are invalid")
    if "default" in config and not _safe_config_scalar(config["default"]):
        raise PricingError(f"model {field_name}.default is invalid")
    if enabled is False:
        if requested is not None:
            raise PricingError(f"model does not allow the {field_name} option")
        return None
    value = requested if requested is not None else config.get("default")
    if (
        value is not None
        and isinstance(options, list)
        and value not in options
        and not _matches_option(value, options)
    ):
        raise PricingError(f"requested {field_name} is not supported")
    return value


def _options(config: object) -> list[object]:
    if not isinstance(config, dict):
        return []
    options = config.get("options")
    return options if isinstance(options, list) else []


def _matches_option(value: object, options: list[object]) -> bool:
    if value in options:
        return True
    if not isinstance(value, str):
        return False
    match = _QUALITY_PREFIX.match(value)
    return bool(match and "-" in value and value.split("-", 1)[1] in options)


def _system_price(
    base_cost: int | str | None,
    params: dict[str, Any],
    category: ModelCategory,
    effective: dict[str, object],
) -> int:
    base = _credit_value(base_cost, "cost_per_call")
    quality = (
        _image_quality(effective.get("size"))
        if category is ModelCategory.IMAGE
        else effective.get("resolution")
    )
    billing_mode = params.get("billing_mode")

    if category is ModelCategory.VIDEO and billing_mode == "per_second":
        duration = effective.get("duration")
        if duration is None:
            raise PricingError("per-second video models require a duration")
        rate = _quality_rate(params.get("per_second_by_quality"), quality)
        if rate is None:
            if "per_second_cost" not in params:
                raise PricingError(
                    "video rate does not match and per_second_cost is missing"
                )
            rate = _credit_value(params["per_second_cost"], "per_second_cost")
        return rate * duration

    if category is ModelCategory.IMAGE:
        selected = (
            _quality_rate(params.get("fixed_by_quality"), quality)
            if billing_mode == "fixed"
            else None
        )
        return selected if selected is not None and selected > 0 else base

    if category is ModelCategory.VIDEO:
        legacy = params.get("aistarslab") or {}
        if billing_mode is None and legacy.get("billingType") == "per_second":
            duration = effective.get("duration")
            if duration is None:
                raise PricingError("legacy per-second video models require a duration")
            rate = _credit_value(
                legacy.get("perSecondCredits", 0), "aistarslab.perSecondCredits"
            )
            return rate * duration

        selected = (
            _quality_rate(params.get("fixed_by_quality"), quality)
            if billing_mode == "fixed"
            else None
        )
        return selected if selected is not None and selected > 0 else base

    raise PricingError("unsupported model billing category")


def _image_quality(size: object) -> str | None:
    if not isinstance(size, str) or not size:
        return None
    match = _QUALITY_PREFIX.match(size.strip())
    return match.group(1) if match else None


def _quality_rate(rates: object, quality: object) -> int | None:
    if rates is None:
        return None
    if not isinstance(rates, dict):
        raise PricingError("quality rates must be an object")
    if not isinstance(quality, str) or quality not in rates:
        return None
    return _credit_value(rates[quality], f"quality rate {quality}")


def _credit_value(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise PricingError(f"{field_name} must be a non-negative integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and _NONNEGATIVE_INTEGER.fullmatch(value):
        result = int(value)
    else:
        raise PricingError(f"{field_name} must be a non-negative integer")
    if result < 0:
        raise PricingError(f"{field_name} must not be negative")
    return result


def _safe_config_scalar(value: object) -> bool:
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= 256 and "://" not in value
    return False
