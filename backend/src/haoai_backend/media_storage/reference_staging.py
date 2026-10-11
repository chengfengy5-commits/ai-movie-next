"""Bounded local staging for typed Muse reference inputs."""

from __future__ import annotations

import base64
import binascii
import ctypes
import errno
import os
import secrets
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from haoai_backend.generation_contracts import PreparedReference, ReferenceInput, TransferFailure


MAX_REFERENCE_BYTES = 200 * 1024 * 1024
WRITE_CHUNK_BYTES = 64 * 1024
_MAX_DATA_URI_HEADER_CHARS = 128
_MAX_CREATE_ATTEMPTS = 8

_EXTENSION_TO_MEDIA_TYPE = {
    ".aac": "audio/aac",
    ".avif": "image/avif",
    ".avi": "video/x-msvideo",
    ".bmp": "image/bmp",
    ".flac": "audio/flac",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".m4a": "audio/mp4",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".webp": "image/webp",
}
_MEDIA_TYPE_TO_EXTENSION = {
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/mpeg": ".mpg",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-m4v": ".m4v",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}
_BLOCKED_MEDIA_TYPES = frozenset(
    {
        "application/javascript",
        "application/json",
        "application/x-javascript",
        "application/xhtml+xml",
        "application/xml",
        "image/svg+xml",
        "text/css",
        "text/html",
        "text/javascript",
        "text/json",
        "text/xml",
    }
)
_BLOCKED_EXTENSIONS = frozenset(
    {".cjs", ".css", ".htm", ".html", ".js", ".json", ".mjs", ".svg", ".xhtml", ".xml"}
)
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True, slots=True)
class ReferenceStagingPolicy:
    """Explicit per-stager byte ceiling, bounded by the fixed 200 MiB maximum."""

    max_bytes: int

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= MAX_REFERENCE_BYTES:
            raise ValueError("reference staging limit must be between 1 byte and 200 MiB")


@dataclass(slots=True)
class _ResolvedDirectories:
    upload_root: Path
    stage_root: Path
    upload_descriptor: int
    staging_descriptor: int

    def is_current(self) -> bool:
        try:
            current_upload = self.upload_root.resolve(strict=True)
            current_stage = self.stage_root.resolve(strict=True)
            return (
                _is_within(current_stage, current_upload)
                and _same_directory(current_upload.stat(), os.fstat(self.upload_descriptor))
                and _same_directory(current_stage.stat(), os.fstat(self.staging_descriptor))
            )
        except (OSError, RuntimeError, ValueError):
            return False

    def close(self) -> None:
        _close_quietly(self.staging_descriptor)
        _close_quietly(self.upload_descriptor)


@dataclass(slots=True)
class _OwnedNamespace:
    name: str
    path: Path
    namespace_descriptor: int | None
    staging_descriptor: int
    device: int | None = None
    inode: int | None = None
    file_owner: _OwnedStagedFile | None = None
    is_protected_capture: bool = False
    capture_namespace: _OwnedNamespace | None = None
    captured_owned_name: str | None = None
    captured_foreign_names: list[str] = field(default_factory=list)
    cleanup_source_name: str | None = None
    target_removed: bool = False
    creation_state: Literal["placeholder", "uncertain", "created", "absent"] = (
        "placeholder"
    )


@dataclass(slots=True)
class _OwnedStagedFile:
    handle: PreparedReference | None
    path: Path
    namespace: _OwnedNamespace
    original_name: str
    entry_name: str
    device: int | None
    inode: int | None
    file_descriptor: int
    released: bool = False


