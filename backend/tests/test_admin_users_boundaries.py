from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.orm import configure_mappers

from haoai_backend.admin_users.tables import (
    AITaskRow,
    AssetDuplicateExclusionRow,
    Base,
    CharacterRow,
    ChapterRow,
    CreditLogRow,
    ModelConfigRow,
    PromptConfigRow,
    PropRow,
    RedemptionCodeRow,
    SceneRow,
    SeriesRow,
    UserCreditRow,
    UserRow,
)


def test_private_models_keep_legacy_columns_and_nine_user_backrefs() -> None:
    configure_mappers()

    assert list(UserRow.__table__.columns.keys()) == [
        "id",
        "username",
        "email",
        "hashed_password",
        "is_superuser",
        "membership_type",
        "membership_expires_at",
        "avatar_url",
        "bio",
        "created_at",
        "password_updated_at",
    ]
    assert set(inspect(UserRow).relationships.keys()) == {
        "credit",
        "credit_logs",
        "created_redemption_codes",
        "redeemed_redemption_codes",
        "team_memberships",
        "series",
        "model_configs",
        "prompt_configs",
        "ai_tasks",
    }
    assert inspect(UserRow).relationships["credit"].uselist is True
    assert list(UserCreditRow.__table__.columns.keys()) == [
        "id",
        "user_id",
        "credits",
        "created_at",
        "updated_at",
    ]
    assert len(CreditLogRow.__table__.columns) == 11
    assert len(RedemptionCodeRow.__table__.columns) == 10


def test_series_and_child_mappings_preserve_full_relationships_and_hidden_columns() -> None:
    configure_mappers()

    assert len(SeriesRow.__table__.columns) == 11
    assert set(inspect(SeriesRow).relationships.keys()) == {
        "user",
        "chapters",
        "characters",
        "scenes",
        "props",
        "storyboard_assets",
        "fused_assets",
        "duplicate_exclusions",
    }
    assert {"aliases", "canonical_key"} <= set(CharacterRow.__table__.columns.keys())
    assert {"aliases", "canonical_key"} <= set(SceneRow.__table__.columns.keys())
    assert {"aliases", "canonical_key"} <= set(PropRow.__table__.columns.keys())
    assert len(AITaskRow.__table__.columns) == 23
    assert len(ModelConfigRow.__table__.columns) == 12
    assert len(PromptConfigRow.__table__.columns) == 9
    assert "chat_messages" in inspect(ChapterRow).relationships
    assert "created_by" in AssetDuplicateExclusionRow.__table__.columns
    assert AssetDuplicateExclusionRow.__table__.c.created_by.nullable


def test_external_user_foreign_keys_are_reference_only_and_not_loaded_as_private_models() -> None:
    for table_name in (
        "billing_units",
        "canvas_documents",
        "chapter_locks",
        "personal_production_notes",
        "rough_cut_drafts",
        "task_quotes",
        "task_submissions",
        "team_invites",
        "user_model_orders",
        "user_preferences",
        "user_prompt_orders",
        "user_sessions",
    ):
        table = Base.metadata.tables[table_name]
        assert table.info["external_reference_only"] is True

    assert next(iter(Base.metadata.tables["user_sessions"].c.user_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(Base.metadata.tables["user_preferences"].c.user_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(Base.metadata.tables["rough_cut_drafts"].c.user_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(Base.metadata.tables["task_quotes"].c.user_id.foreign_keys)).ondelete is None


def test_admin_users_has_no_private_orm_imports_or_app_factory_registration() -> None:
    package_dir = Path(__file__).parents[1] / "src" / "haoai_backend" / "admin_users"
    forbidden_prefixes = (
        "haoai_backend.admin_credit_queries",
        "haoai_backend.admin_redemption_codes",
        "haoai_backend.admin_series",
        "haoai_backend.admin_smtp_accounts_and_statistics",
        "haoai_backend.app",
    )
    for path in package_dir.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith(forbidden_prefixes), (path.name, node.module)
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith(forbidden_prefixes)
                    for alias in node.names
                ), path.name
