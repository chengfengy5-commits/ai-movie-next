"""Bounded server-owned polling lifecycle for an explicitly configured worker."""

from __future__ import annotations

from dataclasses import replace
from math import isfinite
from threading import Event, Lock, Thread, current_thread
from time import monotonic

from haoai_backend.generation_contracts.ports import InertGenerationRuntime
from haoai_backend.generation_contracts.types import ShutdownReceipt


class GenerationWorkerService:
    """Poll one registered task family until shutdown revokes the runtime."""

    def __init__(
        self,
        runtime: InertGenerationRuntime,
        *,
        task_type: str,
        poll_interval_seconds: float,
        shutdown_timeout_seconds: float,
        recovery_scan_interval_seconds: float = 30.0,
    ) -> None:
        if not callable(getattr(runtime, "run_claimed_once", None)) or not callable(
            getattr(runtime, "shutdown", None)
        ):
            raise TypeError("an explicit generation runtime is required")
        if not isinstance(task_type, str) or not task_type:
            raise ValueError("task_type must be non-empty")
        if (
            not isinstance(poll_interval_seconds, (int, float))
            or not isfinite(poll_interval_seconds)
            or poll_interval_seconds <= 0
            or not isinstance(shutdown_timeout_seconds, (int, float))
            or not isfinite(shutdown_timeout_seconds)
            or shutdown_timeout_seconds <= 0
            or isinstance(recovery_scan_interval_seconds, bool)
            or not isinstance(recovery_scan_interval_seconds, (int, float))
            or not isfinite(recovery_scan_interval_seconds)
            or recovery_scan_interval_seconds <= 0
            or recovery_scan_interval_seconds > 30
        ):
            raise ValueError(
                "worker timing values must be positive and recovery scan at most 30s"
            )
        self._runtime = runtime
        self._task_type = task_type
        self._poll_interval_seconds = poll_interval_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._recovery_scan_interval_seconds = recovery_scan_interval_seconds
        self._stop = Event()
        self._recovery_stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._recovery_thread: Thread | None = None
        self._shutdown_receipt: ShutdownReceipt | None = None
        self._runtime_shutdown_called = False
        self._runtime_shutdown_complete = Event()
        self._runtime_shutdown_receipt: ShutdownReceipt | None = None
        self._runtime_shutdown_error: BaseException | None = None
        supports_recovery_scan = getattr(runtime, "supports_recovery_scan", None)
        run_recovery_once = getattr(runtime, "run_recovery_once", None)
        try:
            self._recovery_scan_enabled = bool(
                callable(supports_recovery_scan)
                and callable(run_recovery_once)
                and supports_recovery_scan(task_type) is True
            )
        except Exception:
            self._recovery_scan_enabled = False

    @property
    def is_running(self) -> bool:
        """Report whether the polling thread is alive and has not been shut down."""

        with self._lock:
            return (
                self._thread is not None
                and self._thread.is_alive()
                and not self._stop.is_set()
                and self._shutdown_receipt is None
            )

    def start(self) -> None:
        """Start the explicitly configured polling thread exactly once."""

        with self._lock:
            if self._thread is not None or self._shutdown_receipt is not None:
                raise RuntimeError("generation worker service cannot be restarted")
            poller = Thread(
                target=self._run,
                name=f"generation-worker-{self._task_type}",
                daemon=True,
            )
            scanner = (
                Thread(
                    target=self._run_recovery_scanner,
                    name=f"generation-recovery-{self._task_type}",
                    daemon=True,
                )
                if self._recovery_scan_enabled
                else None
            )
            self._thread = poller
            self._recovery_thread = scanner

        try:
            poller.start()
            if scanner is not None:
                scanner.start()
        except Exception as error:
            self._stop.set()
            self._recovery_stop.set()
            deadline = monotonic() + self._shutdown_timeout_seconds
            try:
                receipt = self._shutdown_runtime_once(
                    "generation worker startup failed"
                )
            except Exception as shutdown_error:
                raise RuntimeError(
                    "generation worker thread could not start and runtime shutdown failed"
                ) from shutdown_error
            receipt = self._join_and_record_live_threads(receipt, deadline)
            with self._lock:
                self._shutdown_receipt = receipt
            raise RuntimeError("generation worker thread could not start") from error

    def shutdown(self, reason: str) -> ShutdownReceipt:
        """Stop polling, revoke active claims, and respect one global time budget."""

        if not reason:
            raise ValueError("shutdown reason must be non-empty")
        with self._lock:
            if self._shutdown_receipt is not None:
                return self._shutdown_receipt
            self._stop.set()
            self._recovery_stop.set()
        deadline = monotonic() + self._shutdown_timeout_seconds
        receipt = self._shutdown_runtime_once(reason)
        receipt = self._join_and_record_live_threads(receipt, deadline)
        with self._lock:
            self._shutdown_receipt = receipt
        return receipt

    def _shutdown_runtime_once(self, reason: str) -> ShutdownReceipt:
        with self._lock:
            owns_shutdown = not self._runtime_shutdown_called
            if owns_shutdown:
                self._runtime_shutdown_called = True
        if not owns_shutdown:
            self._runtime_shutdown_complete.wait()
            with self._lock:
                if self._runtime_shutdown_error is not None:
                    raise self._runtime_shutdown_error
                assert self._runtime_shutdown_receipt is not None
                return self._runtime_shutdown_receipt
        try:
            receipt = self._runtime.shutdown(reason)
        except BaseException as error:
            with self._lock:
                self._runtime_shutdown_error = error
            raise
        else:
            with self._lock:
                self._runtime_shutdown_receipt = receipt
        finally:
            self._runtime_shutdown_complete.set()
        return receipt

    def _join_and_record_live_threads(
        self,
        receipt: ShutdownReceipt,
        deadline: float,
    ) -> ShutdownReceipt:
        threads = (
            (self._thread, f"worker-loop:{self._task_type}"),
            (self._recovery_thread, f"recovery-loop:{self._task_type}"),
        )
        for thread, _resource_ref in threads:
            if (
                thread is not None
                and thread.ident is not None
                and thread is not current_thread()
            ):
                thread.join(timeout=max(0.0, deadline - monotonic()))
        live_refs = tuple(
            resource_ref
            for thread, resource_ref in threads
            if thread is not None and thread.is_alive()
        )
        if not live_refs:
            return receipt
        return replace(
            receipt,
            unreleased_resources=tuple(
                dict.fromkeys((*receipt.unreleased_resources, *live_refs))
            ),
            recovery_plan_refs=tuple(
                dict.fromkeys((*receipt.recovery_plan_refs, *live_refs))
            ),
            timed_out=True,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._runtime.run_claimed_once(self._task_type)
            except Exception:
                # The next bounded poll can recover after transient adapter errors.
                pass
            self._stop.wait(self._poll_interval_seconds)

    def _run_recovery_scanner(self) -> None:
        run_recovery_once = getattr(self._runtime, "run_recovery_once", None)
        if not callable(run_recovery_once):
            return
        while not self._recovery_stop.is_set():
            started_at = monotonic()
            try:
                run_recovery_once(self._task_type)
            except Exception:
                pass
            if self._recovery_stop.is_set():
                return
            next_start = started_at + self._recovery_scan_interval_seconds
            remaining = next_start - monotonic()
            if remaining <= 0:
                remaining = 0.0
            if self._recovery_stop.wait(remaining):
                return


__all__ = ("GenerationWorkerService",)
