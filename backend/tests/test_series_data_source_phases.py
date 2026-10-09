from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import create_engine, event, insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.errors import ChapterMediaStateError
from haoai_backend.personal_production.notes.media import digest_media_identity, normalize_media_identity
from haoai_backend.personal_production.notes.persistence import SqlAlchemyNotesUnitOfWork
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_data.application import create_chapter, delete_frame, update_chapter
from haoai_backend.series_data.domain import ChapterRecord
from haoai_backend.series_data.media_writes import reconcile_source_media_state
from haoai_backend.series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from haoai_backend.series_data.schemas import ChapterCreate, ChapterUpdate, DeleteFrameRequest
from haoai_backend.series_data.tables import (
    ai_tasks,
    chapters,
    chat_messages,
    metadata as series_metadata,
    series,
    storyboard_assets,
)
from haoai_backend.shared.identity import TrustedActor


BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)
LATER_TIME = datetime(2027, 2, 3, 4, 5, 6)
ACTOR = TrustedActor("user-a")


@pytest.fixture
def source_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'series-source-phases.sqlite'}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    auth_metadata.create_all(engine)
    series_metadata.create_all(engine)
    notes_metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(auth_users), [
            {
                "id": user_id,
                "username": username,
                "email": f"{user_id}@example.test",
                "hashed_password": "test-hash",
                "is_superuser": False,
                "membership_type": "free",
                "created_at": BASE_TIME,
                "password_updated_at": None,
            }
            for user_id, username in (("user-a", "作者A"), ("user-b", "作者B"))
        ])
        connection.execute(insert(series).values(
            id="series-a",
            user_id="user-a",
            name="测试剧集",
            description=None,
            image_url=None,
            style_prompt_id=None,
            team_id=None,
            claimed_by=None,
            claimed_at=None,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        ))
    yield engine
    engine.dispose()


def seed_chapter(
    engine: Engine,
    content: object,
    *,
    asset_rows: tuple[dict[str, Any], ...] = (),
    note_rows: tuple[dict[str, Any], ...] = (),
    media_rows: tuple[dict[str, Any], ...] = (),
) -> None:
    with engine.begin() as connection:
        connection.execute(insert(chapters).values(
            id="chapter-a",
            series_id="series-a",
            title="第一集",
            content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            order=1,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        ))
        if asset_rows:
            connection.execute(insert(storyboard_assets), list(asset_rows))
        if note_rows:
            connection.execute(insert(personal_production_notes), list(note_rows))
        if media_rows:
            connection.execute(insert(storyboard_media_states), list(media_rows))


def asset_row(asset_id: str, *, frame_index: int = 0, image_url: str | None = None) -> dict[str, Any]:
    return {
        "id": asset_id,
        "series_id": "series-a",
        "chapter_id": "chapter-a",
        "frame_index": frame_index,
        "name": f"分镜 {frame_index + 1}",
        "description": "镜头描述",
        "image_url": image_url,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }


def note_row(user_id: str, *, revision: int = 1, frame_notes: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": f"notes-{user_id}",
        "chapter_id": "chapter-a",
        "user_id": user_id,
        "revision": revision,
        "frame_notes": frame_notes or {},
        "resume_frame_id": "asset-a",
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }


def media_row(
    asset_id: str,
    *,
    revision: int = 1,
    valid: bool = True,
    image_url: str | None = None,
    preview_url: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"media-{asset_id}",
        "chapter_id": "chapter-a",
        "storyboard_asset_id": asset_id,
        "media_revision": revision,
        "asset_image_digest": digest_media_identity(normalize_media_identity(image_url)),
        "preview_digest": digest_media_identity(normalize_media_identity(preview_url)),
        "source_valid": valid,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }


def uow_factory(engine: Engine, *, new_ids: tuple[str, ...] = ()) -> Callable[[], SqlAlchemySeriesDataUnitOfWork]:
    ids = iter(new_ids)

    def create() -> SqlAlchemySeriesDataUnitOfWork:
        return SqlAlchemySeriesDataUnitOfWork(
            Session(engine, future=True),
            now=lambda: LATER_TIME,
            new_id=lambda: next(ids),
        )

    return create


