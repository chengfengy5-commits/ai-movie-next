from __future__ import annotations

import asyncio
from threading import Event

import pytest

from haoai_backend.generation_runtime.worker_capacity import (
    WorkerCapacityBootstrap,
    WorkerExecutionCapacity,
)


def test_worker_capacity_is_fixed_positive_and_closes_claims_without_releasing_active() -> None:
    capacity = WorkerExecutionCapacity()

    assert capacity.try_acquire() is None
    capacity.configure(1)
    first = capacity.try_acquire()

    assert first is not None
    assert capacity.active_count == 1
    assert capacity.try_acquire() is None

    capacity.close_claims()

    assert capacity.is_closed is True
    assert capacity.try_acquire() is None
    assert capacity.active_count == 1
    assert first.release() is True
    assert first.release() is False
    assert capacity.active_count == 0


@pytest.mark.parametrize("invalid_limit", (0, -1, True, 1.5))
def test_worker_capacity_rejects_non_positive_or_non_integer_limits(
    invalid_limit: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        WorkerExecutionCapacity().configure(invalid_limit)  # type: ignore[arg-type]


def test_worker_capacity_cannot_be_reconfigured() -> None:
    capacity = WorkerExecutionCapacity()
    capacity.configure(2)

    with pytest.raises(RuntimeError, match="configured once"):
        capacity.configure(3)


@pytest.mark.parametrize(
    ("database_value", "environment_value", "expected"),
    (
        ("7", "2", 7),
        (" 7 ", "2", 7),
        ("0", "2", 2),
        ("-1", "2", 2),
        ("invalid", "2", 2),
        (None, "0", 100),
        (None, "-1", 100),
        (None, "invalid", 100),
        (None, None, 100),
    ),
)
def test_capacity_bootstrap_uses_normal_database_and_environment_fallbacks(
    database_value: object,
    environment_value: str | None,
    expected: int,
) -> None:
    calls: list[str] = []

    def read_database_value() -> object:
        calls.append("read")
        return database_value

    bootstrap = WorkerCapacityBootstrap(
        read_database_value=read_database_value,
        environment_value=environment_value,
    )

    assert asyncio.run(bootstrap.resolve()) == expected
    assert calls == ["read"]
    assert bootstrap.pending_bridge_count == 0


@pytest.mark.parametrize(
    ("environment_value", "expected"),
    (("8", 8), ("0", 1), ("-3", 1), ("invalid", 100), (None, 100)),
)
def test_capacity_bootstrap_timeout_fallback_preserves_legacy_semantics(
    environment_value: str | None,
    expected: int,
) -> None:
    started = Event()
    finish = Event()
    reader_finished = Event()
    callback_observations: list[tuple[bool, object | None]] = []

    def read_database_value() -> str:
        started.set()
        finish.wait()
        reader_finished.set()
        return "9"

    bootstrap = WorkerCapacityBootstrap(
        read_database_value=read_database_value,
        environment_value=environment_value,
        timeout_seconds=0.01,
    )
    original_consume_bridge = bootstrap._consume_bridge

    def observe_consume_bridge(bridge: asyncio.Task[object]) -> None:
        cancelled = bridge.cancelled()
        result = None if cancelled else bridge.result()
        callback_observations.append((cancelled, result))
        original_consume_bridge(bridge)

    bootstrap._consume_bridge = observe_consume_bridge  # type: ignore[method-assign]

    async def resolve() -> tuple[int, WorkerExecutionCapacity]:
        try:
            selected = await bootstrap.resolve()
            assert started.is_set()
            assert bootstrap.pending_bridge_count == 1
            capacity = WorkerExecutionCapacity()
            capacity.configure(selected)
            assert capacity.limit == expected
            finish.set()

            loop = asyncio.get_running_loop()
            deadline = loop.time() + 1.0
            while bootstrap.pending_bridge_count and loop.time() < deadline:
                await asyncio.sleep(0.005)

            assert reader_finished.is_set()
            assert bootstrap.pending_bridge_count == 0
            assert callback_observations == [(False, "9")]
            assert capacity.limit == expected
            return selected, capacity
        finally:
            finish.set()

    selected, capacity = asyncio.run(resolve())
    assert selected == expected
    assert capacity.limit == expected


def test_capacity_bootstrap_internal_reader_failure_uses_normal_fallback() -> None:
    def fail_read() -> object:
        raise RuntimeError("must not appear in a safe log")

    bootstrap = WorkerCapacityBootstrap(
        read_database_value=fail_read,
        environment_value="0",
    )

    assert asyncio.run(bootstrap.resolve()) == 100


@pytest.mark.parametrize("timeout", (0, -1, float("nan"), float("inf"), True))
def test_capacity_bootstrap_rejects_unsafe_timeout_values(timeout: object) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        WorkerCapacityBootstrap(timeout_seconds=timeout)  # type: ignore[arg-type]
