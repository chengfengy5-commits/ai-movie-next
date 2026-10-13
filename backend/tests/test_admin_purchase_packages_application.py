from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_purchase_packages import application
from haoai_backend.admin_purchase_packages.domain import (
    NewPurchasePackage,
    PurchasePackage,
)
from haoai_backend.admin_purchase_packages.errors import PurchasePackageNotFound

NOW = datetime(2026, 10, 1, 12, 0, 0)


def package(package_id: str = "pkg-1") -> PurchasePackage:
    return PurchasePackage(
        id=package_id,
        category="credits",
        name="积分包",
        price=12,
        credits=100,
        days=None,
        sort_order=3,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.rows: dict[str, PurchasePackage] = {"pkg-1": package()}
        self.refresh_result: PurchasePackage | None = package()
        self.commit_error: Exception | None = None
        self.query_error: Exception | None = None
        self.refresh_error: Exception | None = None
        self.rollback_error: Exception | None = None
        self.close_error: Exception | None = None

    def count_packages(self) -> int:
        self.events.append(("count",))
        if self.query_error is not None:
            raise self.query_error
        return len(self.rows)

    def list_packages(self, **kwargs: Any) -> tuple[PurchasePackage, ...]:
        self.events.append(("list", kwargs))
        return tuple(self.rows.values())

    def load_package(self, package_id: str) -> PurchasePackage | None:
        self.events.append(("load", package_id))
        return self.rows.get(package_id)

    def add_package(self, value: NewPurchasePackage) -> None:
        self.events.append(("add", value))

    def update_package(self, package_id: str, changes: Any) -> bool:
        self.events.append(("update", package_id, dict(changes)))
        return package_id in self.rows

    def delete_package(self, package_id: str) -> bool:
        self.events.append(("delete", package_id))
        return package_id in self.rows

    def commit(self) -> None:
        self.events.append(("commit",))
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.events.append(("rollback",))
        if self.rollback_error is not None:
            raise self.rollback_error

    def refresh_package(self, package_id: str) -> PurchasePackage | None:
        self.events.append(("refresh", package_id))
        if self.refresh_error is not None:
            raise self.refresh_error
        return self.refresh_result

    def close(self) -> None:
        self.events.append(("close",))
        if self.close_error is not None:
            raise self.close_error


def factory_for(uow: FakeUnitOfWork):
    return lambda: uow


def test_list_counts_before_query_and_passes_raw_legacy_sort_values() -> None:
    uow = FakeUnitOfWork()

    page = application.list_packages(
        factory_for(uow), sort_field="metadata", sort_order="DESC", skip=-4, limit=0
    )

    assert page.total == 1
    assert page.data == (package(),)
    assert uow.events == [
        ("count",),
        (
            "list",
            {"sort_field": "metadata", "sort_order": "DESC", "offset": -4, "limit": 0},
        ),
        ("close",),
    ]


def test_create_commits_once_then_reads_back_real_record_and_closes() -> None:
    uow = FakeUnitOfWork()
    uow.refresh_result = package("fixed-id")

    result = application.create_package(
        factory_for(uow),
        category="custom",
        name="custom package",
        price=-8,
        credits=None,
        days=None,
        sort_order=0,
        is_active=True,
        id_factory=lambda: "fixed-id",
    )

    assert result == package("fixed-id")
    assert uow.events == [
        (
            "add",
            NewPurchasePackage(
                id="fixed-id",
                category="custom",
                name="custom package",
                price=-8,
                credits=None,
                days=None,
                sort_order=0,
                is_active=True,
            ),
        ),
        ("commit",),
        ("refresh", "fixed-id"),
        ("close",),
    ]


def test_update_preserves_explicit_none_and_commits_even_empty_mapping() -> None:
    uow = FakeUnitOfWork()
    uow.refresh_result = package()

    result = application.update_package(factory_for(uow), "pkg-1", {"credits": None})

    assert result == package()
    assert uow.events == [
        ("update", "pkg-1", {"credits": None}),
        ("commit",),
        ("refresh", "pkg-1"),
        ("close",),
    ]

    uow.events.clear()
    application.update_package(factory_for(uow), "pkg-1", {})
    assert uow.events == [
        ("update", "pkg-1", {}), ("commit",), ("refresh", "pkg-1"), ("close",)
    ]


def test_not_found_and_post_commit_failures_propagate_without_replay() -> None:
    uow = FakeUnitOfWork()
    with pytest.raises(PurchasePackageNotFound):
        application.get_package(factory_for(uow), "missing")
    assert uow.events == [("load", "missing"), ("rollback",), ("close",)]

    uow.events.clear()
    failure = RuntimeError("commit outcome unknown")
    uow.commit_error = failure
    with pytest.raises(RuntimeError, match="commit outcome unknown"):
        application.create_package(
            factory_for(uow),
            category="credits",
            name="x",
            price=0,
            credits=None,
            days=None,
            sort_order=0,
            is_active=True,
            id_factory=lambda: "new",
        )
    assert [event[0] for event in uow.events] == ["add", "commit", "rollback", "close"]

    uow.events.clear()
    uow.commit_error = None
    uow.refresh_result = None
    with pytest.raises(RuntimeError, match="could not be refreshed"):
        application.update_package(factory_for(uow), "pkg-1", {})
    assert [event[0] for event in uow.events] == [
        "update", "commit", "refresh", "rollback", "close"
    ]



@pytest.mark.parametrize("phase", ["query", "commit", "refresh"])
def test_primary_errors_survive_rollback_and_close_failures(phase: str) -> None:
    uow = FakeUnitOfWork()
    primary = RuntimeError(f"{phase} failed")
    uow.rollback_error = RuntimeError("rollback failed")
    uow.close_error = RuntimeError("close failed")

    if phase == "query":
        uow.query_error = primary
        call = lambda: application.list_packages(factory_for(uow))
    elif phase == "commit":
        uow.commit_error = primary
        call = lambda: application.create_package(
            factory_for(uow),
            category="credits",
            name="new",
            price=1,
            credits=None,
            days=None,
            sort_order=0,
            is_active=True,
            id_factory=lambda: "new-id",
        )
    else:
        uow.refresh_error = primary
        call = lambda: application.update_package(
            factory_for(uow), "pkg-1", {"name": "updated"}
        )

    with pytest.raises(RuntimeError) as raised:
        call()

    assert raised.value is primary
    assert uow.events[-2:] == [("rollback",), ("close",)]
    assert sum(event[0] == "commit" for event in uow.events) == int(phase != "query")


def test_close_failure_after_success_remains_visible() -> None:
    uow = FakeUnitOfWork()
    close_error = RuntimeError("close failed")
    uow.close_error = close_error

    with pytest.raises(RuntimeError) as raised:
        application.list_packages(factory_for(uow))

    assert raised.value is close_error
    assert not any(event[0] == "rollback" for event in uow.events)
