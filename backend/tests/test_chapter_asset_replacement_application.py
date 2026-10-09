from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from haoai_backend.chapter_asset_replacement.application import replace_chapter_asset
from haoai_backend.chapter_asset_replacement.domain import (
    ChapterAsset,
    ChapterRecord,
    ReplaceAssetCommand,
)
from haoai_backend.chapter_asset_replacement.errors import (
    ChapterAssetReplacementBadRequest,
    ChapterAssetReplacementFailed,
    ChapterAssetReplacementNotFound,
    ChapterAssetReplacementUnavailable,
)
from haoai_backend.shared.identity import TrustedActor


ACTOR = TrustedActor("user-a")
NOW = datetime(2026, 10, 9, 12, 0, 0)
COMMAND = ReplaceAssetCommand("old", "new", "character")


def chapter(series_id: str = "series-a", content: object | None = None) -> ChapterRecord:
    if content is None:
        content = json.dumps([{"character": ["old"]}])
    return ChapterRecord("chapter-a", series_id, "第一章", content, 1, NOW, NOW)


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.chapter_reads: list[ChapterRecord | None] = [chapter(), chapter(), chapter()]
        self.asset_for_replacement: ChapterAsset | None = ChapterAsset("new", "series-a", "新角色")
        self.asset_by_id: ChapterAsset | None = ChapterAsset("new", "series-b", "新角色")
        self.series_chapters: list[ChapterRecord] = [chapter("series-b")]
        self.orphan_candidates: list[str] = ["old"]
        self.delete_results: dict[str, int] = {"old": 1}
        self.update_result = 1
        self.access_error: Exception | None = None
        self.reconcile_error: Exception | None = None
        self.list_error: Exception | None = None
        self.commit_error_at: int | None = None
        self.close_calls = 0
        self.rollback_calls = 0
        self.commits = 0
        self.delete_calls: list[tuple[str, str]] = []
        self.asset_pk_queries: list[tuple[str, str]] = []
        self.reconcile_content: str | None = None

    def ensure_clean(self) -> None:
        self.events.append(("ensure_clean",))

    def load_chapter(self, chapter_id: str, *, lock: bool = False) -> ChapterRecord | None:
        self.events.append(("load_chapter", chapter_id, lock))
        if self.chapter_reads:
            return self.chapter_reads.pop(0)
        return None

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        self.events.append(("require_series_access", actor.user_id, series_id))
        if self.access_error:
            raise self.access_error

    def load_asset_for_replacement(
        self, asset_type: str, asset_id: str, series_id: str
    ) -> ChapterAsset | None:
        self.events.append(("load_asset_for_replacement", asset_type, asset_id, series_id))
        return self.asset_for_replacement

    def reconcile_chapter_media(
        self, loaded_chapter: ChapterRecord, actor: TrustedActor, content_override: str
    ) -> None:
        self.events.append(("reconcile_chapter_media", loaded_chapter.id, actor.user_id))
        self.reconcile_content = content_override
        if self.reconcile_error:
            raise self.reconcile_error

    def update_chapter_content(self, chapter_id: str, content: str) -> int:
        self.events.append(("update_chapter_content", chapter_id, content))
        return self.update_result

    def list_chapters_for_series(self, series_id: str) -> list[ChapterRecord]:
        self.events.append(("list_chapters_for_series", series_id))
        if self.list_error:
            raise self.list_error
        return self.series_chapters

    def list_orphan_candidates(
        self, series_id: str, asset_type: str, asset_id: str
    ) -> list[str]:
        self.events.append(("list_orphan_candidates", series_id, asset_type, asset_id))
        return self.orphan_candidates

    def delete_asset_by_id(self, asset_type: str, asset_id: str) -> int:
        self.events.append(("delete_asset_by_id", asset_type, asset_id))
        self.delete_calls.append((asset_type, asset_id))
        return self.delete_results.get(asset_id, 0)

    def load_asset_by_id(self, asset_type: str, asset_id: str) -> ChapterAsset | None:
        self.events.append(("load_asset_by_id", asset_type, asset_id))
        self.asset_pk_queries.append((asset_type, asset_id))
        return self.asset_by_id

    def commit(self) -> None:
        self.commits += 1
        self.events.append(("commit", self.commits))
        if self.commit_error_at == self.commits:
            raise RuntimeError("commit acknowledgement lost")

    def rollback(self) -> None:
        self.rollback_calls += 1
        self.events.append(("rollback",))

    def close(self) -> None:
        self.close_calls += 1
        self.events.append(("close",))


