"""Pure catalog selection and operation-specific prompt policy."""

from __future__ import annotations

from collections.abc import Sequence

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
    UserModelOrder,
)
from .ports import CatalogReader
from .pricing import quote


def resolve_model(
    reader: CatalogReader,
    *,
    user_id: str,
    model_id: str,
    category: ModelCategory | str,
    size: object = None,
    duration: object = None,
    resolution: object = None,
) -> ModelSelection:
    """Select active system first, then an exact caller-owned BYOK model."""
    category_value = _category(category)
    _require_identity(user_id, model_id)

    system_model = reader.active_system_model(model_id, category_value.value)
    if system_model is not None:
        _validate_model_identity(
            system_model,
            model_id=model_id,
            category=category_value,
            owner_id=None,
            label="active system model",
        )
        if system_model.active:
            return _selection(
                system_model,
                QuoteRequest(category_value, size, duration, resolution),
            )

    user_model = reader.owned_model(user_id, model_id, category_value.value)
    if user_model is None:
        raise CatalogNotFound("model is not available to this user")
    _validate_model_identity(
        user_model,
        model_id=model_id,
        category=category_value,
        owner_id=user_id,
        label="owned model",
    )
    if not user_model.credential_configured:
        raise CatalogError("owned BYOK model has no configured credential")
    return _selection(
        user_model, QuoteRequest(category_value, size, duration, resolution)
    )


def select_text_model(
    reader: CatalogReader,
    *,
    user_id: str,
    model_id: str | None = None,
) -> ModelSelection:
    """Resolve an explicit text model or the legacy ordered automatic choice."""
    _require_identity(user_id)
    if model_id is not None:
        _require_identity(model_id)
        return resolve_model(
            reader,
            user_id=user_id,
            model_id=model_id,
            category=ModelCategory.TEXT,
        )

    system_models = _validated_text_models(
        reader.active_system_text_models(),
        owner_id=None,
        source="active system text models",
        require_credentials=False,
    )
    user_models = _validated_text_models(
        reader.owned_text_models(user_id),
        owner_id=user_id,
        source="owned text models",
        require_credentials=True,
    )

    # The legacy selector sorted each source by sort_order, then concatenated
    # system models before user models. Python sorting is stable, so ties retain
    # the order supplied by the corresponding repository query.
    candidates = sorted(system_models, key=lambda model: model.sort_order)
    candidates.extend(sorted(user_models, key=lambda model: model.sort_order))
    if not candidates:
        raise CatalogNotFound("no usable text model is available")

    orders = _validated_orders(reader.user_model_orders(user_id, "text"))
    rank_by_id: dict[str, int] = {}
    for rank, order in enumerate(orders):
        rank_by_id.setdefault(order.model_id, rank)

    # Stable ordering preserves legacy order for equal ranks and unlisted IDs.
    candidates.sort(key=lambda model: rank_by_id.get(model.id, len(orders)))
    return _selection(candidates[0], QuoteRequest(ModelCategory.TEXT))


def resolve_prompt(
    reader: CatalogReader,
    *,
    user_id: str,
    operation: PromptOperation | str,
    prompt_id: str | None,
) -> PromptSelection | None:
    """Resolve only active system or caller-owned prompts for this operation.

    Stream prompt IDs deliberately remain unapplied under the existing D6
    contract. No prompt port is called for that operation.
    """
    operation_value = _operation(operation)
    if operation_value is PromptOperation.TEXT_STREAM:
        return None
    if prompt_id is None or prompt_id == "":
        return None
    _require_identity(user_id, prompt_id)

    system_prompt = reader.active_system_prompt(prompt_id)
    if system_prompt is not None:
        _validate_prompt_identity(
            system_prompt,
            prompt_id=prompt_id,
            owner_id=None,
            label="active system prompt",
        )
        if system_prompt.active:
            return _prompt_selection(system_prompt, operation_value)

    owned_prompt = reader.owned_prompt(user_id, prompt_id)
    if owned_prompt is None:
        raise CatalogNotFound("prompt is not available to this user")
    _validate_prompt_identity(
        owned_prompt,
        prompt_id=prompt_id,
        owner_id=user_id,
        label="owned prompt",
    )
    return _prompt_selection(owned_prompt, operation_value)


