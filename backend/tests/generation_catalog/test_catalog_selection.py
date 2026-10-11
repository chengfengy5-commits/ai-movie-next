from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from haoai_backend.generation_catalog.application import (
    compose_batch_image_prompt,
    resolve_model,
    resolve_prompt,
    select_text_model,
)
from haoai_backend.generation_catalog.domain import (
    CatalogError,
    CatalogModel,
    CatalogNotFound,
    CatalogPrompt,
    ModelCategory,
    ModelSource,
    PromptOperation,
    UserModelOrder,
)


class FakeReader:
    def __init__(
        self,
        *,
        models: list[CatalogModel] | None = None,
        system_text: list[CatalogModel] | None = None,
        owned_text: list[CatalogModel] | None = None,
        orders: list[UserModelOrder] | None = None,
        prompts: list[CatalogPrompt] | None = None,
        active_system_override: CatalogModel | None = None,
        owned_model_override: CatalogModel | None = None,
        owned_prompt_override: CatalogPrompt | None = None,
    ) -> None:
        self.models = models or []
        self.system_text = system_text or []
        self.owned_text = owned_text or []
        self.orders = orders or []
        self.prompts = prompts or []
        self.active_system_override = active_system_override
        self.owned_model_override = owned_model_override
        self.owned_prompt_override = owned_prompt_override
        self.calls: list[str] = []

    def active_system_model(self, model_id: str, category: str) -> CatalogModel | None:
        self.calls.append("active_system_model")
        if self.active_system_override is not None:
            return self.active_system_override
        return next(
            (
                model
                for model in self.models
                if model.owner_id is None
                and model.id == model_id
                and model.category.value == category
                and model.active
            ),
            None,
        )

    def owned_model(
        self, user_id: str, model_id: str, category: str
    ) -> CatalogModel | None:
        self.calls.append("owned_model")
        if self.owned_model_override is not None:
            return self.owned_model_override
        return next(
            (
                model
                for model in self.models
                if model.owner_id == user_id
                and model.id == model_id
                and model.category.value == category
            ),
            None,
        )

    def active_system_text_models(self) -> list[CatalogModel]:
        self.calls.append("active_system_text_models")
        return self.system_text

    def owned_text_models(self, user_id: str) -> list[CatalogModel]:
        self.calls.append("owned_text_models")
        return self.owned_text

    def user_model_orders(
        self, user_id: str, category: str
    ) -> list[UserModelOrder]:
        self.calls.append("user_model_orders")
        return self.orders

    def active_system_prompt(self, prompt_id: str) -> CatalogPrompt | None:
        self.calls.append("active_system_prompt")
        return next(
            (
                prompt
                for prompt in self.prompts
                if prompt.owner_id is None
                and prompt.id == prompt_id
                and prompt.active
            ),
            None,
        )

    def owned_prompt(self, user_id: str, prompt_id: str) -> CatalogPrompt | None:
        self.calls.append("owned_prompt")
        if self.owned_prompt_override is not None:
            return self.owned_prompt_override
        return next(
            (
                prompt
                for prompt in self.prompts
                if prompt.owner_id == user_id and prompt.id == prompt_id
            ),
            None,
        )


def model(
    model_id: str,
    *,
    category: ModelCategory = ModelCategory.TEXT,
    owner_id: str | None = None,
    display_name: str | None = None,
    wire_model: str | None = None,
    base_cost: int = 0,
    sort_order: int = 0,
    active: bool = True,
    credential_configured: bool = True,
) -> CatalogModel:
    return CatalogModel(
        id=model_id,
        category=category,
        display_name=display_name or f"display-{model_id}",
        wire_model=wire_model or f"wire-{model_id}",
        owner_id=owner_id,
        base_cost=base_cost,
        sort_order=sort_order,
        active=active,
        credential_configured=credential_configured,
    )


def test_explicit_active_system_model_wins_and_returns_distinct_safe_identity() -> None:
    system = CatalogModel(
        id="shared-id",
        category="image",
        display_name="Administrator display",
        wire_model="vendor-image-v9",
        owner_id=None,
        base_cost=15,
        params={
            "billing_mode": "fixed",
            "fixed_by_quality": {"2K": "8"},
            "api_key": "secret-token-for-test",
            "headers": {"Authorization": "Bearer private-header"},
            "base_url": "https://credential.example/private",
        },
        credential_configured=True,
        active=True,
    )
    byok = model(
        "shared-id",
        category=ModelCategory.IMAGE,
        owner_id="user-1",
        base_cost=900,
    )
    reader = FakeReader(models=[byok, system])

    selected = resolve_model(
        reader,
        user_id="user-1",
        model_id="shared-id",
        category="image",
        size="2K-portrait",
    )

    assert selected.source is ModelSource.SYSTEM
    assert selected.catalog_id == "shared-id"
    assert selected.display_name == "Administrator display"
    assert selected.wire_model == "vendor-image-v9"
    assert selected.quote.amount == 8
    assert reader.calls == ["active_system_model"]
    rendered = repr(selected)
    assert "secret-token-for-test" not in rendered
    assert "private-header" not in rendered
    assert "credential.example" not in rendered