class ReferenceStager:
    """Stage approved local or inline references without opening external services."""

    def __init__(
        self,
        *,
        approved_upload_directory: Path,
        policy: ReferenceStagingPolicy,
    ) -> None:
        directory = Path(approved_upload_directory)
        if not directory.is_absolute():
            raise ValueError("approved upload directory must be absolute")
        if not isinstance(policy, ReferenceStagingPolicy):
            raise TypeError("an explicit reference staging policy is required")
        self._approved_upload_directory = directory
        self._policy = policy
        self._owned_files: dict[int, _OwnedStagedFile] = {}
        self._pending_cleanup: dict[int, _OwnedStagedFile] = {}
        self._pending_namespaces: dict[int, _OwnedNamespace] = {}

    def stage_local_reference(
        self,
        reference: ReferenceInput,
        staging_directory: Path,
    ) -> PreparedReference | TransferFailure:
        if not isinstance(reference, ReferenceInput):
            return _failure("invalid_reference", "参考输入格式无效")
        if reference.kind not in {"bytes", "data_uri", "local_muse_path"}:
            return _failure("unsupported_reference_kind", "当前本地适配器不支持该参考类型")

        directories = self._resolve_directories(staging_directory)
        if isinstance(directories, TransferFailure):
            return directories
        try:
            if reference.kind == "bytes":
                payload = self._validate_bytes_reference(reference)
                if isinstance(payload, TransferFailure):
                    return payload
                data, media_type = payload
                return self._write_staged_file(directories, data, media_type)

            if reference.kind == "data_uri":
                payload = self._decode_data_uri(reference)
                if isinstance(payload, TransferFailure):
                    return payload
                data, media_type = payload
                return self._write_staged_file(directories, data, media_type)

            return self._prepare_local_path(reference, directories.upload_root)
        finally:
            directories.close()

    def release_prepared_reference(
        self,
        handle: PreparedReference,
    ) -> Literal["released", "already_released", "not_owned", "failed"]:
        if not isinstance(handle, PreparedReference):
            return "not_owned"
        owned = self._owned_files.get(id(handle))
        if owned is None or owned.handle is not handle:
            return "not_owned"
        if owned.released:
            return "already_released"

        outcome = self._remove_owned_entry(owned)
        if outcome == "failed":
            self._pending_cleanup[id(owned)] = owned
            return "failed"
        self._pending_cleanup.pop(id(owned), None)
        namespace_outcome = self._finish_owned_file(owned)
        if namespace_outcome == "pending":
            return "failed"
        owned.released = True
        return "released" if outcome == "removed" else "not_owned"

    def retry_pending_cleanup(self) -> int:
        """Retry each retained file or empty namespace once."""
        pending_namespaces = tuple(self._pending_namespaces.items())
        recovered = 0
        for key, owned in tuple(self._pending_cleanup.items()):
            outcome = self._remove_owned_entry(owned)
            if outcome == "failed":
                continue
            self._pending_cleanup.pop(key, None)
            namespace_outcome = self._finish_owned_file(owned)
            if namespace_outcome == "pending":
                continue
            if owned.handle is not None:
                owned.released = True
            recovered += 1

        for key, namespace in pending_namespaces:
            if key not in self._pending_namespaces:
                continue
            outcome = self._remove_empty_namespace(namespace)
            if outcome == "pending":
                continue
            self._pending_namespaces.pop(key, None)
            if namespace.file_owner is not None:
                namespace.file_owner.released = True
            recovered += 1
        return recovered

    def _resolve_directories(
        self,
        staging_directory: Path,
    ) -> _ResolvedDirectories | TransferFailure:
        upload_descriptor: int | None = None
        staging_descriptor: int | None = None
        try:
            upload_root = self._approved_upload_directory.resolve(strict=True)
            requested_stage = Path(staging_directory)
            if not requested_stage.is_absolute():
                return _failure("invalid_staging_directory", "暂存目录必须是已批准目录内的绝对路径")
            stage_root = requested_stage.resolve(strict=True)
            upload_stat = upload_root.stat()
            stage_stat = stage_root.stat()
        except (OSError, RuntimeError, TypeError, ValueError):
            return _failure("invalid_staging_directory", "暂存目录不可用")

        if not stat.S_ISDIR(upload_stat.st_mode) or not stat.S_ISDIR(stage_stat.st_mode):
            return _failure("invalid_staging_directory", "暂存目录不可用")
        if not _is_within(stage_root, upload_root):
            return _failure("staging_directory_not_approved", "暂存目录不在已批准范围内")

        try:
            upload_descriptor = _open_absolute_directory(upload_root)
            if not _same_directory(os.fstat(upload_descriptor), upload_stat):
                raise OSError("approved upload directory changed during binding")
            relative_parts = stage_root.relative_to(upload_root).parts
            staging_descriptor = _open_directory_beneath(upload_descriptor, relative_parts)
            if not _same_directory(os.fstat(staging_descriptor), stage_stat):
                raise OSError("staging directory changed during binding")
        except Exception:
            _close_quietly(staging_descriptor)
            _close_quietly(upload_descriptor)
            return _failure("invalid_staging_directory", "暂存目录不可用")
        except BaseException:
            _close_quietly(staging_descriptor)
            _close_quietly(upload_descriptor)
            raise

        return _ResolvedDirectories(
            upload_root=upload_root,
            stage_root=stage_root,
            upload_descriptor=upload_descriptor,
            staging_descriptor=staging_descriptor,
        )

    def _validate_bytes_reference(
        self,
        reference: ReferenceInput,
    ) -> tuple[bytes, str] | TransferFailure:
        if (
            reference.locator is not None
            or reference.content is None
            or reference.provider_operation_ref is not None
        ):
            return _failure("invalid_reference", "字节参考包含冲突字段")
        if not isinstance(reference.content, bytes) or not reference.content:
            return _failure("invalid_reference", "字节参考内容为空或格式无效")
        if len(reference.content) > self._policy.max_bytes:
            return _failure("reference_too_large", "参考内容超过暂存上限")
        media_type = _normalize_media_type(reference.media_type)
        if media_type is None:
            return _media_type_failure(reference.media_type)
        return reference.content, media_type

    def _decode_data_uri(
        self,
        reference: ReferenceInput,
    ) -> tuple[bytes, str] | TransferFailure:
        locator = reference.locator
        if (
            not isinstance(locator, str)
            or reference.content is not None
            or reference.provider_operation_ref is not None
        ):
            return _failure("invalid_reference", "Data URI 参考包含冲突字段")
        if not locator.startswith("data:"):
            return _failure("invalid_data_uri", "Data URI 格式无效")
        comma = locator.find(",")
        if comma < 0 or comma > _MAX_DATA_URI_HEADER_CHARS:
            return _failure("invalid_data_uri", "Data URI 格式无效")
        header = locator[5:comma]
        media_token, separator, encoding = header.partition(";")
        if not separator or encoding != "base64":
            return _failure("invalid_data_uri", "仅支持严格 Base64 Data URI")
        media_type = _normalize_media_type(media_token)
        if media_type is None:
            return _media_type_failure(media_token)
        if reference.media_type is not None:
            declared_type = _normalize_media_type(reference.media_type)
            if declared_type is None:
                return _media_type_failure(reference.media_type)
            if declared_type != media_type:
                return _failure("media_type_mismatch", "Data URI 类型与声明类型不一致")

        payload_start = comma + 1
        encoded_size = len(locator) - payload_start
        max_encoded_size = 4 * ((self._policy.max_bytes + 2) // 3)
        if encoded_size > max_encoded_size:
            return _failure("reference_too_large", "参考内容超过暂存上限")
        if encoded_size == 0 or encoded_size % 4:
            return _failure("invalid_data_uri", "Data URI Base64 长度无效")
        padding = 0
        if locator.endswith("="):
            padding = 1
            if encoded_size > 1 and locator[-2] == "=":
                padding = 2
        decoded_size = (encoded_size // 4) * 3 - padding
        if decoded_size <= 0:
            return _failure("invalid_data_uri", "Data URI 内容为空")
        if decoded_size > self._policy.max_bytes:
            return _failure("reference_too_large", "参考内容超过暂存上限")

        encoded_payload = locator[payload_start:]
        try:
            data = base64.b64decode(encoded_payload, validate=True)
        except (ValueError, binascii.Error):
            return _failure("invalid_data_uri", "Data URI Base64 内容无效")
        if len(data) != decoded_size:
            return _failure("invalid_data_uri", "Data URI Base64 内容无效")
        return data, media_type

    def _prepare_local_path(
        self,
        reference: ReferenceInput,
        upload_root: Path,
    ) -> PreparedReference | TransferFailure:
        locator = reference.locator
        if (
            not isinstance(locator, str)
            or reference.content is not None
            or reference.provider_operation_ref is not None
        ):
            return _failure("invalid_reference", "本地路径参考包含冲突字段")
        try:
            source = Path(locator)
            if not source.is_absolute() or ".." in source.parts:
                return _failure("invalid_local_path", "本地参考路径必须位于已批准目录内")
            resolved = source.resolve(strict=True)
            if not _is_within(resolved, upload_root):
                return _failure("local_path_not_approved", "本地参考路径不在已批准范围内")
            source_stat = resolved.stat()
        except (OSError, RuntimeError, TypeError, ValueError):
            return _failure("invalid_local_path", "本地参考路径不可用")

        if not stat.S_ISREG(source_stat.st_mode):
            return _failure("invalid_local_path", "本地参考必须是普通文件")
        if source_stat.st_size <= 0:
            return _failure("invalid_reference", "本地参考文件为空")
        if source_stat.st_size > self._policy.max_bytes:
            return _failure("reference_too_large", "参考内容超过暂存上限")

        media_type = _media_type_for_path(resolved)
        if isinstance(media_type, TransferFailure):
            return media_type
        if reference.media_type is not None:
            declared_type = _normalize_media_type(reference.media_type)
            if declared_type is None:
                return _media_type_failure(reference.media_type)
            if declared_type != media_type:
                return _failure("media_type_mismatch", "本地文件类型与声明类型不一致")
        return PreparedReference(
            kind="local_muse_path",
            locator=str(resolved),
            content=None,
            media_type=media_type,
        )

    def _create_private_namespace(
        self,
        directories: _ResolvedDirectories,
    ) -> _OwnedNamespace | None:
        return self._create_private_namespace_at(
            directories.staging_descriptor,
            directories.stage_root,
        )

    def _create_private_namespace_at(
        self,
        staging_descriptor: int,
        stage_root: Path,
        *,
        protected_capture: bool = False,
    ) -> _OwnedNamespace | None:
        for _ in range(_MAX_CREATE_ATTEMPTS):
            owned_staging_descriptor: int | None = None
            try:
                owned_staging_descriptor = os.dup(staging_descriptor)
                prefix = (
                    ".muse-reference-capture-"
                    if protected_capture
                    else ".muse-reference-owned-"
                )
                name = f"{prefix}{secrets.token_hex(12)}"
            except Exception:
                _close_quietly(owned_staging_descriptor)
                return None
            except BaseException:
                _close_quietly(owned_staging_descriptor)
                raise

            namespace: _OwnedNamespace | None = None
            try:
                namespace = _OwnedNamespace(
                    name=name,
                    path=stage_root / name,
                    namespace_descriptor=None,
                    staging_descriptor=owned_staging_descriptor,
                    is_protected_capture=protected_capture,
                )
                self._pending_namespaces[id(namespace)] = namespace
            except BaseException:
                if namespace is not None:
                    self._pending_namespaces.pop(id(namespace), None)
                _close_quietly(owned_staging_descriptor)
                raise

            namespace.creation_state = "uncertain"
            try:
                os.mkdir(name, 0o700, dir_fd=owned_staging_descriptor)
            except FileExistsError:
                namespace.creation_state = "absent"
                self._discard_uncreated_namespace(namespace)
                continue
            except Exception:
                namespace.creation_state = "absent"
                self._discard_uncreated_namespace(namespace)
                return None
            except BaseException:
                self._remove_empty_namespace(namespace)
                raise
            else:
                namespace.creation_state = "created"

            try:
                directory_entry = os.stat(
                    name,
                    dir_fd=owned_staging_descriptor,
                    follow_symlinks=False,
                )
                if not stat.S_ISDIR(directory_entry.st_mode):
                    raise OSError("owned staging namespace changed during creation")
                namespace.device = directory_entry.st_dev
                namespace.inode = directory_entry.st_ino
                namespace_descriptor = os.open(
                    name,
                    _DIRECTORY_FLAGS | _NOFOLLOW,
                    dir_fd=owned_staging_descriptor,
                )
                namespace.namespace_descriptor = namespace_descriptor
                namespace_stat = os.fstat(namespace_descriptor)
                is_private_directory = (
                    stat.S_ISDIR(namespace_stat.st_mode)
                    and stat.S_IMODE(namespace_stat.st_mode) & 0o077 == 0
                    and _same_directory(directory_entry, namespace_stat)
                )
                if not is_private_directory:
                    raise OSError("owned staging namespace is not private")
                return namespace
            except Exception:
                self._remove_empty_namespace(namespace)
                return None
            except BaseException:
                self._remove_empty_namespace(namespace)
                raise
        return None

    def _discard_uncreated_namespace(self, namespace: _OwnedNamespace) -> None:
        namespace.creation_state = "absent"
        self._pending_namespaces.pop(id(namespace), None)
        _close_namespace_descriptors(namespace)

    def _discard_missing_uncertain_namespace(
        self,
        namespace: _OwnedNamespace,
    ) -> bool:
        if (
            namespace.creation_state != "uncertain"
            or namespace.namespace_descriptor is not None
            or namespace.device is not None
            or namespace.inode is not None
        ):
            return False

        try:
            os.stat(
                namespace.name,
                dir_fd=namespace.staging_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            self._discard_uncreated_namespace(namespace)
            return True
        except OSError:
            return False
        return False

    def _write_staged_file(
        self,
        directories: _ResolvedDirectories,
        data: bytes,
        media_type: str,
    ) -> PreparedReference | TransferFailure:
        if not data or len(data) > self._policy.max_bytes:
            return _failure("reference_too_large", "参考内容超过暂存上限")
        if not directories.is_current():
            return _failure("staging_directory_changed", "暂存目录路径已发生变化")

        namespace = self._create_private_namespace(directories)
        if namespace is None:
            return _failure("staging_failed", "无法创建私有暂存目录")
        namespace_descriptor = namespace.namespace_descriptor
        if (
            namespace_descriptor is None
            or not directories.is_current()
            or not _path_matches_directory(
                namespace.path,
                namespace_descriptor,
                directories.upload_root,
            )
        ):
            self._remove_empty_namespace(namespace)
            return _failure("staging_directory_changed", "暂存目录路径已发生变化")

        extension = _MEDIA_TYPE_TO_EXTENSION[media_type]
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        for _ in range(_MAX_CREATE_ATTEMPTS):
            filename = f".muse-reference-{secrets.token_hex(16)}{extension}"
            path = namespace.path / filename
            descriptor: int | None = None
            owned: _OwnedStagedFile | None = None
            try:
                descriptor = os.open(filename, flags, 0o600, dir_fd=namespace_descriptor)
                owned = _OwnedStagedFile(
                    handle=None,
                    path=path,
                    namespace=namespace,
                    original_name=filename,
                    entry_name=filename,
                    device=None,
                    inode=None,
                    file_descriptor=descriptor,
                )
                namespace.file_owner = owned
                self._pending_namespaces.pop(id(namespace), None)
                self._pending_cleanup[id(owned)] = owned

                opened_stat = os.fstat(descriptor)
                if not stat.S_ISREG(opened_stat.st_mode):
                    raise OSError("staging target is not a regular file")
                owned.device = opened_stat.st_dev
                owned.inode = opened_stat.st_ino

                view = memoryview(data)
                offset = 0
                while offset < len(view):
                    chunk = view[offset : offset + WRITE_CHUNK_BYTES]
                    written = os.write(descriptor, chunk)
                    if written <= 0:
                        raise OSError("staging write made no progress")
                    offset += written

                final_stat = os.stat(
                    filename,
                    dir_fd=namespace_descriptor,
                    follow_symlinks=False,
                )
                if not _is_same_regular_file(final_stat, owned.device, owned.inode):
                    self._dispose_after_failure(owned)
                    return _failure("staging_path_changed", "暂存文件路径发生变化")
                if not directories.is_current() or not _path_matches_directory(
                    namespace.path,
                    namespace_descriptor,
                    directories.upload_root,
                ):
                    self._dispose_after_failure(owned)
                    return _failure("staging_directory_changed", "暂存目录路径已发生变化")

                handle = PreparedReference(
                    kind="local_muse_path",
                    locator=str(path),
                    content=None,
                    media_type=media_type,
                )
                owned.handle = handle
                self._owned_files[id(handle)] = owned
                self._pending_cleanup.pop(id(owned), None)
                return handle
            except FileExistsError:
                if owned is not None:
                    self._dispose_after_failure(owned)
                    return _failure("staging_failed", "参考文件暂存失败")
                _close_quietly(descriptor)
                continue
            except Exception:
                if owned is not None:
                    self._dispose_after_failure(owned)
                else:
                    _close_quietly(descriptor)
                    self._remove_empty_namespace(namespace)
                return _failure("staging_failed", "参考文件暂存失败")
            except BaseException:
                if owned is not None:
                    self._dispose_after_failure(owned)
                else:
                    _close_quietly(descriptor)
                    self._remove_empty_namespace(namespace)
                raise

        self._remove_empty_namespace(namespace)
        return _failure("staging_failed", "无法创建唯一暂存文件")

    def _dispose_after_failure(self, owned: _OwnedStagedFile) -> None:
        outcome = self._remove_owned_entry(owned)
        if outcome == "failed":
            self._pending_cleanup[id(owned)] = owned
            return
        self._pending_cleanup.pop(id(owned), None)
        self._finish_owned_file(owned)

    def _finish_owned_file(
        self,
        owned: _OwnedStagedFile,
    ) -> Literal["removed", "preserved", "pending"]:
        _close_quietly(owned.file_descriptor)
        owned.file_descriptor = -1
        owned.namespace.file_owner = owned
        return self._remove_empty_namespace(owned.namespace)

    def _remove_empty_namespace(
        self,
        namespace: _OwnedNamespace,
    ) -> Literal["removed", "preserved", "pending"]:
        self._pending_namespaces[id(namespace)] = namespace
        if namespace.is_protected_capture:
            return self._remove_protected_capture_namespace(namespace)

        if namespace.target_removed:
            return self._finish_removed_namespace(namespace)

        self._restore_captured_foreign_entries(namespace)
        for _ in range(_MAX_CREATE_ATTEMPTS):
            if namespace.captured_owned_name is not None:
                captured_outcome = self._remove_captured_owned_namespace(namespace)
                if captured_outcome != "uncaptured":
                    return captured_outcome

            source_name = self._find_owned_namespace_entry(namespace)
            if source_name is None:
                if self._discard_missing_uncertain_namespace(namespace):
                    return "removed"
                return "pending"
            contents = self._namespace_contents_state(namespace)
            if contents == "error":
                return "pending"
            if contents == "nonempty":
                return self._preserve_nonempty_namespace(namespace)

            capture_namespace = namespace.capture_namespace
            if capture_namespace is None:
                capture_namespace = self._create_private_namespace_at(
                    namespace.staging_descriptor,
                    namespace.path.parent,
                    protected_capture=True,
                )
                if capture_namespace is None:
                    return "pending"
                namespace.capture_namespace = capture_namespace

            captured_name = f"owned-{secrets.token_hex(12)}"
            try:
                _rename_without_replacing(
                    source_name,
                    namespace.staging_descriptor,
                    captured_name,
                    capture_namespace.namespace_descriptor or -1,
                )
            except FileNotFoundError:
                continue
            except OSError:
                return "pending"
            namespace.cleanup_source_name = source_name
            namespace.captured_owned_name = captured_name

        return "pending"

    def _remove_captured_owned_namespace(
        self,
        namespace: _OwnedNamespace,
    ) -> Literal["removed", "preserved", "pending", "uncaptured"]:
        captured_name = namespace.captured_owned_name
        if not self._captured_entry_matches_namespace(namespace, captured_name):
            if captured_name is not None:
                namespace.captured_foreign_names.append(captured_name)
            namespace.captured_owned_name = None
            self._restore_captured_foreign_entries(namespace)
            return "uncaptured"

        contents = self._namespace_contents_state(namespace)
        if contents == "error":
            return "pending"
        if contents == "nonempty":
            if not self._restore_captured_owned_namespace(namespace):
                return "pending"
            return self._preserve_nonempty_namespace(namespace)

        capture_namespace = namespace.capture_namespace
        if capture_namespace is None or captured_name is None:
            return "pending"
        try:
            os.rmdir(captured_name, dir_fd=capture_namespace.namespace_descriptor)
        except FileNotFoundError:
            namespace.captured_owned_name = None
            return "uncaptured"
        except OSError:
            if not self._captured_entry_matches_namespace(namespace, captured_name):
                namespace.captured_foreign_names.append(captured_name)
                namespace.captured_owned_name = None
                self._restore_captured_foreign_entries(namespace)
                return "uncaptured"
            self._restore_captured_owned_namespace(namespace)
            return "pending"

        namespace.captured_owned_name = None
        namespace.target_removed = True
        return self._finish_removed_namespace(namespace)

    def _preserve_nonempty_namespace(
        self,
        namespace: _OwnedNamespace,
    ) -> Literal["preserved", "pending"]:
        contents = self._namespace_contents_state(namespace)
        if contents != "nonempty":
            return "pending"
        if namespace.captured_owned_name is not None:
            if not self._restore_captured_owned_namespace(namespace):
                return "pending"
        self._restore_captured_foreign_entries(namespace)
        if namespace.captured_foreign_names:
            return "pending"
        capture_namespace = namespace.capture_namespace
        if capture_namespace is not None:
            if self._remove_protected_capture_namespace(capture_namespace) == "pending":
                return "pending"
            namespace.capture_namespace = None
        self._pending_namespaces.pop(id(namespace), None)
        _close_namespace_descriptors(namespace)
        return "preserved"

    def _finish_removed_namespace(
        self,
        namespace: _OwnedNamespace,
    ) -> Literal["removed", "pending"]:
        self._restore_captured_foreign_entries(namespace)
        if namespace.captured_foreign_names:
            return "pending"
        capture_namespace = namespace.capture_namespace
        if capture_namespace is not None:
            if self._remove_protected_capture_namespace(capture_namespace) == "pending":
                return "pending"
            namespace.capture_namespace = None
        self._pending_namespaces.pop(id(namespace), None)
        _close_namespace_descriptors(namespace)
        return "removed"

    def _remove_protected_capture_namespace(
        self,
        namespace: _OwnedNamespace,
    ) -> Literal["removed", "pending"]:
        if namespace.namespace_descriptor is None:
            if not self._bind_namespace_entry(namespace, namespace.name):
                if self._discard_missing_uncertain_namespace(namespace):
                    return "removed"
                return "pending"
        descriptor = namespace.namespace_descriptor
        if descriptor is None:
            return "pending"

        try:
            directory_entry = os.stat(
                namespace.name,
                dir_fd=namespace.staging_descriptor,
                follow_symlinks=False,
            )
            opened_stat = os.fstat(descriptor)
            entries = os.listdir(descriptor)
        except OSError:
            return "pending"
        if (
            not _same_directory(directory_entry, opened_stat)
            or namespace.device is None
            or namespace.inode is None
            or (directory_entry.st_dev, directory_entry.st_ino)
            != (namespace.device, namespace.inode)
            or entries
        ):
            return "pending"
        try:
            os.rmdir(namespace.name, dir_fd=namespace.staging_descriptor)
        except FileNotFoundError:
            return "pending"
        except OSError:
            return "pending"

        self._pending_namespaces.pop(id(namespace), None)
        _close_namespace_descriptors(namespace)
        return "removed"

    def _find_owned_namespace_entry(self, namespace: _OwnedNamespace) -> str | None:
        descriptor = namespace.namespace_descriptor
        if descriptor is not None:
            try:
                opened_stat = os.fstat(descriptor)
            except OSError:
                return None
            if namespace.device is None or namespace.inode is None:
                namespace.device = opened_stat.st_dev
                namespace.inode = opened_stat.st_ino
        has_identity = namespace.device is not None and namespace.inode is not None
        if not has_identity:
            if namespace.creation_state not in {"created", "uncertain"}:
                return None
            candidates = [namespace.name]
        else:
            try:
                names = os.listdir(namespace.staging_descriptor)
            except OSError:
                return None
            candidates = list(
                dict.fromkeys(
                    name
                    for name in (namespace.cleanup_source_name, namespace.name)
                    if name is not None
                )
            )
            candidates.extend(name for name in names if name not in candidates)

        for name in candidates:
            if self._bind_namespace_entry(namespace, name):
                return name
        return None

    def _bind_namespace_entry(self, namespace: _OwnedNamespace, name: str) -> bool:
        opened_descriptor: int | None = None
        try:
            directory_entry = os.stat(
                name,
                dir_fd=namespace.staging_descriptor,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(directory_entry.st_mode):
                return False
            if namespace.device is not None and namespace.inode is not None:
                if (directory_entry.st_dev, directory_entry.st_ino) != (
                    namespace.device,
                    namespace.inode,
                ):
                    return False

            descriptor = namespace.namespace_descriptor
            if descriptor is None:
                has_identity = namespace.device is not None and namespace.inode is not None
                if not has_identity and (
                    name != namespace.name
                    or namespace.creation_state not in {"created", "uncertain"}
                ):
                    return False
                opened_descriptor = os.open(
                    name,
                    _DIRECTORY_FLAGS | _NOFOLLOW,
                    dir_fd=namespace.staging_descriptor,
                )
                descriptor = opened_descriptor

            opened_stat = os.fstat(descriptor)
            if not _same_directory(directory_entry, opened_stat):
                return False
            if namespace.device is not None and namespace.inode is not None and (
                opened_stat.st_dev,
                opened_stat.st_ino,
            ) != (namespace.device, namespace.inode):
                return False

            if namespace.namespace_descriptor is None:
                namespace.namespace_descriptor = descriptor
                opened_descriptor = None
            if namespace.device is None or namespace.inode is None:
                namespace.device = opened_stat.st_dev
                namespace.inode = opened_stat.st_ino
            namespace.creation_state = "created"
            return True
        except OSError:
            return False
        finally:
            _close_quietly(opened_descriptor)

    def _namespace_contents_state(
        self,
        namespace: _OwnedNamespace,
    ) -> Literal["empty", "nonempty", "error"]:
        descriptor = namespace.namespace_descriptor
        if descriptor is None:
            return "error"
        try:
            return "empty" if not os.listdir(descriptor) else "nonempty"
        except OSError:
            return "error"

    def _captured_entry_matches_namespace(
        self,
        namespace: _OwnedNamespace,
        captured_name: str | None,
    ) -> bool:
        capture_namespace = namespace.capture_namespace
        descriptor = namespace.namespace_descriptor
        if captured_name is None or capture_namespace is None or descriptor is None:
            return False
        try:
            captured = os.stat(
                captured_name,
                dir_fd=capture_namespace.namespace_descriptor,
                follow_symlinks=False,
            )
            opened = os.fstat(descriptor)
        except OSError:
            return False
        return (
            _same_directory(captured, opened)
            and namespace.device is not None
            and namespace.inode is not None
            and (captured.st_dev, captured.st_ino) == (namespace.device, namespace.inode)
        )

    def _restore_captured_owned_namespace(self, namespace: _OwnedNamespace) -> bool:
        capture_namespace = namespace.capture_namespace
        captured_name = namespace.captured_owned_name
        if capture_namespace is None or captured_name is None:
            return False
        destination_name = namespace.cleanup_source_name or namespace.name
        try:
            _rename_without_replacing(
                captured_name,
                capture_namespace.namespace_descriptor or -1,
                destination_name,
                namespace.staging_descriptor,
            )
        except OSError:
            return False
        namespace.captured_owned_name = None
        self._remove_protected_capture_namespace(capture_namespace)
        if capture_namespace.namespace_descriptor is None:
            namespace.capture_namespace = None
        return True

    def _restore_captured_foreign_entries(self, namespace: _OwnedNamespace) -> None:
        capture_namespace = namespace.capture_namespace
        if capture_namespace is None:
            return
        remaining: list[str] = []
        for captured_name in namespace.captured_foreign_names:
            try:
                _rename_without_replacing(
                    captured_name,
                    capture_namespace.namespace_descriptor or -1,
                    namespace.name,
                    namespace.staging_descriptor,
                )
            except FileNotFoundError:
                continue
            except OSError:
                remaining.append(captured_name)
        namespace.captured_foreign_names = remaining

    def _remove_owned_entry(
        self,
        owned: _OwnedStagedFile,
    ) -> Literal["removed", "not_owned", "failed"]:
        namespace_descriptor = owned.namespace.namespace_descriptor
        if namespace_descriptor is None:
            return "failed"
        if owned.device is None or owned.inode is None:
            try:
                opened_stat = os.fstat(owned.file_descriptor)
            except OSError:
                return "failed"
            if not stat.S_ISREG(opened_stat.st_mode):
                return "failed"
            owned.device = opened_stat.st_dev
            owned.inode = opened_stat.st_ino
        if owned.entry_name != owned.original_name:
            return self._remove_captured_entry(owned)

        try:
            current = os.stat(
                owned.original_name,
                dir_fd=namespace_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return "removed"
        except OSError:
            return "failed"
        if not _is_same_regular_file(current, owned.device, owned.inode):
            return "not_owned"

        quarantine_name = f".muse-reference-quarantine-{secrets.token_hex(12)}"
        try:
            os.rename(
                owned.original_name,
                quarantine_name,
                src_dir_fd=namespace_descriptor,
                dst_dir_fd=namespace_descriptor,
            )
            owned.entry_name = quarantine_name
        except FileNotFoundError:
            return "removed"
        except OSError:
            return "failed"

        try:
            captured = os.stat(
                quarantine_name,
                dir_fd=namespace_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return "removed"
        except OSError:
            return "failed"

        if not _is_same_regular_file(captured, owned.device, owned.inode):
            restored = self._restore_captured_replacement(owned, quarantine_name, captured)
            return "not_owned" if restored else "failed"
        try:
            os.unlink(quarantine_name, dir_fd=namespace_descriptor)
        except FileNotFoundError:
            return "removed"
        except OSError:
            return "failed"
        return "removed"

    def _remove_captured_entry(
        self,
        owned: _OwnedStagedFile,
    ) -> Literal["removed", "not_owned", "failed"]:
        namespace_descriptor = owned.namespace.namespace_descriptor
        if namespace_descriptor is None:
            return "failed"
        try:
            current = os.stat(
                owned.entry_name,
                dir_fd=namespace_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return "removed"
        except OSError:
            return "failed"
        if _is_same_regular_file(current, owned.device, owned.inode):
            try:
                os.unlink(owned.entry_name, dir_fd=namespace_descriptor)
            except FileNotFoundError:
                return "removed"
            except OSError:
                return "failed"
            return "removed"
        restored = self._restore_captured_replacement(owned, owned.entry_name, current)
        return "not_owned" if restored else "failed"

    def _restore_captured_replacement(
        self,
        owned: _OwnedStagedFile,
        captured_name: str,
        captured_stat: os.stat_result,
    ) -> bool:
        namespace_descriptor = owned.namespace.namespace_descriptor
        if namespace_descriptor is None:
            return False
        try:
            try:
                current = os.stat(
                    owned.original_name,
                    dir_fd=namespace_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                os.link(
                    captured_name,
                    owned.original_name,
                    src_dir_fd=namespace_descriptor,
                    dst_dir_fd=namespace_descriptor,
                    follow_symlinks=False,
                )
            else:
                if (current.st_dev, current.st_ino) != (captured_stat.st_dev, captured_stat.st_ino):
                    return False
            os.unlink(captured_name, dir_fd=namespace_descriptor)
            return True
        except FileNotFoundError:
            try:
                current = os.stat(
                    owned.original_name,
                    dir_fd=namespace_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                return False
            return (current.st_dev, current.st_ino) == (captured_stat.st_dev, captured_stat.st_ino)
        except OSError:
            return False


def _normalize_media_type(value: object) -> str | None:
    if not isinstance(value, str) or value != value.strip() or ";" in value:
        return None
    normalized = value.lower()
    if normalized in _BLOCKED_MEDIA_TYPES or normalized not in _MEDIA_TYPE_TO_EXTENSION:
        return None
    return normalized


def _media_type_failure(value: object) -> TransferFailure:
    if isinstance(value, str) and value.strip().lower() in _BLOCKED_MEDIA_TYPES:
        return _failure("active_media_type", "不支持活动内容类型")
    return _failure("unsupported_media_type", "参考媒体类型不受支持")


def _media_type_for_path(path: Path) -> str | TransferFailure:
    extension = path.suffix.lower()
    if extension in _BLOCKED_EXTENSIONS:
        return _failure("active_media_type", "不支持活动内容类型")
    media_type = _EXTENSION_TO_MEDIA_TYPE.get(extension)
    if media_type is None:
        return _failure("unsupported_media_type", "参考文件扩展名不受支持")
    return media_type


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _same_directory(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(left.st_mode)
        and stat.S_ISDIR(right.st_mode)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
    )


def _is_same_regular_file(file_stat: os.stat_result, device: int, inode: int) -> bool:
    return (
        stat.S_ISREG(file_stat.st_mode)
        and file_stat.st_dev == device
        and file_stat.st_ino == inode
    )


def _open_absolute_directory(path: Path) -> int:
    descriptor = os.open(os.sep, _DIRECTORY_FLAGS | _NOFOLLOW)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(
                component,
                _DIRECTORY_FLAGS | _NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        _close_quietly(descriptor)
        raise


def _open_directory_beneath(parent_descriptor: int, components: tuple[str, ...]) -> int:
    descriptor = os.dup(parent_descriptor)
    try:
        for component in components:
            next_descriptor = os.open(
                component,
                _DIRECTORY_FLAGS | _NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        _close_quietly(descriptor)
        raise


def _path_matches_directory(path: Path, descriptor: int, approved_root: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        current_root = approved_root.resolve(strict=True)
        return (
            _is_within(resolved, current_root)
            and _same_directory(resolved.stat(), os.fstat(descriptor))
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _close_namespace_descriptors(namespace: _OwnedNamespace) -> None:
    _close_quietly(namespace.namespace_descriptor)
    namespace.namespace_descriptor = None
    _close_quietly(namespace.staging_descriptor)
    namespace.staging_descriptor = -1


def _rename_without_replacing(
    source_name: str,
    source_descriptor: int,
    destination_name: str,
    destination_descriptor: int,
) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(library, "renameatx_np", None)
        exclusive_flag = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = getattr(library, "renameat2", None)
        exclusive_flag = 0x00000001  # RENAME_NOREPLACE
    else:
        rename = None
        exclusive_flag = 0
    if rename is None:
        raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable")

    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    result = rename(
        source_descriptor,
        os.fsencode(source_name),
        destination_descriptor,
        os.fsencode(destination_name),
        exclusive_flag,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination_name)


def _close_quietly(descriptor: int | None) -> None:
    if descriptor is None or descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _failure(code: str, safe_detail: str) -> TransferFailure:
    return TransferFailure(code=code, safe_detail=safe_detail)
