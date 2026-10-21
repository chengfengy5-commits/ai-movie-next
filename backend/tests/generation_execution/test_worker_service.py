from __future__ import annotations

from threading import Event, Thread
from time import monotonic

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
