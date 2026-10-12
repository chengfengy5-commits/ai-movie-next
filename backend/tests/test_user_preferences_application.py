from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.user_preferences.application import (
    get_user_preferences,
    update_user_preferences,
)


@dataclass
class PreferenceRow:
    id: str
    user_id: str
    key: str
    value: Any
    updated_at: datetime


class RecordingUnitOfWork:
    def __init__(self, rows: list[PreferenceRow] | None = None) -> None:
        self.rows = list(rows or [])
        self.events: list[tuple[Any, ...]] = []
        self.commit_error: BaseException | None = None
        self.persisted = False

    def list_preferences(self, user_id: str) -> list[PreferenceRow]:
        self.events.append(("list", user_id))
        return [row for row in self.rows if row.user_id == user_id]

    def find_preference(self, user_id: str, key: str) -> PreferenceRow | None:
        self.events.append(("find", user_id, key))
        return next(
            (
                row
                for row in self.rows
                if row.user_id == user_id and row.key == key
            ),
            None,
        )

    def add_preference(
        self,
        *,
        preference_id: str,
        user_id: str,
        key: str,
        value: str,
        updated_at: datetime,
    ) -> None:
        self.events.append(("add", preference_id, user_id, key, value, updated_at))
        self.rows.append(
            PreferenceRow(preference_id, user_id, key, value, updated_at)
        )

    def commit(self) -> None:
        self.events.append(("commit",))
        self.persisted = True
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.events.append(("rollback",))

    def close(self) -> None:
        self.events.append(("close",))


def test_get_decodes_json_and_keeps_raw_values_without_business_commit() -> None:
    unit_of_work = RecordingUnitOfWork(
        [
            PreferenceRow("one", "user-a", "object", '{"enabled": true}', datetime.min),
            PreferenceRow("two", "user-a", "invalid", "{broken", datetime.min),
            PreferenceRow("three", "user-a", "typed-fallback", None, datetime.min),
            PreferenceRow("four", "user-b", "private", '"hidden"', datetime.min),
        ]
    )

    result = get_user_preferences(lambda: unit_of_work, TrustedActor("user-a"))

    assert result == {
        "object": {"enabled": True},
        "invalid": "{broken",
        "typed-fallback": None,
    }
    assert unit_of_work.events == [("list", "user-a"), ("close",)]


def test_update_preserves_filtering_order_serialization_and_single_commit() -> None:
    timestamp = datetime(2026, 10, 12, 8, 30)
    existing = PreferenceRow(
        "existing-id", "user-a", "existing", '{"old": true}', datetime.min
    )
    same = PreferenceRow("same-id", "user-a", "same", '"stable"', datetime.min)
    unit_of_work = RecordingUnitOfWork([existing, same])
    clock_calls: list[datetime] = []
    id_calls: list[str] = []
    cycle: list[Any] = []
    cycle.append(cycle)

    result = update_user_preferences(
        lambda: unit_of_work,
        TrustedActor("user-a"),
        {
            "existing": {"new": "值"},
            "": "ignored",
            "k" * 101: "ignored",
            "not-serializable": cycle,
            "new": {"路径": "电影"},
            "same": "stable",
        },
        clock=lambda: clock_calls.append(timestamp) or timestamp,
        id_factory=lambda: id_calls.append("new-id") or "new-id",
    )

    assert result == {"ok": True, "updated_keys": ["existing", "new", "same"]}
    assert existing.value == '{"new": "值"}'
    assert existing.updated_at == timestamp
    assert same.value == '"stable"'
    assert id_calls == ["new-id"]
    assert clock_calls == [timestamp]
    assert unit_of_work.events == [
        ("find", "user-a", "existing"),
        ("find", "user-a", "new"),
        ("add", "new-id", "user-a", "new", '{"路径": "电影"}', timestamp),
        ("find", "user-a", "same"),
        ("commit",),
        ("close",),
    ]


@pytest.mark.parametrize("preferences", [{}, {"x" * 101: 1}, {"skip": object()}])
def test_empty_or_skipped_update_still_commits_once(preferences: dict[str, Any]) -> None:
    unit_of_work = RecordingUnitOfWork()

    result = update_user_preferences(
        lambda: unit_of_work,
        TrustedActor("user-a"),
        preferences,
        clock=lambda: datetime(2026, 10, 12),
    )

    assert result == {"ok": True, "updated_keys": []}
    assert unit_of_work.events[-2:] == [("commit",), ("close",)]


def test_unknown_commit_result_propagates_without_replay() -> None:
    failure = RuntimeError("commit result unknown")
    unit_of_work = RecordingUnitOfWork()
    unit_of_work.commit_error = failure

    with pytest.raises(RuntimeError, match="commit result unknown"):
        update_user_preferences(
            lambda: unit_of_work,
            TrustedActor("user-a"),
            {"theme": "dark"},
            clock=lambda: datetime(2026, 10, 12),
            id_factory=lambda: "preference-id",
        )

    assert unit_of_work.persisted is True
    assert unit_of_work.events.count(("commit",)) == 1
    assert unit_of_work.events[-2:] == [("rollback",), ("close",)]


def test_key_length_whitespace_and_json_null_values_keep_legacy_behavior() -> None:
    unit_of_work = RecordingUnitOfWork()
    timestamp = datetime(2026, 10, 12)
    hundred_characters = "k" * 100
    one_hundred_one_characters = "k" * 101

    result = update_user_preferences(
        lambda: unit_of_work,
        TrustedActor("user-a"),
        {
            hundred_characters: "accepted",
            one_hundred_one_characters: "skipped",
            " ": "whitespace",
            "null": None,
            "empty": {},
        },
        clock=lambda: timestamp,
        id_factory=lambda: f"id-{len(unit_of_work.rows) + 1}",
    )

    assert result == {
        "ok": True,
        "updated_keys": [hundred_characters, " ", "null", "empty"],
    }
    assert {row.key: row.value for row in unit_of_work.rows} == {
        hundred_characters: '"accepted"',
        " ": '"whitespace"',
        "null": "null",
        "empty": "{}",
    }
