"""Authenticated compatibility slice for storyboard generation."""

from .domain import StoryboardRuntimeCall, StoryboardRuntimeResult, StoryboardServerModel
from .ports import StoryboardGenerationRuntime

__all__ = [
    "StoryboardGenerationRuntime",
    "StoryboardRuntimeCall",
    "StoryboardRuntimeResult",
    "StoryboardServerModel",
]
