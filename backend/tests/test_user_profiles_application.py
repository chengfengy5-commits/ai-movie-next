"""Framework-free tests for user-card policy and profile update sequencing."""

from __future__ import annotations

from datetime import datetime

import pytest

from haoai_backend.user_profiles.application import UserProfileApplication
from haoai_backend.user_profiles.authentication import UserProfileActor
from haoai_backend.user_profiles.domain import (
    SeriesAssetCounts,
    SeriesRecord,
    TeamMembership,
    TeamRecord,
    UserProfileRecord,
    mask_email,
)
from haoai_backend.user_profiles.errors import (
    UserProfileForbidden,
    UserProfileNotFound,
    UserProfilePersistenceError,
)


NOW = datetime(2026, 10, 13, 12, 0, 0)


class MemoryReader:
    def __init__(self) -> None:
        self.users = {
            "viewer": UserProfileRecord(
                "viewer", "viewer-name", "viewer@example.test", "viewer bio", NOW
            ),
            "target": UserProfileRecord(
                "target", "target-name", "123456@QQ.COM", "target bio", NOW
            ),
        }
        self.team_ids = {
            "viewer": ["team-a", "team-b"],
            "target": ["team-a", "team-c"],
        }
        self.teams = [
            TeamRecord("team-a", "A"),
            TeamRecord("team-b", "B"),
            TeamRecord("team-c", "C"),
        ]
        self.memberships = {
            ("team-a", "target"): TeamMembership(role=None),
        }
        self.series = [
            SeriesRecord("series-a", "shared", "team-a"),
            SeriesRecord("series-b", "also shared", "team-a"),
        ]
        self.asset_counts = {
            "series-a": SeriesAssetCounts(2, 1, 3, 4),
            "series-b": SeriesAssetCounts(1, 0, 0, 2),
        }
        self.calls: list[tuple[object, ...]] = []
        self.team_query_count = 0

    def load_user(self, user_id: str) -> UserProfileRecord | None:
        self.calls.append(("load_user", user_id))
        return self.users.get(user_id)

    def list_team_ids(self, user_id: str) -> list[str]:
        self.calls.append(("list_team_ids", user_id))
        return self.team_ids.get(user_id, [])

    def list_teams(self, team_ids) -> list[TeamRecord]:
        self.calls.append(("list_teams", set(team_ids)))
        self.team_query_count += 1
        return [team for team in self.teams if team.id in team_ids]

    def count_team_members(self, team_id: str) -> int:
        self.calls.append(("count_team_members", team_id))
        return 5 if team_id == "team-a" else 2

    def load_team_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembership | None:
        self.calls.append(("load_team_membership", team_id, user_id))
        return self.memberships.get((team_id, user_id))

    def list_owned_series(self, user_id: str, team_ids) -> list[SeriesRecord]:
        self.calls.append(("list_owned_series", user_id, set(team_ids)))
        return [item for item in self.series if item.team_id in team_ids]

    def count_series_assets(self, series_id: str) -> SeriesAssetCounts:
        self.calls.append(("count_series_assets", series_id))
        return self.asset_counts[series_id]


def actor(
    user_id: str = "viewer",
    *,
    bio: str | None = "snapshot bio",
) -> UserProfileActor:
    return UserProfileActor(user_id, "snapshot-name", "snapshot@example.test", bio)


def test_user_card_uses_common_teams_target_owned_series_and_four_asset_totals() -> None:
    reader = MemoryReader()

    card = UserProfileApplication().get_user_card(
        reader,
        viewer=actor(),
        target_user_id="target",
    )

    assert card["user"] == {
        "id": "target",
        "username": "target-name",
            "avatar_url": "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100",
        "bio": "target bio",
            "email_masked": "1***6@QQ.COM",
        "created_at": NOW,
    }
    assert card["teams"] == [
        {
            "id": "team-a",
            "name": "A",
            "role": None,
            "member_count": 5,
        }
    ]
    assert card["series"] == [
        {
            "id": "series-a",
            "name": "shared",
            "team_id": "team-a",
            "team_name": "A",
            "asset_counts": {
                "characters": 2,
                "scenes": 1,
                "props": 3,
                "storyboard": 4,
            },
        },
        {
            "id": "series-b",
            "name": "also shared",
            "team_id": "team-a",
            "team_name": "A",
            "asset_counts": {
                "characters": 1,
                "scenes": 0,
                "props": 0,
                "storyboard": 2,
            },
        },
    ]
    assert card["asset_totals"] == {
        "characters": 3,
        "scenes": 1,
        "props": 3,
        "storyboard": 6,
    }
    assert reader.team_query_count == 2
    assert ("list_owned_series", "target", {"team-a"}) in reader.calls
    assert reader.calls[:7] == [
        ("load_user", "target"),
        ("list_team_ids", "viewer"),
        ("list_team_ids", "target"),
        ("list_teams", {"team-a"}),
        ("count_team_members", "team-a"),
        ("load_team_membership", "team-a", "target"),
        ("list_owned_series", "target", {"team-a"}),
    ]


