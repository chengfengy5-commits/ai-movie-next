from __future__ import annotations

import ast
import asyncio
import inspect
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy import event

from haoai_backend.shared.identity import TrustedActor
import haoai_backend.teams.reporting.application as reporting_application
import haoai_backend.teams.reporting.ports as reporting_ports
from haoai_backend.teams.reporting.http import build_reporting_router
from haoai_backend.teams.reporting.persistence import SqlAlchemyReportingUnitOfWork
from haoai_backend.teams.reporting.usage import UNKNOWN_MODEL

from test_teams_reporting_persistence import (
    CONSERVATION_TABLES,
    RecordingUnitOfWorkFactory,
    ReportingDatabase,
    reporting_database,
)


def _build_app(factory: Any, user_id: str) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_reporting_router(
            uow_factory=factory,
            resolve_actor=lambda _request: TrustedActor(user_id),
        )
    )
    return app


def _record_non_query_statements(
    database: ReportingDatabase,
) -> tuple[list[str], list[str], Any]:
    statements: list[str] = []
    model_type_queries: list[str] = []

    def before_execute(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        sql = statement.lstrip().upper()
        if sql.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
            statements.append(statement)
        if (
            sql.startswith("SELECT DISTINCT")
            and "AI_TASKS.MODEL_NAME" in sql
            and "AI_TASKS.TYPE" in sql
        ):
            model_type_queries.append(statement)

    event.listen(database.database.engine, "before_cursor_execute", before_execute)
    return statements, model_type_queries, before_execute


def test_application_consumes_only_the_framework_free_reporting_port() -> None:
    tree = ast.parse(inspect.getsource(reporting_application))
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")

    assert not any("persistence" in name for name in imported_modules)
    assert not any("sqlalchemy" in name.lower() for name in imported_modules)
    assert "haoai_backend.teams.ports" not in imported_modules
    port_tree = ast.parse(inspect.getsource(reporting_ports))
    port_imports = [
        node.module or ""
        for node in ast.walk(port_tree)
        if isinstance(node, ast.ImportFrom)
    ]
    assert not any("teams.ports" in name for name in port_imports)
    assert not any("sqlalchemy" in name.lower() for name in port_imports)
    protocol_methods = {
        node.name for node in ast.walk(port_tree) if isinstance(node, ast.FunctionDef)
    }
    assert {
        "load_team",
        "load_membership",
        "list_member_tasks",
        "load_export_model_types",
        "ensure_clean",
        "rollback",
        "close",
    } <= protocol_methods
    forbidden_attributes = {
        "commit",
        "execute_immediate",
        "flush",
        "session",
        "stage_assignment",
        "stage_delete",
        "stage_insert",
    }
    assert not {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } & forbidden_attributes
    assert list(inspect.signature(build_reporting_router).parameters) == [
        "uow_factory",
        "resolve_actor",
    ]

    class FakeReportingReader:
        def __init__(self) -> None:
            self.events: list[Any] = []

        def ensure_clean(self) -> None:
            self.events.append("ensure_clean")

        def load_membership(self, team_id: str, user_id: str) -> Any:
            self.events.append(("load_membership", team_id, user_id))
            return SimpleNamespace(role="owner", permissions=None)

        def load_team(self, team_id: str) -> Any:
            self.events.append(("load_team", team_id))
            return SimpleNamespace(id=team_id)

        def list_team_series(self, team_id: str, *, newest_first: bool) -> list[Any]:
            self.events.append(("list_team_series", team_id, newest_first))
            return []

        def rollback(self) -> None:
            self.events.append("rollback")

        def close(self) -> None:
            self.events.append("close")

    reader = FakeReportingReader()
    factory_calls = 0

    def factory() -> FakeReportingReader:
        nonlocal factory_calls
        factory_calls += 1
        return reader

    result = reporting_application.team_usage_export(
        factory,
        TrustedActor("owner"),
        "team",
    )

    assert result == {
        "rows": [],
        "members": [],
        "total_calls": 0,
        "total_credits": 0,
        "total_failed": 0,
    }
    assert factory_calls == 1
    assert reader.events == [
        "ensure_clean",
        ("load_membership", "team", "owner"),
        ("load_membership", "team", "owner"),
        ("load_team", "team"),
        ("list_team_series", "team", False),
        "close",
    ]
    assert not any(
        hasattr(reader, name)
        for name in ("session", "execute_immediate", "commit", "stage_insert")
    )


def test_reporting_http_routes_are_read_only_and_keep_source_outputs(
    reporting_database,
) -> None:
    bundle: ReportingDatabase = reporting_database
    database, ids = bundle.database, bundle.ids
    snapshot_before = database.snapshot()
    assert all(snapshot_before[name] for name in CONSERVATION_TABLES)
    assert not any(
        row["user_id"] == ids.empty_target_user_id
        for row in snapshot_before["user_credits"]
    )
    assert not any(
        row["user_id"] == ids.empty_target_user_id
        for row in snapshot_before["ai_tasks"]
    )
    write_statements, model_type_queries, write_listener = (
        _record_non_query_statements(bundle)
    )
    factory = RecordingUnitOfWorkFactory(database)
    reported_export: dict[str, Any] = {}
    reported_date_export: dict[str, Any] = {}

    async def exercise() -> None:
        owner_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=_build_app(factory, ids.owner_user_id)
            ),
            base_url="http://reporting.test",
        )
        member_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=_build_app(factory, ids.target_user_id)
            ),
            base_url="http://reporting.test",
        )
        stranger_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=_build_app(factory, ids.stranger_user_id)
            ),
            base_url="http://reporting.test",
        )
        async with owner_client, member_client, stranger_client:
            task_response = await owner_client.get(
                f"/api/teams/{ids.team_id}/members/{ids.target_user_id}/tasks",
                params={"page": 1, "page_size": 50},
            )
            assert task_response.status_code == 200
            task_payload = task_response.json()
            assert task_payload["total"] == 12
            assert len(task_payload["tasks"]) == 12
            assert any(
                item["message_id"] == ids.private_message_id
                and item["chapter_title"] == "Personal chapter"
                for item in task_payload["tasks"]
            )
            assert task_payload["credits"] == 8123
            payload_by_id = {item["id"]: item for item in task_payload["tasks"]}
            assert any(
                item["type"] == "chat"
                and item["chapter_id"] is None
                and item["message_id"] not in {ids.message_id, ids.outside_message_id}
                for item in payload_by_id.values()
            )
            main_message_task = next(
                item
                for item in payload_by_id.values()
                if item["type"] == "video"
                and item["status"] == "completed"
                and item["result"] == "ok"
            )
            assert main_message_task["asset_name"] == "Mira"
            assert main_message_task["frame_index"] == 3
            assert main_message_task["chapter_id"] is None
            batch = next(
                item
                for item in payload_by_id.values()
                if item["type"] == "batch-optimize"
            )
            assert batch["chapter_id"] == ids.team_chapter_id
            assert batch["chapter_title"] == "Team chapter"
            assert batch["frame_count"] == 7
            review = next(item for item in payload_by_id.values() if item["type"] == "ai-review")
            assert review["frame_index"] == 3
            assert review["prompt_id"] == "review-prompt"

            zero_page = await owner_client.get(
                f"/api/teams/{ids.team_id}/members/{ids.target_user_id}/tasks",
                params={"page": 1, "page_size": 0},
            )
            assert zero_page.status_code == 200
            assert zero_page.json()["total"] == 12
            assert zero_page.json()["tasks"] == []
            assert zero_page.json()["page_size"] == 0

            empty_tasks = await owner_client.get(
                f"/api/teams/{ids.team_id}/members/{ids.empty_target_user_id}/tasks"
            )
            assert empty_tasks.status_code == 200
            assert empty_tasks.json() == {
                "total": 0,
                "tasks": [],
                "page": 1,
                "page_size": 10,
                "credits": 0,
            }

            usage_response = await owner_client.get(
                f"/api/teams/{ids.team_id}/usage",
                params={
                    "start_time": "2026-10-09T10:00:00Z",
                    "end_time": "2026-10-09T12:00:00Z",
                },
            )
            assert usage_response.status_code == 200
            usage = usage_response.json()
            assert (
                usage["total_calls"],
                usage["total_credits"],
                usage["total_failed"],
            ) == (6, 14, 2)
            assert [item["series_id"] for item in usage["items"]] == [
                ids.team_series_id
            ]
            team_members = {
                member["user_id"]: member
                for member in usage["items"][0]["by_member"]
            }
            assert team_members[ids.target_user_id]["chapter_count"] == 1
            assert team_members[ids.target_user_id]["calls"] == 5
            assert team_members[ids.target_user_id]["credits"] == 12
            assert team_members[ids.target_user_id]["failed_calls"] == 1
            assert team_members[ids.owner_user_id]["chapter_count"] == 1

            invalid_date = await owner_client.get(
                f"/api/teams/{ids.team_id}/usage",
                params={
                    "start_time": "invalid",
                    "end_time": "2026-10-09T10:00:00Z",
                },
            )
            assert invalid_date.status_code == 200
            assert invalid_date.json()["total_calls"] == 1
            assert invalid_date.json()["total_credits"] == 8

            reversed_range = await owner_client.get(
                f"/api/teams/{ids.team_id}/usage",
                params={
                    "start_time": "2026-10-09T13:00:00",
                    "end_time": "2026-10-09T11:00:00",
                },
            )
            assert reversed_range.status_code == 200
            assert reversed_range.json()["items"] == []
            assert reversed_range.json()["total_calls"] == 0

            by_model = {
                item["model_name"]: item for item in usage["items"][0]["by_model"]
            }
            assert by_model[UNKNOWN_MODEL]["calls"] == 3
            assert by_model[UNKNOWN_MODEL]["credits"] == 7
            assert by_model["a"]["credits"] == -3
            assert by_model["a"]["failed_calls"] == 1

            series_response = await owner_client.get(
                f"/api/teams/{ids.team_id}/series/{ids.team_series_id}/usage"
            )
            assert series_response.status_code == 200
            series_usage = series_response.json()
            assert series_usage["series_total"]["calls"] == 6
            assert series_usage["series_total"]["credits"] == 14
            assert series_usage["series_total"]["failed_calls"] == 2
            assert [chapter["chapter_id"] for chapter in series_usage["chapters"]] == [
                ids.team_chapter_id
            ]

            empty_series_response = await owner_client.get(
                f"/api/teams/{ids.empty_series_team_id}/series/{ids.empty_series_id}/usage"
            )
            assert empty_series_response.status_code == 200
            assert empty_series_response.json() == {
                "series_id": ids.empty_series_id,
                "series_name": "No usage series",
                "series_total": {
                    "calls": 0,
                    "credits": 0,
                    "failed_calls": 0,
                    "by_model": [],
                    "by_member": [],
                },
                "chapters": [],
            }

            empty_team_usage = await owner_client.get(
                f"/api/teams/{ids.empty_series_team_id}/usage"
            )
            assert empty_team_usage.status_code == 200
            assert empty_team_usage.json() == {
                "items": [],
                "total_calls": 0,
                "total_credits": 0,
                "total_failed": 0,
            }

            model_response = await owner_client.get(
                f"/api/teams/{ids.team_id}/usage/model",
                params={"model_name": "A"},
            )
            assert model_response.status_code == 200
            model_usage = model_response.json()
            assert (
                model_usage["total_calls"],
                model_usage["total_credits"],
                model_usage["failed_calls"],
            ) == (2, 10, 0)
            assert model_usage["by_series"][0]["calls"] == 2

            lower_model_response = await owner_client.get(
                f"/api/teams/{ids.team_id}/usage/model",
                params={"model_name": "a"},
            )
            assert lower_model_response.status_code == 200
            assert (
                lower_model_response.json()["total_calls"],
                lower_model_response.json()["total_credits"],
                lower_model_response.json()["failed_calls"],
            ) == (1, -3, 1)

            empty_model_response = await owner_client.get(
                f"/api/teams/{ids.team_id}/usage/model?model_name="
            )
            assert empty_model_response.status_code == 200
            assert empty_model_response.json()["total_calls"] == 1
            assert empty_model_response.json()["total_credits"] == 2

            unmatched_model = await owner_client.get(
                f"/api/teams/{ids.team_id}/usage/model",
                params={"model_name": "no-matching-model"},
            )
            assert unmatched_model.status_code == 200
            assert unmatched_model.json() == {
                "model_name": "no-matching-model",
                "total_calls": 0,
                "total_credits": 0,
                "failed_calls": 0,
                "by_series": [],
                "by_member": [],
            }

            export_response = await owner_client.get(
                f"/api/teams/{ids.team_id}/usage/export"
            )
            assert export_response.status_code == 200
            export = export_response.json()
            assert export["total_calls"] == 6
            assert export["total_credits"] == 14
            assert export["total_failed"] == 2
            assert [member["username"] for member in export["members"]] == [
                "report-target",
                "report-owner",
            ]
            assert next(
                member
                for member in export["members"]
                if member["user_id"] == ids.target_user_id
            )["models"] == ["A", "a", UNKNOWN_MODEL]
            assert export["rows"] == sorted(
                export["rows"],
                key=lambda row: (
                    row["series_name"],
                    row["chapter_title"],
                    row["username"],
                    row["model_name"],
                ),
            )
            assert "model_order" in export
            reported_export["value"] = export

            dated_export_response = await owner_client.get(
                f"/api/teams/{ids.team_id}/usage/export",
                params={
                    "start_time": "2026-10-09T10:00:00Z",
                    "end_time": "2026-10-09T12:00:00Z",
                },
            )
            assert dated_export_response.status_code == 200
            dated_export = dated_export_response.json()
            assert dated_export["total_calls"] == 6
            assert dated_export["total_credits"] == 14
            assert dated_export["total_failed"] == 2
            assert "model_order" in dated_export
            reported_date_export["value"] = dated_export

            empty_export_response = await owner_client.get(
                f"/api/teams/{ids.empty_team_id}/usage/export"
            )
            assert empty_export_response.status_code == 200
            empty_export = empty_export_response.json()
            assert empty_export == {
                "rows": [],
                "members": [],
                "total_calls": 0,
                "total_credits": 0,
                "total_failed": 0,
            }
            assert "model_order" not in empty_export

            empty_series_export_response = await owner_client.get(
                f"/api/teams/{ids.empty_series_team_id}/usage/export",
                params={
                    "start_time": "2026-10-09T10:00:00Z",
                    "end_time": "2026-10-09T12:00:00Z",
                },
            )
            assert empty_series_export_response.status_code == 200
            assert empty_series_export_response.json() == {
                "rows": [],
                "members": [],
                "model_order": [],
                "total_calls": 0,
                "total_credits": 0,
                "total_failed": 0,
            }

            denied_usage = await member_client.get(
                f"/api/teams/{ids.team_id}/usage"
            )
            assert denied_usage.status_code == 403
            assert denied_usage.json()["detail"] == "没有执行该操作的权限"

            missing_member = await owner_client.get(
                f"/api/teams/{ids.team_id}/members/{ids.stranger_user_id}/tasks"
            )
            assert missing_member.status_code == 404
            assert missing_member.json()["detail"] == "该成员不在团队中"

            missing_series = await owner_client.get(
                f"/api/teams/{ids.team_id}/series/{ids.stranger_user_id}/usage"
            )
            assert missing_series.status_code == 404
            assert missing_series.json()["detail"] == "剧集不存在或不属于该团队"

            missing_team = await stranger_client.get(
                "/api/teams/team-that-does-not-exist/usage"
            )
            assert missing_team.status_code == 403
            assert missing_team.json()["detail"] == "你不是该团队成员"

    try:
        asyncio.run(exercise())

        # Verify the real DISTINCT order and derive the expected result from its
        # first row, independently of the production ordering helper.
        assert len(model_type_queries) == 2
        for statement in model_type_queries:
            sql = " ".join(statement.upper().split())
            assert "SELECT DISTINCT" in sql
            assert "AI_TASKS.MODEL_NAME" in sql and "AI_TASKS.TYPE" in sql
            assert "ORDER BY" not in sql
            assert not any(
                column in sql for column in ("CREATED_AT", "STATUS", "TEAM_ID", "SERIES_ID")
            )

        unit = factory()
        try:
            reader = SqlAlchemyReportingUnitOfWork(unit)
            start = datetime(2026, 10, 9, 10)
            end = datetime(2026, 10, 9, 12)
            raw = reader.load_export_usage_rows([ids.team_series_id], start, end)
            model_names = {row[3] or UNKNOWN_MODEL for row in raw}
            type_pairs = reader.load_export_model_types(list(model_names))
            assert {row[0] for row in type_pairs} == model_names
            assert None not in {row[0] for row in type_pairs}
            assert "" not in {row[0] for row in type_pairs}

            first_type_by_name: dict[str, str] = {}
            last_type_by_name: dict[str, str] = {}
            for model_name, model_type in type_pairs:
                first_type_by_name.setdefault(model_name, model_type or "")
                last_type_by_name[model_name] = model_type or ""

            type_priority = {
                "video": 0,
                "image": 1,
                "chat": 2,
                "optimize-frame": 3,
            }

            def expected_order(type_by_name: dict[str, str]) -> list[str]:
                return sorted(
                    model_names,
                    key=lambda name: (
                        type_priority.get(type_by_name.get(name, ""), 9),
                        name.lower(),
                    ),
                )

            expected_first = expected_order(first_type_by_name)
            expected_last = expected_order(last_type_by_name)
            assert expected_first != expected_last
            assert reported_date_export["value"]["model_order"] == expected_first

            pivot_types = [
                model_type for name, model_type in type_pairs if name == "0-pivot"
            ]
            assert len(pivot_types) == 2 and pivot_types[0] != pivot_types[-1]
            assert set(pivot_types) == {"chat", "video"}
            tie_input = [name for name in model_names if name.lower() == "a"]
            assert [name for name in expected_first if name.lower() == "a"] == tie_input

            task_by_id = {row["id"]: row for row in snapshot_before["ai_tasks"]}
            outside_pivot = task_by_id[ids.competition_outside_task_id]
            reportable_pivot = task_by_id[ids.competition_reportable_task_id]
            assert (
                outside_pivot["type"],
                outside_pivot["status"],
                outside_pivot["created_at"],
            ) == ("chat", "processing", datetime(2025, 1, 1))
            assert (
                reportable_pivot["type"],
                reportable_pivot["status"],
                reportable_pivot["created_at"],
            ) == ("video", "failed", datetime(2026, 10, 9, 11, 45))
            assert outside_pivot["model_name"] == reportable_pivot["model_name"] == "0-pivot"
        finally:
            unit.close()
    finally:
        event.remove(database.engine, "before_cursor_execute", write_listener)

    snapshot_after = database.snapshot()
    assert snapshot_after == snapshot_before
    assert write_statements == []
    assert factory.units
    assert all(unit.commit_calls == 0 for unit in factory.units)
    assert all(unit.close_calls == 1 for unit in factory.units)
    assert database.engine.pool.checkedout() == 0


