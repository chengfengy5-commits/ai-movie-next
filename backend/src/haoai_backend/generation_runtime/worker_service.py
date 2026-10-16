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
        ):
            raise ValueError("worker timing values must be positive")
        self._runtime = runtime
        self._task_type = task_type
        self._poll_interval_seconds = poll_interval_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._shutdown_receipt: ShutdownReceipt | None = None

    def start(self) -> None:
        """Start the explicitly configured polling thread exactly once."""

        with self._lock:
            if self._thread is not None or self._shutdown_receipt is not None:
                raise RuntimeError("generation worker service cannot be restarted")
            thread = Thread(
                target=self._run,
                name=f"generation-worker-{self._task_type}",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception as error:
                self._thread = None
                self._stop.set()
                try:
                    self._shutdown_receipt = self._runtime.shutdown(
                        "generation worker startup failed"
                    )
                except Exception:
                    self._shutdown_receipt = None
                raise RuntimeError("generation worker thread could not start") from error

    def shutdown(self, reason: str) -> ShutdownReceipt:
        """Stop polling, revoke active claims, and respect one global time budget."""

        if not reason:
            raise ValueError("shutdown reason must be non-empty")
        with self._lock:
            if self._shutdown_receipt is not None:
                return self._shutdown_receipt
            self._stop.set()
            thread = self._thread
        deadline = monotonic() + self._shutdown_timeout_seconds
        receipt = self._runtime.shutdown(reason)
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(0.0, deadline - monotonic()))
        if thread is not None and thread.is_alive():
            worker_ref = f"worker-loop:{self._task_type}"
            receipt = replace(
                receipt,
                unreleased_resources=tuple(
                    dict.fromkeys((*receipt.unreleased_resources, worker_ref))
                ),
                recovery_plan_refs=tuple(
                    dict.fromkeys((*receipt.recovery_plan_refs, worker_ref))
                ),
                timed_out=True,
            )
        with self._lock:
            self._shutdown_receipt = receipt
        return receipt

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._runtime.run_claimed_once(self._task_type)
            except Exception:
                # The next bounded poll can recover after transient adapter errors.
                pass
            self._stop.wait(self._poll_interval_seconds)


__all__ = ("GenerationWorkerService",)
