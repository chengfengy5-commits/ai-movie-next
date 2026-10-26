from __future__ import annotations

from threading import Condition, Event, Thread
from time import monotonic, sleep

import pytest

from haoai_backend.generation_contracts.types import ShutdownReceipt
from haoai_backend.generation_runtime.worker_service import GenerationWorkerService


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = Event()
        self.finish = Event()
        self.finished = Event()
        self.block = False
        self.shutdown_reasons: list[str] = []

    def run_claimed_once(self, task_type: str) -> str:
        self.calls.append(task_type)
        self.started.set()
        if self.block:
            self.finish.wait()
        self.finished.set()
        return "pending"

    def shutdown(self, reason: str) -> ShutdownReceipt:
        self.shutdown_reasons.append(reason)
        return ShutdownReceipt(
            reason=reason,
            released_resources=(),
            unreleased_resources=(),
            revoked_claim_refs=(),
            recovery_plan_refs=(),
            timed_out=False,
        )


class _RecoveryRuntime(_Runtime):
    def __init__(
        self,
        *,
        task_type: str = "image-single",
        scan_delay_seconds: float = 0.0,
        block_scan: bool = False,
    ) -> None:
        super().__init__()
        self.task_type = task_type
        self.scan_delay_seconds = scan_delay_seconds
        self.block_scan = block_scan
        self.scan_finish = Event()
        self.scan_changed = Condition()
        self.recovery_starts: list[float] = []
        self.recovery_active = 0
        self.recovery_max_active = 0

    def supports_recovery_scan(self, task_type: str) -> bool:
        return task_type == self.task_type

    def run_recovery_once(self, task_type: str) -> bool:
        assert task_type == self.task_type
        with self.scan_changed:
            self.recovery_active += 1
            self.recovery_max_active = max(
                self.recovery_max_active,
                self.recovery_active,
            )
            self.recovery_starts.append(monotonic())
            self.scan_changed.notify_all()
        try:
            if self.block_scan:
                self.scan_finish.wait()
            elif self.scan_delay_seconds:
                sleep(self.scan_delay_seconds)
            return True
        finally:
            with self.scan_changed:
                self.recovery_active -= 1
                self.scan_changed.notify_all()

    def wait_for_recovery_count(self, count: int, timeout: float = 1.0) -> bool:
        with self.scan_changed:
            return self.scan_changed.wait_for(
                lambda: len(self.recovery_starts) >= count,
                timeout=timeout,
            )


def _recovery_service(
    runtime: _RecoveryRuntime,
    *,
    interval: float = 0.2,
    timeout: float = 0.5,
    task_type: str | None = None,
) -> GenerationWorkerService:
    selected_task_type = task_type or runtime.task_type
    return GenerationWorkerService(
        runtime,
        task_type=selected_task_type,
        poll_interval_seconds=1.0,
        shutdown_timeout_seconds=timeout,
        recovery_scan_interval_seconds=interval,
    )


def _service(runtime: _Runtime, timeout: float = 0.5) -> GenerationWorkerService:
    return GenerationWorkerService(
        runtime,
        task_type="image-single",
        poll_interval_seconds=0.01,
        shutdown_timeout_seconds=timeout,
    )


def test_worker_service_is_explicit_and_stops_polling_on_shutdown() -> None:
    runtime = _Runtime()
    service = _service(runtime)

    assert runtime.calls == []
    assert service.is_running is False
    service.start()
    assert runtime.started.wait(timeout=1.0)
    assert service.is_running is True

    receipt = service.shutdown("test shutdown")
    assert service.is_running is False

    call_count = len(runtime.calls)
    assert call_count >= 1
    assert runtime.shutdown_reasons == ["test shutdown"]
    assert receipt.reason == "test shutdown"
    assert receipt.timed_out is False
    assert receipt.unreleased_resources == ()
    assert service.shutdown("duplicate shutdown") == receipt
    assert len(runtime.calls) == call_count


