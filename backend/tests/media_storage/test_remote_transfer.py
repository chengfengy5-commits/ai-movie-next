from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from haoai_backend.generation_contracts import ReferenceInput, StoredMedia, TransferFailure
from haoai_backend.media_storage.pinned_http import EncodedRequestTarget, PinnedPublicMediaFetcher
from haoai_backend.media_storage.public_fetch_policy import PublicFetchPolicy
from haoai_backend.media_storage.remote_staging import RemoteStaging
from haoai_backend.media_storage.remote_transfer import RemoteMediaTransfer
from haoai_backend.media_storage.transfer_types import (
    MediaTypeRule,
    SafeMediaPolicy,
    TRANSFER_CHUNK_BYTES,
    TransferConfigurationError,
)


class Iterator:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = list(chunks)
        self.closed = False

    def __aiter__(self) -> Iterator:
        return self

    async def __anext__(self) -> bytes:
        if not self.chunks:
            raise StopAsyncIteration
        return self.chunks.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class Response:
    status = 200

    def __init__(self, chunks: tuple[bytes, ...], content_length: str | None = None) -> None:
        self.headers = {"Content-Type": "image/png"}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.iterator = Iterator(chunks)
        self.closed = False

    def iter_chunks(self, chunk_size: int) -> Iterator:
        if chunk_size != TRANSFER_CHUNK_BYTES:
            raise AssertionError("unexpected chunk size")
        return self.iterator

    async def close(self) -> None:
        self.closed = True


class Connector:
    def __init__(self, resolver) -> None:
        self.resolver = resolver
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class Session:
    def __init__(self, connector: Connector, response: Response) -> None:
        self.connector = connector
        self.response = response
        self.closed = False

    async def get(self, target, *, headers, timeout_seconds, allow_redirects):
        parsed = urlsplit(target.raw_url)
        await self.connector.resolver.resolve(target.tls_hostname, parsed.port or 443)
        if headers != {"Host": parsed.netloc} or allow_redirects:
            raise AssertionError("unsafe request options")
        return self.response

    async def close(self) -> None:
        self.closed = True


class HttpLibrary:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.connector_value = None
        self.session_value = None

    def encoded_url(self, raw_url: str, *, preserve_encoded: bool) -> EncodedRequestTarget:
        parsed = urlsplit(raw_url)
        return EncodedRequestTarget(raw_url, parsed.netloc, parsed.hostname or "", preserve_encoded)

    def connector(self, resolver, *, tls_hostname, verify_tls, use_dns_cache):
        if not verify_tls or use_dns_cache:
            raise AssertionError("connector security options changed")
        self.connector_value = Connector(resolver)
        return self.connector_value

    def session(
        self,
        connector,
        *,
        trust_env,
        cookies_enabled,
        auto_decompress,
        connector_owned_by_session,
    ):
        if trust_env or cookies_enabled or not auto_decompress or connector_owned_by_session:
            raise AssertionError("session security options changed")
        self.session_value = Session(connector, self.response)
        return self.session_value


class MemoryStorage:
    def __init__(self) -> None:
        self.calls = 0
        self.body: bytes | None = None

    async def store_download(self, download):
        self.calls += 1
        with download.open_reader() as body:
            self.body = body.read()
        return StoredMedia(
            locator="https://media.example.test/result.png",
            content_digest=download.content_digest,
            media_type=download.media_type,
            size_bytes=download.size_bytes,
        )


def _make_policy() -> PublicFetchPolicy:
    async def getaddrinfo(host, port, *, family, type):
        return ((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),)
    return PublicFetchPolicy(getaddrinfo)


