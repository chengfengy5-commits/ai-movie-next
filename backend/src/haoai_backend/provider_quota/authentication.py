"""Administrator identity adapter backed by the shared authentication service."""

from __future__ import annotations

from typing import Any

from haoai_backend.authentication.errors import AuthenticationError

from .domain import ProviderQuotaActor


def authenticate_provider_quota_actor(
    service: Any,
    authorization: str,
) -> ProviderQuotaActor:
    with service.authenticated(authorization, require_membership=False) as principal:
        actor = ProviderQuotaActor(
            user_id=principal.user.id,
            is_superuser=bool(principal.user.is_superuser),
        )
        if not actor.is_superuser:
            raise AuthenticationError(403, "权限不足，仅管理员可执行此操作")
    return actor
