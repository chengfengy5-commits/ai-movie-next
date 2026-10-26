"""Fail-closed settings for the explicitly launched backend host."""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from haoai_backend.media_storage.public_fetch_policy import parse_public_http_url
from haoai_backend.media_storage.transfer_types import (
    R2Configuration,
    SecretValue,
    TransferConfigurationError,
)


class ServerConfigurationError(ValueError):
    """A configuration problem whose message never contains supplied values."""


@dataclass(frozen=True, slots=True)
class ServerConfiguration:
    environment: str
    database_url: str = field(repr=False)
    secret_key: SecretValue
    r2_configuration: R2Configuration | None
    r2_access_key: SecretValue | None
    r2_secret_key: SecretValue | None
    frame_optimize_enabled: bool = False
    frame_optimize_credentials_file: Path | None = field(default=None, repr=False)
    user_task_max_concurrency: int = 0
    image_single_enabled: bool = False
    image_single_credentials_file: Path | None = field(default=None, repr=False)
    image_batch_enabled: bool = False
    image_batch_credentials_file: Path | None = field(default=None, repr=False)
    text_chat_enabled: bool = False
    video_single_enabled: bool = False
    video_single_credentials_file: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.user_task_max_concurrency) is not int:
            raise ServerConfigurationError("AI_USER_MAX_CONCURRENT must be an integer")
        if type(self.image_single_enabled) is not bool:
            raise ServerConfigurationError("IMAGE_SINGLE_ENABLED must be explicit")
        if type(self.image_batch_enabled) is not bool:
            raise ServerConfigurationError("IMAGE_BATCH_ENABLED must be explicit")
        if type(self.text_chat_enabled) is not bool:
            raise ServerConfigurationError("TEXT_CHAT_ENABLED must be explicit")
        if type(self.video_single_enabled) is not bool:
            raise ServerConfigurationError("VIDEO_SINGLE_ENABLED must be explicit")
        if self.image_single_credentials_file is not None and (
            not isinstance(self.image_single_credentials_file, Path)
            or not self.image_single_credentials_file.is_absolute()
        ):
            raise ServerConfigurationError(
                "IMAGE_SINGLE_CREDENTIALS_FILE must be an absolute path"
            )
        if self.image_single_enabled:
            if self.image_single_credentials_file is None:
                raise ServerConfigurationError(
                    "IMAGE_SINGLE_CREDENTIALS_FILE is required when image.single is enabled"
                )
            if (
                self.r2_configuration is None
                or self.r2_access_key is None
                or self.r2_secret_key is None
            ):
                raise ServerConfigurationError(
                    "image.single requires complete object storage"
                )
        if self.image_batch_credentials_file is not None and (
            not isinstance(self.image_batch_credentials_file, Path)
            or not self.image_batch_credentials_file.is_absolute()
        ):
            raise ServerConfigurationError(
                "IMAGE_BATCH_CREDENTIALS_FILE must be an absolute path"
            )
        if self.image_batch_enabled:
            if self.image_batch_credentials_file is None:
                raise ServerConfigurationError(
                    "IMAGE_BATCH_CREDENTIALS_FILE is required when image.batch is enabled"
                )
            if (
                self.r2_configuration is None
                or self.r2_access_key is None
                or self.r2_secret_key is None
            ):
                raise ServerConfigurationError(
                    "image.batch requires complete object storage"
                )
        if self.video_single_credentials_file is not None and (
            not isinstance(self.video_single_credentials_file, Path)
            or not self.video_single_credentials_file.is_absolute()
        ):
            raise ServerConfigurationError(
                "VIDEO_SINGLE_CREDENTIALS_FILE must be an absolute path"
            )
        if self.video_single_enabled:
            if self.video_single_credentials_file is None:
                raise ServerConfigurationError(
                    "VIDEO_SINGLE_CREDENTIALS_FILE is required when video.single is enabled"
                )
            if (
                self.r2_configuration is None
                or self.r2_access_key is None
                or self.r2_secret_key is None
            ):
                raise ServerConfigurationError(
                    "video.single requires complete object storage"
                )

        enabled_media_credentials = tuple(
            credentials_file
            for enabled, credentials_file in (
                (self.image_single_enabled, self.image_single_credentials_file),
                (self.image_batch_enabled, self.image_batch_credentials_file),
                (self.video_single_enabled, self.video_single_credentials_file),
            )
            if enabled
        )
        if enabled_media_credentials and any(
            credentials_file != enabled_media_credentials[0]
            for credentials_file in enabled_media_credentials[1:]
        ):
            raise ServerConfigurationError(
                "enabled media workers must use the same credentials file"
            )

    @property
    def frame_optimize_max_user_concurrency(self) -> int:
        """Deprecated read alias for the shared user task limit."""

        return self.user_task_max_concurrency