def test_self_card_keeps_all_teams_and_empty_asset_totals() -> None:
    reader = MemoryReader()
    reader.series = []
    reader.memberships[("team-a", "viewer")] = TeamMembership(role="owner")
    reader.memberships[("team-b", "viewer")] = TeamMembership(role="member")

    card = UserProfileApplication().get_user_card(
        reader,
        viewer=actor(),
        target_user_id="viewer",
    )

    assert {team["id"] for team in card["teams"]} == {"team-a", "team-b"}
    assert card["series"] == []
    assert card["asset_totals"] == {
        "characters": 0,
        "scenes": 0,
        "props": 0,
        "storyboard": 0,
    }


def test_email_mask_preserves_legacy_empty_and_raw_case_behavior() -> None:
    assert mask_email(None) == ""
    assert mask_email("") == ""
    assert mask_email("not-an-email") == "not-an-email"
    assert mask_email("x@domain") == "x***@domain"
    assert mask_email("@domain") == "***@domain"
    assert mask_email("Ab@DOMAIN") == "A***b@DOMAIN"


def test_missing_target_fails_before_team_queries() -> None:
    reader = MemoryReader()

    with pytest.raises(UserProfileNotFound, match="用户不存在"):
        UserProfileApplication().get_user_card(
            reader,
            viewer=actor(),
            target_user_id="missing",
        )

    assert reader.calls == [("load_user", "missing")]


def test_no_shared_team_fails_before_team_and_series_projection() -> None:
    reader = MemoryReader()
    reader.team_ids["viewer"] = ["team-b"]
    reader.team_ids["target"] = ["team-c"]

    with pytest.raises(UserProfileForbidden, match="无权查看该用户信息"):
        UserProfileApplication().get_user_card(
            reader,
            viewer=actor(),
            target_user_id="target",
        )

    assert reader.calls == [
        ("load_user", "target"),
        ("list_team_ids", "viewer"),
        ("list_team_ids", "target"),
    ]


class MemoryUnitOfWork(MemoryReader):
    def __init__(self, current_user: UserProfileRecord | None) -> None:
        super().__init__()
        self.current_user = current_user
        self.events: list[tuple[object, ...]] = []

    def set_bio(self, user_id: str, bio: str) -> None:
        self.events.append(("set_bio", user_id, bio))

    def commit(self) -> None:
        self.events.append(("commit",))

    def load_user(self, user_id: str) -> UserProfileRecord | None:
        self.events.append(("readback", user_id))
        return self.current_user


def test_profile_noop_commits_and_returns_database_snapshot_not_actor_snapshot() -> None:
    database_user = UserProfileRecord(
        "viewer", "fresh-name", "123456@qq.com", "concurrent database bio", NOW
    )
    unit_of_work = MemoryUnitOfWork(database_user)

    payload = UserProfileApplication().update_my_profile(
        unit_of_work,
        actor=actor(bio="same request"),
        requested_bio="same request",
    )

    assert unit_of_work.events == [("commit",), ("readback", "viewer")]
    assert payload == {
        "id": "viewer",
        "username": "fresh-name",
        "avatar_url": "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100",
        "bio": "concurrent database bio",
    }


def test_null_bio_commits_without_assignment_and_missing_readback_propagates() -> None:
    unit_of_work = MemoryUnitOfWork(None)

    with pytest.raises(UserProfilePersistenceError, match="post-commit readback"):
        UserProfileApplication().update_my_profile(
            unit_of_work,
            actor=actor(),
            requested_bio=None,
        )

    assert unit_of_work.events == [("commit",), ("readback", "viewer")]


def test_empty_bio_is_a_real_change_and_postcommit_missing_row_propagates() -> None:
    unit_of_work = MemoryUnitOfWork(None)

    with pytest.raises(UserProfilePersistenceError, match="post-commit readback"):
        UserProfileApplication().update_my_profile(
            unit_of_work,
            actor=actor(bio="existing"),
            requested_bio="",
        )

    assert unit_of_work.events == [
        ("set_bio", "viewer", ""),
        ("commit",),
        ("readback", "viewer"),
    ]


def test_commit_failure_propagates_without_readback_or_retry() -> None:
    class FailingUnitOfWork(MemoryUnitOfWork):
        def commit(self) -> None:
            self.events.append(("commit",))
            raise RuntimeError("commit acknowledgment unknown")

        def load_user(self, user_id: str) -> UserProfileRecord | None:
            raise AssertionError("a failed commit must not trigger readback")

    unit_of_work = FailingUnitOfWork(None)
    with pytest.raises(RuntimeError, match="acknowledgment unknown"):
        UserProfileApplication().update_my_profile(
            unit_of_work,
            actor=actor(bio="old"),
            requested_bio="new",
        )

    assert unit_of_work.events == [
        ("set_bio", "viewer", "new"),
        ("commit",),
    ]
