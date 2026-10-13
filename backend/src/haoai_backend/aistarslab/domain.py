"""Pure compatibility rules for the legacy AIStarsLab routes."""

from __future__ import annotations

import json
from typing import Any


def duration_options(duration: dict[str, Any] | None) -> list[int]:
    """Convert a duration range to the options shown by the legacy UI."""
    values = duration or {}
    options = values.get("options")
    if options:
        return [int(option) for option in options]

    lower = values.get("min")
    upper = values.get("max")
    if lower is None and upper is None:
        return []
    lower = int(lower) if lower is not None else 4
    upper = int(upper) if upper is not None else 15
    step = 1 if upper - lower <= 12 else 5
    result = list(range(lower, upper + 1, step))
    if result[-1] != upper:
        result.append(upper)
    return result


def short_line_title(title: str | None) -> str:
    value = (title or "").strip()
    for prefix in ("图片-", "视频-"):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def build_video_params_snapshot(
    channel: str,
    model_name: str,
    model_config: dict[str, Any],
    line_title: str = "",
    default_quality: str | None = None,
) -> str:
    qualities = model_config.get("qualities") or []
    quality_names = [
        item.get("quality") for item in qualities if isinstance(item, dict)
    ] or [item for item in qualities if isinstance(item, str)]
    aspect_ratios = model_config.get("aspectRatios") or []
    duration = model_config.get("duration") or {}
    modes = model_config.get("modes") or []
    image_limit = model_config.get("inputImagesMax")
    video_limit = model_config.get("inputVideosMax")
    audio_limit = model_config.get("inputAudiosMax")

    if not default_quality and quality_names:
        default_quality = quality_names[0]

    billing_type = None
    per_second_credits = 0
    fixed_credits = 0
    per_second_by_quality: dict[str, int] = {}
    fixed_by_quality: dict[str, int] = {}
    for quality in qualities:
        if not isinstance(quality, dict):
            continue
        pricing = quality.get("pricing") or {}
        if pricing.get("type") == "per_second" and pricing.get("credits") is not None:
            per_second_by_quality[str(quality.get("quality"))] = int(
                pricing.get("credits")
            )
        elif (
            pricing.get("type") == "fixed_total"
            and pricing.get("credits") is not None
        ):
            fixed_by_quality[str(quality.get("quality"))] = int(
                pricing.get("credits")
            )
        if quality.get("quality") != default_quality:
            continue
        billing_type = pricing.get("type")
        if billing_type == "fixed_total":
            fixed_credits = pricing.get("credits") or 0
        else:
            per_second_credits = pricing.get("credits") or 0

    duration_values = duration_options(duration)
    params = {
        "image_size": {
            "enabled": True,
            "default": aspect_ratios[0] if aspect_ratios else "16:9",
            "options": aspect_ratios or ["16:9"],
        },
        "duration": {
            "enabled": True,
            "default": duration_values[0] if duration_values else 5,
            "options": duration_values or [5],
        },
        "resolution": {
            "enabled": True,
            "default": default_quality or "720p",
            "options": quality_names or ["720p"],
        },
        "max_reference_images": {
            "enabled": True,
            "default": image_limit if image_limit is not None else 9,
        },
        "aistarslab": {
            "channel": channel,
            "model": model_name,
            "label": model_config.get("label") or model_name,
            "lineTitle": line_title or "",
            "modes": modes,
            "aspectRatios": aspect_ratios,
            "qualities": quality_names,
            "pricing": model_config.get("pricing")
            or [item for item in qualities if isinstance(item, dict)],
            "inputImagesMax": image_limit,
            "inputVideosMax": video_limit,
            "inputAudiosMax": audio_limit,
            "billingType": billing_type or "per_second",
            "perSecondCredits": per_second_credits,
            "fixedCredits": fixed_credits,
        },
        "billing_mode": "per_second" if billing_type == "per_second" else "fixed",
        "per_second_cost": (
            per_second_credits if billing_type == "per_second" else fixed_credits
        ),
        "per_second_by_quality": per_second_by_quality,
        "fixed_by_quality": fixed_by_quality,
    }
    return json.dumps(params, ensure_ascii=False)


def apply_video_billing_multiplier(
    params_value: str, multiplier: float
) -> tuple[str, str, Any, Any, Any]:
    billing_type, per_second, fixed, default_duration = "per_second", 0, 0, 5
    try:
        params = json.loads(params_value)
        aistarslab = params.get("aistarslab") or {}
        billing_type = aistarslab.get("billingType") or "per_second"
        per_second = aistarslab.get("perSecondCredits") or 0
        fixed = aistarslab.get("fixedCredits") or 0
        default_duration = (params.get("duration") or {}).get("default", 5)
        if billing_type == "per_second":
            params["billing_mode"] = "per_second"
            params["per_second_cost"] = int(round(per_second * multiplier))
            for quality, value in (params.get("per_second_by_quality") or {}).items():
                params["per_second_by_quality"][quality] = int(round(value * multiplier))
        else:
            params["billing_mode"] = "fixed"
            params["per_second_cost"] = int(round(fixed * multiplier))
            for quality, value in (params.get("fixed_by_quality") or {}).items():
                params["fixed_by_quality"][quality] = int(round(value * multiplier))
        params_value = json.dumps(params, ensure_ascii=False)
    except Exception:
        pass
    return params_value, billing_type, per_second, fixed, default_duration


