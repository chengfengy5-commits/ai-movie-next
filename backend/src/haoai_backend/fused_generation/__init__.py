"""Isolated compatibility slice for fused-image generation."""

from .domain import (
    FusedGenerationInlineImage,
    FusedGenerationOutput,
    FusedServerImageModel,
)
from .ports import (
    FusedGenerationImageStorage,
    FusedGenerationPlannedMediaStorage,
    FusedGenerationRuntime,
    FusedGenerationStorage,
)

__all__ = [
    "FusedGenerationInlineImage",
    "FusedGenerationImageStorage",
    "FusedGenerationPlannedMediaStorage",
    "FusedGenerationOutput",
    "FusedGenerationRuntime",
    "FusedGenerationStorage",
    "FusedServerImageModel",
]