def test_inactive_system_row_does_not_hide_an_exact_owned_byok_model() -> None:
    owned = CatalogModel(
        id="shared-id",
        category="image",
        display_name="Owned display",
        wire_model="owned-wire",
        owner_id="user-1",
        base_cost=999,
        params={
            "image_size": {
                "enabled": True,
                "default": "1K-square",
                "options": ["square"],
            }
        },
        credential_configured=True,
    )
    inactive = model(
        "shared-id",
        category=ModelCategory.IMAGE,
        base_cost=10,
        active=False,
    )
    reader = FakeReader(models=[inactive, owned])

    selected = resolve_model(
        reader,
        user_id="user-1",
        model_id="shared-id",
        category=ModelCategory.IMAGE,
    )

    assert selected.source is ModelSource.USER
    assert selected.quote.amount == 0
    assert selected.quote.effective_parameters == {"size": "1K-square"}
    assert reader.calls == ["active_system_model", "owned_model"]


def test_wrong_category_from_system_reader_is_rejected_before_owned_fallback() -> None:
    reader = FakeReader(
        active_system_override=model("target-id", category=ModelCategory.VIDEO)
    )

    with pytest.raises(CatalogError, match="mismatched identity"):
        resolve_model(
            reader,
            user_id="user-1",
            model_id="target-id",
            category=ModelCategory.IMAGE,
        )

    assert reader.calls == ["active_system_model"]


def test_wrong_owner_from_owned_reader_is_rejected() -> None:
    reader = FakeReader(
        owned_model_override=model(
            "target-id",
            category=ModelCategory.IMAGE,
            owner_id="another-user",
        )
    )

    with pytest.raises(CatalogError, match="owner"):
        resolve_model(
            reader,
            user_id="user-1",
            model_id="target-id",
            category=ModelCategory.IMAGE,
        )

    assert reader.calls == ["active_system_model", "owned_model"]


def test_byok_without_credential_is_rejected_before_any_followup_read() -> None:
    reader = FakeReader(
        models=[
            model(
                "owned-text",
                owner_id="user-1",
                credential_configured=False,
            )
        ]
    )

    with pytest.raises(CatalogError, match="credential"):
        resolve_model(
            reader,
            user_id="user-1",
            model_id="owned-text",
            category=ModelCategory.TEXT,
        )

    assert reader.calls == ["active_system_model", "owned_model"]


def test_duplicate_stale_user_order_preserves_listed_model_precedence_and_price() -> None:
    reader = FakeReader(
        system_text=[
            model("unlisted-u", sort_order=1, base_cost=11),
            model("listed-b", sort_order=2, base_cost=37),
        ],
        orders=[
            UserModelOrder("missing-a", 0),
            UserModelOrder("missing-a", 1),
            UserModelOrder("listed-b", 2),
        ],
    )

    selected = select_text_model(reader, user_id="user-1")

    assert selected.catalog_id == "listed-b"
    assert selected.source is ModelSource.SYSTEM
    assert selected.quote.amount == 37


def test_automatic_text_model_uses_user_order_after_legacy_stable_merge() -> None:
    reader = FakeReader(
        system_text=[
            model("system-late", sort_order=5, base_cost=20),
            model("system-early", sort_order=1, base_cost=12),
        ],
        owned_text=[
            model("owned-late", owner_id="user-1", sort_order=9),
            model("owned-early", owner_id="user-1", sort_order=2),
            model(
                "owned-no-credential",
                owner_id="user-1",
                sort_order=0,
                credential_configured=False,
            ),
        ],
        orders=[
            UserModelOrder("system-late", 3),
            UserModelOrder("owned-late", 1),
            UserModelOrder("system-early", 2),
        ],
    )

    selected = select_text_model(reader, user_id="user-1")

    assert selected.catalog_id == "owned-late"
    assert selected.source is ModelSource.USER
    assert selected.quote.amount == 0
    assert reader.calls == [
        "active_system_text_models",
        "owned_text_models",
        "user_model_orders",
    ]


def test_automatic_text_ties_and_unlisted_ids_keep_legacy_stable_order() -> None:
    shared_system = model("shared", sort_order=4, base_cost=7)
    shared_owned = model("shared", owner_id="user-1", sort_order=0)
    reader = FakeReader(
        system_text=[model("system-late", sort_order=8), shared_system],
        owned_text=[shared_owned, model("owned", owner_id="user-1", sort_order=1)],
        orders=[UserModelOrder("shared", 4)],
    )

    selected = select_text_model(reader, user_id="user-1")

    # The same ordered ID has the same preference rank in both sources; the
    # system-first merged order breaks that tie, as the legacy selector did.
    assert selected.catalog_id == "shared"
    assert selected.source is ModelSource.SYSTEM
    assert selected.quote.amount == 7

    no_preferences = FakeReader(
        system_text=[model("system-8", sort_order=8), model("system-2", sort_order=2)],
        owned_text=[model("owned-0", owner_id="user-1", sort_order=0)],
    )
    unlisted = select_text_model(no_preferences, user_id="user-1")
    assert unlisted.catalog_id == "system-2"
    assert unlisted.source is ModelSource.SYSTEM


