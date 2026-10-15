"""Authenticated compatibility slice for storyboard generation."""

from .domain import StoryboardRuntimeCall, StoryboardRuntimeResult, StoryboardServerModel
from .openai_runtime import AiohttpStoryboardTextTransport, OpenAICompatibleStoryboardRuntime
from .ports import StoryboardGenerationRuntime

__all__ = [
    "StoryboardGenerationRuntime",
    "StoryboardRuntimeCall",
    "StoryboardRuntimeResult",
    "StoryboardServerModel",
    "AiohttpStoryboardTextTransport",
    "OpenAICompatibleStoryboardRuntime",
]