def test_worker_service_shutdown_uses_bounded_join_for_a_blocked_run() -> None:
    runtime = _Runtime()
    runtime.block = True
    service = _service(runtime, timeout=0.05)
    service.start()
    assert runtime.started.wait(timeout=1.0)

    began = monotonic()
    receipt = service.shutdown("bounded test")
    elapsed = monotonic() - began

    assert elapsed < 0.5
    assert receipt.timed_out is True
    assert receipt.unreleased_resources == ("worker-loop:image-single",)
    assert receipt.recovery_plan_refs == ("worker-loop:image-single",)

    runtime.finish.set()
    deadline = monotonic() + 1.0
    while monotonic() < deadline and not runtime.finished.is_set():
        runtime.finished.wait(0.01)
    assert runtime.finished.is_set()


def test_worker_thread_start_failure_closes_runtime_without_polling(monkeypatch) -> None:
    runtime = _Runtime()

    def fail_start(_thread: Thread) -> None:
        raise RuntimeError("injected start failure")

    monkeypatch.setattr(Thread, "start", fail_start)
    service = _service(runtime)

    with pytest.raises(RuntimeError, match="thread could not start"):
        service.start()

    assert runtime.calls == []
    assert runtime.shutdown_reasons == ["generation worker startup failed"]


@pytest.mark.parametrize("shutdown_raises", (False, True))
def test_concurrent_shutdown_reuses_one_in_progress_runtime_shutdown(
    monkeypatch,
    shutdown_raises: bool,
) -> None:
    shutdown_entered = Event()
    release_shutdown = Event()
    second_shutdown_entered = Event()
    first_finished = Event()
    second_finished = Event()
    outcomes: dict[str, ShutdownReceipt] = {}
    errors: dict[str, BaseException] = {}

    class BlockingShutdownRuntime(_Runtime):
        def shutdown(self, reason: str) -> ShutdownReceipt:
            self.shutdown_reasons.append(reason)
            shutdown_entered.set()
            assert release_shutdown.wait(timeout=2.0)
            if shutdown_raises:
                raise RuntimeError("synthetic runtime shutdown failure")
            return ShutdownReceipt(
                reason=reason,
                released_resources=(),
                unreleased_resources=(),
                revoked_claim_refs=(),
                recovery_plan_refs=(),
                timed_out=False,
            )

    runtime = BlockingShutdownRuntime()
    service = _service(runtime)
    original_shutdown_once = service._shutdown_runtime_once

    def observe_second_shutdown(reason: str) -> ShutdownReceipt:
        if reason == "second concurrent shutdown":
            second_shutdown_entered.set()
        return original_shutdown_once(reason)

    monkeypatch.setattr(service, "_shutdown_runtime_once", observe_second_shutdown)

    def invoke_shutdown(label: str, reason: str, finished: Event) -> None:
        try:
            outcomes[label] = service.shutdown(reason)
        except BaseException as error:
            errors[label] = error
        finally:
            finished.set()

    first = Thread(
        target=invoke_shutdown,
        args=("first", "first concurrent shutdown", first_finished),
        daemon=True,
    )
    second = Thread(
        target=invoke_shutdown,
        args=("second", "second concurrent shutdown", second_finished),
        daemon=True,
    )
    first.start()
    try:
        assert shutdown_entered.wait(timeout=1.0)
        second.start()
        assert second_shutdown_entered.wait(timeout=1.0)
        assert not second_finished.wait(timeout=0.05)
    finally:
        release_shutdown.set()
        first.join(timeout=1.0)
        if second.ident is not None:
            second.join(timeout=1.0)

    assert first_finished.is_set()
    assert second_finished.is_set()
    assert not first.is_alive()
    assert not second.is_alive()
    assert runtime.shutdown_reasons == ["first concurrent shutdown"]
    if shutdown_raises:
        assert outcomes == {}
        assert set(errors) == {"first", "second"}
        assert errors["first"] is errors["second"]
        assert isinstance(errors["first"], RuntimeError)
    else:
        assert errors == {}
        assert set(outcomes) == {"first", "second"}
        assert outcomes["first"] == outcomes["second"]
        assert outcomes["first"].reason == "first concurrent shutdown"


