"""Same-Session application of canonical completed Canvas image results."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.canvas_sources.persistence import SqlAlchemyCanvasSourceUnitOfWork
from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.records import ApplicationRecord, GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    ApplicationOutcome,
    CanvasApplyAuthority,
    GenerationActor,
    GenerationApplicationCommand,
    TargetSnapshot,
)
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data.tables import chapter_locks, chapters, series, system_configs
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation import (
    GenerationResultApplicationFacade,
    application_key_for,
    application_ref_for,
    current_target_application,
    expected_target_digest,
    find_application_record,
    target_matches_receipt,
    target_scope_for_snapshot,
)
from haoai_backend.task_observation.errors import (
    TaskObservationBadRequest,
    TaskObservationConflict,
    TaskObservationForbidden,
    TaskObservationIntegrityError,
    TaskObservationNotFound,
    TaskObservationUnavailable,
)
from .schemas import GenerationApplicationResponse

SessionFactory = Callable[[], Session]
AssetType = Literal["character", "scene", "prop"]


def apply_chapter_generation(
    session_factory: SessionFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    command: GenerationApplicationCommand,
) -> dict[str, object]:
    """Apply a preview only when its canonical snapshot names this chapter."""
    return _apply_generation(
        session_factory,
        actor,
        command,
        path_kind="chapter",
        path_id=chapter_id,
    )


def apply_asset_generation(
    session_factory: SessionFactory | None,
    actor: TrustedActor,
    asset_type: AssetType,
    asset_id: str,
    command: GenerationApplicationCommand,
) -> dict[str, object]:
    """Apply an image only to the canonical asset named by this PUT route."""
    if asset_type not in {"character", "scene", "prop"}:
        raise TaskObservationBadRequest("素材类型无效")
    return _apply_generation(
        session_factory,
        actor,
        command,
        path_kind=asset_type,
        path_id=asset_id,
    )


def _apply_generation(
    session_factory: SessionFactory | None,
    actor: TrustedActor,
    command: GenerationApplicationCommand,
    *,
    path_kind: Literal["chapter", "character", "scene", "prop"],
    path_id: str,
) -> dict[str, object]:
    if session_factory is None:
        raise TaskObservationUnavailable("画布生成应用服务尚未接线")
    if not isinstance(actor, TrustedActor) or not actor.user_id:
        raise TaskObservationForbidden("可信身份不可用")
    target = command.expected_target
    _validate_path_binding(path_kind, path_id, target)

    try:
        expected_digest = expected_target_digest(target)
        target_scope = target_scope_for_snapshot(target)
    except ValueError as exc:
        raise TaskObservationBadRequest("原始目标快照缺少必要身份或摘要") from exc
    if command.application_key != application_key_for(command.result_ref, target_scope):
        raise TaskObservationConflict("应用键与原始结果目标不匹配")

    try:
        session = session_factory()
    except Exception as exc:
        raise TaskObservationUnavailable("画布生成应用数据库暂不可用") from exc
    if not isinstance(session, Session):
        raise TaskObservationUnavailable("画布生成应用端口未返回 SQLAlchemy Session")

    try:
        SqlAlchemyCanvasSourceUnitOfWork(session).begin_write()
        facade = GenerationResultApplicationFacade(session)
        generation_actor = GenerationActor(actor.user_id, actor)
        receipt = facade.load_owned_completed_result(
            session,
            generation_actor,
            command.task_id,
            command.result_ref,
        )
        if receipt == "not_found":
            raise TaskObservationNotFound("已完成的生成结果不存在")
        if receipt == "unready":
            raise TaskObservationConflict("生成结果尚未达到可应用状态")
        if not isinstance(receipt, GenerationResultReceipt):
            raise TaskObservationUnavailable("生成结果读取端返回了无效数据")

        if receipt.operation not in {"image.single", "image.batch"}:
            raise TaskObservationConflict("该生成结果不适用于画布图像目标")
        if not target_matches_receipt(session, receipt, target):
            raise TaskObservationConflict("目标快照与原始生成准入记录不匹配")

        _require_current_access(session, actor, target)
        _require_current_chapter(session, target)

        history = current_target_application(
            session,
            receipt,
            target,
            expected_digest,
            command.application_key,
        )
        if history is not None:
            if history.kind != "already_applied":
                raise TaskObservationConflict("原结果已绑定到不同的应用记录")
            record = _load_exact_application_record(
                session,
                receipt,
                target_scope,
                expected_digest,
                history,
            )
            payload = _application_response(command, "already_applied", record)
            session.rollback()
            return payload

        _lock_current_chapter(session, target)
        authority = CanvasApplyAuthority(
            actor=generation_actor,
            task_id=receipt.task_id,
            result_ref=receipt.result_ref,
            operation=receipt.operation,
            target=target,
            result_digest=receipt.content_digest,
        )
        outcome = facade.apply_canvas_media(session, authority, command)
        if outcome.kind == "already_applied":
            record = _load_exact_application_record(
                session,
                receipt,
                target_scope,
                expected_digest,
                outcome,
            )
            payload = _application_response(command, "already_applied", record)
            session.rollback()
            return payload
        if outcome.kind != "applied":
            _raise_application_outcome(outcome)

        _refresh_existing_team_chapter_lock(session, actor, target)

        record = _load_exact_application_record(
            session,
            receipt,
            target_scope,
            expected_digest,
            outcome,
        )
        payload = _application_response(command, "applied", record)
        try:
            session.commit()
        except Exception as exc:
            raise TaskObservationIntegrityError(
                "保存提交结果暂无法确认，请只读查询原应用记录"
            ) from exc
        return payload
    except BusinessError:
        _rollback_quietly(session)
        raise
    except GenerationError as exc:
        _rollback_quietly(session)
        if exc.code == "configuration_unavailable":
            raise TaskObservationUnavailable("画布生成应用依赖暂不可用") from exc
        raise TaskObservationConflict("生成结果或画布目标已发生变化") from exc
    except IntegrityError as exc:
        _rollback_quietly(session)
        raise TaskObservationConflict("目标或应用记录已被并发修改") from exc
    except Exception as exc:
        _rollback_quietly(session)
        raise TaskObservationUnavailable("画布生成应用暂不可用") from exc
    finally:
        try:
            session.close()
        except Exception:
            pass


def _validate_path_binding(
    path_kind: Literal["chapter", "character", "scene", "prop"],
    path_id: str,
    target: TargetSnapshot,
) -> None:
    if path_kind == "chapter":
        if target.kind != "canvas_frame_preview" or target.chapter_id != path_id:
            raise TaskObservationConflict("生成目标与章节 PUT 路径不匹配")
        return
    if (
        target.kind != "canvas_asset_image"
        or target.asset_type != path_kind
        or target.asset_id != path_id
    ):
        raise TaskObservationConflict("生成目标与素材 PUT 路径不匹配")


def _require_current_access(session: Session, actor: TrustedActor, target: TargetSnapshot) -> None:
    if not target.series_id:
        raise TaskObservationConflict("生成目标未绑定剧集")
    try:
        require_series_access(
            SqlAlchemySeriesAccessReader(session),
            ActorIdentity(actor.user_id),
            target.series_id,
        )
    except SeriesAccessDenied as exc:
        raise TaskObservationForbidden(exc.detail) from exc
    except SeriesNotFound as exc:
        raise TaskObservationNotFound("目标所属剧集不存在") from exc


def _require_current_chapter(session: Session, target: TargetSnapshot) -> None:
    if not target.chapter_id or not target.series_id:
        raise TaskObservationConflict("生成目标未绑定当前章节")
    chapter = session.execute(
        select(chapters.c.id, chapters.c.series_id).where(chapters.c.id == target.chapter_id)
    ).mappings().one_or_none()
    if chapter is None:
        raise TaskObservationNotFound("目标章节不存在")
    if chapter["series_id"] != target.series_id:
        raise TaskObservationConflict("目标章节已不属于原始剧集")


def _lock_current_chapter(session: Session, target: TargetSnapshot) -> None:
    assert target.chapter_id is not None and target.series_id is not None
    chapter = session.execute(
        select(chapters.c.id, chapters.c.series_id)
        .where(chapters.c.id == target.chapter_id)
        .with_for_update()
    ).mappings().one_or_none()
    if chapter is None:
        raise TaskObservationNotFound("目标章节不存在")
    if chapter["series_id"] != target.series_id:
        raise TaskObservationConflict("目标章节已不属于原始剧集")


def _load_exact_application_record(
    session: Session,
    receipt: GenerationResultReceipt,
    target_scope: str,
    expected_digest: str,
    outcome: ApplicationOutcome,
) -> ApplicationRecord:
    record = find_application_record(
        session,
        receipt.owner_id,
        receipt.task_id,
        receipt.result_ref,
        target_scope,
    )
    if (
        record is None
        or record.application_ref != outcome.application_ref
        or record.application_ref != application_ref_for(receipt.result_ref, target_scope)
        or record.result_ref != receipt.result_ref
        or record.owner_id != receipt.owner_id
        or record.task_id != receipt.task_id
        or record.unit_id != receipt.unit_id
        or record.target_scope != target_scope
        or record.result_digest != receipt.content_digest
        or record.delivery_status != "applied"
        or record.before_target_digest != expected_digest
        or not _is_lower_sha256(record.after_target_digest)
        or record.after_target_digest != outcome.target_revision
        or record.committed_at is None
    ):
        raise TaskObservationIntegrityError("应用记录与原始结果证明不一致")
    return record


def _application_response(
    command: GenerationApplicationCommand,
    status_value: Literal["applied", "already_applied"],
    record: ApplicationRecord,
) -> dict[str, object]:
    committed_at = record.committed_at
    if committed_at is None:
        raise TaskObservationIntegrityError("应用提交时间缺失")
    normalized = (
        committed_at.replace(tzinfo=timezone.utc)
        if committed_at.tzinfo is None
        else committed_at.astimezone(timezone.utc)
    )
    response = GenerationApplicationResponse.model_validate(
        {
            "task_id": command.task_id,
            "result_ref": command.result_ref,
            "status": status_value,
            "application": {
                "application_ref": record.application_ref,
                "target_revision": record.after_target_digest,
                "committed_at": normalized.isoformat(),
            },
        }
    )
    return response.model_dump(mode="json")


def _raise_application_outcome(outcome: ApplicationOutcome) -> None:
    if outcome.kind == "target_deleted":
        raise TaskObservationNotFound("画布目标已删除")
    if outcome.kind in {"stale", "target_conflict"}:
        raise TaskObservationConflict("画布目标或生成结果已发生变化")
    raise TaskObservationConflict("生成结果无法应用到该画布目标")


def _refresh_existing_team_chapter_lock(
    session: Session,
    actor: TrustedActor,
    target: TargetSnapshot,
) -> None:
    assert target.chapter_id is not None and target.series_id is not None
    current_series = session.execute(
        select(series.c.id, series.c.team_id).where(series.c.id == target.series_id)
    ).mappings().one_or_none()
    if current_series is None:
        raise TaskObservationNotFound("目标所属剧集不存在")
    if not current_series["team_id"]:
        return

    now = _fresh_database_time(session)
    locks = session.execute(
        select(chapter_locks).where(chapter_locks.c.chapter_id == target.chapter_id)
    ).mappings().all()
    if len(locks) != 1:
        raise TaskObservationConflict("团队章节编辑锁不存在或已重复")
    lock = locks[0]
    if lock["user_id"] != actor.user_id or lock["expires_at"] <= now:
        raise TaskObservationConflict("请先取得仍有效的当前团队章节锁")

    idle_minutes = session.execute(
        select(system_configs.c.chapter_lock_idle_minutes).limit(1)
    ).scalar_one_or_none()
    minutes = idle_minutes if idle_minutes and idle_minutes > 0 else 15
    changed = session.execute(
        update(chapter_locks)
        .where(
            chapter_locks.c.id == lock["id"],
            chapter_locks.c.chapter_id == target.chapter_id,
            chapter_locks.c.user_id == actor.user_id,
            chapter_locks.c.expires_at > _database_time_expression(session),
        )
        .values(
            last_active_at=now,
            expires_at=now + timedelta(minutes=minutes),
        )
    )
    if changed.rowcount != 1:
        raise TaskObservationConflict("团队章节锁在保存期间已变化")


def _fresh_database_time(session: Session) -> datetime:
    value = session.execute(select(_database_time_expression(session))).scalar_one()
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo is not None else value


def _database_time_expression(session: Session):
    if session.get_bind().dialect.name == "postgresql":
        return func.timezone("UTC", func.clock_timestamp())
    return func.current_timestamp()


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _rollback_quietly(session: Session) -> None:
    try:
        session.rollback()
    except Exception:
        pass
