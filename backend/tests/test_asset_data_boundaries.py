from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from haoai_backend.app import create_app
from haoai_backend.shared.identity import TrustedActor


ASSET_ROUTES = {
    ("/api/series/{series_id}/characters", "GET"),
    ("/api/series/{series_id}/characters", "POST"),
    ("/api/characters/{character_id}", "PUT"),
    ("/api/characters/{character_id}", "DELETE"),
    ("/api/series/{series_id}/scenes", "GET"),
    ("/api/series/{series_id}/scenes", "POST"),
    ("/api/scenes/{scene_id}", "PUT"),
    ("/api/scenes/{scene_id}", "DELETE"),
    ("/api/series/{series_id}/props", "GET"),
    ("/api/series/{series_id}/props", "POST"),
    ("/api/props/{prop_id}", "PUT"),
    ("/api/props/{prop_id}", "DELETE"),
    ("/api/storyboard-assets", "POST"),
    ("/api/storyboard-assets/{asset_id}", "PUT"),
    ("/api/storyboard-assets/{asset_id}", "DELETE"),
}


def test_factory_has_exact_asset_route_surface_with_canvas_methods() -> None:
    app = create_app(
        session_factory=lambda: None,
        resolve_actor=lambda request: TrustedActor("test-user"),
        series_access_policy=lambda session, actor, series_id: None,
    )
    method_paths = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    assert len(method_paths) == 57
    assert ASSET_ROUTES <= method_paths
    assert app.openapi_url is None
    assert app.docs_url is None
    assert app.redoc_url is None


def test_cold_import_and_factory_do_not_start_database_or_read_dotenv(tmp_path: Path) -> None:
    script = """
import builtins
import os
from pathlib import Path
import sqlalchemy
from sqlalchemy import MetaData
from sqlalchemy.orm import Session
import fastapi
import pydantic

calls = []
def forbidden(*args, **kwargs):
    calls.append((args, kwargs))
    raise AssertionError('unexpected startup database side effect')

Session.__init__ = forbidden
sqlalchemy.create_engine = forbidden
MetaData.create_all = forbidden
real_open = builtins.open
def guarded_open(file, *args, **kwargs):
    if Path(file).name == '.env':
        raise AssertionError('dotenv file access during import')
    return real_open(file, *args, **kwargs)
builtins.open = guarded_open
real_path_open = Path.open
def guarded_path_open(self, *args, **kwargs):
    if self.name == '.env':
        raise AssertionError('dotenv path access during import')
    return real_path_open(self, *args, **kwargs)
Path.open = guarded_path_open

from haoai_backend.app import create_app
from haoai_backend.shared.identity import TrustedActor
app = create_app(
    session_factory=lambda: Session(),
    resolve_actor=lambda request: TrustedActor('test-user'),
    series_access_policy=lambda session, actor, series_id: None,
)
assert len({(route.path, method) for route in app.routes for method in getattr(route, 'methods', set())}) == 57
assert calls == []
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == ""