_R2_KEYS = (
    "OBJECT_STORAGE_ENDPOINT_URL",
    "OBJECT_STORAGE_ACCESS_KEY_ID",
    "OBJECT_STORAGE_SECRET_ACCESS_KEY",
    "OBJECT_STORAGE_BUCKET",
    "OBJECT_STORAGE_PUBLIC_BASE_URL",
    "TRANSFER_TMP_DIR",
)


def load_server_configuration(
    environ: Mapping[str, str] | None = None,
) -> ServerConfiguration:
    """Read only the host's named variables; never load dotenv or ambient AWS config."""
    values = os.environ if environ is None else environ
    environment = _required(values, "ENVIRONMENT")
    if environment not in {"development", "test", "production"}:
        raise ServerConfigurationError("ENVIRONMENT is unsupported")

    database_url = _required(values, "DATABASE_URL")
    _validate_database_url(database_url, environment)
    secret_key = _required(values, "SECRET_KEY")
    if environment == "production" and len(secret_key) < 32:
        raise ServerConfigurationError("SECRET_KEY must contain at least 32 characters")

    allow_r2_dev = _boolean(values, "ALLOW_R2_DEV", default=False)
    frame_optimize_enabled = _boolean(values, "FRAME_OPTIMIZE_ENABLED", default=False)
    frame_optimize_credentials_file = _optional_absolute_path(
        values, "FRAME_OPTIMIZE_CREDENTIALS_FILE"
    )
    user_task_max_concurrency = _user_task_concurrency_limit(values)
    if frame_optimize_enabled and frame_optimize_credentials_file is None:
        raise ServerConfigurationError(
            "FRAME_OPTIMIZE_CREDENTIALS_FILE is required when frame.optimize is enabled"
        )
    image_single_enabled = _boolean(
        values,
        "IMAGE_SINGLE_ENABLED",
        default=False,
    )
    image_single_credentials_file = _optional_absolute_path(
        values,
        "IMAGE_SINGLE_CREDENTIALS_FILE",
    )
    if image_single_enabled and image_single_credentials_file is None:
        raise ServerConfigurationError(
            "IMAGE_SINGLE_CREDENTIALS_FILE is required when image.single is enabled"
        )
    image_batch_enabled = _boolean(
        values,
        "IMAGE_BATCH_ENABLED",
        default=False,
    )
    text_chat_enabled = _boolean(
        values,
        "TEXT_CHAT_ENABLED",
        default=False,
    )
    image_batch_credentials_file = _optional_absolute_path(
        values,
        "IMAGE_BATCH_CREDENTIALS_FILE",
    )
    if image_batch_enabled and image_batch_credentials_file is None:
        raise ServerConfigurationError(
            "IMAGE_BATCH_CREDENTIALS_FILE is required when image.batch is enabled"
        )
    video_single_enabled = _boolean(
        values,
        "VIDEO_SINGLE_ENABLED",
        default=False,
    )
    video_single_credentials_file = _optional_absolute_path(
        values,
        "VIDEO_SINGLE_CREDENTIALS_FILE",
    )
    if video_single_enabled and video_single_credentials_file is None:
        raise ServerConfigurationError(
            "VIDEO_SINGLE_CREDENTIALS_FILE is required when video.single is enabled"
        )
    enabled_media_credentials = tuple(
        credentials_file
        for enabled, credentials_file in (
            (image_single_enabled, image_single_credentials_file),
            (image_batch_enabled, image_batch_credentials_file),
            (video_single_enabled, video_single_credentials_file),
        )
        if enabled
    )
    if enabled_media_credentials and any(
        credentials_file != enabled_media_credentials[0]
        for credentials_file in enabled_media_credentials[1:]
    ):
        raise ServerConfigurationError(
            "enabled media workers must use the same credentials file"
        )

    configured = tuple(key for key in _R2_KEYS if key in values)
    if not configured:
        if environment == "production":
            raise ServerConfigurationError("production object storage is required")
        if image_single_enabled:
            raise ServerConfigurationError(
                "image.single requires complete object storage"
            )
        if image_batch_enabled:
            raise ServerConfigurationError(
                "image.batch requires complete object storage"
            )
        if video_single_enabled:
            raise ServerConfigurationError(
                "video.single requires complete object storage"
            )
        if allow_r2_dev:
            raise ServerConfigurationError(
                "ALLOW_R2_DEV requires complete object storage"
            )
        return ServerConfiguration(
            environment=environment,
            database_url=database_url,
            secret_key=SecretValue(secret_key),
            r2_configuration=None,
            r2_access_key=None,
            r2_secret_key=None,
            frame_optimize_enabled=frame_optimize_enabled,
            frame_optimize_credentials_file=frame_optimize_credentials_file,
            user_task_max_concurrency=user_task_max_concurrency,
            image_single_enabled=image_single_enabled,
            image_single_credentials_file=image_single_credentials_file,
            image_batch_enabled=image_batch_enabled,
            image_batch_credentials_file=image_batch_credentials_file,
            text_chat_enabled=text_chat_enabled,
            video_single_enabled=video_single_enabled,
            video_single_credentials_file=video_single_credentials_file,
        )

    missing = tuple(
        key
        for key in _R2_KEYS
        if not isinstance(values.get(key), str) or not values[key].strip()
    )
    if missing:
        raise ServerConfigurationError(
            "object storage configuration is incomplete: " + ", ".join(missing)
        )
    if allow_r2_dev and environment == "production":
        raise ServerConfigurationError("ALLOW_R2_DEV is not supported in production")

    endpoint_url = values["OBJECT_STORAGE_ENDPOINT_URL"]
    public_base_url = values["OBJECT_STORAGE_PUBLIC_BASE_URL"]
    _validate_storage_url(endpoint_url, "OBJECT_STORAGE_ENDPOINT_URL")
    _validate_storage_url(public_base_url, "OBJECT_STORAGE_PUBLIC_BASE_URL")
    if urlsplit(endpoint_url).path not in {"", "/"}:
        raise ServerConfigurationError("OBJECT_STORAGE_ENDPOINT_URL must be an origin")
    tmp_dir = Path(values["TRANSFER_TMP_DIR"])
    _probe_existing_directory(tmp_dir)

    try:
        r2_configuration = R2Configuration(
            endpoint_url=endpoint_url,
            bucket=values["OBJECT_STORAGE_BUCKET"],
            public_base_url=public_base_url,
            transfer_tmp_dir=tmp_dir,
            business_type="upload",
            environment=environment,
            connect_timeout_seconds=10,
            read_timeout_seconds=60,
            allow_r2_dev=allow_r2_dev,
        )
    except (TransferConfigurationError, TypeError, ValueError):
        raise ServerConfigurationError(
            "object storage configuration is invalid"
        ) from None

    return ServerConfiguration(
        environment=environment,
        database_url=database_url,
        secret_key=SecretValue(secret_key),
        r2_configuration=r2_configuration,
        r2_access_key=SecretValue(values["OBJECT_STORAGE_ACCESS_KEY_ID"]),
        r2_secret_key=SecretValue(values["OBJECT_STORAGE_SECRET_ACCESS_KEY"]),
        frame_optimize_enabled=frame_optimize_enabled,
        frame_optimize_credentials_file=frame_optimize_credentials_file,
        user_task_max_concurrency=user_task_max_concurrency,
        image_single_enabled=image_single_enabled,
        image_single_credentials_file=image_single_credentials_file,
        image_batch_enabled=image_batch_enabled,
        image_batch_credentials_file=image_batch_credentials_file,
        text_chat_enabled=text_chat_enabled,
        video_single_enabled=video_single_enabled,
        video_single_credentials_file=video_single_credentials_file,
    )


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ServerConfigurationError(f"{name} is required")
    return value


