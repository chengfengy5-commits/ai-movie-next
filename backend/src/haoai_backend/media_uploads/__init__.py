"""Legacy-compatible media upload routes and their private adapters."""

from .application import MediaUploadApplication
from .http import build_media_uploads_router

__all__ = ["MediaUploadApplication", "build_media_uploads_router"]
