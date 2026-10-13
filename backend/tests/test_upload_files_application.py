"""Path policy checks for authenticated local upload reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from haoai_backend.upload_files.application import resolve_upload_file
from haoai_backend.upload_files.errors import (
    EmptyUploadPath,
    UploadFileNotFound,
    UnsafeUploadPath,
)
from haoai_backend.upload_files.local_files import LocalApprovedFileReader


class FixedUploadDirectory:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.calls = 0

    def get_upload_directory(self) -> Path:
        self.calls += 1
        return self.directory


def test_reads_encoded_name_and_in_root_symlink_from_the_resolved_root(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    source = upload_root / "字幕 角色.txt"
    source.write_text("owned test file", encoding="utf-8")
    alias = upload_root / "alias.txt"
    try:
        alias.symlink_to(source.name)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable in this test environment")

    provider = FixedUploadDirectory(upload_root)
    reader = LocalApprovedFileReader()
    resolved = resolve_upload_file(provider, reader, alias.name)

    assert resolved.path == source.resolve()
    assert resolved.path.read_text(encoding="utf-8") == "owned test file"
    assert provider.calls == 1


def test_empty_path_is_rejected_before_reading_the_directory() -> None:
    provider = FixedUploadDirectory(Path("unused"))

    with pytest.raises(EmptyUploadPath) as caught:
        resolve_upload_file(provider, LocalApprovedFileReader(), "")

    assert caught.value.status_code == 400
    assert caught.value.detail == "文件路径不能为空"
    assert provider.calls == 0


def test_traversal_absolute_outside_and_escaping_symlink_are_forbidden(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("owned outside fixture", encoding="utf-8")
    escaping_link = upload_root / "escape.txt"
    try:
        escaping_link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable in this test environment")

    provider = FixedUploadDirectory(upload_root)
    reader = LocalApprovedFileReader()
    for requested_path in ("../outside.txt", str(outside), str(escaping_link)):
        with pytest.raises(UnsafeUploadPath) as caught:
            resolve_upload_file(provider, reader, requested_path)
        assert caught.value.status_code == 403
        assert caught.value.detail == "非法文件路径"


def test_absolute_path_inside_root_remains_compatible(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    source = upload_root / "inside.txt"
    source.write_text("inside", encoding="utf-8")

    resolved = resolve_upload_file(
        FixedUploadDirectory(upload_root),
        LocalApprovedFileReader(),
        str(source),
    )

    assert resolved.path == source.resolve()


@pytest.mark.parametrize("requested_path", ["missing.txt", "directory"])
def test_missing_files_and_directories_are_reported_as_not_found(
    tmp_path: Path,
    requested_path: str,
) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    (upload_root / "directory").mkdir()

    with pytest.raises(UploadFileNotFound) as caught:
        resolve_upload_file(
            FixedUploadDirectory(upload_root),
            LocalApprovedFileReader(),
            requested_path,
        )

    assert caught.value.status_code == 404
    assert caught.value.detail == "文件不存在"
