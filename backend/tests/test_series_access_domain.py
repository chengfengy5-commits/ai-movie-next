"""Pure permission parsing and claimed-series rules."""

from haoai_backend.series_access.domain import (
    CLAIMED_SERIES_PERMISSION,
    TeamMembership,
    can_enter_claimed_series,
    parse_team_permissions,
)


def test_permission_parser_keeps_only_exact_known_keys() -> None:
    assert parse_team_permissions(
        '["enter_claimed_series", "view_tasks", "unknown", " Enter_claimed_series "]'
    ) == frozenset({CLAIMED_SERIES_PERMISSION, "view_tasks"})


def test_empty_invalid_and_non_list_permission_json_are_empty() -> None:
    for value in (None, "", " ", "{", "{}", "null", "42", "false"):
        assert parse_team_permissions(value) == frozenset()


def test_one_unhashable_permission_invalidates_the_complete_list() -> None:
    assert parse_team_permissions('["enter_claimed_series", {"bad": true}]') == frozenset()


def test_claimed_series_access_is_owner_or_exact_permission_only() -> None:
    assert can_enter_claimed_series(TeamMembership(role="owner", permissions="broken"))
    assert can_enter_claimed_series(
        TeamMembership(role="admin", permissions='["enter_claimed_series"]')
    )
    assert not can_enter_claimed_series(
        TeamMembership(role="member", permissions='["ENTER_CLAIMED_SERIES"]')
    )
    assert not can_enter_claimed_series(None)
