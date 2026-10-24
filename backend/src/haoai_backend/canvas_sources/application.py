"""Use cases for guarded Canvas source reads and writes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from haoai_backend.asset_data.domain import AssetKind
from haoai_backend.asset_data.naming import dump_aliases, make_key
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    SourceRows,
    decode_frames,
    encode_frames,
    referenced_storyboard_ids,
    resolve_frame_target,
    row_fingerprint,
)
from .errors import CanvasSourceConflict, CanvasSourceUnavailable
from .ports import CanvasSourceUnitOfWork, CanvasSourceUnitOfWorkFactory
from .schemas import (
    AssetCreateCommand,
    AssetUpdateCommand,
    CanvasSourceRequest,
    CanvasSourceResponse,
    CreationReceipt,
    CreationUndoCommand,
    FrameDeleteCommand,
    FrameInsertCommand,
    FrameTarget,
    FrameUpdateCommand,
    MediaAppendCommand,
    MediaUpdateCommand,
    SourceSnapshot,
)


_RECEIPT_PURPOSE = "haoai.canvas-source.creation.v1"
_RECEIPT_KEY_CONTEXT = b"haoai:canvas-source:creation-receipt:v1"


def get_source_snapshot(
    uow_factory: CanvasSourceUnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
) -> SourceSnapshot:
    if uow_factory is None:
        raise CanvasSourceUnavailable("画布来源数据端口未接线")
    uow = uow_factory()
    try:
        rows = uow.load_source(chapter_id, actor)
        return uow.snapshot(rows, actor)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def execute_source_command(
    uow_factory: CanvasSourceUnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    request: CanvasSourceRequest,
    *,
    receipt_secret: str | None,
) -> CanvasSourceResponse:
    if uow_factory is None:
        raise CanvasSourceUnavailable("画布来源数据端口未接线")
    if not receipt_secret:
        raise CanvasSourceUnavailable("画布来源签名能力未配置")

    uow = uow_factory()
    try:
        rows = uow.load_source(
            chapter_id,
            actor,
            expected_series_id=request.expected_series_id,
            write=True,
        )
        uow.require_write_lock(rows, actor)
        uow.compare_version(rows, request.expected_version)
        source_frames = decode_frames(rows.chapter["content"], allow_null=True)
        referenced_storyboard_ids(
            source_frames,
            rows.storyboard_assets,
            chapter_id=str(rows.chapter["id"]),
            series_id=str(rows.chapter["series_id"]),
        )

        command = request.command
        receipt: CreationReceipt | None = None
        if isinstance(command, FrameUpdateCommand):
            _update_frame_text(uow, rows, command)
        elif isinstance(command, FrameInsertCommand):
            receipt = _insert_frame(uow, rows, actor, command, request, receipt_secret)
        elif isinstance(command, FrameDeleteCommand):
            _delete_frame(uow, rows, command)
        elif isinstance(command, AssetCreateCommand):
            receipt = _create_asset(uow, rows, actor, command, request, receipt_secret)
        elif isinstance(command, AssetUpdateCommand):
            _update_asset(uow, rows, command)
        elif isinstance(command, MediaAppendCommand):
            receipt = _append_video(uow, rows, actor, command, request, receipt_secret)
        elif isinstance(command, MediaUpdateCommand):
            _update_media(uow, rows, command)
        elif isinstance(command, CreationUndoCommand):
            _undo_creation(uow, rows, actor, command.receipt, receipt_secret)
        else:  # pragma: no cover - the request union is exhaustive
            raise CanvasSourceUnavailable("不支持的画布来源命令")

        uow.refresh_write_lock(chapter_id, actor)
        updated_rows = uow.load_source(
            chapter_id,
            actor,
            expected_series_id=str(rows.chapter["series_id"]),
        )
        snapshot = uow.snapshot(updated_rows, actor)
        response = CanvasSourceResponse(
            capability="canvas-source-v1",
            request_id=request.request_id,
            command_type=command.type,
            snapshot=snapshot,
            creation_receipt=receipt,
        )
        uow.commit()
        return response
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _update_frame_text(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    command: FrameUpdateCommand,
) -> None:
    frames = decode_frames(rows.chapter["content"])
    index, frame, asset = resolve_frame_target(
        frames,
        rows.storyboard_assets,
        command.target,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    updated_frame = dict(frame)
    updated_frame["text"] = command.text
    frames[index] = updated_frame
    content = rows.chapter["content"] if updated_frame == frame else encode_frames(frames)
    uow.update_chapter_content(
        rows,
        content,
        updated_at=uow.now() if content != rows.chapter["content"] else None,
    )
    if asset is not None:
        uow.update_storyboard_description(asset, index=index, frame=updated_frame)


def _insert_frame(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    actor: TrustedActor,
    command: FrameInsertCommand,
    request: CanvasSourceRequest,
    receipt_secret: str,
) -> CreationReceipt:
    old_frames = decode_frames(rows.chapter["content"], allow_null=True)
    if command.after is None:
        insertion_index = 0
    else:
        after_index, _frame, _asset = resolve_frame_target(
            old_frames,
            rows.storyboard_assets,
            command.after,
            chapter_id=str(rows.chapter["id"]),
            series_id=str(rows.chapter["series_id"]),
        )
        insertion_index = after_index + 1

    frame = dict(command.frame)
    _validate_frame_asset_references(frame, rows)
    new_asset_id = uow.new_id()
    frame["storyboard"] = [new_asset_id]
    new_frames = [*old_frames[:insertion_index], frame, *old_frames[insertion_index:]]
    new_content = encode_frames(new_frames)

    linked = referenced_storyboard_ids(
        old_frames,
        rows.storyboard_assets,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    shifted_ids = [asset_id for index, asset_id in linked.items() if index >= insertion_index]
    preserved_state_ids = _unlinked_asset_ids(rows, linked.values())
    original_state_ids = [
        str(state["id"])
        for state in uow.load_creation_side_effects(
            str(rows.chapter["id"]), lock=True
        )["media_states"]
    ]

    uow.update_chapter_content(rows, new_content, updated_at=uow.now())
    uow.shift_storyboard_indexes(
        str(rows.chapter["id"]), asset_ids=shifted_ids, delta=1
    )
    description = frame.get("text") or frame.get("original_text") or ""
    new_asset = uow.create_storyboard_asset(
        {
            "id": new_asset_id,
            "series_id": rows.chapter["series_id"],
            "chapter_id": rows.chapter["id"],
            "frame_index": insertion_index,
            "name": f"分镜 {insertion_index + 1}",
            "description": description,
            "image_url": None,
        }
    )
    uow.shift_chat_indexes(
        str(rows.chapter["id"]), start_index=insertion_index, delta=1
    )
    uow.reconcile_media(
        str(rows.chapter["id"]),
        content=new_content,
        new_assets=(new_asset,),
        preserve_unreferenced_state_ids=preserved_state_ids,
    )

    post_rows = uow.load_source(str(rows.chapter["id"]), actor)
    target = {"id": new_asset_id, "storyboard_id": new_asset_id, "index": insertion_index}
    claims = _common_receipt_claims(actor, rows, request, "frame", target)
    claims.update(
        {
            "post_version": post_rows.version,
            "created_asset_fingerprint": row_fingerprint(new_asset),
            "side_effects_fingerprint": uow.fingerprint_side_effects(
                str(rows.chapter["id"])
            ),
            "original_media_state_ids": original_state_ids,
            "before_content_was_null": rows.chapter["content"] is None,
        }
    )
    return CreationReceipt(
        token=_sign_receipt(receipt_secret, claims),
        kind="frame",
        target=target,
    )


def _delete_frame(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    command: FrameDeleteCommand,
) -> None:
    frames = decode_frames(rows.chapter["content"])
    if len(frames) <= 1:
        raise CanvasSourceConflict("章节至少需要保留一个分镜")
    index, _frame, target_asset = resolve_frame_target(
        frames,
        rows.storyboard_assets,
        command.target,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    new_frames = [*frames[:index], *frames[index + 1 :]]
    new_content = encode_frames(new_frames)
    linked = referenced_storyboard_ids(
        frames,
        rows.storyboard_assets,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    shifted_ids = [asset_id for frame_index, asset_id in linked.items() if frame_index > index]
    preserved_state_ids = _unlinked_asset_ids(rows, linked.values())

    uow.update_chapter_content(rows, new_content, updated_at=uow.now())
    if target_asset is not None:
        uow.delete_storyboard_asset(target_asset)
    uow.shift_storyboard_indexes(
        str(rows.chapter["id"]), asset_ids=shifted_ids, delta=-1
    )
    uow.delete_chat_frame(str(rows.chapter["id"]), index)
    uow.shift_chat_indexes(str(rows.chapter["id"]), start_index=index, delta=-1)
    uow.reconcile_media(
        str(rows.chapter["id"]),
        content=new_content,
        deleted_asset_ids={str(target_asset["id"])} if target_asset is not None else set(),
        preserve_unreferenced_state_ids=preserved_state_ids,
    )


def _create_asset(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    actor: TrustedActor,
    command: AssetCreateCommand,
    request: CanvasSourceRequest,
    receipt_secret: str,
) -> CreationReceipt:
    uow.update_chapter_content(rows, rows.chapter["content"])
    values = command.values.model_dump()
    values["series_id"] = rows.chapter["series_id"]
    asset = uow.create_asset(command.kind, values)
    target = {"id": str(asset["id"])}
    claims = _common_receipt_claims(actor, rows, request, command.kind, target)
    claims["created_asset_fingerprint"] = row_fingerprint(asset)
    return CreationReceipt(
        token=_sign_receipt(receipt_secret, claims),
        kind=command.kind,
        target=target,
    )


def _update_asset(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    command: AssetUpdateCommand,
) -> None:
    collection = _asset_collection(rows, command.kind)
    current = next((asset for asset in collection if asset["id"] == command.id), None)
    if current is None:
        raise CanvasSourceConflict("素材不存在或不属于当前剧集")

    provided = command.values.model_dump(exclude_unset=True)
    requested_values: dict[str, Any] = {}
    for field, value in provided.items():
        if field == "audio_url":
            requested_values[field] = value
        elif value is not None:
            requested_values[field] = dump_aliases(value) if field == "aliases" else value

    values = {
        field: value
        for field, value in requested_values.items()
        if value != current.get(field)
    }
    if requested_values:
        name_field = "title" if command.kind == "scene" else "name"
        desired_name = requested_values.get(name_field, current.get(name_field))
        canonical_key = make_key(command.kind, desired_name)
        if canonical_key != current.get("canonical_key"):
            values["canonical_key"] = canonical_key

        desired_aliases = requested_values.get("aliases", current.get("aliases"))
        if not desired_aliases:
            empty_aliases = dump_aliases([])
            if empty_aliases != current.get("aliases"):
                values["aliases"] = empty_aliases

    uow.update_chapter_content(rows, rows.chapter["content"])
    updated = uow.update_asset(
        command.kind,
        command.id,
        values,
        assigned_fields=frozenset(values),
    )
    if updated is None:
        raise CanvasSourceConflict("素材在更新期间已变化")


def _append_video(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    actor: TrustedActor,
    command: MediaAppendCommand,
    request: CanvasSourceRequest,
    receipt_secret: str,
) -> CreationReceipt:
    frames = decode_frames(rows.chapter["content"])
    old_frames = [dict(frame) for frame in frames]
    index, frame, _asset = resolve_frame_target(
        frames,
        rows.storyboard_assets,
        command.target,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    videos, had_videos = _video_list(frame)
    slot = len(videos)
    frame["videos"] = [*videos, command.value]
    frames[index] = frame
    content = encode_frames(frames)
    linked = referenced_storyboard_ids(
        old_frames,
        rows.storyboard_assets,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    preserve_ids = _unlinked_asset_ids(rows, linked.values())
    original_state_ids = [
        str(state["id"])
        for state in uow.load_creation_side_effects(
            str(rows.chapter["id"]), lock=True
        )["media_states"]
    ]
    uow.update_chapter_content(rows, content, updated_at=uow.now())
    uow.reconcile_media(
        str(rows.chapter["id"]),
        content=content,
        preserve_unreferenced_state_ids=preserve_ids,
    )

    post_rows = uow.load_source(str(rows.chapter["id"]), actor)
    target = {
        "storyboard_id": command.target.storyboard_id,
        "index": index,
        "slot": slot,
        "value": command.value,
    }
    claims = _common_receipt_claims(actor, rows, request, "video", target)
    claims.update(
        {
            "post_version": post_rows.version,
            "side_effects_fingerprint": uow.fingerprint_side_effects(
                str(rows.chapter["id"])
            ),
            "original_media_state_ids": original_state_ids,
            "videos_was_present": had_videos,
        }
    )
    return CreationReceipt(
        token=_sign_receipt(receipt_secret, claims),
        kind="video",
        target=target,
    )


def _update_media(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    command: MediaUpdateCommand,
) -> None:
    frames = decode_frames(rows.chapter["content"])
    index, frame, _asset = resolve_frame_target(
        frames,
        rows.storyboard_assets,
        command.target,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    if command.field == "preview":
        frame["preview"] = command.value
    else:
        videos, _had_videos = _video_list(frame)
        if command.slot is None or command.slot >= len(videos):
            raise CanvasSourceConflict("视频槽位已变化")
        videos[command.slot] = command.value
        frame["videos"] = videos
    frames[index] = frame
    content = encode_frames(frames)
    linked = referenced_storyboard_ids(
        frames,
        rows.storyboard_assets,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    uow.update_chapter_content(rows, content, updated_at=uow.now())
    uow.reconcile_media(
        str(rows.chapter["id"]),
        content=content,
        preserve_unreferenced_state_ids=_unlinked_asset_ids(rows, linked.values()),
    )


def _undo_creation(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    actor: TrustedActor,
    token: str,
    receipt_secret: str,
) -> None:
    claims = _verify_receipt(receipt_secret, token)
    if (
        claims.get("actor_id") != actor.user_id
        or claims.get("series_id") != rows.chapter["series_id"]
        or claims.get("chapter_id") != rows.chapter["id"]
    ):
        raise CanvasSourceConflict("创建回执不属于当前用户或章节")
    kind = claims.get("kind")
    target = claims.get("target")
    if not isinstance(target, dict):
        raise CanvasSourceConflict("创建回执内容无效")

    if kind in {"character", "scene", "prop"}:
        _undo_asset_creation(uow, rows, str(kind), target, claims)
        return
    if kind == "frame":
        _undo_frame_creation(uow, rows, target, claims)
        return
    if kind == "video":
        _undo_video_append(uow, rows, target, claims)
        return
    raise CanvasSourceConflict("创建回执操作类型无效")


def _undo_asset_creation(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    kind: AssetKind,
    target: Mapping[str, Any],
    claims: Mapping[str, Any],
) -> None:
    asset_id = target.get("id")
    if not isinstance(asset_id, str) or not asset_id:
        raise CanvasSourceConflict("创建回执素材身份无效")
    current = next(
        (asset for asset in _asset_collection(rows, kind) if asset["id"] == asset_id),
        None,
    )
    if current is None:
        raise CanvasSourceConflict("待撤销素材已变化")
    claims_fingerprint = claims.get("created_asset_fingerprint")
    if not isinstance(claims_fingerprint, str) or row_fingerprint(current) != claims_fingerprint:
        raise CanvasSourceConflict("待撤销素材已被修改")

    for chapter in uow.load_series_chapters(str(rows.chapter["series_id"])):
        chapter_frames = decode_frames(chapter["content"], allow_null=True)
        for frame in chapter_frames:
            if kind not in frame:
                continue
            references = frame[kind]
            if not isinstance(references, list):
                raise CanvasSourceConflict("无法证明素材未被章节引用")
            if asset_id in references:
                raise CanvasSourceConflict("素材已被章节引用，不能撤销创建")
    if uow.has_chat_asset_reference(kind, asset_id):
        raise CanvasSourceConflict("素材已被对话引用，不能撤销创建")

    uow.update_chapter_content(rows, rows.chapter["content"])
    uow.delete_created_asset(kind, asset_id, series_id=str(rows.chapter["series_id"]))


def _undo_frame_creation(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    target: Mapping[str, Any],
    claims: Mapping[str, Any],
) -> None:
    _require_post_creation_state(uow, rows, claims)
    storyboard_id = target.get("storyboard_id")
    index = target.get("index")
    if not isinstance(storyboard_id, str) or not isinstance(index, int) or isinstance(index, bool):
        raise CanvasSourceConflict("分镜创建回执身份无效")
    frames = decode_frames(rows.chapter["content"])
    frame_target = FrameTarget(index=index, storyboard_id=storyboard_id)
    resolved_index, _frame, asset = resolve_frame_target(
        frames,
        rows.storyboard_assets,
        frame_target,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    if asset is None or row_fingerprint(asset) != claims.get("created_asset_fingerprint"):
        raise CanvasSourceConflict("新分镜素材已变化")

    linked = referenced_storyboard_ids(
        frames,
        rows.storyboard_assets,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    shifted_ids = [asset_id for frame_index, asset_id in linked.items() if frame_index > resolved_index]
    preserved_state_ids = _unlinked_asset_ids(rows, linked.values())
    new_frames = [*frames[:resolved_index], *frames[resolved_index + 1 :]]
    if claims.get("before_content_was_null") is True and not new_frames:
        new_content = None
    else:
        new_content = encode_frames(new_frames)
    original_state_ids = claims.get("original_media_state_ids")
    if not _is_string_list(original_state_ids):
        raise CanvasSourceConflict("分镜创建回执媒体状态无效")

    uow.update_chapter_content(rows, new_content, updated_at=uow.now())
    uow.delete_created_storyboard_asset(
        str(rows.chapter["id"]), str(rows.chapter["series_id"]), storyboard_id
    )
    uow.shift_storyboard_indexes(
        str(rows.chapter["id"]), asset_ids=shifted_ids, delta=-1
    )
    uow.delete_chat_frame(str(rows.chapter["id"]), resolved_index)
    uow.shift_chat_indexes(str(rows.chapter["id"]), start_index=resolved_index, delta=-1)
    uow.reconcile_media(
        str(rows.chapter["id"]),
        content=new_content,
        deleted_asset_ids={storyboard_id},
        preserve_unreferenced_state_ids=preserved_state_ids,
    )
    uow.remove_media_states_not_in(str(rows.chapter["id"]), original_state_ids)


def _undo_video_append(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    target: Mapping[str, Any],
    claims: Mapping[str, Any],
) -> None:
    _require_post_creation_state(uow, rows, claims)
    storyboard_id = target.get("storyboard_id")
    index = target.get("index")
    slot = target.get("slot")
    value = target.get("value")
    if (
        storyboard_id is not None and not isinstance(storyboard_id, str)
    ) or not isinstance(index, int) or isinstance(index, bool) or not isinstance(slot, int) or isinstance(slot, bool) or not isinstance(value, str):
        raise CanvasSourceConflict("视频创建回执身份无效")
    frame_target = FrameTarget(index=index, storyboard_id=storyboard_id)
    frames = decode_frames(rows.chapter["content"])
    resolved_index, frame, _asset = resolve_frame_target(
        frames,
        rows.storyboard_assets,
        frame_target,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    videos, _had_videos = _video_list(frame)
    if slot >= len(videos) or videos[slot] != value:
        raise CanvasSourceConflict("视频创建槽位已变化")
    videos.pop(slot)
    if not videos and claims.get("videos_was_present") is False:
        frame.pop("videos", None)
    else:
        frame["videos"] = videos
    frames[resolved_index] = frame
    content = encode_frames(frames)
    linked = referenced_storyboard_ids(
        frames,
        rows.storyboard_assets,
        chapter_id=str(rows.chapter["id"]),
        series_id=str(rows.chapter["series_id"]),
    )
    original_state_ids = claims.get("original_media_state_ids")
    if not _is_string_list(original_state_ids):
        raise CanvasSourceConflict("视频创建回执媒体状态无效")

    uow.update_chapter_content(rows, content, updated_at=uow.now())
    uow.reconcile_media(
        str(rows.chapter["id"]),
        content=content,
        preserve_unreferenced_state_ids=_unlinked_asset_ids(rows, linked.values()),
    )
    uow.remove_media_states_not_in(str(rows.chapter["id"]), original_state_ids)


def _require_post_creation_state(
    uow: CanvasSourceUnitOfWork,
    rows: SourceRows,
    claims: Mapping[str, Any],
) -> None:
    if rows.version != claims.get("post_version"):
        raise CanvasSourceConflict("创建后来源已变化，不能撤销")
    if uow.fingerprint_side_effects(str(rows.chapter["id"])) != claims.get(
        "side_effects_fingerprint"
    ):
        raise CanvasSourceConflict("创建后对话、媒体或便签数据已变化，不能撤销")


def _validate_frame_asset_references(frame: Mapping[str, Any], rows: SourceRows) -> None:
    collections: dict[str, Sequence[Mapping[str, Any]]] = {
        "character": rows.characters,
        "scene": rows.scenes,
        "prop": rows.props,
    }
    for kind, assets in collections.items():
        key = kind
        if key not in frame:
            continue
        references = frame[key]
        if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
            raise CanvasSourceConflict(f"新分镜的{key}引用无效")
        available = {str(asset["id"]) for asset in assets}
        if any(reference not in available for reference in references):
            raise CanvasSourceConflict(f"新分镜引用了其他剧集或类型的{kind}素材")


def _asset_collection(rows: SourceRows, kind: AssetKind) -> Sequence[Mapping[str, Any]]:
    return {
        "character": rows.characters,
        "scene": rows.scenes,
        "prop": rows.props,
    }[kind]


def _video_list(frame: Mapping[str, Any]) -> tuple[list[str], bool]:
    present = "videos" in frame
    videos = frame.get("videos", [])
    if not isinstance(videos, list) or any(not isinstance(value, str) for value in videos):
        raise CanvasSourceConflict("目标分镜的视频列表无法安全编辑")
    return list(videos), present


def _unlinked_asset_ids(rows: SourceRows, linked_ids: Iterable[str]) -> list[str]:
    linked = set(linked_ids)
    return [
        str(asset["id"])
        for asset in rows.storyboard_assets
        if str(asset["id"]) not in linked
    ]


def _common_receipt_claims(
    actor: TrustedActor,
    rows: SourceRows,
    request: CanvasSourceRequest,
    kind: str,
    target: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "purpose": _RECEIPT_PURPOSE,
        "request_id": str(request.request_id),
        "actor_id": actor.user_id,
        "series_id": str(rows.chapter["series_id"]),
        "chapter_id": str(rows.chapter["id"]),
        "before_version": request.expected_version,
        "kind": kind,
        "target": dict(target),
    }


def _sign_receipt(secret: str, claims: Mapping[str, Any]) -> str:
    payload = json.dumps(
        claims,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    key = hmac.new(secret.encode("utf-8"), _RECEIPT_KEY_CONTEXT, hashlib.sha256).digest()
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(signature)}"


def _verify_receipt(secret: str, token: str) -> dict[str, Any]:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload = _b64url_decode(encoded_payload)
        signature = _b64url_decode(encoded_signature)
        key = hmac.new(secret.encode("utf-8"), _RECEIPT_KEY_CONTEXT, hashlib.sha256).digest()
        expected = hmac.new(key, payload, hashlib.sha256).digest()
        claims = json.loads(payload)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanvasSourceConflict("创建回执无效或已损坏") from exc
    if not hmac.compare_digest(signature, expected):
        raise CanvasSourceConflict("创建回执签名无效")
    if not isinstance(claims, dict) or claims.get("purpose") != _RECEIPT_PURPOSE:
        raise CanvasSourceConflict("创建回执用途无效")
    return claims


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )
    if _b64url(decoded) != value:
        raise ValueError("noncanonical base64url value")
    return decoded


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
