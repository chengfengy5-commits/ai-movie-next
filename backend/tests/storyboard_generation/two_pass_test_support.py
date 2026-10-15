"""Reusable isolated JWT/SQLite fixture for paid storyboard two-pass routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_system_models.crypto import LegacyFernet
from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as auth_metadata,
    user_sessions,
    users,
    user_credits,
)
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.generation_catalog.tables import (
    metadata as catalog_metadata,
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.generation_core.base_schema import Base
from haoai_backend.public_site_config.tables import metadata as site_metadata, system_configs
from haoai_backend.series_data.tables import metadata as series_metadata, series, team_members
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.domain import StoryboardRuntimeCall, StoryboardRuntimeResult


TEST_SECRET = "two-pass-synthetic-auth-secret"
API_KEY = "two-pass-synthetic-provider-key"


class FakeTwoPassRuntime:
    """Deterministic chunks for the two documented operation names."""

    def __init__(self) -> None:
        self.calls: list[StoryboardRuntimeCall] = []
        self.scene_chunks = ["开场画面_::~RECORD::~_冲突升级"]
        self.frame_chunks = ["镜头一_::~RECORD::~_镜头二"]

    async def generate(self, call: StoryboardRuntimeCall) -> StoryboardRuntimeResult:
        return StoryboardRuntimeResult("")

    async def stream(self, call: StoryboardRuntimeCall):
        self.calls.append(call)
        chunks = self.scene_chunks if call.operation == "scene_segmentation" else self.frame_chunks
        for chunk in chunks:
            yield chunk


@dataclass(slots=True)
class TwoPassTestSystem:
    engine: Any
    app: Any
    runtime: FakeTwoPassRuntime
    session_factory: Any
    now: datetime

    def token_for(self, user_id: str = "member") -> str:
        codec = JoseTokenCodec(TEST_SECRET)
        return codec.encode(
            {
                "sub": user_id,
                "jti": f"jti-{user_id}",
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            }
        )


def build_two_pass_system(database_path: Path, *, multiplier: int | None = None) -> TwoPassTestSystem:
    engine = create_engine(f"sqlite:///{database_path}", future=True)
    auth_metadata.create_all(engine)
    catalog_metadata.create_all(
        engine,
        tables=[model_configs, system_models, prompt_configs, system_prompts],
    )
    series_metadata.create_all(engine, tables=[series, team_members])
    site_metadata.create_all(engine, tables=[system_configs])
    Base.metadata.create_all(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {
                    "id": "member",
                    "username": "member",
                    "email": "member@example.test",
                    "hashed_password": "synthetic-test-hash",
                    "is_superuser": False,
                    "membership_type": "premium",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": now,
                    "password_updated_at": now,
                },
                {
                    "id": "free",
                    "username": "free",
                    "email": "free@example.test",
                    "hashed_password": "synthetic-test-hash",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": now,
                    "password_updated_at": now,
                },
            ],
        )
        connection.execute(
            insert(user_sessions),
            [
                {
                    "id": f"session-{user_id}",
                    "user_id": user_id,
                    "jti": f"jti-{user_id}",
                    "user_agent": "two-pass-test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": now,
                    "last_seen_at": now,
                    "revoked_at": None,
                }
                for user_id in ("member", "free")
            ],
        )
        connection.execute(
            insert(user_credits),
            [
                {
                    "id": f"credit-{user_id}",
                    "user_id": user_id,
                    "credits": 50 if user_id == "member" else 0,
                    "created_at": now,
                    "updated_at": now,
                }
                for user_id in ("member", "free")
            ],
        )
        connection.execute(
            insert(series).values(
                id="series-member",
                user_id="member",
                name="Synthetic series",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(system_models).values(
                id="system-text-model",
                category="text",
                name="Synthetic text model",
                provider="openai-compatible",
                api_key=API_KEY,
                model="synthetic-wire-model",
                base_url="https://provider.example.test/v1",
                cost_per_call=3,
                description=None,
                params=None,
                is_active=True,
                sort_order=0,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(model_configs).values(
                id="owned-text-model",
                user_id="member",
                category="reasoning",
                name="Owned reasoning model",
                provider="openai-compatible",
                api_key="owned-test-key",
                model="owned-wire-model",
                base_url="https://provider.example.test/v1",
                sort_order=1,
                params=None,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(system_prompts),
            [
                {
                    "id": "scene-prompt",
                    "category": "scene-segmentation",
                    "name": "Scene prompt",
                    "system_prompt": "Scene system",
                    "user_prompt": "Split {content}",
                    "is_active": True,
                    "sort_order": 0,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "frame-prompt",
                    "category": "storyboard",
                    "name": "Frame prompt",
                    "system_prompt": "Frame system",
                    "user_prompt": "Draw each segment",
                    "is_active": True,
                    "sort_order": 0,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        if multiplier is not None:
            connection.execute(
                insert(system_configs).values(
                    id="site-config",
                    site_title="Synthetic",
                    site_tagline="",
                    storyboard_cost_multiplier=multiplier,
                    membership_benefits="",
                    payments_enabled=1,
                    tutorial_url="",
                    purchase_qr_code_url="",
                    purchase_contact_info="",
                )
            )

    maker = sessionmaker(engine, expire_on_commit=False, future=True)
    auth_maker = sessionmaker(engine, expire_on_commit=False, future=True)
    runtime = FakeTwoPassRuntime()
    auth_runtime = AuthenticationRuntime(
        session_factory=auth_maker,
        secret_key=TEST_SECRET,
        clock=lambda: now,
    )
    app = create_app(
        session_factory=maker,
        authentication_runtime=auth_runtime,
        storyboard_generation_runtime=runtime,
    )
    return TwoPassTestSystem(engine, app, runtime, maker, now)


__all__ = (
    "API_KEY",
    "FakeTwoPassRuntime",
    "TwoPassTestSystem",
    "build_two_pass_system",
)
