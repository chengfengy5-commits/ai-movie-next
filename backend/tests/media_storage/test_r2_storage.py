from __future__ import annotations

import asyncio
import concurrent.futures
import os
from dataclasses import replace
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from haoai_backend.generation_contracts import ReferenceInput, StoredMedia, TransferFailure
from haoai_backend.media_storage.r2_storage import R2Storage
from haoai_backend.media_storage.remote_staging import RemoteStaging
from haoai_backend.media_storage.remote_transfer import RemoteMediaTransfer
from haoai_backend.media_storage.transfer_types import (
    MediaTypeRule,
    R2Configuration,
    SafeMediaPolicy,
    SecretValue,
    TransferConfigurationError,
    WriteReceipt,
)


class Secret:
    def __init__(self, value: str) -> None:
        self.value = value

    def reveal(self) -> SecretValue:
        return SecretValue(self.value)


class FakeClient:
    def __init__(self, factory, *, receipt: WriteReceipt | None = None) -> None:
        self.factory = factory
        self.receipt = receipt
        self.closed = False
        self.close_calls = 0

    def put_object(self, *, Bucket, Key, Body, ContentType, CacheControl):
        self.factory.call_thread = threading.get_ident()
        self.factory.put_calls += 1
        self.factory.body = Body.read()
        self.factory.request = (Bucket, Key, ContentType, CacheControl)
        if self.factory.entered is not None:
            self.factory.entered.set()
        if self.factory.release is not None:
            if not self.factory.release.wait(5):
                raise TimeoutError("fake worker barrier timed out")
        return self.receipt or WriteReceipt(Key, 200, "fake-request")

    def close(self) -> None:
        self.close_calls += 1
        self.factory.close_threads.append(threading.get_ident())
        self.factory.close_thread_names.append(threading.current_thread().name)
        self.factory.closed.set()
        if self.factory.close_failure and self.close_calls == 1:
            raise OSError("fake close failure")
        if self.close_calls == 2 and self.factory.retry_entered is not None:
            self.factory.retry_entered.set()
            if threading.get_ident() == self.factory.loop_thread:
                raise AssertionError("SDK retry close ran on the event loop")
            if self.factory.retry_release is not None:
                if not self.factory.retry_release.wait(5):
                    raise TimeoutError("fake SDK retry barrier timed out")
            if self.factory.retry_cancel is not None:
                raise self.factory.retry_cancel
        self.closed = True


class FakeFactory:
    def __init__(
        self,
        *,
        status_code: int = 200,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
        close_failure: bool = False,
        retry_entered: threading.Event | None = None,
        retry_release: threading.Event | None = None,
        retry_cancel: asyncio.CancelledError | None = None,
    ) -> None:
        self.status_code = status_code
        self.entered = entered
        self.release = release
        self.close_failure = close_failure
        self.retry_entered = retry_entered
        self.retry_release = retry_release
        self.retry_cancel = retry_cancel
        self.loop_thread: int | None = None
        self.close_threads: list[int] = []
        self.close_thread_names: list[str] = []
        self.call_thread: int | None = None
        self.put_calls = 0
        self.body: bytes | None = None
        self.request = None
        self.closed = threading.Event()
        self.settings = None
        self.client = None

    def create(self, settings, access_key, secret_key):
        self.settings = settings
        if access_key.value != "access-secret" or secret_key.value != "secret-secret":
            raise AssertionError("secret handles were not resolved")
        self.client = FakeClient(
            self,
            receipt=WriteReceipt("", self.status_code, "") if self.status_code != 200 else None,
        )
        return self.client


def make_configuration(directory: Path) -> R2Configuration:
    return R2Configuration(
        endpoint_url="https://account.r2.cloudflarestorage.com",
        bucket="media-bucket",
        public_base_url="https://media.example.test",
        transfer_tmp_dir=directory,
        business_type="generation",
        environment="production",
        connect_timeout_seconds=9,
        read_timeout_seconds=45,
    )


def make_storage(directory: Path, factory: FakeFactory, executor) -> R2Storage:
    return R2Storage(
        make_configuration(directory),
        SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
        access_key=Secret("access-secret"),
        secret_key=Secret("secret-secret"),
        client_factory=factory,
        executor=executor,
    )


