from __future__ import annotations

import asyncio
import concurrent.futures
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from haoai_backend.generation_contracts import (
    GenerationRuntimeConfig,
    ReferenceInput,
    StoredMedia,
    TransferFailure,
)
from haoai_backend.media_storage.owned_media_runtime import (
    OwnedMediaDependencies,
    OwnedMediaRuntime,
)
from haoai_backend.media_storage.reference_staging import (
    ReferenceStager,
    ReferenceStagingPolicy,
)
from haoai_backend.media_storage.sync_facade import (
    MediaStoreOperationError,
    SynchronousMediaStore,
)
from haoai_backend.media_storage.transfer_types import (
    MediaTypeRule,
    R2Configuration,
    SafeMediaPolicy,
    SecretValue,
    WriteReceipt,
)


class _UnusedHttpLibrary:
    pass


def _runtime(
    directory: Path,
    *,
    getaddrinfo=None,
    http_library=None,
    r2_configuration=None,
    access_key=None,
    secret_key=None,
    s3_client_factory=None,
) -> OwnedMediaRuntime:
    return OwnedMediaRuntime(
        OwnedMediaDependencies(
            transfer_tmp_dir=directory,
            getaddrinfo=getaddrinfo,
            http_library=http_library,
            media_policy=SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
            r2_configuration=r2_configuration,
            access_key=access_key,
            secret_key=secret_key,
            s3_client_factory=s3_client_factory,
        ),
        loop_factory=asyncio.new_event_loop,
        thread_factory=threading.Thread,
        executor_factory=concurrent.futures.ThreadPoolExecutor,
    )


def _stager(directory: Path) -> ReferenceStager:
    upload = directory / "uploads"
    upload.mkdir()
    staging = upload / "staging"
    staging.mkdir()
    return ReferenceStager(
        approved_upload_directory=upload,
        policy=ReferenceStagingPolicy(max_bytes=1024),
    )


def _runtime_config() -> GenerationRuntimeConfig:
    return GenerationRuntimeConfig(
        session_factory=lambda: None,
        db_clock=lambda: datetime.now(timezone.utc),
        id_factory=lambda: "test-operation-id",
        http_client_factory=lambda: None,
        credential_resolver=lambda *_args: None,
        provider_factories=(),
        media_store_factory=lambda: None,
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: None,
        bound_provider_content_fetcher_factory=lambda: None,
        local_muse_capability_factory=lambda: None,
        r2_storage_factory=lambda: None,
    )


