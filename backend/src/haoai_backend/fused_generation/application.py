"""Fused-image request validation and durable task admission."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from typing import Any

from haoai_backend.shared.identity import TrustedActor

from .domain import FusedFrameTarget, FusedPromptText, PreparedFusedGeneration
from .errors import FusedGenerationBadRequest, FusedGenerationConflict, FusedGenerationNotFound, FusedGenerationUnavailable
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
    has_target_proof = any((
        request.storyboard_asset_id is not None,
        request.expected_frame is not None,
        request.expected_content is not None,
    ))
    if has_target_proof:
        return _resolve_guarded_target_frame(uow, request)

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


def _resolve_guarded_target_frame(
    uow: FusedGenerationUnitOfWork,
    request: FusedAssetGenerateRequest,
) -> FusedFrameTarget:
    if not request.chapter_id:
        raise FusedGenerationBadRequest("验证分镜目标时必须提供 chapter_id")
    chapter = uow.load_chapter(request.chapter_id)
    if chapter is None:
        raise FusedGenerationNotFound("章节不存在")
    if chapter.get("series_id") != request.series_id:
        raise FusedGenerationBadRequest("章节不属于当前剧集")
    frames = _parse_frames(chapter.get("content"))

    if request.storyboard_asset_id is not None:
        if request.expected_frame is None or request.expected_content is not None:
            raise FusedGenerationBadRequest("稳定分镜目标必须提供 expected_frame，且不能同时提供 expected_content")
        if request.frame_index is not None and (
            isinstance(request.frame_index, bool) or request.frame_index < 0
        ):
            raise FusedGenerationBadRequest("frame_index 无效")
        matches = [
            index for index, frame in enumerate(frames)
            if isinstance(frame, Mapping)
            and _frame_storyboard_id(frame) == request.storyboard_asset_id
        ]
        if len(matches) != 1:
            raise FusedGenerationConflict("分镜目标不存在或身份重复，请刷新后重新选择")
        index = matches[0]
        actual = frames[index]
        if not isinstance(actual, Mapping) or not _frame_snapshot_matches(actual, request.expected_frame):
            raise FusedGenerationConflict("分镜目标内容已变化，请刷新后重新选择")
        return FusedFrameTarget(
            chapter_id=request.chapter_id,
            frame_index=index,
            identity=_frame_identity(actual),
        )

    if (
        request.expected_frame is None
        or request.expected_content is None
        or request.frame_index is None
        or isinstance(request.frame_index, bool)
        or request.frame_index < 0
    ):
        raise FusedGenerationBadRequest("无稳定分镜ID时必须提供完整章节快照、目标分镜快照和有效 frame_index")
    if not _chapter_snapshot_matches(frames, request.expected_content):
        raise FusedGenerationConflict("章节分镜内容已变化，请刷新后重新选择")
    index = request.frame_index
    if index >= len(frames) or not isinstance(frames[index], Mapping):
        raise FusedGenerationConflict("目标分镜不存在，请刷新后重新选择")
    target = frames[index]
    if not _frame_snapshot_matches(target, request.expected_frame):
        raise FusedGenerationConflict("目标分镜内容已变化，请刷新后重新选择")
    if not isinstance(target, Mapping) or not _frame_has_no_storyboard_identity(target):
        raise FusedGenerationConflict("目标分镜身份结构无效，请刷新后重新选择")
    identity = _frame_identity(target)
    if sum(
        1 for frame in frames
        if isinstance(frame, Mapping) and _frame_identity(frame) == identity
    ) != 1:
        raise FusedGenerationConflict("章节中存在无法区分的相同分镜，不能安全指定目标")
    return FusedFrameTarget(chapter_id=request.chapter_id, frame_index=index, identity=identity)


def _frame_snapshot_matches(actual: Mapping[str, object], expected: Mapping[str, object]) -> bool:
    actual_content = {key: value for key, value in actual.items() if key != "fused"}
    expected_content = {key: value for key, value in expected.items() if key != "fused"}
    return _json_values_equal(actual_content, expected_content)


def _chapter_snapshot_matches(actual: list[object], expected: list[object]) -> bool:
    if len(actual) != len(expected):
        return False
    for actual_frame, expected_frame in zip(actual, expected, strict=True):
        if isinstance(actual_frame, Mapping) and isinstance(expected_frame, Mapping):
            if not _frame_snapshot_matches(actual_frame, expected_frame):
                return False
        elif not _json_values_equal(actual_frame, expected_frame):
            return False
    return True


def _json_values_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            return False
        if (isinstance(left, float) and not math.isfinite(left)) or (
            isinstance(right, float) and not math.isfinite(right)
        ):
            return False
        return left == right
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping) or left.keys() != right.keys():
            return False
        return all(_json_values_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) or isinstance(right, list):
        if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
            return False
        return all(_json_values_equal(a, b) for a, b in zip(left, right, strict=True))
    return type(left) is type(right) and left == right


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


def _frame_storyboard_id(frame: Mapping[str, object]) -> str | None:
    """Return the one canonical storyboard reference stored on a frame."""
    references = frame.get("storyboard")
    if (
        not isinstance(references, list)
        or len(references) != 1
        or not isinstance(references[0], str)
        or not references[0].strip()
    ):
        return None
    return references[0]


def _frame_has_no_storyboard_identity(frame: Mapping[str, object]) -> bool:
    if "storyboard" not in frame:
        return True
    references = frame["storyboard"]
    return isinstance(references, list) and all(
        reference is None or reference == "" for reference in references
    )
