from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, Thread, current_thread
from time import monotonic

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import ClaimAuthority, OwnerTaskScope
from haoai_backend.generation_runtime.configuration import RegisteredExecutorFactory
from haoai_backend.generation_runtime.runtime import build_runtime
from haoai_backend.generation_runtime.worker_capacity import WorkerExecutionCapacity
from haoai_backend.generation_runtime.worker_service import GenerationWorkerService


class _Signal:
    def __init__(self) -> None:
        self._event = Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()


class _Port:
    lease_seconds = 60

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.release_gate: Event | None = None
        self.release_result = True
        self.claim = ClaimAuthority(
            scope=OwnerTaskScope("owner", "task", "image.single"),
            worker_instance="worker",
            token="claim-token",
            execution_generation=1,
            lease_until=datetime(2026, 10, 16, tzinfo=timezone.utc),
            local_lost_signal=_Signal(),
        )

    def claim_next(self, task_type: str, instance: str) -> ClaimAuthority:
        self.events.append(f"claim:{task_type}:{instance}")
        return self.claim

    def renew_or_revoke(self, claim: ClaimAuthority) -> str:
        return "renewed"

    def release_claim(self, claim: ClaimAuthority) -> bool:
        self.events.append("release")
        if self.release_gate is not None:
            self.release_gate.wait()
        return self.release_result


class _Executor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def bind_execution_port(self, port: object) -> None:
        self.events.append("bind")

    def execute_claim(self, claim: ClaimAuthority) -> str:
        self.events.append("execute")
        return "pending"


def _config(events: list[str], *, shutdown_timeout: float = 10.0) -> GenerationRuntimeConfig:
    def unused_factory(name: str):
        return lambda: events.append(name)

    return GenerationRuntimeConfig(
        session_factory=unused_factory("session"),
        db_clock=lambda: datetime(2026, 10, 16, tzinfo=timezone.utc),
        id_factory=lambda: "instance-id",
        http_client_factory=unused_factory("http"),
        credential_resolver=lambda _binding, _purpose: None,
        provider_factories=(),
        media_store_factory=unused_factory("media"),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=unused_factory("fetcher"),
        bound_provider_content_fetcher_factory=unused_factory("bound-fetcher"),
        local_muse_capability_factory=unused_factory("muse"),
        r2_storage_factory=unused_factory("storage"),
        shutdown_timeout_seconds=shutdown_timeout,
    )


def test_build_is_inert_and_only_registered_task_type_can_claim() -> None:
    events: list[str] = []
    port = _Port(events)
    executor_factory_calls = 0
    port_factory_calls = 0

    def port_factory() -> _Port:
        nonlocal port_factory_calls
        port_factory_calls += 1
        events.append("port-factory")
        return port

    def executor_factory() -> _Executor:
        nonlocal executor_factory_calls
        executor_factory_calls += 1
        events.append("executor-factory")
        return _Executor(events)

    runtime = build_runtime(
        _config(events),
        (RegisteredExecutorFactory("image-single", port_factory, executor_factory),),
    )
    assert events == []
    assert runtime.run_claimed_once("batch-image") == "pending"
    assert events == []
    assert port_factory_calls == executor_factory_calls == 0

    assert runtime.run_claimed_once("image-single") == "pending"
    assert events == [
        "port-factory",
        "claim:image-single:generation-worker:instance-id",
        "executor-factory",
        "bind",
        "execute",
        "release",
    ]
    assert port_factory_calls == executor_factory_calls == 1

    receipt = runtime.shutdown("test complete")
    assert receipt.released_resources == ()


class _Recovery:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def scan_due(self, *, stop_signal: Event) -> None:
        assert not stop_signal.is_set()
        self.events.append("recovery")


