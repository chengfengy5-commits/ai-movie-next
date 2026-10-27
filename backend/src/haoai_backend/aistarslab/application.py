"""AIStarsLab compatibility use cases with injected storage and transport ports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from haoai_backend.admin_system_models.ports import Cipher
from haoai_backend.admin_system_config.ports import UnitOfWorkFactory as SystemConfigUnitOfWorkFactory
from haoai_backend.model_configs.ports import ModelConfigsUnitOfWorkFactory

from . import domain
from .credentials import get_credit_multiplier, get_credentials
from .errors import (
    AistarslabBadRequest,
    AistarslabForbidden,
    AistarslabNotFound,
)
from .ports import (
    AistarslabConfigCache,
    AistarslabLegacyReaderFactory,
    AistarslabProvider,
    AistarslabSystemModelWriter,
    Clock,
    IdFactory,
)


class AistarslabApplication:
    def __init__(
        self,
        *,
        model_configs_uow_factory: ModelConfigsUnitOfWorkFactory | None,
        legacy_reader_factory: AistarslabLegacyReaderFactory,
        system_config_uow_factory: SystemConfigUnitOfWorkFactory | None,
        system_model_writer: AistarslabSystemModelWriter,
        cipher: Cipher,
        provider: AistarslabProvider,
        clock: Clock,
        id_factory: IdFactory,
        cache: AistarslabConfigCache,
    ) -> None:
        self._model_configs_uow_factory = model_configs_uow_factory
        self._legacy_reader_factory = legacy_reader_factory
        self._system_config_uow_factory = system_config_uow_factory
        self._system_model_writer = system_model_writer
        self._cipher = cipher
        self._provider = provider
        self._clock = clock
        self._id_factory = id_factory
        self._cache = cache

    async def _credentials(self, user_id: str) -> tuple[str | None, str | None]:
        return await run_in_threadpool(
            get_credentials,
            self._model_configs_uow_factory,
            self._legacy_reader_factory,
            self._cipher,
            user_id,
        )

    async def _multiplier(self) -> float:
        return await run_in_threadpool(
            get_credit_multiplier,
            self._system_config_uow_factory,
        )

    async def _cached_config(self, user_id: str) -> dict[str, Any]:
        try:
            api_key, base_url = await self._credentials(user_id)
            if not api_key:
                raise RuntimeError(
                    "请先在模型设置中配置 AIStarsLab 模型的 API Key"
                )
            now = self._clock()
            cached = self._cache.get_fresh(now)
            if cached is not None:
                return cached
            data = await self._provider.fetch_config(api_key, base_url)
            self._cache.store(data, now)
            return data
        except Exception as error:
            raise AistarslabBadRequest(str(error)) from error

    async def config(self, user_id: str) -> dict[str, Any]:
        data = await self._cached_config(user_id)
        return {
            "videoConfig": domain.normalize_video_config(data),
            "imageConfig": domain.normalize_image_config(data),
            "credits": None,
        }

    async def credits(self, user_id: str) -> dict[str, Any]:
        api_key, base_url = await self._credentials(user_id)
        if not api_key:
            raise AistarslabBadRequest(
                "请先在模型设置中配置 AIStarsLab 模型的 API Key"
            )
        try:
            credits = await self._provider.get_credits(api_key, base_url)
        except Exception as error:
            raise AistarslabBadRequest(str(error)) from error
        return {"credits": credits}

    async def add_default(self, actor: Any, request: Any) -> dict[str, Any]:
        if not actor.is_superuser:
            raise AistarslabForbidden("仅管理员可配置 AIStarsLab 兜底渠道")

        api_key, base_url = await self._credentials(actor.user_id)
        if request.api_key:
            api_key = request.api_key.strip()
        if not api_key:
            raise AistarslabBadRequest("请填写 AIStarsLab API Key")

        try:
            data = await self._provider.fetch_config(api_key, base_url)
        except Exception as error:
            raise AistarslabBadRequest(str(error)) from error

        channel, model_name, model_config = None, None, None
        line_title = ""
        for config in data.get("videoConfig") or []:
            if not config.get("defaultOption"):
                continue
            models = config.get("models") or []
            if models:
                channel = config.get("channel")
                model_name = models[0].get("model")
                model_config = models[0]
                line_title = config.get("title") or ""
                break
        if not channel or not model_name:
            raise AistarslabBadRequest(
                "AIStarsLab 配置中未找到默认视频线路"
            )

        params_value = domain.build_video_params_snapshot(
            channel, model_name, model_config, line_title
        )
        name = (
            f"AIS-{domain.short_line_title(line_title) or model_config.get('label') or model_name}"
        )
        multiplier = await self._multiplier()
        (
            params_value,
            billing_type,
            per_second,
            fixed,
            default_duration,
        ) = domain.apply_video_billing_multiplier(params_value, multiplier)
        if billing_type == "fixed_total":
            base_cost = int(round(fixed * multiplier))
        else:
            base_cost = int(
                round(per_second * multiplier * int(default_duration))
            )
        cost_description = (
            f"固定 {fixed} 积分/次"
            if billing_type == "fixed_total"
            else f"{per_second} 积分/秒 × {multiplier}倍"
        )
        description = (
            f"AIS 兜底渠道（{line_title or ''} / "
            f"{model_config.get('label') or model_name}）。"
            f"按{'条计费' if billing_type == 'fixed_total' else '秒计费'}："
            f"{cost_description}。默认时长 {default_duration}s，"
            f"基准每次约 {base_cost} 积分（实际按所选时长动态扣费）。"
        )

        record = await run_in_threadpool(
            self._system_model_writer.create_system_model,
            category="video",
            name=name,
            model=f"{channel}:{model_name}",
            base_url=base_url or domain.DEFAULT_BASE_URL,
            params=params_value,
            cost_per_call=base_cost,
            description=description,
            api_key=(request.api_key or api_key) or "",
            id_factory=self._id_factory,
        )
        self._cache.clear()
        return model_response(record, is_system=True)

    async def build_image_params(
        self, user_id: str, channel: str, model_name: str
    ) -> dict[str, Any]:
        data = await self._cached_config(user_id)
        for config in data.get("imageConfig") or []:
            if config.get("channel") != channel:
                continue
            for model_config in config.get("models") or []:
                if model_config.get("model") != model_name:
                    continue
                params = domain.build_image_params_snapshot(
                    channel, model_name, model_config, config.get("title") or ""
                )
                params, _, _, _ = domain.apply_image_billing_multiplier(
                    params, await self._multiplier()
                )
                return {
                    "params": params,
                    "channel": channel,
                    "model": model_name,
                    "label": model_config.get("label") or model_name,
                    "lineTitle": config.get("title") or "",
                }
        raise AistarslabNotFound("未找到对应的图片线路/模型配置")

    async def add_image_default(
        self, actor: Any, request: Any
    ) -> dict[str, Any]:
        if not actor.is_superuser:
            raise AistarslabForbidden("仅管理员可配置 AIStarsLab 图片渠道")

        api_key, base_url = await self._credentials(actor.user_id)
        if request.api_key:
            api_key = request.api_key.strip()
        if not api_key:
            raise AistarslabBadRequest("请填写 AIStarsLab API Key")

        try:
            data = await self._provider.fetch_config(api_key, base_url)
        except Exception as error:
            raise AistarslabBadRequest(str(error)) from error

        channel, model_name, model_config = None, None, None
        line_title = ""
        for config in data.get("imageConfig") or []:
            if not config.get("defaultOption"):
                continue
            models = config.get("models") or []
            if models:
                channel = config.get("channel")
                model_name = models[0].get("model")
                model_config = models[0]
                line_title = config.get("title") or ""
                break
        if not channel or not model_name:
            raise AistarslabBadRequest(
                "AIStarsLab 配置中未找到默认图片线路"
            )

        params_value = domain.build_image_params_snapshot(
            channel, model_name, model_config, line_title
        )
        name = (
            f"AIS-{domain.short_line_title(line_title) or model_config.get('label') or model_name}"
        )
        multiplier = await self._multiplier()
        (
            params_value,
            _,
            fixed_credits,
            default_quality,
        ) = domain.apply_image_billing_multiplier(params_value, multiplier)
        base_cost = int(round(fixed_credits * multiplier))
        description = (
            f"AIS 图片渠道（{line_title or ''} / "
            f"{model_config.get('label') or model_name}）。"
            f"按条计费：固定 {fixed_credits} 积分/次 × {multiplier}倍"
            f"（默认质量档 {default_quality or '-'}）。"
        )

        record = await run_in_threadpool(
            self._system_model_writer.create_system_model,
            category="image",
            name=name,
            model=f"{channel}:{model_name}",
            base_url=base_url or domain.DEFAULT_BASE_URL,
            params=params_value,
            cost_per_call=base_cost,
            description=description,
            api_key=(request.api_key or api_key) or "",
            id_factory=self._id_factory,
        )
        self._cache.clear()
        return model_response(record, is_system=True)

    async def estimate_cost(
        self,
        user_id: str,
        *,
        channel: str,
        model_name: str,
        quality: str,
        duration: int,
        has_input_video: bool,
    ) -> dict[str, Any]:
        data = await self._cached_config(user_id)
        for config in data.get("videoConfig") or []:
            if config.get("channel") != channel:
                continue
            for model_config in config.get("models") or []:
                if model_config.get("model") != model_name:
                    continue
                return {
                    "costCredits": domain.estimate_cost(
                        model_config,
                        quality,
                        duration,
                        has_input_video,
                    )
                }
        raise AistarslabNotFound("未找到对应的线路/模型配置")

    async def build_video_params(
        self, user_id: str, channel: str, model_name: str
    ) -> dict[str, Any]:
        data = await self._cached_config(user_id)
        for config in data.get("videoConfig") or []:
            if config.get("channel") != channel:
                continue
            for model_config in config.get("models") or []:
                if model_config.get("model") != model_name:
                    continue
                params = domain.build_video_params_snapshot(
                    channel, model_name, model_config, config.get("title") or ""
                )
                params, _, _, _, _ = domain.apply_video_billing_multiplier(
                    params, await self._multiplier()
                )
                return {
                    "params": params,
                    "channel": channel,
                    "model": model_name,
                    "label": model_config.get("label") or model_name,
                    "lineTitle": config.get("title") or "",
                }
        raise AistarslabNotFound("未找到对应的线路/模型配置")

    async def sync_all(self, actor: Any) -> dict[str, int]:
        if not actor.is_superuser:
            raise AistarslabForbidden("仅管理员可同步 AIStarsLab 渠道")

        api_key, base_url = await self._credentials(actor.user_id)
        if not api_key:
            raise AistarslabBadRequest(
                "请先在系统模型中配置 AIStarsLab API Key"
            )
        try:
            data = await self._provider.fetch_config(api_key, base_url)
        except Exception as error:
            raise AistarslabBadRequest(str(error)) from error

        video_config = data.get("videoConfig") or []
        if not video_config:
            raise AistarslabBadRequest("AIStarsLab 未返回视频线路")

        image_config = data.get("imageConfig") or []
        multiplier = await self._multiplier()
        result = await run_in_threadpool(
            self._system_model_writer.sync_system_models,
            video_config=video_config,
            image_config=image_config,
            api_key=api_key,
            base_url=base_url or domain.DEFAULT_BASE_URL,
            multiplier=multiplier,
            id_factory=self._id_factory,
        )
        self._cache.clear()
        return result


def model_response(record: Any, *, is_system: bool = False) -> dict[str, Any]:
    """Project the saved model exactly as the legacy add routes did."""
    created_at = getattr(record, "created_at", None)
    updated_at = getattr(record, "updated_at", None)
    return {
        "id": record.id,
        "user_id": getattr(record, "user_id", "system"),
        "category": record.category,
        "name": record.name,
        "provider": record.provider,
        "api_key": record.api_key,
        "model": record.model,
        "base_url": record.base_url,
        "sort_order": record.sort_order,
        "params": record.params,
        "is_system": is_system,
        "cost_per_call": getattr(record, "cost_per_call", 0) or 0,
        "description": getattr(record, "description", None),
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }
