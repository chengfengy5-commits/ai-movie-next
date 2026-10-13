"""Administrator prompt-configuration management."""

from .domain import AdminPromptConfigsActor, PromptConfigRecord
from .http import build_admin_prompt_configs_router

__all__ = [
    "AdminPromptConfigsActor",
    "PromptConfigRecord",
    "build_admin_prompt_configs_router",
]