def test_reporting_validation_is_local_first_message_and_read_free(
    reporting_database,
) -> None:
    bundle: ReportingDatabase = reporting_database
    database, ids = bundle.database, bundle.ids
    snapshot_before = database.snapshot()
    assert all(snapshot_before[name] for name in CONSERVATION_TABLES)
    write_statements, _model_type_queries, write_listener = (
        _record_non_query_statements(bundle)
    )
    factory = RecordingUnitOfWorkFactory(database)
    app = FastAPI()
    reporting_router = build_reporting_router(
        uow_factory=factory,
        resolve_actor=lambda _request: TrustedActor(ids.owner_user_id),
    )

    def raise_prefixed_validation_error() -> None:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "first"),
                    "msg": "Value error, expected first value",
                    "input": "first",
                },
                {
                    "type": "value_error",
                    "loc": ("query", "second"),
                    "msg": "Value error, ignored second value",
                    "input": "second",
                },
            ]
        )

    reporting_router.add_api_route(
        "/__test__/validation-prefix",
        raise_prefixed_validation_error,
        methods=["GET"],
    )
    app.include_router(reporting_router)

    @app.get("/__test__/ordinary-validation")
    def ordinary_validation(value: int) -> dict[str, int]:
        return {"value": value}

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://reporting.test",
        ) as client:
            invalid_page = await client.get(
                f"/api/teams/{ids.team_id}/members/{ids.target_user_id}/tasks",
                params={"page": "not-a-page"},
            )
            invalid_page_size = await client.get(
                f"/api/teams/{ids.team_id}/members/{ids.target_user_id}/tasks",
                params={"page_size": "not-a-page-size"},
            )
            invalid_both = await client.get(
                f"/api/teams/{ids.team_id}/members/{ids.target_user_id}/tasks",
                params={"page": "not-a-page", "page_size": "not-a-page-size"},
            )
            missing_model_name = await client.get(
                f"/api/teams/{ids.team_id}/usage/model"
            )
            prefixed = await client.get("/api/__test__/validation-prefix")
            ordinary = await client.get(
                "/__test__/ordinary-validation?value=not-an-int"
            )

            integer_message = (
                "Input should be a valid integer, unable to parse string as an integer"
            )
            expected_integer_error = {"detail": integer_message}
            assert invalid_page.status_code == 422
            assert invalid_page.json() == expected_integer_error
            assert invalid_page_size.status_code == 422
            assert invalid_page_size.json() == expected_integer_error
            assert invalid_both.status_code == 422
            assert invalid_both.json() == expected_integer_error
            assert missing_model_name.status_code == 422
            assert missing_model_name.json() == {"detail": "Field required"}
            assert prefixed.status_code == 422
            assert prefixed.json() == {"detail": "expected first value"}
            assert ordinary.status_code == 422
            assert isinstance(ordinary.json()["detail"], list)
            assert ordinary.json()["detail"][0]["msg"] == integer_message
            assert factory.units == []
            assert write_statements == []

            empty_model = await client.get(
                f"/api/teams/{ids.team_id}/usage/model",
                params={"model_name": ""},
            )
            assert empty_model.status_code == 200
            assert (
                empty_model.json()["total_calls"],
                empty_model.json()["total_credits"],
            ) == (1, 2)

    try:
        asyncio.run(exercise())
    finally:
        event.remove(database.engine, "before_cursor_execute", write_listener)

    assert len(factory.units) == 1
    assert all(unit.commit_calls == 0 for unit in factory.units)
    assert all(unit.close_calls == 1 for unit in factory.units)
    assert write_statements == []
    assert database.snapshot() == snapshot_before
    assert all(snapshot_before[name] for name in CONSERVATION_TABLES)
    assert database.engine.pool.checkedout() == 0


def test_unwired_reporting_router_returns_503_without_opening_a_session(
    reporting_database,
) -> None:
    bundle: ReportingDatabase = reporting_database
    app = FastAPI()
    app.include_router(
        build_reporting_router(
            uow_factory=None,
            resolve_actor=lambda _request: TrustedActor(bundle.ids.owner_user_id),
        )
    )

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://reporting.test",
        ) as client:
            return await client.get(f"/api/teams/{bundle.ids.team_id}/usage")

    response = asyncio.run(request())

    assert response.status_code == 503
    assert response.json()["detail"] == "团队服务尚未接线"
    assert bundle.database.engine.pool.checkedout() == 0
