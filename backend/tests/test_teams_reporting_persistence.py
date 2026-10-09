from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import event, insert

from chapter_asset_replacement_support import (
    CONSERVATION_TABLES,
    OWNER_TABLES,
    OwnerDatabase,
    create_owner_database,
)
from haoai_backend.teams.persistence import TeamSqlAlchemyUnitOfWork
from haoai_backend.teams.reporting.persistence import (
    SqlAlchemyReportingUnitOfWork,
    adapt_reporting_uow_factory,
)
from haoai_backend.teams.reporting.usage import UNKNOWN_MODEL, order_export_models


NOW = datetime(2026, 10, 9, 12)


@dataclass(frozen=True, slots=True)
class ReportingIds:
    owner_user_id: str
    target_user_id: str
    empty_target_user_id: str
    stranger_user_id: str
    team_id: str
    empty_team_id: str
    empty_series_team_id: str
    outside_team_id: str
    team_series_id: str
    empty_series_id: str
    outside_series_id: str
    private_series_id: str
    team_chapter_id: str
    empty_chapter_id: str
    outside_chapter_id: str
    private_chapter_id: str
    character_id: str
    scene_id: str
    prop_id: str
    storyboard_id: str
    message_id: str
    outside_message_id: str
    private_message_id: str
    competition_reportable_task_id: str
    competition_outside_task_id: str
    task_ids: tuple[str, ...]


@dataclass(slots=True)
class ReportingDatabase:
    database: OwnerDatabase
    ids: ReportingIds


class RecordingReportingUnitOfWork(TeamSqlAlchemyUnitOfWork):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()

    def close(self) -> None:
        self.close_calls += 1
        super().close()


@dataclass(slots=True)
class RecordingUnitOfWorkFactory:
    database: OwnerDatabase
    units: list[RecordingReportingUnitOfWork] = field(default_factory=list)

    def __call__(self) -> RecordingReportingUnitOfWork:
        unit = RecordingReportingUnitOfWork(self.database.session_factory())
        self.units.append(unit)
        return unit


def _id() -> str:
    return str(uuid4())


def _insert_rows(connection: Any, table_name: str, rows: list[dict[str, Any]]) -> None:
    connection.execute(insert(OWNER_TABLES[table_name]), rows)


def _task(
    task_id: str,
    user_id: str,
    message_id: str,
    *,
    task_type: str,
    status: str,
    credit_cost: int,
    model_name: str | None,
    created_at: datetime,
    result: str | None = None,
    request_data: str | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "user_id": user_id,
        "type": task_type,
        "message_id": message_id,
        "status": status,
        "credit_cost": credit_cost,
        "result": result,
        "request_data": request_data,
        "model_name": model_name,
        "progress": 35,
        "progress_message": "processing",
        "external_task_id": None,
        "external_provider": None,
        "claimed_by": "reporting-test-worker",
        "lease_until": NOW,
        "execution_generation": 2,
        "claim_token": "reporting-test-token",
        "recovery_status": "ready",
        "billing_status": "unbilled",
        "user_cancelled_at": None,
        "cancellation_reason": None,
        "created_at": created_at,
        "updated_at": created_at,
    }


