from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from haoai_backend.generation_contracts import TransferFailure

from .pinned_http import MatureHttpLibrary, PinnedPublicMediaFetcher
from .public_fetch_policy import AsyncGetAddrInfo, PublicFetchPolicy
from .r2_storage import R2Storage
from .remote_staging import RemoteStaging
from .remote_transfer import RemoteMediaTransfer
from .transfer_types import (
    MAX_TRANSFER_SECONDS,
    R2Configuration,
    SafeMediaPolicy,
    S3ClientFactory,
    SecretHandle,
)

_T = TypeVar("_T")
_MAX_SHARED_OPERATIONS = 2
_OWNER_NAME = "haoai-owned-media-runtime"


@dataclass(frozen=True, slots=True)
class OwnedMediaDependencies:
    transfer_tmp_dir: Path
    getaddrinfo: AsyncGetAddrInfo | None
    http_library: MatureHttpLibrary | None
    media_policy: SafeMediaPolicy | None
    r2_configuration: R2Configuration | None
    access_key: SecretHandle | None
    secret_key: SecretHandle | None
    s3_client_factory: S3ClientFactory | None


@dataclass(slots=True)
class OwnedMediaServices:
    shared_slots: asyncio.Semaphore
    public_policy: PublicFetchPolicy | None
    staging: RemoteStaging | None
    remote_transfer: RemoteMediaTransfer | None
    storage: R2Storage


