from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import re
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol
from urllib.parse import urlsplit


MAX_TRANSFER_BYTES = 200 * 1024 * 1024
TRANSFER_CHUNK_BYTES = 64 * 1024
MAX_TRANSFER_SECONDS = 600
TRANSFER_DISK_RESERVE_BYTES = 16 * 1024 * 1024
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
R2_MAX_ATTEMPTS = 3
R2_REGION = "auto"
_ACTIVE_MEDIA_TYPES = frozenset({
    "application/javascript", "application/json", "application/x-javascript",
    "application/xhtml+xml", "application/xml", "image/svg+xml", "text/css",
    "text/html", "text/javascript", "text/json", "text/xml",
})
_ACTIVE_EXTENSIONS = frozenset({
    ".cjs", ".css", ".htm", ".html", ".js", ".json", ".mjs", ".svg",
    ".xhtml", ".xml",
})
_BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
_CANONICAL_EXTENSIONS = {
    "audio/aac": ".aac", "audio/flac": ".flac", "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/opus": ".opus",
    "audio/wav": ".wav", "image/avif": ".avif", "image/bmp": ".bmp",
    "image/gif": ".gif", "image/jpeg": ".jpg", "image/png": ".png",
    "image/tiff": ".tif", "image/webp": ".webp", "video/mp4": ".mp4",
    "video/mpeg": ".mpg", "video/quicktime": ".mov", "video/webm": ".webm",
    "video/x-m4v": ".m4v", "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}
_SAFE_EXTENSIONS = frozenset({
    ".aac", ".avif", ".avi", ".bmp", ".flac", ".gif", ".jpg", ".m4a",
    ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".mpg", ".oga", ".ogg", ".opus",
    ".png", ".tif", ".wav", ".webm", ".webp",
})


class TransferConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TransferLimits:
    max_bytes: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= MAX_TRANSFER_BYTES:
            raise ValueError("max_bytes must be between 1 byte and 200 MiB")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= MAX_TRANSFER_SECONDS
        ):
            raise ValueError("timeout_seconds must be positive and no greater than 600")


@dataclass(frozen=True, slots=True)
class MediaTypeRule:
    media_type: str
    extension: str

    def __post_init__(self) -> None:
        normalized = self.media_type.strip().lower()
        extension = self.extension.strip().lower()
        if (
            normalized != self.media_type
            or "/" not in normalized
            or ";" in normalized
            or normalized in _ACTIVE_MEDIA_TYPES
            or extension not in _SAFE_EXTENSIONS
            or extension in _ACTIVE_EXTENSIONS
            or _CANONICAL_EXTENSIONS.get(normalized) != extension
        ):
            raise TransferConfigurationError("invalid media type policy rule")


@dataclass(frozen=True, slots=True)
class SafeMediaPolicy:
    rules: tuple[MediaTypeRule, ...]
    missing_fallback: MediaTypeRule | None = None
    generic_fallback: MediaTypeRule | None = None

    def __post_init__(self) -> None:
        values = [rule.media_type for rule in self.rules]
        if not values or len(set(values)) != len(values):
            raise TransferConfigurationError("an explicit unique media policy is required")
        if any(
            rule is not None and not isinstance(rule, MediaTypeRule)
            for rule in (self.missing_fallback, self.generic_fallback)
        ):
            raise TransferConfigurationError("media fallbacks must use explicit policy rules")

    def resolve(self, value: str | None) -> MediaTypeRule | None:
        if value is None:
            return self.missing_fallback
        if not isinstance(value, str):
            return None
        media_type = value.split(";", 1)[0].strip().lower()
        if media_type in _ACTIVE_MEDIA_TYPES:
            return None
        if media_type == "application/octet-stream":
            return self.generic_fallback
        return next((rule for rule in self.rules if rule.media_type == media_type), None)


