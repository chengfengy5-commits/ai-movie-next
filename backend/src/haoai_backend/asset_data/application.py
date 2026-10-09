"""Use cases for the fifteen legacy asset-data methods."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.shared.identity import TrustedActor

from .domain import ASSET_NOT_FOUND_DETAILS, AssetKind
from .errors import (
    AssetDataForbidden,
    AssetDataNotFound,
    AssetDataUnavailable,
    AssetDataWriteConflict,
)
from .naming import dump_aliases, make_key
from .ports import AssetDataUnitOfWork, AssetDataUnitOfWorkFactory
from .presentation import asset_response, storyboard_asset_response
from .references import remove_asset_references
from .schemas import (
    CharacterCreate,
    CharacterUpdate,
    PropCreate,
    PropUpdate,
    SceneCreate,
    SceneUpdate,
    StoryboardAssetCreate,
    StoryboardAssetUpdate,
)


def _load_committed_asset(
    uow: AssetDataUnitOfWork,
    kind: AssetKind,
    asset_id: str,
) -> Mapping[str, Any]:
    row = uow.load_asset(kind, asset_id)
    if row is None:
        raise RuntimeError(f"{kind} asset disappeared after its write committed")
    return row


def list_assets(
    factory: AssetDataUnitOfWorkFactory | None,
    actor: TrustedActor,
    kind: AssetKind,
    series_id: str,
) -> list[dict[str, Any]]:
    uow = _open(factory)
    try:
        _require_access(uow, actor, series_id)
        return [asset_response(kind, row) for row in uow.list_assets(kind, series_id)]
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def create_asset(
    factory: AssetDataUnitOfWorkFactory | None,
    actor: TrustedActor,
    kind: AssetKind,
    series_id: str,
    request: CharacterCreate | SceneCreate | PropCreate,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        _require_access(uow, actor, series_id)
        values = request.model_dump()
        values["series_id"] = series_id
        row = uow.create_asset(kind, values)
        uow.commit()
        committed = _load_committed_asset(uow, kind, str(row["id"]))
        return asset_response(kind, committed)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def update_asset(
    factory: AssetDataUnitOfWorkFactory | None,
    actor: TrustedActor,
    kind: AssetKind,
    asset_id: str,
    request: CharacterUpdate | SceneUpdate | PropUpdate,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        current = uow.load_asset(kind, asset_id)
        if current is None:
            raise AssetDataNotFound(ASSET_NOT_FOUND_DETAILS[kind])
        _require_access(uow, actor, str(current["series_id"]))

        provided = request.model_dump(exclude_unset=True)
        requested_values: dict[str, Any] = {}
        for field, value in provided.items():
            if field == "audio_url":
                requested_values[field] = value
            elif value is not None:
                requested_values[field] = dump_aliases(value) if field == "aliases" else value

        # The first loaded row defines the legacy ORM assignment history. Keep
        # that write set stable across the adapter's later existence read.
        values = {
            field: value
            for field, value in requested_values.items()
            if value != current.get(field)
        }
        if kind in {"character", "scene", "prop"} and requested_values:
            name_field = "title" if kind == "scene" else "name"
            desired_name = requested_values.get(name_field, current.get(name_field))
            canonical_key = make_key(kind, desired_name)
            if canonical_key != current.get("canonical_key"):
                values["canonical_key"] = canonical_key

            desired_aliases = requested_values.get("aliases", current.get("aliases"))
            if not desired_aliases:
                empty_aliases = dump_aliases([])
                if empty_aliases != current.get("aliases"):
                    values["aliases"] = empty_aliases

        updated = uow.update_asset(
            kind,
            asset_id,
            values,
            assigned_fields=frozenset(values),
        )
        if updated is None:
            raise AssetDataWriteConflict("素材在更新前已被删除")
        uow.commit()
        committed = _load_committed_asset(uow, kind, asset_id)
        return asset_response(kind, committed)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def delete_asset(
    factory: AssetDataUnitOfWorkFactory | None,
    actor: TrustedActor,
    kind: AssetKind,
    asset_id: str,
) -> None:
    uow = _open(factory)
    try:
        current = uow.load_asset(kind, asset_id)
        if current is None:
            raise AssetDataNotFound(ASSET_NOT_FOUND_DETAILS[kind])
        series_id = str(current["series_id"])
        _require_access(uow, actor, series_id)

        uow.delete_asset(kind, asset_id)
        candidates = uow.list_chapters(series_id)
        affected_ids = [
            str(chapter["id"])
            for chapter in candidates
            if remove_asset_references(
                chapter["content"],
                kind,
                asset_id,
            ).changed
        ]
        locked_chapters = uow.lock_chapters(affected_ids)
        expected_locked_ids = set(affected_ids)
        actual_locked_ids = {str(chapter["id"]) for chapter in locked_chapters}
        if actual_locked_ids != expected_locked_ids:
            raise RuntimeError(
                "a chapter changed while media reconciliation was acquiring locks"
            )

        replacements: list[tuple[Mapping[str, Any], str]] = []
        for chapter in locked_chapters:
            removal = remove_asset_references(chapter["content"], kind, asset_id)
            if not removal.changed:
                continue
            content = str(removal.content)
            uow.reconcile_chapter_media(
                chapter,
                actor,
                content_override=content,
            )
            replacements.append((chapter, content))

        for chapter, content in replacements:
            updated = uow.update_chapter_content(
                str(chapter["id"]),
                chapter["content"],
                content,
                updated_at=uow.current_time(),
            )
            if not updated:
                raise AssetDataWriteConflict("章节内容在素材引用清理期间已变化")

        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def create_storyboard_asset(
    factory: AssetDataUnitOfWorkFactory | None,
    actor: TrustedActor,
    request: StoryboardAssetCreate,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        chapter = uow.load_chapter(request.chapter_id)
        if chapter is None:
            raise AssetDataNotFound("章节不存在")
        _require_access(uow, actor, str(chapter["series_id"]))

        locked_chapter = uow.load_chapter(request.chapter_id, lock=True)
        if locked_chapter is None:
            raise AssetDataNotFound("章节不存在")

        values = {
            "id": uow.new_id(),
            "series_id": str(locked_chapter["series_id"]),
            "chapter_id": request.chapter_id,
            "frame_index": request.frame_index,
            "name": request.name,
            "description": request.description,
            "image_url": request.image_url,
        }
        uow.reconcile_chapter_media(
            locked_chapter,
            actor,
            new_assets=(values,),
        )
        row = uow.create_asset("storyboard", values)
        uow.refresh_chapter_lock(request.chapter_id, actor.user_id)
        uow.commit()
        committed = _load_committed_asset(uow, "storyboard", str(row["id"]))
        return storyboard_asset_response(committed)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def update_storyboard_asset(
    factory: AssetDataUnitOfWorkFactory | None,
    actor: TrustedActor,
    asset_id: str,
    request: StoryboardAssetUpdate,
) -> dict[str, Any]:
    uow = _open(factory)
    try:
        current = uow.load_asset("storyboard", asset_id)
        if current is None:
            raise AssetDataNotFound("故事板资产不存在")
        _require_access(uow, actor, str(current["series_id"]))

        provided = request.model_dump(exclude_unset=True)
        values = {
            field: value
            for field, value in provided.items()
            if value is not None and value != current.get(field)
        }
        image_assignment_changed = "image_url" in values
        assigned_fields = frozenset(values)
        expected_storyboard_facts: tuple[str, object] | None = None
        if image_assignment_changed:
            persisted = uow.load_asset("storyboard", asset_id)
            if persisted is None:
                raise RuntimeError(
                    f"storyboard asset {asset_id!r} disappeared before media reconciliation"
                )
            persisted_chapter_id = persisted["chapter_id"]
            if not isinstance(persisted_chapter_id, str):
                raise RuntimeError(
                    f"storyboard asset {asset_id!r} has no valid chapter identity"
                )
            persisted_facts = (persisted_chapter_id, persisted["image_url"])
            chapter = uow.load_chapter(persisted_chapter_id, lock=True)
            if chapter is None:
                raise RuntimeError(
                    "a chapter changed while media reconciliation was acquiring locks"
                )
            locked_asset = uow.load_asset("storyboard", asset_id, lock=True)
            if locked_asset is None:
                raise RuntimeError(
                    f"storyboard asset {asset_id!r} disappeared before media reconciliation"
                )
            locked_facts = (locked_asset["chapter_id"], locked_asset["image_url"])
            if locked_facts != persisted_facts:
                raise RuntimeError(
                    "a storyboard asset changed while media locks were acquired"
                )
            next_image_url = values["image_url"]
            uow.reconcile_chapter_media(
                chapter,
                actor,
                asset_image_overrides={asset_id: next_image_url},
            )
            expected_storyboard_facts = persisted_facts

        updated = uow.update_asset(
            "storyboard",
            asset_id,
            values,
            assigned_fields=assigned_fields,
            expected_storyboard_facts=expected_storyboard_facts,
        )
        if updated is None:
            raise AssetDataWriteConflict("故事板资产在更新前已被删除")
        uow.refresh_chapter_lock(str(current["chapter_id"]), actor.user_id)
        uow.commit()
        committed = _load_committed_asset(uow, "storyboard", asset_id)
        return storyboard_asset_response(committed)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def delete_storyboard_asset(
    factory: AssetDataUnitOfWorkFactory | None,
    actor: TrustedActor,
    asset_id: str,
) -> None:
    uow = _open(factory)
    try:
        current = uow.load_asset("storyboard", asset_id)
        if current is None:
            raise AssetDataNotFound("故事板资产不存在")
        _require_access(uow, actor, str(current["series_id"]))

        chapter_id = str(current["chapter_id"])
        chapter = uow.load_chapter(chapter_id, lock=True)
        if chapter is None:
            raise AssetDataNotFound("章节不存在")
        uow.reconcile_chapter_media(
            chapter,
            actor,
            deleted_asset_ids={asset_id},
        )
        uow.delete_asset("storyboard", asset_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _require_access(
    uow: AssetDataUnitOfWork,
    actor: TrustedActor,
    series_id: str,
) -> None:
    try:
        require_series_access(
            uow,
            ActorIdentity(user_id=actor.user_id),
            series_id,
        )
    except SeriesNotFound as exc:
        raise AssetDataNotFound("剧集不存在") from exc
    except SeriesAccessDenied as exc:
        raise AssetDataForbidden(exc.detail) from exc


def _open(
    factory: AssetDataUnitOfWorkFactory | None,
) -> AssetDataUnitOfWork:
    if factory is None:
        raise AssetDataUnavailable()
    uow = factory()
    try:
        uow.ensure_clean()
    except Exception:
        try:
            uow.close()
        except Exception:
            pass
        raise
    return uow