def test_two_families_share_capacity_and_never_claim_while_full() -> None:
    capacity = WorkerExecutionCapacity()
    capacity.configure(1)
    first_events: list[str] = []
    second_events: list[str] = []
    first_port = _Port(first_events)
    second_port = _Port(second_events)
    first_started = Event()
    first_finish = Event()
    first_outcomes: list[str] = []

    first_runtime = build_runtime(
        replace(
            _config(first_events, shutdown_timeout=0.05),
            worker_capacity=capacity,
        ),
        (
            RegisteredExecutorFactory(
                "image-single",
                lambda: first_port,
                lambda: _BlockingExecutor(first_started, first_finish),
                recovery_factory=lambda: _Recovery(first_events),
            ),
        ),
    )
    second_runtime = build_runtime(
        replace(_config(second_events), worker_capacity=capacity),
        (
            RegisteredExecutorFactory(
                "ai-review",
                lambda: second_port,
                lambda: _Executor(second_events),
                recovery_factory=lambda: _Recovery(second_events),
            ),
        ),
    )
    first = Thread(
        target=lambda: first_outcomes.append(
            first_runtime.run_claimed_once("image-single")
        ),
        daemon=True,
    )

    try:
        first.start()
        assert first_started.wait(timeout=1.0)
        first_active_count = capacity.active_count
        second_outcome = second_runtime.run_claimed_once("ai-review")
        second_claims = [event for event in second_events if event.startswith("claim:")]
        assert (first_active_count, second_outcome, second_claims) == (1, "pending", [])
        assert second_events == ["recovery"]

        shutdown_started = monotonic()
        first_shutdown = first_runtime.shutdown("shutdown while executor is blocked")
        shutdown_elapsed = monotonic() - shutdown_started

        assert shutdown_elapsed < 0.5
        assert first.is_alive()
        assert capacity.active_count == 1
        assert first_shutdown.revoked_claim_refs == ("task:1",)
        assert second_runtime.run_claimed_once("ai-review") == "pending"
        assert not any(event.startswith("claim:") for event in second_events)
        assert capacity.active_count == 1
    finally:
        first_finish.set()
        first.join(timeout=1.0)

    assert not first.is_alive()
    assert first_outcomes == ["pending"]
    assert capacity.active_count == 0
    assert first_events.count("release") == 1
    assert second_runtime.run_claimed_once("ai-review") == "pending"
    assert any(event.startswith("claim:ai-review:") for event in second_events)
    assert capacity.active_count == 0
    second_runtime.shutdown("test complete")


def test_independent_due_scanner_runs_while_executor_holds_full_capacity(
    monkeypatch,
) -> None:
    executor_started = Event()
    executor_finish = Event()
    recovery_during_executor = Event()
    events: list[str] = []
    counters = {"capacity": 0, "executor": 0, "id": 0, "provider": 0}

    class CountingCapacity(WorkerExecutionCapacity):
        def try_acquire(self):
            counters["capacity"] += 1
            return super().try_acquire()

    class ObservedRecovery:
        def __init__(self) -> None:
            self.calls = 0

        def scan_due(self, *, stop_signal: Event) -> None:
            assert not stop_signal.is_set()
            self.calls += 1
            if executor_started.is_set():
                recovery_during_executor.set()

    class BlockingExecutor:
        def bind_execution_port(self, _port: object) -> None:
            return None

        def execute_claim(self, _claim: ClaimAuthority) -> str:
            executor_started.set()
            executor_finish.wait()
            return "pending"

    capacity = CountingCapacity()
    capacity.configure(1)
    recovery = ObservedRecovery()
    port = _Port(events)

    def new_worker_id() -> str:
        counters["id"] += 1
        return "instance-id"

    def unused_provider_factory() -> object:
        counters["provider"] += 1
        return object()

    def build_executor() -> BlockingExecutor:
        counters["executor"] += 1
        return BlockingExecutor()

    runtime = build_runtime(
        replace(
            _config(events, shutdown_timeout=0.05),
            worker_capacity=capacity,
            id_factory=new_worker_id,
            provider_factories=(("unused", unused_provider_factory),),
        ),
        (
            RegisteredExecutorFactory(
                "image-single",
                lambda: port,
                build_executor,
                recovery_factory=lambda: recovery,
            ),
        ),
    )
    service = GenerationWorkerService(
        runtime,
        task_type="image-single",
        poll_interval_seconds=0.01,
        shutdown_timeout_seconds=0.05,
    )
    real_start = Thread.start

    def start_scanner_after_executor(thread: Thread) -> None:
        if thread.name == "generation-recovery-image-single":
            assert executor_started.wait(timeout=1.0)
        real_start(thread)

    monkeypatch.setattr(Thread, "start", start_scanner_after_executor)

    try:
        service.start()
        assert executor_started.wait(timeout=1.0)
        assert recovery_during_executor.wait(timeout=0.25)
        assert recovery.calls >= 2
        assert capacity.active_count == 1
        assert counters == {"capacity": 1, "executor": 1, "id": 1, "provider": 0}
        assert sum(event.startswith("claim:") for event in events) == 1
    finally:
        service.shutdown("stop independent scanner regression")
        executor_finish.set()
        poller = getattr(service, "_thread", None)
        scanner = getattr(service, "_recovery_thread", None)
        if poller is not None:
            poller.join(timeout=1.0)
        if scanner is not None:
            scanner.join(timeout=1.0)

    assert poller is None or not poller.is_alive()
    assert scanner is None or not scanner.is_alive()
    assert capacity.active_count == 0


