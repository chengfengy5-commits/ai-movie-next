"""Fused-image request validation and durable task admission."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from haoai_backend.shared.identity import TrustedActor

from .domain import FusedFrameTarget, FusedPromptText, PreparedFusedGeneration
from .errors import FusedGenerationBadRequest, FusedGenerationNotFound, FusedGenerationUnavailable
from .ports import FusedGenerationUnitOfWork, FusedGenerationUnitOfWorkFactory
from .schemas import FusedAssetGenerateRequest


def prepare_fused_generation(
    uow_factory: FusedGenerationUnitOfWorkFactory | None,
    actor: TrustedActor,
    request: FusedAssetGenerateRequest,
) -> PreparedFusedGeneration:
    """Validate a request and commit its task and optional assistant message."""
    if uow_factory is None:
        raise FusedGenerationUnavailable()
    uow = uow_factory()
    try:
        uow.require_series_access(actor, request.series_id)
        if request.member_type not in {"character", "prop"}:
            raise FusedGenerationBadRequest("member_type 只能是 character 或 prop")
        if not request.members:
            raise FusedGenerationBadRequest("请至少选择一个融合成员")

        members, reference_images, reference_names = _resolve_members(uow, request)
        if not members:
            raise FusedGenerationBadRequest("没有有效的融合成员")
        if not reference_images:
            raise FusedGenerationBadRequest("融合成员都没有图片，无法生成")

        # Reject a stale/foreign target before any fee transaction is committed.
        target_frame = _resolve_target_frame(uow, request)

        # Legacy ordering is deliberate: model resolution and its independent
        # system-model charge happen before prompt resolution and task staging.
        server_model = uow.resolve_and_charge_model(actor.user_id, request.model_id)
        mapping = _reference_mapping(request.member_type, reference_names)
        prompt = _resolve_prompt(uow, actor.user_id, request, mapping)
        task_id = uow.new_id()
        message_id = uow.new_id() if request.chapter_id else None
        label = "角色" if request.member_type == "character" else "道具"
        fused_name = request.name or f"{label}融合 {len(members)}合1"
        prepared = PreparedFusedGeneration(
            task_id=task_id,
            message_id=message_id,
            user_id=actor.user_id,
            series_id=request.series_id,
            chapter_id=request.chapter_id,
            member_type=request.member_type,
            members=tuple(members),
            name=fused_name,
            size=request.size,
            prompt=prompt,
            reference_images=tuple(reference_images),
            model_id=server_model.id,
            model_name=server_model.name,
            credit_cost=server_model.cost_per_call if server_model.is_system else 0,
            server_model=server_model,
            target_frame=target_frame,
        )
        uow.stage_request(prepared, actor.user_id)
        uow.commit()
        return prepared
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _resolve_members(
    uow: FusedGenerationUnitOfWork,
    request: FusedAssetGenerateRequest,
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    members: list[dict[str, str]] = []
    reference_images: list[str] = []
    reference_names: list[str] = []

    for member in request.members:
        if member.kind == "asset":
            if not member.id:
                continue
            asset = uow.load_member_asset(member_type=request.member_type, series_id=request.series_id, asset_id=member.id)
            if asset is None:
                continue
            name = _asset_name(asset.get("name"))
            members.append({"kind": "asset", "id": str(asset["id"]), "name": name})
            image_url = asset.get("image_url")
            if isinstance(image_url, str) and image_url:
                reference_images.append(image_url)
                reference_names.append(name)
        elif member.kind == "image" and isinstance(member.url, str) and member.url:
            name = member.name or "自定义图片"
            reference_images.append(member.url)
            reference_names.append(name)
            members.append({"kind": "image", "url": member.url, "name": name})
    return members, reference_images, reference_names


def _asset_name(value: object) -> str:
    return value if isinstance(value, str) and value else "未命名"


def _reference_mapping(member_type: str, names: list[str]) -> str:
    type_label = "角色" if member_type == "character" else "道具"
    lines = [f"【{type_label}资产对照】"]
    lines.extend(f"图片{index} = {name}" for index, name in enumerate(names, start=1))
    return "\n".join(lines)


def _resolve_prompt(
    uow: FusedGenerationUnitOfWork,
    user_id: str,
    request: FusedAssetGenerateRequest,
    mapping: str,
) -> str:
    if request.prompt_system or request.prompt_user:
        parts = [
            text.strip()
            for text in (request.prompt_system, request.prompt_user)
            if isinstance(text, str) and text.strip()
        ]
        parts.append(mapping)
        return "\n".join(parts).strip()
    if request.prompt_id:
        selection: FusedPromptText | None = uow.resolve_image_prompt(user_id, request.prompt_id)
        if selection is None:
            raise FusedGenerationBadRequest(
                "所选提示词无法引用（不存在或已停用），请重新选择后再试"
            )
        parts = [
            part
            for part in (selection.system_prompt, selection.user_prompt, mapping)
            if part
        ]
        return "\n".join(parts).strip()
    return mapping


def _resolve_target_frame(
    uow: FusedGenerationUnitOfWork,
    request: FusedAssetGenerateRequest,
) -> FusedFrameTarget | None:
    if request.frame_index is not None and not request.chapter_id:
        raise FusedGenerationBadRequest("指定分镜时必须同时提供 chapter_id")
    if not request.chapter_id or request.frame_index is None:
        if request.chapter_id:
            chapter = uow.load_chapter(request.chapter_id)
            if chapter is None:
                raise FusedGenerationNotFound("章节不存在")
            if chapter.get("series_id") != request.series_id:
                raise FusedGenerationBadRequest("章节不属于当前剧集")
        return None

    if request.frame_index < 0:
        raise FusedGenerationBadRequest("frame_index 无效")
    chapter = uow.load_chapter(request.chapter_id)
    if chapter is None:
        raise FusedGenerationNotFound("章节不存在")
    if chapter.get("series_id") != request.series_id:
        raise FusedGenerationBadRequest("章节不属于当前剧集")
    frames = _parse_frames(chapter.get("content"))
    if request.frame_index >= len(frames) or not isinstance(frames[request.frame_index], dict):
        raise FusedGenerationBadRequest("目标分镜不存在")
    return FusedFrameTarget(
        chapter_id=request.chapter_id,
        frame_index=request.frame_index,
        identity=_frame_identity(frames[request.frame_index]),
    )


def _parse_frames(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed: Any = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _frame_identity(frame: Mapping[str, object]) -> str:
    stable_frame = {key: value for key, value in frame.items() if key != "fused"}
    return json.dumps(stable_frame, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
