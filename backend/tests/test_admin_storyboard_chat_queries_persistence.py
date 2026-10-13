from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_storyboard_chat_queries.persistence import (
    SqlAlchemyAdminStoryboardChatQueriesUnitOfWork,
)
from haoai_backend.admin_storyboard_chat_queries.tables import metadata


BASE_TIME = datetime(2026, 10, 13, 12, 0, 0)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with Session(engine) as session:
        session.execute(
            metadata.tables["series"].insert(),
            [{"id": "series-a"}, {"id": "series-b"}],
        )
        session.execute(
            metadata.tables["chapters"].insert(),
            [{"id": "chapter-a"}, {"id": "chapter-b"}],
        )
        from haoai_backend.admin_storyboard_chat_queries.tables import (
            ChatMessageRow,
            StoryboardAssetRow,
        )

        session.add_all(
            [
                StoryboardAssetRow(
                    id=f"asset-{index:02d}",
                    series_id="series-a" if index % 2 else "series-b",
                    chapter_id="chapter-a" if index % 3 else "chapter-b",
                    frame_index=index,
                    name=f"镜头-{index:02d}",
                    description=None if index % 2 else "描述",
                    image_url=None,
                    created_at=BASE_TIME + timedelta(seconds=index),
                    updated_at=BASE_TIME + timedelta(seconds=index),
                )
                for index in range(11)
            ]
        )
        session.add_all(
            [
                ChatMessageRow(
                    id=f"message-{index:02d}",
                    chapter_id="chapter-a" if index % 2 else "chapter-b",
                    frame_index=index,
                    asset_type=None,
                    asset_id=None,
                    chat_mode="chat",
                    role="assistant",
                    content=f"内容-{index:02d}",
                    model_name=None,
                    created_at=BASE_TIME + timedelta(seconds=index),
                )
                for index in range(11)
            ]
        )
        session.commit()

    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def test_lists_count_before_pagination_and_include_all_series_and_chapters(
    session_factory,
) -> None:
    unit = SqlAlchemyAdminStoryboardChatQueriesUnitOfWork(session_factory())
    assets, asset_total = unit.list_storyboard_assets(
        sort_field="created_at", sort_order="desc", skip=0, limit=10
    )
    messages, message_total = unit.list_chat_messages(
        sort_field="created_at", sort_order="desc", skip=0, limit=10
    )
    unit.close()

    assert asset_total == 11
    assert len(assets) == 10
    assert assets[0].id == "asset-10"
    assert {record.series_id for record in assets} == {"series-a", "series-b"}
    assert {record.chapter_id for record in assets} == {"chapter-a", "chapter-b"}
    assert message_total == 11
    assert len(messages) == 10
    assert {record.chapter_id for record in messages} == {"chapter-a", "chapter-b"}


def test_sort_uses_raw_column_fallback_and_only_exact_desc_is_descending(
    session_factory,
) -> None:
    unit = SqlAlchemyAdminStoryboardChatQueriesUnitOfWork(session_factory())
    named_desc, _ = unit.list_storyboard_assets(
        sort_field="name", sort_order="desc", skip=0, limit=11
    )
    unknown_desc, _ = unit.list_storyboard_assets(
        sort_field="no_such_attribute", sort_order="desc", skip=0, limit=11
    )
    named_default, _ = unit.list_storyboard_assets(
        sort_field="name", sort_order=" DESC ", skip=0, limit=11
    )
    unit.close()

    assert [record.name for record in named_desc] == sorted(
        (f"镜头-{index:02d}" for index in range(11)), reverse=True
    )
    assert [record.id for record in unknown_desc] == sorted(
        (f"asset-{index:02d}" for index in range(11)), reverse=True
    )
    assert [record.name for record in named_default] == sorted(
        f"镜头-{index:02d}" for index in range(11)
    )


def test_raw_metadata_registry_and_relationship_sort_errors_are_preserved(
    session_factory,
) -> None:
    cases = (
        ("storyboard", "metadata"),
        ("storyboard", "registry"),
        ("storyboard", "series"),
        ("chat", "metadata"),
        ("chat", "registry"),
        ("chat", "chapter"),
    )
    for kind, field in cases:
        unit = SqlAlchemyAdminStoryboardChatQueriesUnitOfWork(session_factory())
        with pytest.raises(SQLAlchemyError):
            if kind == "storyboard":
                unit.list_storyboard_assets(
                    sort_field=field, sort_order="asc", skip=0, limit=10
                )
            else:
                unit.list_chat_messages(
                    sort_field=field, sort_order="asc", skip=0, limit=10
                )
        unit.close()
