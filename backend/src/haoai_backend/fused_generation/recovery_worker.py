"""Bounded startup and periodic recovery for durable fused submissions."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from starlette.concurrency import run_in_threadpool

from .execution import FusedGenerationExecution
from .ports import FusedGenerationUnitOfWorkFactory


class FusedGenerationRecoveryWorker:
    """Wake only submissions created by the durable fused producer."""

    def __init__(
        self,
        execution: FusedGenerationExecution,
        uow_factory: FusedGenerationUnitOfWorkFactory,
        *,
        poll_interval_seconds: float = 2.0,
        scan_limit: int = 32,
    ) -> None:
        if not 0.1 <= poll_interval_seconds <= 30:
            raise ValueError("fused recovery interval must be between 0.1 and 30 seconds")
        if type(scan_limit) is not int or not 1 <= scan_limit <= 128:
            raise ValueError("fused recovery scan limit is invalid")
        self._execution = execution
        self._uow_factory = uow_factory
        self._poll_interval_seconds = poll_interval_seconds
        self._scan_limit = scan_limit
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("fused recovery worker cannot be restarted")
        self._task = asyncio.create_task(
            self._run(), name="fused-generation-durable-recovery"
        )

    async def shutdown(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                rows = await run_in_threadpool(
                    _read_recoverable_submissions,
                    self._uow_factory,
                    self._scan_limit,
                )
                for row in rows:
                    if self._stop.is_set():
                        return
                    submission_id = row.get("submission_id")
                    owner_id = row.get("owner_id")
                    if type(submission_id) is not str or type(owner_id) is not str:
                        continue
                    await self._execution.execute_submission(submission_id, owner_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A later bounded scan can recover after transient database errors.
                pass
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._poll_interval_seconds
                )
            except TimeoutError:
                continue


def _read_recoverable_submissions(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    limit: int,
) -> tuple[Mapping[str, object], ...]:
    uow = uow_factory()
    try:
        return uow.recoverable_durable_submissions(limit=limit)
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


__all__ = ("FusedGenerationRecoveryWorker",)
