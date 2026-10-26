"""Series exchange use cases, independent of HTTP and SQLAlchemy."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from haoai_backend.asset_data.references import remove_asset_references
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    InvalidSeriesExport,
    build_export_payload,
    build_import_plan,
)
from .errors import (
    SeriesExchangeBadRequest,
    SeriesExchangeForbidden,
    SeriesExchangeNotFound,
    SeriesExchangeUnavailable,
    SeriesExchangeWriteConflict,
)
from .ports import SeriesExchangeUnitOfWorkFactory


_Result = TypeVar("_Result")


def _run(
    factory: SeriesExchangeUnitOfWorkFactory | None,
    operation: Callable[[object], _Result],
) -> _Result:
    if factory is None:
        raise SeriesExchangeUnavailable()
    uow = factory()
    try:
        result = operation(uow)
    except BaseException:
        try:
            uow.rollback()
        except BaseException:
            pass
        try:
            uow.close()
        except BaseException:
            pass
        raise
    uow.close()
    return result


def _require_access(uow: object, actor: TrustedActor, series_id: str) -> None:
    try:
        require_series_access(uow, ActorIdentity(actor.user_id), series_id)
    except SeriesNotFound as error:
        raise SeriesExchangeNotFound("剧集不存在") from error
    except SeriesAccessDenied as error:
        raise SeriesExchangeForbidden(error.detail) from error


def list_fused_assets(
    factory: SeriesExchangeUnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
) -> list[dict[str, Any]]:
    def read(uow: object) -> list[dict[str, Any]]:
        _require_access(uow, actor, series_id)
        return [row.as_payload() for row in uow.list_fused_assets(series_id)]

    return _run(factory, read)


def update_fused_asset(
    factory: SeriesExchangeUnitOfWorkFactory | None,
    actor: TrustedActor,
    asset_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    def update(uow: object) -> dict[str, Any]:
        current = uow.load_fused_asset(asset_id)
        if current is None:
            raise SeriesExchangeNotFound("融合资产不存在")
        _require_access(uow, actor, current.series_id)

        requested: dict[str, Any] = {}
        for field in ("name", "member_type", "image_url", "size"):
            value = payload.get(field)
            if value is not None:
                requested[field] = value
        members = payload.get("members")
        if members is not None:
            requested["members"] = json.dumps(members, ensure_ascii=False)

        changed = {
            field: value
            for field, value in requested.items()
            if getattr(current, field) != value
        }
        if changed:
            uow.update_fused_asset_fields(asset_id, changed)
        uow.commit()

        refreshed = uow.load_fused_asset(asset_id)
        if refreshed is None:
            raise SeriesExchangeNotFound("融合资产不存在")
        return refreshed.as_payload()

    return _run(factory, update)


def delete_fused_asset(
    factory: SeriesExchangeUnitOfWorkFactory | None,
    actor: TrustedActor,
    asset_id: str,
) -> None:
    def remove(uow: object) -> None:
        current = uow.load_fused_asset(asset_id)
        if current is None:
            raise SeriesExchangeNotFound("融合资产不存在")
        _require_access(uow, actor, current.series_id)

        candidates = sorted(
            uow.list_chapters(current.series_id),
            key=lambda chapter: str(chapter["id"]),
        )
        affected_ids: list[str] = []
        for chapter in candidates:
            _require_readable_fused_chapter(chapter["content"])
            if remove_asset_references(
                chapter["content"],
                "fused",
                asset_id,
            ).changed:
                affected_ids.append(str(chapter["id"]))

        locked_chapters = uow.lock_chapters(affected_ids)
        locked_ids = [str(chapter["id"]) for chapter in locked_chapters]
        if len(locked_ids) != len(set(locked_ids)) or set(locked_ids) != set(affected_ids):
            raise SeriesExchangeWriteConflict("章节在融合资产引用清理期间已变化")

        replacements: list[tuple[Mapping[str, Any], str]] = []
        for chapter in sorted(locked_chapters, key=lambda row: str(row["id"])):
            _require_readable_fused_chapter(chapter["content"])
            removal = remove_asset_references(
                chapter["content"],
                "fused",
                asset_id,
            )
            if not removal.changed:
                continue
            content = str(removal.content)
            try:
                uow.reconcile_chapter_media(
                    chapter,
                    actor,
                    content_override=content,
                )
            except Exception as error:
                raise SeriesExchangeWriteConflict(
                    "章节媒体状态无法与融合资产删除同步"
                ) from error
            replacements.append((chapter, content))

        for chapter, content in replacements:
            if not uow.update_chapter_content(
                str(chapter["id"]),
                chapter["content"],
                content,
                updated_at=uow.current_time(),
            ):
                raise SeriesExchangeWriteConflict(
                    "章节内容在融合资产引用清理期间已变化"
                )

        if not uow.delete_fused_asset(asset_id):
            raise SeriesExchangeWriteConflict("融合资产在删除前已发生变化")
        uow.commit()

    _run(factory, remove)


def _require_readable_fused_chapter(content: object) -> None:
    if content is None or content == "":
        return
    frames = content
    if isinstance(content, str):
        try:
            frames = json.loads(content)
        except (json.JSONDecodeError, TypeError) as error:
            raise SeriesExchangeWriteConflict(
                "章节内容无法读取，无法安全清理融合资产引用"
            ) from error
    if not isinstance(frames, list):
        raise SeriesExchangeWriteConflict(
            "章节内容无法读取，无法安全清理融合资产引用"
        )


def export_series(
    factory: SeriesExchangeUnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
) -> dict[str, Any]:
    def export(uow: object) -> dict[str, Any]:
        _require_access(uow, actor, series_id)
        series = uow.load_series_for_export(series_id)
        if series is None:
            raise SeriesExchangeNotFound("剧集不存在")
        return build_export_payload(
            series=series,
            chapters=uow.list_export_chapters(series_id),
            characters=uow.list_export_characters(series_id),
            scenes=uow.list_export_scenes(series_id),
            props=uow.list_export_props(series_id),
            storyboard_assets=uow.list_export_storyboard_assets(series_id),
            exported_at=uow.current_time(),
        )

    return _run(factory, export)


def import_series(
    factory: SeriesExchangeUnitOfWorkFactory | None,
    actor: TrustedActor,
    *,
    data: Mapping[str, Any],
    requested_name: str | None,
    requested_description: str | None,
    team_id: str | None,
    claim: bool,
) -> dict[str, str]:
    if data.get("version") != 1:
        raise SeriesExchangeBadRequest("不支持的导出格式版本")

    def create(uow: object) -> dict[str, str]:
        if team_id:
            if not uow.team_exists(team_id):
                raise SeriesExchangeNotFound("团队不存在")
            if not uow.is_team_member(team_id, actor.user_id):
                raise SeriesExchangeForbidden("你不是该团队成员")

        try:
            plan = build_import_plan(
                data=data,
                user_id=actor.user_id,
                requested_name=requested_name,
                requested_description=requested_description,
                team_id=team_id,
                claim=claim,
                now=uow.current_time(),
                new_id=uow.new_id,
            )
        except InvalidSeriesExport as error:
            raise SeriesExchangeBadRequest(str(error)) from error

        uow.insert_import_plan(plan)
        uow.commit()
        saved = uow.refresh_imported_series(str(plan.series["id"]))
        if saved is None:
            raise RuntimeError("imported series disappeared after commit")
        return {
            "id": str(saved["id"]),
            "name": str(saved["name"]),
            "message": "导入成功",
        }

    return _run(factory, create)