def use(uow: FakeUnitOfWork, command: ReplaceAssetCommand = COMMAND) -> dict[str, str]:
    return replace_chapter_asset(lambda: uow, ACTOR, "chapter-a", command)


@pytest.mark.parametrize(
    ("command", "chapter_reads", "expected_error", "expected_access"),
    [
        (
            ReplaceAssetCommand("same", "same", "character"),
            [chapter()],
            ChapterAssetReplacementBadRequest,
            False,
        ),
        (
            ReplaceAssetCommand("old", "new", "unknown"),
            [chapter()],
            ChapterAssetReplacementBadRequest,
            False,
        ),
        (COMMAND, [None], ChapterAssetReplacementNotFound, False),
    ],
)
def test_validation_precedence_stops_before_later_ports(
    command: ReplaceAssetCommand,
    chapter_reads: list[ChapterRecord | None],
    expected_error: type[Exception],
    expected_access: bool,
) -> None:
    uow = FakeUnitOfWork()
    uow.chapter_reads = chapter_reads

    with pytest.raises(expected_error):
        use(uow, command)

    assert any(event[0] == "require_series_access" for event in uow.events) is expected_access
    assert not any(event[0] == "load_asset_for_replacement" for event in uow.events)
    assert uow.commits == 0
    assert uow.rollback_calls == 1
    assert uow.close_calls == 1


def test_access_denial_precedes_asset_lookup_and_media_mutation() -> None:
    uow = FakeUnitOfWork()
    uow.access_error = ChapterAssetReplacementNotFound("series not available")

    with pytest.raises(ChapterAssetReplacementNotFound):
        use(uow)

    assert uow.events[:3] == [
        ("ensure_clean",),
        ("load_chapter", "chapter-a", False),
        ("require_series_access", "user-a", "series-a"),
    ]
    assert uow.events[3] == ("rollback",)
    assert not any(
        event[0] in {"load_asset_for_replacement", "reconcile_chapter_media"}
        for event in uow.events
    )


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [
        ('{"not": "a list"}', ChapterAssetReplacementBadRequest),
        ('[{"character": ["other"]}]', ChapterAssetReplacementBadRequest),
    ],
)
def test_bad_content_or_absent_reference_is_rejected_before_media_write(
    content: object, expected_error: type[Exception]
) -> None:
    uow = FakeUnitOfWork()
    uow.chapter_reads[0] = chapter(content=content)

    with pytest.raises(expected_error):
        use(uow)

    assert not any(
        event[0] in {"reconcile_chapter_media", "update_chapter_content"}
        for event in uow.events
    )
    assert uow.commits == 0
    assert uow.close_calls == 1


def test_missing_new_asset_is_not_found_before_parsing_or_reconciliation() -> None:
    uow = FakeUnitOfWork()
    uow.asset_for_replacement = None
    uow.chapter_reads[0] = chapter(content="not-json")

    with pytest.raises(ChapterAssetReplacementNotFound):
        use(uow)

    assert not any(event[0] == "reconcile_chapter_media" for event in uow.events)
    assert uow.commits == 0


def test_media_reconciliation_precedes_source_update_and_two_commits() -> None:
    uow = FakeUnitOfWork()
    uow.chapter_reads[1] = chapter(series_id="series-b")
    uow.chapter_reads[2] = chapter(series_id="series-b")
    uow.series_chapters = [chapter("series-b", '[{"character": ["elsewhere"]}]')]

    assert use(uow) == {"message": "已替换 1 处分镜，使用 新角色"}

    event_names = [event[0] for event in uow.events]
    assert event_names.index("reconcile_chapter_media") < event_names.index(
        "update_chapter_content"
    )
    assert event_names.index("update_chapter_content") < event_names.index("commit")
    assert event_names.count("commit") == 2
    assert ("list_chapters_for_series", "series-b") in uow.events
    assert ("list_orphan_candidates", "series-b", "character", "old") in uow.events
    assert uow.delete_calls == [("character", "old")]
    assert uow.asset_pk_queries == [("character", "new")]
    assert uow.rollback_calls == 0
    assert uow.close_calls == 1


