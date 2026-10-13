"""Framework-free identity, records, and legacy public projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TypedDict


@dataclass(frozen=True, slots=True)
class AdminSystemConfigActor:
    """Identity facts copied from public authentication before its session closes."""

    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class SystemConfigRecord:
    """All columns in the fixed legacy system_configs table."""

    id: Any
    muse_token: Any
    site_title: Any
    site_tagline: Any
    storyboard_cost_multiplier: Any
    membership_benefits: Any
    payments_enabled: Any
    tutorial_url: Any
    purchase_qr_code_url: Any
    purchase_contact_info: Any
    oss_access_key_id: Any
    oss_access_key_secret: Any
    oss_bucket_name: Any
    oss_endpoint: Any
    fc_transfer_url: Any
    fc_transfer_url_cn: Any
    oss_haoai_match_domain: Any
    oss_haoai_replace_domain: Any
    team_created_limit: Any
    team_joined_limit: Any
    team_member_limit: Any
    invite_code_ttl_hours: Any
    chapter_lock_idle_minutes: Any
    aistarslab_credit_multiplier: Any
    zhangyuge_billing_pat: Any
    frontend_url: Any
    allowed_origins: Any
    upload_dir: Any
    worker_max_concurrency: Any


class SystemConfigResponse(TypedDict):
    """The twenty legacy fields exposed by the administrator API."""

    id: Any
    muse_token: Any
    site_title: Any
    site_tagline: Any
    storyboard_cost_multiplier: Any
    membership_benefits: Any
    payments_enabled: bool
    tutorial_url: Any
    purchase_qr_code_url: Any
    purchase_contact_info: Any
    team_created_limit: Any
    team_joined_limit: Any
    team_member_limit: Any
    invite_code_ttl_hours: Any
    chapter_lock_idle_minutes: Any
    aistarslab_credit_multiplier: Any
    frontend_url: Any
    allowed_origins: Any
    upload_dir: Any
    worker_max_concurrency: Any


_INTEGER_UPDATE_FIELDS = frozenset(
    {
        "storyboard_cost_multiplier",
        "team_created_limit",
        "team_joined_limit",
        "team_member_limit",
        "invite_code_ttl_hours",
        "chapter_lock_idle_minutes",
    }
)
_STRIP_UPDATE_FIELDS = frozenset(
    {"frontend_url", "allowed_origins", "upload_dir"}
)


def convert_legacy_update_value(field: str, value: Any) -> Any:
    """Keep each field's original coercion and exception behavior."""

    if field in _INTEGER_UPDATE_FIELDS:
        return int(value)
    if field == "payments_enabled":
        return 1 if value else 0
    if field == "aistarslab_credit_multiplier":
        try:
            return float(value)
        except (TypeError, ValueError):
            return 1.0
    if field in _STRIP_UPDATE_FIELDS:
        return (value or "").strip()
    if field == "worker_max_concurrency":
        try:
            return str(int(str(value or "").strip() or 0))
        except (TypeError, ValueError):
            return ""
    return value


def system_config_payload(config: SystemConfigRecord) -> SystemConfigResponse:
    """Return the legacy twenty-field response without OSS, FC, or PAT values."""

    return {
        "id": config.id,
        "muse_token": config.muse_token,
        "site_title": config.site_title,
        "site_tagline": config.site_tagline,
        "storyboard_cost_multiplier": config.storyboard_cost_multiplier,
        "membership_benefits": config.membership_benefits or "",
        "payments_enabled": bool(config.payments_enabled),
        "tutorial_url": config.tutorial_url or "",
        "purchase_qr_code_url": config.purchase_qr_code_url or "",
        "purchase_contact_info": config.purchase_contact_info or "",
        "team_created_limit": getattr(config, "team_created_limit", None) or 3,
        "team_joined_limit": getattr(config, "team_joined_limit", None) or 3,
        "team_member_limit": getattr(config, "team_member_limit", None) or 20,
        "invite_code_ttl_hours": getattr(config, "invite_code_ttl_hours", None) or 24,
        "chapter_lock_idle_minutes": getattr(config, "chapter_lock_idle_minutes", None) or 15,
        "aistarslab_credit_multiplier": getattr(
            config, "aistarslab_credit_multiplier", None
        )
        or 1.0,
        "frontend_url": getattr(config, "frontend_url", "") or "",
        "allowed_origins": getattr(config, "allowed_origins", "") or "",
        "upload_dir": getattr(config, "upload_dir", "") or "",
        "worker_max_concurrency": getattr(config, "worker_max_concurrency", "") or "",
    }
