"""Download URL application module."""

from .compatibility import IdentityDownloadURLResolver
from .ports import DownloadURLResolver

__all__ = ["DownloadURLResolver", "IdentityDownloadURLResolver"]

