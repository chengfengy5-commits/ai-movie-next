from __future__ import annotations

import asyncio
import errno
import os
import secrets
import shutil
import stat
import threading
from pathlib import Path

from .transfer_types import (
    MAX_TRANSFER_BYTES,
    TRANSFER_DISK_RESERVE_BYTES,
    CleanupOutcome,
    OwnedDownload,
    TransferConfigurationError,
    _close_owned_fd,
)


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class RemoteStaging:
    def __init__(self, directory: Path) -> None:
        if not directory.is_absolute():
            raise TransferConfigurationError("transfer temp directory must be absolute")
        self._directory = directory
        self._pending: list[OwnedDownload | _PendingAcquisition] = []
        self._lock = threading.RLock()

    @property
    def pending_cleanup_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def create(self, max_bytes: int) -> OwnedDownload:
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_TRANSFER_BYTES:
            raise ValueError("invalid transfer size limit")
        directory_fd = -1
        file_fd = -1
        name = ""
        directory_identity: tuple[int, int] | None = None
        file_identity: tuple[int, int] | None = None
        operation_error: BaseException | None = None
        try:
            before = self._directory.lstat()
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise OSError("transfer temp directory is unavailable")
            directory_fd = os.open(self._directory, _DIRECTORY_FLAGS)
            opened = os.fstat(directory_fd)
            directory_identity = (opened.st_dev, opened.st_ino)
            after = self._directory.lstat()
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
            ):
                raise OSError("transfer temp directory identity changed")
            required = max_bytes * 2 + TRANSFER_DISK_RESERVE_BYTES
            if shutil.disk_usage(self._directory).free < required:
                raise OSError("transfer temp directory has insufficient free space")
            for _ in range(8):
                candidate = ".haoai-transfer-" + secrets.token_hex(16) + ".part"
                try:
                    file_fd = os.open(
                        candidate,
                        os.O_CREAT
                        | os.O_EXCL
                        | os.O_RDWR
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=directory_fd,
                    )
                    name = candidate
                    break
                except FileExistsError:
                    continue
            if file_fd < 0:
                raise OSError("could not create an owned transfer file")
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise OSError("created transfer object is not a regular file")
            file_identity = (file_stat.st_dev, file_stat.st_ino)
            result = OwnedDownload(
                directory_fd,
                file_fd,
                name,
                self._directory,
                file_identity=file_identity,
                directory_identity=directory_identity,
            )
            directory_fd = file_fd = -1
            return result
        except BaseException as exc:
            operation_error = exc

        cleanup_cancel: BaseException | None = None
        if file_fd >= 0 or directory_fd >= 0:
            owner = _PendingAcquisition(
                directory_fd=directory_fd,
                directory_identity=directory_identity,
                file_fd=file_fd,
                file_identity=file_identity,
                name=name,
            )
            file_fd = directory_fd = -1
            try:
                outcome = owner.close()
            except BaseException as exc:
                cleanup_cancel = exc
                outcome = CleanupOutcome(owner.released, "acquire cleanup interrupted")
            if not outcome.released:
                self._remember_pending(owner)

        if isinstance(operation_error, BaseException):
            if isinstance(operation_error, asyncio.CancelledError):
                raise operation_error
            if isinstance(cleanup_cancel, asyncio.CancelledError):
                raise cleanup_cancel
            if isinstance(operation_error, OSError):
                raise TransferConfigurationError(
                    "transfer temp directory is unavailable"
                ) from operation_error
            raise operation_error
        if cleanup_cancel is not None:
            raise cleanup_cancel
        raise TransferConfigurationError("transfer temp directory is unavailable")

    def release(self, download: OwnedDownload) -> CleanupOutcome:
        try:
            outcome = download.close()
        except BaseException:
            if not download.released:
                self._remember_pending(download)
            raise
        if not outcome.released:
            self._remember_pending(download)
        return outcome

    def retry_pending_cleanup(self) -> tuple[CleanupOutcome, ...]:
        with self._lock:
            pending = tuple(self._pending)
        outcomes: list[CleanupOutcome] = []
        cancellation: BaseException | None = None
        for owner in pending:
            try:
                outcome = owner.close()
            except BaseException as exc:
                if cancellation is None and isinstance(
                    exc, asyncio.CancelledError
                ):
                    cancellation = exc
                outcome = CleanupOutcome(owner.released, "pending cleanup interrupted")
            outcomes.append(outcome)
            if owner.released:
                with self._lock:
                    self._pending = [item for item in self._pending if item is not owner]
        if cancellation is not None:
            raise cancellation
        return tuple(outcomes)

    def _remember_pending(self, owner: OwnedDownload | _PendingAcquisition) -> None:
        with self._lock:
            if all(item is not owner for item in self._pending):
                self._pending.append(owner)


class _PendingAcquisition:
    def __init__(
        self,
        *,
        directory_fd: int,
        directory_identity: tuple[int, int] | None,
        file_fd: int,
        file_identity: tuple[int, int] | None,
        name: str,
    ) -> None:
        self._directory_fd = directory_fd
        self._directory_identity = directory_identity
        self._file_fd = file_fd
        self._file_identity = file_identity
        self._name = name
        self._name_removed = not bool(name)

    @property
    def released(self) -> bool:
        return (
            self._directory_fd < 0
            and self._file_fd < 0
            and self._name_removed
        )

    def close(self) -> CleanupOutcome:
        errors: list[str] = []
        cancellation: BaseException | None = None
        if self._file_fd >= 0:
            if self._file_identity is None:
                try:
                    os.fstat(self._file_fd)
                except OSError as exc:
                    if exc.errno == errno.EBADF:
                        self._file_fd = -1
                    else:
                        errors.append("created transfer descriptor state is unknown")
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        cancellation = exc
                    errors.append("created transfer descriptor state is unknown")
                else:
                    errors.append("created transfer descriptor identity is unavailable")
            else:
                closed, close_cancel, detail = _close_owned_fd(
                    self._file_fd, self._file_identity
                )
                if close_cancel is not None and cancellation is None:
                    cancellation = close_cancel
                if closed:
                    self._file_fd = -1
                elif detail:
                    errors.append("created transfer descriptor close failed")

        if not self._name_removed and self._directory_fd >= 0:
            try:
                named = os.stat(
                    self._name,
                    dir_fd=self._directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                self._name_removed = True
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError) and cancellation is None:
                    cancellation = exc
                errors.append("created transfer name identity check failed")
            else:
                if (
                    self._file_identity is None
                    or not stat.S_ISREG(named.st_mode)
                    or (named.st_dev, named.st_ino) != self._file_identity
                ):
                    errors.append("created transfer name ownership mismatch")
                else:
                    try:
                        os.unlink(self._name, dir_fd=self._directory_fd)
                        self._name_removed = True
                    except FileNotFoundError:
                        self._name_removed = True
                    except BaseException as exc:
                        if isinstance(exc, asyncio.CancelledError) and cancellation is None:
                            cancellation = exc
                        errors.append("created transfer name cleanup failed")

        if self._name_removed and self._directory_fd >= 0:
            closed, close_cancel, detail = _close_owned_fd(
                self._directory_fd, self._directory_identity
            )
            if close_cancel is not None and cancellation is None:
                cancellation = close_cancel
            if closed:
                self._directory_fd = -1
            elif detail:
                errors.append("created transfer directory descriptor close failed")

        if cancellation is not None:
            raise cancellation
        return CleanupOutcome(self.released and not errors, "; ".join(errors))