def _boolean(environ: Mapping[str, str], name: str, *, default: bool) -> bool:
    value = environ.get(name)
    if value is None:
        return default
    if value == "true":
        return True
    if value == "false":
        return False
    raise ServerConfigurationError(f"{name} must be 'true' or 'false'")


def _optional_absolute_path(environ: Mapping[str, str], name: str) -> Path | None:
    value = environ.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ServerConfigurationError(f"{name} must be a non-empty absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise ServerConfigurationError(f"{name} must be an absolute path")
    return path


def _configured_integer(environ: Mapping[str, str], name: str) -> int | None:
    value = environ.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ServerConfigurationError(f"{name} must be an integer")
    try:
        return int(value)
    except ValueError:
        raise ServerConfigurationError(f"{name} must be an integer") from None


def _user_task_concurrency_limit(environ: Mapping[str, str]) -> int:
    configured = _configured_integer(environ, "AI_USER_MAX_CONCURRENT")
    legacy = _configured_integer(environ, "FRAME_OPTIMIZE_MAX_USER_CONCURRENCY")
    if configured is not None and legacy is not None and configured != legacy:
        raise ServerConfigurationError(
            "AI_USER_MAX_CONCURRENT conflicts with FRAME_OPTIMIZE_MAX_USER_CONCURRENCY"
        )
    if configured is not None:
        return configured
    if legacy is not None:
        return legacy
    return 0


def _validate_database_url(value: str, environment: str) -> None:
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        url = make_url(value)
    except (ArgumentError, TypeError, ValueError):
        raise ServerConfigurationError("DATABASE_URL is invalid") from None
    driver = url.drivername
    if driver in {"sqlite", "sqlite+pysqlite"}:
        if environment == "production":
            raise ServerConfigurationError("production requires PostgreSQL")
        database = url.database
        mode_values: list[str] = []
        for key, query_values in url.query.items():
            if key.casefold() != "mode":
                continue
            if isinstance(query_values, (tuple, list)):
                mode_values.extend(
                    value for value in query_values if isinstance(value, str)
                )
            elif isinstance(query_values, str):
                mode_values.append(query_values)
        if not database or database == ":memory:" or any(
            value.strip().casefold() == "memory" for value in mode_values
        ):
            raise ServerConfigurationError(
                "SQLite DATABASE_URL must use an absolute path"
            )
        if not Path(database).is_absolute():
            raise ServerConfigurationError(
                "SQLite DATABASE_URL must use an absolute path"
            )
        return
    if driver in {"postgresql", "postgresql+psycopg2"}:
        return
    raise ServerConfigurationError("DATABASE_URL must use SQLite or PostgreSQL")


def _validate_storage_url(value: str, name: str) -> None:
    try:
        parsed = parse_public_http_url(value)
    except (TypeError, ValueError):
        raise ServerConfigurationError(f"{name} is invalid") from None
    normalized_host = parsed.hostname.rstrip(".").lower()
    if (
        normalized_host == "localhost"
        or normalized_host in {"local", "internal"}
        or normalized_host.endswith((".localhost", ".local", ".internal"))
    ):
        raise ServerConfigurationError(f"{name} cannot use a local host")


def _probe_existing_directory(path: Path) -> None:
    if not path.is_absolute():
        raise ServerConfigurationError("TRANSFER_TMP_DIR must be an absolute directory")

    directory_fd: int | None = None
    probe_fd: int | None = None
    probe_name: str | None = None
    probe_identity: tuple[int, int] | None = None
    failed = False
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        directory_fd = os.open(path, flags)
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            raise OSError("not a directory")
        for _ in range(8):
            probe_name = f".haoai-write-probe-{secrets.token_hex(12)}"
            try:
                probe_fd = os.open(
                    probe_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                continue
        if probe_fd is None:
            raise OSError("unable to allocate probe")
        probe_identity_stat = os.fstat(probe_fd)
        probe_identity = (probe_identity_stat.st_dev, probe_identity_stat.st_ino)
        if os.write(probe_fd, b"probe") != len(b"probe"):
            raise OSError("probe write was incomplete")
    except (OSError, TypeError, ValueError):
        failed = True
    finally:
        if probe_fd is not None:
            try:
                os.close(probe_fd)
            except OSError:
                failed = True
        if directory_fd is not None:
            if probe_name is not None and probe_identity is not None:
                try:
                    current = os.stat(
                        probe_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                except OSError:
                    failed = True
                else:
                    if (current.st_dev, current.st_ino) == probe_identity:
                        try:
                            os.unlink(probe_name, dir_fd=directory_fd)
                        except OSError:
                            failed = True
                    else:
                        failed = True
            try:
                os.close(directory_fd)
            except OSError:
                failed = True
    if failed:
        raise ServerConfigurationError(
            "TRANSFER_TMP_DIR must be an existing writable directory"
        ) from None


__all__ = (
    "ServerConfiguration",
    "ServerConfigurationError",
    "load_server_configuration",
)
