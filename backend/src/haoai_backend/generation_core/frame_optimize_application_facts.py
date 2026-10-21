"""Pure validation and reconstruction helpers for frame.optimize applications."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    VerifiedFrameOptimizeExecutionBatch,
    VerifiedFrameOptimizeExecutionItem,
)
from haoai_backend.task_observation.generation import target_scope_for_snapshot


@dataclass(frozen=True, slots=True)
class AppliedFrameOptimizeFact:
    """One application whose source and durable result are both authenticated."""

    item: VerifiedFrameOptimizeExecutionItem
    application: Mapping[str, object]
    optimized_text: str
    before_frame: Mapping[str, object]
    after_frame: Mapping[str, object]


def parse_partial_results(
    raw: object,
    items_by_index: Mapping[int, VerifiedFrameOptimizeExecutionItem],
) -> tuple[tuple[int, str], ...] | None:
    """Parse the historical `{index, optimized_text}` partial-result shape."""

    if raw is None:
        return ()
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, list) or not value:
        return None
    results: list[tuple[int, str]] = []
    seen: set[int] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"index", "optimized_text"}:
            return None
        index = entry.get("index")
        text = entry.get("optimized_text")
        if (
            type(index) is not int
            or index not in items_by_index
            or index in seen
            or not isinstance(text, str)
            or not text.strip()
            or "\x00" in text
        ):
            return None
        results.append((index, text))
        seen.add(index)
    return tuple(results)


def apply_optimized_text(
    frame: Mapping[str, object], requested_text: str, optimized_text: str
) -> dict[str, object] | None:
    """Return the legacy-compatible frame copy after a truthful optimize write."""

    if not isinstance(frame, Mapping) or not isinstance(frame.get("text"), str):
        return None
    result = _plain(frame)
    if not isinstance(result, dict):
        return None
    # Keep the old worker's truthiness rule: a truthy request fills only a
    # falsey original_text value; whitespace is intentionally truthy here.
    if requested_text and not result.get("original_text"):
        result["original_text"] = requested_text
    result["text"] = optimized_text
    return result


def reconstruct_expected_chapter(
    batch: VerifiedFrameOptimizeExecutionBatch,
    applied_text_by_index: Mapping[int, str],
) -> list[object] | None:
    """Rebuild the current chapter from its authenticated baseline and Apps."""

    if not batch.items:
        return None
    baseline = batch.items[0].database_chapter
    if any(item.database_chapter != baseline for item in batch.items):
        return None
    frames = _plain(baseline)
    if not isinstance(frames, list):
        return None
    item_by_index = {item.frame_index: item for item in batch.items}
    if len(item_by_index) != len(batch.items):
        return None
    for index, text in applied_text_by_index.items():
        item = item_by_index.get(index)
        if item is None or type(index) is not int or not isinstance(text, str):
            return None
        if not 0 <= index < len(frames) or not isinstance(frames[index], Mapping):
            return None
        updated = apply_optimized_text(frames[index], item.requested_text, text)
        if updated is None:
            return None
        frames[index] = updated
    return frames


def application_fact(
    application: Mapping[str, object],
    item: VerifiedFrameOptimizeExecutionItem,
    *,
    owner_id: str,
    task_id: str,
    result_ref: str,
    result_digest: str,
    optimized_text: str,
) -> AppliedFrameOptimizeFact | None:
    """Validate the immutable identity and exact target digests of one App."""

    if not item.database_frame or item.frame_index < 0:
        return None
    before = _plain(item.database_frame)
    if not isinstance(before, dict):
        return None
    after = apply_optimized_text(before, item.requested_text, optimized_text)
    if after is None:
        return None
    try:
        target_scope = target_scope_for_snapshot(item.snapshot.target_snapshot)
    except (TypeError, ValueError):
        return None
    stable_storyboard_asset_id = item.snapshot.target_snapshot.stable_storyboard_asset_id
    expected_ref = _application_ref(result_ref, target_scope)
    if not all(
        (
            application.get("application_ref") == expected_ref,
            application.get("result_ref") == result_ref,
            application.get("owner_id") == owner_id,
            application.get("task_id") == task_id,
            application.get("unit_id") == item.unit_id,
            application.get("target_scope") == target_scope,
            application.get("result_digest") == result_digest,
            application.get("delivery_status") == "applied",
            application.get("before_target_digest") == canonical_digest(before),
            application.get("after_target_digest") == canonical_digest(after),
            application.get("committed_at") is not None,
            _media_revision_pair_matches(
                application.get("media_revision_before"),
                application.get("media_revision_after"),
                has_stable_storyboard_id=stable_storyboard_asset_id is not None,
            ),
            _revision_pair_matches(
                application.get("notes_revision_before"),
                application.get("notes_revision_after"),
            ),
        )
    ):
        return None
    return AppliedFrameOptimizeFact(item, dict(application), optimized_text, before, after)


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _application_ref(result_ref: str, target_scope: str) -> str:
    from haoai_backend.task_observation.generation import application_ref_for

    return application_ref_for(result_ref, target_scope)


def _revision_pair_matches(before: object, after: object) -> bool:
    return type(before) is int and type(after) is int and before >= 0 and after >= before


def _media_revision_pair_matches(
    before: object, after: object, *, has_stable_storyboard_id: bool
) -> bool:
    if before is None or after is None:
        if before is None and after is None:
            return not has_stable_storyboard_id
        if has_stable_storyboard_id and before is None:
            return type(after) is int and after == 1
        return False
    return type(before) is int and type(after) is int and before > 0 and after >= before


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


__all__ = [
    "AppliedFrameOptimizeFact",
    "application_fact",
    "apply_optimized_text",
    "canonical_digest",
    "parse_partial_results",
    "reconstruct_expected_chapter",
]
