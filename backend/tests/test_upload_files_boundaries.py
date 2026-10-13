"""Import and construction boundaries for upload-file composition."""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi import Request

from haoai_backend.upload_files.domain import UploadFileActor
from haoai_backend.upload_files.http import build_upload_files_router
from haoai_backend.upload_files.local_files import LocalApprovedFileReader
from haoai_backend.upload_files.persistence import SqlAlchemyUploadDirectoryProvider


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "haoai_backend" / "upload_files"
FORBIDDEN_IMPORTS = {
    "app.config",
    "app.database",
    "app.models",
    "app.services.deploy_config",
    "haoai_backend.admin_system_config.tables",
}


def test_upload_files_modules_do_not_import_runtime_or_private_orm_globals() -> None:
    imported: set[str] = set()
    for source_path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)

    assert imported.isdisjoint(FORBIDDEN_IMPORTS)


def test_router_and_providers_are_inert_until_a_request(tmp_path: Path) -> None:
    events: list[str] = []

    def session_factory():
        events.append("session")
        raise AssertionError("construction must not open a session")

    def resolve_actor(_request: Request) -> UploadFileActor:
        events.append("auth")
        return UploadFileActor(user_id="member")

    provider = SqlAlchemyUploadDirectoryProvider(session_factory, tmp_path / "uploads")
    router = build_upload_files_router(
        directory_provider=provider,
        file_reader=LocalApprovedFileReader(),
        resolve_active_actor=resolve_actor,
    )

    assert [route.path for route in router.routes] == ["/uploads/{file_path:path}"]
    assert events == []
