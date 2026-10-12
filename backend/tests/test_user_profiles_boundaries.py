"""Import and identity boundaries for the user-profile module."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from haoai_backend.user_profiles.authentication import (
    UserProfileActor,
    authenticate_user_profile_actor,
)
from haoai_backend.user_profiles.tables import (
    characters,
    props,
    scenes,
    series,
    storyboard_assets,
    team_members,
    teams,
)


def test_profile_actor_is_copied_from_public_authenticated_context_and_is_frozen() -> None:
    events: list[object] = []

    class FakeAuthenticationService:
        @contextmanager
        def authenticated(self, authorization: str, *, require_membership: bool):
            events.append(("enter", authorization, require_membership))
            try:
                yield SimpleNamespace(
                    user=SimpleNamespace(
                        id="user-1",
                        username="trusted-name",
                        email="trusted@example.test",
                        bio="auth snapshot",
                        is_superuser=True,
                    )
                )
            finally:
                events.append("close")

    actor = authenticate_user_profile_actor(
        FakeAuthenticationService(), "Bearer trusted-token"
    )

    assert events == [
        ("enter", "Bearer trusted-token", True),
        "close",
    ]
    assert actor == UserProfileActor(
        "user-1",
        "trusted-name",
        "trusted@example.test",
        "auth snapshot",
    )
    assert not hasattr(actor, "is_superuser")
    with pytest.raises(FrozenInstanceError):
        actor.bio = "mutated"


def test_module_imports_stay_inside_the_profile_and_public_auth_boundaries() -> None:
    package = Path(__file__).parents[1] / "src" / "haoai_backend" / "user_profiles"
    source_files = sorted(package.glob("*.py"))
    assert len(source_files) == 10

    imported: set[str] = set()
    source = ""
    for path in source_files:
        contents = path.read_text()
        source += contents
        tree = ast.parse(contents, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not any(name == "app" or name.startswith("app.") for name in imported)
    assert not any(
        name.split(".", 1)[0] in {"aiohttp", "boto3", "httpx", "requests", "urllib"}
        for name in imported
    )
    assert "TrustedActor" not in source
    assert "claimed_by" not in source
    assert "create_engine(" not in source
    assert ".create_all(" not in source
    assert "os.environ" not in source


def test_projection_contains_only_the_four_legacy_asset_tables() -> None:
    assert {
        table.name
        for table in (
            characters,
            scenes,
            props,
            storyboard_assets,
        )
    } == {"characters", "scenes", "props", "storyboard_assets"}
    assert teams.name == "teams"
    assert team_members.name == "team_members"
    assert series.name == "series"
