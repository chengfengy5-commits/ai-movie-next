"""Framework-independent values used by authenticated upload-file access."""

from __future__ import annotations

from dataclasses import dataclass
from os import stat_result
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UploadFileActor:
    """Identity facts copied from authentication before its session closes."""

    user_id: str


@dataclass(frozen=True, slots=True)
class ApprovedUploadFile:
    """A resolved file and the stat snapshot obtained on a worker thread."""

    path: Path
    stat: stat_result
