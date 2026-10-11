"""Typed, framework-free values for catalog selection and server pricing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
FrozenJson: TypeAlias = JsonScalar | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]


class CatalogError(ValueError):
    """A catalog value or request cannot be safely resolved."""


class CatalogNotFound(CatalogError):
    """A requested model or prompt is not visible to the caller."""


class ModelCategory(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"


class ModelSource(StrEnum):
    SYSTEM = "system"
    USER = "user"


class PromptOperation(StrEnum):
    IMAGE_BATCH = "image.batch"
    REVIEW = "review"
    OPTIMIZE = "optimize"
    TEXT_STREAM = "text.stream"


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{field_name} must be a non-empty string")
    return value


def _require_public_label(value: str, field_name: str) -> str:
    _require_text(value, field_name)
    if "://" in value:
        raise CatalogError(f"{field_name} must not contain an endpoint URL")
    return value


def _freeze_json(value: object, field_name: str) -> FrozenJson:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise CatalogError(f"{field_name} must contain finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJson] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CatalogError(f"{field_name} keys must be strings")
            frozen[key] = _freeze_json(item, field_name)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, field_name) for item in value)
    raise CatalogError(f"{field_name} must contain JSON-compatible values")


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """Safe identity plus input metadata needed by pure pricing.

    Params are omitted from repr and never copied into a quote or selection.
    """

    id: str
    category: ModelCategory | str
    display_name: str
    wire_model: str | None
    owner_id: str | None
    base_cost: int | str | None
    params: Mapping[str, object] | str | None = field(default=None, repr=False)
    credential_configured: bool = False
    active: bool = True
    sort_order: int = 0

    def __post_init__(self) -> None:
        _require_public_label(self.id, "model id")
        _require_public_label(self.display_name, "model display_name")
        if self.wire_model is not None:
            _require_public_label(self.wire_model, "model wire_model")
        if self.owner_id is not None:
            _require_text(self.owner_id, "model owner_id")
        try:
            category = ModelCategory(self.category)
        except (TypeError, ValueError) as exc:
            raise CatalogError("unsupported model category") from exc
        object.__setattr__(self, "category", category)
        if self.params is not None and not isinstance(self.params, (str, Mapping)):
            raise CatalogError("model params must be a JSON string or mapping")
        if isinstance(self.params, Mapping):
            frozen = _freeze_json(self.params, "model params")
            if not isinstance(frozen, Mapping):
                raise CatalogError("model params must be an object")
            object.__setattr__(self, "params", frozen)
        if type(self.credential_configured) is not bool:
            raise CatalogError("credential_configured must be a boolean")
        if type(self.active) is not bool:
            raise CatalogError("active must be a boolean")
        if type(self.sort_order) is not int:
            raise CatalogError("sort_order must be an integer")


@dataclass(frozen=True, slots=True)
class CatalogPrompt:
    """Prompt text selected from a system row or one caller-owned row."""

    id: str
    category: str
    display_name: str
    owner_id: str | None
    system_prompt: str | None = None
    user_prompt: str | None = None
    active: bool = True

    def __post_init__(self) -> None:
        _require_public_label(self.id, "prompt id")
        _require_text(self.category, "prompt category")
        _require_public_label(self.display_name, "prompt display_name")
        if self.owner_id is not None:
            _require_text(self.owner_id, "prompt owner_id")
        for field_name, value in (
            ("system_prompt", self.system_prompt),
            ("user_prompt", self.user_prompt),
        ):
            if value is not None and not isinstance(value, str):
                raise CatalogError(f"{field_name} must be a string or None")
        if type(self.active) is not bool:
            raise CatalogError("prompt active must be a boolean")


@dataclass(frozen=True, slots=True)
class UserModelOrder:
    """One caller's display-order preference for a model."""

    model_id: str
    sort_order: int

    def __post_init__(self) -> None:
        _require_text(self.model_id, "ordered model id")
        if type(self.sort_order) is not int:
            raise CatalogError("user model sort_order must be an integer")


@dataclass(frozen=True, slots=True)
class QuoteRequest:
    """Client-selectable image/video options; fees always come from the catalog."""

    category: ModelCategory | str
    size: object = None
    duration: object = None
    resolution: object = None

    def __post_init__(self) -> None:
        try:
            category = ModelCategory(self.category)
        except (TypeError, ValueError) as exc:
            raise CatalogError("unsupported model category") from exc
        object.__setattr__(self, "category", category)


@dataclass(frozen=True, slots=True)
class ServerQuote:
    """Safe result of catalog pricing, with no credential or endpoint fields."""

    category: ModelCategory
    source: ModelSource
    amount: int
    effective_parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        try:
            category = ModelCategory(self.category)
            source = ModelSource(self.source)
        except (TypeError, ValueError) as exc:
            raise CatalogError("quote category or source is invalid") from exc
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "source", source)
        if type(self.amount) is not int or self.amount < 0:
            raise CatalogError("quote amount must be a non-negative integer")
        frozen = _freeze_json(self.effective_parameters, "effective_parameters")
        if not isinstance(frozen, Mapping):
            raise CatalogError("effective_parameters must be an object")
        if set(frozen) - {"size", "duration", "resolution"}:
            raise CatalogError("effective_parameters contain unsupported fields")
        object.__setattr__(self, "effective_parameters", frozen)


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """Model identity and quote safe for downstream preparation."""

    catalog_id: str
    category: ModelCategory
    source: ModelSource
    display_name: str
    wire_model: str | None
    quote: ServerQuote

    def __post_init__(self) -> None:
        _require_public_label(self.catalog_id, "catalog id")
        _require_public_label(self.display_name, "model display_name")
        if self.wire_model is not None:
            _require_public_label(self.wire_model, "model wire_model")
        if not isinstance(self.quote, ServerQuote):
            raise CatalogError("model selection requires a server quote")
        try:
            category = ModelCategory(self.category)
            source = ModelSource(self.source)
        except (TypeError, ValueError) as exc:
            raise CatalogError("model selection category or source is invalid") from exc
        if self.quote.category is not category or self.quote.source is not source:
            raise CatalogError("model selection and quote facts do not match")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "source", source)


@dataclass(frozen=True, slots=True)
class PromptSelection:
    """Operation-specific prompt selection with its text kept explicit."""

    prompt_id: str
    operation: PromptOperation
    source: ModelSource
    category: str
    display_name: str
    system_prompt: str
    user_prompt: str

    def __post_init__(self) -> None:
        _require_public_label(self.prompt_id, "prompt id")
        _require_text(self.category, "prompt category")
        _require_public_label(self.display_name, "prompt display_name")
        if not isinstance(self.system_prompt, str) or not isinstance(
            self.user_prompt, str
        ):
            raise CatalogError("selected prompt text must be strings")
        try:
            operation = PromptOperation(self.operation)
            source = ModelSource(self.source)
        except (TypeError, ValueError) as exc:
            raise CatalogError("prompt selection operation or source is invalid") from exc
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "source", source)