@pytest.mark.parametrize(
    ("asset_type", "old_id"),
    [("character", "old-c"), ("scene", "old-s"), ("prop", "old-p")],
)
def test_current_series_references_in_any_asset_category_prevent_orphan_query(
    asset_type: str, old_id: str
) -> None:
    uow = FakeUnitOfWork()
    uow.chapter_reads[0] = chapter(content=json.dumps([{asset_type: [old_id]}]))
    uow.chapter_reads[1] = chapter(series_id="series-current")
    uow.chapter_reads[2] = chapter(series_id="series-current")
    uow.series_chapters = [chapter("series-current", json.dumps([{asset_type: [old_id]}]))]
    command = ReplaceAssetCommand(old_id, "new", asset_type)

    use(uow, command)

    assert ("list_chapters_for_series", "series-current") in uow.events
    assert not any(event[0] == "list_orphan_candidates" for event in uow.events)
    assert uow.commits == 2


def test_missing_orphan_candidate_and_zero_row_delete_still_commit_phase_two() -> None:
    uow = FakeUnitOfWork()
    uow.series_chapters = [chapter("series-b", '[{"character": ["still-used"]}]')]
    uow.orphan_candidates = ["old"]
    uow.delete_results["old"] = 0

    assert use(uow)["message"] == "已替换 1 处分镜，使用 新角色"

    assert uow.commits == 2
    assert uow.delete_calls == [("character", "old")]
    assert uow.events[-3:] == [
        ("load_chapter", "chapter-a", False),
        ("load_asset_by_id", "character", "new"),
        ("close",),
    ]


def test_phase_two_truthy_unhashable_failure_leaves_phase_one_durable() -> None:
    uow = FakeUnitOfWork()
    uow.series_chapters = [chapter(content='[{"character": [["not", "hashable"]]}]')]

    with pytest.raises(TypeError):
        use(uow)

    assert uow.commits == 1
    assert not any(event[0] == "list_orphan_candidates" for event in uow.events)
    assert uow.rollback_calls == 1
    assert uow.close_calls == 1


def test_missing_chapter_at_phase_two_entry_fails_after_first_commit() -> None:
    uow = FakeUnitOfWork()
    uow.chapter_reads[1] = None

    with pytest.raises(ChapterAssetReplacementFailed) as failure:
        use(uow)

    assert failure.value.status_code == 500
    assert uow.commits == 1
    assert not any(event[0] == "list_chapters_for_series" for event in uow.events)
    assert not uow.asset_pk_queries
    assert uow.rollback_calls == 1


def test_missing_chapter_after_second_commit_short_circuits_asset_readback() -> None:
    uow = FakeUnitOfWork()
    uow.chapter_reads[2] = None

    with pytest.raises(ChapterAssetReplacementFailed) as failure:
        use(uow)

    assert failure.value.status_code == 500
    assert uow.commits == 2
    assert not uow.asset_pk_queries
    assert uow.rollback_calls == 1
    assert uow.close_calls == 1


@pytest.mark.parametrize(
    ("asset_type", "display_name", "expected"),
    [
        ("character", "", "未知"),
        ("prop", None, "未知"),
        ("scene", "", ""),
        ("scene", None, "None"),
    ],
)
def test_readback_display_name_preserves_category_specific_legacy_fallback(
    asset_type: str, display_name: str | None, expected: str
) -> None:
    uow = FakeUnitOfWork()
    uow.chapter_reads[0] = chapter(content=json.dumps([{asset_type: ["old"]}]))
    uow.asset_by_id = ChapterAsset("new", "other-series", display_name)

    result = use(uow, ReplaceAssetCommand("old", "new", asset_type))

    assert result["message"] == f"已替换 1 处分镜，使用 {expected}"
    assert uow.asset_pk_queries == [(asset_type, "new")]


def test_phase_one_update_zero_is_generic_server_failure_and_rolls_back() -> None:
    uow = FakeUnitOfWork()
    uow.update_result = 0

    with pytest.raises(ChapterAssetReplacementFailed) as failure:
        use(uow)

    assert failure.value.status_code == 500
    assert uow.commits == 0
    assert uow.rollback_calls == 1
    assert not any(event[0] == "list_chapters_for_series" for event in uow.events)


def test_missing_factory_is_fail_closed_without_creating_or_replaying_uow() -> None:
    with pytest.raises(ChapterAssetReplacementUnavailable) as failure:
        replace_chapter_asset(None, ACTOR, "chapter-a", COMMAND)

    assert failure.value.status_code == 503
