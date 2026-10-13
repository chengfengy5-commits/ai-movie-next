"""Pure validation and legacy media naming rules."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from .errors import InvalidMediaUpload

MediaKind = Literal["image", "audio", "video"]

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".wma")
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv")

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_RETRY_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class UploadSpec:
    kind: MediaKind
    business_type: str
    extension: str
    storage_extension: str
    content_type: str
    max_bytes: int


@dataclass(frozen=True, slots=True)
class RetryMediaSpec:
    extension: str
    storage_extension: str
    content_type: str
    allowed_content_prefix: str


_UPLOADS: dict[MediaKind, tuple[str, tuple[str, ...], int, dict[str, tuple[str, str]]]] = {
    "image": (
        "upload",
        IMAGE_EXTENSIONS,
        MAX_IMAGE_BYTES,
        {
            ".jpg": (".jpg", "image/jpeg"),
            ".jpeg": (".jpg", "image/jpeg"),
            ".png": (".png", "image/png"),
            ".gif": (".gif", "image/gif"),
            ".webp": (".webp", "image/webp"),
            ".bmp": (".bmp", "image/bmp"),
        },
    ),
    "audio": (
        "audio",
        AUDIO_EXTENSIONS,
        MAX_AUDIO_BYTES,
        {
            ".mp3": (".mp3", "audio/mpeg"),
            ".wav": (".wav", "audio/wav"),
            ".ogg": (".ogg", "audio/ogg"),
            ".m4a": (".m4a", "audio/mp4"),
            ".aac": (".aac", "audio/aac"),
            ".flac": (".flac", "audio/flac"),
            ".wma": (".wma", "audio/x-ms-wma"),
        },
    ),
    "video": (
        "video",
        VIDEO_EXTENSIONS,
        MAX_VIDEO_BYTES,
        {
            ".mp4": (".mp4", "video/mp4"),
            ".webm": (".webm", "video/webm"),
            ".mov": (".mov", "video/quicktime"),
            ".m4v": (".m4v", "video/x-m4v"),
            ".avi": (".avi", "video/x-msvideo"),
            ".mkv": (".mkv", "video/x-matroska"),
        },
    ),
}

_RETRY_MEDIA: dict[str, tuple[str, str]] = {
    ".aac": (".aac", "audio/aac"),
    ".avif": (".avif", "image/avif"),
    ".avi": (".avi", "video/x-msvideo"),
    ".bmp": (".bmp", "image/bmp"),
    ".flac": (".flac", "audio/flac"),
    ".gif": (".gif", "image/gif"),
    ".jpeg": (".jpg", "image/jpeg"),
    ".jpg": (".jpg", "image/jpeg"),
    ".m4a": (".m4a", "audio/mp4"),
    ".m4v": (".m4v", "video/x-m4v"),
    ".mkv": (".mkv", "video/x-matroska"),
    ".mov": (".mov", "video/quicktime"),
    ".mp3": (".mp3", "audio/mpeg"),
    ".mp4": (".mp4", "video/mp4"),
    ".mpeg": (".mpg", "video/mpeg"),
    ".mpg": (".mpg", "video/mpeg"),
    ".oga": (".oga", "audio/ogg"),
    ".ogg": (".ogg", "audio/ogg"),
    ".opus": (".opus", "audio/opus"),
    ".png": (".png", "image/png"),
    ".tif": (".tif", "image/tiff"),
    ".tiff": (".tif", "image/tiff"),
    ".wav": (".wav", "audio/wav"),
    ".webm": (".webm", "video/webm"),
    ".webp": (".webp", "image/webp"),
}

_IMAGE_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
    ".bmp": (b"BM",),
}


def upload_spec(kind: MediaKind, filename: str | None) -> UploadSpec:
    """Resolve the old filename extension before reading the uploaded body."""

    try:
        business_type, allowed, max_bytes, metadata = _UPLOADS[kind]
    except KeyError:
        raise ValueError("unknown media upload kind") from None
    extension = os.path.splitext(filename or "")[1].lower()
    if extension not in allowed:
        if kind == "image":
            detail = f"不支持的文件格式。允许的格式: {', '.join(IMAGE_EXTENSIONS)}"
        elif kind == "audio":
            detail = f"不支持的音频格式。允许的格式: {', '.join(AUDIO_EXTENSIONS)}"
        else:
            detail = f"不支持的视频格式。允许的格式: {', '.join(VIDEO_EXTENSIONS)}"
        raise InvalidMediaUpload(detail)
    storage_extension, content_type = metadata[extension]
    return UploadSpec(
        kind=kind,
        business_type=business_type,
        extension=extension,
        storage_extension=storage_extension,
        content_type=content_type,
        max_bytes=max_bytes,
    )


def validate_upload_content(spec: UploadSpec, content: bytes) -> None:
    if len(content) > spec.max_bytes:
        size_mb = spec.max_bytes // (1024 * 1024)
        raise InvalidMediaUpload(f"文件大小超过限制（最大 {size_mb}MB）")
    if spec.kind == "image" and not _valid_image_magic(content, spec.extension):
        raise InvalidMediaUpload("文件内容与扩展名不匹配，无法识别为有效的图片文件")


def retry_media_spec(extension: str) -> RetryMediaSpec | None:
    metadata = _RETRY_MEDIA.get(extension.lower())
    if metadata is None:
        return None
    storage_extension, content_type = metadata
    return RetryMediaSpec(
        extension=extension.lower(),
        storage_extension=storage_extension,
        content_type=content_type,
        allowed_content_prefix=content_type.split("/", 1)[0] + "/",
    )


def storage_media_type(extension: str) -> str | None:
    normalized = extension.lower()
    for _business, _allowed, _limit, metadata in _UPLOADS.values():
        for storage_extension, content_type in metadata.values():
            if normalized == storage_extension:
                return content_type
    for storage_extension, content_type in _RETRY_MEDIA.values():
        if normalized == storage_extension:
            return content_type
    return None


def _valid_image_magic(content: bytes, extension: str) -> bool:
    if not any(content.startswith(prefix) for prefix in _IMAGE_MAGIC[extension]):
        return False
    if extension == ".webp":
        return content[8:12] == b"WEBP"
    return True
