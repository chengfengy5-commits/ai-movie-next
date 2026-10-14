"""Legacy-compatible chat text HTTP and stream composition."""

from .configuration import ChatTextGenerationConfiguration
from .ports import ChatTextGenerationRuntime

__all__ = ("ChatTextGenerationConfiguration", "ChatTextGenerationRuntime")
