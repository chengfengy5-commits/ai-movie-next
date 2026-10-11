from __future__ import annotations

import asyncio
import base64
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haoai_backend.generation_contracts import PreparedReference, ReferenceInput, TransferFailure
from haoai_backend.media_storage import reference_staging as staging_module
from haoai_backend.media_storage.reference_staging import (
    MAX_REFERENCE_BYTES,
    ReferenceStager,
    ReferenceStagingPolicy,
    WRITE_CHUNK_BYTES,
)


class ReferenceStagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)
        self.upload_directory = self.base / "approved-uploads"
        self.upload_directory.mkdir()
        self.staging_directory = self.upload_directory / "request-stage"
        self.staging_directory.mkdir()
        self.policy = ReferenceStagingPolicy(max_bytes=128)
        self.stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=self.policy,
        )

    def stage(self, reference: ReferenceInput) -> PreparedReference | TransferFailure:
        return self.stager.stage_local_reference(reference, self.staging_directory)

    def staged_files(self) -> list[Path]:
        return list(self.staging_directory.rglob(".muse-reference-*"))

    def test_constructor_is_inert_and_requires_explicit_absolute_root_and_policy(self) -> None:
        absent_root = self.base / "not-created"
        ReferenceStager(
            approved_upload_directory=absent_root,
            policy=ReferenceStagingPolicy(max_bytes=1),
        )
        self.assertFalse(absent_root.exists())
        with self.assertRaises(ValueError):
            ReferenceStagingPolicy(max_bytes=MAX_REFERENCE_BYTES + 1)
        with self.assertRaises(ValueError):
            ReferenceStager(
                approved_upload_directory=Path("relative-upload-dir"),
                policy=self.policy,
            )

    def test_bytes_stage_in_chunks_as_private_owned_absolute_path(self) -> None:
        result = self.stage(
            ReferenceInput(kind="bytes", content=b"png-data", media_type="image/png")
        )
        self.assertIsInstance(result, PreparedReference)
        self.assertEqual(result.kind, "local_muse_path")
        self.assertEqual(result.media_type, "image/png")
        self.assertIsNone(result.content)
        self.assertTrue(Path(result.locator or "").is_absolute())
        self.assertEqual(Path(result.locator or "").read_bytes(), b"png-data")
        self.assertEqual(stat.S_IMODE(Path(result.locator or "").stat().st_mode) & 0o077, 0)
        self.assertEqual(self.stager.release_prepared_reference(result), "released")
        self.assertFalse(Path(result.locator or "").exists())
        self.assertEqual(self.stager.release_prepared_reference(result), "already_released")

    def test_successful_write_uses_bounded_chunks(self) -> None:
        payload = b"p" * (WRITE_CHUNK_BYTES + 9)
        stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=ReferenceStagingPolicy(max_bytes=len(payload)),
        )
        real_write = os.write
        attempted_sizes: list[int] = []
        written_sizes: list[int] = []

        def record_write(descriptor: int, data: memoryview) -> int:
            attempted_sizes.append(len(data))
            written = real_write(descriptor, data)
            written_sizes.append(written)
            return written

        with patch(
            "haoai_backend.media_storage.reference_staging.os.write",
            side_effect=record_write,
        ):
            result = stager.stage_local_reference(
                ReferenceInput(kind="bytes", content=payload, media_type="image/png"),
                self.staging_directory,
            )
        self.assertIsInstance(result, PreparedReference)
        self.assertEqual(max(attempted_sizes), WRITE_CHUNK_BYTES)
        self.assertEqual(sum(written_sizes), len(payload))
        self.assertEqual(Path(result.locator or "").read_bytes(), payload)
        self.assertEqual(stager.release_prepared_reference(result), "released")

    def test_data_uri_is_strictly_decoded_to_canonical_file_extension(self) -> None:
        encoded = base64.b64encode(b"jpeg-data").decode("ascii")
        result = self.stage(
            ReferenceInput(
                kind="data_uri",
                locator=f"data:image/jpeg;base64,{encoded}",
                media_type="IMAGE/JPEG",
            )
        )
        self.assertIsInstance(result, PreparedReference)
        path = Path(result.locator or "")
        self.assertEqual(path.suffix, ".jpg")
        self.assertEqual(path.read_bytes(), b"jpeg-data")
        self.assertEqual(result.media_type, "image/jpeg")
        self.assertEqual(self.stager.release_prepared_reference(result), "released")

    def test_data_uri_rejects_encoded_and_decoded_oversize_before_staging(self) -> None:
        small_stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=ReferenceStagingPolicy(max_bytes=4),
        )
        encoded_oversize = small_stager.stage_local_reference(
            ReferenceInput(
                kind="data_uri",
                locator="data:image/png;base64," + "A" * 12,
            ),
            self.staging_directory,
        )
        decoded_oversize = small_stager.stage_local_reference(
            ReferenceInput(
                kind="data_uri",
                locator="data:image/png;base64," + base64.b64encode(b"12345").decode("ascii"),
            ),
            self.staging_directory,
        )
        for result in (encoded_oversize, decoded_oversize):
            self.assertIsInstance(result, TransferFailure)
            self.assertEqual(result.code, "reference_too_large")
        self.assertEqual(self.staged_files(), [])

    def test_bytes_and_data_uri_reject_invalid_or_oversize_inputs(self) -> None:
        cases = (
            ReferenceInput(kind="bytes", content=b"x" * 129, media_type="image/png"),
            ReferenceInput(kind="bytes", content=b"x", media_type="application/json"),
            ReferenceInput(kind="bytes", content=b"x", media_type="image/svg+xml"),
            ReferenceInput(
                kind="bytes",
                locator="data:image/png;base64,eA==",
                content=b"x",
                media_type="image/png",
            ),
            ReferenceInput(kind="data_uri", locator="data:image/png;base64,%%%"),
            ReferenceInput(kind="data_uri", locator="data:image/png,eA=="),
            ReferenceInput(kind="data_uri", locator="data:text/html;base64,PGgxPjwvaDE+"),
            ReferenceInput(
                kind="data_uri",
                locator="data:image/png;base64,eA==",
                media_type="image/jpeg",
            ),
        )
        results = [self.stage(reference) for reference in cases]
        self.assertTrue(all(isinstance(result, TransferFailure) for result in results))
        self.assertEqual(results[0].code, "reference_too_large")
        self.assertEqual(results[2].code, "active_media_type")
        self.assertEqual(results[4].code, "invalid_data_uri")
        self.assertEqual(results[6].code, "active_media_type")
        self.assertEqual(results[7].code, "media_type_mismatch")
        self.assertEqual(self.staged_files(), [])

    def test_only_typed_reference_kinds_are_supported_without_fetching(self) -> None:
        for kind in ("public_http", "managed_url", "provider_content"):
            with self.subTest(kind=kind):
                result = self.stage(
                    ReferenceInput(kind=kind, locator="https://example.invalid/ref.png")
                )
                self.assertIsInstance(result, TransferFailure)
                self.assertEqual(result.code, "unsupported_reference_kind")
        self.assertEqual(self.staged_files(), [])

    def test_local_path_must_be_approved_regular_and_type_consistent(self) -> None:
        source = self.upload_directory / "caller-image.PNG"
        source.write_bytes(b"caller-owned")
        result = self.stage(
            ReferenceInput(kind="local_muse_path", locator=str(source), media_type="image/png")
        )
        self.assertIsInstance(result, PreparedReference)
        self.assertEqual(result.locator, str(source.resolve()))
        self.assertEqual(result.media_type, "image/png")
        self.assertEqual(self.stager.release_prepared_reference(result), "not_owned")
        self.assertEqual(source.read_bytes(), b"caller-owned")

        mismatch = self.stage(
            ReferenceInput(kind="local_muse_path", locator=str(source), media_type="image/jpeg")
        )
        outside = self.base / "outside.png"
        outside.write_bytes(b"outside")
        traversal = self.stage(
            ReferenceInput(
                kind="local_muse_path",
                locator=str(self.upload_directory / ".." / "outside.png"),
            )
        )
        missing_path = self.upload_directory / "missing.png"
        missing = self.stage(
            ReferenceInput(kind="local_muse_path", locator=str(missing_path))
        )
        directory = self.stage(
            ReferenceInput(kind="local_muse_path", locator=str(self.staging_directory))
        )
        too_large_path = self.upload_directory / "large.png"
        too_large_path.write_bytes(b"x" * 129)
        too_large = self.stage(
            ReferenceInput(kind="local_muse_path", locator=str(too_large_path))
        )
        self.assertEqual(mismatch.code, "media_type_mismatch")
        self.assertEqual(traversal.code, "invalid_local_path")
        self.assertEqual(missing.code, "invalid_local_path")
        self.assertEqual(directory.code, "invalid_local_path")
        self.assertEqual(too_large.code, "reference_too_large")
        self.assertEqual(source.read_bytes(), b"caller-owned")
        self.assertEqual(self.staged_files(), [])

    def test_local_symlink_escape_and_unapproved_staging_directory_are_rejected(self) -> None:
        outside = self.base / "outside.png"
        outside.write_bytes(b"outside")
        escaped = self.upload_directory / "escape.png"
        try:
            escaped.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        result = self.stage(
            ReferenceInput(kind="local_muse_path", locator=str(escaped))
        )
        outside_stage = self.base / "outside-stage"
        outside_stage.mkdir()
        rejected_stage = self.stager.stage_local_reference(
            ReferenceInput(kind="bytes", content=b"data", media_type="image/png"),
            outside_stage,
        )
        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "local_path_not_approved")
        self.assertIsInstance(rejected_stage, TransferFailure)
        self.assertEqual(rejected_stage.code, "staging_directory_not_approved")
        self.assertEqual(outside.read_bytes(), b"outside")
        self.assertEqual(self.staged_files(), [])

    def test_forged_and_foreign_handles_cannot_release_owned_or_caller_files(self) -> None:
        result = self.stage(
            ReferenceInput(kind="bytes", content=b"generated", media_type="image/png")
        )
        self.assertIsInstance(result, PreparedReference)
        generated_path = Path(result.locator or "")
        forged = PreparedReference(
            kind=result.kind,
            locator=result.locator,
            content=result.content,
            media_type=result.media_type,
        )
        other_stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=self.policy,
        )
        self.assertEqual(forged, result)
        self.assertEqual(self.stager.release_prepared_reference(forged), "not_owned")
        self.assertEqual(other_stager.release_prepared_reference(result), "not_owned")
        caller_file = self.upload_directory / "caller.png"
        caller_file.write_bytes(b"caller")
        fake_caller_handle = PreparedReference(
            kind="local_muse_path",
            locator=str(caller_file),
            content=None,
            media_type="image/png",
        )
        self.assertEqual(self.stager.release_prepared_reference(fake_caller_handle), "not_owned")
        self.assertTrue(generated_path.exists())
        self.assertEqual(caller_file.read_bytes(), b"caller")
        self.assertEqual(self.stager.release_prepared_reference(result), "released")

    def test_replacement_and_symlink_never_authorize_deleting_caller_file(self) -> None:
        for replacement_kind in ("regular", "symlink"):
            with self.subTest(replacement_kind=replacement_kind):
                result = self.stage(
                    ReferenceInput(kind="bytes", content=b"generated", media_type="image/png")
                )
                self.assertIsInstance(result, PreparedReference)
                path = Path(result.locator or "")
                path.unlink()
                target = self.base / f"replacement-{replacement_kind}.png"
                target.write_bytes(b"caller-replacement")
                if replacement_kind == "symlink":
                    try:
                        path.symlink_to(target)
                    except (OSError, NotImplementedError) as exc:
                        self.skipTest(f"symlink unavailable: {exc}")
                else:
                    path.write_bytes(b"caller-replacement")
                self.assertEqual(self.stager.release_prepared_reference(result), "not_owned")
                if replacement_kind == "symlink":
                    self.assertTrue(path.is_symlink())
                else:
                    self.assertEqual(path.read_bytes(), b"caller-replacement")
                self.assertEqual(target.read_bytes(), b"caller-replacement")

    def test_partial_write_error_removes_only_the_new_owned_file(self) -> None:
        payload = b"p" * (WRITE_CHUNK_BYTES + 8)
        large_stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=ReferenceStagingPolicy(max_bytes=len(payload)),
        )
        real_write = os.write
        calls = 0

        def partial_then_fail(descriptor: int, data: memoryview) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(descriptor, data[:7])
            raise OSError("simulated disk full")

        with patch(
            "haoai_backend.media_storage.reference_staging.os.write",
            side_effect=partial_then_fail,
        ):
            result = large_stager.stage_local_reference(
                ReferenceInput(kind="bytes", content=payload, media_type="image/png"),
                self.staging_directory,
            )
        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "staging_failed")
        self.assertEqual(calls, 2)
        self.assertEqual(self.staged_files(), [])

    def test_partial_write_cancellation_removes_the_new_owned_file(self) -> None:
        payload = b"p" * (WRITE_CHUNK_BYTES + 8)
        large_stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=ReferenceStagingPolicy(max_bytes=len(payload)),
        )
        real_write = os.write
        calls = 0

        def partial_then_cancel(descriptor: int, data: memoryview) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(descriptor, data[:7])
            raise asyncio.CancelledError

        with patch(
            "haoai_backend.media_storage.reference_staging.os.write",
            side_effect=partial_then_cancel,
        ):
            with self.assertRaises(asyncio.CancelledError):
                large_stager.stage_local_reference(
                    ReferenceInput(kind="bytes", content=payload, media_type="image/png"),
                    self.staging_directory,
                )
        self.assertEqual(calls, 2)
        self.assertEqual(self.staged_files(), [])


    def test_directory_replacement_after_validation_never_writes_through_symlink(self) -> None:
        outside_stage = self.base / "outside-stage"
        outside_stage.mkdir()
        moved_stage = self.upload_directory / "renamed-stage"
        original_create = self.stager._create_private_namespace

        def replace_stage_path(directories):
            self.staging_directory.rename(moved_stage)
            self.staging_directory.symlink_to(outside_stage, target_is_directory=True)
            return original_create(directories)

        with patch.object(
            self.stager,
            "_create_private_namespace",
            side_effect=replace_stage_path,
        ):
            result = self.stage(
                ReferenceInput(kind="bytes", content=b"must-not-escape", media_type="image/png")
            )

        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "staging_directory_changed")
        self.assertEqual(list(outside_stage.iterdir()), [])
        self.assertEqual(list(moved_stage.rglob(".muse-reference-*")), [])
        self.assertFalse(any(moved_stage.iterdir()))

    def test_release_preserves_replacement_racing_atomic_capture(self) -> None:
        result = self.stage(
            ReferenceInput(kind="bytes", content=b"generated", media_type="image/png")
        )
        self.assertIsInstance(result, PreparedReference)
        path = Path(result.locator or "")
        real_rename = os.rename
        replaced = False

        def replace_before_capture(source, destination, *args, **kwargs):
            nonlocal replaced
            if not replaced:
                replaced = True
                path.unlink()
                path.write_bytes(b"caller-replacement")
            return real_rename(source, destination, *args, **kwargs)

        with patch(
            "haoai_backend.media_storage.reference_staging.os.rename",
            side_effect=replace_before_capture,
        ):
            outcome = self.stager.release_prepared_reference(result)

        self.assertTrue(replaced)
        self.assertEqual(outcome, "not_owned")
        self.assertEqual(path.read_bytes(), b"caller-replacement")
        self.assertEqual(list(path.parent.glob(".muse-reference-quarantine-*")), [])

    def test_failed_write_and_cancellation_cleanup_can_be_retried(self) -> None:
        payload = b"p" * (WRITE_CHUNK_BYTES + 8)
        for failure_kind in ("write", "cancel"):
            with self.subTest(failure_kind=failure_kind):
                stager = ReferenceStager(
                    approved_upload_directory=self.upload_directory,
                    policy=ReferenceStagingPolicy(max_bytes=len(payload)),
                )
                real_write = os.write
                real_unlink = os.unlink
                write_calls = 0
                unlink_calls = 0

                def partial_then_fail(descriptor: int, data: memoryview) -> int:
                    nonlocal write_calls
                    write_calls += 1
                    if write_calls == 1:
                        return real_write(descriptor, data[:7])
                    if failure_kind == "cancel":
                        raise asyncio.CancelledError
                    raise OSError("simulated disk full")

                def fail_first_unlink(path, *args, **kwargs):
                    nonlocal unlink_calls
                    unlink_calls += 1
                    if unlink_calls == 1:
                        raise OSError("simulated cleanup failure")
                    return real_unlink(path, *args, **kwargs)

                with patch(
                    "haoai_backend.media_storage.reference_staging.os.write",
                    side_effect=partial_then_fail,
                ), patch(
                    "haoai_backend.media_storage.reference_staging.os.unlink",
                    side_effect=fail_first_unlink,
                ):
                    if failure_kind == "cancel":
                        with self.assertRaises(asyncio.CancelledError):
                            stager.stage_local_reference(
                                ReferenceInput(
                                    kind="bytes",
                                    content=payload,
                                    media_type="image/png",
                                ),
                                self.staging_directory,
                            )
                    else:
                        result = stager.stage_local_reference(
                            ReferenceInput(kind="bytes", content=payload, media_type="image/png"),
                            self.staging_directory,
                        )
                        self.assertIsInstance(result, TransferFailure)
                        self.assertEqual(result.code, "staging_failed")

                self.assertEqual(write_calls, 2)
                self.assertEqual(unlink_calls, 1)
                self.assertEqual(len(stager._pending_cleanup), 1)
                owned = next(iter(stager._pending_cleanup.values()))
                namespace = owned.path.parent
                captured_partial = namespace / owned.entry_name
                caller_path = namespace / owned.original_name
                self.assertNotEqual(owned.entry_name, owned.original_name)
                self.assertEqual(captured_partial.read_bytes(), b"p" * 7)
                caller_path.write_bytes(b"caller-replacement")

                self.assertEqual(stager.retry_pending_cleanup(), 1)
                self.assertEqual(caller_path.read_bytes(), b"caller-replacement")
                self.assertFalse(captured_partial.exists())
                self.assertEqual(stager.retry_pending_cleanup(), 0)


    def test_release_retains_empty_namespace_when_rmdir_fails_until_explicit_retry(self) -> None:
        result = self.stage(
            ReferenceInput(kind="bytes", content=b"generated", media_type="image/png")
        )
        self.assertIsInstance(result, PreparedReference)
        owned = self.stager._owned_files[id(result)]
        namespace = owned.namespace
        namespace_descriptor = namespace.namespace_descriptor
        staging_descriptor = namespace.staging_descriptor
        file_descriptor = owned.file_descriptor
        real_rmdir = os.rmdir
        rmdir_calls = 0

        def fail_first_rmdir(path, *args, **kwargs):
            nonlocal rmdir_calls
            rmdir_calls += 1
            if rmdir_calls == 1:
                raise OSError("simulated directory cleanup failure")
            return real_rmdir(path, *args, **kwargs)

        with patch(
            "haoai_backend.media_storage.reference_staging.os.rmdir",
            side_effect=fail_first_rmdir,
        ):
            outcome = self.stager.release_prepared_reference(result)

        self.assertEqual(outcome, "failed")
        self.assertFalse(Path(result.locator or "").exists())
        self.assertTrue(namespace.path.is_dir())
        self.assertEqual(list(namespace.path.iterdir()), [])
        self.assertIn(id(namespace), self.stager._pending_namespaces)
        self.assertNotIn(id(owned), self.stager._pending_cleanup)
        self.assertEqual(os.fstat(namespace_descriptor).st_ino, namespace.inode)
        self.assertGreater(os.fstat(staging_descriptor).st_ino, 0)
        with self.assertRaises(OSError):
            os.fstat(file_descriptor)

        self.assertEqual(self.stager.retry_pending_cleanup(), 1)
        self.assertFalse(namespace.path.exists())
        with self.assertRaises(OSError):
            os.fstat(namespace_descriptor)
        with self.assertRaises(OSError):
            os.fstat(staging_descriptor)
        self.assertEqual(self.stager.release_prepared_reference(result), "already_released")
        self.assertEqual(self.stager.retry_pending_cleanup(), 0)

    def test_postvalidation_directory_failure_retains_namespace_for_retry(self) -> None:
        outside_stage = self.base / "outside-stage"
        outside_stage.mkdir()
        moved_stage = self.upload_directory / "renamed-stage"
        original_create = self.stager._create_private_namespace
        real_rmdir = os.rmdir
        rmdir_calls = 0

        def move_stage_after_create(directories):
            namespace = original_create(directories)
            self.staging_directory.rename(moved_stage)
            self.staging_directory.symlink_to(outside_stage, target_is_directory=True)
            return namespace

        def fail_first_rmdir(path, *args, **kwargs):
            nonlocal rmdir_calls
            rmdir_calls += 1
            if rmdir_calls == 1:
                raise OSError("simulated directory cleanup failure")
            return real_rmdir(path, *args, **kwargs)

        with patch.object(
            self.stager,
            "_create_private_namespace",
            side_effect=move_stage_after_create,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.rmdir",
            side_effect=fail_first_rmdir,
        ):
            result = self.stage(
                ReferenceInput(kind="bytes", content=b"not-published", media_type="image/png")
            )

        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "staging_directory_changed")
        self.assertEqual(list(outside_stage.iterdir()), [])
        self.assertEqual(len(self.stager._pending_namespaces), 1)
        namespace = next(iter(self.stager._pending_namespaces.values()))
        namespace_descriptor = namespace.namespace_descriptor
        staging_descriptor = namespace.staging_descriptor
        self.assertTrue((moved_stage / namespace.name).is_dir())

        self.assertEqual(self.stager.retry_pending_cleanup(), 1)
        self.assertFalse((moved_stage / namespace.name).exists())
        with self.assertRaises(OSError):
            os.fstat(namespace_descriptor)
        with self.assertRaises(OSError):
            os.fstat(staging_descriptor)

    def test_mkdir_failure_before_creation_does_not_adopt_caller_directories(self) -> None:
        empty_directory = self.staging_directory / "caller-empty"
        nonempty_directory = self.staging_directory / "caller-nonempty"
        empty_directory.mkdir()
        nonempty_directory.mkdir()
        caller_file = nonempty_directory / "caller.txt"
        caller_file.write_bytes(b"caller data")
        original_stats = {
            path: (path.stat().st_dev, path.stat().st_ino)
            for path in (empty_directory, nonempty_directory)
        }
        stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=self.policy,
        )
        real_mkdir = os.mkdir

        def fail_before_creation(path, mode=0o777, *, dir_fd=None):
            if isinstance(path, str) and path.startswith(".muse-reference-owned-"):
                raise OSError("simulated mkdir failure before filesystem mutation")
            return real_mkdir(path, mode, dir_fd=dir_fd)

        with patch(
            "haoai_backend.media_storage.reference_staging.os.mkdir",
            side_effect=fail_before_creation,
        ):
            result = stager.stage_local_reference(
                ReferenceInput(kind="bytes", content=b"not-created", media_type="image/png"),
                self.staging_directory,
            )

        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "staging_failed")
        for path, expected in original_stats.items():
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), expected)
        self.assertEqual(caller_file.read_bytes(), b"caller data")
        self.assertEqual(
            {path.name for path in self.staging_directory.iterdir()},
            {"caller-empty", "caller-nonempty"},
        )
        self.assertEqual(stager._pending_namespaces, {})
        self.assertEqual(stager.retry_pending_cleanup(), 0)
        self.assertEqual(stager._pending_namespaces, {})
        for path, expected in original_stats.items():
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), expected)
        self.assertEqual(caller_file.read_bytes(), b"caller data")

    def test_mkdir_cancellation_before_creation_discards_only_its_placeholder(self) -> None:
        empty_directory = self.staging_directory / "caller-empty"
        nonempty_directory = self.staging_directory / "caller-nonempty"
        empty_directory.mkdir()
        nonempty_directory.mkdir()
        caller_file = nonempty_directory / "caller.txt"
        caller_file.write_bytes(b"caller data")
        original_stats = {
            path: (path.stat().st_dev, path.stat().st_ino)
            for path in (empty_directory, nonempty_directory)
        }
        real_mkdir = os.mkdir
        real_dup = os.dup
        saved_descriptors: list[int] = []

        def record_dup(descriptor: int) -> int:
            duplicated = real_dup(descriptor)
            saved_descriptors.append(duplicated)
            return duplicated

        def cancel_before_creation(path, mode=0o777, *, dir_fd=None):
            if isinstance(path, str) and path.startswith(".muse-reference-owned-"):
                raise asyncio.CancelledError
            return real_mkdir(path, mode, dir_fd=dir_fd)

        stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=self.policy,
        )
        with patch(
            "haoai_backend.media_storage.reference_staging.os.dup",
            side_effect=record_dup,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.mkdir",
            side_effect=cancel_before_creation,
        ):
            with self.assertRaises(asyncio.CancelledError):
                stager.stage_local_reference(
                    ReferenceInput(kind="bytes", content=b"not-created", media_type="image/png"),
                    self.staging_directory,
                )

        self.assertGreaterEqual(len(saved_descriptors), 2)
        for descriptor in saved_descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        self.assertEqual(stager._pending_namespaces, {})
        self.assertEqual(stager.retry_pending_cleanup(), 0)
        self.assertEqual(
            {path.name for path in self.staging_directory.iterdir()},
            {"caller-empty", "caller-nonempty"},
        )
        for path, expected in original_stats.items():
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), expected)
        self.assertEqual(caller_file.read_bytes(), b"caller data")

    def test_missing_namespace_inspection_error_stays_pending_until_retry(self) -> None:
        empty_directory = self.staging_directory / "caller-empty"
        nonempty_directory = self.staging_directory / "caller-nonempty"
        empty_directory.mkdir()
        nonempty_directory.mkdir()
        caller_file = nonempty_directory / "caller.txt"
        caller_file.write_bytes(b"caller data")
        original_stats = {
            path: (path.stat().st_dev, path.stat().st_ino)
            for path in (empty_directory, nonempty_directory)
        }
        real_mkdir = os.mkdir
        real_dup = os.dup
        real_stat = os.stat
        saved_descriptors: list[int] = []
        attempted_names: set[str] = set()

        def record_dup(descriptor: int) -> int:
            duplicated = real_dup(descriptor)
            saved_descriptors.append(duplicated)
            return duplicated

        def cancel_before_creation(path, mode=0o777, *, dir_fd=None):
            if isinstance(path, str) and path.startswith(".muse-reference-owned-"):
                attempted_names.add(path)
                raise asyncio.CancelledError
            return real_mkdir(path, mode, dir_fd=dir_fd)

        def fail_exact_inspection(path, *args, **kwargs):
            if path in attempted_names:
                raise PermissionError("simulated namespace inspection failure")
            return real_stat(path, *args, **kwargs)

        stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=self.policy,
        )
        with patch(
            "haoai_backend.media_storage.reference_staging.os.dup",
            side_effect=record_dup,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.mkdir",
            side_effect=cancel_before_creation,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.stat",
            side_effect=fail_exact_inspection,
        ):
            with self.assertRaises(asyncio.CancelledError):
                stager.stage_local_reference(
                    ReferenceInput(kind="bytes", content=b"not-created", media_type="image/png"),
                    self.staging_directory,
                )

        self.assertGreaterEqual(len(saved_descriptors), 2)
        self.assertEqual(len(stager._pending_namespaces), 1)
        namespace = next(iter(stager._pending_namespaces.values()))
        self.assertEqual(namespace.creation_state, "uncertain")
        self.assertIsNone(namespace.namespace_descriptor)
        self.assertGreater(os.fstat(saved_descriptors[-1]).st_ino, 0)
        for path, expected in original_stats.items():
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), expected)
        self.assertEqual(caller_file.read_bytes(), b"caller data")

        self.assertEqual(stager.retry_pending_cleanup(), 1)
        self.assertEqual(stager._pending_namespaces, {})
        with self.assertRaises(OSError):
            os.fstat(saved_descriptors[-1])
        for path, expected in original_stats.items():
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), expected)
        self.assertEqual(caller_file.read_bytes(), b"caller data")

    def test_capture_mkdir_failure_discards_only_its_uncreated_placeholder(self) -> None:
        empty_directory = self.staging_directory / "caller-empty"
        nonempty_directory = self.staging_directory / "caller-nonempty"
        empty_directory.mkdir()
        nonempty_directory.mkdir()
        caller_file = nonempty_directory / "caller.txt"
        caller_file.write_bytes(b"caller data")
        original_stats = {
            path: (path.stat().st_dev, path.stat().st_ino)
            for path in (empty_directory, nonempty_directory)
        }
        stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=self.policy,
        )
        result = stager.stage_local_reference(
            ReferenceInput(kind="bytes", content=b"generated", media_type="image/png"),
            self.staging_directory,
        )
        self.assertIsInstance(result, PreparedReference)
        namespace = stager._owned_files[id(result)].namespace
        real_mkdir = os.mkdir

        def fail_before_capture_creation(path, mode=0o777, *, dir_fd=None):
            if isinstance(path, str) and path.startswith(".muse-reference-capture-"):
                raise OSError("simulated capture mkdir failure before filesystem mutation")
            return real_mkdir(path, mode, dir_fd=dir_fd)

        with patch(
            "haoai_backend.media_storage.reference_staging.os.mkdir",
            side_effect=fail_before_capture_creation,
        ):
            outcome = stager.release_prepared_reference(result)

        self.assertEqual(outcome, "failed")
        self.assertFalse(Path(result.locator or "").exists())
        self.assertIsNone(namespace.capture_namespace)
        self.assertEqual(set(stager._pending_namespaces), {id(namespace)})
        self.assertFalse(stager._owned_files[id(result)].released)
        for path, expected in original_stats.items():
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), expected)
        self.assertEqual(caller_file.read_bytes(), b"caller data")

        self.assertEqual(stager.retry_pending_cleanup(), 1)
        self.assertFalse(namespace.path.exists())
        self.assertEqual(stager._pending_namespaces, {})
        self.assertTrue(stager._owned_files[id(result)].released)
        for path, expected in original_stats.items():
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), expected)
        self.assertEqual(caller_file.read_bytes(), b"caller data")

    def test_capture_mkdir_cancellation_before_creation_keeps_original_retryable(self) -> None:
        empty_directory = self.staging_directory / "caller-empty"
        nonempty_directory = self.staging_directory / "caller-nonempty"
        empty_directory.mkdir()
        nonempty_directory.mkdir()
        caller_file = nonempty_directory / "caller.txt"
        caller_file.write_bytes(b"caller data")
        original_stats = {
            path: (path.stat().st_dev, path.stat().st_ino)
            for path in (empty_directory, nonempty_directory)
        }
        result = self.stage(
            ReferenceInput(kind="bytes", content=b"generated", media_type="image/png")
        )
        self.assertIsInstance(result, PreparedReference)
        owned = self.stager._owned_files[id(result)]
        namespace = owned.namespace
        real_mkdir = os.mkdir
        real_dup = os.dup
        capture_descriptors: list[int] = []

        def record_dup(descriptor: int) -> int:
            duplicated = real_dup(descriptor)
            capture_descriptors.append(duplicated)
            return duplicated

        def cancel_before_capture_creation(path, mode=0o777, *, dir_fd=None):
            if isinstance(path, str) and path.startswith(".muse-reference-capture-"):
                raise asyncio.CancelledError
            return real_mkdir(path, mode, dir_fd=dir_fd)

        with patch(
            "haoai_backend.media_storage.reference_staging.os.dup",
            side_effect=record_dup,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.mkdir",
            side_effect=cancel_before_capture_creation,
        ):
            with self.assertRaises(asyncio.CancelledError):
                self.stager.release_prepared_reference(result)

        self.assertEqual(len(capture_descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(capture_descriptors[0])
        self.assertFalse(Path(result.locator or "").exists())
        self.assertFalse(owned.released)
        self.assertIsNone(namespace.captured_owned_name)
        self.assertIsNone(namespace.capture_namespace)
        self.assertEqual(set(self.stager._pending_namespaces), {id(namespace)})
        self.assertTrue(namespace.path.is_dir())
        self.assertEqual(list(namespace.path.iterdir()), [])
        self.assertGreater(os.fstat(namespace.staging_descriptor).st_ino, 0)
        for path, expected in original_stats.items():
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), expected)
        self.assertEqual(caller_file.read_bytes(), b"caller data")

        self.assertEqual(self.stager.retry_pending_cleanup(), 1)
        self.assertTrue(owned.released)
        self.assertEqual(self.stager._pending_namespaces, {})
        self.assertFalse(namespace.path.exists())
        with self.assertRaises(OSError):
            os.fstat(namespace.staging_descriptor)
        for path, expected in original_stats.items():
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), expected)
        self.assertEqual(caller_file.read_bytes(), b"caller data")

    def test_captured_owned_namespace_retry_precedes_parent_search(self) -> None:
        result = self.stage(
            ReferenceInput(kind="bytes", content=b"generated", media_type="image/png")
        )
        self.assertIsInstance(result, PreparedReference)
        owned = self.stager._owned_files[id(result)]
        namespace = owned.namespace
        namespace_descriptor = namespace.namespace_descriptor
        staging_descriptor = namespace.staging_descriptor
        real_rmdir = os.rmdir
        real_exclusive_rename = staging_module._rename_without_replacing
        failed_rmdir = False
        blocked_restore = False
        caller_path: Path | None = None

        def fail_owned_rmdir_once(path, *args, **kwargs):
            nonlocal failed_rmdir
            capture = namespace.capture_namespace
            if (
                not failed_rmdir
                and capture is not None
                and path == namespace.captured_owned_name
                and kwargs.get("dir_fd") == capture.namespace_descriptor
            ):
                failed_rmdir = True
                raise OSError("simulated owned namespace rmdir failure")
            return real_rmdir(path, *args, **kwargs)

        def block_owned_restore(source, source_fd, destination, destination_fd):
            nonlocal blocked_restore, caller_path
            capture = namespace.capture_namespace
            if (
                not blocked_restore
                and capture is not None
                and source == namespace.captured_owned_name
                and source_fd == capture.namespace_descriptor
                and destination == namespace.cleanup_source_name
            ):
                blocked_restore = True
                caller_path = self.staging_directory / destination
                os.mkdir(destination, 0o700, dir_fd=destination_fd)
            return real_exclusive_rename(source, source_fd, destination, destination_fd)

        with patch(
            "haoai_backend.media_storage.reference_staging.os.rmdir",
            side_effect=fail_owned_rmdir_once,
        ), patch.object(
            staging_module,
            "_rename_without_replacing",
            side_effect=block_owned_restore,
        ):
            outcome = self.stager.release_prepared_reference(result)

        self.assertTrue(failed_rmdir)
        self.assertTrue(blocked_restore)
        self.assertEqual(outcome, "failed")
        self.assertFalse(owned.released)
        self.assertFalse(namespace.target_removed)
        self.assertIsNotNone(namespace.captured_owned_name)
        self.assertIsNotNone(namespace.capture_namespace)
        assert caller_path is not None
        caller_stat = caller_path.stat()
        caller_identity = (caller_stat.st_dev, caller_stat.st_ino)
        self.assertTrue(stat.S_ISDIR(caller_stat.st_mode))
        capture_namespace = namespace.capture_namespace
        assert capture_namespace is not None
        capture_descriptor = capture_namespace.namespace_descriptor
        capture_path = capture_namespace.path
        captured_owned_path = capture_path / (namespace.captured_owned_name or "")
        self.assertTrue(captured_owned_path.is_dir())

        current_caller_stat = caller_path.stat()
        self.assertEqual(
            (current_caller_stat.st_dev, current_caller_stat.st_ino),
            caller_identity,
        )
        caller_path.rmdir()
        self.assertEqual(self.stager.retry_pending_cleanup(), 1)
        self.assertFalse(namespace.path.exists())
        self.assertFalse(capture_path.exists())
        self.assertFalse(captured_owned_path.exists())
        self.assertEqual(self.stager._pending_namespaces, {})
        self.assertTrue(owned.released)
        self.assertIsNone(namespace.namespace_descriptor)
        for descriptor in (namespace_descriptor, staging_descriptor, capture_descriptor):
            self.assertIsNotNone(descriptor)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        self.assertFalse((self.staging_directory / namespace.name).exists())

    def test_namespace_listdir_error_before_capture_keeps_cleanup_retryable(self) -> None:
        result = self.stage(
            ReferenceInput(kind="bytes", content=b"generated", media_type="image/png")
        )
        self.assertIsInstance(result, PreparedReference)
        owned = self.stager._owned_files[id(result)]
        namespace = owned.namespace
        namespace_descriptor = namespace.namespace_descriptor
        staging_descriptor = namespace.staging_descriptor
        real_listdir = os.listdir

        def fail_owned_namespace_listdir(path):
            if path == namespace_descriptor:
                raise OSError("simulated namespace listdir failure")
            return real_listdir(path)

        with patch(
            "haoai_backend.media_storage.reference_staging.os.listdir",
            side_effect=fail_owned_namespace_listdir,
        ):
            outcome = self.stager.release_prepared_reference(result)

        self.assertEqual(outcome, "failed")
        self.assertFalse(owned.released)
        self.assertFalse(namespace.target_removed)
        self.assertIsNone(namespace.captured_owned_name)
        self.assertTrue(namespace.path.is_dir())
        self.assertIn(id(namespace), self.stager._pending_namespaces)
        self.assertNotIn(id(owned), self.stager._pending_cleanup)
        self.assertEqual(self.stager.retry_pending_cleanup(), 1)
        self.assertFalse(namespace.path.exists())
        self.assertEqual(self.stager._pending_namespaces, {})
        self.assertTrue(owned.released)
        for descriptor in (namespace_descriptor, staging_descriptor):
            self.assertIsNotNone(descriptor)
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_namespace_listdir_error_after_capture_keeps_capture_retryable(self) -> None:
        result = self.stage(
            ReferenceInput(kind="bytes", content=b"generated", media_type="image/png")
        )
        self.assertIsInstance(result, PreparedReference)
        owned = self.stager._owned_files[id(result)]
        namespace = owned.namespace
        namespace_descriptor = namespace.namespace_descriptor
        staging_descriptor = namespace.staging_descriptor
        real_listdir = os.listdir
        real_rename = staging_module._rename_without_replacing
        captured = False

        def capture_then_fail_namespace_listdir(source, source_fd, destination, destination_fd):
            nonlocal captured
            result_name = real_rename(source, source_fd, destination, destination_fd)
            if (
                source == namespace.name
                and namespace.capture_namespace is not None
                and destination_fd == namespace.capture_namespace.namespace_descriptor
            ):
                captured = True
            return result_name

        def fail_after_capture(path):
            if captured and path == namespace_descriptor:
                raise OSError("simulated post-capture namespace listdir failure")
            return real_listdir(path)

        with patch.object(
            staging_module,
            "_rename_without_replacing",
            side_effect=capture_then_fail_namespace_listdir,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.listdir",
            side_effect=fail_after_capture,
        ):
            outcome = self.stager.release_prepared_reference(result)

        self.assertTrue(captured)
        self.assertEqual(outcome, "failed")
        self.assertFalse(owned.released)
        self.assertFalse(namespace.target_removed)
        self.assertIsNotNone(namespace.captured_owned_name)
        capture_namespace = namespace.capture_namespace
        self.assertIsNotNone(capture_namespace)
        capture_descriptor = capture_namespace.namespace_descriptor
        captured_path = capture_namespace.path / (namespace.captured_owned_name or "")
        self.assertTrue(captured_path.is_dir())
        self.assertIn(id(namespace), self.stager._pending_namespaces)

        self.assertEqual(self.stager.retry_pending_cleanup(), 1)
        self.assertFalse(namespace.path.exists())
        self.assertFalse(capture_namespace.path.exists())
        self.assertFalse(captured_path.exists())
        self.assertEqual(self.stager._pending_namespaces, {})
        self.assertTrue(owned.released)
        for descriptor in (namespace_descriptor, staging_descriptor, capture_descriptor):
            self.assertIsNotNone(descriptor)
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_namespace_replacement_after_inspection_preserves_caller_directory(self) -> None:
        for contents in (None, b"caller-owned"):
            with self.subTest(contents=contents):
                stager = ReferenceStager(
                    approved_upload_directory=self.upload_directory,
                    policy=self.policy,
                )
                result = stager.stage_local_reference(
                    ReferenceInput(kind="bytes", content=b"generated", media_type="image/png"),
                    self.staging_directory,
                )
                self.assertIsInstance(result, PreparedReference)
                namespace = stager._owned_files[id(result)].namespace
                caller_path = self.staging_directory / namespace.name
                moved_original_name = ".caller-moved-owned-namespace"
                real_rename = os.rename
                real_mkdir = os.mkdir
                real_exclusive_rename = staging_module._rename_without_replacing
                replaced = False

                def replace_after_inspection(source, source_fd, destination, destination_fd):
                    nonlocal replaced
                    capture = namespace.capture_namespace
                    if (
                        not replaced
                        and source == namespace.name
                        and capture is not None
                        and destination_fd == capture.namespace_descriptor
                    ):
                        replaced = True
                        real_rename(
                            namespace.name,
                            moved_original_name,
                            src_dir_fd=source_fd,
                            dst_dir_fd=source_fd,
                        )
                        real_mkdir(namespace.name, 0o700, dir_fd=source_fd)
                        if contents is not None:
                            (caller_path / "caller.txt").write_bytes(contents)
                    return real_exclusive_rename(
                        source,
                        source_fd,
                        destination,
                        destination_fd,
                    )

                with patch.object(
                    staging_module,
                    "_rename_without_replacing",
                    side_effect=replace_after_inspection,
                ):
                    outcome = stager.release_prepared_reference(result)

                self.assertTrue(replaced)
                self.assertEqual(outcome, "released")
                caller_stat = caller_path.stat()
                self.assertTrue(stat.S_ISDIR(caller_stat.st_mode))
                if contents is not None:
                    self.assertEqual((caller_path / "caller.txt").read_bytes(), contents)
                self.assertFalse((self.staging_directory / moved_original_name).exists())
                self.assertEqual(stager._pending_namespaces, {})
                self.assertEqual(stager.release_prepared_reference(result), "already_released")

    def test_foreign_capture_restore_never_overwrites_a_new_caller_directory(self) -> None:
        stager = ReferenceStager(
            approved_upload_directory=self.upload_directory,
            policy=self.policy,
        )
        result = stager.stage_local_reference(
            ReferenceInput(kind="bytes", content=b"generated", media_type="image/png"),
            self.staging_directory,
        )
        self.assertIsInstance(result, PreparedReference)
        namespace = stager._owned_files[id(result)].namespace
        caller_path = self.staging_directory / namespace.name
        moved_original_name = ".caller-moved-owned-namespace"
        real_rename = os.rename
        real_mkdir = os.mkdir
        real_exclusive_rename = staging_module._rename_without_replacing
        raced_at_capture = False
        raced_at_restore = False

        def add_replacement_at_capture(source, source_fd, destination, destination_fd):
            nonlocal raced_at_capture, raced_at_restore
            capture = namespace.capture_namespace
            if (
                not raced_at_capture
                and source == namespace.name
                and capture is not None
                and destination_fd == capture.namespace_descriptor
            ):
                raced_at_capture = True
                real_rename(
                    namespace.name,
                    moved_original_name,
                    src_dir_fd=source_fd,
                    dst_dir_fd=source_fd,
                )
                real_mkdir(namespace.name, 0o700, dir_fd=source_fd)
                (caller_path / "first.txt").write_bytes(b"first caller directory")
            if (
                raced_at_capture
                and not raced_at_restore
                and destination == namespace.name
                and source_fd == capture.namespace_descriptor
            ):
                raced_at_restore = True
                real_mkdir(namespace.name, 0o700, dir_fd=destination_fd)
                (caller_path / "new.txt").write_bytes(b"new caller directory")
            return real_exclusive_rename(
                source,
                source_fd,
                destination,
                destination_fd,
            )

        with patch.object(
            staging_module,
            "_rename_without_replacing",
            side_effect=add_replacement_at_capture,
        ):
            outcome = stager.release_prepared_reference(result)

        self.assertTrue(raced_at_capture)
        self.assertTrue(raced_at_restore)
        self.assertEqual(outcome, "failed")
        self.assertEqual((caller_path / "new.txt").read_bytes(), b"new caller directory")
        self.assertFalse((self.staging_directory / moved_original_name).exists())
        self.assertTrue(namespace.target_removed)
        self.assertEqual(len(namespace.captured_foreign_names), 1)
        capture_path = namespace.capture_namespace.path / namespace.captured_foreign_names[0]
        self.assertEqual((capture_path / "first.txt").read_bytes(), b"first caller directory")
        self.assertEqual(stager.release_prepared_reference(result), "failed")
        self.assertEqual(stager.retry_pending_cleanup(), 0)
        self.assertEqual((caller_path / "new.txt").read_bytes(), b"new caller directory")

        (caller_path / "new.txt").unlink()
        caller_path.rmdir()
        self.assertEqual(stager.retry_pending_cleanup(), 1)
        self.assertEqual((caller_path / "first.txt").read_bytes(), b"first caller directory")
        self.assertEqual(stager.release_prepared_reference(result), "already_released")

    def test_directory_binding_cancellation_closes_approved_root_descriptor(self) -> None:
        real_open_absolute = staging_module._open_absolute_directory
        real_fstat = os.fstat
        acquired: list[int] = []

        def remember_upload_descriptor(path: Path) -> int:
            descriptor = real_open_absolute(path)
            acquired.append(descriptor)
            return descriptor

        def cancel_after_root_open(descriptor: int) -> os.stat_result:
            if acquired and descriptor == acquired[0]:
                raise asyncio.CancelledError
            return real_fstat(descriptor)

        with patch.object(
            staging_module,
            "_open_absolute_directory",
            side_effect=remember_upload_descriptor,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.fstat",
            side_effect=cancel_after_root_open,
        ):
            with self.assertRaises(asyncio.CancelledError):
                self.stage(
                    ReferenceInput(kind="bytes", content=b"cancel-at-bind", media_type="image/png")
                )

        self.assertEqual(len(acquired), 1)
        with self.assertRaises(OSError):
            os.fstat(acquired[0])
        self.assertFalse(any(self.staging_directory.iterdir()))

    def test_namespace_open_cancellation_retains_and_retries_failed_directory_cleanup(self) -> None:
        real_open = os.open
        real_fstat = os.fstat
        real_rmdir = os.rmdir
        private_descriptors: list[int] = []
        cancelled = False
        rmdir_calls = 0

        def remember_namespace_open(path, flags, *args, **kwargs):
            descriptor = real_open(path, flags, *args, **kwargs)
            if isinstance(path, str) and path.startswith(".muse-reference-owned-"):
                private_descriptors.append(descriptor)
            return descriptor

        def cancel_once_on_namespace_stat(descriptor: int) -> os.stat_result:
            nonlocal cancelled
            if private_descriptors and descriptor == private_descriptors[0] and not cancelled:
                cancelled = True
                raise asyncio.CancelledError
            return real_fstat(descriptor)

        def fail_first_rmdir(path, *args, **kwargs):
            nonlocal rmdir_calls
            rmdir_calls += 1
            if rmdir_calls == 1:
                raise OSError("simulated directory cleanup failure")
            return real_rmdir(path, *args, **kwargs)

        with patch(
            "haoai_backend.media_storage.reference_staging.os.open",
            side_effect=remember_namespace_open,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.fstat",
            side_effect=cancel_once_on_namespace_stat,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.rmdir",
            side_effect=fail_first_rmdir,
        ):
            with self.assertRaises(asyncio.CancelledError):
                self.stage(
                    ReferenceInput(
                        kind="bytes",
                        content=b"cancel-after-open",
                        media_type="image/png",
                    )
                )

        self.assertTrue(cancelled)
        self.assertEqual(len(private_descriptors), 1)
        self.assertEqual(len(self.stager._pending_namespaces), 1)
        namespace = next(iter(self.stager._pending_namespaces.values()))
        namespace_descriptor = namespace.namespace_descriptor
        staging_descriptor = namespace.staging_descriptor
        self.assertEqual(namespace_descriptor, private_descriptors[0])
        self.assertTrue(namespace.path.is_dir())
        self.assertEqual(list(namespace.path.iterdir()), [])
        self.assertGreater(os.fstat(namespace_descriptor).st_ino, 0)
        self.assertGreater(os.fstat(staging_descriptor).st_ino, 0)

        self.assertEqual(self.stager.retry_pending_cleanup(), 1)
        self.assertFalse(namespace.path.exists())
        with self.assertRaises(OSError):
            os.fstat(namespace_descriptor)
        with self.assertRaises(OSError):
            os.fstat(staging_descriptor)

    def test_mkdir_cancellation_retains_namespace_before_open_for_retry(self) -> None:
        real_mkdir = os.mkdir
        real_rmdir = os.rmdir
        rmdir_calls = 0

        cancelled = False

        def create_then_cancel(path, mode=0o777, *, dir_fd=None):
            nonlocal cancelled
            real_mkdir(path, mode, dir_fd=dir_fd)
            if (
                not cancelled
                and isinstance(path, str)
                and path.startswith(".muse-reference-owned-")
            ):
                cancelled = True
                raise asyncio.CancelledError

        def fail_first_rmdir(path, *args, **kwargs):
            nonlocal rmdir_calls
            rmdir_calls += 1
            if rmdir_calls == 1:
                raise OSError("simulated directory cleanup failure")
            return real_rmdir(path, *args, **kwargs)

        with patch(
            "haoai_backend.media_storage.reference_staging.os.mkdir",
            side_effect=create_then_cancel,
        ), patch(
            "haoai_backend.media_storage.reference_staging.os.rmdir",
            side_effect=fail_first_rmdir,
        ):
            with self.assertRaises(asyncio.CancelledError):
                self.stage(
                    ReferenceInput(
                        kind="bytes",
                        content=b"cancel-after-mkdir",
                        media_type="image/png",
                    )
                )

        self.assertEqual(rmdir_calls, 2)
        self.assertEqual(len(self.stager._pending_namespaces), 1)
        namespace = next(iter(self.stager._pending_namespaces.values()))
        namespace_descriptor = namespace.namespace_descriptor
        staging_descriptor = namespace.staging_descriptor
        self.assertIsNotNone(namespace_descriptor)
        self.assertTrue(namespace.path.is_dir())
        self.assertEqual(list(namespace.path.iterdir()), [])
        self.assertGreater(os.fstat(namespace_descriptor).st_ino, 0)
        self.assertGreater(os.fstat(staging_descriptor).st_ino, 0)

        self.assertEqual(self.stager.retry_pending_cleanup(), 1)
        self.assertFalse(namespace.path.exists())
        with self.assertRaises(OSError):
            os.fstat(namespace_descriptor)
        with self.assertRaises(OSError):
            os.fstat(staging_descriptor)


    def test_write_error_and_cancellation_retain_empty_namespace_after_rmdir_failure(self) -> None:
        payload = b"p" * (WRITE_CHUNK_BYTES + 8)
        for failure_kind in ("write", "cancel"):
            with self.subTest(failure_kind=failure_kind):
                stager = ReferenceStager(
                    approved_upload_directory=self.upload_directory,
                    policy=ReferenceStagingPolicy(max_bytes=len(payload)),
                )
                real_write = os.write
                real_rmdir = os.rmdir
                write_calls = 0
                rmdir_calls = 0

                def partial_then_fail(descriptor: int, data: memoryview) -> int:
                    nonlocal write_calls
                    write_calls += 1
                    if write_calls == 1:
                        return real_write(descriptor, data[:7])
                    if failure_kind == "cancel":
                        raise asyncio.CancelledError
                    raise OSError("simulated disk full")

                def fail_first_rmdir(path, *args, **kwargs):
                    nonlocal rmdir_calls
                    rmdir_calls += 1
                    if rmdir_calls == 1:
                        raise OSError("simulated directory cleanup failure")
                    return real_rmdir(path, *args, **kwargs)

                with patch(
                    "haoai_backend.media_storage.reference_staging.os.write",
                    side_effect=partial_then_fail,
                ), patch(
                    "haoai_backend.media_storage.reference_staging.os.rmdir",
                    side_effect=fail_first_rmdir,
                ):
                    if failure_kind == "cancel":
                        with self.assertRaises(asyncio.CancelledError):
                            stager.stage_local_reference(
                                ReferenceInput(
                                    kind="bytes",
                                    content=payload,
                                    media_type="image/png",
                                ),
                                self.staging_directory,
                            )
                    else:
                        result = stager.stage_local_reference(
                            ReferenceInput(
                                kind="bytes",
                                content=payload,
                                media_type="image/png",
                            ),
                            self.staging_directory,
                        )
                        self.assertIsInstance(result, TransferFailure)
                        self.assertEqual(result.code, "staging_failed")

                self.assertEqual(write_calls, 2)
                self.assertEqual(rmdir_calls, 2)
                self.assertEqual(len(stager._pending_cleanup), 0)
                self.assertEqual(len(stager._pending_namespaces), 1)
                namespace = next(iter(stager._pending_namespaces.values()))
                namespace_descriptor = namespace.namespace_descriptor
                staging_descriptor = namespace.staging_descriptor
                self.assertTrue(namespace.path.is_dir())
                self.assertEqual(list(namespace.path.iterdir()), [])
                self.assertGreater(os.fstat(namespace_descriptor).st_ino, 0)

                self.assertEqual(stager.retry_pending_cleanup(), 1)
                self.assertFalse(namespace.path.exists())
                with self.assertRaises(OSError):
                    os.fstat(namespace_descriptor)
                with self.assertRaises(OSError):
                    os.fstat(staging_descriptor)


if __name__ == "__main__":
    unittest.main()
