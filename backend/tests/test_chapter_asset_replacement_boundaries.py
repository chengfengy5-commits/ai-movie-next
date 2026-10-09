from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI

from haoai_backend.chapter_asset_replacement.http import (
    build_chapter_asset_replacement_router,
)
from chapter_asset_replacement_support import create_owner_database


BACKEND_ROOT = Path(__file__).parents[1]
SOURCE_ROOT = BACKEND_ROOT / "src" / "haoai_backend" / "chapter_asset_replacement"


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize(
    "module_name",
    ["domain.py", "errors.py", "ports.py", "application.py"],
)
def test_domain_and_application_boundary_does_not_depend_on_http_or_sqlalchemy(
    module_name: str,
) -> None:
    tree = ast.parse((SOURCE_ROOT / module_name).read_text(encoding="utf-8"))
    roots = imported_roots(tree)

    assert "fastapi" not in roots
    assert "sqlalchemy" not in roots
    assert "app" not in roots


def test_media_adapter_delegates_to_shared_notes_coordinator_with_caller_session() -> None:
    tree = ast.parse((SOURCE_ROOT / "media_writes.py").read_text(encoding="utf-8"))
    reconcile = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "reconcile_replacement_media"
    )
    assert [argument.arg for argument in reconcile.args.args] == [
        "session",
        "chapter",
        "actor",
        "content_override",
    ]
    notes_uow_constructors = [
        node
        for node in ast.walk(reconcile)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SqlAlchemyNotesUnitOfWork"
    ]
    assert len(notes_uow_constructors) == 1
    assert isinstance(notes_uow_constructors[0].args[0], ast.Name)
    assert notes_uow_constructors[0].args[0].id == "session"
    calls = [
        node
        for node in ast.walk(reconcile)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "reconcile_chapter_media_state"
    ]
    assert len(calls) == 1
    assert any(
        keyword.arg == "chapter_content_override"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "content_override"
        for keyword in calls[0].keywords
    )


def test_production_modules_define_no_engine_bootstrap_or_external_side_effects() -> None:
    forbidden = (
        "create_engine(",
        ".create_all(",
        "autoload_with=",
        "load_dotenv(",
        'open(".env"',
        "requests.",
        "httpx.",
        "smtplib.",
        "subprocess.",
    )
    for path in sorted(SOURCE_ROOT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not any(marker in source for marker in forbidden), path.name


def test_router_is_one_inert_post_route_with_explicit_injection_ports() -> None:
    router = build_chapter_asset_replacement_router(
        uow_factory=lambda: pytest.fail("router construction must not create a UoW"),
        resolve_actor=lambda _request: pytest.fail("router construction must not resolve identity"),
    )
    app = FastAPI()
    app.include_router(router)
    routes = [
        (route.path, frozenset(getattr(route, "methods", set())))
        for route in app.routes
        if route.path.startswith("/api/chapters/{chapter_id}/replace-asset")
    ]

    assert routes == [
        ("/api/chapters/{chapter_id}/replace-asset", frozenset({"POST"}))
    ]


def test_reusable_owner_database_helper_closes_sessions_connections_and_sqlite_files(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "cleanup" / "owner.sqlite"
    database = create_owner_database(database_path)
    session = database.session_factory()
    session.close()

    with database.physical_connection_pair() as (first, second):
        assert first.connection.dbapi_connection is not second.connection.dbapi_connection

    assert first.closed and second.closed
    database.close()

    assert session.close_calls == 1
    for suffix in ("", "-journal", "-wal", "-shm"):
        assert not Path(f"{database_path}{suffix}").exists()

    database.close()
