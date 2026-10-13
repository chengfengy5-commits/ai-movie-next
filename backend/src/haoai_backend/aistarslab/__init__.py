"""AIStarsLab legacy API compatibility module."""

from .http import build_aistarslab_router
from .provider import AioHttpTransport

__all__ = ["AioHttpTransport", "build_aistarslab_router"]