def media_changing_content(preview: str, *, text: str = "镜头文本") -> list[dict[str, Any]]:
    return [{"text": text, "storyboard": ["asset-a"], "preview": preview, "unknown_frame": "keep"}]


def test_source_preview_change_seeds_r1_then_revokes_each_approved_user_once(source_store: Engine) -> None:
    engine = source_store
    seed_chapter(
        engine,
        media_changing_content("https://media.example/preview-v1.jpg"),
        asset_rows=(asset_row("asset-a", image_url="https://media.example/source.jpg"),),
        note_rows=(
            note_row("user-a", frame_notes={
                "asset-a": {
                    "status": "approved",
                    "note": "保留A",
                    "approved_media_revision": 1,
                    "needs_reconfirmation": False,
                    "future_field": {"keep": True},
                },
                "unrelated": {"opaque": "keep"},
            }),
            note_row("user-b", frame_notes={
                "asset-a": {
                    "status": "approved",
                    "note": "保留B",
                    "approved_media_revision": 1,
                    "needs_reconfirmation": False,
                },
            }),
        ),
    )

    result = update_chapter(
        uow_factory(engine),
        ACTOR,
        "chapter-a",
        ChapterUpdate(content=media_changing_content("https://media.example/preview-v2.jpg")),
        now=lambda: LATER_TIME,
    )

    assert result["content"][0]["preview"] == "https://media.example/preview-v2.jpg"
    with Session(engine, future=True) as session:
        states = session.execute(select(storyboard_media_states)).mappings().all()
        notes = {
            row["user_id"]: row
            for row in session.execute(select(personal_production_notes)).mappings().all()
        }
        chapter = session.execute(select(chapters).where(chapters.c.id == "chapter-a")).mappings().one()

    assert [(row["storyboard_asset_id"], row["media_revision"], row["source_valid"]) for row in states] == [
        ("asset-a", 2, True)
    ]
    for user_id, note_text in (("user-a", "保留A"), ("user-b", "保留B")):
        assert notes[user_id]["revision"] == 2
        assert notes[user_id]["frame_notes"]["asset-a"] == {
            "status": "unmarked",
            "note": note_text,
            "approved_media_revision": None,
            "needs_reconfirmation": True,
            **({"future_field": {"keep": True}} if user_id == "user-a" else {}),
        }
        assert notes[user_id]["resume_frame_id"] == "asset-a"
    assert notes["user-a"]["frame_notes"]["unrelated"] == {"opaque": "keep"}
    assert json.loads(chapter["content"])[0]["unknown_frame"] == "keep"


def test_text_only_source_change_does_not_advance_media_or_revoke_notes(source_store: Engine) -> None:
    engine = source_store
    content = media_changing_content("https://media.example/preview.jpg", text="原文本")
    seed_chapter(
        engine,
        content,
        asset_rows=(asset_row("asset-a", image_url="https://media.example/source.jpg"),),
        note_rows=(note_row("user-a", frame_notes={
            "asset-a": {
                "status": "approved",
                "note": "维持认可",
                "approved_media_revision": 1,
                "needs_reconfirmation": False,
            },
        }),),
        media_rows=(media_row(
            "asset-a",
            image_url="https://media.example/source.jpg",
            preview_url="https://media.example/preview.jpg",
        ),),
    )
    new_content = media_changing_content("https://media.example/preview.jpg", text="改过的文本")

    update_chapter(
        uow_factory(engine),
        ACTOR,
        "chapter-a",
        ChapterUpdate(content=new_content),
        now=lambda: LATER_TIME,
    )

    with Session(engine, future=True) as session:
        state = session.execute(select(storyboard_media_states)).mappings().one()
        notes = session.execute(select(personal_production_notes)).mappings().one()
        source_asset = session.execute(select(storyboard_assets)).mappings().one()
    assert state["media_revision"] == 1
    assert state["source_valid"] is True
    assert notes["revision"] == 1
    assert notes["frame_notes"]["asset-a"]["status"] == "approved"
    assert source_asset["description"] == "改过的文本"
    assert source_asset["updated_at"] == LATER_TIME