@dataclass(frozen=True, slots=True)
class R2Configuration:
    endpoint_url: str
    bucket: str
    public_base_url: str
    transfer_tmp_dir: Path
    business_type: str
    environment: str
    connect_timeout_seconds: float
    read_timeout_seconds: float
    managed_origin_map: tuple[tuple[str, str], ...] = ()
    legacy_public_hosts: tuple[str, ...] = ()
    legacy_public_origins: tuple[tuple[str, str, int], ...] = ()
    legacy_acceleration_map: tuple[
        tuple[tuple[str, str, int], tuple[str, str, int]], ...
    ] = ()
    allow_r2_dev: bool = False

    def __post_init__(self) -> None:
        endpoint = _storage_url(self.endpoint_url, "R2 endpoint")
        public = _storage_url(self.public_base_url, "R2 public base")
        if endpoint[1] == public[1]:
            raise TransferConfigurationError(
                "public media host must be separate from the R2 endpoint"
            )
        if not _BUCKET_RE.fullmatch(self.bucket) or ".." in self.bucket:
            raise TransferConfigurationError("invalid R2 bucket")
        if type(self.allow_r2_dev) is not bool:
            raise TransferConfigurationError("r2.dev allowance must be explicit")
        if self.environment not in {"development", "test", "production"}:
            raise TransferConfigurationError("environment must be explicit")
        if self.environment == "production" and (
            endpoint[0] != "https" or public[0] != "https"
        ):
            raise TransferConfigurationError("production R2 URLs must use HTTPS")
        if _is_r2_dev(public[1]) and (
            self.environment == "production" or not self.allow_r2_dev
        ):
            raise TransferConfigurationError(
                "r2.dev requires an explicit nonproduction configuration"
            )
        if not self.business_type or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", self.business_type
        ):
            raise TransferConfigurationError("business type is invalid")
        if not self.transfer_tmp_dir.is_absolute():
            raise TransferConfigurationError("transfer temp directory must be absolute")
        for timeout in (self.connect_timeout_seconds, self.read_timeout_seconds):
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not 0 < timeout <= MAX_TRANSFER_SECONDS
            ):
                raise TransferConfigurationError("SDK timeouts must be positive and bounded")
        for old, new in self.managed_origin_map:
            _origin_tuple(old, "managed origin")
            _origin_tuple(new, "managed target")
        for host in self.legacy_public_hosts:
            if (
                not isinstance(host, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*(?::[0-9]+)?", host)
            ):
                raise TransferConfigurationError("legacy public host is invalid")
        for origin in self.legacy_public_origins:
            _validate_origin_tuple(origin, "legacy public origin")
        legacy_origins = set(self.legacy_public_origins)
        public_origin = (public[0], public[1], public[2])
        for source, target in self.legacy_acceleration_map:
            _validate_origin_tuple(source, "legacy acceleration source")
            _validate_origin_tuple(target, "legacy acceleration target")
            if source not in legacy_origins:
                raise TransferConfigurationError(
                    "legacy acceleration source must be explicitly allowed"
                )
            if target == public_origin or _is_r2_dev(target[1]):
                raise TransferConfigurationError(
                    "legacy acceleration target cannot be current R2 or r2.dev"
                )

    @property
    def public_base(self) -> str:
        return self.public_base_url.rstrip("/")


def _storage_url(value: str, label: str) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        raise TransferConfigurationError(f"{label} is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise TransferConfigurationError(
            f"{label} must be a credential-free HTTP(S) URL"
        )
    if ":" in hostname:
        normalized_host = hostname.lower()
    else:
        try:
            normalized_host = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise TransferConfigurationError(f"{label} has an invalid host") from None
    return (
        parsed.scheme,
        normalized_host,
        port or (443 if parsed.scheme == "https" else 80),
        parsed.path,
    )


def _origin_tuple(value: str, label: str) -> tuple[str, str, int]:
    scheme, host, port, path = _storage_url(value, label)
    if path not in {"", "/"}:
        raise TransferConfigurationError(f"{label} must be an origin")
    return scheme, host, port


def _validate_origin_tuple(value: tuple[str, str, int], label: str) -> None:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or value[0] not in {"http", "https"}
        or not isinstance(value[1], str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", value[1])
        or type(value[2]) is not int
        or not 1 <= value[2] <= 65535
    ):
        raise TransferConfigurationError(f"{label} is invalid")


def _is_r2_dev(host: str) -> bool:
    return host == "r2.dev" or host.endswith(".r2.dev")

@dataclass(frozen=True, slots=True)
class R2ClientSettings:
    endpoint_url: str
    bucket: str
    region: str = R2_REGION
    signature_version: str = "s3v4"
    retry_mode: str = "standard"
    total_max_attempts: int = R2_MAX_ATTEMPTS
    connect_timeout_seconds: float = 10
    read_timeout_seconds: float = 60

    def __post_init__(self) -> None:
        if (
            self.region != R2_REGION
            or self.signature_version != "s3v4"
            or self.retry_mode != "standard"
            or self.total_max_attempts != R2_MAX_ATTEMPTS
        ):
            raise TransferConfigurationError("R2 client retry and signing policy is fixed")
        for timeout in (self.connect_timeout_seconds, self.read_timeout_seconds):
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                raise TransferConfigurationError("R2 client timeouts must be positive")


@dataclass(frozen=True, slots=True)
class SecretValue:
    value: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.value:
            raise TransferConfigurationError("credential handle returned an empty secret")


class SecretHandle(Protocol):
    def reveal(self) -> SecretValue: ...


class S3Client(Protocol):
    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: BinaryIO,
        ContentType: str,
        CacheControl: str,
    ) -> WriteReceipt: ...
    def close(self) -> None: ...


