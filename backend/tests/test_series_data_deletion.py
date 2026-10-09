from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    insert,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.personal_production.rough_cut.tables import metadata as rough_metadata
from haoai_backend.series_data.application import delete_chapter, delete_frame, delete_series
from haoai_backend.series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from haoai_backend.series_data.schemas import DeleteFrameRequest
from haoai_backend.series_data.tables import (
    ai_tasks,
    chapters,
    characters,
    chat_messages,
    metadata as series_metadata,
    props,
    scenes,
    series,
    rough_cut_drafts,
    storyboard_assets,
)
from haoai_backend.shared.identity import TrustedActor


NOW = datetime(2026, 1, 1, 10, 0, 0)
OWNER = TrustedActor("owner")


@pytest.fixture
def deletion_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'series-deletion.sqlite'}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    auth_metadata.create_all(engine)
    series_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    rough_metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(auth_users).values(
            id="owner",
            username="剧集作者",
            email="owner@example.test",
            hashed_password="test-hash",
            is_superuser=False,
            membership_type="free",
            created_at=NOW,
            password_updated_at=None,
        ))
    yield engine
    engine.dispose()


def insert_series(engine: Engine, series_id: str, *, owner_id: str = "owner") -> None:
    with engine.begin() as connection:
        connection.execute(insert(series).values(
            id=series_id,
            user_id=owner_id,
            name=series_id,
            description=None,
            image_url=None,
            style_prompt_id=None,
            team_id=None,
            claimed_by=None,
            claimed_at=None,
            created_at=NOW,
            updated_at=NOW,
        ))


def insert_chapter(engine: Engine, chapter_id: str, series_id: str, content: object) -> None:
    encoded = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    with engine.begin() as connection:
        connection.execute(insert(chapters).values(
            id=chapter_id,
            series_id=series_id,
            title="测试章节",
            content=encoded,
            order=1,
            created_at=NOW,
            updated_at=NOW,
        ))


