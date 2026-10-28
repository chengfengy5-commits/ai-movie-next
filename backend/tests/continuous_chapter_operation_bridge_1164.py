"""One-shot synthetic ASGI bridge for the TypeScript-to-Python digest contract."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from haoai_backend.personal_production.rough_cut.tables import metadata as rough_cut_metadata
from haoai_backend.series_data.continuous_operation_schema import apply_continuous_operation_migration
from haoai_backend.series_data.tables import chapters, metadata as series_metadata, series
from haoai_backend.shared.identity import TrustedActor


def _read_input() -> tuple[str, str]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict) or not isinstance(value.get("body_json"), str) or not isinstance(value.get("operation_id"), str):
        raise ValueError("bridge input must contain body_json and operation_id strings")
    return value["body_json"], value["operation_id"]


async def _send(app: Any, operation_id: str, body_json: str) -> tuple[int, dict[str, Any]]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://continuous-operation-bridge.test",
        headers={"x-test-user": "user-a", "content-type": "application/json"},
    ) as client:
        response = await client.post(
            f"/api/chapters/chapter-a/continuous-edit/operations/{operation_id}",
            content=body_json.encode("utf-8"),
        )
    return response.status_code, response.json()


def main() -> None:
    body_json, operation_id = _read_input()
    original = [{
        "text": "打开时原文",
        "original_text": "打开时原文",
        "character": [],
        "scene": [],
        "prop": [],
        "storyboard": [],
        "unknown": {"ratio": 1e-7, "label": "雪だるま🧊", "nested": {"keep": True}},
    }]
    with tempfile.TemporaryDirectory(prefix="haoai-continuous-bridge-1164-") as temporary_directory:
        database_path = Path(temporary_directory) / "synthetic.sqlite"
        engine = create_engine(f"sqlite:///{database_path}", future=True)
        try:
            auth_metadata.create_all(engine)
            series_metadata.create_all(engine)
            notes_metadata.create_all(engine)
            rough_cut_metadata.create_all(engine)
            now = datetime(2026, 1, 1)
            with engine.begin() as connection:
                connection.execute(insert(auth_users).values(
                    id="user-a", username="作者", email="author@example.test", hashed_password="synthetic",
                    is_superuser=False, membership_type="free", created_at=now, password_updated_at=None,
                ))
                connection.execute(insert(series).values(
                    id="series-a", user_id="user-a", name="合成跨语言测试", description=None,
                    image_url=None, style_prompt_id=None, team_id=None, claimed_by=None,
                    claimed_at=None, created_at=now, updated_at=now,
                ))
                connection.execute(insert(chapters).values(
                    id="chapter-a", series_id="series-a", title="第一章",
                    content=json.dumps(original, ensure_ascii=False), order=1,
                    created_at=now, updated_at=now,
                ))
            apply_continuous_operation_migration(engine)
            sessions = sessionmaker(engine, expire_on_commit=False, future=True)

            def resolve_actor(request: Any) -> TrustedActor:
                if request.headers.get("x-test-user") != "user-a":
                    raise AuthenticationError(401, "synthetic auth failure")
                return TrustedActor(user_id="user-a")

            app = create_app(session_factory=sessions, resolve_actor=resolve_actor)
            status, payload = asyncio.run(_send(app, operation_id, body_json))
            print(json.dumps({"bridge_pid": os.getpid(), "http_status": status, "payload": payload}, ensure_ascii=False, separators=(",", ":")))
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
