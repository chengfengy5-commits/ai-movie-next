"""System-model writes through the existing administrator model unit-of-work port."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from haoai_backend.admin_system_models.domain import SystemModelRecord
from haoai_backend.admin_system_models.ports import (
    AdminSystemModelUnitOfWork,
    Cipher,
    UnitOfWorkFactory,
)

from .errors import AistarslabUnavailable
from .ports import (
    AistarslabLegacyReaderFactory,
    AistarslabSystemModelWriter,
    IdFactory,
)

_LIST_LIMIT = 2_147_483_647


@contextmanager
def _open(
    factory: UnitOfWorkFactory | None,
) -> Iterator[AdminSystemModelUnitOfWork]:
    if factory is None:
        raise AistarslabUnavailable()
    unit_of_work = factory()
    try:
        yield unit_of_work
    except BaseException:
        try:
            unit_of_work.rollback()
        except BaseException:
            pass
        try:
            unit_of_work.close()
        except BaseException:
            pass
        raise
    else:
        unit_of_work.close()


def create_system_model(
    factory: UnitOfWorkFactory | None,
    cipher: Cipher,
    *,
    category: str,
    name: str,
    model: str,
    base_url: str,
    params: str,
    cost_per_call: int,
    description: str,
    api_key: str,
    id_factory: Callable[[], str] = lambda: str(uuid4()),
) -> SystemModelRecord:
    with _open(factory) as unit_of_work:
        rows, _ = unit_of_work.list_models(
            category=category,
            provider=None,
            is_active=None,
            skip=0,
            limit=_LIST_LIMIT,
        )
        maximum = max((row.sort_order for row in rows), default=None) or 0
        row = unit_of_work.add_model(
            id_factory(),
            {
                "category": category,
                "name": name,
                "provider": "aistarslab",
                "api_key": cipher.encrypt(api_key),
                "model": model,
                "base_url": base_url,
                "cost_per_call": cost_per_call,
                "description": description,
                "is_active": True,
                "sort_order": maximum + 1,
                "params": params,
            },
        )
        unit_of_work.commit()
        return unit_of_work.refresh_model(row)


def sync_system_models(
    factory: UnitOfWorkFactory | None,
    legacy_reader_factory: AistarslabLegacyReaderFactory,
    cipher: Cipher,
    *,
    video_config: list[dict[str, Any]],
    image_config: list[dict[str, Any]],
    api_key: str,
    base_url: str,
    multiplier: float,
    id_factory: Callable[[], str] = lambda: str(uuid4()),
) -> dict[str, int]:
    from .domain import (
        apply_image_billing_multiplier,
        apply_video_billing_multiplier,
        build_image_params_snapshot,
        build_video_params_snapshot,
        short_line_title,
    )

    with _open(factory) as unit_of_work:
        existing_rows = legacy_reader_factory().list_existing_system_models(
            "aistarslab"
        )
        existing = {row.model: row for row in existing_rows}
        video_rows, _ = unit_of_work.list_models(
            category="video",
            provider=None,
            is_active=None,
            skip=0,
            limit=_LIST_LIMIT,
        )
        max_order = max((row.sort_order for row in video_rows), default=None) or 0
        created = 0
        updated = 0

        def apply_config(
            config: dict[str, Any],
            model_config: dict[str, Any],
            *,
            category: str,
        ) -> None:
            nonlocal max_order, created, updated

            channel = config.get("channel")
            title = config.get("title") or ""
            model_name = model_config.get("model")
            label = model_config.get("label") or model_name
            key = f"{channel}:{model_name}"

            if category == "video":
                params = build_video_params_snapshot(
                    channel, model_name, model_config, title
                )
                params, billing_type, per_second, fixed, default_duration = (
                    apply_video_billing_multiplier(params, multiplier)
                )
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
                    f"AIS 渠道（{title or ''} / {label}）。"
                    f"按{'条计费' if billing_type == 'fixed_total' else '秒计费'}："
                    f"{cost_description}。默认时长 {default_duration}s，"
                    f"基准每次约 {base_cost} 积分（实际按所选时长动态扣费）。"
                )
            else:
                params = build_image_params_snapshot(
                    channel, model_name, model_config, title
                )
                params, _, fixed_credits, _ = (
                    apply_image_billing_multiplier(params, multiplier)
                )
                base_cost = int(round(fixed_credits * multiplier))
                default_size = "16:9"
                try:
                    params_object = json.loads(params)
                    default_size = (
                        (params_object.get("image_size") or {}).get(
                            "default", "16:9"
                        )
                    )
                except Exception:
                    pass
                description = (
                    f"AIS 图片渠道（{title or ''} / {label}）。"
                    f"按条计费：固定 {fixed_credits} 积分/次 × {multiplier}倍；"
                    f"默认尺寸 {default_size}。"
                )

            name = f"AIS-{short_line_title(title) or label or key}"
            current = existing.get(key)
            if current is not None:
                row = unit_of_work.load_model_for_update(current.id)
                changes = {
                    "name": name,
                    "params": params,
                    "cost_per_call": base_cost,
                    "description": description,
                    "base_url": base_url,
                }
                if category == "image":
                    changes["category"] = "image"
                unit_of_work.set_model_values(row, changes)
                updated += 1
                return

            max_order += 1
            unit_of_work.add_model(
                id_factory(),
                {
                    "category": category,
                    "name": name,
                    "provider": "aistarslab",
                    "api_key": cipher.encrypt(api_key or ""),
                    "model": key,
                    "base_url": base_url,
                    "params": params,
                    "cost_per_call": base_cost,
                    "description": description,
                    "sort_order": max_order,
                    "is_active": False,
                },
            )
            created += 1

        for channel_config in video_config:
            for model_config in channel_config.get("models") or []:
                apply_config(channel_config, model_config, category="video")

        for channel_config in image_config:
            for model_config in channel_config.get("models") or []:
                apply_config(channel_config, model_config, category="image")

        unit_of_work.commit()
        return {
            "created": created,
            "updated": updated,
            "total": created + updated,
        }


class AistarslabSystemModelWriterAdapter(AistarslabSystemModelWriter):
    """Bind AIStarsLab model writes to the existing UoW and cipher adapters."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory | None,
        legacy_reader_factory: AistarslabLegacyReaderFactory,
        cipher: Cipher,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._legacy_reader_factory = legacy_reader_factory
        self._cipher = cipher

    def create_system_model(
        self,
        *,
        category: str,
        name: str,
        model: str,
        base_url: str,
        params: str,
        cost_per_call: int,
        description: str,
        api_key: str,
        id_factory: IdFactory,
    ) -> SystemModelRecord:
        return create_system_model(
            self._unit_of_work_factory,
            self._cipher,
            category=category,
            name=name,
            model=model,
            base_url=base_url,
            params=params,
            cost_per_call=cost_per_call,
            description=description,
            api_key=api_key,
            id_factory=id_factory,
        )

    def sync_system_models(
        self,
        *,
        video_config: list[dict[str, Any]],
        image_config: list[dict[str, Any]],
        api_key: str,
        base_url: str,
        multiplier: float,
        id_factory: IdFactory,
    ) -> dict[str, int]:
        return sync_system_models(
            self._unit_of_work_factory,
            self._legacy_reader_factory,
            self._cipher,
            video_config=video_config,
            image_config=image_config,
            api_key=api_key,
            base_url=base_url,
            multiplier=multiplier,
            id_factory=id_factory,
        )