class OwnedMediaRuntime:
    """One explicit event-loop owner and executor shared by media facades."""

    def __init__(
        self,
        dependencies: OwnedMediaDependencies,
        *,
        loop_factory: Callable[[], asyncio.AbstractEventLoop],
        thread_factory: Callable[..., threading.Thread],
        executor_factory: Callable[..., concurrent.futures.Executor],
    ) -> None:
        if not isinstance(dependencies, OwnedMediaDependencies):
            raise TypeError("explicit media runtime dependencies are required")
        for name, factory in (
            ("loop_factory", loop_factory),
            ("thread_factory", thread_factory),
            ("executor_factory", executor_factory),
        ):
            if not callable(factory):
                raise TypeError(f"{name} must be an explicit callable")
        self._dependencies = dependencies
        self._loop_factory = loop_factory
        self._thread_factory = thread_factory
        self._executor_factory = executor_factory
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._state = "new"
        self._active_calls = 0
        self._retained_shared_slots = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._executor: concurrent.futures.Executor | None = None
        self._thread: threading.Thread | None = None
        self._loop_thread_id: int | None = None
        self._ready = threading.Event()
        self._services: OwnedMediaServices | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def owner_thread_id(self) -> int | None:
        return self._loop_thread_id

    def assert_sync_caller(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("synchronous media facade cannot run inside an event loop")
        if threading.get_ident() == self._loop_thread_id:
            raise RuntimeError("owned media loop thread cannot synchronously call itself")

    @contextmanager
    def admit_local_effect(self) -> Iterator[None]:
        """Count a local facade effect without starting the owned runtime."""
        self.assert_sync_caller()
        with self._condition:
            if self._state not in {"new", "running"}:
                raise RuntimeError("owned media runtime is shutting down")
            self._active_calls += 1
        try:
            yield
        finally:
            self._leave_call()

    def run(self, operation: Callable[[OwnedMediaServices], Awaitable[_T]]) -> _T:
        self.assert_sync_caller()
        self.start()
        self._enter_call()
        try:
            return self._submit_raw(self._invoke(operation))
        finally:
            self._leave_call()

    def call_blocking_under_shared_slot(
        self, operation: Callable[[OwnedMediaServices], _T]
    ) -> _T:
        """Reserve the shared media budget while a synchronous SDK call settles."""
        self.assert_sync_caller()
        self.start()
        self._enter_call()
        services: OwnedMediaServices | None = None
        acquired = False
        retain_slot = False
        try:
            services = self._submit_raw(self._acquire_shared_slot())
            acquired = True
            try:
                result = operation(services)
            except BaseException:
                retain_slot = True
                raise
            storage_pending = getattr(
                services.storage, "pending_resource_cleanup_count", 0
            )
            retain_slot = (
                isinstance(result, TransferFailure)
                and result.code == "cleanup_failure"
            ) or (type(storage_pending) is int and storage_pending > 0)
            return result
        finally:
            try:
                if acquired and services is not None:
                    if retain_slot:
                        self._retained_shared_slots += 1
                    else:
                        try:
                            self._submit_raw(self._release_shared_slot(services))
                        except BaseException:
                            self._retained_shared_slots += 1
                            raise
            finally:
                self._leave_call()

    def start(self) -> None:
        self.assert_sync_caller()
        with self._condition:
            if self._state == "starting":
                self._condition.wait_for(lambda: self._state != "starting")
            if self._state == "running":
                return
            if self._state == "cleanup_pending":
                raise RuntimeError("owned media runtime has pending cleanup")
            if self._state != "new":
                raise RuntimeError("owned media runtime cannot be restarted")
            loop = self._loop_factory()
            try:
                executor = self._executor_factory(
                    max_workers=_MAX_SHARED_OPERATIONS,
                    thread_name_prefix="haoai-media-sdk",
                )
            except BaseException:
                loop.close()
                raise
            try:
                thread = self._thread_factory(
                    target=self._serve_loop,
                    args=(loop,),
                    name=_OWNER_NAME,
                    daemon=True,
                )
            except BaseException:
                executor.shutdown(wait=True)
                loop.close()
                raise
            self._loop = loop
            self._executor = executor
            self._thread = thread
            self._state = "starting"
            try:
                thread.start()
            except BaseException:
                self._state = "failed"
                executor.shutdown(wait=True)
                loop.close()
                self._condition.notify_all()
                raise
        if not self._ready.wait(timeout=10):
            with self._condition:
                self._state = "failed"
                self._condition.notify_all()
            raise RuntimeError("owned media loop did not start")
        with self._condition:
            if self._state == "starting":
                self._state = "running"
                self._condition.notify_all()
            if self._state != "running":
                raise RuntimeError("owned media runtime failed to start")

    def close(self) -> None:
        self.assert_sync_caller()
        with self._condition:
            if self._state == "closed":
                return
            if self._state == "new":
                self._state = "closing"
                self._condition.notify_all()
                self._condition.wait_for(lambda: self._active_calls == 0)
                self._state = "closed"
                self._condition.notify_all()
                return
            if self._state not in {"running", "cleanup_pending"}:
                raise RuntimeError("owned media runtime is not available for shutdown")
            self._state = "closing"
            self._condition.notify_all()
            self._condition.wait_for(lambda: self._active_calls == 0)

        try:
            if self._services is not None:
                self._submit_raw(self._settle_services())
            loop = self._require_loop()
            pending = self._submit_raw(self._pending_task_count())
            if pending:
                raise RuntimeError("owned media runtime still has pending tasks")
        except BaseException:
            with self._condition:
                self._state = "cleanup_pending"
                self._condition.notify_all()
            raise

        loop = self._require_loop()
        loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread is not None:
            thread.join()
        executor = self._executor
        if executor is not None:
            executor.shutdown(wait=True)
        loop.close()
        with self._condition:
            self._state = "closed"
            self._condition.notify_all()

    def _enter_call(self) -> None:
        with self._condition:
            if self._state != "running":
                raise RuntimeError("owned media runtime is shutting down")
            self._active_calls += 1

    def _leave_call(self) -> None:
        with self._condition:
            self._active_calls -= 1
            self._condition.notify_all()

    def _serve_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        self._loop_thread_id = threading.get_ident()
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            asyncio.set_event_loop(None)

    async def _invoke(
        self, operation: Callable[[OwnedMediaServices], Awaitable[_T]]
    ) -> _T:
        return await operation(self._get_services())

    def _get_services(self) -> OwnedMediaServices:
        loop = self._require_loop()
        if asyncio.get_running_loop() is not loop:
            raise RuntimeError("media services may only be created by their owner loop")
        if self._services is not None:
            return self._services

        dependencies = self._dependencies
        executor = self._executor
        if executor is None:
            raise RuntimeError("owned media executor is unavailable")
        slots = asyncio.Semaphore(_MAX_SHARED_OPERATIONS)
        public_policy = (
            PublicFetchPolicy(dependencies.getaddrinfo)
            if dependencies.getaddrinfo is not None
            else None
        )
        staging: RemoteStaging | None = None
        transfer: RemoteMediaTransfer | None = None
        storage = self._new_storage(executor)
        if (
            public_policy is not None
            and dependencies.http_library is not None
            and dependencies.media_policy is not None
            and dependencies.transfer_tmp_dir.is_absolute()
        ):
            staging = RemoteStaging(dependencies.transfer_tmp_dir)
            fetcher = PinnedPublicMediaFetcher(
                public_policy,
                dependencies.media_policy,
                http_library=dependencies.http_library,
                staging=staging,
            )
            transfer = RemoteMediaTransfer(
                fetcher,
                staging,
                storage,
                shared_slots=slots,
            )
        self._services = OwnedMediaServices(
            shared_slots=slots,
            public_policy=public_policy,
            staging=staging,
            remote_transfer=transfer,
            storage=storage,
        )
        return self._services

    def _new_storage(self, executor: concurrent.futures.Executor) -> R2Storage:
        dependencies = self._dependencies
        return R2Storage(
            dependencies.r2_configuration,
            dependencies.media_policy,
            access_key=dependencies.access_key,
            secret_key=dependencies.secret_key,
            client_factory=dependencies.s3_client_factory,
            executor=executor,
        )

    async def _acquire_shared_slot(self) -> OwnedMediaServices:
        services = self._get_services()
        async with asyncio.timeout(MAX_TRANSFER_SECONDS):
            await services.shared_slots.acquire()
        return services

    async def _release_shared_slot(self, services: OwnedMediaServices) -> None:
        services.shared_slots.release()

    async def _settle_services(self) -> None:
        services = self._services
        if services is None:
            return
        transfer = services.remote_transfer
        if transfer is not None:
            await transfer.retry_pending_cleanup()
        else:
            await services.storage.retry_pending_resource_cleanup_async()
        storage_pending = services.storage.pending_resource_cleanup_count
        staging_pending = (
            services.staging.pending_cleanup_count if services.staging is not None else 0
        )
        if storage_pending or staging_pending:
            raise RuntimeError("owned media resources remain pending cleanup")
        retained_slots = self._retained_shared_slots
        self._retained_shared_slots = 0
        for _ in range(retained_slots):
            services.shared_slots.release()

    async def _pending_task_count(self) -> int:
        current = asyncio.current_task()
        return sum(task is not current for task in asyncio.all_tasks())

    def _submit_raw(self, coroutine: Awaitable[_T]) -> _T:
        loop = self._require_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return future.result()

    def _require_loop(self) -> asyncio.AbstractEventLoop:
        loop = self._loop
        if loop is None:
            raise RuntimeError("owned media loop is unavailable")
        return loop
