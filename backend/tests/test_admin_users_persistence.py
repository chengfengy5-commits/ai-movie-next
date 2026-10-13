from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_users import persistence
from haoai_backend.admin_users.persistence import SqlAlchemyAdminUsersUnitOfWork
from haoai_backend.admin_users.tables import (
    AITaskRow,
    AssetDuplicateExclusionRow,
    Base,
    ChapterRow,
    ChatMessageRow,
    CharacterRow,
    CreditLogRow,
    ModelConfigRow,
    PropRow,
    PromptConfigRow,
    RedemptionCodeRow,
    SceneRow,
    SeriesRow,
    StoryboardAssetRow,
    TeamMemberRow,
    TeamRow,
    UserCreditRow,
    UserRow,
)

NOW = datetime(2026, 10, 13, 1, 2, 3)


def _make_engine(path: Path):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, _: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


def _user(user_id: str, *, username: str | None = None) -> UserRow:
    return UserRow(
        id=user_id,
        username=username or user_id,
        email=f"{user_id}@example.test",
        hashed_password="unused",
        is_superuser=False,
        membership_type="free",
        membership_expires_at=None,
        avatar_url=None,
        bio=None,
        created_at=NOW,
        password_updated_at=None,
    )


def test_raw_filters_count_before_sort_and_per_user_credit_queries(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path / "admin-users-query.sqlite")
    session = sessionmaker(engine, expire_on_commit=False)()
    sql: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda *_args: sql.append(_args[2]),
    )
    try:
        session.add_all(
            [
                _user("one", username="Ada"),
                _user("two", username="ada-lovelace"),
                _user("three", username="Grace"),
                UserRow(
                    **{
                        "id": "admin",
                        "username": "root",
                        "email": "root@example.test",
                        "hashed_password": "unused",
                        "is_superuser": True,
                        "membership_type": "premium",
                        "membership_expires_at": None,
                        "avatar_url": None,
                        "bio": "hidden sort field",
                        "created_at": NOW,
                        "password_updated_at": None,
                    }
                ),
            ]
        )
        session.add_all(
            [
                UserCreditRow(id="credit-one", user_id="one", credits=9),
                UserCreditRow(id="credit-three", user_id="three", credits=3),
            ]
        )
        session.commit()
        sql.clear()

        unit = SqlAlchemyAdminUsersUnitOfWork(session)
        records, total = unit.list_users(
            skip=0,
            limit=10,
            sort_field="username",
            sort_order="asc",
            username="%",
            email=None,
            membership_type="free",
            is_superuser=False,
        )

        assert total == 3
        assert [record.username for record in records] == ["Ada", "Grace", "ada-lovelace"]
        assert [record.credits for record in records] == [9, 3, 0]
        count_at = next(index for index, statement in enumerate(sql) if "count(" in statement.lower())
        user_page_at = next(
            index
            for index, statement in enumerate(sql)
            if "from users" in statement.lower() and "order by" in statement.lower()
        )
        assert count_at < user_page_at
        assert sum("from user_credits" in statement.lower() for statement in sql) == 3
        assert all(not hasattr(record, "hashed_password") for record in records)
    finally:
        session.close()
        engine.dispose()


def test_sort_field_uses_raw_getattr_including_relationship_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    class QuerySpy:
        def __init__(self) -> None:
            self.ordering: Any = None

        def count(self) -> int:
            return 0

        def order_by(self, ordering: Any) -> QuerySpy:
            self.ordering = ordering
            return self

        def offset(self, _: int) -> QuerySpy:
            return self

        def limit(self, _: int) -> QuerySpy:
            return self

        def all(self) -> list[Any]:
            return []

    class SessionSpy:
        def __init__(self) -> None:
            self.query_spy = QuerySpy()

        def query(self, _: Any) -> QuerySpy:
            return self.query_spy

    session = SessionSpy()
    monkeypatch.setattr(persistence, "asc", lambda value: value)
    monkeypatch.setattr(persistence, "desc", lambda value: value)

    SqlAlchemyAdminUsersUnitOfWork(session).list_users(
        skip=0,
        limit=10,
        sort_field="credit",
        sort_order="asc",
        username=None,
        email=None,
        membership_type=None,
        is_superuser=None,
    )

    assert session.query_spy.ordering is UserRow.credit