def build_image_params_snapshot(
    channel: str,
    model_name: str,
    model_config: dict[str, Any],
    line_title: str = "",
    default_quality: str | None = None,
) -> str:
    qualities = model_config.get("qualities") or []
    quality_names = [
        item.get("quality") for item in qualities if isinstance(item, dict)
    ] or [item for item in qualities if isinstance(item, str)]
    aspect_ratios = model_config.get("aspectRatios") or []
    n_range = model_config.get("n") or {}
    image_limit = model_config.get("inputImagesMax")

    if not default_quality and quality_names:
        default_quality = quality_names[0]

    fixed_by_quality: dict[str, int] = {}
    fixed_credits = 0
    n_count = max(int((n_range or {}).get("max") or 1), 1)
    for quality in qualities:
        if not isinstance(quality, dict):
            continue
        pricing = quality.get("pricing") or {}
        if pricing.get("type") == "fixed_total" and pricing.get("credits") is not None:
            fixed_by_quality[str(quality.get("quality"))] = (
                int(pricing.get("credits")) * n_count
            )
        if quality.get("quality") == default_quality:
            fixed_credits = pricing.get("credits") or 0

    size_options = (
        [f"{quality}-{ratio}" for quality in quality_names for ratio in aspect_ratios]
        if quality_names and aspect_ratios
        else (aspect_ratios or ["16:9"])
    )
    default_size = (
        f"{default_quality or '1K'}-{aspect_ratios[0]}"
        if aspect_ratios
        else "16:9"
    )
    params = {
        "image_size": {
            "enabled": True,
            "default": default_size,
            "options": size_options,
        },
        "resolution": {
            "enabled": True,
            "default": default_quality or "1K",
            "options": quality_names or ["1K"],
        },
        "max_reference_images": {
            "enabled": True,
            "default": image_limit if image_limit is not None else 9,
        },
        "aistarslab": {
            "channel": channel,
            "model": model_name,
            "label": model_config.get("label") or model_name,
            "lineTitle": line_title or "",
            "qualities": quality_names,
            "pricing": model_config.get("pricing")
            or [item for item in qualities if isinstance(item, dict)],
            "aspectRatios": aspect_ratios,
            "inputImagesMax": image_limit,
            "n": n_range,
            "billingType": "fixed_total",
            "fixedCredits": fixed_credits,
        },
        "billing_mode": "fixed",
        "per_second_cost": fixed_credits,
        "fixed_by_quality": fixed_by_quality,
    }
    return json.dumps(params, ensure_ascii=False)


def apply_image_billing_multiplier(
    params_value: str, multiplier: float
) -> tuple[str, str, Any, Any]:
    billing_type, fixed_credits, default_quality = "fixed_total", 0, "1K"
    try:
        params = json.loads(params_value)
        aistarslab = params.get("aistarslab") or {}
        billing_type = aistarslab.get("billingType") or "fixed_total"
        fixed_credits = aistarslab.get("fixedCredits") or 0
        default_quality = (params.get("resolution") or {}).get("default", "1K")
        params["billing_mode"] = "fixed"
        params["per_second_cost"] = int(round(fixed_credits * multiplier))
        for quality, value in (params.get("fixed_by_quality") or {}).items():
            params["fixed_by_quality"][quality] = int(round(value * multiplier))
        params_value = json.dumps(params, ensure_ascii=False)
    except Exception:
        pass
    return params_value, billing_type, fixed_credits, default_quality


def normalize_video_config(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for config in data.get("videoConfig") or []:
        item = {
            "channel": config.get("channel"),
            "title": config.get("title"),
            "description": config.get("description"),
            "defaultOption": bool(config.get("defaultOption")),
            "models": [],
        }
        for model in config.get("models") or []:
            qualities = model.get("qualities") or []
            item["models"].append(
                {
                    "model": model.get("model"),
                    "label": model.get("label"),
                    "qualities": [quality.get("quality") for quality in qualities],
                    "pricing": qualities,
                    "modes": model.get("modes") or [],
                    "aspectRatios": model.get("aspectRatios") or [],
                    "duration": model.get("duration") or {},
                    "inputImagesMax": model.get("inputImagesMax"),
                    "inputVideosMax": model.get("inputVideosMax"),
                    "inputAudiosMax": model.get("inputAudiosMax"),
                }
            )
        result.append(item)
    return result


def normalize_image_config(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for config in data.get("imageConfig") or []:
        item = {
            "channel": config.get("channel"),
            "title": config.get("title"),
            "description": config.get("description"),
            "defaultOption": bool(config.get("defaultOption")),
            "models": [],
        }
        for model in config.get("models") or []:
            qualities = model.get("qualities") or []
            item["models"].append(
                {
                    "model": model.get("model"),
                    "label": model.get("label"),
                    "qualities": [quality.get("quality") for quality in qualities],
                    "pricing": qualities,
                    "aspectRatios": model.get("aspectRatios") or [],
                    "n": model.get("n") or {},
                    "inputImagesMax": model.get("inputImagesMax"),
                }
            )
        result.append(item)
    return result


def estimate_cost(
    model_config: dict[str, Any],
    quality: str,
    duration: int = 0,
    has_input_video: bool = False,
) -> int | None:
    entries = model_config.get("pricing")
    if not entries:
        entries = [
            item
            for item in (model_config.get("qualities") or [])
            if isinstance(item, dict)
        ]
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("quality") != quality:
            continue
        pricing = entry.get("pricing") or entry
        credits = pricing.get("credits") or 0
        if pricing.get("type") == "fixed_total":
            base = credits
        else:
            base = credits * int(duration or 0)
        if has_input_video and pricing.get("inputVideoMultiplier"):
            base = round(base * float(pricing.get("inputVideoMultiplier")))
        return base
    return None
