"""Use cases for the five legacy series asset-maintenance routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from haoai_backend.asset_data.naming import (
    dump_aliases,
    load_aliases,
    make_key,
    normalize_name,
)
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    ASSET_KINDS, ASSET_NAME_FIELDS, asset_frame_ref_counts,
    collect_asset_ids_from_content, find_duplicate_groups, pair_key,
    redirect_asset_references, redirect_fused_members,
)
from .errors import (
    SeriesMaintenanceBadRequest, SeriesMaintenanceForbidden,
    SeriesMaintenanceNotFound, SeriesMaintenanceUnavailable,
)
from .ports import SeriesMaintenanceUnitOfWorkFactory
from .schemas import AssetDuplicateIgnoreRequest, AssetMergeRequest, AssetRefCheckRequest

_Result = TypeVar("_Result")


def _with_unit_of_work(
    factory: SeriesMaintenanceUnitOfWorkFactory | None,
    operation: Callable[[object], _Result],
) -> _Result:
    if factory is None:
        raise SeriesMaintenanceUnavailable()
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
        raise SeriesMaintenanceNotFound("剧集不存在") from error
    except SeriesAccessDenied as error:
        raise SeriesMaintenanceForbidden(error.detail) from error


def _asset_kind(asset_type: str) -> str:
    if asset_type not in ASSET_KINDS:
        raise SeriesMaintenanceBadRequest("无效的资产类型")
    return asset_type


def _load_exclusion_pairs(
    uow: object,
    series_id: str,
    asset_type: str,
    *,
    valid_ids: set[str] | None = None,
) -> tuple[set[tuple[str, str]], int]:
    pairs: set[tuple[str, str]] = set()
    stale_ids: list[str] = []
    for row in uow.list_duplicate_exclusions(series_id, asset_type):
        if valid_ids is not None and (
            row["asset_id_a"] not in valid_ids or row["asset_id_b"] not in valid_ids
        ):
            stale_ids.append(str(row["id"]))
            continue
        pair = pair_key(row["asset_id_a"], row["asset_id_b"])
        if pair:
            pairs.add(pair)
    if stale_ids:
        uow.delete_duplicate_exclusions(stale_ids)
        uow.commit()
    return pairs, len(pairs)


def _series_asset_ids(uow: object, series_id: str, asset_type: str) -> set[str]:
    return {str(row["id"]) for row in uow.list_assets(asset_type, series_id)}


def check_orphan_assets(
    factory: SeriesMaintenanceUnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    request: AssetRefCheckRequest,
) -> dict[str, list[dict[str, str]]]:
    def check(uow: object) -> dict[str, list[dict[str, str]]]:
        _require_access(uow, actor, series_id)
        if not request.items:
            return {"orphans": []}
        referenced = {kind: set() for kind in ASSET_KINDS}
        for chapter in uow.list_chapters(series_id):
            if (
                request.exclude_chapter_id
                and str(chapter["id"]) == str(request.exclude_chapter_id)
            ):
                continue
            refs = collect_asset_ids_from_content(chapter.get("content"))
            for kind in ASSET_KINDS:
                referenced[kind].update(refs[kind])
        owned = {
            kind: _series_asset_ids(uow, series_id, kind)
            for kind in ASSET_KINDS
        }
        orphans = []
        for item in request.items:
            if item.type in ASSET_KINDS and item.id in owned[item.type]:
                if item.id not in referenced[item.type]:
                    orphans.append({"type": item.type, "id": item.id})
        return {"orphans": orphans}
    return _with_unit_of_work(factory, check)


def list_duplicate_assets(
    factory: SeriesMaintenanceUnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    asset_type: str = "scene",
    threshold: float = 0.82,
) -> dict[str, Any]:
    def list_with(uow: object) -> dict[str, Any]:
        _require_access(uow, actor, series_id)
        kind = _asset_kind(asset_type)
        rows = uow.list_assets(kind, series_id)
        counts = asset_frame_ref_counts(uow.list_chapters(series_id))
        valid_ids = {str(row["id"]) for row in rows}
        exclusions, ignored_total = _load_exclusion_pairs(
            uow, series_id, kind, valid_ids=valid_ids
        )
        groups = find_duplicate_groups(
            rows,
            ASSET_NAME_FIELDS[kind],
            threshold=max(0.5, min(0.99, threshold)),
            scene_prefix_min=4 if kind == "scene" else 0,
            excluded_pairs=exclusions,
        )
        result_groups = []
        for index, group in enumerate(groups):
            items = [
                {
                    "id": row["id"],
                    "name": row.get(ASSET_NAME_FIELDS[kind]) or "",
                    "description": (row.get("description") or "")[:120],
                    "image_url": row.get("image_url"),
                    "ref_count": counts.get((kind, row["id"]), 0),
                    "aliases": load_aliases(row.get("aliases")),
                    "canonical_key": row.get("canonical_key"),
                }
                for row in group
            ]
            items.sort(key=lambda item: -item["ref_count"])
            result_groups.append({"index": index, "items": items})
        return {
            "type": kind,
            "total": len(rows),
            "ignored_total": ignored_total,
            "groups": result_groups,
        }
    return _with_unit_of_work(factory, list_with)


def ignore_duplicate_assets(
    factory: SeriesMaintenanceUnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    request: AssetDuplicateIgnoreRequest,
) -> dict[str, Any]:
    def ignore_with(uow: object) -> dict[str, Any]:
        _require_access(uow, actor, series_id)
        kind = _asset_kind(request.type)
        ids = [item for item in dict.fromkeys(request.ids or []) if item]
        if len(ids) < 2:
            raise SeriesMaintenanceBadRequest("至少需要选择 2 条资产")
        if len(ids) > 50:
            raise SeriesMaintenanceBadRequest("单次最多处理 50 条资产")
        valid_ids = _series_asset_ids(uow, series_id, kind)
        unknown = [item for item in ids if item not in valid_ids]
        if unknown:
            raise SeriesMaintenanceBadRequest(
                f"有 {len(unknown)} 条资产不属于该剧集的该类型，已中止"
            )
        pairs = [
            pair for left in range(len(ids)) for right in range(left + 1, len(ids))
            if (pair := pair_key(ids[left], ids[right])) is not None
        ]
        if not pairs:
            raise SeriesMaintenanceBadRequest("无法生成有效的资产组合")

        if not request.ignored:
            removed = uow.delete_duplicate_pairs(series_id, kind, pairs)
            uow.commit()
            return {"ignored": False, "pairs_removed": removed, "pairs_total": len(pairs)}

        existing = {
            pair for row in uow.list_duplicate_exclusions(series_id, kind)
            if (pair := pair_key(row["asset_id_a"], row["asset_id_b"])) is not None
        }
        added = 0
        for pair in pairs:
            if pair in existing:
                continue
            uow.add_duplicate_exclusion(
                series_id=series_id,
                asset_type=kind,
                pair=pair,
                created_by=actor.user_id,
            )
            existing.add(pair)
            added += 1
        uow.commit()
        return {"ignored": True, "pairs_added": added, "pairs_total": len(pairs)}
    return _with_unit_of_work(factory, ignore_with)


def list_ignored_duplicate_pairs(
    factory: SeriesMaintenanceUnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    asset_type: str = "scene",
) -> dict[str, Any]:
    def list_with(uow: object) -> dict[str, Any]:
        _require_access(uow, actor, series_id)
        kind = _asset_kind(asset_type)
        rows = uow.list_assets(kind, series_id)
        name_field = ASSET_NAME_FIELDS[kind]
        names = {str(row["id"]): row.get(name_field) or "" for row in rows}
        images = {str(row["id"]): row.get("image_url") for row in rows}
        exclusions = uow.list_duplicate_exclusions(
            series_id, kind, newest_first=True
        )
        items, stale_ids = [], []
        for exclusion in exclusions:
            left, right = exclusion["asset_id_a"], exclusion["asset_id_b"]
            if left not in names or right not in names:
                stale_ids.append(str(exclusion["id"]))
                continue
            created_at = exclusion.get("created_at")
            items.append({
                "id": exclusion["id"],
                "asset_id_a": left,
                "asset_id_b": right,
                "name_a": names.get(left, ""),
                "name_b": names.get(right, ""),
                "image_a": images.get(left),
                "image_b": images.get(right),
                "created_at": created_at.isoformat() if created_at else None,
            })
        if stale_ids:
            uow.delete_duplicate_exclusions(stale_ids)
            uow.commit()
        return {"type": kind, "total": len(items), "items": items}
    return _with_unit_of_work(factory, list_with)


def merge_duplicate_assets(
    factory: SeriesMaintenanceUnitOfWorkFactory | None,
    actor: TrustedActor,
    series_id: str,
    request: AssetMergeRequest,
) -> dict[str, Any]:
    def merge_with(uow: object) -> dict[str, Any]:
        _require_access(uow, actor, series_id)
        kind = _asset_kind(request.type)
        name_field = ASSET_NAME_FIELDS[kind]
        merge_ids = [
            item for item in dict.fromkeys(request.merge_ids or [])
            if item and item != request.keep_id
        ]
        if not merge_ids:
            raise SeriesMaintenanceBadRequest("没有需要合并的资产")
        if len(merge_ids) > 50:
            raise SeriesMaintenanceBadRequest("单次最多合并 50 条资产")

        keep = uow.load_asset(kind, request.keep_id, series_id=series_id)
        if keep is None:
            raise SeriesMaintenanceNotFound("保留的资产不存在")
        merged_rows = uow.list_assets_by_ids(kind, series_id, merge_ids)
        if len(merged_rows) != len(merge_ids):
            raise SeriesMaintenanceBadRequest(
                "部分被合并的资产不存在或不属于该剧集"
            )

        keep_name = keep.get(name_field) or ""
        aliases = load_aliases(
            request.keep_aliases
            if request.keep_aliases is not None
            else keep.get("aliases")
        )
        alias_norms = {normalize_name(alias) for alias in aliases}
        keep_norm = normalize_name(keep_name)
        aliases_added = 0
        for row in merged_rows:
            candidates = [row.get(name_field) or "", *load_aliases(row.get("aliases"))]
            for candidate in candidates:
                normalized = normalize_name(candidate)
                if not normalized or normalized == keep_norm or normalized in alias_norms:
                    continue
                aliases.append(candidate)
                alias_norms.add(normalized)
                aliases_added += 1

        changes: dict[str, Any] = {
            "canonical_key": make_key(kind, keep_name),
        }
        encoded_aliases = dump_aliases(aliases)
        if encoded_aliases != keep.get("aliases"):
            changes["aliases"] = encoded_aliases
        keep_image = keep.get("image_url")
        dropped_images = []
        for row in merged_rows:
            image = row.get("image_url")
            if not image:
                continue
            if keep_image:
                dropped_images.append({"id": row["id"], "name": row.get(name_field) or ""})
            else:
                keep_image = image
                changes["image_url"] = image
        if changes:
            uow.update_asset_fields(kind, request.keep_id, changes)

        old_ids = set(merge_ids)
        redirected_frames = 0
        for chapter in uow.list_chapters(series_id):
            content, affected = redirect_asset_references(
                chapter.get("content"), kind, old_ids, request.keep_id
            )
            if affected:
                uow.update_chapter_content(str(chapter["id"]), str(content))
                redirected_frames += affected

        fused_updated = 0
        for fused in uow.list_fused_assets(series_id, kind):
            members, changed = redirect_fused_members(
                fused.get("members"), old_ids, request.keep_id, keep_name
            )
            if changed:
                uow.update_fused_members(str(fused["id"]), members)
                fused_updated += 1

        for row in merged_rows:
            uow.delete_asset(kind, str(row["id"]))
        uow.commit()

        committed_keep = uow.load_asset(kind, request.keep_id, series_id=series_id)
        if committed_keep is None:
            raise RuntimeError("保留的资产在合并提交后消失")
        _load_exclusion_pairs(
            uow,
            series_id,
            kind,
            valid_ids=_series_asset_ids(uow, series_id, kind),
        )
        return {
            "message": f"已合并 {len(merged_rows)} 条资产到「{keep_name}」",
            "keep_id": request.keep_id,
            "merged_count": len(merged_rows),
            "redirected_frames": redirected_frames,
            "fused_updated": fused_updated,
            "aliases_added": aliases_added,
            "dropped_images": dropped_images,
            "aliases": load_aliases(committed_keep.get("aliases")),
        }
    return _with_unit_of_work(factory, merge_with)