def test_recovery_scanner_only_uses_registered_recovery_without_claim_adapters() -> None:
    events: list[str] = []
    counters = {"capacity": 0, "id": 0, "port": 0, "executor": 0, "provider": 0}

    class CountingCapacity:
        def try_acquire(self):
            counters["capacity"] += 1
            return None

    recovery = _Recovery(events)

    def unused_provider_factory() -> object:
        counters["provider"] += 1
        return object()

    def unused_worker_id() -> str:
        counters["id"] += 1
        return "worker-id"

    def port_factory() -> _Port:
        counters["port"] += 1
        return _Port(events)

    def executor_factory() -> _Executor:
        counters["executor"] += 1
        return _Executor(events)

    runtime = build_runtime(
        replace(
            _config(events),
            worker_capacity=CountingCapacity(),
            id_factory=unused_worker_id,
            provider_factories=(("unused", unused_provider_factory),),
        ),
        (
            RegisteredExecutorFactory(
                "image-single",
                port_factory,
                executor_factory,
                recovery_factory=lambda: recovery,
            ),
            RegisteredExecutorFactory(
                "without-recovery",
                port_factory,
                executor_factory,
            ),
        ),
    )

    assert runtime.supports_recovery_scan("image-single") is True
    assert runtime.supports_recovery_scan("without-recovery") is False
    assert runtime.supports_recovery_scan("unknown") is False
    assert runtime.run_recovery_once("image-single") is True
    assert runtime.run_recovery_once("without-recovery") is False
    assert runtime.run_recovery_once("unknown") is False
    assert events == ["recovery"]
    assert counters == {"capacity": 0, "id": 0, "port": 0, "executor": 0, "provider": 0}

    inert = build_runtime(_config(events), ())
    assert inert.supports_recovery_scan("image-single") is False
    assert inert.run_recovery_once("image-single") is False
    runtime.shutdown("test complete")
    inert.shutdown("test complete")


def test_inline_timer_and_explicit_recovery_share_one_tracked_guard() -> None:
    scan_started = Event()
    release_scan = Event()
    active = 0
    maximum_active = 0
    cancel_calls = 0
    events: list[str] = []

    class BlockingRecovery:
        def scan_due(self, *, stop_signal: Event) -> None:
            nonlocal active, maximum_active
            assert not stop_signal.is_set()
            active += 1
            maximum_active = max(maximum_active, active)
            scan_started.set()
            release_scan.wait()
            active -= 1

        def consume_cancel_and_due(self, scope, *, stop_signal: Event) -> str:
            nonlocal cancel_calls
            cancel_calls += 1
            return "pending"

    capacity = WorkerExecutionCapacity()
    capacity.configure(1)
    recovery = BlockingRecovery()
    port = _Port(events)
    outcomes: list[bool] = []
    runtime = build_runtime(
        replace(_config(events), worker_capacity=capacity),
        (
            RegisteredExecutorFactory(
                "ai-review",
                lambda: port,
                lambda: _Executor(events),
                recovery_factory=lambda: recovery,
            ),
        ),
    )
    timer = Thread(
        target=lambda: outcomes.append(runtime.run_recovery_once("ai-review")),
        daemon=True,
    )

    try:
        timer.start()
        assert scan_started.wait(timeout=1.0)
        assert runtime.run_claimed_once("ai-review") == "pending"
        assert (
            runtime.consume_cancel_and_due(
                OwnerTaskScope("owner", "task", "frame.review")
            )
            == "pending"
        )
        assert active == 1
        assert maximum_active == 1
        assert cancel_calls == 0
        assert capacity.active_count == 0
        assert not any(event.startswith("claim:") for event in events)
    finally:
        release_scan.set()
        timer.join(timeout=1.0)

    assert not timer.is_alive()
    assert outcomes == [True]
    assert active == 0
    assert maximum_active == 1
    assert cancel_calls == 0
    runtime.shutdown("test complete")


