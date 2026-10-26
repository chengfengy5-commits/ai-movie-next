from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, Thread
from time import monotonic

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import ClaimAuthority, OwnerTaskScope
from haoai_backend.generation_runtime.configuration import RegisteredExecutorFactory
from haoai_backend.generation_runtime.runtime import build_runtime
from haoai_backend.generation_runtime.worker_capacity import WorkerExecutionCapacity


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