def compose_batch_image_prompt(
    selection: PromptSelection | None, *, description: str
) -> str:
    """Compose the legacy batch-image prompt from templates and description."""
    if not isinstance(description, str) or not description.strip():
        raise CatalogError("image description must not be empty")
    parts: list[str] = []
    if selection is not None:
        if selection.operation is not PromptOperation.IMAGE_BATCH:
            raise CatalogError("prompt selection belongs to another operation")
        if selection.system_prompt.strip():
            parts.append(selection.system_prompt.strip())
        if selection.user_prompt.strip():
            parts.append(selection.user_prompt.strip())
    parts.append(description.strip())
    return "\n".join(parts).strip()


def _selection(model: CatalogModel, request: QuoteRequest) -> ModelSelection:
    result = quote(model, request)
    source = ModelSource.USER if model.owner_id is not None else ModelSource.SYSTEM
    return ModelSelection(
        catalog_id=model.id,
        category=model.category,
        source=source,
        display_name=model.display_name,
        wire_model=model.wire_model,
        quote=result,
    )


def _category(value: ModelCategory | str) -> ModelCategory:
    try:
        return ModelCategory(value)
    except (TypeError, ValueError) as exc:
        raise CatalogError("unsupported model category") from exc


def _operation(value: PromptOperation | str) -> PromptOperation:
    try:
        return PromptOperation(value)
    except (TypeError, ValueError) as exc:
        raise CatalogError("unsupported prompt operation") from exc


def _require_identity(*values: str) -> None:
    if any(not isinstance(value, str) or not value for value in values):
        raise CatalogError("user and catalog identities must be non-empty strings")


def _validate_model_identity(
    model: CatalogModel,
    *,
    model_id: str,
    category: ModelCategory,
    owner_id: str | None,
    label: str,
) -> None:
    if not isinstance(model, CatalogModel):
        raise CatalogError(f"{label} reader returned an invalid value")
    if model.id != model_id or model.category is not category:
        raise CatalogError(f"{label} reader returned a mismatched identity")
    if model.owner_id != owner_id:
        raise CatalogError(f"{label} reader returned a mismatched owner")


def _validated_text_models(
    models: Sequence[CatalogModel],
    *,
    owner_id: str | None,
    source: str,
    require_credentials: bool,
) -> list[CatalogModel]:
    result: list[CatalogModel] = []
    for model in models:
        if not isinstance(model, CatalogModel):
            raise CatalogError(f"{source} reader returned an invalid value")
        if model.category is not ModelCategory.TEXT:
            raise CatalogError(f"{source} reader returned a wrong category")
        if model.owner_id != owner_id:
            raise CatalogError(f"{source} reader returned a mismatched owner")
        if owner_id is None and not model.active:
            continue
        if require_credentials and not model.credential_configured:
            continue
        result.append(model)
    return result


def _validated_orders(orders: Sequence[UserModelOrder]) -> list[UserModelOrder]:
    if any(not isinstance(order, UserModelOrder) for order in orders):
        raise CatalogError("user model order reader returned an invalid value")
    return sorted(orders, key=lambda order: order.sort_order)


def _validate_prompt_identity(
    prompt: CatalogPrompt,
    *,
    prompt_id: str,
    owner_id: str | None,
    label: str,
) -> None:
    if not isinstance(prompt, CatalogPrompt):
        raise CatalogError(f"{label} reader returned an invalid value")
    if prompt.id != prompt_id or prompt.owner_id != owner_id:
        raise CatalogError(f"{label} reader returned a mismatched identity or owner")


def _prompt_selection(
    prompt: CatalogPrompt, operation: PromptOperation
) -> PromptSelection:
    source = ModelSource.USER if prompt.owner_id is not None else ModelSource.SYSTEM
    return PromptSelection(
        prompt_id=prompt.id,
        operation=operation,
        source=source,
        category=prompt.category,
        display_name=prompt.display_name,
        system_prompt=prompt.system_prompt or "",
        user_prompt=prompt.user_prompt or "",
    )