def test_scanner_shutdown_reports_tracked_recovery_and_live_scanner_until_unwind() -> None:
    scanner_entered = Event()
    release_scanner = Event()
    inline_scans = 0
    events: list[str] = []

    class StopIgnoringRecovery:
        def scan_due(self, *, stop_signal: Event) -> None:
            nonlocal inline_scans
            if current_thread().name == "generation-recovery-image-single":
                scanner_entered.set()
                release_scanner.wait()
                return
            inline_scans += 1

    class EmptyPort:
        def claim_next(self, _task_type: str, _instance: str):
            return None

    def unexpected_executor() -> _Executor:
        raise AssertionError("scanner regression must not build an executor")

    recovery = StopIgnoringRecovery()
    runtime = build_runtime(
        _config([], shutdown_timeout=0.05),
        (
            RegisteredExecutorFactory(
                "image-single",
                EmptyPort,
                unexpected_executor,
                recovery_factory=lambda: recovery,
            ),
        ),
    )
    service = GenerationWorkerService(
        runtime,
        task_type="image-single",
        poll_interval_seconds=1.0,
        shutdown_timeout_seconds=0.05,
        recovery_scan_interval_seconds=0.01,
    )

    service.start()
    assert scanner_entered.wait(timeout=1.0)
    receipt = service.shutdown("stop blocked scanner")

    assert inline_scans <= 1
    assert receipt.timed_out is True
    assert "recovery:image-single" in receipt.unreleased_resources
    assert "recovery-loop:image-single" in receipt.unreleased_resources
    assert "recovery:image-single" in receipt.recovery_plan_refs
    assert "recovery-loop:image-single" in receipt.recovery_plan_refs

    release_scanner.set()
    scanner = service._recovery_thread
    assert scanner is not None
    scanner.join(timeout=1.0)
    assert not scanner.is_alive()
    assert service.shutdown("duplicate stop") == receipt



class _BlockingExecutor:
    def __init__(self, started: Event, finish: Event) -> None:
        self.started = started
        self.finish = finish

    def bind_execution_port(self, port: object) -> None:
        return None

    def execute_claim(self, claim: ClaimAuthority) -> str:
        self.started.set()
        self.finish.wait()
        return "pending"