class S3ClientFactory(Protocol):
    def create(
        self,
        settings: R2ClientSettings,
        access_key: SecretValue,
        secret_key: SecretValue,
    ) -> S3Client: ...


@dataclass(frozen=True, slots=True)
class WriteReceipt:
    key: str
    status_code: int
    request_id: str

    @property
    def is_positive(self) -> bool:
        return self.status_code == 200 and bool(self.key) and bool(self.request_id)


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    released: bool
    safe_detail: str = ""


class OwnedDownload:
    __slots__ = (
        "_directory_fd", "_fd", "_name", "_directory", "_size", "_digest",
        "_hash", "_media_type", "_sealed", "_lock", "_file_identity",
        "_directory_identity", "_pending_reader_fds", "_name_removed",
    )

    def __init__(
        self,
        directory_fd: int,
        fd: int,
        name: str,
        directory: Path,
        *,
        file_identity: tuple[int, int],
        directory_identity: tuple[int, int],
    ) -> None:
        self._directory_fd = directory_fd
        self._fd = fd
        self._name = name
        self._directory = directory
        self._file_identity = file_identity
        self._directory_identity = directory_identity
        self._pending_reader_fds: list[tuple[int, tuple[int, int] | None]] = []
        self._name_removed = False
        self._size = 0
        self._digest = ""
        self._hash = hashlib.sha256()
        self._media_type = ""
        self._sealed = False
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return "OwnedDownload(state=sealed)" if self._sealed else "OwnedDownload(state=writing)"

    @property
    def size_bytes(self) -> int:
        return self._size

    @property
    def content_digest(self) -> str:
        if not self._sealed:
            raise RuntimeError("download is not sealed")
        return self._digest

    @property
    def media_type(self) -> str:
        if not self._sealed:
            raise RuntimeError("download is not sealed")
        return self._media_type

    @property
    def released(self) -> bool:
        return (
            self._fd < 0
            and self._directory_fd < 0
            and not self._pending_reader_fds
            and self._name_removed
        )

    def write_chunk(self, chunk: bytes, *, max_bytes: int) -> None:
        with self._lock:
            if self._sealed or self._fd < 0:
                raise OSError("owned download is closed")
            if not isinstance(chunk, bytes) or len(chunk) > TRANSFER_CHUNK_BYTES:
                raise OSError("invalid transfer chunk")
            if self._size + len(chunk) > max_bytes:
                raise OverflowError("transfer size limit exceeded")
            offset = 0
            while offset < len(chunk):
                written = os.write(self._fd, chunk[offset:])
                if written <= 0:
                    raise OSError("transfer write made no progress")
                self._hash.update(chunk[offset:offset + written])
                self._size += written
                offset += written

    def seal(self, media_type: str) -> None:
        with self._lock:
            if self._fd < 0 or self._sealed or self._size <= 0:
                raise OSError("owned download cannot be sealed")
            os.fsync(self._fd)
            self._digest = self._hash.hexdigest()
            self._media_type = media_type
            self._sealed = True

    def open_reader(self) -> BinaryIO:
        with self._lock:
            if not self._sealed or self._directory_fd < 0:
                raise OSError("owned download is not available")
            fd = -1
            identity: tuple[int, int] | None = self._file_identity
            operation_error: BaseException | None = None
            try:
                fd = os.open(
                    self._name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=self._directory_fd,
                )
                actual = os.fstat(fd)
                identity = (actual.st_dev, actual.st_ino)
                if (
                    not stat.S_ISREG(actual.st_mode)
                    or identity != self._file_identity
                ):
                    raise OSError("owned download identity changed")
                reader = os.fdopen(fd, "rb", closefd=True)
                fd = -1
                return reader
            except BaseException as exc:
                operation_error = exc

            if fd >= 0:
                closed, close_error, close_detail = _close_owned_fd(fd, identity)
                if not closed:
                    self._pending_reader_fds.append((fd, identity))
                if close_detail:
                    operation_error = operation_error or OSError(close_detail)
                if isinstance(operation_error, asyncio.CancelledError):
                    raise operation_error
                if close_error is not None:
                    raise close_error
            assert operation_error is not None
            raise operation_error

    def close(self) -> CleanupOutcome:
        with self._lock:
            errors: list[str] = []
            cancellation: asyncio.CancelledError | None = None
            retained_readers: list[tuple[int, tuple[int, int] | None]] = []
            for fd, identity in self._pending_reader_fds:
                closed, close_cancel, detail = _close_owned_fd(fd, identity)
                if close_cancel is not None and cancellation is None:
                    cancellation = close_cancel
                if not closed:
                    retained_readers.append((fd, identity))
                    if detail:
                        errors.append("owned reader descriptor close failed")
            self._pending_reader_fds = retained_readers
            if self._fd >= 0:
                closed, close_cancel, detail = _close_owned_fd(
                    self._fd, self._file_identity
                )
                if close_cancel is not None and cancellation is None:
                    cancellation = close_cancel
                if closed:
                    self._fd = -1
                elif detail:
                    errors.append("owned file descriptor close failed")
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
                    errors.append("owned temporary file identity check failed")
                else:
                    if (
                        not stat.S_ISREG(named.st_mode)
                        or (named.st_dev, named.st_ino) != self._file_identity
                    ):
                        errors.append("owned temporary file ownership mismatch")
                    else:
                        try:
                            os.unlink(self._name, dir_fd=self._directory_fd)
                            self._name_removed = True
                        except FileNotFoundError:
                            self._name_removed = True
                        except BaseException as exc:
                            if isinstance(exc, asyncio.CancelledError) and cancellation is None:
                                cancellation = exc
                            errors.append("owned temporary file cleanup failed")
            if self._name_removed and self._directory_fd >= 0:
                closed, close_cancel, detail = _close_owned_fd(
                    self._directory_fd, self._directory_identity
                )
                if close_cancel is not None and cancellation is None:
                    cancellation = close_cancel
                if closed:
                    self._directory_fd = -1
                elif detail:
                    errors.append("owned directory descriptor close failed")
            if cancellation is not None:
                raise cancellation
            released = self.released and not errors
            return CleanupOutcome(released, "; ".join(errors))