class R2StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="media-sdk-test"
        )

    async def asyncTearDown(self) -> None:
        self.executor.shutdown(wait=True)
        self.temp.cleanup()

    def make_download(self):
        staging = RemoteStaging(self.directory)
        download = staging.create(10)
        download.write_chunk(b"payload", max_bytes=10)
        download.seal("image/png")
        return staging, download

    async def test_r2_config_keeps_explicit_environment_and_public_path_rules(self) -> None:
        production = make_configuration(self.directory)
        with self.assertRaises(TransferConfigurationError):
            replace(production, public_base_url="http://media.example.test")
        with self.assertRaises(TransferConfigurationError):
            replace(production, public_base_url="https://bucket.r2.dev")

        development = replace(
            production,
            endpoint_url="http://account.r2.example.test",
            public_base_url="http://media.example.test/previews",
            environment="development",
        )
        self.assertEqual(development.public_base, "http://media.example.test/previews")
        with self.assertRaises(TransferConfigurationError):
            replace(development, public_base_url="https://bucket.r2.dev")
        test_override = replace(
            development,
            environment="test",
            public_base_url="https://bucket.r2.dev",
            allow_r2_dev=True,
        )
        self.assertEqual(test_override.public_base, "https://bucket.r2.dev")

        allowed_legacy = replace(
            production,
            legacy_public_origins=(("https", "old.example.test", 443),),
            legacy_acceleration_map=(
                (("https", "old.example.test", 443), ("https", "cdn.example.test", 443)),
            ),
        )
        self.assertEqual(len(allowed_legacy.legacy_acceleration_map), 1)
        with self.assertRaises(TransferConfigurationError):
            replace(
                production,
                legacy_acceleration_map=(
                    (("https", "unknown.example.test", 443), ("https", "cdn.example.test", 443)),
                ),
            )

    async def test_positive_receipt_is_required_and_sdk_runs_off_loop(self) -> None:
        factory = FakeFactory()
        storage = make_storage(self.directory, factory, self.executor)
        staging, download = self.make_download()
        loop_thread = threading.get_ident()

        result = await storage.store_download(download)

        self.assertIsInstance(result, StoredMedia)
        self.assertEqual(factory.body, b"payload")
        self.assertEqual(factory.put_calls, 1)
        self.assertNotEqual(factory.call_thread, loop_thread)
        self.assertEqual(factory.settings.region, "auto")
        self.assertEqual(factory.settings.signature_version, "s3v4")
        self.assertEqual(factory.settings.retry_mode, "standard")
        self.assertEqual(factory.settings.total_max_attempts, 3)
        self.assertEqual(factory.request[0], "media-bucket")
        self.assertTrue(factory.request[1].endswith(".png"))
        self.assertEqual(factory.request[2:], ("image/png", "public, max-age=31536000, immutable"))
        self.assertTrue(factory.closed.is_set())
        self.assertTrue(staging.release(download).released)

    async def test_nonpositive_receipt_never_constructs_stored_media(self) -> None:
        factory = FakeFactory(status_code=202)
        storage = make_storage(self.directory, factory, self.executor)
        staging, download = self.make_download()

        result = await storage.store_download(download)

        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "storage_outcome_unverified")
        self.assertEqual(factory.put_calls, 1)
        self.assertTrue(factory.closed.is_set())
        self.assertTrue(staging.release(download).released)

    async def test_close_failure_retains_client_for_explicit_cleanup_retry(self) -> None:
        factory = FakeFactory(close_failure=True)
        storage = make_storage(self.directory, factory, self.executor)
        staging, download = self.make_download()

        result = await storage.store_download(download)

        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "cleanup_failure")
        self.assertEqual(factory.put_calls, 1)
        self.assertEqual(await storage.retry_pending_resource_cleanup_async(), (True,))
        self.assertTrue(factory.client.closed)
        self.assertTrue(staging.release(download).released)

    async def test_async_cleanup_retry_uses_executor_and_settles_before_cancel(self) -> None:
        retry_entered = threading.Event()
        retry_release = threading.Event()
        cleanup_cancel = asyncio.CancelledError("cleanup B")
        factory = FakeFactory(
            close_failure=True,
            retry_entered=retry_entered,
            retry_release=retry_release,
            retry_cancel=cleanup_cancel,
        )
        storage = make_storage(self.directory, factory, self.executor)
        staging = RemoteStaging(self.directory)

        class OneDownloadFetcher:
            async def fetch_to_owned(self, url, *, max_bytes, timeout_seconds):
                download = staging.create(max_bytes)
                download.write_chunk(b"payload", max_bytes=max_bytes)
                download.seal("image/png")
                return download

        slots = asyncio.Semaphore(2)
        transfer = RemoteMediaTransfer(
            OneDownloadFetcher(), staging, storage, shared_slots=slots
        )
        reference = ReferenceInput(
            kind="public_http", locator="https://example.test/p.png"
        )
        loop_thread = threading.get_ident()

        result = await transfer.transfer(reference, max_bytes=10, timeout_seconds=2)

        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "cleanup_failure")
        self.assertEqual(storage.pending_resource_cleanup_count, 1)
        self.assertEqual(slots._value, 1)
        self.assertNotEqual(factory.close_threads[0], loop_thread)

        await slots.acquire()
        spool = staging.create(10)
        spool.write_chunk(b"pending", max_bytes=10)
        spool.seal("image/png")
        cleanup_cancel = asyncio.CancelledError("cleanup B")
        original_unlink = os.unlink

        def cancel_spool_unlink(path, *args, **kwargs):
            name = path if isinstance(path, str) else os.fspath(path)
            if isinstance(name, str) and name == spool._name:
                raise cleanup_cancel
            return original_unlink(path, *args, **kwargs)

        with patch(
            "haoai_backend.media_storage.transfer_types.os.unlink",
            side_effect=cancel_spool_unlink,
        ):
            with self.assertRaises(asyncio.CancelledError) as spool_cleanup:
                staging.release(spool)
        self.assertIs(spool_cleanup.exception, cleanup_cancel)
        self.assertEqual(staging.pending_cleanup_count, 1)
        self.assertEqual(slots._value, 0)

        factory.loop_thread = loop_thread
        observed_cancellation: list[asyncio.CancelledError] = []

        async def retry_and_record_cancellation() -> int:
            try:
                return await transfer.retry_pending_cleanup()
            except asyncio.CancelledError as exc:
                observed_cancellation.append(exc)
                raise

        retry_task = asyncio.create_task(retry_and_record_cancellation())
        unlink_patcher = patch(
            "haoai_backend.media_storage.transfer_types.os.unlink",
            side_effect=cancel_spool_unlink,
        )
        unlink_patcher.start()
        try:
            deadline = asyncio.get_running_loop().time() + 1
            while not retry_entered.is_set() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.005)

            self.assertTrue(retry_entered.is_set())
            unlink_patcher.stop()
            retry_task.cancel("retry cancel A")
            await asyncio.sleep(0.02)
            self.assertFalse(retry_task.done())
            self.assertEqual(slots._value, 0)

            retry_release.set()
            with self.assertRaises(asyncio.CancelledError) as caught:
                await retry_task

            self.assertEqual(caught.exception.args, ("retry cancel A",))
            self.assertIs(caught.exception, observed_cancellation[0])
            self.assertIsNot(caught.exception, cleanup_cancel)
            self.assertEqual(storage.pending_resource_cleanup_count, 1)
            self.assertEqual(slots._value, 0)
            self.assertTrue(
                all(name.startswith("media-sdk-test") for name in factory.close_thread_names)
            )
            self.assertTrue(all(thread != loop_thread for thread in factory.close_threads))

            factory.retry_cancel = None
            self.assertEqual(await transfer.retry_pending_cleanup(), 1)
            self.assertEqual(storage.pending_resource_cleanup_count, 0)
            self.assertEqual(slots._value, 1)
            self.assertEqual(len(factory.close_threads), 3)
            self.assertTrue(all(thread != loop_thread for thread in factory.close_threads))
            self.assertEqual(slots._value, 1)
        finally:
            unlink_patcher.stop()
            retry_release.set()
            if not retry_task.done():
                await asyncio.gather(retry_task, return_exceptions=True)
            if storage.pending_resource_cleanup_count or staging.pending_cleanup_count:
                factory.retry_cancel = None
                await transfer.retry_pending_cleanup()
            slots.release()

    async def test_cancel_waits_for_sync_worker_before_closing_client_or_spool(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        factory = FakeFactory(entered=entered, release=release)
        storage = make_storage(self.directory, factory, self.executor)
        staging, download = self.make_download()
        task = asyncio.create_task(storage.store_download(download))

        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        task.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        self.assertFalse(factory.closed.is_set())
        self.assertEqual(staging.pending_cleanup_count, 0)

        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(factory.closed.is_set())
        self.assertTrue(staging.release(download).released)

    async def test_factory_and_secret_cancellation_identity_is_not_converted(self) -> None:
        for source in ("secret", "factory"):
            with self.subTest(source=source):
                cancellation = asyncio.CancelledError(f"{source} cancel")
                factory = FakeFactory()
                storage = make_storage(self.directory, factory, self.executor)
                staging, download = self.make_download()

                class CancelSecret:
                    def reveal(self):
                        raise cancellation

                class CancelFactory:
                    def create(self, settings, access_key, secret_key):
                        raise cancellation

                if source == "secret":
                    storage._access_key = CancelSecret()
                else:
                    storage._client_factory = CancelFactory()

                caught = None
                try:
                    await storage.store_download(download)
                except asyncio.CancelledError as exc:
                    caught = exc
                self.assertTrue(staging.release(download).released)
                self.assertIs(caught, cancellation)

    async def test_put_cancel_a_wins_while_every_cleanup_is_attempted(self) -> None:
        operation_cancel = asyncio.CancelledError("SDK put A")
        cleanup_cancel = asyncio.CancelledError("body and client close B")
        staging, download = self.make_download()
        opened_readers = []
        original_open_reader = type(download).open_reader

        class CancelOnFirstClose:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.close_calls = 0

            def read(self, *args, **kwargs):
                return self.wrapped.read(*args, **kwargs)

            def fileno(self):
                return self.wrapped.fileno()

            def close(self):
                self.close_calls += 1
                if self.close_calls == 1:
                    raise cleanup_cancel
                self.wrapped.close()

        def open_reader(owner):
            reader = CancelOnFirstClose(original_open_reader(owner))
            opened_readers.append(reader)
            return reader

        class CancelClient:
            def __init__(self):
                self.close_calls = 0

            def put_object(self, *, Bucket, Key, Body, ContentType, CacheControl):
                Body.read()
                raise operation_cancel

            def close(self):
                self.close_calls += 1
                if self.close_calls == 1:
                    raise cleanup_cancel

        class CancelFactory:
            def __init__(self):
                self.client = CancelClient()

            def create(self, settings, access_key, secret_key):
                return self.client

        factory = CancelFactory()
        storage = R2Storage(
            make_configuration(self.directory),
            SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
            access_key=Secret("access-secret"),
            secret_key=Secret("secret-secret"),
            client_factory=factory,
            executor=self.executor,
        )

        caught = None
        with patch.object(type(download), "open_reader", open_reader):
            try:
                await storage.store_download(download)
            except asyncio.CancelledError as exc:
                caught = exc

        self.assertEqual(len(opened_readers), 1)
        reader_fd = opened_readers[0].fileno()
        self.assertEqual(opened_readers[0].close_calls, 1)
        self.assertEqual(factory.client.close_calls, 1)
        self.assertEqual(await storage.retry_pending_resource_cleanup_async(), (True, True))
        with self.assertRaises(OSError):
            os.fstat(reader_fd)
        self.assertTrue(staging.release(download).released)
        self.assertIs(caught, operation_cancel)

    async def test_cleanup_cancel_b_propagates_when_operation_only_failed_normally(self) -> None:
        cleanup_cancel = asyncio.CancelledError("client cleanup B")
        staging, download = self.make_download()

        class NormalFailureClient:
            def __init__(self):
                self.close_calls = 0

            def put_object(self, *, Bucket, Key, Body, ContentType, CacheControl):
                Body.read()
                raise ValueError("ordinary SDK failure")

            def close(self):
                self.close_calls += 1
                if self.close_calls == 1:
                    raise cleanup_cancel

        class NormalFailureFactory:
            def __init__(self):
                self.client = NormalFailureClient()

            def create(self, settings, access_key, secret_key):
                return self.client

        factory = NormalFailureFactory()
        storage = R2Storage(
            make_configuration(self.directory),
            SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
            access_key=Secret("access-secret"),
            secret_key=Secret("secret-secret"),
            client_factory=factory,
            executor=self.executor,
        )

        caught = None
        try:
            await storage.store_download(download)
        except asyncio.CancelledError as exc:
            caught = exc
        self.assertEqual(factory.client.close_calls, 1)
        self.assertEqual(await storage.retry_pending_resource_cleanup_async(), (True,))
        self.assertTrue(staging.release(download).released)
        self.assertIs(caught, cleanup_cancel)


if __name__ == "__main__":
    unittest.main()