def test_heartbeat_thread_start_failure_releases_claim_without_executor(
    monkeypatch,
) -> None:
    events: list[str] = []
    port = _Port(events)
    real_start = Thread.start
    executor_calls = 0

    def fail_heartbeat_start(thread: Thread) -> None:
        if thread.name.startswith("generation-lease-"):
            raise RuntimeError("injected heartbeat start failure")
        real_start(thread)

    def executor_factory() -> _Executor:
        nonlocal executor_calls
        executor_calls += 1
        return _Executor(events)

    monkeypatch.setattr(Thread, "start", fail_heartbeat_start)
    runtime = build_runtime(
        _config(events),
        (RegisteredExecutorFactory("image-single", lambda: port, executor_factory),),
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome == "ownership_lost"
    assert executor_calls == 0
    assert events == [
        "claim:image-single:generation-worker:instance-id",
        "release",
    ]
    assert port.claim.local_lost_signal.is_set()


def test_shutdown_during_heartbeat_start_is_bounded_and_reports_unreleased_claim(
    monkeypatch,
) -> None:
    events: list[str] = []
    port = _Port(events)
    release_gate = Event()
    release_done = Event()
    port.release_gate = release_gate
    real_release = port.release_claim

    def gated_release(claim: ClaimAuthority) -> bool:
        try:
            return real_release(claim)
        finally:
            release_done.set()

    port.release_claim = gated_release  # type: ignore[method-assign]
    heartbeat_start_entered = Event()
    allow_heartbeat_start = Event()
    real_start = Thread.start
    executor_calls = 0

    def gated_start(thread: Thread) -> None:
        if thread.name.startswith("generation-lease-"):
            heartbeat_start_entered.set()
            if not allow_heartbeat_start.wait(timeout=2.0):
                raise TimeoutError("heartbeat start gate was not released")
        real_start(thread)

    def executor_factory() -> _Executor:
        nonlocal executor_calls
        executor_calls += 1
        return _Executor(events)

    monkeypatch.setattr(Thread, "start", gated_start)
    runtime = build_runtime(
        _config(events, shutdown_timeout=0.05),
        (RegisteredExecutorFactory("image-single", lambda: port, executor_factory),),
    )
    outcomes: list[str] = []
    runner = Thread(
        target=lambda: outcomes.append(runtime.run_claimed_once("image-single")),
        daemon=True,
    )

    try:
        runner.start()
        assert heartbeat_start_entered.wait(timeout=1.0)

        began = monotonic()
        receipt = runtime.shutdown("shutdown during heartbeat start")
        elapsed = monotonic() - began

        assert elapsed < 0.5
        assert receipt.timed_out is True
        assert receipt.released_resources == ()
        assert receipt.unreleased_resources == (
            "claim:task",
            "heartbeat:task",
        )
        assert receipt.revoked_claim_refs == ("task:1",)
        assert receipt.recovery_plan_refs == ("task:task",)
        assert port.claim.local_lost_signal.is_set()
        assert executor_calls == 0
        assert events.count("release") == 1
    finally:
        allow_heartbeat_start.set()
        release_gate.set()
        runner.join(timeout=1.0)

    assert not runner.is_alive()
    assert release_done.is_set()
    assert events.count("release") == 1
    assert outcomes == ["ownership_lost"]


def test_shutdown_uses_one_global_budget_for_blocking_release_and_marks_local_loss() -> None:
    events: list[str] = []
    port = _Port(events)
    release_gate = Event()
    port.release_gate = release_gate
    started = Event()
    finish = Event()
    runtime = build_runtime(
        _config(events, shutdown_timeout=0.05),
        (
            RegisteredExecutorFactory(
                "image-single",
                lambda: port,
                lambda: _BlockingExecutor(started, finish),
            ),
        ),
    )
    outcome: list[str] = []
    runner = Thread(
        target=lambda: outcome.append(runtime.run_claimed_once("image-single")),
        daemon=True,
    )
    runner.start()
    assert started.wait(timeout=1.0)

    began = monotonic()
    receipt = runtime.shutdown("bounded test")
    elapsed = monotonic() - began

    assert elapsed < 0.5
    assert receipt.timed_out is True
    assert receipt.released_resources == ()
    assert receipt.unreleased_resources == ("claim:task",)
    assert receipt.revoked_claim_refs == ("task:1",)
    assert receipt.recovery_plan_refs == ("task:task",)
    assert port.claim.local_lost_signal.is_set()

    release_gate.set()
    finish.set()
    runner.join(timeout=1.0)
    assert not runner.is_alive()
    assert outcome == ["pending"]


def test_shutdown_reports_release_failure_as_lost_and_recoverable() -> None:
    events: list[str] = []
    port = _Port(events)
    port.release_result = False
    started = Event()
    finish = Event()
    runtime = build_runtime(
        _config(events, shutdown_timeout=0.5),
        (
            RegisteredExecutorFactory(
                "image-single",
                lambda: port,
                lambda: _BlockingExecutor(started, finish),
            ),
        ),
    )
    runner = Thread(
        target=lambda: runtime.run_claimed_once("image-single"),
        daemon=True,
    )
    runner.start()
    assert started.wait(timeout=1.0)

    receipt = runtime.shutdown("release failure test")

    assert receipt.timed_out is False
    assert receipt.released_resources == ()
    assert receipt.unreleased_resources == ("claim:task",)
    assert receipt.revoked_claim_refs == ("task:1",)
    assert receipt.recovery_plan_refs == ("task:task",)
    assert port.claim.local_lost_signal.is_set()
    finish.set()
    runner.join(timeout=1.0)
    assert not runner.is_alive()