def test_batch_prompt_prefers_active_system_and_composes_legacy_text_order() -> None:
    system = CatalogPrompt(
        id="prompt-id",
        category="ai-review",
        display_name="System prompt",
        owner_id=None,
        system_prompt="  system instructions  ",
        user_prompt="  draw {description}  ",
        active=True,
    )
    owned = CatalogPrompt(
        id="prompt-id",
        category="image",
        display_name="Owned prompt",
        owner_id="user-1",
        system_prompt="owned system",
        user_prompt="owned user",
    )
    reader = FakeReader(prompts=[owned, system])

    selected = resolve_prompt(
        reader,
        user_id="user-1",
        operation=PromptOperation.IMAGE_BATCH,
        prompt_id="prompt-id",
    )

    assert selected is not None
    assert selected.source is ModelSource.SYSTEM
    assert selected.category == "ai-review"
    assert compose_batch_image_prompt(
        selected, description="  character description  "
    ) == "system instructions\ndraw {description}\ncharacter description"
    assert reader.calls == ["active_system_prompt"]


def test_review_and_optimize_allow_operation_specific_prompt_categories() -> None:
    prompt = CatalogPrompt(
        id="review-prompt",
        category="ai-review",
        display_name="Review prompt",
        owner_id="user-1",
        system_prompt="system",
        user_prompt="user",
    )
    reader = FakeReader(prompts=[prompt])

    review = resolve_prompt(
        reader,
        user_id="user-1",
        operation="review",
        prompt_id="review-prompt",
    )
    optimize = resolve_prompt(
        reader,
        user_id="user-1",
        operation="optimize",
        prompt_id="review-prompt",
    )

    assert review is not None and review.operation is PromptOperation.REVIEW
    assert optimize is not None and optimize.operation is PromptOperation.OPTIMIZE
    assert review.category == "ai-review"
    assert reader.calls == [
        "active_system_prompt",
        "owned_prompt",
        "active_system_prompt",
        "owned_prompt",
    ]


def test_stream_prompt_id_is_not_applied_and_causes_no_prompt_reads() -> None:
    reader = FakeReader(
        prompts=[
            CatalogPrompt(
                id="stream-prompt",
                category="text",
                display_name="Should not be read",
                owner_id=None,
            )
        ]
    )

    assert (
        resolve_prompt(
            reader,
            user_id="user-1",
            operation=PromptOperation.TEXT_STREAM,
            prompt_id="stream-prompt",
        )
        is None
    )
    assert reader.calls == []


def test_foreign_or_missing_prompt_is_rejected_and_empty_prompt_is_optional() -> None:
    foreign = CatalogPrompt(
        id="prompt-id",
        category="ai-review",
        display_name="Foreign",
        owner_id="another-user",
    )
    reader = FakeReader(owned_prompt_override=foreign)

    with pytest.raises(CatalogError, match="owner"):
        resolve_prompt(
            reader,
            user_id="user-1",
            operation=PromptOperation.REVIEW,
            prompt_id="prompt-id",
        )

    missing_reader = FakeReader()
    with pytest.raises(CatalogNotFound):
        resolve_prompt(
            missing_reader,
            user_id="user-1",
            operation=PromptOperation.OPTIMIZE,
            prompt_id="missing",
        )
    assert resolve_prompt(
        missing_reader,
        user_id="user-1",
        operation=PromptOperation.OPTIMIZE,
        prompt_id="",
    ) is None


def test_batch_prompt_rejects_a_selection_from_another_operation() -> None:
    reader = FakeReader(
        prompts=[
            CatalogPrompt(
                id="review-prompt",
                category="ai-review",
                display_name="Review",
                owner_id=None,
            )
        ]
    )
    selection = resolve_prompt(
        reader,
        user_id="user-1",
        operation=PromptOperation.REVIEW,
        prompt_id="review-prompt",
    )
    assert selection is not None

    with pytest.raises(CatalogError, match="another operation"):
        compose_batch_image_prompt(selection, description="scene")


def test_modules_import_without_orm_http_legacy_or_network_dependencies() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src"
    script = r"""
import builtins
import sys

forbidden = {"sqlalchemy", "fastapi", "app", "requests", "httpx", "socket"}
original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    root = name.split(".", 1)[0]
    if root in forbidden:
        raise AssertionError(f"forbidden import: {name}")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import haoai_backend.generation_catalog
import haoai_backend.generation_catalog.application
import haoai_backend.generation_catalog.domain
import haoai_backend.generation_catalog.ports
import haoai_backend.generation_catalog.pricing
assert not any(
    name.split(".", 1)[0] in forbidden
    for name in sys.modules
)
"""
    environment = {
        "PYTHONPATH": str(source_root),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