class RemoteTransferTests(unittest.IsolatedAsyncioTestCase):
    async def test_collision_exhaustion_preserves_preexisting_caller_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            caller_file = directory / ".haoai-transfer-fixed.part"
            caller_file.write_bytes(b"caller-owned")
            staging = RemoteStaging(directory)

            with patch(
                "haoai_backend.media_storage.remote_staging.secrets.token_hex",
                return_value="fixed",
            ):
                with self.assertRaises(TransferConfigurationError):
                    staging.create(10)

            self.assertEqual(caller_file.read_bytes(), b"caller-owned")
            self.assertEqual(staging.pending_cleanup_count, 0)

    async def test_release_preserves_replacement_and_reports_ownership_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            download = staging.create(10)
            download.write_chunk(b"owned", max_bytes=10)
            download.seal("image/png")
            owned_path = directory / download._name
            held_original = directory / "held-original.part"
            os.replace(owned_path, held_original)
            owned_path.write_bytes(b"caller-replacement")

            outcome = staging.release(download)

            self.assertFalse(outcome.released)
            self.assertIn("ownership", outcome.safe_detail.lower())
            self.assertEqual(owned_path.read_bytes(), b"caller-replacement")
            self.assertEqual(staging.pending_cleanup_count, 1)

            owned_path.unlink()
            os.replace(held_original, owned_path)
            self.assertTrue(staging.retry_pending_cleanup()[0].released)
            self.assertEqual(tuple(directory.iterdir()), ())

    async def test_fd_retry_does_not_close_reused_caller_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            caller_path = directory / "caller.txt"
            caller_path.write_bytes(b"caller-data")
            staging = RemoteStaging(directory)
            download = staging.create(10)
            download.write_chunk(b"owned", max_bytes=10)
            download.seal("image/png")
            owned_fd = download._fd

            real_open = os.open
            real_close = os.close
            real_fstat = os.fstat
            replacement_fds: list[int] = []
            replaced = False
            state_failed = False

            def close_then_reuse(fd: int) -> None:
                nonlocal replaced
                if fd == owned_fd and not replaced:
                    replaced = True
                    real_close(fd)
                    replacement_fd = real_open(caller_path, os.O_RDONLY)
                    replacement_fds.append(replacement_fd)
                    if replacement_fd != fd:
                        raise AssertionError("caller descriptor did not reuse the closed fd")
                    raise OSError("injected close error after the owned fd was closed")
                real_close(fd)

            def fail_unknown_state_once(fd: int):
                nonlocal state_failed
                if fd == owned_fd and replaced and not state_failed:
                    state_failed = True
                    raise OSError("injected unknown descriptor state")
                return real_fstat(fd)

            try:
                with (
                    patch(
                        "haoai_backend.media_storage.transfer_types.os.close",
                        side_effect=close_then_reuse,
                    ),
                    patch(
                        "haoai_backend.media_storage.transfer_types.os.fstat",
                        side_effect=fail_unknown_state_once,
                    ),
                ):
                    outcome = staging.release(download)

                self.assertFalse(outcome.released)
                self.assertTrue(state_failed)
                self.assertEqual(staging.pending_cleanup_count, 1)
                self.assertTrue(staging.retry_pending_cleanup()[0].released)

                caller_fd = replacement_fds[0]
                self.assertEqual(os.fstat(caller_fd).st_size, len(b"caller-data"))
                os.lseek(caller_fd, 0, os.SEEK_SET)
                self.assertEqual(os.read(caller_fd, 32), b"caller-data")
            finally:
                for fd in replacement_fds:
                    try:
                        real_fstat(fd)
                    except OSError:
                        continue
                    real_close(fd)
                if staging.pending_cleanup_count:
                    staging.retry_pending_cleanup()

    async def test_unknown_fd_identity_never_claims_a_reused_caller_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            caller_path = directory / "caller.txt"
            caller_path.write_bytes(b"caller-data")
            staging = RemoteStaging(directory)
            real_open = os.open
            real_close = os.close
            real_fstat = os.fstat
            file_fds: list[int] = []
            file_names: list[str] = []
            file_identities: list[tuple[int, int]] = []
            failed_fstats = 0

            def capture_file_open(path, flags, mode=0o777, *, dir_fd=None):
                fd = real_open(path, flags, mode, dir_fd=dir_fd)
                if dir_fd is not None:
                    file_fds.append(fd)
                    file_names.append(os.fspath(path))
                    actual = real_fstat(fd)
                    file_identities.append((actual.st_dev, actual.st_ino))
                return fd

            def fail_file_identity_twice(fd: int):
                nonlocal failed_fstats
                if file_fds and fd == file_fds[0] and failed_fstats < 2:
                    failed_fstats += 1
                    raise OSError("injected unknown acquired file identity")
                return real_fstat(fd)

            caller_fd = None
            try:
                with (
                    patch(
                        "haoai_backend.media_storage.remote_staging.os.open",
                        side_effect=capture_file_open,
                    ),
                    patch(
                        "haoai_backend.media_storage.remote_staging.os.fstat",
                        side_effect=fail_file_identity_twice,
                    ),
                ):
                    with self.assertRaises(TransferConfigurationError):
                        staging.create(10)

                self.assertEqual(failed_fstats, 2)
                self.assertEqual(staging.pending_cleanup_count, 1)
                owned_fd = file_fds[0]
                real_close(owned_fd)
                caller_fd = real_open(caller_path, os.O_RDWR)
                self.assertEqual(caller_fd, owned_fd)

                outcomes = staging.retry_pending_cleanup()
                self.assertEqual(len(outcomes), 1)
                self.assertFalse(outcomes[0].released)
                self.assertEqual(staging.pending_cleanup_count, 1)
                os.lseek(caller_fd, 0, os.SEEK_SET)
                self.assertEqual(os.read(caller_fd, 32), b"caller-data")
            finally:
                if caller_fd is not None:
                    try:
                        real_fstat(caller_fd)
                    except OSError:
                        caller_fd = None
                    else:
                        real_close(caller_fd)
                        caller_fd = None
                if file_names:
                    owned_path = directory / file_names[0]
                    try:
                        named = owned_path.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        if (named.st_dev, named.st_ino) == file_identities[0]:
                            owned_path.unlink()
                if staging.pending_cleanup_count:
                    staging.retry_pending_cleanup()

    async def test_directory_fd_retry_does_not_close_reused_caller_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            caller_path = directory / "caller.txt"
            caller_path.write_bytes(b"caller-data")
            staging = RemoteStaging(directory)
            download = staging.create(10)
            download.write_chunk(b"owned", max_bytes=10)
            download.seal("image/png")
            owned_fd = download._directory_fd

            real_open = os.open
            real_close = os.close
            real_fstat = os.fstat
            replacement_fds: list[int] = []
            replaced = False
            state_failed = False

            def close_then_reuse(fd: int) -> None:
                nonlocal replaced
                if fd == owned_fd and not replaced:
                    replaced = True
                    real_close(fd)
                    replacement_fd = real_open(caller_path, os.O_RDONLY)
                    replacement_fds.append(replacement_fd)
                    if replacement_fd != fd:
                        raise AssertionError("caller descriptor did not reuse the closed fd")
                    raise OSError("injected close error after the owned directory fd closed")
                real_close(fd)

            def fail_unknown_state_once(fd: int):
                nonlocal state_failed
                if fd == owned_fd and replaced and not state_failed:
                    state_failed = True
                    raise OSError("injected unknown directory descriptor state")
                return real_fstat(fd)

            try:
                with (
                    patch(
                        "haoai_backend.media_storage.transfer_types.os.close",
                        side_effect=close_then_reuse,
                    ),
                    patch(
                        "haoai_backend.media_storage.transfer_types.os.fstat",
                        side_effect=fail_unknown_state_once,
                    ),
                ):
                    outcome = staging.release(download)

                self.assertFalse(outcome.released)
                self.assertTrue(state_failed)
                self.assertEqual(staging.pending_cleanup_count, 1)
                self.assertTrue(staging.retry_pending_cleanup()[0].released)
                caller_fd = replacement_fds[0]
                os.lseek(caller_fd, 0, os.SEEK_SET)
                self.assertEqual(os.read(caller_fd, 32), b"caller-data")
            finally:
                for fd in replacement_fds:
                    try:
                        real_fstat(fd)
                    except OSError:
                        continue
                    real_close(fd)
                if staging.pending_cleanup_count:
                    staging.retry_pending_cleanup()

    async def test_reader_fd_retry_does_not_close_reused_caller_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            caller_path = directory / "caller.txt"
            caller_path.write_bytes(b"caller-data")
            staging = RemoteStaging(directory)
            download = staging.create(10)
            download.write_chunk(b"owned", max_bytes=10)
            download.seal("image/png")

            real_open = os.open
            real_close = os.close
            real_fstat = os.fstat
            reader_fds: list[int] = []
            replacement_fds: list[int] = []
            replaced = False
            state_failed = False
            first_validation_failed = False

            def capture_reader_open(path, flags, mode=0o777, *, dir_fd=None):
                fd = real_open(path, flags, mode, dir_fd=dir_fd)
                if dir_fd is not None:
                    reader_fds.append(fd)
                return fd

            def close_then_reuse(fd: int) -> None:
                nonlocal replaced
                if reader_fds and fd == reader_fds[0] and not replaced:
                    replaced = True
                    real_close(fd)
                    replacement_fd = real_open(caller_path, os.O_RDONLY)
                    replacement_fds.append(replacement_fd)
                    if replacement_fd != fd:
                        raise AssertionError("caller descriptor did not reuse the reader fd")
                    raise OSError("injected reader close error after close took effect")
                real_close(fd)

            def fail_reader_states(fd: int):
                nonlocal first_validation_failed, state_failed
                if reader_fds and fd == reader_fds[0]:
                    if not first_validation_failed:
                        first_validation_failed = True
                        raise OSError("injected reader validation state failure")
                    if replaced and not state_failed:
                        state_failed = True
                        raise OSError("injected unknown reader descriptor state")
                return real_fstat(fd)

            try:
                with (
                    patch(
                        "haoai_backend.media_storage.transfer_types.os.open",
                        side_effect=capture_reader_open,
                    ),
                    patch(
                        "haoai_backend.media_storage.transfer_types.os.close",
                        side_effect=close_then_reuse,
                    ),
                    patch(
                        "haoai_backend.media_storage.transfer_types.os.fstat",
                        side_effect=fail_reader_states,
                    ),
                ):
                    with self.assertRaises(OSError):
                        download.open_reader()
                    self.assertEqual(len(reader_fds), 1)
                    self.assertTrue(replaced)
                    self.assertTrue(state_failed)

                self.assertTrue(staging.release(download).released)
                caller_fd = replacement_fds[0]
                os.lseek(caller_fd, 0, os.SEEK_SET)
                self.assertEqual(os.read(caller_fd, 32), b"caller-data")
            finally:
                for fd in replacement_fds:
                    try:
                        real_fstat(fd)
                    except OSError:
                        continue
                    real_close(fd)
                if staging.pending_cleanup_count:
                    staging.retry_pending_cleanup()

    async def test_failed_reader_validation_closes_or_retains_the_real_fd(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            download = staging.create(10)
            download.write_chunk(b"owned", max_bytes=10)
            download.seal("image/png")
            real_fstat = os.fstat
            real_close = os.close
            opened_reader_fds: list[int] = []
            calls = 0
            failed_close = False

            def fail_reader_fstat(fd: int):
                nonlocal calls
                calls += 1
                if calls == 1:
                    opened_reader_fds.append(fd)
                    raise OSError("injected fstat failure after real open")
                return real_fstat(fd)

            def fail_reader_close_once(fd: int) -> None:
                nonlocal failed_close
                if opened_reader_fds and fd == opened_reader_fds[0] and not failed_close:
                    failed_close = True
                    raise OSError("injected reader close failure")
                real_close(fd)

            with patch(
                "haoai_backend.media_storage.transfer_types.os.fstat",
                side_effect=fail_reader_fstat,
            ), patch(
                "haoai_backend.media_storage.transfer_types.os.close",
                side_effect=fail_reader_close_once,
            ):
                with self.assertRaises(OSError):
                    download.open_reader()

            self.assertEqual(len(opened_reader_fds), 1)
            reader_fd = opened_reader_fds[0]
            os.fstat(reader_fd)
            self.assertTrue(staging.release(download).released)
            reader_still_open = False
            try:
                os.fstat(reader_fd)
                reader_still_open = True
            except OSError:
                pass
            if reader_still_open:
                real_close(reader_fd)
            self.assertTrue(failed_close)
            self.assertFalse(reader_still_open)

    async def test_failed_staging_acquire_retains_a_descriptor_after_close_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            real_open = os.open
            real_close = os.close
            file_fds: list[int] = []
            failed_once = False

            def capture_open(path, flags, mode=0o777, *, dir_fd=None):
                fd = real_open(path, flags, mode, dir_fd=dir_fd)
                if dir_fd is not None:
                    file_fds.append(fd)
                return fd

            def fail_file_close_once(fd: int) -> None:
                nonlocal failed_once
                if file_fds and fd == file_fds[0] and not failed_once:
                    failed_once = True
                    raise OSError("injected close failure after real file acquire")
                real_close(fd)

            with (
                patch(
                    "haoai_backend.media_storage.remote_staging.os.open",
                    side_effect=capture_open,
                ),
                patch(
                    "haoai_backend.media_storage.remote_staging.os.close",
                    side_effect=fail_file_close_once,
                ),
                patch(
                    "haoai_backend.media_storage.remote_staging.OwnedDownload",
                    side_effect=OSError("injected owner construction failure"),
                ),
            ):
                with self.assertRaises(TransferConfigurationError):
                    staging.create(10)

            self.assertTrue(failed_once)
            pending_count = staging.pending_cleanup_count
            self.assertEqual(len(file_fds), 1)
            os.fstat(file_fds[0])
            if pending_count:
                outcomes = staging.retry_pending_cleanup()
                self.assertEqual(len(outcomes), 1)
                self.assertTrue(outcomes[0].released)
            else:
                real_close(file_fds[0])
            self.assertEqual(pending_count, 1)
            still_open = False
            try:
                os.fstat(file_fds[0])
                still_open = True
            except OSError:
                pass
            self.assertFalse(still_open)
            self.assertEqual(tuple(directory.iterdir()), ())

    async def test_acquire_cancel_a_wins_and_retains_the_directory_owner(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            acquire_cancel = asyncio.CancelledError("acquire A")
            close_cancel = asyncio.CancelledError("close B")
            real_open = os.open
            real_close = os.close
            directory_fds: list[int] = []
            failed_close = False

            def cancel_file_open(path, flags, mode=0o777, *, dir_fd=None):
                if dir_fd is not None:
                    raise acquire_cancel
                fd = real_open(path, flags, mode, dir_fd=dir_fd)
                directory_fds.append(fd)
                return fd

            def fail_directory_close_once(fd: int) -> None:
                nonlocal failed_close
                if directory_fds and fd == directory_fds[0] and not failed_close:
                    failed_close = True
                    raise close_cancel
                real_close(fd)

            caught = None
            with (
                patch(
                    "haoai_backend.media_storage.remote_staging.os.open",
                    side_effect=cancel_file_open,
                ),
                patch(
                    "haoai_backend.media_storage.remote_staging.os.close",
                    side_effect=fail_directory_close_once,
                ),
            ):
                try:
                    staging.create(10)
                except asyncio.CancelledError as exc:
                    caught = exc

            self.assertIs(caught, acquire_cancel)
            self.assertTrue(failed_close)
            self.assertEqual(staging.pending_cleanup_count, 1)
            os.fstat(directory_fds[0])
            outcomes = staging.retry_pending_cleanup()
            self.assertEqual(len(outcomes), 1)
            self.assertTrue(outcomes[0].released)
            with self.assertRaises(OSError):
                os.fstat(directory_fds[0])
            self.assertEqual(tuple(directory.iterdir()), ())

    async def test_write_cancel_a_wins_over_spool_cleanup_cancel_b(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            response = Response((b"payload",))
            library = HttpLibrary(response)
            fetcher = PinnedPublicMediaFetcher(
                _make_policy(),
                SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
                http_library=library,
                staging=staging,
            )
            write_cancel = asyncio.CancelledError("spool write A")
            cleanup_cancel = asyncio.CancelledError("spool cleanup B")
            original_unlink = os.unlink

            def cancel_write(fd: int, data: bytes) -> int:
                raise write_cancel

            def cancel_owned_unlink(path, *args, **kwargs):
                name = path if isinstance(path, str) else os.fspath(path)
                if isinstance(name, str) and name.startswith(".haoai-transfer-"):
                    raise cleanup_cancel
                return original_unlink(path, *args, **kwargs)

            caught = None
            with (
                patch(
                    "haoai_backend.media_storage.transfer_types.os.write",
                    side_effect=cancel_write,
                ),
                patch(
                    "haoai_backend.media_storage.transfer_types.os.unlink",
                    side_effect=cancel_owned_unlink,
                ),
            ):
                try:
                    await fetcher.fetch_to_owned(
                        "https://example.test/p.png", max_bytes=100, timeout_seconds=10
                    )
                except asyncio.CancelledError as exc:
                    caught = exc

            self.assertIs(caught, write_cancel)
            self.assertEqual(staging.pending_cleanup_count, 1)
            self.assertTrue(response.iterator.closed)
            self.assertTrue(response.closed)
            self.assertTrue(library.session_value.closed)
            self.assertTrue(library.connector_value.closed)
            self.assertTrue(staging.retry_pending_cleanup()[0].released)
            self.assertEqual(staging.pending_cleanup_count, 0)
            self.assertEqual(tuple(directory.iterdir()), ())

    async def test_retry_cancel_attempts_every_pending_spool_close(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            downloads = []
            for _ in range(2):
                download = staging.create(10)
                download.write_chunk(b"x", max_bytes=10)
                download.seal("image/png")
                downloads.append(download)

            cancellation = asyncio.CancelledError("retry cleanup B")
            original_unlink = os.unlink

            def cancel_owned_unlinks(path, *args, **kwargs):
                name = path if isinstance(path, str) else os.fspath(path)
                if isinstance(name, str) and name.startswith(".haoai-transfer-"):
                    raise cancellation
                return original_unlink(path, *args, **kwargs)

            with patch(
                "haoai_backend.media_storage.transfer_types.os.unlink",
                side_effect=cancel_owned_unlinks,
            ):
                for download in downloads:
                    with self.assertRaises(asyncio.CancelledError):
                        staging.release(download)
                self.assertEqual(staging.pending_cleanup_count, 2)

                with self.assertRaises(asyncio.CancelledError) as caught:
                    staging.retry_pending_cleanup()

            self.assertIs(caught.exception, cancellation)
            self.assertEqual(staging.pending_cleanup_count, 2)
            outcomes = staging.retry_pending_cleanup()
            self.assertEqual(len(outcomes), 2)
            self.assertTrue(all(outcome.released for outcome in outcomes))
            self.assertEqual(staging.pending_cleanup_count, 0)
            self.assertEqual(tuple(directory.iterdir()), ())

    async def test_concurrent_retry_cannot_cancel_out_new_pending_growth(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            second_entered = asyncio.Event()
            allow_second = asyncio.Event()
            third_entered = asyncio.Event()
            fourth_entered = asyncio.Event()
            allow_third_and_fourth = asyncio.Event()
            fail_names: set[str] = set()
            calls = 0

            class BarrierFetcher:
                async def fetch_to_owned(self, url, *, max_bytes, timeout_seconds):
                    nonlocal calls
                    calls += 1
                    request_number = calls
                    if request_number == 2:
                        second_entered.set()
                        await allow_second.wait()
                    elif request_number in (3, 4):
                        (third_entered if request_number == 3 else fourth_entered).set()
                        await allow_third_and_fourth.wait()

                    download = staging.create(max_bytes)
                    download.write_chunk(b"x", max_bytes=max_bytes)
                    download.seal("image/png")
                    fail_names.add(download._name)
                    staging.release(download)
                    return TransferFailure("http_failure", "injected fetch failure")

            transfer = RemoteMediaTransfer(
                BarrierFetcher(), staging, MemoryStorage(), shared_slots=asyncio.Semaphore(2)
            )
            reference = ReferenceInput(kind="public_http", locator="https://example.test/p.png")
            original_unlink = os.unlink

            def fail_owned_unlink(path, *args, **kwargs):
                name = path if isinstance(path, str) else os.fspath(path)
                if (
                    isinstance(name, str)
                    and name.startswith(".haoai-transfer-")
                    and name in fail_names
                ):
                    fail_names.remove(name)
                    raise OSError("injected request-owned spool cleanup failure")
                return original_unlink(path, *args, **kwargs)

            second_task = None
            third_task = None
            fourth_task = None
            try:
                with patch(
                    "haoai_backend.media_storage.transfer_types.os.unlink",
                    side_effect=fail_owned_unlink,
                ):
                    first = await transfer.transfer(
                        reference, max_bytes=10, timeout_seconds=2
                    )
                    self.assertIsInstance(first, TransferFailure)
                    self.assertEqual(staging.pending_cleanup_count, 1)

                    second_task = asyncio.create_task(
                        transfer.transfer(reference, max_bytes=10, timeout_seconds=2)
                    )
                    await asyncio.wait_for(second_entered.wait(), timeout=1)
                    self.assertEqual(await transfer.retry_pending_cleanup(), 1)
                    self.assertEqual(staging.pending_cleanup_count, 0)

                    allow_second.set()
                    second = await second_task
                    self.assertIsInstance(second, TransferFailure)
                    self.assertEqual(staging.pending_cleanup_count, 1)

                    third_task = asyncio.create_task(
                        transfer.transfer(reference, max_bytes=10, timeout_seconds=0.3)
                    )
                    fourth_task = asyncio.create_task(
                        transfer.transfer(reference, max_bytes=10, timeout_seconds=0.3)
                    )
                    await asyncio.wait_for(third_entered.wait(), timeout=1)
                    await asyncio.sleep(0.05)
                    self.assertEqual(
                        calls,
                        3,
                        "a net-zero pending count must not release the second request lease",
                    )
                    self.assertFalse(fourth_entered.is_set())

                    allow_third_and_fourth.set()
                    third = await third_task
                    fourth = await fourth_task
                    self.assertIsInstance(third, TransferFailure)
                    self.assertIsInstance(fourth, TransferFailure)
                    self.assertEqual(calls, 3)
                    self.assertEqual(staging.pending_cleanup_count, 2)
            finally:
                allow_second.set()
                allow_third_and_fourth.set()
                active = [
                    task
                    for task in (second_task, third_task, fourth_task)
                    if task is not None and not task.done()
                ]
                if active:
                    await asyncio.gather(*active, return_exceptions=True)
                if staging.pending_cleanup_count:
                    fail_names.clear()
                    await transfer.retry_pending_cleanup()

            self.assertEqual(staging.pending_cleanup_count, 0)

    async def test_two_pending_spools_keep_both_slots_until_cleanup_retry(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            calls = 0

            class OwnedFetcher:
                async def fetch_to_owned(self, url, *, max_bytes, timeout_seconds):
                    nonlocal calls
                    calls += 1
                    download = staging.create(max_bytes)
                    download.write_chunk(b"x", max_bytes=max_bytes)
                    download.seal("image/png")
                    fail_names.add(download._name)
                    return download

            storage = MemoryStorage()
            transfer = RemoteMediaTransfer(
                OwnedFetcher(), staging, storage, shared_slots=asyncio.Semaphore(2)
            )
            reference = ReferenceInput(kind="public_http", locator="https://example.test/p.png")
            original_unlink = os.unlink
            fail_names: set[str] = set()

            def fail_owned_unlink(path, *args, **kwargs):
                name = path if isinstance(path, str) else os.fspath(path)
                if (
                    isinstance(name, str)
                    and name.startswith(".haoai-transfer-")
                    and name in fail_names
                ):
                    fail_names.remove(name)
                    raise OSError("injected local unlink failure")
                return original_unlink(path, *args, **kwargs)

            try:
                with patch(
                    "haoai_backend.media_storage.transfer_types.os.unlink",
                    side_effect=fail_owned_unlink,
                ):
                    first = await transfer.transfer(reference, max_bytes=10, timeout_seconds=2)
                    second = await transfer.transfer(reference, max_bytes=10, timeout_seconds=2)
                    self.assertIsInstance(first, TransferFailure)
                    self.assertIsInstance(second, TransferFailure)
                    self.assertEqual(staging.pending_cleanup_count, 2)

                    third = asyncio.create_task(
                        transfer.transfer(reference, max_bytes=10, timeout_seconds=0.15)
                    )
                    await asyncio.sleep(0.03)
                    self.assertEqual(calls, 2)
                    third_result = await third
                    self.assertIsInstance(third_result, TransferFailure)
                    self.assertEqual(calls, 2)

                self.assertEqual(await transfer.retry_pending_cleanup(), 2)
            finally:
                if staging.pending_cleanup_count:
                    fail_names.clear()
                    staging.retry_pending_cleanup()

            self.assertEqual(staging.pending_cleanup_count, 0)
            fourth = await transfer.transfer(reference, max_bytes=10, timeout_seconds=2)
            self.assertIsInstance(fourth, StoredMedia)
            self.assertEqual(calls, 3)
            self.assertEqual(tuple(directory.iterdir()), ())

    async def test_operation_cancel_identity_survives_spool_cancel_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            operation_cancel = asyncio.CancelledError("operation A")
            cleanup_cancel = asyncio.CancelledError("cleanup B")
            created = []

            class OneDownloadFetcher:
                async def fetch_to_owned(self, url, *, max_bytes, timeout_seconds):
                    download = staging.create(max_bytes)
                    download.write_chunk(b"x", max_bytes=max_bytes)
                    download.seal("image/png")
                    created.append(download)
                    return download

            class CancelledStorage:
                async def store_download(self, download):
                    raise operation_cancel

            original_unlink = os.unlink

            def cancel_unlink(path, *args, **kwargs):
                name = path if isinstance(path, str) else os.fspath(path)
                if isinstance(name, str) and name.startswith(".haoai-transfer-"):
                    raise cleanup_cancel
                return original_unlink(path, *args, **kwargs)

            transfer = RemoteMediaTransfer(
                OneDownloadFetcher(), staging, CancelledStorage(), shared_slots=asyncio.Semaphore(1)
            )
            reference = ReferenceInput(kind="public_http", locator="https://example.test/p.png")
            caught = None
            with patch(
                "haoai_backend.media_storage.transfer_types.os.unlink",
                side_effect=cancel_unlink,
            ):
                try:
                    await transfer.transfer(reference, max_bytes=10, timeout_seconds=2)
                except asyncio.CancelledError as exc:
                    caught = exc

            pending_after_cancel = staging.pending_cleanup_count
            self.assertEqual(len(created), 1)
            if pending_after_cancel:
                self.assertEqual(await transfer.retry_pending_cleanup(), 1)
            else:
                staging.release(created[0])
            self.assertEqual(pending_after_cancel, 1)
            self.assertIs(caught, operation_cancel)
            self.assertEqual(staging.pending_cleanup_count, 0)
            self.assertEqual(tuple(directory.iterdir()), ())
    async def test_streams_to_owned_file_and_preserves_caller_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            caller_file = directory / "caller.txt"
            caller_file.write_bytes(b"keep")
            staging = RemoteStaging(directory)
            response = Response((b"abc", b"def"))
            library = HttpLibrary(response)
            policy = SafeMediaPolicy((MediaTypeRule("image/png", ".png"),))
            fetcher = PinnedPublicMediaFetcher(
                _make_policy(), policy, http_library=library, staging=staging
            )
            storage = MemoryStorage()
            transfer = RemoteMediaTransfer(
                fetcher, staging, storage, shared_slots=asyncio.Semaphore(2)
            )

            result = await transfer.transfer(
                ReferenceInput(kind="public_http", locator="https://example.test/p.png"),
                max_bytes=100,
                timeout_seconds=10,
            )

            self.assertIsInstance(result, StoredMedia)
            self.assertEqual(storage.body, b"abcdef")
            self.assertEqual(result.content_digest, hashlib.sha256(b"abcdef").hexdigest())
            self.assertEqual(result.size_bytes, 6)
            self.assertEqual(storage.calls, 1)
            self.assertEqual(caller_file.read_bytes(), b"keep")
            self.assertEqual(staging.pending_cleanup_count, 0)
            self.assertEqual(sorted(path.name for path in directory.iterdir()), ["caller.txt"])
            self.assertTrue(response.iterator.closed)
            self.assertTrue(response.closed)
            self.assertTrue(library.session_value.closed)
            self.assertTrue(library.connector_value.closed)

    async def test_actual_size_limit_stops_before_storage_and_cleans_spool(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            response = Response((b"x" * 64, b"y" * 37), content_length="1")
            library = HttpLibrary(response)
            policy = SafeMediaPolicy((MediaTypeRule("image/png", ".png"),))
            fetcher = PinnedPublicMediaFetcher(
                _make_policy(), policy, http_library=library, staging=staging
            )
            storage = MemoryStorage()
            transfer = RemoteMediaTransfer(
                fetcher, staging, storage, shared_slots=asyncio.Semaphore(2)
            )

            result = await transfer.transfer(
                ReferenceInput(kind="managed_url", locator="https://example.test/p.png"),
                max_bytes=100,
                timeout_seconds=10,
            )

            self.assertIsInstance(result, TransferFailure)
            self.assertEqual(result.code, "reference_too_large")
            self.assertEqual(storage.calls, 0)
            self.assertEqual(staging.pending_cleanup_count, 0)
            self.assertEqual(tuple(directory.iterdir()), ())

    async def test_shared_slots_limit_owned_spool_lifecycle_to_two(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            staging = RemoteStaging(directory)
            entered = asyncio.Event()
            release = asyncio.Event()
            active = 0
            maximum = 0
            calls = 0

            class BlockingFetcher:
                async def fetch_to_owned(self, url, *, max_bytes, timeout_seconds):
                    nonlocal active, maximum, calls
                    calls += 1
                    active += 1
                    maximum = max(maximum, active)
                    entered.set()
                    await release.wait()
                    download = staging.create(max_bytes)
                    download.write_chunk(b"x", max_bytes=max_bytes)
                    download.seal("image/png")
                    active -= 1
                    return download

            storage = MemoryStorage()
            transfer = RemoteMediaTransfer(
                BlockingFetcher(), staging, storage, shared_slots=asyncio.Semaphore(2)
            )
            reference = ReferenceInput(kind="public_http", locator="https://example.test/p.png")
            tasks = [
                asyncio.create_task(
                    transfer.transfer(reference, max_bytes=10, timeout_seconds=20)
                )
                for _ in range(3)
            ]
            await entered.wait()
            await asyncio.sleep(0)
            self.assertEqual(calls, 2)
            self.assertEqual(maximum, 2)
            tasks[2].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await tasks[2]
            release.set()
            results = await asyncio.gather(*tasks[:2])

            self.assertTrue(all(isinstance(result, StoredMedia) for result in results))
            self.assertEqual(calls, 2)
            self.assertEqual(maximum, 2)
            self.assertEqual(storage.calls, 2)
            self.assertEqual(staging.pending_cleanup_count, 0)
            self.assertEqual(tuple(directory.iterdir()), ())


if __name__ == "__main__":
    unittest.main()
