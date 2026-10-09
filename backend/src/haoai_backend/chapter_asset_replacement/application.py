"""Transactional use case for replacing one chapter asset reference."""

from __future__ import annotations

import logging
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    ASSET_TYPES,
    ReplaceAssetCommand,
    collect_asset_ids_from_content,
    parse_chapter_frames,
    replace_asset_references,
    serialize_chapter_content,
)
from .errors import (
    ChapterAssetReplacementBadRequest,
    ChapterAssetReplacementFailed,
    ChapterAssetReplacementNotFound,
    ChapterAssetReplacementUnavailable,
)
from .ports import ChapterAssetReplacementUnitOfWork, UnitOfWorkFactory

logger = logging.getLogger(__name__)


def replace_chapter_asset(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    chapter_id: str,
    command: ReplaceAssetCommand,
) -> dict[str, str]:
    """Replace references, commit source/media work, then commit orphan cleanup."""
    uow = _open(factory)
    try:
        if command.old_asset_id == command.new_asset_id:
            raise ChapterAssetReplacementBadRequest("新旧资产不能相同")
        if command.asset_type not in ASSET_TYPES:
            raise ChapterAssetReplacementBadRequest("无效的资产类型")

        chapter = uow.load_chapter(chapter_id)
        if chapter is None:
            raise ChapterAssetReplacementNotFound("章节不存在")

        uow.require_series_access(actor, chapter.series_id)
        new_asset = uow.load_asset_for_replacement(
            command.asset_type,
            command.new_asset_id,
            chapter.series_id,
        )
        if new_asset is None:
            raise ChapterAssetReplacementNotFound("新资产不存在")

        try:
            frames = parse_chapter_frames(chapter.content)
        except ValueError as exc:
            raise ChapterAssetReplacementBadRequest("章节内容格式异常") from exc

        updated_frames, replaced_count, observed_ids = replace_asset_references(
            frames,
            old_asset_id=command.old_asset_id,
            new_asset_id=command.new_asset_id,
            asset_type=command.asset_type,
        )
        if replaced_count == 0:
            logger.warning(
                "[replace-asset] 未找到资产引用: old_id=%s, type=%s, chapter=%s, "
                "frames_count=%d, all_ids_in_frames=%r",
                command.old_asset_id,
                command.asset_type,
                chapter_id,
                len(frames),
                observed_ids,
            )
            raise ChapterAssetReplacementBadRequest("该章节中没有引用此资产")

        content = serialize_chapter_content(updated_frames)
        uow.reconcile_chapter_media(chapter, actor, content)
        if uow.update_chapter_content(chapter.id, content) != 1:
            raise ChapterAssetReplacementFailed()

        uow.commit()

        current_chapter = uow.load_chapter(chapter_id)
        if current_chapter is None:
            raise ChapterAssetReplacementFailed()

        referenced = {
            asset_type: set()
            for asset_type in ASSET_TYPES
        }
        for source_chapter in uow.list_chapters_for_series(current_chapter.series_id):
            chapter_references = collect_asset_ids_from_content(source_chapter.content)
            for asset_type in ASSET_TYPES:
                referenced[asset_type].update(chapter_references[asset_type])

        if command.old_asset_id not in referenced[command.asset_type]:
            candidates = uow.list_orphan_candidates(
                current_chapter.series_id,
                command.asset_type,
                command.old_asset_id,
            )
            for asset_id in candidates:
                if uow.delete_asset_by_id(command.asset_type, asset_id) == 0:
                    logger.warning(
                        "[replace-asset] 孤儿素材已不存在: id=%s, type=%s",
                        asset_id,
                        command.asset_type,
                    )

        uow.commit()

        refreshed_chapter = uow.load_chapter(chapter_id)
        if refreshed_chapter is None:
            raise ChapterAssetReplacementFailed()

        refreshed_asset = uow.load_asset_by_id(
            command.asset_type,
            command.new_asset_id,
        )
        if refreshed_asset is None:
            raise ChapterAssetReplacementFailed()

        display_name = refreshed_asset.display_name
        if command.asset_type in ("character", "prop") and not display_name:
            display_name = "未知"

        return {
            "message": (
                f"已替换 {replaced_count} 处分镜，使用 {display_name}"
            )
        }
    except Exception:
        try:
            uow.rollback()
        except Exception:
            pass
        raise
    finally:
        uow.close()


def _open(factory: UnitOfWorkFactory | None) -> ChapterAssetReplacementUnitOfWork:
    if factory is None:
        raise ChapterAssetReplacementUnavailable()

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
