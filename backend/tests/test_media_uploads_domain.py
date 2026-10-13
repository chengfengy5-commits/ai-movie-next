from __future__ import annotations

import pytest

from haoai_backend.media_uploads.domain import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    retry_media_spec,
    upload_spec,
    validate_upload_content,
)
from haoai_backend.media_uploads.errors import InvalidMediaUpload


@pytest.mark.parametrize(
    ("kind", "filename", "business_type", "extension", "content_type"),
    [
        ("image", "cover.jpeg", "upload", ".jpg", "image/jpeg"),
        ("audio", "voice.wma", "audio", ".wma", "audio/x-ms-wma"),
        ("video", "sample.mkv", "video", ".mkv", "video/x-matroska"),
    ],
)
def test_upload_specs_preserve_legacy_business_and_extension_mapping(
    kind: str,
    filename: str,
    business_type: str,
    extension: str,
    content_type: str,
) -> None:
    spec = upload_spec(kind, filename)  # type: ignore[arg-type]

    assert spec.business_type == business_type
    assert spec.storage_extension == extension
    assert spec.content_type == content_type


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("image.jpg", b"\xff\xd8\xff"),
        ("image.jpeg", b"\xff\xd8\xff"),
        ("image.png", b"\x89PNG\r\n\x1a\n"),
        ("image.gif", b"GIF87a"),
        ("image.gif", b"GIF89a"),
        ("image.webp", b"RIFF\x00\x00\x00\x00WEBP"),
        ("image.bmp", b"BM"),
    ],
)
def test_images_accept_only_the_legacy_magic_for_the_filename(
    filename: str,
    content: bytes,
) -> None:
    validate_upload_content(upload_spec("image", filename), content)


@pytest.mark.parametrize(
    ("kind", "filename"),
    [
        *((
            "image",
            f"file{extension}",
        ) for extension in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")),
        *((
            "audio",
            f"file{extension}",
        ) for extension in (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".wma")),
        *((
            "video",
            f"file{extension}",
        ) for extension in (".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv")),
    ],
)
def test_every_legacy_upload_extension_is_allowlisted(
    kind: str, filename: str
) -> None:
    assert upload_spec(kind, filename).extension == "." + filename.rsplit(".", 1)[-1]  # type: ignore[arg-type]


def test_webp_checks_the_webp_marker_after_riff_header() -> None:
    spec = upload_spec("image", "image.webp")

    with pytest.raises(InvalidMediaUpload, match="文件内容与扩展名不匹配"):
        validate_upload_content(spec, b"RIFF\x00\x00\x00\x00NOPE")


def test_audio_and_video_do_not_apply_image_magic_checks() -> None:
    validate_upload_content(upload_spec("audio", "voice.wma"), b"arbitrary bytes")
    validate_upload_content(upload_spec("video", "clip.mp4"), b"arbitrary bytes")


@pytest.mark.parametrize(
    ("kind", "filename", "limit"),
    [
        ("image", "image.png", MAX_IMAGE_BYTES),
        ("audio", "audio.mp3", MAX_AUDIO_BYTES),
        ("video", "video.mp4", MAX_VIDEO_BYTES),
    ],
)
def test_upload_size_limit_is_inclusive(kind: str, filename: str, limit: int) -> None:
    content = b"x" * limit
    if kind == "image":
        content = b"\x89PNG\r\n\x1a\n" + content[8:]

    validate_upload_content(upload_spec(kind, filename), content)  # type: ignore[arg-type]


def test_upload_size_limit_rejects_the_first_byte_over_limit() -> None:
    spec = upload_spec("audio", "audio.mp3")

    with pytest.raises(InvalidMediaUpload, match="最大 10MB"):
        validate_upload_content(spec, b"x" * (MAX_AUDIO_BYTES + 1))


def test_retry_extension_mapping_matches_the_shared_legacy_media_table() -> None:
    assert retry_media_spec(".jpeg").storage_extension == ".jpg"
    assert retry_media_spec(".tiff").storage_extension == ".tif"
    assert retry_media_spec(".mpeg").storage_extension == ".mpg"
    assert retry_media_spec(".wma") is None


def test_upload_error_order_checks_extension_before_content() -> None:
    with pytest.raises(InvalidMediaUpload, match="不支持的文件格式"):
        upload_spec("image", "image.svg")


@pytest.mark.parametrize(
    ("kind", "filename", "limit", "prefix"),
    [
        ("image", "image.png", MAX_IMAGE_BYTES, b"\x89PNG\r\n\x1a\n"),
        ("audio", "audio.mp3", MAX_AUDIO_BYTES, b""),
        ("video", "video.mp4", MAX_VIDEO_BYTES, b""),
    ],
)
def test_each_upload_rejects_the_first_byte_over_its_limit(
    kind: str, filename: str, limit: int, prefix: bytes
) -> None:
    content = prefix + b"x" * (limit + 1 - len(prefix))

    with pytest.raises(InvalidMediaUpload, match=f"最大 {limit // (1024 * 1024)}MB"):
        validate_upload_content(upload_spec(kind, filename), content)  # type: ignore[arg-type]
