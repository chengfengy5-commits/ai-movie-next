from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from haoai_backend.admin_series import application
from haoai_backend.admin_series.domain import (
    AdminSeriesActor,
    SeriesRecord,
)
from haoai_backend.admin_series.errors import (
    AdminSeriesForbidden,
    AdminSeriesNotFound,
    AdminSeriesUnavailable,
)

ADMIN = AdminSeriesActor(user_id="trusted-admin", is_superuser=True)
MEMBER = AdminSeriesActor(user_id="member", is_superuser=False)
STAMP = datetime(2026, 10, 13, 9, 30)


def record(**changes) -> SeriesRecord:
    values = {
        "id": "series-1",
        "user_id": "owner",
        "name": "Series",
        "description": None,
        "image_url": None,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    values.update(changes)
    return SeriesRecord(**values)


class FakeUnitOfWork:
    def __init__(self, *, existing=True, fail=None, close_error=None):
        self.events: list[object] = []
        self.existing = existing
        self.fail = fail
        self.close_error = close_error
        self.loaded = SimpleNamespace(
            id="series-1",
            user_id="owner",
            name="before",
            description=None,
            image_url=None,
            created_at=STAMP,
            updated_at=STAMP,
            metadata="inherited",
            registry="inherited",
            __table__="inherited",
            chapters=["existing"],
        )
        self.created = None

    def list_series(self, **query):
        self.events.append(("list", query))
        if self.fail is not None:
            raise self.fail
        return (record(),), 4

    def load_series(self, series_id):
        self.events.append(("load", series_id))
        return record() if self.existing else None

    def load_series_for_update(self, series_id):
        self.events.append(("load_for_update", series_id))
        return self.loaded if self.existing else None

    def add_series(self, series):
        self.events.append(("add", series))
        self.created = SimpleNamespace(
            id=series.id,
            user_id=series.user_id,
            name=series.name,
            description=series.description,
            image_url=series.image_url,
            created_at=STAMP,
            updated_at=STAMP,
        )
        return self.created

    def commit(self):
        self.events.append("commit")
        if self.fail is not None:
            raise self.fail

    def refresh_series(self, row):
        self.events.append(("refresh", row))
        return record(
            id=row.id,
            user_id=row.user_id,
            name=row.name,
            description=row.description,
            image_url=row.image_url,
        )

    def rollback(self):
        self.events.append("rollback")
        if self.fail is not None:
            raise RuntimeError("secondary rollback failure")

    def close(self):
        self.events.append("close")
        if self.close_error is not None:
            raise self.close_error


def test_list_passes_legacy_query_values_and_returns_the_seven_field_dto():
    unit = FakeUnitOfWork()
    result = application.list_series(
        lambda: unit,
        ADMIN,
        user_id="owner",
        name="raw_%",
        sort_field="unknown",
        sort_order="DESC",
        skip=-2,
        limit=1001,
    )

    assert unit.events == [
        (
            "list",
            {
                "user_id": "owner",
                "name": "raw_%",
                "sort_field": "unknown",
                "sort_order": "DESC",
                "skip": -2,
                "limit": 1001,
            },
        ),
        "close",
    ]
    assert result == {
        "data": [
            {
                "id": "series-1",
                "user_id": "owner",
                "name": "Series",
                "description": None,
                "image_url": None,
                "created_at": STAMP,
                "updated_at": STAMP,
            }
        ],
        "total": 4,
    }


def test_create_uses_only_missing_defaults_and_trusted_administrator_identity():
    unit = FakeUnitOfWork()
    result = application.create_series(
        lambda: unit,
        ADMIN,
        {
            "id": "caller-id",
            "user_id": "caller-user",
            "created_at": "caller-time",
            "updated_at": "caller-time",
        },
    )

    created = unit.created
    assert created is not None
    UUID(created.id)
    assert created.id != "caller-id"
    assert created.user_id == "trusted-admin"
    assert created.name == ""
    assert created.description == ""
    assert created.image_url is None
    assert result["id"] == created.id
    assert [event if isinstance(event, str) else event[0] for event in unit.events] == [
        "add",
        "commit",
        "refresh",
        "close",
    ]
    assert unit.events[2][1] is created


def test_update_applies_every_existing_attribute_in_body_order_without_refetch():
    class TrackingRow:
        def __init__(self):
            object.__setattr__(self, "sets", [])
            for key, value in {
                "id": "series-1",
                "user_id": "owner",
                "name": "before",
                "description": None,
                "image_url": None,
                "created_at": STAMP,
                "updated_at": STAMP,
                "metadata": "inherited",
                "registry": "inherited",
                "__table__": "inherited",
                "chapters": ["existing"],
            }.items():
                object.__setattr__(self, key, value)

        def __setattr__(self, name, value):
            self.sets.append((name, value))
            object.__setattr__(self, name, value)

    unit = FakeUnitOfWork()
    row = TrackingRow()
    unit.loaded = row
    result = application.update_series(
        lambda: unit,
        ADMIN,
        "series-1",
        {
            "metadata": "metadata-shadow",
            "registry": "registry-shadow",
            "__table__": "table-shadow",
            "id": "changed-id",
            "user_id": "other-user",
            "chapters": [],
            "unknown": "ignored",
        },
    )

    assert [key for key, _value in row.sets] == [
        "metadata",
        "registry",
        "__table__",
        "id",
        "user_id",
        "chapters",
    ]
    assert row.chapters == []
    assert unit.events[0] == ("load_for_update", "series-1")
    assert unit.events[1] == "commit"
    assert unit.events[2] == ("refresh", row)
    assert unit.events[3] == "close"
    assert result["id"] == "changed-id"
    assert result["user_id"] == "other-user"


def test_not_found_and_primary_failure_cleanup_preserve_the_original_error():
    missing = FakeUnitOfWork(existing=False)
    with pytest.raises(AdminSeriesNotFound):
        application.update_series(
            lambda: missing,
            ADMIN,
            "missing",
            {"name": "unused"},
        )
    assert missing.events == [
        ("load_for_update", "missing"),
        "rollback",
        "close",
    ]

    primary = OSError("commit acknowledgement lost")
    failing = FakeUnitOfWork(fail=primary, close_error=RuntimeError("close failed"))
    with pytest.raises(OSError) as raised:
        application.list_series(lambda: failing, ADMIN)
    assert raised.value is primary
    assert failing.events[-2:] == ["rollback", "close"]


def test_authorization_and_missing_factory_do_not_open_a_session():
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        raise AssertionError("no worker should be created")

    with pytest.raises(AdminSeriesForbidden):
        application.list_series(factory, MEMBER)
    with pytest.raises(AdminSeriesUnavailable):
        application.list_series(None, ADMIN)
    assert calls == 0


def test_successful_close_failure_is_not_suppressed():
    close_error = RuntimeError("close failed")
    unit = FakeUnitOfWork(close_error=close_error)

    with pytest.raises(RuntimeError) as raised:
        application.get_series(lambda: unit, ADMIN, "series-1")

    assert raised.value is close_error
    assert unit.events == [("load", "series-1"), "close"]
