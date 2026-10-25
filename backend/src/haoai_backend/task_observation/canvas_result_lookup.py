"""Read a committed Canvas result-application receipt for its original target."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import GenerationActor, TargetSnapshot
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data.tables import chapters
from haoai_backend.shared.identity import TrustedActor

from .errors import (
    TaskObservationConflict,
    TaskObservationForbidden,
    TaskObservationNotFound,
    TaskObservationUnavailable,
)
from .generation import (
    ResultLookup,
    application_ref_for,
    current_target_application,
    expected_target_digest,
    find_application_record,
    load_owned_completed_result,
    target_matches_receipt,
    target_scope_for_snapshot,
)

SessionFactory = Callable[[], Session]

_TARGET_FIELDS = frozenset(
    {
        "kind",
        "chapter_id",
        "series_id",
        "stable_storyboard_asset_id",
        "asset_type",
        "asset_id",
        "compat_frame_index",
        "expected_text_digest",
        "media_identity",
        "content_digest",
        "notes_revision",
        "full_frame_digest",
        "full_chapter_digest",
        "expected_asset_image_url",
        "same_series_target_digest",
    }
)
_REQUIRED_TARGET_FIELDS = frozenset({"kind", "chapter_id", "series_id"})
_ID_FIELDS = frozenset(
    {"chapter_id", "series_id", "stable_storyboard_asset_id", "asset_id"}
)
_DIGEST_FIELDS = frozenset(
    {
        "expected_text_digest",
        "content_digest",
        "full_frame_digest",
        "full_chapter_digest",
        "same_series_target_digest",
    }
)
_STRING_FIELDS = frozenset(
    {
        "kind",
        "chapter_id",
        "series_id",
        "stable_storyboard_asset_id",
        "asset_type",
        "asset_id",
        "expected_text_digest",
        "media_identity",
        "content_digest",
        "full_frame_digest",
        "full_chapter_digest",
        "expected_asset_image_url",
        "same_series_target_digest",
    }
)
_INTEGER_FIELDS = frozenset({"compat_frame_index", "notes_revision"})
_CANVAS_OPERATIONS = frozenset({"image.single", "image.batch"})
_CANVAS_TARGET_KINDS = frozenset({"canvas_frame_preview", "canvas_asset_image"})
_ASSET_TYPES = frozenset({"character", "scene", "prop"})
_HEX = frozenset("0123456789abcdef")


def lookup_canvas_result_application(
    session_factory: SessionFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    result_ref: str,
    task_id: str,
) -> dict[str, object]:
    """Read and validate one historical application proof in a fresh Session."""

    if session_factory is None:
        raise TaskObservationUnavailable("画布结果查询服务尚未接线")

    session: Session | None = None
    cleanup_error: SQLAlchemyError | None = None
    try:
        candidate = session_factory()
        if not isinstance(candidate, Session):
            raise TaskObservationUnavailable("画布结果查询服务尚未接线")
        session = candidate
        with session.no_autoflush:
            return _lookup_in_session(
                session,
                actor,
                chapter_id=chapter_id,
                result_ref=result_ref,
                task_id=task_id,
            )
    except SQLAlchemyError as exc:
        raise TaskObservationUnavailable("数据库暂时不可用") from exc
    finally:
        if session is not None:
            try:
                session.rollback()
            except SQLAlchemyError as exc:
                cleanup_error = exc
            try:
                session.close()
            except SQLAlchemyError as exc:
                cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                raise TaskObservationUnavailable("数据库暂时不可用") from cleanup_error


def _lookup_in_session(
    session: Session,
    actor: TrustedActor,
    *,
    chapter_id: str,
    result_ref: str,
    task_id: str,
) -> dict[str, object]:
    owned_result: ResultLookup = load_owned_completed_result(
        session,
        GenerationActor(actor.user_id, actor),
        task_id,
        result_ref,
    )
    if owned_result == "not_found":
        raise TaskObservationNotFound("画布生成结果不存在或无权访问")
    if owned_result == "unready":
        raise TaskObservationConflict("画布生成结果尚未具备可查询的规范回执")
    if owned_result.operation not in _CANVAS_OPERATIONS:
        raise TaskObservationConflict("该生成任务不属于受支持的画布图片操作")

    request_data = session.execute(
        select(AITaskRow.request_data).where(
            AITaskRow.id == task_id,
            AITaskRow.user_id == actor.user_id,
        )
    ).scalar_one_or_none()
    if request_data is None:
        raise TaskObservationConflict("画布生成结果缺少原始目标快照")
    target = _target_snapshot_from_request_data(request_data)
    if target.kind not in _CANVAS_TARGET_KINDS:
        raise TaskObservationConflict("画布生成结果的原始目标类型无效")
    if target.chapter_id != chapter_id:
        raise TaskObservationNotFound("画布生成结果不存在或无权访问")
    if not target.series_id or not target_matches_receipt(session, owned_result, target):
        raise TaskObservationConflict("画布生成结果与原始目标快照不一致")

    current_chapter_series_id = session.execute(
        select(chapters.c.series_id).where(chapters.c.id == target.chapter_id)
    ).scalar_one_or_none()
    if current_chapter_series_id is None:
        raise TaskObservationNotFound("画布生成结果不存在或无权访问")
    if current_chapter_series_id != target.series_id:
        raise TaskObservationConflict("原始章节已不属于生成时的剧集")
    try:
        require_series_access(
            SqlAlchemySeriesAccessReader(session),
            ActorIdentity(actor.user_id),
            target.series_id,
        )
    except SeriesNotFound as exc:
        raise TaskObservationNotFound("剧集不存在") from exc
    except SeriesAccessDenied as exc:
        raise TaskObservationForbidden("无权访问该剧集") from exc

    try:
        target_scope = target_scope_for_snapshot(target)
        before_digest = expected_target_digest(target)
    except ValueError as exc:
        raise TaskObservationConflict("画布生成结果的原始目标摘要无效") from exc
    if not _is_sha256(before_digest):
        raise TaskObservationConflict("画布生成结果的原始目标摘要无效")

    outcome = current_target_application(session, owned_result, target, before_digest)
    if outcome is None:
        return {
            "task_id": task_id,
            "result_ref": result_ref,
            "status": "not_applied",
            "application": None,
        }
    if outcome.kind != "already_applied":
        raise TaskObservationConflict("画布生成结果的应用证明存在冲突")

    record = find_application_record(
        session,
        actor.user_id,
        task_id,
        result_ref,
        target_scope,
    )
    if (
        record is None
        or record.application_ref != application_ref_for(result_ref, target_scope)
        or not is_valid_canvas_identifier(record.application_ref)
        or record.result_ref != result_ref
        or record.owner_id != actor.user_id
        or record.task_id != task_id
        or record.unit_id != owned_result.unit_id
        or record.target_scope != target_scope
        or record.result_digest != owned_result.content_digest
        or record.delivery_status != "applied"
        or record.before_target_digest != before_digest
        or not _is_sha256(record.after_target_digest)
        or not isinstance(record.committed_at, datetime)
    ):
        raise TaskObservationConflict("画布生成结果的应用证明无效")

    committed_at = record.committed_at
    try:
        if committed_at.utcoffset() is None:
            committed_at = committed_at.replace(tzinfo=timezone.utc)
        else:
            committed_at = committed_at.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise TaskObservationConflict("画布生成结果的应用证明无效") from exc
    return {
        "task_id": task_id,
        "result_ref": result_ref,
        "status": "applied",
        "application": {
            "application_ref": record.application_ref,
            "target_revision": record.after_target_digest,
            "committed_at": committed_at.isoformat(),
        },
    }


def _target_snapshot_from_request_data(request_data: object) -> TargetSnapshot:
    if not isinstance(request_data, str):
        raise TaskObservationConflict("画布生成结果缺少原始目标快照")
    try:
        decoded = json.loads(
            request_data,
            object_pairs_hook=_unique_object_pairs,
            parse_constant=_reject_non_json_number,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskObservationConflict("画布生成结果的原始目标快照无效") from exc
    if type(decoded) is not dict or not _contains_only_finite_numbers(decoded):
        raise TaskObservationConflict("画布生成结果的原始目标快照无效")
    raw_target = decoded.get("target_snapshot")
    if (
        type(raw_target) is not dict
        or not _REQUIRED_TARGET_FIELDS.issubset(raw_target)
        or not frozenset(raw_target).issubset(_TARGET_FIELDS)
    ):
        raise TaskObservationConflict("画布生成结果的原始目标快照无效")
    target_values = {field: raw_target.get(field) for field in _TARGET_FIELDS}

    for field in _STRING_FIELDS:
        value = target_values[field]
        if value is not None and not isinstance(value, str):
            raise TaskObservationConflict("画布生成结果的原始目标快照无效")
        if isinstance(value, str) and _has_control_characters(value):
            raise TaskObservationConflict("画布生成结果的原始目标快照无效")
    for field in _ID_FIELDS:
        value = target_values[field]
        if value is not None and not is_valid_canvas_identifier(value):
            raise TaskObservationConflict("画布生成结果的原始目标快照无效")
    for field in _DIGEST_FIELDS:
        value = target_values[field]
        if value is not None and not _is_sha256(value):
            raise TaskObservationConflict("画布生成结果的原始目标快照无效")
    for field in _INTEGER_FIELDS:
        value = target_values[field]
        if value is not None and type(value) is not int:
            raise TaskObservationConflict("画布生成结果的原始目标快照无效")

    target = TargetSnapshot(**target_values)
    if (
        target.kind not in _CANVAS_TARGET_KINDS
        or not is_valid_canvas_identifier(target.chapter_id)
        or not is_valid_canvas_identifier(target.series_id)
        or target.compat_frame_index is not None and target.compat_frame_index < 0
        or target.notes_revision is not None and target.notes_revision < 0
    ):
        raise TaskObservationConflict("画布生成结果的原始目标快照无效")

    if target.kind == "canvas_frame_preview":
        if (
            target.asset_type not in {None, "storyboard"}
            or not _is_sha256(target.full_frame_digest)
            or (
                target.stable_storyboard_asset_id is None
                and not _is_sha256(target.full_chapter_digest)
            )
            or (
                target.same_series_target_digest is not None
                and not _is_sha256(target.same_series_target_digest)
            )
        ):
            raise TaskObservationConflict("画布分镜快照无效")
        if target.stable_storyboard_asset_id is None:
            if (
                target.asset_type is not None
                or target.asset_id is not None
                or type(target.compat_frame_index) is not int
            ):
                raise TaskObservationConflict("画布分镜身份快照无效")
    else:
        if (
            target.asset_type not in _ASSET_TYPES
            or not is_valid_canvas_identifier(target.asset_id)
            or target.stable_storyboard_asset_id is not None
            or target.compat_frame_index is not None
            or not _is_sha256(target.same_series_target_digest)
        ):
            raise TaskObservationConflict("画布素材快照无效")
    return target


def _unique_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def is_valid_canvas_identifier(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 36
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or _has_control_characters(value)
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _has_control_characters(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _contains_only_finite_numbers(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_contains_only_finite_numbers(item) for item in value.values())
    if isinstance(value, list):
        return all(_contains_only_finite_numbers(item) for item in value)
    return True


__all__ = ("is_valid_canvas_identifier", "lookup_canvas_result_application")