def test_source_update_rolls_back_all_sql_when_note_cas_matches_zero_rows(
    source_store: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = source_store
    old_content = media_changing_content("https://media.example/preview-v1.jpg")
    seed_chapter(
        engine,
        old_content,
        asset_rows=(asset_row("asset-a", image_url="https://media.example/source.jpg"),),
        note_rows=(note_row("user-a", frame_notes={
            "asset-a": {"status": "approved", "note": "留存", "approved_media_revision": 1},
        }),),
    )
    stale_cas_results: list[bool] = []
    actual_update = SqlAlchemyNotesUnitOfWork.update_personal_notes

    def make_revision_stale(
        notes_uow: SqlAlchemyNotesUnitOfWork,
        record_id: str,
        expected_revision: int,
        next_revision: int,
        frame_notes: dict[str, object],
        resume_frame_id: str | None,
    ) -> bool:
        notes_uow._session.execute(
            update(personal_production_notes)
            .where(personal_production_notes.c.id == record_id)
            .values(revision=expected_revision + 1, frame_notes={"winner": "same transaction"})
        )
        changed = actual_update(
            notes_uow, record_id, expected_revision, next_revision, frame_notes, resume_frame_id
        )
        stale_cas_results.append(changed)
        return changed

    monkeypatch.setattr(SqlAlchemyNotesUnitOfWork, "update_personal_notes", make_revision_stale)
    with pytest.raises(ChapterMediaStateError, match="changed concurrently"):
        update_chapter(
            uow_factory(engine),
            ACTOR,
            "chapter-a",
            ChapterUpdate(content=media_changing_content("https://media.example/preview-v2.jpg")),
            now=lambda: LATER_TIME,
        )

    with Session(engine, future=True) as session:
        chapter = session.execute(select(chapters).where(chapters.c.id == "chapter-a")).mappings().one()
        notes = session.execute(select(personal_production_notes)).mappings().one()
        states = session.execute(select(storyboard_media_states)).mappings().all()
    assert stale_cas_results == [False]
    assert json.loads(chapter["content"])[0]["preview"] == "https://media.example/preview-v1.jpg"
    assert notes["revision"] == 1
    assert notes["frame_notes"]["asset-a"]["status"] == "approved"
    assert states == []


class CommitFaultSession(Session):
    def __init__(self, *args: Any, fail_before: int | None = None, fail_after: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fail_before = fail_before
        self.fail_after = fail_after
        self.commit_attempts = 0

    def commit(self) -> None:
        self.commit_attempts += 1
        if self.commit_attempts == self.fail_before:
            raise RuntimeError("injected failure before the database commit")
        super().commit()
        if self.commit_attempts == self.fail_after:
            raise RuntimeError("injected acknowledgement failure after commit")


def faulting_factory(
    engine: Engine,
    captured: list[CommitFaultSession],
    *,
    fail_before: int | None = None,
    fail_after: int | None = None,
) -> Callable[[], SqlAlchemySeriesDataUnitOfWork]:
    def create() -> SqlAlchemySeriesDataUnitOfWork:
        session = CommitFaultSession(bind=engine, future=True, fail_before=fail_before, fail_after=fail_after)
        captured.append(session)
        return SqlAlchemySeriesDataUnitOfWork(session, now=lambda: LATER_TIME)

    return create


@pytest.mark.parametrize(
    ("fault", "durable"),
    [("before", False), ("after", True)],
)
def test_source_commit_failure_is_not_reported_as_success_and_readback_is_authoritative(
    source_store: Engine, fault: str, durable: bool
) -> None:
    engine = source_store
    old_content = media_changing_content("https://media.example/preview-v1.jpg")
    seed_chapter(
        engine,
        old_content,
        asset_rows=(asset_row("asset-a", image_url="https://media.example/source.jpg"),),
        note_rows=(note_row("user-a", frame_notes={
            "asset-a": {"status": "approved", "note": "保留", "approved_media_revision": 1},
        }),),
    )
    sessions: list[CommitFaultSession] = []
    factory = faulting_factory(
        engine,
        sessions,
        fail_before=1 if fault == "before" else None,
        fail_after=1 if fault == "after" else None,
    )

    with pytest.raises(RuntimeError, match="injected"):
        update_chapter(
            factory,
            ACTOR,
            "chapter-a",
            ChapterUpdate(content=media_changing_content("https://media.example/preview-v2.jpg")),
            now=lambda: LATER_TIME,
        )

    assert len(sessions) == 1
    assert sessions[0].commit_attempts == 1
    with Session(engine, future=True) as verifier:
        chapter = verifier.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one()
        media = verifier.execute(select(storyboard_media_states)).mappings().all()
        notes = verifier.execute(select(personal_production_notes)).mappings().one()
    if durable:
        assert json.loads(chapter)[0]["preview"] == "https://media.example/preview-v2.jpg"
        assert [(row["media_revision"], row["source_valid"]) for row in media] == [(2, True)]
        assert notes["revision"] == 2
        assert notes["frame_notes"]["asset-a"]["status"] == "unmarked"
    else:
        assert json.loads(chapter)[0]["preview"] == "https://media.example/preview-v1.jpg"
        assert media == []
        assert notes["revision"] == 1
        assert notes["frame_notes"]["asset-a"]["status"] == "approved"


def test_media_adapter_reads_current_chapter_sql_instead_of_stale_record(source_store: Engine) -> None:
    engine = source_store
    old_content = media_changing_content("https://media.example/stale-preview.jpg")
    current_content = media_changing_content("https://media.example/current-preview.jpg")
    seed_chapter(engine, old_content, asset_rows=(asset_row("asset-a", image_url="https://media.example/source.jpg"),))
    stale = ChapterRecord("chapter-a", "series-a", "第一集", json.dumps(old_content), 1, BASE_TIME, BASE_TIME)
    with engine.begin() as connection:
        connection.execute(
            update(chapters).where(chapters.c.id == "chapter-a").values(
                content=json.dumps(current_content, ensure_ascii=False)
            )
        )

    with Session(engine, future=True) as session:
        reconcile_source_media_state(session, stale, ACTOR)
        session.commit()
    with Session(engine, future=True) as session:
        state = session.execute(select(storyboard_media_states)).mappings().one()

    assert state["media_revision"] == 1
    assert state["preview_digest"] == digest_media_identity(normalize_media_identity("https://media.example/current-preview.jpg"))
    assert state["preview_digest"] != digest_media_identity(normalize_media_identity("https://media.example/stale-preview.jpg"))


def test_sync_phase_can_add_new_asset_and_delete_stale_asset_without_using_mutated_lookup(source_store: Engine) -> None:
    engine = source_store
    seed_chapter(
        engine,
        [{"text": "新镜头", "storyboard": []}],
        asset_rows=(asset_row("stale-asset", frame_index=7, image_url="https://media.example/old.jpg"),),
        note_rows=(note_row("user-a", frame_notes={
            "stale-asset": {
                "status": "approved",
                "note": "旧记录保留",
                "approved_media_revision": 1,
                "future": "keep",
            },
        }),),
        media_rows=(media_row("stale-asset"),),
    )
    factory = uow_factory(engine, new_ids=("new-asset",))
    unit_of_work = factory()
    try:
        chapter = unit_of_work.sync_storyboard_assets("chapter-a", "series-a")
        assert chapter is not None
        unit_of_work.commit()
    finally:
        unit_of_work.close()

    with Session(engine, future=True) as session:
        stored = session.execute(select(chapters.c.content).where(chapters.c.id == "chapter-a")).scalar_one()
        assets = session.execute(select(storyboard_assets)).mappings().all()
        states = {
            row["storyboard_asset_id"]: row
            for row in session.execute(select(storyboard_media_states)).mappings().all()
        }
        notes = session.execute(select(personal_production_notes)).mappings().one()

    assert json.loads(stored)[0]["storyboard"] == ["new-asset"]
    assert [(row["id"], row["frame_index"]) for row in assets] == [("new-asset", 0)]
    assert (states["stale-asset"]["media_revision"], states["stale-asset"]["source_valid"]) == (2, False)
    assert (states["new-asset"]["media_revision"], states["new-asset"]["source_valid"]) == (1, True)
    assert notes["revision"] == 2
    assert notes["frame_notes"]["stale-asset"] == {
        "status": "unmarked",
        "note": "旧记录保留",
        "approved_media_revision": None,
        "needs_reconfirmation": True,
        "future": "keep",
    }
    assert notes["resume_frame_id"] == "asset-a"


def chat_row(message_id: str, frame_index: int | None) -> dict[str, Any]:
    return {
        "id": message_id,
        "chapter_id": "chapter-a",
        "frame_index": frame_index,
        "asset_type": None,
        "asset_id": None,
        "chat_mode": "normal",
        "role": "user",
        "content": f"message-{message_id}",
        "model_name": None,
        "created_at": BASE_TIME,
    }


def test_delete_frame_second_phase_failure_keeps_first_phase_and_orphan_task(source_store: Engine) -> None:
    engine = source_store
    content = [
        {"text": "删除的镜头", "storyboard": ["asset-a"]},
        {"text": "保留的镜头", "storyboard": ["asset-b"]},
    ]
    seed_chapter(
        engine,
        content,
        asset_rows=(asset_row("asset-a", frame_index=0), asset_row("asset-b", frame_index=1)),
        note_rows=(note_row("user-a", frame_notes={
            "asset-a": {"status": "approved", "note": "保留文本", "approved_media_revision": 1},
        }),),
        media_rows=(media_row("asset-a"), media_row("asset-b")),
    )
    with engine.begin() as connection:
        connection.execute(insert(chat_messages), [chat_row("message-a", 0), chat_row("message-b", 1), chat_row("message-null", None)])
        connection.execute(insert(ai_tasks).values(
            id="task-a", message_id="message-a", user_id="user-a",
        ))

    sessions: list[CommitFaultSession] = []
    factory = faulting_factory(engine, sessions, fail_before=2)
    with pytest.raises(RuntimeError, match="before the database commit"):
        delete_frame(factory, ACTOR, "chapter-a", DeleteFrameRequest(frame_index=0))
    assert len(sessions) == 1 and sessions[0].commit_attempts == 2

    with Session(engine, future=True) as session:
        chapter = session.execute(select(chapters).where(chapters.c.id == "chapter-a")).mappings().one()
        messages = session.execute(select(chat_messages).order_by(chat_messages.c.id)).mappings().all()
        tasks = session.execute(select(ai_tasks.c.id, ai_tasks.c.message_id)).all()
        assets = {
            row["id"]: row
            for row in session.execute(select(storyboard_assets)).mappings().all()
        }
        states = {
            row["storyboard_asset_id"]: row
            for row in session.execute(select(storyboard_media_states)).mappings().all()
        }
        notes = session.execute(select(personal_production_notes)).mappings().one()

    assert json.loads(chapter["content"]) == [{"text": "保留的镜头", "storyboard": ["asset-b"]}]
    assert [(row["id"], row["frame_index"]) for row in messages] == [
        ("message-b", 0),
        ("message-null", None),
    ]
    assert tasks == [("task-a", "message-a")]
    assert set(assets) == {"asset-a", "asset-b"}
    assert assets["asset-b"]["frame_index"] == 1
    assert (states["asset-a"]["media_revision"], states["asset-a"]["source_valid"]) == (2, False)
    assert (states["asset-b"]["media_revision"], states["asset-b"]["source_valid"]) == (1, True)
    assert notes["revision"] == 2
    assert notes["frame_notes"]["asset-a"]["status"] == "unmarked"
    assert notes["frame_notes"]["asset-a"]["note"] == "保留文本"


def _phase_snapshot(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    tables = (
        chapters,
        storyboard_assets,
        storyboard_media_states,
        personal_production_notes,
    )
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


def test_zero_row_asset_update_aborts_same_phase_media_and_note_changes(source_store: Engine) -> None:
    engine = source_store
    old_preview = "https://media.example/preview-v1.jpg"
    current_preview = "https://media.example/preview-v2.jpg"
    seed_chapter(
        engine,
        [
            {"text": "已改描述", "storyboard": ["asset-a"], "preview": current_preview},
            {"text": "新增镜头"},
        ],
        asset_rows=(asset_row("asset-a", image_url="https://media.example/source.jpg"),),
        note_rows=(note_row("user-a", frame_notes={
            "asset-a": {
                "status": "approved",
                "note": "保留文本",
                "approved_media_revision": 1,
                "needs_reconfirmation": False,
            },
        }),),
        media_rows=(media_row(
            "asset-a",
            image_url="https://media.example/source.jpg",
            preview_url=old_preview,
        ),),
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER ignore_asset_metadata_update
            BEFORE UPDATE ON storyboard_assets
            WHEN OLD.id = 'asset-a'
            BEGIN
                SELECT RAISE(IGNORE);
            END
            """
        )

    before = _phase_snapshot(engine)
    zero_row_counts: list[int] = []

    def capture_ignored_update(connection, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().lower().startswith("update storyboard_assets"):
            zero_row_counts.append(cursor.rowcount)

    event.listen(engine, "after_cursor_execute", capture_ignored_update)
    session = Session(engine, future=True)
    uow = SqlAlchemySeriesDataUnitOfWork(session, new_id=lambda: "asset-new")
    try:
        with pytest.raises(RuntimeError, match="storyboard asset update expected one row"):
            uow.sync_storyboard_assets("chapter-a", "series-a")
    finally:
        uow.rollback()
        uow.close()
        event.remove(engine, "after_cursor_execute", capture_ignored_update)

    assert zero_row_counts == [0]
    assert _phase_snapshot(engine) == before


def test_sync_defers_matched_asset_metadata_until_chapter_media_coordination(source_store: Engine) -> None:
    engine = source_store
    seed_chapter(
        engine,
        [
            {
                "text": "新描述",
                "storyboard": ["asset-a"],
                "preview": "https://media.example/preview-v2.jpg",
            },
            {"text": "新素材"},
        ],
        asset_rows=(asset_row("asset-a", image_url="https://media.example/source.jpg"),),
        note_rows=(note_row("user-a", frame_notes={
            "asset-a": {
                "status": "approved",
                "note": "保留文本",
                "approved_media_revision": 1,
                "needs_reconfirmation": False,
            },
        }),),
        media_rows=(media_row(
            "asset-a",
            image_url="https://media.example/source.jpg",
            preview_url="https://media.example/preview-v1.jpg",
        ),),
    )
    trace: list[str] = []

    def capture_statement(connection, clauseelement, multiparams, params, execution_options) -> None:
        if getattr(clauseelement, "_for_update_arg", None) is not None:
            froms = getattr(clauseelement, "get_final_froms", lambda: [])()
            compiled = str(clauseelement.compile(dialect=postgresql.dialect())).upper()
            if any(getattr(table, "name", None) == "chapters" for table in froms) and "FOR UPDATE" in compiled:
                trace.append("locked-chapter-read")

    def capture_sql(connection, cursor, statement, parameters, context, executemany) -> None:
        normalized = statement.lstrip().lower()
        if normalized.startswith("update storyboard_assets"):
            trace.append("matched-asset-update")
        elif normalized.startswith("insert into storyboard_assets"):
            trace.append("new-asset-insert")

    event.listen(engine, "before_execute", capture_statement)
    event.listen(engine, "before_cursor_execute", capture_sql)
    session = Session(engine, future=True)
    uow = SqlAlchemySeriesDataUnitOfWork(session, new_id=lambda: "asset-new")
    try:
        chapter = uow.sync_storyboard_assets("chapter-a", "series-a")
        assert chapter is not None
        uow.commit()
    finally:
        uow.close()
        event.remove(engine, "before_execute", capture_statement)
        event.remove(engine, "before_cursor_execute", capture_sql)

    lock_positions = [index for index, item in enumerate(trace) if item == "locked-chapter-read"]
    source_dml_positions = [
        index for index, item in enumerate(trace)
        if item in {"matched-asset-update", "new-asset-insert"}
    ]
    assert source_dml_positions
    assert all(any(lock_position < dml_position for lock_position in lock_positions) for dml_position in source_dml_positions)
    assert trace.index("matched-asset-update") < trace.index("new-asset-insert")
    with Session(engine, future=True) as verifier:
        updated_asset = verifier.execute(
            select(storyboard_assets).where(storyboard_assets.c.id == "asset-a")
        ).mappings().one()
        new_asset = verifier.execute(
            select(storyboard_assets).where(storyboard_assets.c.id == "asset-new")
        ).mappings().one()
        state = verifier.execute(
            select(storyboard_media_states).where(
                storyboard_media_states.c.storyboard_asset_id == "asset-a"
            )
        ).mappings().one()
        note = verifier.execute(
            select(personal_production_notes).where(
                personal_production_notes.c.user_id == "user-a"
            )
        ).mappings().one()
    assert updated_asset["description"] == "新描述"
    assert updated_asset["frame_index"] == 0
    assert new_asset["frame_index"] == 1
    assert state["media_revision"] == 2
    assert note["frame_notes"]["asset-a"]["status"] == "unmarked"


class ObservedCommitSession(Session):
    def __init__(
        self,
        *args: Any,
        observer_engine: Engine,
        snapshots: list[dict[str, Any]],
        chapter_id: str,
        **kwargs: Any,
    ) -> None:
        self._observer_engine = observer_engine
        self._snapshots = snapshots
        self._observed_chapter_id = chapter_id
        super().__init__(*args, **kwargs)

    def commit(self) -> None:
        super().commit()
        with self._observer_engine.connect() as connection:
            content = connection.execute(
                select(chapters.c.content).where(chapters.c.id == self._observed_chapter_id)
            ).scalar_one_or_none()
            asset_ids = list(connection.execute(
                select(storyboard_assets.c.id)
                .where(storyboard_assets.c.chapter_id == self._observed_chapter_id)
                .order_by(storyboard_assets.c.frame_index)
            ).scalars())
            media_ids = list(connection.execute(
                select(storyboard_media_states.c.storyboard_asset_id)
                .where(storyboard_media_states.c.chapter_id == self._observed_chapter_id)
                .order_by(storyboard_media_states.c.storyboard_asset_id)
            ).scalars())
        self._snapshots.append({
            "raw_content": content,
            "content": json.loads(content) if content is not None else None,
            "asset_ids": asset_ids,
            "media_ids": media_ids,
        })


def test_create_chapter_commits_source_before_assets_and_observes_each_insert(source_store: Engine) -> None:
    engine = source_store
    committed: list[dict[str, Any]] = []
    insert_observations: list[dict[str, Any]] = []
    ids = iter(("chapter-created", "asset-created-a", "asset-created-b"))

    def capture_asset_insert(connection, cursor, statement, parameters, context, executemany) -> None:
        if not statement.lstrip().lower().startswith("insert into storyboard_assets"):
            return
        with engine.connect() as observer:
            content = observer.execute(
                select(chapters.c.content).where(chapters.c.id == "chapter-created")
            ).scalar_one()
            persisted_assets = list(observer.execute(
                select(storyboard_assets.c.id)
                .where(storyboard_assets.c.chapter_id == "chapter-created")
                .order_by(storyboard_assets.c.frame_index)
            ).scalars())
        insert_observations.append({
            "raw_content": content,
            "content": json.loads(content),
            "asset_ids": persisted_assets,
        })

    event.listen(engine, "before_cursor_execute", capture_asset_insert)

    def factory() -> SqlAlchemySeriesDataUnitOfWork:
        session = ObservedCommitSession(
            bind=engine,
            future=True,
            observer_engine=engine,
            snapshots=committed,
            chapter_id="chapter-created",
        )
        return SqlAlchemySeriesDataUnitOfWork(session, new_id=lambda: next(ids))

    try:
        result = create_chapter(
            factory,
            ACTOR,
            "series-a",
            ChapterCreate(title="两阶段创建", content=[{"text": "A"}, {"text": "B"}]),
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_asset_insert)

    assert len(committed) == 2
    typed_initial_content = [
        {"text": "A", "character": None, "scene": None, "prop": None},
        {"text": "B", "character": None, "scene": None, "prop": None},
    ]
    assert committed[0]["content"] == typed_initial_content
    assert committed[0]["asset_ids"] == []
    assert committed[0]["media_ids"] == []
    assert committed[1]["content"] == result["content"]
    assert committed[1]["asset_ids"] == ["asset-created-a", "asset-created-b"]
    assert len(insert_observations) == 2
    assert [observation["asset_ids"] for observation in insert_observations] == [[], []]
    assert all(observation["raw_content"] == committed[0]["raw_content"] for observation in insert_observations)
    assert all(observation["content"] == typed_initial_content for observation in insert_observations)


def test_delete_frame_first_phase_commit_failure_rolls_back_every_table(source_store: Engine) -> None:
    engine = source_store
    seed_chapter(
        engine,
        [
            {"text": "删除的镜头", "storyboard": ["asset-a"], "preview": "https://media.example/a.jpg"},
            {"text": "保留的镜头", "storyboard": ["asset-b"], "preview": "https://media.example/b.jpg"},
        ],
        asset_rows=(
            asset_row("asset-a", frame_index=0, image_url="https://media.example/source-a.jpg"),
            asset_row("asset-b", frame_index=1, image_url="https://media.example/source-b.jpg"),
        ),
        note_rows=(note_row("user-a", frame_notes={
            "asset-a": {
                "status": "approved",
                "note": "保留文字",
                "approved_media_revision": 1,
                "needs_reconfirmation": False,
            },
        }),),
        media_rows=(
            media_row("asset-a", image_url="https://media.example/source-a.jpg", preview_url="https://media.example/a.jpg"),
            media_row("asset-b", image_url="https://media.example/source-b.jpg", preview_url="https://media.example/b.jpg"),
        ),
    )
    with engine.begin() as connection:
        connection.execute(insert(chat_messages), [
            chat_row("message-a", 0),
            chat_row("message-b", 1),
            chat_row("message-null", None),
        ])
        connection.execute(insert(ai_tasks).values(
            id="task-a",
            message_id="message-a",
            user_id="user-a",
        ))

    tables = (chapters, chat_messages, ai_tasks, storyboard_assets, storyboard_media_states, personal_production_notes)
    before = _phase_snapshot_for_tables(engine, tables)
    sessions: list[CommitFaultSession] = []
    factory = faulting_factory(engine, sessions, fail_before=1)

    with pytest.raises(RuntimeError, match="before the database commit"):
        delete_frame(factory, ACTOR, "chapter-a", DeleteFrameRequest(frame_index=0))

    assert len(sessions) == 1
    assert sessions[0].commit_attempts == 1
    assert _phase_snapshot_for_tables(engine, tables) == before


def _phase_snapshot_for_tables(engine: Engine, tables: tuple[Any, ...]) -> dict[str, list[dict[str, Any]]]:
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
