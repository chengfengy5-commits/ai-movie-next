"""Decision priority and fresh-query behavior for the access use case."""

from dataclasses import dataclass, field

import pytest

from haoai_backend.series_access.application import (
    CLAIMED_SERIES_DENIED,
    SERIES_ACCESS_DENIED,
    require_series_access,
)
from haoai_backend.series_access.domain import ActorIdentity, SeriesAccessRecord, TeamMembership
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound


@dataclass
class ScriptedReader:
    series: SeriesAccessRecord | None
    memberships: list[TeamMembership | None] = field(default_factory=list)
    usernames: dict[str, str] = field(default_factory=dict)
    events: list[tuple[object, ...]] = field(default_factory=list)

    def load_series(self, series_id: str) -> SeriesAccessRecord | None:
        self.events.append(("series", series_id))
        return self.series

    def load_team_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembership | None:
        self.events.append(("membership", team_id, user_id))
        return self.memberships.pop(0) if self.memberships else None

    def load_username(self, user_id: str) -> str | None:
        self.events.append(("username", user_id))
        return self.usernames.get(user_id)


def record(
    *,
    owner: str = "author",
    team: str | None = "team-a",
    claimant: str | None = None,
) -> SeriesAccessRecord:
    return SeriesAccessRecord(
        id="series-a",
        user_id=owner,
        team_id=team,
        claimed_by=claimant,
    )


def test_missing_series_short_circuits_membership_and_username() -> None:
    reader = ScriptedReader(series=None)
    with pytest.raises(SeriesNotFound, match="剧集不存在"):
        require_series_access(reader, ActorIdentity("viewer"), "missing")
    assert reader.events == [("series", "missing")]


def test_claim_gate_precedes_author_and_uses_current_claimed_username() -> None:
    reader = ScriptedReader(
        series=record(claimant="claimant"),
        memberships=[None],
        usernames={"claimant": "当前负责人"},
    )
    with pytest.raises(
        SeriesAccessDenied,
        match=CLAIMED_SERIES_DENIED.format(name="当前负责人"),
    ):
        require_series_access(reader, ActorIdentity("author"), "series-a")
    assert reader.events == [
        ("series", "series-a"),
        ("membership", "team-a", "author"),
        ("username", "claimant"),
    ]


def test_missing_claimed_user_uses_an_empty_name() -> None:
    reader = ScriptedReader(series=record(claimant="deleted-user"), memberships=[None])
    with pytest.raises(SeriesAccessDenied) as caught:
        require_series_access(reader, ActorIdentity("viewer"), "series-a")
    assert caught.value.detail == "该剧集已由「」负责制作，暂不可进入"
    assert reader.events[-1] == ("username", "deleted-user")


def test_claim_permission_does_not_replace_fresh_final_membership_check() -> None:
    reader = ScriptedReader(
        series=record(claimant="claimant"),
        memberships=[
            TeamMembership(role="member", permissions='["enter_claimed_series"]'),
            None,
        ],
        usernames={"claimant": "负责人"},
    )
    with pytest.raises(SeriesAccessDenied) as caught:
        require_series_access(reader, ActorIdentity("viewer"), "series-a")
    assert caught.value.detail == SERIES_ACCESS_DENIED
    assert reader.events == [
        ("series", "series-a"),
        ("membership", "team-a", "viewer"),
        ("membership", "team-a", "viewer"),
    ]


def test_claim_owner_is_requeried_for_final_membership() -> None:
    reader = ScriptedReader(
        series=record(claimant="claimant"),
        memberships=[
            TeamMembership(role="owner", permissions=None),
            TeamMembership(role="member", permissions="[]"),
        ],
    )
    require_series_access(reader, ActorIdentity("viewer"), "series-a")
    assert reader.events.count(("membership", "team-a", "viewer")) == 2


def test_author_passes_after_claim_gate_without_final_membership_query() -> None:
    reader = ScriptedReader(
        series=record(claimant="claimant"),
        memberships=[
            TeamMembership(role="admin", permissions='["enter_claimed_series"]'),
        ],
    )
    require_series_access(reader, ActorIdentity("author"), "series-a")
    assert reader.events == [
        ("series", "series-a"),
        ("membership", "team-a", "author"),
    ]


def test_claimed_actor_is_not_independently_admitted() -> None:
    reader = ScriptedReader(
        series=record(owner="author", claimant="viewer"),
        memberships=[None],
    )
    with pytest.raises(SeriesAccessDenied) as caught:
        require_series_access(reader, ActorIdentity("viewer"), "series-a")
    assert caught.value.detail == SERIES_ACCESS_DENIED
    assert reader.events == [
        ("series", "series-a"),
        ("membership", "team-a", "viewer"),
    ]


def test_unclaimed_team_member_is_admitted_but_nonmember_is_not() -> None:
    member = ScriptedReader(
        series=record(claimant=None),
        memberships=[TeamMembership(role="member", permissions="[]")],
    )
    require_series_access(member, ActorIdentity("viewer"), "series-a")
    assert member.events == [
        ("series", "series-a"),
        ("membership", "team-a", "viewer"),
    ]

    nonmember = ScriptedReader(series=record(claimant=None), memberships=[None])
    with pytest.raises(SeriesAccessDenied) as caught:
        require_series_access(nonmember, ActorIdentity("viewer"), "series-a")
    assert caught.value.detail == SERIES_ACCESS_DENIED


def test_personal_series_needs_only_author_identity() -> None:
    reader = ScriptedReader(series=record(owner="viewer", team=None, claimant=None))
    require_series_access(reader, ActorIdentity("viewer"), "series-a")
    assert reader.events == [("series", "series-a")]


def test_unexpected_repository_error_is_not_reclassified() -> None:
    class BrokenReader:
        def load_series(self, series_id: str) -> SeriesAccessRecord | None:
            raise RuntimeError("database unavailable")

        def load_team_membership(self, team_id: str, user_id: str) -> None:
            raise AssertionError("no membership query expected")

        def load_username(self, user_id: str) -> None:
            raise AssertionError("no username query expected")

    with pytest.raises(RuntimeError, match="database unavailable"):
        require_series_access(BrokenReader(), ActorIdentity("viewer"), "series-a")
