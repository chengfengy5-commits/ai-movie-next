"""Isolated compatibility slice for fused-image generation."""

from .domain import (
    FusedGenerationInlineImage,
    FusedGenerationOutput,
    FusedServerImageModel,
)
from .ports import (
    FusedGenerationImageStorage,
    FusedGenerationRuntime,
    FusedGenerationStorage,
)

__all__ = [
    "FusedGenerationInlineImage",
    "FusedGenerationImageStorage",
    "FusedGenerationOutput",
    "FusedGenerationRuntime",
    "FusedGenerationStorage",
    "FusedServerImageModel",
]