def asset_values(asset_id: str, chapter_id: str, series_id: str, frame_index: int) -> dict[str, Any]:
    return {
        "id": asset_id,
        "series_id": series_id,
        "chapter_id": chapter_id,
        "frame_index": frame_index,
        "name": f"镜头{frame_index + 1}",
        "description": None,
        "image_url": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def message_values(message_id: str, chapter_id: str, frame_index: int | None) -> dict[str, Any]:
    return {
        "id": message_id,
        "chapter_id": chapter_id,
        "frame_index": frame_index,
        "asset_type": None,
        "asset_id": None,
        "chat_mode": "normal",
        "role": "user",
        "content": message_id,
        "model_name": None,
        "created_at": NOW,
    }


def uow_factory(engine: Engine):
    return lambda: SqlAlchemySeriesDataUnitOfWork(Session(engine, future=True))


def test_deleted_frame_task_becomes_orphan_and_chapter_only_cleans_live_message_tasks(deletion_store: Engine) -> None:
    engine = deletion_store
    insert_series(engine, "series-a")
    insert_chapter(engine, "chapter-a", "series-a", [
        {"text": "删除", "storyboard": ["asset-a"]},
        {"text": "保留", "storyboard": ["asset-b"]},
    ])
    with engine.begin() as connection:
        connection.execute(insert(storyboard_assets), [
            asset_values("asset-a", "chapter-a", "series-a", 0),
            asset_values("asset-b", "chapter-a", "series-a", 1),
        ])
        connection.execute(insert(chat_messages), [
            message_values("message-a", "chapter-a", 0),
            message_values("message-b", "chapter-a", 1),
            message_values("message-null", "chapter-a", None),
        ])
        connection.execute(insert(ai_tasks), [
            {"id": "task-a", "message_id": "message-a", "user_id": "owner"},
            {"id": "task-b", "message_id": "message-b", "user_id": "owner"},
        ])

    factory = uow_factory(engine)
    delete_frame(factory, OWNER, "chapter-a", DeleteFrameRequest(frame_index=0))
    with Session(engine, future=True) as session:
        after_frame = session.execute(select(chat_messages).order_by(chat_messages.c.id)).mappings().all()
        tasks_after_frame = session.execute(select(ai_tasks.c.id, ai_tasks.c.message_id).order_by(ai_tasks.c.id)).all()
    assert [(row["id"], row["frame_index"]) for row in after_frame] == [
        ("message-b", 0),
        ("message-null", None),
    ]
    assert tasks_after_frame == [("task-a", "message-a"), ("task-b", "message-b")]

    delete_chapter(factory, OWNER, "chapter-a")
    with Session(engine, future=True) as session:
        assert session.execute(select(chat_messages.c.id)).all() == []
        assert session.execute(select(chapters.c.id)).all() == []
        tasks_after_chapter = session.execute(select(ai_tasks.c.id, ai_tasks.c.message_id)).all()
    assert tasks_after_chapter == [("task-a", "message-a")]


def test_whole_series_explicitly_deletes_tasks_for_existing_messages(deletion_store: Engine) -> None:
    engine = deletion_store
    insert_series(engine, "series-a")
    insert_chapter(engine, "chapter-a", "series-a", [{"text": "镜头"}])
    with engine.begin() as connection:
        connection.execute(insert(chat_messages).values(message_values("message-a", "chapter-a", 0)))
        connection.execute(insert(ai_tasks).values(
            id="task-a", message_id="message-a", user_id="owner",
        ))

    delete_series(uow_factory(engine), OWNER, "series-a")

    with Session(engine, future=True) as session:
        assert session.execute(select(series.c.id)).all() == []
        assert session.execute(select(chapters.c.id)).all() == []
        assert session.execute(select(chat_messages.c.id)).all() == []
        assert session.execute(select(ai_tasks.c.id)).all() == []


def test_chapter_cleanup_uses_chapter_id_even_for_asset_with_wrong_series_id(deletion_store: Engine) -> None:
    engine = deletion_store
    insert_series(engine, "series-a")
    insert_series(engine, "series-b")
    insert_chapter(engine, "chapter-a", "series-a", [{"text": "镜头", "storyboard": ["misfiled"]}])
    with engine.begin() as connection:
        connection.execute(insert(storyboard_assets).values(
            asset_values("misfiled", "chapter-a", "series-b", 0)
        ))

    delete_chapter(uow_factory(engine), OWNER, "chapter-a")

    with Session(engine, future=True) as session:
        assert session.execute(select(chapters.c.id).where(chapters.c.id == "chapter-a")).all() == []
        assert session.execute(select(storyboard_assets.c.id).where(storyboard_assets.c.id == "misfiled")).all() == []
        assert session.execute(select(series.c.id).order_by(series.c.id)).all() == [("series-a",), ("series-b",)]


def _add_billing_reference(engine: Engine):
    billing_metadata = MetaData()
    existing_tasks = Table(
        "ai_tasks",
        billing_metadata,
        Column("id", String(36), primary_key=True),
    )
    Table(
        "users",
        billing_metadata,
        Column("id", String(36), primary_key=True),
    )
    billing_units = Table(
        "billing_units",
        billing_metadata,
        Column("id", String(36), primary_key=True),
        Column("task_id", String(36), ForeignKey("ai_tasks.id"), nullable=False),
        Column("item_key", String(255), nullable=False),
        Column("operation", String(64), nullable=False),
        Column("step_graph_version", String(64), nullable=False),
        Column("input_digest", String(128), nullable=False),
        Column("quoted_amount", Integer, nullable=False),
        Column("debited_amount", Integer, nullable=False),
        Column("refunded_amount", Integer, nullable=False),
    )
    execution_steps = Table(
        "execution_steps",
        billing_metadata,
        Column("id", String(36), primary_key=True),
        Column(
            "billing_unit_id",
            String(36),
            ForeignKey("billing_units.id"),
            nullable=False,
        ),
        Column("step_key", String(128), nullable=False),
    )
    credit_logs = Table(
        "credit_logs",
        billing_metadata,
        Column("id", String(36), primary_key=True),
        Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
        Column("amount", Integer, nullable=False),
        Column("balance_after", Integer, nullable=False),
        Column("type", String(20), nullable=False),
        Column("business_key", String(255), unique=True),
        Column(
            "billing_unit_id",
            String(36),
            ForeignKey(
                "billing_units.id",
                name="fk_credit_logs_billing_unit_id_billing_units",
            ),
            nullable=True,
        ),
        Column(
            "related_debit_id",
            String(36),
            ForeignKey(
                "credit_logs.id",
                name="fk_credit_logs_related_debit_id_credit_logs",
            ),
            nullable=True,
        ),
    )
    billing_metadata.create_all(engine)
    return billing_units, execution_steps, credit_logs


@pytest.mark.parametrize("target", ["chapter", "series"])
def test_billing_foreign_key_rejects_source_delete_and_preserves_complete_ledger(
    deletion_store: Engine, target: str
) -> None:
    engine = deletion_store
    insert_series(engine, "series-a")
    insert_chapter(engine, "chapter-a", "series-a", [{"text": "镜头"}])
    billing_units, execution_steps, credit_logs = _add_billing_reference(engine)
    with engine.begin() as connection:
        connection.execute(insert(rough_cut_drafts).values(id="draft-a", chapter_id="chapter-a"))
        connection.execute(insert(storyboard_assets).values(
            asset_values("asset-a", "chapter-a", "series-a", 0)
        ))
        connection.execute(insert(storyboard_media_states).values(
            id="media-a",
            chapter_id="chapter-a",
            storyboard_asset_id="asset-a",
            media_revision=1,
            asset_image_digest="image-digest",
            preview_digest="preview-digest",
            source_valid=True,
            created_at=NOW,
            updated_at=NOW,
        ))
        connection.execute(insert(personal_production_notes).values(
            id="notes-a",
            chapter_id="chapter-a",
            user_id="owner",
            revision=1,
            frame_notes={"asset-a": {"status": "approved", "note": "keep"}},
            resume_frame_id="asset-a",
            created_at=NOW,
            updated_at=NOW,
        ))
        connection.execute(insert(chat_messages).values(message_values("message-a", "chapter-a", 0)))
        connection.execute(insert(ai_tasks).values(
            id="task-a",
            message_id="message-a",
            user_id="owner",
        ))
        connection.execute(insert(billing_units).values(
            id="bill-a",
            task_id="task-a",
            item_key="task-a:frame:0",
            operation="image_generation",
            step_graph_version="v1",
            input_digest="input-digest",
            quoted_amount=7,
            debited_amount=7,
            refunded_amount=2,
        ))
        connection.execute(insert(execution_steps).values(
            id="step-a",
            billing_unit_id="bill-a",
            step_key="generate",
        ))
        connection.execute(insert(credit_logs).values(
            id="credit-debit",
            user_id="owner",
            amount=-7,
            balance_after=93,
            type="usage",
            business_key="task-a:debit",
            billing_unit_id="bill-a",
            related_debit_id=None,
        ))
        connection.execute(insert(credit_logs).values(
            id="credit-refund",
            user_id="owner",
            amount=2,
            balance_after=95,
            type="refund",
            business_key="task-a:refund",
            billing_unit_id="bill-a",
            related_debit_id="credit-debit",
        ))

    tables = (
        series,
        chapters,
        rough_cut_drafts,
        storyboard_assets,
        chat_messages,
        ai_tasks,
        personal_production_notes,
        storyboard_media_states,
        billing_units,
        execution_steps,
        credit_logs,
    )
    before = _deletion_snapshot(engine, tables)

    with pytest.raises(IntegrityError):
        if target == "chapter":
            delete_chapter(uow_factory(engine), OWNER, "chapter-a")
        else:
            delete_series(uow_factory(engine), OWNER, "series-a")

    assert _deletion_snapshot(engine, tables) == before


def _deletion_snapshot(engine: Engine, tables: tuple[Any, ...]) -> dict[str, list[dict[str, Any]]]:
    with Session(engine, future=True) as session:
        return {
            table.name: [
                dict(row)
                for row in session.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings().all()
            ]
            for table in tables
        }


def test_chapter_orphan_cleanup_keeps_shared_and_other_series_assets(deletion_store: Engine) -> None:
    engine = deletion_store
    insert_series(engine, "series-a")
    insert_series(engine, "series-b")
    insert_chapter(engine, "chapter-a", "series-a", [{
        "text": "删除章节",
        "character": ["character-shared", "character-orphan"],
        "scene": ["scene-shared", "scene-orphan"],
        "prop": ["prop-shared", "prop-orphan"],
    }])
    insert_chapter(engine, "chapter-b", "series-a", [{
        "text": "保留章节",
        "character": ["character-shared"],
        "scene": ["scene-shared"],
        "prop": ["prop-shared"],
    }])
    created = {"created_at": NOW, "updated_at": NOW}
    with engine.begin() as connection:
        connection.execute(insert(characters), [
            {"id": "character-shared", "series_id": "series-a", "name": "共享人物", **created},
            {"id": "character-orphan", "series_id": "series-a", "name": "孤立人物", **created},
            {"id": "character-other", "series_id": "series-b", "name": "他剧人物", **created},
        ])
        connection.execute(insert(scenes), [
            {"id": "scene-shared", "series_id": "series-a", "title": "共享场景", **created},
            {"id": "scene-orphan", "series_id": "series-a", "title": "孤立场景", **created},
            {"id": "scene-other", "series_id": "series-b", "title": "他剧场景", **created},
        ])
        connection.execute(insert(props), [
            {"id": "prop-shared", "series_id": "series-a", "name": "共享道具", **created},
            {"id": "prop-orphan", "series_id": "series-a", "name": "孤立道具", **created},
            {"id": "prop-other", "series_id": "series-b", "name": "他剧道具", **created},
        ])

    delete_chapter(uow_factory(engine), OWNER, "chapter-a")

    with Session(engine, future=True) as session:
        character_ids = set(session.execute(select(characters.c.id)).scalars())
        scene_ids = set(session.execute(select(scenes.c.id)).scalars())
        prop_ids = set(session.execute(select(props.c.id)).scalars())
    assert character_ids == {"character-shared", "character-other"}
    assert scene_ids == {"scene-shared", "scene-other"}
    assert prop_ids == {"prop-shared", "prop-other"}
