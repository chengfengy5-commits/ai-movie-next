"""Isolated compatibility slice for fused-image generation."""

from .domain import FusedGenerationOutput, FusedServerImageModel
from .ports import FusedGenerationRuntime, FusedGenerationStorage

__all__ = [
    "FusedGenerationOutput",
    "FusedGenerationRuntime",
    "FusedGenerationStorage",
    "FusedServerImageModel",
]
