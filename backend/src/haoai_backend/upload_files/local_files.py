"""Filesystem adapter that confines reads to the configured upload directory."""

from __future__ import annotations

import stat
from os import PathLike
from pathlib import Path

from .domain import ApprovedUploadFile
from .errors import UploadFileNotFound, UnsafeUploadPath


class LocalApprovedFileReader:
    """Resolve and stat local uploads without reading outside the approved root."""

    def read_approved_file(
        self,
        upload_directory: str | PathLike[str],
        file_path: str,
    ) -> ApprovedUploadFile:
        try:
            upload_root = Path(upload_directory).resolve()
            approved_path = (upload_root / file_path).resolve()
            approved_path.relative_to(upload_root)
        except (ValueError, RuntimeError) as exc:
            raise UnsafeUploadPath() from exc

        try:
            file_stat = approved_path.stat()
        except (OSError, ValueError) as exc:
            raise UploadFileNotFound() from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise UploadFileNotFound()
        return ApprovedUploadFile(path=approved_path, stat=file_stat)
