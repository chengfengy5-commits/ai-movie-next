"""Upload-file use case orchestration without framework or SQL dependencies."""

from __future__ import annotations

from pathlib import Path

from .domain import ApprovedUploadFile
from .errors import EmptyUploadPath, UploadFilesUnavailable
from .ports import ApprovedLocalFileReader, UploadDirectoryProvider


def resolve_upload_file(
    directory_provider: UploadDirectoryProvider | None,
    file_reader: ApprovedLocalFileReader | None,
    file_path: str,
) -> ApprovedUploadFile:
    """Resolve one requested file using explicitly supplied local-file ports."""

    if not file_path:
        raise EmptyUploadPath()
    if directory_provider is None or file_reader is None:
        raise UploadFilesUnavailable()

    directory = directory_provider.get_upload_directory()
    if not directory:
        raise UploadFilesUnavailable()
    return file_reader.read_approved_file(Path(directory), file_path)
