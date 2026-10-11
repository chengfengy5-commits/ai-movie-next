"""Pure catalog selection and pricing primitives."""

from .application import (
    compose_batch_image_prompt,
    resolve_model,
    resolve_prompt,
    select_text_model,
)
from .domain import (
    CatalogError,
    CatalogModel,
    CatalogNotFound,
    CatalogPrompt,
    ModelCategory,
    ModelSelection,
    ModelSource,
    PromptOperation,
    PromptSelection,
    QuoteRequest,
    ServerQuote,
    UserModelOrder,
)
from .ports import CatalogReader
from .pricing import PricingError, quote

__all__ = [
    "CatalogError",
    "CatalogModel",
    "CatalogNotFound",
    "CatalogPrompt",
    "CatalogReader",
    "ModelCategory",
    "ModelSelection",
    "ModelSource",
    "PricingError",
    "PromptOperation",
    "PromptSelection",
    "QuoteRequest",
    "ServerQuote",
    "UserModelOrder",
    "compose_batch_image_prompt",
    "quote",
    "resolve_model",
    "resolve_prompt",
    "select_text_model",
]