@pytest.mark.parametrize(
    "interval",
    (0, -1, 30.01, float("nan"), float("inf"), True),
)
def test_worker_service_recovery_interval_must_be_positive_finite_and_at_most_30(
    interval: object,
) -> None:
    runtime = _RecoveryRuntime()
    with pytest.raises(ValueError, match="recovery scan at most 30s"):
        GenerationWorkerService(
            runtime,
            task_type="image-single",
            poll_interval_seconds=0.1,
            shutdown_timeout_seconds=0.1,
            recovery_scan_interval_seconds=interval,  # type: ignore[arg-type]
        )


def test_recovery_scanner_starts_immediately_and_deducts_scan_duration_from_period() -> None:
    runtime = _RecoveryRuntime(scan_delay_seconds=0.1)
    service = _recovery_service(runtime, interval=0.25)
    service_started = monotonic()
    service.start()

    try:
        assert runtime.wait_for_recovery_count(3, timeout=1.0)
    finally:
        receipt = service.shutdown("stop cadence test")

    starts = runtime.recovery_starts[:3]
    intervals = (starts[1] - starts[0], starts[2] - starts[1])
    assert starts[0] - service_started < 0.1
    assert all(0.2 <= interval <= 0.34 for interval in intervals)
    assert runtime.recovery_max_active == 1
    assert receipt.timed_out is False
    assert runtime.shutdown_reasons == ["stop cadence test"]


def test_recovery_scanners_are_independent_per_family_and_shutdown_reports_live_scan() -> None:
    blocked = _RecoveryRuntime(block_scan=True)
    available = _RecoveryRuntime(task_type="ai-review")
    blocked_service = _recovery_service(blocked, interval=0.1, timeout=0.05)
    available_service = _recovery_service(
        available,
        interval=0.1,
        timeout=0.2,
        task_type="ai-review",
    )
    blocked_service.start()

    try:
        assert blocked.wait_for_recovery_count(1)
        available_service.start()
        assert available.wait_for_recovery_count(1)
        receipt = blocked_service.shutdown("stop blocked recovery")
        assert receipt.timed_out is True
        assert receipt.unreleased_resources == ("recovery-loop:image-single",)
        assert receipt.recovery_plan_refs == ("recovery-loop:image-single",)
        assert available_service.shutdown("stop available recovery").timed_out is False
    finally:
        blocked.scan_finish.set()
        for service in (blocked_service, available_service):
            scanner = getattr(service, "_recovery_thread", None)
            if scanner is not None:
                scanner.join(timeout=1.0)

    assert blocked_service._recovery_thread is not None
    assert not blocked_service._recovery_thread.is_alive()
    assert available_service._recovery_thread is not None
    assert not available_service._recovery_thread.is_alive()
    assert blocked.recovery_starts == [blocked.recovery_starts[0]]
    assert blocked.shutdown_reasons == ["stop blocked recovery"]
    assert available.shutdown_reasons == ["stop available recovery"]


def test_scanner_start_failure_stops_poller_and_shuts_down_runtime_once(monkeypatch) -> None:
    runtime = _RecoveryRuntime()
    service = _recovery_service(runtime)
    real_start = Thread.start

    def fail_scanner_start(thread: Thread) -> None:
        if thread.name == "generation-recovery-image-single":
            raise RuntimeError("injected scanner thread start failure")
        real_start(thread)

    monkeypatch.setattr(Thread, "start", fail_scanner_start)

    with pytest.raises(RuntimeError, match="worker thread could not start"):
        service.start()

    poller = service._thread
    scanner = service._recovery_thread
    assert poller is not None and not poller.is_alive()
    assert scanner is not None and not scanner.is_alive()
    assert runtime.shutdown_reasons == ["generation worker startup failed"]
    receipt = service.shutdown("startup cleanup replay")
    assert receipt.reason == "generation worker startup failed"
    assert runtime.shutdown_reasons == ["generation worker startup failed"]
