"""Resource-free runtime composition boundary for generation workers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from threading import Event, Lock, RLock, Thread
from time import monotonic
from typing import Literal

from haoai_backend.generation_contracts.ports import (
    GenerationRuntimeConfig,
    InertGenerationRuntime,
    WorkerExecutionOutcome,
)
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    OwnerTaskScope,
    ShutdownReceipt,
    TextEvent,
    WebStreamAuthority,
    WebStreamCommand,
)

from .configuration import (
    ExecutorFactory,
    RegisteredExecutorFactory,
    validate_runtime_configuration,
)


@dataclass(slots=True)
class _ActiveClaim:
    port: object
    claim: ClaimAuthority
    heartbeat_stop: Event
    heartbeat: Thread | None = None
    heartbeat_start_complete: Event = field(default_factory=Event)
    heartbeat_started: bool = False
    release_lock: Lock | None = None
    release_complete: Event | None = None
    release_succeeded: bool = False
    release_started: bool = False


@dataclass(slots=True)
class _InertRuntime:
    _config: GenerationRuntimeConfig
    _executor_factories: tuple[ExecutorFactory, ...]

    def run_claimed_once(self, task_type: str) -> WorkerExecutionOutcome:
        """Remain pending until a reviewed claim-acquisition adapter is installed."""

        del task_type
        return "pending"

    async def run_web_stream(
        self,
        command: WebStreamCommand,
        authority: WebStreamAuthority,
    ) -> AsyncIterator[TextEvent]:
        """Report an unavailable executor through the existing error event shape."""

        del command, authority
        yield TextEvent(
            kind="text_error",
            safe_error="Generation execution is not configured.",
        )

    def consume_cancel_and_due(
        self, scope: OwnerTaskScope
    ) -> Literal["settled", "retained", "pending", "terminal_projection"]:
        """Keep business settlement pending until its owner adapter is installed."""

        del scope
        return "pending"

    def shutdown(self, reason: str) -> ShutdownReceipt:
        """Return bounded operational evidence; this inert object owns no resources."""

        if not reason:
            raise ValueError("shutdown reason must be non-empty")
        return ShutdownReceipt(
            reason=reason,
            released_resources=(),
            unreleased_resources=(),
            revoked_claim_refs=(),
            recovery_plan_refs=(),
            timed_out=False,
        )


@dataclass(slots=True)
class _ActiveRecovery:
    task_type: str
    stop_signal: Event
    complete: Event


@dataclass(slots=True)
class _ConfiguredRuntime:
    _config: GenerationRuntimeConfig
    _executor_factories: tuple[ExecutorFactory, ...]
    _instance: str | None = None
    _closed: bool = False
    _active: dict[str, _ActiveClaim] | None = None
    _recoveries: dict[str, object] | None = None
    _active_recoveries: dict[str, _ActiveRecovery] | None = None
    _lock: RLock | None = None

    def __post_init__(self) -> None:
        self._active = {}
        self._recoveries = {}
        self._active_recoveries = {}
        self._lock = RLock()

    def run_claimed_once(self, task_type: str) -> WorkerExecutionOutcome:
        """Claim only an explicitly registered family, then execute it once."""

        if self._closed or not isinstance(task_type, str) or not task_type:
            return "pending"
        factory = next(
            (
                candidate
                for candidate in self._executor_factories
                if isinstance(candidate, RegisteredExecutorFactory)
                and candidate.task_type == task_type
            ),
            None,
        )
        if factory is None:
            return "pending"
        if not self._run_recovery(factory):
            return "pending"
        instance = self._worker_instance()
        if instance is None:
            return "pending"
        try:
            port = factory.execution_port_factory()
            claim = port.claim_next(task_type, instance)
        except Exception:
            return "pending"
        if claim is None:
            return "pending"
        active = _ActiveClaim(
            port,
            claim,
            Event(),
            release_lock=Lock(),
            release_complete=Event(),
        )
        assert self._active is not None and self._lock is not None
        with self._lock:
            rejected_after_claim = self._closed
            if not rejected_after_claim:
                self._active[claim.token] = active
        if rejected_after_claim:
            _signal_lost(active.claim)
            self._release(active)
            return "ownership_lost"
        outcome: WorkerExecutionOutcome = "pending"
        try:
            if not self._start_heartbeat(active) or not self._begin_execution(active):
                return "ownership_lost"
            executor = factory()
            bind_port = getattr(executor, "bind_execution_port", None)
            if callable(bind_port):
                bind_port(port)
            outcome = executor.execute_claim(claim)
        except Exception:
            # A durable intent, if already committed, prevents any later re-POST.
            outcome = "pending"
        finally:
            active.heartbeat_stop.set()
            if active.heartbeat is not None:
                active.heartbeat.join(timeout=self._config.shutdown_timeout_seconds)
            self._release(active)
            with self._lock:
                self._active.pop(claim.token, None)
        return outcome

    def _run_recovery(self, factory: RegisteredExecutorFactory) -> bool:
        recovery = self._recovery_for(factory)
        scan_due = getattr(recovery, "scan_due", None)
        if not callable(scan_due):
            return True
        active = _ActiveRecovery(factory.task_type, Event(), Event())
        assert self._active_recoveries is not None and self._lock is not None
        with self._lock:
            if self._closed or factory.task_type in self._active_recoveries:
                return False
            self._active_recoveries[factory.task_type] = active
        try:
            scan_due(stop_signal=active.stop_signal)
        except Exception:
            pass
        finally:
            active.complete.set()
            with self._lock:
                self._active_recoveries.pop(factory.task_type, None)
                closed = self._closed
        return not active.stop_signal.is_set() and not closed

    def _recovery_for(self, factory: RegisteredExecutorFactory) -> object | None:
        if factory.recovery_factory is None:
            return None
        assert self._recoveries is not None and self._lock is not None
        with self._lock:
            existing = self._recoveries.get(factory.task_type)
            if existing is not None:
                return existing
            try:
                recovery = factory.recovery_factory()
            except Exception:
                return None
            self._recoveries[factory.task_type] = recovery
            return recovery

    async def run_web_stream(
        self,
        command: WebStreamCommand,
        authority: WebStreamAuthority,
    ) -> AsyncIterator[TextEvent]:
        del command, authority
        yield TextEvent(
            kind="text_error",
            safe_error="Generation execution is not configured.",
        )

    def consume_cancel_and_due(
        self, scope: OwnerTaskScope
    ) -> Literal["settled", "retained", "pending", "terminal_projection"]:
        del scope
        return "pending"

    def shutdown(self, reason: str) -> ShutdownReceipt:
        if not reason:
            raise ValueError("shutdown reason must be non-empty")
        assert self._active is not None and self._active_recoveries is not None and self._lock is not None
        with self._lock:
            self._closed = True
            active = tuple(self._active.values())
            recoveries = tuple(self._active_recoveries.values())
        for recovery in recoveries:
            recovery.stop_signal.set()
        timeout = self._config.shutdown_timeout_seconds
        deadline = monotonic() + timeout
        released: list[str] = []
        unreleased: list[str] = []
        heartbeat_unreleased: list[str] = []
        recovery_released: list[str] = []
        recovery_unreleased: list[str] = []
        revoked: list[str] = []
        for item in active:
            item.heartbeat_stop.set()
            _signal_lost(item.claim)
            self._start_release(item)
            revoked.append(
                f"{item.claim.scope.task_id}:{item.claim.execution_generation}"
            )
        for item in active:
            heartbeat = item.heartbeat
            if heartbeat is None:
                continue
            if not item.heartbeat_start_complete.wait(
                timeout=max(0.0, deadline - monotonic())
            ):
                heartbeat_unreleased.append(
                    f"heartbeat:{item.claim.scope.task_id}"
                )
                continue
            if item.heartbeat_started:
                heartbeat.join(timeout=max(0.0, deadline - monotonic()))
                if heartbeat.is_alive():
                    heartbeat_unreleased.append(
                        f"heartbeat:{item.claim.scope.task_id}"
                    )
        for item in active:
            assert item.release_complete is not None
            item.release_complete.wait(timeout=max(0.0, deadline - monotonic()))
            label = f"claim:{item.claim.scope.task_id}"
            if item.release_complete.is_set() and item.release_succeeded:
                released.append(label)
            else:
                unreleased.append(label)
        unreleased.extend(heartbeat_unreleased)
        for recovery in recoveries:
            label = f"recovery:{recovery.task_type}"
            if recovery.complete.wait(timeout=max(0.0, deadline - monotonic())):
                recovery_released.append(label)
            else:
                recovery_unreleased.append(label)
        unreleased.extend(recovery_unreleased)
        return ShutdownReceipt(
            reason=reason,
            released_resources=tuple((*released, *recovery_released)),
            unreleased_resources=tuple(unreleased),
            revoked_claim_refs=tuple(revoked),
            recovery_plan_refs=(
                *(f"task:{item.claim.scope.task_id}" for item in active),
                *(f"recovery:{item.task_type}" for item in recoveries),
            ),
            timed_out=bool(heartbeat_unreleased or recovery_unreleased) or any(
                item.release_complete is None or not item.release_complete.is_set()
                for item in active
            ),
        )

    def _worker_instance(self) -> str | None:
        if self._instance is not None:
            return self._instance
        try:
            value = self._config.id_factory()
        except Exception:
            return None
        if not isinstance(value, str) or not value:
            return None
        self._instance = f"generation-worker:{value}"
        return self._instance

    def _start_heartbeat(self, active: _ActiveClaim) -> bool:
        assert self._lock is not None
        with self._lock:
            if self._closed or active.claim.local_lost_signal.is_set():
                active.heartbeat_stop.set()
                _signal_lost(active.claim)
                return False
            renew = getattr(active.port, "renew_or_revoke", None)
            if not callable(renew):
                return True
            lease_seconds = getattr(active.port, "lease_seconds", 60)
            if type(lease_seconds) is not int or lease_seconds <= 0:
                lease_seconds = 60

        def renew_until_stopped() -> None:
            interval = max(0.1, lease_seconds / 3)
            while not active.heartbeat_stop.wait(interval):
                try:
                    result = renew(active.claim)
                except Exception:
                    result = "ownership_lost"
                if result != "renewed":
                    lost = getattr(active.claim.local_lost_signal, "set", None)
                    if callable(lost):
                        lost()
                    return

        heartbeat = Thread(
            target=renew_until_stopped,
            name=f"generation-lease-{active.claim.execution_generation}",
            daemon=True,
        )
        with self._lock:
            if self._closed or active.claim.local_lost_signal.is_set():
                active.heartbeat_stop.set()
                _signal_lost(active.claim)
                return False
            active.heartbeat = heartbeat
            active.heartbeat_start_complete.clear()
        try:
            heartbeat.start()
        except Exception:
            with self._lock:
                active.heartbeat = None
                active.heartbeat_started = False
                active.heartbeat_start_complete.set()
            _signal_lost(active.claim)
            return False
        with self._lock:
            active.heartbeat_started = True
            active.heartbeat_start_complete.set()
            closing = self._closed
        if closing:
            active.heartbeat_stop.set()
            _signal_lost(active.claim)
        return not closing

    def _begin_execution(self, active: _ActiveClaim) -> bool:
        """Linearize executor startup against shutdown before invoking its factory."""

        assert self._lock is not None
        with self._lock:
            if self._closed or active.claim.local_lost_signal.is_set():
                _signal_lost(active.claim)
                return False
            return True

    def _release(self, active: _ActiveClaim) -> bool:
        self._start_release(active)
        complete = active.release_complete
        if complete is None:
            return False
        complete.wait()
        return active.release_succeeded

    @staticmethod
    def _start_release(active: _ActiveClaim) -> None:
        lock = active.release_lock
        complete = active.release_complete
        if lock is None or complete is None:
            _signal_lost(active.claim)
            return
        with lock:
            if complete.is_set() or active.release_started:
                return
            active.release_started = True

        def release_claim() -> None:
            release = getattr(active.port, "release_claim", None)
            try:
                active.release_succeeded = bool(
                    callable(release) and release(active.claim)
                )
            except Exception:
                active.release_succeeded = False
            finally:
                if not active.release_succeeded:
                    _signal_lost(active.claim)
                complete.set()

        try:
            thread = Thread(
                target=release_claim,
                name=f"generation-release-{active.claim.execution_generation}",
                daemon=True,
            )
            thread.start()
        except Exception:
            _signal_lost(active.claim)
            complete.set()


def _signal_lost(claim: ClaimAuthority) -> None:
    signal = getattr(claim.local_lost_signal, "set", None)
    if callable(signal):
        try:
            signal()
        except Exception:
            pass


def build_runtime(
    explicit_config: GenerationRuntimeConfig,
    executor_factories: Sequence[ExecutorFactory],
) -> InertGenerationRuntime:
    """Build a lazy runtime without invoking any dependency or executor factory."""

    factories = validate_runtime_configuration(explicit_config, executor_factories)
    if not any(isinstance(factory, RegisteredExecutorFactory) for factory in factories):
        return _InertRuntime(explicit_config, factories)
    return _ConfiguredRuntime(explicit_config, factories)


__all__ = ("build_runtime",)
