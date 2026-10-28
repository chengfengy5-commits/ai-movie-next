"""Use cases for original save/create operations in the continuous chapter editor."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from haoai_backend.shared.identity import TrustedActor

from fastapi.encoders import jsonable_encoder

from .continuous_operation_receipts import (
    canonical_json,
    continuous_source_snapshot_binding,
    parse_json_object,
    receipt_record,
    sha256_text,
)
from .continuous_operation_schemas import (
    CreateAssetCommand,
    ContinuousChapterOperationRequest,
    SaveChapterCommand,
)
from .errors import SeriesDataBadRequest, SeriesDataConflict, SeriesDataNotFound, SeriesDataUnavailable
from .ports import UnitOfWorkFactory
from .presentation import chapter_payload


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ContinuousOperationExecution:
    status_code: int
    payload: dict[str, Any]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _open(factory: UnitOfWorkFactory | None):
    if factory is None:
        raise SeriesDataUnavailable()
    uow = factory()
    uow.ensure_clean()
    return uow


def _operation_uuid(value: str, field: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise SeriesDataBadRequest(f"{field} must be a UUID") from error
    if str(parsed) != value:
        raise SeriesDataBadRequest(f"{field} must use canonical lowercase UUID form")
    return value


def _source_guard(command: SaveChapterCommand | CreateAssetCommand):
    return command.chapter_edit_guard if isinstance(command, SaveChapterCommand) else command.source_context


def _command_kind(command: SaveChapterCommand | CreateAssetCommand) -> str:
    return command.kind


def _source_digest(request_body_sha256: str) -> str:
    return continuous_source_snapshot_binding(request_body_sha256)


def _target_manifest_digest(command: SaveChapterCommand | CreateAssetCommand) -> str:
    return sha256_text(canonical_json(command.draft_frame_ids))


def _identity_for(
    command: SaveChapterCommand | CreateAssetCommand,
    *,
    chapter_id: str,
    source_digest: str,
    manifest_digest: str,
) -> dict[str, Any]:
    if isinstance(command, SaveChapterCommand):
        return {
            "kind": command.kind,
            "series_id": command.chapter_edit_guard.expected_series_id,
            "chapter_id": chapter_id,
            "draft_frame_ids": command.draft_frame_ids,
            "manifest_digest": manifest_digest,
            "source_snapshot_digest": source_digest,
        }
    asset_kind = command.kind.removeprefix("create-")
    return {
        "kind": command.kind,
        "asset_type": asset_kind,
        "series_id": command.source_context.expected_series_id,
        "chapter_id": chapter_id,
        "target_draft_id": command.target_draft_id,
        "draft_frame_ids": command.draft_frame_ids,
        "manifest_digest": manifest_digest,
        "source_snapshot_digest": source_digest,
    }


def _receipt_payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        result_identity = parse_json_object(str(row["result_identity"]))
        result = parse_json_object(str(row["canonical_response"]))
    except (TypeError, ValueError) as error:
        raise SeriesDataUnavailable("原操作回执内容无法读取") from error
    if (
        row.get("receipt_version") != 1
        or row.get("outcome") not in {"committed", "rejected"}
        or not _SHA256_PATTERN.fullmatch(str(row.get("request_body_sha256", "")))
        or not _SHA256_PATTERN.fullmatch(str(row.get("source_snapshot_digest", "")))
        or not _SHA256_PATTERN.fullmatch(str(row.get("result_digest", "")))
        or not _SHA256_PATTERN.fullmatch(str(row.get("target_manifest_digest", "")))
        or result_identity.get("kind") != row.get("command_kind")
        or result_identity.get("series_id") != row.get("series_id")
        or result_identity.get("chapter_id") != row.get("chapter_id")
    ):
        raise SeriesDataUnavailable("原操作回执字段校验失败")
    if row.get("outcome") == "committed" and row.get("http_status") != 200:
        raise SeriesDataUnavailable("原操作成功回执状态无效")
    if row.get("outcome") == "rejected" and not 400 <= int(row.get("http_status", 0)) < 500:
        raise SeriesDataUnavailable("原操作拒绝回执状态无效")
    response_digest = sha256_text(str(row["canonical_response"]))
    if response_digest != row["result_digest"]:
        raise SeriesDataUnavailable("原操作回执摘要校验失败")
    committed_at = row["committed_at"]
    if isinstance(committed_at, datetime):
        committed_at_value = committed_at.isoformat()
    else:
        committed_at_value = str(committed_at)
    return {
        "receipt_version": row["receipt_version"],
        "operation_id": row["operation_id"],
        "owner_id": row["owner_id"],
        "editor_session_id": row["editor_session_id"],
        "series_id": row["series_id"],
        "chapter_id": row["chapter_id"],
        "command_kind": row["command_kind"],
        "request_body_sha256": row["request_body_sha256"],
        "source_snapshot_digest": row["source_snapshot_digest"],
        "target_draft_id": row["target_draft_id"],
        "target_manifest_digest": row["target_manifest_digest"],
        "outcome": row["outcome"],
        "http_status": row["http_status"],
        "result_identity": result_identity,
        "result": result,
        "canonical_response_json": str(row["canonical_response"]),
        "result_digest": row["result_digest"],
        "committed_at": committed_at_value,
    }


def _same_identity(
    row: dict[str, Any],
    *,
    operation_id: str,
    chapter_id: str,
    series_id: str,
    editor_session_id: str,
    command_kind: str,
    body_digest: str,
    source_digest: str,
    target_draft_id: str | None,
    manifest_digest: str,
) -> bool:
    return (
        row["operation_id"] == operation_id
        and row["chapter_id"] == chapter_id
        and row["series_id"] == series_id
        and row["editor_session_id"] == editor_session_id
        and row["command_kind"] == command_kind
        and row["request_body_sha256"] == body_digest
        and row["source_snapshot_digest"] == source_digest
        and row["target_draft_id"] == target_draft_id
        and row["target_manifest_digest"] == manifest_digest
    )


def _execution_from_receipt(row: dict[str, Any]) -> ContinuousOperationExecution:
    payload = _receipt_payload(row)
    result: dict[str, Any] = {"receipt": payload}
    if payload["outcome"] == "rejected":
        result["error"] = payload["result"]
        return ContinuousOperationExecution(int(payload["http_status"]), result)
    result["result"] = payload["result"]
    return ContinuousOperationExecution(200, result)


def _execution_from_payload(receipt: dict[str, Any]) -> ContinuousOperationExecution:
    if receipt["outcome"] == "rejected":
        return ContinuousOperationExecution(
            int(receipt["http_status"]),
            {"receipt": receipt, "error": receipt["result"]},
        )
    return ContinuousOperationExecution(
        200,
        {"receipt": receipt, "result": receipt["result"]},
    )


def _schema_ready(uow: Any) -> bool:
    try:
        return uow.continuous_operation_schema_ready()
    except Exception:
        return False


def continuous_operation_receipts_ready(factory: UnitOfWorkFactory | None) -> bool:
    """Read-only capability check; absent or malformed feature schema stays unavailable."""

    if factory is None:
        return False
    try:
        uow = _open(factory)
    except Exception:
        return False
    try:
        return _schema_ready(uow)
    finally:
        uow.close()


def execute_continuous_chapter_operation(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    operation_id: str,
    request: ContinuousChapterOperationRequest,
    request_body_sha256: str,
) -> ContinuousOperationExecution:
    """Stage one original business operation and its receipt in one UOW commit."""

    operation_id = _operation_uuid(operation_id, "operation_id")
    editor_session_id = _operation_uuid(request.editor_session_id, "editor_session_id")
    if not _SHA256_PATTERN.fullmatch(request_body_sha256):
        raise SeriesDataBadRequest("原请求摘要无效")
    command = request.command
    guard = _source_guard(command)
    if guard.expected_chapter_id != chapter_id:
        raise SeriesDataBadRequest("请求章节与来源基线不一致")
    series_id = guard.expected_series_id
    command_kind = _command_kind(command)
    source_digest = _source_digest(request_body_sha256)
    manifest_digest = _target_manifest_digest(command)
    target_draft_id = command.target_draft_id if isinstance(command, CreateAssetCommand) else None
    identity = _identity_for(
        command,
        chapter_id=chapter_id,
        source_digest=source_digest,
        manifest_digest=manifest_digest,
    )

    uow = _open(factory)
    try:
        if not _schema_ready(uow):
            raise SeriesDataUnavailable("连续编辑原操作回执表尚未就绪")
        # Readiness inspection starts SQLAlchemy autobegin; close that read-only
        # transaction before opening the operation's explicit write transaction.
        uow.rollback()
        uow.begin_continuous_operation()
        existing = uow.get_continuous_operation_receipt(actor.user_id, operation_id, for_update=True)
        if existing is not None:
            if existing["chapter_id"] != chapter_id:
                raise SeriesDataNotFound("原操作不存在")
            uow.require_access(actor, str(existing["series_id"]))
            if not _same_identity(
                existing,
                operation_id=operation_id,
                chapter_id=chapter_id,
                series_id=series_id,
                editor_session_id=editor_session_id,
                command_kind=command_kind,
                body_digest=request_body_sha256,
                source_digest=source_digest,
                target_draft_id=target_draft_id,
                manifest_digest=manifest_digest,
            ):
                raise SeriesDataConflict("原操作编号已绑定其他请求")
            uow.commit()
            return _execution_from_receipt(existing)

        uow.require_access(actor, series_id)
        try:
            with uow.continuous_operation_savepoint():
                if isinstance(command, SaveChapterCommand):
                    chapter = uow.stage_update_chapter_guarded(
                        actor,
                        chapter_id,
                        command.content,
                        command.chapter_edit_guard,
                        _utcnow(),
                    )
                    result = jsonable_encoder(chapter_payload(chapter))
                    result_content = result.get("content")
                    if not isinstance(result_content, list):
                        raise SeriesDataUnavailable("保存后的章节回读结果不可用")
                    frame_storyboard_ids = [
                        frame.get("storyboard") if isinstance(frame, dict) else None
                        for frame in result_content
                    ]
                    result_identity = {
                        **identity,
                        "frame_storyboard_ids": frame_storyboard_ids,
                    }
                else:
                    asset_kind = command.kind.removeprefix("create-")
                    asset_result = uow.stage_create_continuous_asset(
                        actor,
                        series_id,
                        chapter_id,
                        asset_kind,
                        command.input,
                        command.source_context,
                        _utcnow(),
                    )
                    result = jsonable_encoder(asset_result)
                    result_identity = {
                        **identity,
                        "asset_id": str(asset_result["id"]),
                    }
                uow.verify_continuous_operation_lock(
                    actor,
                    series_id,
                    chapter_id,
                    guard,
                    _utcnow(),
                )
        except (SeriesDataBadRequest, SeriesDataConflict) as error:
            record = receipt_record(
                owner_id=actor.user_id,
                operation_id=operation_id,
                editor_session_id=editor_session_id,
                series_id=series_id,
                chapter_id=chapter_id,
                command_kind=command_kind,
                request_body_sha256=request_body_sha256,
                source_snapshot_digest=source_digest,
                target_draft_id=target_draft_id,
                target_manifest_digest=manifest_digest,
                outcome="rejected",
                http_status=error.status_code,
                result_identity=identity,
                canonical_response={"detail": error.detail},
                committed_at=_utcnow(),
            )
            uow.insert_continuous_operation_receipt(record)
            uow.commit()
            return _execution_from_receipt(record)

        record = receipt_record(
            owner_id=actor.user_id,
            operation_id=operation_id,
            editor_session_id=editor_session_id,
            series_id=series_id,
            chapter_id=chapter_id,
            command_kind=command_kind,
            request_body_sha256=request_body_sha256,
            source_snapshot_digest=source_digest,
            target_draft_id=target_draft_id,
            target_manifest_digest=manifest_digest,
            outcome="committed",
            http_status=200,
            result_identity=result_identity,
            canonical_response=result,
            committed_at=_utcnow(),
        )
        uow.insert_continuous_operation_receipt(record)
        uow.commit()
        return _execution_from_receipt(record)
    except Exception as error:
        uow.rollback()
        if uow.is_integrity_error(error):
            uow.close()
            uow = None
            try:
                recovered = get_continuous_chapter_operation(
                    factory,
                    actor,
                    chapter_id,
                    operation_id,
                )["receipt"]
            except SeriesDataNotFound:
                raise error
            if not _same_identity(
                recovered,
                operation_id=operation_id,
                chapter_id=chapter_id,
                series_id=series_id,
                editor_session_id=editor_session_id,
                command_kind=command_kind,
                body_digest=request_body_sha256,
                source_digest=source_digest,
                target_draft_id=target_draft_id,
                manifest_digest=manifest_digest,
            ):
                raise SeriesDataConflict("原操作编号已绑定其他请求") from error
            return _execution_from_payload(recovered)
        raise
    finally:
        if uow is not None:
            uow.close()


def get_continuous_chapter_operation(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    operation_id: str,
) -> dict[str, Any]:
    operation_id = _operation_uuid(operation_id, "operation_id")
    uow = _open(factory)
    try:
        if not _schema_ready(uow):
            raise SeriesDataUnavailable("连续编辑原操作回执表尚未就绪")
        row = uow.get_continuous_operation_receipt(actor.user_id, operation_id, for_update=False)
        if row is None or row["chapter_id"] != chapter_id:
            raise SeriesDataNotFound("原操作不存在")
        uow.require_access(actor, str(row["series_id"]))
        return {"receipt": _receipt_payload(row)}
    finally:
        uow.close()