def _close_owned_fd(
    fd: int, identity: tuple[int, int] | None
) -> tuple[bool, asyncio.CancelledError | None, str | None]:
    cancellation: asyncio.CancelledError | None = None
    try:
        current = os.fstat(fd)
    except OSError as state_error:
        if state_error.errno == errno.EBADF:
            return True, None, None
        return False, None, "descriptor state is unknown"
    except BaseException as state_error:
        if isinstance(state_error, asyncio.CancelledError):
            cancellation = state_error
        return False, cancellation, "descriptor state is unknown"

    if identity is None:
        return False, cancellation, "descriptor identity is unknown"
    if (current.st_dev, current.st_ino) != identity:
        return True, cancellation, None

    try:
        os.close(fd)
        return True, cancellation, None
    except BaseException as close_error:
        if isinstance(close_error, asyncio.CancelledError) and cancellation is None:
            cancellation = close_error
        try:
            current = os.fstat(fd)
        except OSError as state_error:
            if state_error.errno == errno.EBADF:
                return True, cancellation, None
            return False, cancellation, "descriptor state is unknown"
        except BaseException as state_error:
            if isinstance(state_error, asyncio.CancelledError) and cancellation is None:
                cancellation = state_error
            return False, cancellation, "descriptor state is unknown"
        if (current.st_dev, current.st_ino) != identity:
            return True, cancellation, None
        return False, cancellation, "descriptor remains open"