def test_delete_preserves_bulk_order_and_nullable_redeemed_by_relation(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path / "admin-users-delete.sqlite")
    session = sessionmaker(engine, expire_on_commit=False)()
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda *_args: statements.append(_args[2]),
    )
    try:
        target = _user("target")
        keeper = _user("keeper")
        session.add_all([target, keeper])
        session.flush()
        session.add_all(
            [
                UserCreditRow(id="target-credit", user_id="target", credits=12),
                CreditLogRow(
                    id="target-log",
                    user_id="target",
                    amount=-2,
                    balance_after=10,
                    type="usage",
                    description=None,
                    task_id=None,
                    business_key=None,
                    billing_unit_id=None,
                    related_debit_id=None,
                ),
                ModelConfigRow(
                    id="target-model",
                    user_id="target",
                    category="video",
                    name="model",
                    provider="local",
                    api_key="unused",
                ),
                PromptConfigRow(
                    id="target-prompt",
                    user_id="target",
                    category="story",
                ),
                RedemptionCodeRow(
                    id="redeemed",
                    code="redeemed-code",
                    type="credits",
                    value=10,
                    is_used=True,
                    used_by="target",
                    used_at=NOW,
                    created_by="keeper",
                    expires_at=None,
                ),
            ]
        )
        session.add_all(
            [
                SeriesRow(
                    id="target-series",
                    user_id="target",
                    name="target series",
                    description=None,
                    image_url=None,
                    style_prompt_id=None,
                    team_id=None,
                    claimed_by=None,
                    claimed_at=None,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                SeriesRow(
                    id="keeper-series",
                    user_id="keeper",
                    name="keeper series",
                    description=None,
                    image_url=None,
                    style_prompt_id=None,
                    team_id=None,
                    claimed_by=None,
                    claimed_at=None,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                ChapterRow(
                    id="target-chapter",
                    series_id="target-series",
                    title="target chapter",
                    content="text",
                    order=0,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                ChapterRow(
                    id="keeper-chapter",
                    series_id="keeper-series",
                    title="keeper chapter",
                    content=None,
                    order=0,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                CharacterRow(id="character", series_id="target-series", name="character"),
                SceneRow(id="scene", series_id="target-series", title="scene"),
                PropRow(id="prop", series_id="target-series", name="prop"),
                StoryboardAssetRow(
                    id="chapter-orphan-asset",
                    series_id="keeper-series",
                    chapter_id="target-chapter",
                    frame_index=0,
                    name="orphan by series id",
                    description=None,
                    image_url=None,
                ),
                StoryboardAssetRow(
                    id="series-orphan-asset",
                    series_id="target-series",
                    chapter_id="keeper-chapter",
                    frame_index=1,
                    name="orphan by chapter id",
                    description=None,
                    image_url=None,
                ),
                ChatMessageRow(
                    id="chat",
                    chapter_id="target-chapter",
                    frame_index=None,
                    asset_type=None,
                    asset_id=None,
                    chat_mode="chat",
                    role="user",
                    content="hello",
                    model_name=None,
                ),
                AssetDuplicateExclusionRow(
                    id="exclusion",
                    series_id="target-series",
                    asset_type="character",
                    asset_id_a="a",
                    asset_id_b="b",
                    created_by="target",
                ),
            ]
        )
        session.commit()
        statements.clear()

        unit = SqlAlchemyAdminUsersUnitOfWork(session)
        row = unit.load_user("target")
        assert row is not None
        unit.delete_related_user_data("target")
        unit.delete_user(row)
        unit.commit()

        deletes = [
            statement.split("DELETE FROM", 1)[1].strip().split()[0].strip('"`')
            for statement in statements
            if "DELETE FROM" in statement.upper()
        ]
        assert deletes == [
            "credit_logs",
            "user_credits",
            "prompt_configs",
            "model_configs",
            "chat_messages",
            "storyboard_assets",
            "chapters",
            "storyboard_assets",
            "props",
            "scenes",
            "characters",
            "asset_duplicate_exclusions",
            "series",
            "users",
        ]
        assert session.query(UserRow).filter(UserRow.id == "target").count() == 0
        assert session.query(SeriesRow).filter(SeriesRow.id == "target-series").count() == 0
        assert session.query(ChatMessageRow).filter(ChatMessageRow.id == "chat").count() == 0
        assert session.query(StoryboardAssetRow).count() == 0
        assert session.query(RedemptionCodeRow).filter(RedemptionCodeRow.id == "redeemed").one().used_by is None
        assert session.query(AssetDuplicateExclusionRow).count() == 0
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("association", ["ai_task", "team_member", "created_code", "external_task_quote"])
def test_nonnull_unhandled_user_references_keep_their_original_delete_failures(
    tmp_path: Path,
    association: str,
) -> None:
    engine = _make_engine(tmp_path / f"admin-users-{association}.sqlite")
    session = sessionmaker(engine, expire_on_commit=False)()
    try:
        target = _user("target")
        keeper = _user("keeper")
        session.add_all([target, keeper])
        session.flush()
        if association == "ai_task":
            session.add(
                AITaskRow(
                    id="task",
                    user_id="target",
                    type="image",
                    message_id="message",
                    billing_status="unbilled",
                )
            )
        elif association == "team_member":
            session.add(TeamRow(id="team", name="team", owner_id="keeper"))
            session.flush()
            session.add(
                TeamMemberRow(
                    id="membership",
                    team_id="team",
                    user_id="target",
                    role="member",
                    permissions=None,
                )
            )
        elif association == "created_code":
            session.add(
                RedemptionCodeRow(
                    id="created",
                    code="created-code",
                    type="credits",
                    value=1,
                    is_used=False,
                    used_by=None,
                    used_at=None,
                    created_by="target",
                    expires_at=None,
                )
            )
        else:
            session.execute(
                Base.metadata.tables["task_quotes"].insert().values(
                    id="quote",
                    user_id="target",
                )
            )
        session.commit()

        unit = SqlAlchemyAdminUsersUnitOfWork(session)
        row = unit.load_user("target")
        assert row is not None
        unit.delete_related_user_data("target")
        unit.delete_user(row)
        with pytest.raises(IntegrityError):
            unit.commit()
        unit.rollback()

        assert session.query(UserRow).filter(UserRow.id == "target").count() == 1
    finally:
        session.close()
        engine.dispose()
