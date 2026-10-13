"""Bounded database and object-storage readiness checks."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from .configuration import RuntimeStatusConfiguration
from .ports import DatabaseReadinessProbe, ObjectStorageReadinessProbe

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReadinessSnapshot:
    ready: bool
    reason: str
    retry_after: float = 0.0


class ReadinessGate:
    """Run the legacy readiness sequence through injected lazy probe ports."""

    def __init__(
        self,
        configuration: RuntimeStatusConfiguration,
        *,
        database_probe: DatabaseReadinessProbe | None,
        object_storage_probe: ObjectStorageReadinessProbe | None,
    ) -> None:
        self._configuration = configuration
        self._database_probe = database_probe
        self._object_storage_probe = object_storage_probe
        self._snapshot: ReadinessSnapshot | None = None
        self._next_probe_at = 0.0
        self._failure_backoff = configuration.initial_failure_backoff_seconds
        self._probe_lock: asyncio.Lock | None = None

    def _failure_snapshot(self, reason: str) -> ReadinessSnapshot:
        return ReadinessSnapshot(
            ready=False,
            reason=reason,
            retry_after=max(0.0, self._next_probe_at - time.monotonic()),
        )

    async def _probe_once(self) -> ReadinessSnapshot:
        if not self._configuration.object_storage_configured:
            return ReadinessSnapshot(False, "object_storage")

        try:
            if self._database_probe is None:
                raise RuntimeError("database readiness port is not configured")
            await asyncio.wait_for(
                asyncio.to_thread(self._database_probe),
                timeout=self._configuration.probe_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "object_storage stage=database media_type=database bytes=0 "
                "duration_ms=0 attempts=1 error_code=database_unavailable"
            )
            return self._failure_snapshot("database")

        try:
            if self._object_storage_probe is None:
                raise RuntimeError("object-storage readiness port is not configured")
            await asyncio.wait_for(
                self._object_storage_probe(),
                timeout=self._configuration.probe_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "object_storage stage=head_bucket media_type=bucket bytes=0 "
                "duration_ms=0 attempts=1 error_code=provider_unavailable"
            )
            return self._failure_snapshot("object_storage")

        return ReadinessSnapshot(True, "ready")

    async def check(self) -> ReadinessSnapshot:
        """Return the cached result or run one bounded, read-only probe cycle."""

        if not self._configuration.readiness_initialized:
            return ReadinessSnapshot(True, "uninitialized")

        now = time.monotonic()
        if self._snapshot is not None and now < self._next_probe_at:
            return self._snapshot

        if self._probe_lock is None:
            self._probe_lock = asyncio.Lock()
        async with self._probe_lock:
            now = time.monotonic()
            if self._snapshot is not None and now < self._next_probe_at:
                return self._snapshot

            result = await self._probe_once()
            if result.ready:
                self._failure_backoff = (
                    self._configuration.initial_failure_backoff_seconds
                )
                self._next_probe_at = (
                    time.monotonic() + self._configuration.ready_cache_seconds
                )
                self._snapshot = ReadinessSnapshot(True, "ready")
            else:
                self._next_probe_at = time.monotonic() + self._failure_backoff
                self._snapshot = ReadinessSnapshot(
                    False,
                    result.reason,
                    retry_after=self._failure_backoff,
                )
                self._failure_backoff = min(
                    self._configuration.max_failure_backoff_seconds,
                    self._failure_backoff * 2,
                )
            return self._snapshot


__all__ = ("ReadinessGate", "ReadinessSnapshot")
