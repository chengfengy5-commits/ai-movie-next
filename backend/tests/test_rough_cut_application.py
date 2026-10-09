"""Use-case sequencing, transaction boundaries, and conflict tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from haoai_backend.personal_production.rough_cut.application import get_rough_cut, save_rough_cut
from haoai_backend.personal_production.rough_cut.domain import (
    ChapterRecord,
    RoughCutUpdate,
    SavedDraft,
    SourceAsset,
    UpdateFrame,
)
from haoai_backend.personal_production.rough_cut.errors import (
    Conflict,
    Forbidden,
    InvalidSource,
    InvalidUpdate,
    SourceTooLarge,
)
from haoai_backend.personal_production.rough_cut.ports import TrustedActor


class InsertRace(Exception):
    pass


class StorageFailure(Exception):
    pass


@dataclass
class FakeUnitOfWork:
    content: Any = None
    asset_ids: tuple[str, ...] = ()
    draft: SavedDraft | None = None
    access_error: Exception | None = None
    commit_error: Exception | None = None
    fail_update: bool = False
    race_on_insert: bool = False
    post_commit_revision: int | None = None

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.draft_read_count = 0
        self.committed_rows: list[dict[str, object]] | None = None

    def load_chapter(self, chapter_id: str, *, lock: bool) -> ChapterRecord | None:
        self.calls.append("chapter-lock" if lock else "chapter-read")
        if chapter_id != "chapter-a":
            return None
        return ChapterRecord(id=chapter_id, series_id="series-a", content=self.content)

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        self.calls.append("access")
        if self.access_error is not None:
            raise self.access_error

    def list_source_assets(self, chapter_id: str) -> list[SourceAsset]:
        self.calls.append("assets")
        return [SourceAsset(id=asset_id) for asset_id in self.asset_ids]

    def load_private_draft(self, chapter_id: str, user_id: str) -> SavedDraft | None:
        self.calls.append("draft")
        self.draft_read_count += 1
        return self.draft

    def insert_private_draft(
        self,
        chapter_id: str,
        user_id: str,
        revision: int,
        frames: tuple[dict[str, object], ...],
    ) -> None:
        self.calls.append("insert")
        if self.race_on_insert:
            raise InsertRace()
        self.committed_rows = list(frames)
        self.draft = SavedDraft(revision, list(frames))

    def update_private_draft(
        self,
        chapter_id: str,
        user_id: str,
        expected_revision: int,
        next_revision: int,
        frames: tuple[dict[str, object], ...],
    ) -> bool:
        self.calls.append("update")
        if self.fail_update:
            return False
        self.committed_rows = list(frames)
        self.draft = SavedDraft(next_revision, list(frames))
        return True

    def commit(self) -> None:
        self.calls.append("commit")
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error
        if self.post_commit_revision is not None and self.draft is not None:
            self.draft = SavedDraft(self.post_commit_revision, self.draft.frames)

    def rollback(self) -> None:
        self.rollback_count += 1
        self.calls.append("rollback")

    def close(self) -> None:
        self.close_count += 1

    def is_private_draft_unique_conflict(self, error: BaseException) -> bool:
        return isinstance(error, InsertRace)


def one_frame_content(asset_id: str = "asset-a") -> list[dict[str, Any]]:
    return [{"storyboard": [asset_id, "ignored"], "text": "镜头", "preview": "clip.MP4?x=1"}]


def test_get_runs_access_before_source_and_private_read_without_commit() -> None:
    unit = FakeUnitOfWork(content=one_frame_content(), asset_ids=("asset-a",))
    response = get_rough_cut(lambda: unit, TrustedActor("user-a"), "chapter-a")

    assert unit.calls == ["chapter-read", "access", "assets", "draft"]
    assert unit.commit_count == 0
    assert unit.close_count == 1
    assert response["revision"] == 0
    assert response["frames"][0]["included"] is True


def test_denied_access_prevents_source_and_private_draft_reads() -> None:
    unit = FakeUnitOfWork(
        content=one_frame_content(),
        asset_ids=("asset-a",),
        access_error=Forbidden("拒绝访问"),
    )

    with pytest.raises(Forbidden):
        get_rough_cut(lambda: unit, TrustedActor("user-a"), "chapter-a")

    assert unit.calls == ["chapter-read", "access", "rollback"]
    assert unit.draft_read_count == 0
    assert unit.commit_count == 0
    assert unit.close_count == 1


def test_bad_chapter_source_precedes_stale_revision_conflict() -> None:
    unit = FakeUnitOfWork(content="broken", asset_ids=(), draft=SavedDraft(4, []))
    update = RoughCutUpdate(expected_revision=3, frames=())

    with pytest.raises(InvalidSource):
        save_rough_cut(lambda: unit, TrustedActor("user-a"), "chapter-a", update)

    assert unit.calls == ["chapter-lock", "access", "rollback"]
    assert unit.draft_read_count == 0


def test_revision_conflict_precedes_invalid_source_identity_and_size() -> None:
    frames = [{"storyboard": [f"asset-{index}"]} for index in range(501)]
    frames[-1] = {"text": "invalid identity"}
    assets = tuple(f"asset-{index}" for index in range(500))
    unit = FakeUnitOfWork(content=frames, asset_ids=assets, draft=SavedDraft(8, []))

    with pytest.raises(Conflict):
        save_rough_cut(
            lambda: unit,
            TrustedActor("user-a"),
            "chapter-a",
            RoughCutUpdate(expected_revision=7, frames=()),
        )
    assert unit.calls == ["chapter-lock", "access", "assets", "draft", "rollback"]


def test_invalid_source_identity_precedes_oversized_valid_source() -> None:
    frames = [{"storyboard": [f"asset-{index}"]} for index in range(501)]
    frames[-1] = {"storyboard": ["missing"]}
    assets = tuple(f"asset-{index}" for index in range(500))
    unit = FakeUnitOfWork(content=frames, asset_ids=assets)

    with pytest.raises(InvalidSource):
        save_rough_cut(
            lambda: unit,
            TrustedActor("user-a"),
            "chapter-a",
            RoughCutUpdate(expected_revision=0, frames=()),
        )


def test_valid_source_over_limit_is_413_and_get_remains_unlimited() -> None:
    frames = [{"storyboard": [f"asset-{index}"]} for index in range(501)]
    assets = tuple(f"asset-{index}" for index in range(501))
    unit = FakeUnitOfWork(content=frames, asset_ids=assets)

    with pytest.raises(SourceTooLarge):
        save_rough_cut(
            lambda: unit,
            TrustedActor("user-a"),
            "chapter-a",
            RoughCutUpdate(expected_revision=0, frames=()),
        )
    assert unit.commit_count == 0

    read_unit = FakeUnitOfWork(content=frames, asset_ids=assets)
    response = get_rough_cut(lambda: read_unit, TrustedActor("user-a"), "chapter-a")
    assert len(response["frames"]) == 501


def test_first_empty_save_commits_once_and_returns_its_own_revision() -> None:
    unit = FakeUnitOfWork(content=None, asset_ids=(), post_commit_revision=2)
    response = save_rough_cut(
        lambda: unit,
        TrustedActor("user-a"),
        "chapter-a",
        RoughCutUpdate(expected_revision=0, frames=()),
    )

    assert unit.calls == ["chapter-lock", "access", "assets", "draft", "insert", "commit"]
    assert unit.commit_count == 1
    assert unit.draft_read_count == 1
    assert unit.close_count == 1
    assert response == {
        "chapter_id": "chapter-a",
        "revision": 1,
        "saved": True,
        "frames": [],
        "removed_asset_ids": [],
    }


def test_save_complete_reordered_set_and_rowcount_conflict() -> None:
    unit = FakeUnitOfWork(content=one_frame_content(), asset_ids=("asset-a",))
    response = save_rough_cut(
        lambda: unit,
        TrustedActor("user-a"),
        "chapter-a",
        RoughCutUpdate(0, (UpdateFrame("asset-a", False),)),
    )
    assert unit.committed_rows == [{"asset_id": "asset-a", "included": False}]
    assert response["revision"] == 1
    assert response["frames"][0]["included"] is False
    assert unit.commit_count == 1

    stale = FakeUnitOfWork(
        content=one_frame_content(),
        asset_ids=("asset-a",),
        draft=SavedDraft(1, [{"asset_id": "asset-a", "included": True}]),
        fail_update=True,
    )
    with pytest.raises(Conflict):
        save_rough_cut(
            lambda: stale,
            TrustedActor("user-a"),
            "chapter-a",
            RoughCutUpdate(1, (UpdateFrame("asset-a", False),)),
        )
    assert stale.commit_count == 0
    assert stale.rollback_count == 1


def test_private_pair_insert_race_maps_to_conflict_and_attempts_no_retry() -> None:
    unit = FakeUnitOfWork(content=one_frame_content(), asset_ids=("asset-a",), race_on_insert=True)

    with pytest.raises(Conflict):
        save_rough_cut(
            lambda: unit,
            TrustedActor("user-a"),
            "chapter-a",
            RoughCutUpdate(0, (UpdateFrame("asset-a", True),)),
        )

    assert unit.calls.count("insert") == 1
    assert unit.commit_count == 0
    assert unit.rollback_count == 1
    assert unit.close_count == 1


def test_storage_failure_rolls_back_without_retry_or_false_success() -> None:
    failure = StorageFailure("commit failed before durable write")
    unit = FakeUnitOfWork(content=one_frame_content(), asset_ids=("asset-a",), commit_error=failure)

    with pytest.raises(StorageFailure) as raised:
        save_rough_cut(
            lambda: unit,
            TrustedActor("user-a"),
            "chapter-a",
            RoughCutUpdate(0, (UpdateFrame("asset-a", True),)),
        )

    assert raised.value is failure
    assert unit.commit_count == 1
    assert unit.rollback_count == 1
    assert unit.close_count == 1
    assert unit.calls.count("insert") == 1
