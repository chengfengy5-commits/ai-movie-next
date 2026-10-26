"""Process-local execution capacity shared by enabled generation workers."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from threading import BoundedSemaphore, Lock

logger = logging.getLogger(__name__)


class WorkerCapacityBootstrap:
    """Resolve one startup limit while preserving bounded-read fallback paths."""

    def __init__(
        self,
        *,
        read_database_value: Callable[[], object] | None = None,
        environment_value: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if read_database_value is not None and not callable(read_database_value):
            raise TypeError("worker capacity reader must be callable")
        if environment_value is not None and not isinstance(environment_value, str):
            raise TypeError("worker concurrency environment value must be text")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("worker DB read timeout must be finite and positive")
        self._read_database_value = read_database_value
        self._environment_value = environment_value
        self._timeout_seconds = float(timeout_seconds)
        self._bridges: set[asyncio.Task[object]] = set()

    @property
    def pending_bridge_count(self) -> int:
        """Expose retained timed-out DB bridges for lifecycle tests and diagnostics."""

        return len(self._bridges)

    async def resolve(self) -> int:
        """Read once; late results are consumed but never alter the chosen limit."""

        if self._read_database_value is None:
            return self._normal_environment_fallback()

        bridge = asyncio.create_task(asyncio.to_thread(self._read_database_value))
        self._bridges.add(bridge)
        bridge.add_done_callback(self._consume_bridge)
        completed, _pending = await asyncio.wait(
            (bridge,), timeout=self._timeout_seconds
        )
        if bridge not in completed:
            logger.warning("Worker capacity DB read timed out; using timeout fallback")
            return self._timeout_environment_fallback()

        try:
            database_value = bridge.result()
        except Exception:
            logger.warning("Worker capacity DB read failed; using environment fallback")
            return self._normal_environment_fallback()
        parsed_database_value = self._positive_integer(database_value)
        if parsed_database_value is not None:
            return parsed_database_value
        return self._normal_environment_fallback()

    def _consume_bridge(self, bridge: asyncio.Task[object]) -> None:
        self._bridges.discard(bridge)
        if bridge.cancelled():
            return
        try:
            bridge.exception()
        except BaseException:
            pass

    @staticmethod
    def _positive_integer(value: object) -> int | None:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _normal_environment_fallback(self) -> int:
        try:
            parsed = int(self._environment_value or "")
        except (TypeError, ValueError):
            return 100
        return parsed if parsed > 0 else 100

    def _timeout_environment_fallback(self) -> int:
        raw = (
            self._environment_value
            if self._environment_value is not None
            else "100"
        )
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return 100
        return max(1, parsed)


class WorkerCapacityLease:
    """One idempotently released slot in the shared worker capacity gate."""

    __slots__ = ("_capacity", "_lock", "_released")

    def __init__(self, capacity: WorkerExecutionCapacity) -> None:
        self._capacity = capacity
        self._lock = Lock()
        self._released = False

    def release(self) -> bool:
        """Return this slot once; repeated cleanup paths are harmless."""

        with self._lock:
            if self._released:
                return False
            self._released = True
        self._capacity._release()
        return True


class WorkerExecutionCapacity:
    """A fixed process-local semaphore initialized once during worker startup."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._semaphore: BoundedSemaphore | None = None
        self._limit: int | None = None
        self._active_count = 0
        self._closed = False

    def configure(self, limit: int) -> None:
        """Set the startup limit exactly once; zero is not a stopped state."""

        if type(limit) is not int or limit <= 0:
            raise ValueError("worker capacity must be a positive integer")
        with self._lock:
            if self._closed or self._semaphore is not None:
                raise RuntimeError("worker capacity can only be configured once")
            self._limit = limit
            self._semaphore = BoundedSemaphore(limit)

    def try_acquire(self) -> WorkerCapacityLease | None:
        """Acquire without waiting so a full worker never claims work to queue."""

        with self._lock:
            semaphore = self._semaphore
            if self._closed or semaphore is None or not semaphore.acquire(blocking=False):
                return None
            self._active_count += 1
            return WorkerCapacityLease(self)

    def close_claims(self) -> None:
        """Close the common claim entrance while active leases remain held."""

        with self._lock:
            self._closed = True

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count

    @property
    def limit(self) -> int | None:
        with self._lock:
            return self._limit

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def _release(self) -> None:
        with self._lock:
            semaphore = self._semaphore
            if semaphore is None or self._active_count <= 0:
                raise RuntimeError("worker capacity lease is not active")
            self._active_count -= 1
            semaphore.release()


__all__ = (
    "WorkerCapacityBootstrap",
    "WorkerCapacityLease",
    "WorkerExecutionCapacity",
)
