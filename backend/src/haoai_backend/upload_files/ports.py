"""Ports used by the upload-file application."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .domain import ApprovedUploadFile


class UploadDirectoryProvider(Protocol):
    def get_upload_directory(self) -> str | Path: ...


class ApprovedLocalFileReader(Protocol):
    def read_approved_file(
        self,
        upload_directory: str | Path,
        file_path: str,
    ) -> ApprovedUploadFile: ...