def seed_reporting_rows(database: OwnerDatabase) -> ReportingIds:
    owner_user_id, target_user_id, empty_target_user_id, stranger_user_id = (
        _id(),
        _id(),
        _id(),
        _id(),
    )
    team_id, empty_team_id, empty_series_team_id, outside_team_id = (
        _id(),
        _id(),
        _id(),
        _id(),
    )
    team_series_id, empty_series_id, outside_series_id, private_series_id = (
        _id(),
        _id(),
        _id(),
        _id(),
    )
    team_chapter_id, empty_chapter_id, outside_chapter_id, private_chapter_id = (
        _id(),
        _id(),
        _id(),
        _id(),
    )
    character_id, scene_id, prop_id, storyboard_id = _id(), _id(), _id(), _id()
    message_id, outside_message_id, private_message_id = _id(), _id(), _id()

    with database.engine.begin() as connection:
        _insert_rows(
            connection,
            "users",
            [
                {
                    "id": owner_user_id,
                    "username": "report-owner",
                    "email": "123456789@QQ.COM",
                    "hashed_password": "test-hash",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW,
                    "password_updated_at": NOW,
                },
                {
                    "id": target_user_id,
                    "username": "report-target",
                    "email": "target@example.com",
                    "hashed_password": "test-hash",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW,
                    "password_updated_at": NOW,
                },
                {
                    "id": empty_target_user_id,
                    "username": "report-empty-target",
                    "email": "empty-target@example.com",
                    "hashed_password": "test-hash",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW,
                    "password_updated_at": NOW,
                },
                {
                    "id": stranger_user_id,
                    "username": "report-stranger",
                    "email": "stranger@example.com",
                    "hashed_password": "test-hash",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": NOW,
                    "password_updated_at": NOW,
                },
            ],
        )
        _insert_rows(
            connection,
            "teams",
            [
                {
                    "id": team_id,
                    "name": "Reporting team",
                    "owner_id": owner_user_id,
                    "created_at": NOW,
                },
                {
                    "id": empty_team_id,
                    "name": "Empty reporting team",
                    "owner_id": owner_user_id,
                    "created_at": NOW,
                },
                {
                    "id": empty_series_team_id,
                    "name": "No usage series team",
                    "owner_id": owner_user_id,
                    "created_at": NOW,
                },
                {
                    "id": outside_team_id,
                    "name": "Outside team",
                    "owner_id": stranger_user_id,
                    "created_at": NOW,
                },
            ],
        )
        _insert_rows(
            connection,
            "team_members",
            [
                {
                    "id": _id(),
                    "team_id": team_id,
                    "user_id": owner_user_id,
                    "role": "owner",
                    "permissions": None,
                    "joined_at": NOW,
                },
                {
                    "id": _id(),
                    "team_id": team_id,
                    "user_id": target_user_id,
                    "role": "member",
                    "permissions": '["view_tasks"]',
                    "joined_at": NOW,
                },
                {
                    "id": _id(),
                    "team_id": empty_team_id,
                    "user_id": owner_user_id,
                    "role": "owner",
                    "permissions": None,
                    "joined_at": NOW,
                },
                {
                    "id": _id(),
                    "team_id": team_id,
                    "user_id": empty_target_user_id,
                    "role": "member",
                    "permissions": '["view_tasks"]',
                    "joined_at": NOW,
                },
                {
                    "id": _id(),
                    "team_id": empty_series_team_id,
                    "user_id": owner_user_id,
                    "role": "owner",
                    "permissions": None,
                    "joined_at": NOW,
                },
            ],
        )
        _insert_rows(
            connection,
            "series",
            [
                {
                    "id": team_series_id,
                    "user_id": owner_user_id,
                    "name": "Team series",
                    "description": "Reporting fixture",
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": team_id,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": NOW,
                    "updated_at": datetime(2026, 10, 8),
                },
                {
                    "id": empty_series_id,
                    "user_id": owner_user_id,
                    "name": "No usage series",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": empty_series_team_id,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": outside_series_id,
                    "user_id": target_user_id,
                    "name": "Other team series",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": outside_team_id,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": private_series_id,
                    "user_id": target_user_id,
                    "name": "Personal series",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": None,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        _insert_rows(
            connection,
            "chapters",
            [
                {
                    "id": team_chapter_id,
                    "series_id": team_series_id,
                    "title": "Team chapter",
                    "content": "Preserved chapter body",
                    "order": 1,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": empty_chapter_id,
                    "series_id": team_series_id,
                    "title": "No usage chapter",
                    "content": "No task rows",
                    "order": 2,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": outside_chapter_id,
                    "series_id": outside_series_id,
                    "title": "Outside chapter",
                    "content": None,
                    "order": 1,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": private_chapter_id,
                    "series_id": private_series_id,
                    "title": "Personal chapter",
                    "content": None,
                    "order": 1,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        _insert_rows(
            connection,
            "characters",
            [
                {
                    "id": character_id,
                    "series_id": team_series_id,
                    "name": "Mira",
                    "gender": None,
                    "age": None,
                    "role": None,
                    "appearance": None,
                    "description": None,
                    "image_url": None,
                    "audio_url": None,
                    "voice_ref": None,
                    "aliases": None,
                    "canonical_key": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
            ],
        )
        _insert_rows(
            connection,
            "scenes",
            [
                {
                    "id": scene_id,
                    "series_id": team_series_id,
                    "title": "Station",
                    "description": None,
                    "image_url": None,
                    "aliases": None,
                    "canonical_key": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
            ],
        )
        _insert_rows(
            connection,
            "props",
            [
                {
                    "id": prop_id,
                    "series_id": team_series_id,
                    "name": "Key",
                    "description": None,
                    "image_url": None,
                    "aliases": None,
                    "canonical_key": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
            ],
        )
        _insert_rows(
            connection,
            "storyboard_assets",
            [
                {
                    "id": storyboard_id,
                    "series_id": team_series_id,
                    "chapter_id": team_chapter_id,
                    "frame_index": 2,
                    "name": "Frame three",
                    "description": None,
                    "image_url": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
            ],
        )
        _insert_rows(
            connection,
            "chat_messages",
            [
                {
                    "id": message_id,
                    "chapter_id": team_chapter_id,
                    "frame_index": 2,
                    "asset_type": "character",
                    "asset_id": character_id,
                    "chat_mode": "chat",
                    "role": "assistant",
                    "content": "Fixture response",
                    "model_name": "A",
                    "created_at": NOW,
                },
                {
                    "id": outside_message_id,
                    "chapter_id": outside_chapter_id,
                    "frame_index": None,
                    "asset_type": None,
                    "asset_id": None,
                    "chat_mode": "chat",
                    "role": "assistant",
                    "content": "Outside response",
                    "model_name": "outside",
                    "created_at": NOW,
                },
                {
                    "id": private_message_id,
                    "chapter_id": private_chapter_id,
                    "frame_index": None,
                    "asset_type": None,
                    "asset_id": None,
                    "chat_mode": "chat",
                    "role": "assistant",
                    "content": "Personal response",
                    "model_name": "personal",
                    "created_at": NOW,
                },
            ],
        )

        task_rows: list[dict[str, Any]] = []
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                message_id,
                task_type="video",
                status="completed",
                credit_cost=8,
                model_name="A",
                created_at=datetime(2026, 10, 9, 10),
                result="ok",
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                message_id,
                task_type="video",
                status="completed",
                credit_cost=-3,
                model_name="a",
                created_at=datetime(2026, 10, 9, 11),
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                message_id,
                task_type="video",
                status="failed",
                credit_cost=900,
                model_name="a",
                created_at=datetime(2026, 10, 9, 12),
                result="failure detail",
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                message_id,
                task_type="image",
                status="completed",
                credit_cost=1,
                model_name=None,
                created_at=datetime(2026, 10, 9, 11, 30),
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                message_id,
                task_type="video",
                status="completed",
                credit_cost=2,
                model_name="",
                created_at=datetime(2026, 10, 9, 11, 15),
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                message_id,
                task_type="chat",
                status="completed",
                credit_cost=4,
                model_name=UNKNOWN_MODEL,
                created_at=datetime(2026, 10, 9, 10, 30),
            )
        )
        task_rows.append(
            _task(
                _id(),
                owner_user_id,
                message_id,
                task_type="video",
                status="completed",
                credit_cost=2,
                model_name="A",
                created_at=datetime(2026, 10, 9, 11, 5),
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                outside_message_id,
                task_type="image",
                status="completed",
                credit_cost=20,
                model_name=UNKNOWN_MODEL,
                created_at=datetime(2026, 10, 9, 11),
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                _id(),
                task_type="chat",
                status="completed",
                credit_cost=50,
                model_name="Orphan model",
                created_at=datetime(2026, 10, 9, 10, 50),
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                private_message_id,
                task_type="image",
                status="completed",
                credit_cost=13,
                model_name="Personal model",
                created_at=datetime(2026, 10, 9, 9, 55),
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                message_id,
                task_type="chat",
                status="processing",
                credit_cost=70,
                model_name="Processing model",
                created_at=datetime(2026, 10, 9, 10, 40),
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                _id(),
                task_type="batch-optimize",
                status="completed",
                credit_cost=11,
                model_name="Batch model",
                created_at=datetime(2026, 10, 9, 10, 20),
                request_data='{"chapter_id": "' + team_chapter_id + '", "frame_count": 7}',
            )
        )
        task_rows.append(
            _task(
                _id(),
                target_user_id,
                _id(),
                task_type="ai-review",
                status="failed",
                credit_cost=123,
                model_name="Review model",
                created_at=datetime(2026, 10, 9, 10, 10),
                request_data=(
                    '{"chapter_id": "' + team_chapter_id
                    + '", "frame_index": "2", "prompt_id": "review-prompt"}'
                ),
            )
        )
        competition_reportable_task_id = _id()
        task_rows.append(
            _task(
                competition_reportable_task_id,
                owner_user_id,
                message_id,
                task_type="video",
                status="failed",
                credit_cost=0,
                model_name="0-pivot",
                created_at=datetime(2026, 10, 9, 11, 45),
            )
        )
        competition_outside_task_id = _id()
        # This is the sole chat type for 0-pivot. It lies outside the report
        # window, outside this team, and in a non-reportable status.
        task_rows.append(
            _task(
                competition_outside_task_id,
                stranger_user_id,
                outside_message_id,
                task_type="chat",
                status="processing",
                credit_cost=0,
                model_name="0-pivot",
                created_at=datetime(2025, 1, 1),
            )
        )

        # The global type lookup intentionally sees this outside-series,
        # non-reportable task. It has the same raw name with a second type.
        task_rows.append(
            _task(
                _id(),
                stranger_user_id,
                outside_message_id,
                task_type="image",
                status="processing",
                credit_cost=0,
                model_name=UNKNOWN_MODEL,
                created_at=datetime(2025, 1, 1),
            )
        )
        _insert_rows(connection, "ai_tasks", task_rows)
        _insert_rows(
            connection,
            "user_credits",
            [
                {
                    "id": _id(),
                    "user_id": target_user_id,
                    "credits": 8123,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
            ],
        )

    return ReportingIds(
        owner_user_id=owner_user_id,
        target_user_id=target_user_id,
        empty_target_user_id=empty_target_user_id,
        stranger_user_id=stranger_user_id,
        team_id=team_id,
        empty_team_id=empty_team_id,
        empty_series_team_id=empty_series_team_id,
        outside_team_id=outside_team_id,
        team_series_id=team_series_id,
        empty_series_id=empty_series_id,
        outside_series_id=outside_series_id,
        private_series_id=private_series_id,
        team_chapter_id=team_chapter_id,
        empty_chapter_id=empty_chapter_id,
        outside_chapter_id=outside_chapter_id,
        private_chapter_id=private_chapter_id,
        character_id=character_id,
        scene_id=scene_id,
        prop_id=prop_id,
        storyboard_id=storyboard_id,
        message_id=message_id,
        outside_message_id=outside_message_id,
        private_message_id=private_message_id,
        competition_reportable_task_id=competition_reportable_task_id,
        competition_outside_task_id=competition_outside_task_id,
        task_ids=tuple(row["id"] for row in task_rows),
    )


@pytest.fixture
def reporting_database(tmp_path: Path):
    database = create_owner_database(tmp_path / "reporting-owner.sqlite", seed=True)
    try:
        database.assert_foreign_keys_enabled()
        yield ReportingDatabase(database, seed_reporting_rows(database))
    finally:
        database.close()


def test_full_owner_reporting_reads_preserve_nonempty_history(reporting_database) -> None:
    bundle = reporting_database
    database, ids = bundle.database, bundle.ids
    snapshot_before = database.snapshot()

    required_nonempty = (
        "ai_tasks",
        "credit_logs",
        "task_quotes",
        "task_submissions",
        "billing_units",
        "execution_steps",
        "external_submissions",
        "result_evidence",
        "storyboard_media_states",
        "personal_production_notes",
        "rough_cut_drafts",
    )
    assert all(snapshot_before[name] for name in required_nonempty)
    assert all(len(snapshot_before[name]) >= 1 for name in CONSERVATION_TABLES)

    factory = RecordingUnitOfWorkFactory(database)
    unit = factory()
    try:
        reader = SqlAlchemyReportingUnitOfWork(unit)

        target_tasks, total = reader.list_member_tasks(
            ids.target_user_id,
            page=1,
            page_size=100,
        )
        assert total == 12
        assert len(target_tasks) == 12
        assert any(task["message_id"] == ids.private_message_id for task in target_tasks)
        excluded_messages = {
            ids.message_id,
            ids.outside_message_id,
            ids.private_message_id,
        }
        assert any(
            task["message_id"] not in excluded_messages for task in target_tasks
        )
        assert any(
            task["message_id"] not in {ids.message_id, ids.outside_message_id}
            for task in target_tasks
        )
        assert reader.load_user_credit(ids.target_user_id) == 8123

        team_series = reader.list_team_series(ids.team_id, newest_first=True)
        assert [row["id"] for row in team_series] == [ids.team_series_id]
        assert reader.load_team_series(ids.team_id, ids.team_series_id)["id"] == ids.team_series_id
        assert [
            row["id"]
            for row in reader.list_series_chapters(ids.team_series_id)
        ] == [ids.empty_chapter_id, ids.team_chapter_id]

        rows = reader.load_team_usage_rows(
            [ids.team_series_id],
            datetime(2026, 10, 9, 10),
            datetime(2026, 10, 9, 12),
        )
        assert sum(row[4] for row in rows) == 8
        assert sum(row[5] for row in rows) == 914
        assert sum(row[4] for row in reader.load_series_usage_rows(
            ids.team_series_id, None, None
        )) == 8
        assert reader.load_model_usage_rows(
            [ids.team_series_id], "", None, None
        )[0][3] == 1

        raw = reader.load_export_usage_rows([ids.team_series_id], None, None)
        assert len(raw) == 8
        model_names = {row[3] or UNKNOWN_MODEL for row in raw}
        assert model_names == {"A", "a", "0-pivot", UNKNOWN_MODEL}
        type_pairs = reader.load_export_model_types(list(model_names))
        assert {row[0] for row in type_pairs} == model_names
        assert None not in {row[0] for row in type_pairs}
        assert "" not in {row[0] for row in type_pairs}
        type_by_name: dict[str, str] = {}
        for model_name, model_type in type_pairs:
            type_by_name.setdefault(model_name, model_type or "")
        expected_order = order_export_models(model_names, type_by_name)
        expected_input = list(model_names)
        assert [name for name in expected_order if name.lower() == "a"] == [
            name for name in expected_input if name.lower() == "a"
        ]

        messages = reader.load_messages([ids.message_id, ids.outside_message_id])
        assert set(messages) == {ids.message_id, ids.outside_message_id}
        assert reader.load_chapter_titles([ids.team_chapter_id]) == {
            ids.team_chapter_id: "Team chapter"
        }
        assert reader.load_asset_names("character", [ids.character_id]) == {
            ids.character_id: "Mira"
        }
        assert reader.load_asset_names("scene", [_id()]) == {}
    finally:
        unit.close()

    snapshot_after = database.snapshot()
    assert snapshot_after == snapshot_before
    assert factory.units[0].commit_calls == 0
    assert factory.units[0].close_calls == 1
    assert database.engine.pool.checkedout() == 0



def test_adapter_factory_returns_only_the_reporting_read_surface(
    reporting_database,
) -> None:
    bundle: ReportingDatabase = reporting_database
    database, ids = bundle.database, bundle.ids
    snapshot_before = database.snapshot()
    shared_factory = RecordingUnitOfWorkFactory(database)
    reporting_factory = adapt_reporting_uow_factory(shared_factory)

    reader = reporting_factory()
    assert reader.load_team(ids.team_id).id == ids.team_id
    assert reader.load_membership(ids.team_id, ids.owner_user_id).role == "owner"
    assert not any(
        hasattr(reader, name)
        for name in (
            "session",
            "execute_immediate",
            "commit",
            "flush",
            "stage_insert",
            "stage_assignment",
            "stage_delete",
        )
    )
    reader.close()

    assert len(shared_factory.units) == 1
    assert shared_factory.units[0].commit_calls == 0
    assert shared_factory.units[0].close_calls == 1
    assert database.snapshot() == snapshot_before
    assert database.engine.pool.checkedout() == 0
