"""Administrator system-prompt management."""

from .domain import AdminSystemPromptActor, SystemPromptRecord
from .http import build_admin_system_prompts_router

__all__ = [
    "AdminSystemPromptActor",
    "SystemPromptRecord",
    "build_admin_system_prompts_router",
]
