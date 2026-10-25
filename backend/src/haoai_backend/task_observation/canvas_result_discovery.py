"""Bounded, read-only discovery of canonical Canvas generation results."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.asset_data.generation import digest_asset_target
from haoai_backend.asset_data.tables import ASSET_TABLES
from haoai_backend.generation_contracts.canvas_targets import (
    CANVAS_ASSET_TARGET,
    CANVAS_ASSET_TYPES,
    CANVAS_FRAME_TARGET,
    CanvasTargetParseError,
    canonical_target_snapshot_digest,
    canvas_target_identity,
    is_valid_canvas_identifier,
    target_snapshot_from_request_data,
    target_snapshot_mapping,
)
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import GenerationActor, TargetSnapshot
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_core.canvas_result_targets import (
    CanvasResultTargetProjection,
    prepare_canvas_result_target_projection,
)
from haoai_backend.generation_core.schema import (
    GenerationResultReceiptRow,
    GenerationResultTargetRow,
)
from haoai_backend.generation_runtime.readiness import check_generation_readiness
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data.storyboard import first_storyboard_id
from haoai_backend.series_data.tables import chapters, storyboard_assets
from haoai_backend.shared.identity import TrustedActor

from .errors import (
    TaskObservationBadRequest,
    TaskObservationForbidden,
    TaskObservationNotFound,
    TaskObservationUnavailable,
)
from .generation import (
    ResultLookup,
    application_key_for,
    application_ref_for,
    current_target_application,
    expected_target_digest,
    find_application_record,
    load_owned_completed_result,
    target_matches_receipt,
    target_scope_for_snapshot,
)

SessionFactory = Callable[[], Session]
DiscoveryEligibility = Literal[
    "ready", "already_applied", "stale", "target_deleted", "unready"
]
_CANVAS_OPERATIONS = frozenset({"image.single", "image.batch"})
_CURSOR_VERSION = 1
_CURSOR_LIMIT = 2048
_SAFE_INTEGER_MAX = (1 << 53) - 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DIGITS_RE = re.compile(r"^[0-9]+$")


@dataclass(frozen=True, slots=True)
class CanvasTargetSelector:
    target_kind: str
    target_identity: str
    target: dict[str, object]
    frame_index: int | None = None
    expected_chapter_digest: str | None = None
    storyboard_asset_id: str | None = None
    asset_type: str | None = None
    asset_id: str | None = None


@dataclass(frozen=True, slots=True)
class CanvasTargetBackfillReport:
    scanned: int
    projected: int
    already_present: int
    unready: int
    conflict: int
    ignored_noncanvas: int
    next_cursor: str | None
    has_more: bool


def normalize_canvas_target_selector(
    chapter_id: str,
    query_items: list[tuple[str, str]],
) -> tuple[CanvasTargetSelector, int, str | None]:
    """Validate and normalize the complete selector/limit/cursor query."""

    if not is_valid_canvas_identifier(chapter_id):
        raise TaskObservationBadRequest("章节标识必须为 1 到 36 个有效字符")
    values: dict[str, str] = {}
    for key, value in query_items:
        if key in values:
            raise TaskObservationBadRequest("查询参数不能重复")
        values[key] = value

    limit_text = values.pop("limit", "20")
    cursor = values.pop("cursor", None)
    limit = _parse_bounded_decimal(
        limit_text,
        maximum=50,
        detail="limit 必须为 1 到 50 的整数",
    )
    if limit < 1:
        raise TaskObservationBadRequest("limit 必须为 1 到 50 的整数")
    if cursor is not None and (
        not cursor
        or len(cursor) > _CURSOR_LIMIT
        or not cursor.isascii()
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for character in cursor)
    ):
        raise TaskObservationBadRequest("分页游标无效")

    target_kind = values.pop("target_kind", None)
    if target_kind == "frame_preview":
        selector = _normalize_frame_selector(chapter_id, values)
    elif target_kind == "asset_image":
        selector = _normalize_asset_selector(chapter_id, values)
    else:
        raise TaskObservationBadRequest("target_kind 必须为 frame_preview 或 asset_image")
    return selector, limit, cursor


def _normalize_frame_selector(
    chapter_id: str,
    values: dict[str, str],
) -> CanvasTargetSelector:
    if "storyboard_asset_id" in values:
        if set(values) != {"storyboard_asset_id"}:
            raise TaskObservationBadRequest("分镜选择器不能混用其他身份参数")
        storyboard_id = values["storyboard_asset_id"]
        _require_identifier(storyboard_id)
        target = {
            "kind": "frame_preview",
            "chapter_id": chapter_id,
            "storyboard_asset_id": storyboard_id,
        }
        return CanvasTargetSelector(
            target_kind=CANVAS_FRAME_TARGET,
            target_identity=f"storyboard|{storyboard_id}",
            target=target,
            storyboard_asset_id=storyboard_id,
        )

    if set(values) != {
        "identity_mode",
        "frame_index",
        "expected_chapter_digest",
    }:
        raise TaskObservationBadRequest("严格无ID分镜选择器字段不完整或包含未知参数")
    if values["identity_mode"] != "strict-no-id-v1":
        raise TaskObservationBadRequest("identity_mode 无效")
    index_text = values["frame_index"]
    frame_index = _parse_bounded_decimal(
        index_text,
        maximum=_SAFE_INTEGER_MAX,
        detail="frame_index 必须为非负安全整数",
    )
    chapter_digest = values["expected_chapter_digest"]
    if not _SHA256_RE.fullmatch(chapter_digest):
        raise TaskObservationBadRequest("expected_chapter_digest 必须为小写 SHA256")
    target = {
        "kind": "frame_preview",
        "chapter_id": chapter_id,
        "identity_mode": "strict-no-id-v1",
        "frame_index": frame_index,
        "expected_chapter_digest": chapter_digest,
    }
    return CanvasTargetSelector(
        target_kind=CANVAS_FRAME_TARGET,
        target_identity=(
            f"strict-no-id|chapter|{chapter_digest}|index|{frame_index}"
        ),
        target=target,
        frame_index=frame_index,
        expected_chapter_digest=chapter_digest,
    )


def _normalize_asset_selector(
    chapter_id: str,
    values: dict[str, str],
) -> CanvasTargetSelector:
    if set(values) != {"asset_type", "asset_id"}:
        raise TaskObservationBadRequest("素材选择器字段不完整或包含未知参数")
    asset_type = values["asset_type"]
    asset_id = values["asset_id"]
    if asset_type not in CANVAS_ASSET_TYPES:
        raise TaskObservationBadRequest("asset_type 无效")
    _require_identifier(asset_id)
    return CanvasTargetSelector(
        target_kind=CANVAS_ASSET_TARGET,
        target_identity=f"asset|{asset_type}|{asset_id}",
        target={
            "kind": "asset_image",
            "chapter_id": chapter_id,
            "asset_type": asset_type,
            "asset_id": asset_id,
        },
        asset_type=asset_type,
        asset_id=asset_id,
    )


def discover_canvas_results(
    session_factory: SessionFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    selector: CanvasTargetSelector,
    *,
    limit: int,
    cursor: str | None,
) -> dict[str, object]:
    """Read one bounded page from the indexed owner projection."""

    if session_factory is None:
        raise TaskObservationUnavailable("画布结果发现服务尚未接线")
    session: Session | None = None
    cleanup_error: SQLAlchemyError | None = None
    try:
        candidate = session_factory()
        if not isinstance(candidate, Session):
            raise TaskObservationUnavailable("画布结果发现服务尚未接线")
        session = candidate
        with session.no_autoflush:
            return _discover_in_session(
                session,
                actor,
                chapter_id,
                selector,
                limit=limit,
                cursor=cursor,
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


def _discover_in_session(
    session: Session,
    actor: TrustedActor,
    chapter_id: str,
    selector: CanvasTargetSelector,
    *,
    limit: int,
    cursor: str | None,
) -> dict[str, object]:
    if not is_valid_canvas_identifier(actor.user_id):
        raise TaskObservationUnavailable("可信身份端口未正确接线")
    readiness = check_generation_readiness(session)
    if readiness.status != "ready":
        raise TaskObservationUnavailable("画布结果发现数据结构尚未就绪")

    chapter = session.execute(
        select(chapters).where(chapters.c.id == chapter_id)
    ).mappings().one_or_none()
    if chapter is None:
        raise TaskObservationNotFound("章节不存在")
    current_series_id = str(chapter["series_id"])
    try:
        require_series_access(
            SqlAlchemySeriesAccessReader(session),
            ActorIdentity(actor.user_id),
            current_series_id,
        )
    except SeriesNotFound as exc:
        raise TaskObservationNotFound("剧集不存在") from exc
    except SeriesAccessDenied as exc:
        raise TaskObservationForbidden("无权访问该剧集") from exc

    strict_no_id_identity_current = (
        None
        if selector.frame_index is None
        else _strict_no_id_identity_is_current(chapter.get("content"), selector)
    )

    cursor_position = (
        None
        if cursor is None
        else _decode_cursor(cursor, actor.user_id, chapter_id, selector.target)
    )
    statement = (
        select(
            GenerationResultTargetRow,
            GenerationResultReceiptRow,
            AITaskRow.id,
            AITaskRow.user_id,
            AITaskRow.status,
        )
        .join(
            GenerationResultReceiptRow,
            and_(
                GenerationResultReceiptRow.result_ref
                == GenerationResultTargetRow.result_ref,
                GenerationResultReceiptRow.owner_id == GenerationResultTargetRow.owner_id,
                GenerationResultReceiptRow.task_id == GenerationResultTargetRow.task_id,
            ),
        )
        .join(
            AITaskRow,
            and_(
                AITaskRow.id == GenerationResultTargetRow.task_id,
                AITaskRow.user_id == GenerationResultTargetRow.owner_id,
            ),
        )
        .where(
            GenerationResultTargetRow.owner_id == actor.user_id,
            GenerationResultTargetRow.series_id == current_series_id,
            GenerationResultTargetRow.chapter_id == chapter_id,
            GenerationResultTargetRow.target_kind == selector.target_kind,
            GenerationResultTargetRow.target_identity == selector.target_identity,
            GenerationResultReceiptRow.operation.in_(_CANVAS_OPERATIONS),
            AITaskRow.status == "completed",
        )
        .order_by(
            GenerationResultTargetRow.generation_fulfilled_at.desc(),
            GenerationResultTargetRow.result_ref.desc(),
        )
        .limit(limit + 1)
    )
    if cursor_position is not None:
        fulfilled_at, result_ref = cursor_position
        statement = statement.where(
            or_(
                GenerationResultTargetRow.generation_fulfilled_at < fulfilled_at,
                and_(
                    GenerationResultTargetRow.generation_fulfilled_at == fulfilled_at,
                    GenerationResultTargetRow.result_ref < result_ref,
                ),
            )
        )
    rows = session.execute(statement).all()
    page_rows = rows[:limit]
    items = [
        _candidate_item(
            session,
            actor,
            chapter,
            target_row,
            receipt_row,
            task_id,
            task_owner_id,
            task_status,
            selector,
            strict_no_id_identity_current=strict_no_id_identity_current,
        )
        for target_row, receipt_row, task_id, task_owner_id, task_status in page_rows
    ]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page_rows:
        last_target = page_rows[-1][0]
        next_cursor = _encode_cursor(
            actor.user_id,
            chapter_id,
            selector.target,
            last_target.generation_fulfilled_at,
            last_target.result_ref,
        )
    return {
        "chapter_id": chapter_id,
        "target": selector.target,
        "items": items,
        "next_cursor": next_cursor,
    }


def _candidate_item(
    session: Session,
    actor: TrustedActor,
    chapter: Mapping[str, object],
    target_row: GenerationResultTargetRow,
    receipt_row: GenerationResultReceiptRow,
    task_id: str,
    task_owner_id: str,
    task_status: str,
    selector: CanvasTargetSelector,
    *,
    strict_no_id_identity_current: bool | None,
) -> dict[str, object]:
    base = {
        "task_id": target_row.task_id,
        "result_ref": target_row.result_ref,
        "operation": receipt_row.operation,
        "completed_at": _iso_utc(receipt_row.generation_fulfilled_at),
    }

    loaded: ResultLookup = load_owned_completed_result(
        session,
        GenerationActor(actor.user_id, actor),
        target_row.task_id,
        target_row.result_ref,
    )
    if (
        loaded in {"not_found", "unready"}
        or not isinstance(loaded, GenerationResultReceipt)
        or loaded.operation not in _CANVAS_OPERATIONS
        or loaded.operation != receipt_row.operation
        or loaded.owner_id != actor.user_id
        or loaded.task_id != target_row.task_id
        or loaded.result_ref != target_row.result_ref
        or loaded.unit_id != receipt_row.unit_id
        or task_id != target_row.task_id
        or task_owner_id != actor.user_id
        or task_status != "completed"
        or not _same_instant(
            loaded.generation_fulfilled_at,
            target_row.generation_fulfilled_at,
        )
    ):
        return _unready_item(base)

    task_request_data = session.execute(
        select(AITaskRow.request_data).where(
            AITaskRow.id == task_id,
            AITaskRow.user_id == actor.user_id,
            AITaskRow.status == "completed",
        )
    ).scalar_one_or_none()
    try:
        target = target_snapshot_from_request_data(task_request_data)
        expected_target = target_snapshot_mapping(target)
        target_identity = canvas_target_identity(target)
        target_digest = canonical_target_snapshot_digest(target)
        target_scope = target_scope_for_snapshot(target)
        expected_digest = expected_target_digest(target)
    except (CanvasTargetParseError, TypeError, ValueError):
        return _unready_item(base)
    if (
        target.kind != target_row.target_kind
        or target.chapter_id != target_row.chapter_id
        or target.series_id != target_row.series_id
        or target_identity != target_row.target_identity
        or target_digest != target_row.target_snapshot_digest
        or target.chapter_id != selector.target["chapter_id"]
        or not target_matches_receipt(session, loaded, target)
    ):
        return _unready_item(base)

    application_key = application_key_for(loaded.result_ref, target_scope)
    try:
        outcome = current_target_application(
            session,
            loaded,
            target,
            expected_digest,
            application_key,
        )
    except (TypeError, ValueError):
        return _unready_item(base)
    if outcome is not None:
        if outcome.kind not in {"already_applied", "applied"}:
            return _unready_item(base)
        application = _application_proof(
            session,
            loaded,
            target_scope,
            expected_digest,
            outcome.application_ref,
            outcome.target_revision,
        )
        if application is None:
            return _unready_item(base)
        return {
            **base,
            "expected_target": expected_target,
            "application_key": application_key,
            "eligibility": "already_applied",
            "application": application,
        }

    eligibility = _current_target_eligibility(
        session,
        chapter,
        target,
        selector,
        strict_no_id_identity_current=strict_no_id_identity_current,
    )
    return {
        **base,
        "expected_target": expected_target,
        "application_key": application_key,
        "eligibility": eligibility,
        "application": None,
    }


def _application_proof(
    session: Session,
    receipt: GenerationResultReceipt,
    target_scope: str,
    expected_digest: str,
    outcome_application_ref: str | None,
    outcome_after_digest: str | None,
) -> dict[str, object] | None:
    record = find_application_record(
        session,
        receipt.owner_id,
        receipt.task_id,
        receipt.result_ref,
        target_scope,
    )
    if (
        record is None
        or record.application_ref != application_ref_for(receipt.result_ref, target_scope)
        or record.application_ref != outcome_application_ref
        or not is_valid_canvas_identifier(record.application_ref)
        or record.result_ref != receipt.result_ref
        or record.owner_id != receipt.owner_id
        or record.task_id != receipt.task_id
        or record.unit_id != receipt.unit_id
        or record.target_scope != target_scope
        or record.result_digest != receipt.content_digest
        or record.delivery_status != "applied"
        or record.before_target_digest != expected_digest
        or not _is_sha256(record.after_target_digest)
        or record.after_target_digest != outcome_after_digest
        or not isinstance(record.committed_at, datetime)
    ):
        return None
    committed_at = _as_utc(record.committed_at)
    return {
        "application_ref": record.application_ref,
        "target_revision": record.after_target_digest,
        "committed_at": committed_at.isoformat(),
    }


def _current_target_eligibility(
    session: Session,
    chapter: Mapping[str, object],
    target: TargetSnapshot,
    selector: CanvasTargetSelector,
    *,
    strict_no_id_identity_current: bool | None,
) -> DiscoveryEligibility:
    if target.kind == CANVAS_FRAME_TARGET:
        if target.stable_storyboard_asset_id is None:
            if (
                target.full_chapter_digest != selector.expected_chapter_digest
                or target.compat_frame_index != selector.frame_index
            ):
                return "unready"
            if strict_no_id_identity_current is not True:
                return "stale"
            frames = _parse_canonical_frames(chapter.get("content"))
            if frames is None:
                return "stale"
            index = target.compat_frame_index
            if (
                type(index) is not int
                or not 0 <= index < len(frames)
                or not _frame_has_no_storyboard_identity(frames[index])
            ):
                return "stale"
            return (
                "ready"
                if _canonical_json_digest(frames[index]) == target.full_frame_digest
                else "stale"
            )

        frames = _parse_canonical_frames(chapter.get("content"))
        if frames is None:
            return "stale"
        matching = [
            index
            for index, frame in enumerate(frames)
            if first_storyboard_id(frame) == target.stable_storyboard_asset_id
        ]
        asset = session.execute(
            select(storyboard_assets).where(
                storyboard_assets.c.id == target.stable_storyboard_asset_id,
                storyboard_assets.c.chapter_id == target.chapter_id,
                storyboard_assets.c.series_id == target.series_id,
            )
        ).mappings().one_or_none()
        if not matching or asset is None:
            return "target_deleted"
        if len(matching) != 1 or asset["frame_index"] != matching[0]:
            return "stale"
        return (
            "ready"
            if _canonical_json_digest(frames[matching[0]]) == target.full_frame_digest
            else "stale"
        )

    table = ASSET_TABLES.get(target.asset_type or "")
    if table is None or target.asset_id is None:
        return "unready"
    asset = session.execute(
        select(table).where(
            table.c.id == target.asset_id,
            table.c.series_id == target.series_id,
        )
    ).mappings().one_or_none()
    if asset is None:
        return "target_deleted"
    row = dict(asset)
    if row.get("image_url") != target.expected_asset_image_url:
        return "stale"
    try:
        digest = digest_asset_target(target.asset_type or "", row)
    except (TypeError, ValueError):
        return "stale"
    return "ready" if digest == target.same_series_target_digest else "stale"


def _parse_canonical_frames(content: object) -> list[dict[str, object]] | None:
    if not isinstance(content, str) or not content:
        return None
    try:
        frames = json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if type(frames) is not list or any(type(frame) is not dict for frame in frames):
        return None
    if not _contains_only_finite_json(frames):
        return None
    return frames


def _frame_has_no_storyboard_identity(frame: Mapping[str, object]) -> bool:
    if "storyboard" not in frame:
        return True
    references = frame["storyboard"]
    return isinstance(references, list) and all(
        reference is None or reference == "" for reference in references
    )


def _canonical_json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _chapter_digest_from_canvas_source(content: object) -> str | None:
    try:
        from haoai_backend.canvas_sources.domain import chapter_canonical_digest
    except ImportError as exc:
        raise TaskObservationUnavailable("严格无ID章节摘要服务尚未接线") from exc
    return chapter_canonical_digest(content)


def _strict_no_id_identity_is_current(
    content: object,
    selector: CanvasTargetSelector,
) -> bool:
    """Check freshness before reading the current frame at the selector index."""

    current_digest = _chapter_digest_from_canvas_source(content)
    if current_digest != selector.expected_chapter_digest:
        return False
    frames = _parse_canonical_frames(content)
    index = selector.frame_index
    return bool(
        frames is not None
        and type(index) is int
        and 0 <= index < len(frames)
        and _frame_has_no_storyboard_identity(frames[index])
    )


def backfill_canvas_result_targets(
    session: Session,
    actor: TrustedActor,
    *,
    after_result_ref: str | None = None,
    limit: int = 50,
) -> CanvasTargetBackfillReport:
    """Stage a bounded owner-scoped projection repair in the caller Session."""

    if not isinstance(session, Session):
        raise TypeError("backfill requires the caller-owned SQLAlchemy Session")
    if not is_valid_canvas_identifier(actor.user_id):
        raise ValueError("trusted actor identity is invalid")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be between one and fifty")
    if after_result_ref is not None and not is_valid_canvas_identifier(after_result_ref):
        raise ValueError("after_result_ref is invalid")

    statement = select(GenerationResultReceiptRow.result_ref).where(
        GenerationResultReceiptRow.owner_id == actor.user_id
    )
    if after_result_ref is not None:
        statement = statement.where(
            GenerationResultReceiptRow.result_ref > after_result_ref
        )
    statement = statement.order_by(
        GenerationResultReceiptRow.owner_id,
        GenerationResultReceiptRow.result_ref,
    ).limit(limit + 1)

    with session.no_autoflush:
        result_refs = [row[0] for row in session.execute(statement).all()]
        has_more = len(result_refs) > limit
        scanned_refs = result_refs[:limit]
        projected = 0
        already_present = 0
        unready = 0
        conflict = 0
        ignored_noncanvas = 0
        for result_ref in scanned_refs:
            receipt_row = session.get(GenerationResultReceiptRow, result_ref)
            if receipt_row is None or receipt_row.owner_id != actor.user_id:
                unready += 1
                continue
            loaded: ResultLookup = load_owned_completed_result(
                session,
                GenerationActor(actor.user_id, actor),
                receipt_row.task_id,
                result_ref,
            )
            if not isinstance(loaded, GenerationResultReceipt):
                unready += 1
                continue

            decision: CanvasResultTargetProjection = (
                prepare_canvas_result_target_projection(session, loaded)
            )
            current = session.get(GenerationResultTargetRow, result_ref)
            if current is None:
                current = _pending_projection(session, result_ref)
            if decision.status == "ignored_noncanvas":
                if current is None:
                    ignored_noncanvas += 1
                else:
                    conflict += 1
                continue
            if decision.status == "invalid_binding" or decision.row is None:
                if current is None:
                    unready += 1
                else:
                    conflict += 1
                continue

            if current is not None:
                if _projection_rows_equal(current, decision.row):
                    already_present += 1
                else:
                    conflict += 1
                continue
            session.add(decision.row)
            projected += 1

    next_cursor = scanned_refs[-1] if has_more and scanned_refs else None
    return CanvasTargetBackfillReport(
        scanned=len(scanned_refs),
        projected=projected,
        already_present=already_present,
        unready=unready,
        conflict=conflict,
        ignored_noncanvas=ignored_noncanvas,
        next_cursor=next_cursor,
        has_more=has_more,
    )


def _projection_rows_equal(
    left: GenerationResultTargetRow,
    right: GenerationResultTargetRow,
) -> bool:
    return all(
        getattr(left, name) == getattr(right, name)
        for name in (
            "result_ref",
            "owner_id",
            "task_id",
            "series_id",
            "chapter_id",
            "target_kind",
            "target_identity",
            "target_snapshot_digest",
        )
    ) and _same_instant(left.generation_fulfilled_at, right.generation_fulfilled_at)


def _pending_projection(
    session: Session,
    result_ref: str,
) -> GenerationResultTargetRow | None:
    return next(
        (
            row
            for row in session.new
            if isinstance(row, GenerationResultTargetRow)
            and row.result_ref == result_ref
        ),
        None,
    )


def _unready_item(base: Mapping[str, object]) -> dict[str, object]:
    return {
        **base,
        "expected_target": None,
        "application_key": None,
        "eligibility": "unready",
        "application": None,
    }


def _encode_cursor(
    actor_id: str,
    chapter_id: str,
    target: Mapping[str, object],
    fulfilled_at: datetime,
    result_ref: str,
) -> str:
    payload = {
        "v": _CURSOR_VERSION,
        "actor_id": actor_id,
        "chapter_id": chapter_id,
        "target": dict(target),
        "completed_at": _iso_utc(fulfilled_at),
        "result_ref": result_ref,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def _decode_cursor(
    cursor: str,
    actor_id: str,
    chapter_id: str,
    target: Mapping[str, object],
) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskObservationBadRequest("分页游标无效") from exc
    if (
        type(payload) is not dict
        or set(payload)
        != {"v", "actor_id", "chapter_id", "target", "completed_at", "result_ref"}
        or type(payload.get("v")) is not int
        or payload["v"] != _CURSOR_VERSION
        or payload.get("actor_id") != actor_id
        or payload.get("chapter_id") != chapter_id
        or payload.get("target") != dict(target)
        or not isinstance(payload.get("completed_at"), str)
        or not isinstance(payload.get("result_ref"), str)
        or not is_valid_canvas_identifier(payload["result_ref"])
    ):
        raise TaskObservationBadRequest("分页游标不属于当前身份、章节或选择器")
    try:
        fulfilled_at = datetime.fromisoformat(payload["completed_at"])
        if fulfilled_at.utcoffset() is None:
            raise ValueError("cursor timestamp has no timezone")
        normalized_at = _as_utc(fulfilled_at).replace(tzinfo=None)
    except (OverflowError, TypeError, ValueError) as exc:
        raise TaskObservationBadRequest("分页游标时间无效") from exc
    canonical = _encode_cursor(
        actor_id,
        chapter_id,
        target,
        normalized_at,
        payload["result_ref"],
    )
    if canonical != cursor:
        raise TaskObservationBadRequest("分页游标编码不是规范格式")
    return normalized_at, payload["result_ref"]


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _contains_only_finite_json(value: object) -> bool:
    if value is None or type(value) in {str, bool, int}:
        return True
    if type(value) is float:
        return value == value and value not in {float("inf"), float("-inf")}
    if type(value) is list:
        return all(_contains_only_finite_json(item) for item in value)
    if type(value) is dict:
        return all(
            isinstance(key, str) and _contains_only_finite_json(item)
            for key, item in value.items()
        )
    return False


def _require_identifier(value: str) -> None:
    if not is_valid_canvas_identifier(value):
        raise TaskObservationBadRequest("画布目标标识必须为 1 到 36 个有效字符")


def _parse_bounded_decimal(value: str, *, maximum: int, detail: str) -> int:
    if not _DIGITS_RE.fullmatch(value):
        raise TaskObservationBadRequest(detail)
    normalized = value.lstrip("0") or "0"
    if len(normalized) > len(str(maximum)):
        raise TaskObservationBadRequest(detail)
    try:
        parsed = int(normalized)
    except ValueError as exc:
        raise TaskObservationBadRequest(detail) from exc
    if parsed > maximum:
        raise TaskObservationBadRequest(detail)
    return parsed


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _same_instant(left: object, right: object) -> bool:
    if not isinstance(left, datetime) or not isinstance(right, datetime):
        return False
    return _as_utc(left) == _as_utc(right)


def _as_utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).isoformat()


__all__ = (
    "CanvasTargetBackfillReport",
    "CanvasTargetSelector",
    "backfill_canvas_result_targets",
    "discover_canvas_results",
    "normalize_canvas_target_selector",
)