class SyncFacadeTests(unittest.TestCase):
    def test_url_or_inline_mode_preserves_selected_kind_and_signed_query(self) -> None:
        calls: list[tuple[str, int, int, int]] = []

        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            calls.append((host, port, family, type))
            return (
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),
            )

        url = "https://media.example.test/a%2fb?sig=a%2Fb&sig=two"
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            stager = _stager(directory)
            runtime = _runtime(
                directory,
                getaddrinfo=getaddrinfo,
                http_library=_UnusedHttpLibrary(),
            )
            store = SynchronousMediaStore(
                runtime,
                mode="url_or_inline",
                reference_stager=stager,
                staging_directory=None,
                managed_origin_map={"https://media.example.test": "https://cdn.example.test"},
            )
            try:
                public = store.prepare_reference(
                    ReferenceInput(kind="public_http", locator=url),
                    capability=None,
                    config=_runtime_config(),
                )
                inline_uri = "data:image/png;base64,cG5n"
                inline = store.prepare_reference(
                    ReferenceInput(kind="data_uri", locator=inline_uri),
                    capability=None,
                    config=_runtime_config(),
                )
                byte_reference = store.prepare_reference(
                    ReferenceInput(kind="bytes", content=b"png", media_type="image/png"),
                    capability=None,
                    config=_runtime_config(),
                )
                managed = store.prepare_reference(
                    ReferenceInput(
                        kind="managed_url",
                        locator="https://cdn.example.test/path/a%2fb?x=1&x=2",
                    ),
                    capability=None,
                    config=_runtime_config(),
                )
                self.assertEqual((public.kind, public.locator), ("public_http", url))
                self.assertEqual(
                    (inline.kind, inline.locator, inline.content),
                    ("data_uri", inline_uri, None),
                )
                self.assertEqual(inline.media_type, "image/png")
                self.assertEqual(
                    (byte_reference.kind, byte_reference.content, byte_reference.media_type),
                    ("bytes", b"png", "image/png"),
                )
                self.assertEqual(managed.kind, "managed_url")
                self.assertEqual(
                    managed.locator,
                    "https://cdn.example.test/path/a%2fb?x=1&x=2",
                )
                self.assertEqual(
                    calls,
                    [("media.example.test", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)],
                )
            finally:
                runtime.close()

    def test_local_muse_staging_returns_an_owned_handle_that_can_be_released(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            stager = _stager(directory)
            runtime = _runtime(directory)
            store = SynchronousMediaStore(
                runtime,
                mode="local_muse",
                reference_stager=stager,
                staging_directory=directory / "uploads" / "staging",
            )
            try:
                prepared = store.prepare_reference(
                    ReferenceInput(
                        kind="bytes",
                        content=b"local-png",
                        media_type="image/png",
                    ),
                    capability=None,
                    config=_runtime_config(),
                )
                self.assertEqual(prepared.kind, "local_muse_path")
                self.assertTrue(Path(prepared.locator or "").is_file())
                self.assertEqual(store.release_prepared_reference(prepared), "released")
                self.assertFalse(Path(prepared.locator or "").exists())
                self.assertEqual(
                    store.release_prepared_reference(prepared), "already_released"
                )
            finally:
                runtime.close()

    def test_bytes_storage_requires_and_returns_the_actual_positive_receipt(self) -> None:
        class _Secret:
            def __init__(self, value: str) -> None:
                self.value = value

            def reveal(self) -> SecretValue:
                return SecretValue(self.value)

        class _Client:
            def __init__(self) -> None:
                self.closed = False
                self.worker_thread: int | None = None
                self.body: bytes | None = None
                self.key: str | None = None

            def put_object(self, **kwargs):
                self.worker_thread = threading.get_ident()
                self.body = kwargs["Body"].read()
                self.key = kwargs["Key"]
                return WriteReceipt(self.key, 200, "actual-dummy-request-id")

            def close(self) -> None:
                self.closed = True

        class _Factory:
            def __init__(self, client: _Client) -> None:
                self.client = client
                self.settings = None

            def create(self, settings, access_key, secret_key):
                self.settings = settings
                if access_key.value != "dummy-access" or secret_key.value != "dummy-secret":
                    raise AssertionError("explicit dummy secrets were not passed")
                return self.client

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            client = _Client()
            factory = _Factory(client)
            configuration = R2Configuration(
                endpoint_url="https://account.r2.example.test",
                bucket="media-bucket",
                public_base_url="https://media.example.test",
                transfer_tmp_dir=directory,
                business_type="generation",
                environment="production",
                connect_timeout_seconds=7,
                read_timeout_seconds=19,
            )
            runtime = _runtime(
                directory,
                r2_configuration=configuration,
                access_key=_Secret("dummy-access"),
                secret_key=_Secret("dummy-secret"),
                s3_client_factory=factory,
            )
            store = SynchronousMediaStore(
                runtime,
                mode="url_or_inline",
                reference_stager=None,
                staging_directory=None,
            )
            caller_thread = threading.get_ident()
            try:
                result = store.store_bytes_or_provider_content(
                    ReferenceInput(
                        kind="bytes",
                        content=b"store-this-png",
                        media_type="image/png",
                    ),
                    {"max_bytes": 128},
                )
                self.assertIsInstance(result, StoredMedia)
                self.assertEqual(client.body, b"store-this-png")
                self.assertEqual(
                    client.key,
                    result.locator.removeprefix("https://media.example.test/"),
                )
                self.assertTrue(client.closed)
                self.assertNotEqual(client.worker_thread, caller_thread)
                self.assertEqual(factory.settings.region, "auto")
                self.assertEqual(factory.settings.total_max_attempts, 3)
            finally:
                runtime.close()

    def test_sdk_cleanup_failure_keeps_its_budget_until_shutdown_retry_settles(self) -> None:
        class _Secret:
            def reveal(self) -> SecretValue:
                return SecretValue("dummy-secret")

        class _Client:
            def __init__(self) -> None:
                self.close_calls = 0
                self.close_threads: list[int] = []

            def put_object(self, **kwargs):
                return WriteReceipt(kwargs["Key"], 200, "write-request")

            def close(self) -> None:
                self.close_calls += 1
                self.close_threads.append(threading.get_ident())
                if self.close_calls == 1:
                    raise OSError("injected first close failure")

        class _Factory:
            def __init__(self, client: _Client) -> None:
                self.client = client

            def create(self, *_args):
                return self.client

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            client = _Client()
            runtime = _runtime(
                directory,
                r2_configuration=R2Configuration(
                    endpoint_url="https://account.r2.example.test",
                    bucket="media-bucket",
                    public_base_url="https://media.example.test",
                    transfer_tmp_dir=directory,
                    business_type="generation",
                    environment="production",
                    connect_timeout_seconds=7,
                    read_timeout_seconds=19,
                ),
                access_key=_Secret(),
                secret_key=_Secret(),
                s3_client_factory=_Factory(client),
            )
            store = SynchronousMediaStore(
                runtime,
                mode="url_or_inline",
                reference_stager=None,
                staging_directory=None,
            )
            try:
                with self.assertRaises(MediaStoreOperationError) as raised:
                    store.store_bytes_or_provider_content(
                        ReferenceInput(
                            kind="bytes", content=b"png", media_type="image/png"
                        ),
                        {},
                    )
                self.assertEqual(raised.exception.failure.code, "cleanup_failure")

                async def inspect_services(services):
                    return services

                services = runtime.run(inspect_services)
                self.assertEqual(services.storage.pending_resource_cleanup_count, 1)
                self.assertEqual(services.shared_slots._value, 1)

                runtime.close()
                self.assertEqual(client.close_calls, 2)
                self.assertEqual(services.shared_slots._value, 2)
                self.assertEqual(runtime.state, "closed")
            finally:
                if runtime.state != "closed":
                    runtime.close()

    def test_persistent_sdk_close_failure_blocks_new_business_until_retry_succeeds(self) -> None:
        class _Secret:
            def reveal(self) -> SecretValue:
                return SecretValue("dummy-secret")

        class _Client:
            def __init__(self) -> None:
                self.close_calls = 0
                self.fail_close = True

            def put_object(self, **kwargs):
                return WriteReceipt(kwargs["Key"], 200, "write-request")

            def close(self) -> None:
                self.close_calls += 1
                if self.fail_close:
                    raise OSError("injected persistent close failure")

        class _Factory:
            def __init__(self, client: _Client) -> None:
                self.client = client

            def create(self, *_args):
                return self.client

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            client = _Client()
            runtime = _runtime(
                directory,
                r2_configuration=R2Configuration(
                    endpoint_url="https://account.r2.example.test",
                    bucket="media-bucket",
                    public_base_url="https://media.example.test",
                    transfer_tmp_dir=directory,
                    business_type="generation",
                    environment="production",
                    connect_timeout_seconds=7,
                    read_timeout_seconds=19,
                ),
                access_key=_Secret(),
                secret_key=_Secret(),
                s3_client_factory=_Factory(client),
            )
            store = SynchronousMediaStore(
                runtime,
                mode="url_or_inline",
                reference_stager=None,
                staging_directory=None,
            )
            business_calls: list[object] = []
            try:
                with self.assertRaises(MediaStoreOperationError) as raised:
                    store.store_bytes_or_provider_content(
                        ReferenceInput(
                            kind="bytes", content=b"png", media_type="image/png"
                        ),
                        {},
                    )
                self.assertEqual(raised.exception.failure.code, "cleanup_failure")

                async def inspect_services(services):
                    return services

                services = runtime.run(inspect_services)
                self.assertEqual(services.storage.pending_resource_cleanup_count, 1)
                self.assertEqual(services.shared_slots._value, 1)
                owner_thread = runtime._thread
                self.assertIsNotNone(owner_thread)

                with self.assertRaisesRegex(
                    RuntimeError, "owned media resources remain pending cleanup"
                ):
                    runtime.close()
                self.assertEqual(client.close_calls, 2)
                self.assertEqual(services.storage.pending_resource_cleanup_count, 1)
                self.assertEqual(runtime._thread, owner_thread)
                self.assertTrue(owner_thread.is_alive())

                async def new_business(services):
                    business_calls.append(services)
                    return "business call ran"

                with self.assertRaises(RuntimeError):
                    runtime.run(new_business)
                self.assertEqual(business_calls, [])
                self.assertEqual(runtime.state, "cleanup_pending")
                self.assertEqual(runtime._thread, owner_thread)
                self.assertTrue(owner_thread.is_alive())

                client.fail_close = False
                runtime.close()
                self.assertEqual(client.close_calls, 3)
                self.assertEqual(services.storage.pending_resource_cleanup_count, 0)
                self.assertEqual(services.shared_slots._value, 2)
                self.assertEqual(runtime.state, "closed")
                self.assertFalse(owner_thread.is_alive())
            finally:
                client.fail_close = False
                if runtime.state != "closed":
                    runtime.close()

    def test_cleanup_pending_rejects_local_bytes_and_data_uri_before_staging(self) -> None:
        class _Secret:
            def reveal(self) -> SecretValue:
                return SecretValue("dummy-secret")

        class _Client:
            def __init__(self) -> None:
                self.fail_close = True
                self.close_calls = 0

            def put_object(self, **kwargs):
                return WriteReceipt(kwargs["Key"], 200, "local-muse-write")

            def close(self) -> None:
                self.close_calls += 1
                if self.fail_close:
                    raise OSError("injected persistent close failure")

        class _Factory:
            def __init__(self, client: _Client) -> None:
                self.client = client

            def create(self, *_args):
                return self.client

        class _CountingStager(ReferenceStager):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.stage_calls: list[ReferenceInput] = []

            def stage_local_reference(self, input, staging_directory):
                self.stage_calls.append(input)
                return super().stage_local_reference(input, staging_directory)

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            upload = directory / "uploads"
            staging = upload / "staging"
            staging.mkdir(parents=True)
            stager = _CountingStager(
                approved_upload_directory=upload,
                policy=ReferenceStagingPolicy(max_bytes=1024),
            )
            client = _Client()
            runtime = _runtime(
                directory,
                r2_configuration=R2Configuration(
                    endpoint_url="https://account.r2.example.test",
                    bucket="media-bucket",
                    public_base_url="https://media.example.test",
                    transfer_tmp_dir=directory,
                    business_type="generation",
                    environment="production",
                    connect_timeout_seconds=7,
                    read_timeout_seconds=19,
                ),
                access_key=_Secret(),
                secret_key=_Secret(),
                s3_client_factory=_Factory(client),
            )
            store = SynchronousMediaStore(
                runtime,
                mode="local_muse",
                reference_stager=stager,
                staging_directory=staging,
            )
            unexpected_handles = []
            try:
                existing = store.prepare_reference(
                    ReferenceInput(kind="bytes", content=b"before", media_type="image/png"),
                    capability=None,
                    config=_runtime_config(),
                )
                self.assertEqual(runtime.state, "new")
                self.assertIsNone(runtime.owner_thread_id)

                with self.assertRaises(MediaStoreOperationError) as raised:
                    store.store_bytes_or_provider_content(
                        ReferenceInput(kind="bytes", content=b"png", media_type="image/png"),
                        {},
                    )
                self.assertEqual(raised.exception.failure.code, "cleanup_failure")
                services = runtime._services
                self.assertIsNotNone(services)
                self.assertEqual(services.storage.pending_resource_cleanup_count, 1)
                self.assertEqual(services.shared_slots._value, 1)
                with self.assertRaisesRegex(RuntimeError, "pending cleanup"):
                    runtime.close()
                self.assertEqual(runtime.state, "cleanup_pending")

                self.assertEqual(
                    store.normalize_managed_url("https://media.example.test/a", {}),
                    "unmanaged",
                )
                self.assertEqual(store.release_prepared_reference(existing), "released")
                stage_call_count = len(stager.stage_calls)
                self.assertEqual(list(staging.iterdir()), [])

                rejected: list[str] = []
                for reference in (
                    ReferenceInput(kind="bytes", content=b"after", media_type="image/png"),
                    ReferenceInput(
                        kind="data_uri", locator="data:image/png;base64,cG5n"
                    ),
                ):
                    try:
                        prepared = store.prepare_reference(
                            reference, capability=None, config=_runtime_config()
                        )
                    except RuntimeError:
                        rejected.append(reference.kind)
                    else:
                        unexpected_handles.append(prepared)

                observed = (
                    rejected,
                    [item.kind for item in stager.stage_calls[stage_call_count:]],
                    sorted(item.name for item in staging.iterdir()),
                )
                self.assertEqual(observed, (["bytes", "data_uri"], [], []))
            finally:
                for prepared in unexpected_handles:
                    store.release_prepared_reference(prepared)
                client.fail_close = False
                if runtime.state != "closed":
                    runtime.close()

    def test_local_stage_admission_is_atomic_and_close_waits_without_eager_owner(self) -> None:
        class _BlockingStager(ReferenceStager):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.stage_calls: list[ReferenceInput] = []
                self.first_stage_entered = threading.Event()
                self.allow_first_stage_to_finish = threading.Event()

            def stage_local_reference(self, input, staging_directory):
                self.stage_calls.append(input)
                if len(self.stage_calls) == 1:
                    self.first_stage_entered.set()
                    if not self.allow_first_stage_to_finish.wait(timeout=5):
                        raise AssertionError("test did not release the admitted local stage")
                return super().stage_local_reference(input, staging_directory)

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            upload = directory / "uploads"
            (upload / "staging").mkdir(parents=True)
            stager = _BlockingStager(
                approved_upload_directory=upload,
                policy=ReferenceStagingPolicy(max_bytes=1024),
            )
            factory_calls: list[str] = []

            def make_loop():
                factory_calls.append("loop")
                return asyncio.new_event_loop()

            def make_thread(*args, **kwargs):
                factory_calls.append("thread")
                return threading.Thread(*args, **kwargs)

            def make_executor(*args, **kwargs):
                factory_calls.append("executor")
                return concurrent.futures.ThreadPoolExecutor(*args, **kwargs)

            runtime = OwnedMediaRuntime(
                OwnedMediaDependencies(
                    transfer_tmp_dir=directory,
                    getaddrinfo=None,
                    http_library=None,
                    media_policy=SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
                    r2_configuration=None,
                    access_key=None,
                    secret_key=None,
                    s3_client_factory=None,
                ),
                loop_factory=make_loop,
                thread_factory=make_thread,
                executor_factory=make_executor,
            )
            store = SynchronousMediaStore(
                runtime,
                mode="local_muse",
                reference_stager=stager,
                staging_directory=directory / "uploads" / "staging",
            )
            first_result = []
            first_errors = []
            second_result = []
            first_stage_finished = threading.Event()
            close_finished = threading.Event()
            close_errors = []

            def run_first_stage() -> None:
                try:
                    first_result.append(
                        store.prepare_reference(
                            ReferenceInput(
                                kind="bytes", content=b"first", media_type="image/png"
                            ),
                            capability=None,
                            config=_runtime_config(),
                        )
                    )
                except BaseException as exc:
                    first_errors.append(exc)
                finally:
                    first_stage_finished.set()

            def close_runtime() -> None:
                try:
                    runtime.close()
                except BaseException as exc:
                    close_errors.append(exc)
                finally:
                    with runtime._condition:
                        close_finished.set()
                        runtime._condition.notify_all()

            caller = threading.Thread(target=run_first_stage, name="local-stage-caller")
            closer = None
            try:
                caller.start()
                self.assertTrue(stager.first_stage_entered.wait(timeout=3))
                self.assertEqual(runtime.state, "new")
                self.assertEqual(factory_calls, [])
                active_calls_before_close = runtime._active_calls

                closer = threading.Thread(target=close_runtime, name="local-stage-close")
                closer.start()
                with runtime._condition:
                    state_observed = runtime._condition.wait_for(
                        lambda: runtime._state in {"closing", "closed"}
                        or close_finished.is_set(),
                        timeout=3,
                    )
                self.assertTrue(state_observed)

                second_rejected = False
                try:
                    second_result.append(
                        store.prepare_reference(
                            ReferenceInput(
                                kind="bytes", content=b"second", media_type="image/png"
                            ),
                            capability=None,
                            config=_runtime_config(),
                        )
                    )
                except RuntimeError:
                    second_rejected = True

                observed = {
                    "state": runtime.state,
                    "active_calls_before_close": active_calls_before_close,
                    "active_calls": runtime._active_calls,
                    "stage_calls": len(stager.stage_calls),
                    "second_rejected": second_rejected,
                    "close_finished": close_finished.is_set(),
                    "owner_factories": factory_calls,
                    "owner_resources": (
                        runtime._loop,
                        runtime._thread,
                        runtime._executor,
                    ),
                }
                self.assertEqual(
                    observed,
                    {
                        "state": "closing",
                        "active_calls_before_close": 1,
                        "active_calls": 1,
                        "stage_calls": 1,
                        "second_rejected": True,
                        "close_finished": False,
                        "owner_factories": [],
                        "owner_resources": (None, None, None),
                    },
                )
            finally:
                stager.allow_first_stage_to_finish.set()
                caller.join(timeout=5)
                if closer is not None:
                    closer.join(timeout=5)
                for prepared in first_result + second_result:
                    store.release_prepared_reference(prepared)
                if runtime.state != "closed":
                    runtime.close()
            self.assertFalse(caller.is_alive())
            self.assertTrue(first_stage_finished.is_set())
            self.assertEqual(first_errors, [])
            self.assertEqual(close_errors, [])
            self.assertTrue(close_finished.is_set())
            self.assertEqual(runtime.state, "closed")
            self.assertEqual(factory_calls, [])

    def test_private_store_and_fetch_fail_before_runtime_or_sdk_effects(self) -> None:
        calls = 0

        class _ForbiddenFactory:
            def create(self, *_args):
                nonlocal calls
                calls += 1
                raise AssertionError("SDK construction must not be reached")

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            runtime = _runtime(directory, s3_client_factory=_ForbiddenFactory())
            store = SynchronousMediaStore(
                runtime,
                mode="url_or_inline",
                reference_stager=None,
                staging_directory=None,
            )
            try:
                for operation in (
                    lambda: store.store_bytes_or_provider_content(
                        ReferenceInput(kind="provider_content"), {}
                    ),
                    lambda: store.fetch_bound_content(None, None),
                ):
                    with self.assertRaises(MediaStoreOperationError) as raised:
                        operation()
                    self.assertEqual(
                        raised.exception.failure.code, "configuration_unavailable"
                    )
                self.assertEqual(calls, 0)
                self.assertEqual(runtime.state, "new")
            finally:
                runtime.close()

    def test_managed_url_normalization_is_pure_and_returns_unmanaged_marker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            runtime = _runtime(directory)
            store = SynchronousMediaStore(
                runtime,
                mode="url_or_inline",
                reference_stager=None,
                staging_directory=None,
            )
            try:
                url = "https://legacy.example.test/media/a%2fb?sig=x%2Fy"
                normalized = store.normalize_managed_url(
                    url,
                    {"https://legacy.example.test": "https://cdn.example.test"},
                )
                self.assertEqual(
                    normalized, "https://cdn.example.test/media/a%2fb?sig=x%2Fy"
                )
                self.assertEqual(
                    store.normalize_managed_url(
                        "https://other.example.test/media/a", {}
                    ),
                    "unmanaged",
                )
                self.assertEqual(runtime.state, "new")
            finally:
                runtime.close()

    def test_private_content_fails_before_resolution_and_carries_safe_failure(self) -> None:
        calls = 0

        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            nonlocal calls
            calls += 1
            return ()

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            runtime = _runtime(
                directory,
                getaddrinfo=getaddrinfo,
                http_library=_UnusedHttpLibrary(),
            )
            store = SynchronousMediaStore(
                runtime,
                mode="url_or_inline",
                reference_stager=_stager(directory),
                staging_directory=None,
            )
            try:
                with self.assertRaises(MediaStoreOperationError) as raised:
                    store.prepare_reference(
                        ReferenceInput(kind="provider_content"),
                        capability=object(),
                        config=_runtime_config(),
                    )
                self.assertEqual(raised.exception.failure.code, "configuration_unavailable")
                self.assertNotIn("test-operation-id", str(raised.exception))
                self.assertEqual(calls, 0)
                self.assertEqual(runtime.state, "new")
            finally:
                runtime.close()

    def test_running_loop_rejects_sync_method_before_staging_effects(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            stager = _stager(directory)
            runtime = _runtime(directory)
            store = SynchronousMediaStore(
                runtime,
                mode="local_muse",
                reference_stager=stager,
                staging_directory=directory / "uploads" / "staging",
            )

            async def call_inside_loop() -> None:
                with self.assertRaisesRegex(RuntimeError, "inside an event loop"):
                    store.prepare_reference(
                        ReferenceInput(kind="bytes", content=b"png", media_type="image/png"),
                        capability=None,
                        config=_runtime_config(),
                    )

            try:
                asyncio.run(call_inside_loop())
                self.assertEqual(
                    tuple((directory / "uploads" / "staging").iterdir()), ()
                )
                self.assertEqual(runtime.state, "new")
            finally:
                runtime.close()

    def test_guard_and_object_policy_cannot_expand_fixed_limits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            runtime = _runtime(directory)
            store = SynchronousMediaStore(
                runtime,
                mode="url_or_inline",
                reference_stager=None,
                staging_directory=None,
            )
            try:
                transfer = store.transfer_public_url(
                    ReferenceInput(kind="public_http", locator="https://example.test/a"),
                    {"max_bytes": 200 * 1024 * 1024 + 1},
                )
                self.assertIsInstance(transfer, TransferFailure)
                self.assertEqual(transfer.code, "invalid_limits")
                self.assertEqual(runtime.state, "new")
                with self.assertRaises(MediaStoreOperationError) as raised:
                    store.store_bytes_or_provider_content(
                        ReferenceInput(
                            kind="bytes", content=b"x", media_type="image/png"
                        ),
                        {"content_type": "application/javascript"},
                    )
                self.assertEqual(raised.exception.failure.code, "invalid_limits")
                self.assertEqual(runtime.state, "new")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
