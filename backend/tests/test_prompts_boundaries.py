from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.domain import UserRecord
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.prompts.authentication import create_prompt_identity_resolvers
from haoai_backend.prompts.domain import PromptActor
from haoai_backend.prompts.errors import PromptNotFound
from haoai_backend.prompts.http import build_prompts_router
from haoai_backend.prompts.persistence import (
    SqlAlchemyPromptsUnitOfWork,
    prompts_unit_of_work_factory,
)
from haoai_backend.prompts.schemas import PromptConfigUpdate
from haoai_backend.prompts.tables import metadata as prompts_metadata, users as prompt_users


class TokenCodec:
    def decode(self, token: str) -> dict[str, str]:
        return {"sub": token}


class AuthRuntime:
    secret_key = "test-secret"
    clock = staticmethod(lambda: datetime(2026, 10, 13, 9, 30))

    def get_token_codec(self) -> TokenCodec:
        return TokenCodec()


class AuthUnitOfWork:
    def __init__(self, user: UserRecord, events: list[str]) -> None:
        self.user = user
        self.events = events

    def load_user_by_id(self, user_id: str) -> UserRecord | None:
        return self.user if user_id == self.user.id else None

    def rollback(self) -> None:
        self.events.append("auth-rollback")

    def close(self) -> None:
        self.events.append("auth-close")


def user_record(user_id: str, *, admin: bool, membership: str) -> UserRecord:
    return UserRecord(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.test",
        hashed_password="unused",
        is_superuser=admin,
        membership_type=membership,
        membership_expires_at=None,
        avatar_url=None,
        bio=None,
        created_at=datetime(2026, 1, 1),
        password_updated_at=None,
    )


def test_prompt_actor_is_immutable_and_schema_keeps_explicit_none() -> None:
    actor = PromptActor("user-a", False)
    with pytest.raises(FrozenInstanceError):
        actor.user_id = "user-b"  # type: ignore[misc]

    assert PromptConfigUpdate.model_validate({"system_prompt": None}).model_dump(
        exclude_unset=True
    ) == {"system_prompt": None}
    assert PromptConfigUpdate.model_validate({}).model_dump(exclude_unset=True) == {}


def test_factory_and_router_construction_do_not_open_a_business_session() -> None:
    opened: list[str] = []

    def session_factory():
        opened.append("opened")
        raise AssertionError("factory opened a session eagerly")

    factory = prompts_unit_of_work_factory(session_factory)
    router = build_prompts_router(
        uow_factory=factory,
        resolve_active_actor=lambda _request: PromptActor("user-a", False),
        resolve_administrator=lambda _request: PromptActor("user-a", True),
    )

    assert router.routes
    assert opened == []


def test_auth_context_closes_before_business_session_and_admin_is_account_only(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    users_by_id = {
        "premium": user_record("premium", admin=False, membership="premium"),
        "free-admin": user_record("free-admin", admin=True, membership="free"),
        "free-user": user_record("free-user", admin=False, membership="free"),
    }

    def auth_factory() -> AuthUnitOfWork:
        # The token is selected by the request and made available via this temporary holder.
        user_id = current_user[0]
        events.append("auth-open")
        return AuthUnitOfWork(users_by_id[user_id], events)

    current_user = ["premium"]
    service = AuthenticationService(AuthRuntime(), auth_factory)
    identities = create_prompt_identity_resolvers(service)

    engine = create_engine(f"sqlite:///{tmp_path / 'prompts-boundary.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    prompts_metadata.create_all(engine)
    session_factory = sessionmaker(engine, autoflush=False, expire_on_commit=False, future=True)
    with engine.begin() as connection:
        connection.execute(insert(prompt_users), [{"id": "premium"}, {"id": "free-admin"}])

    def select_user(request) -> str:
        authorization = request.headers.get("authorization", "")
        return authorization.removeprefix("Bearer ")

    def resolve_active(request) -> PromptActor:
        current_user[0] = select_user(request)
        return identities.active(request.headers.get("authorization", ""))

    def resolve_admin(request) -> PromptActor:
        current_user[0] = select_user(request)
        return identities.administrator(request.headers.get("authorization", ""))

    def business_factory():
        events.append("business-open")
        unit_of_work = SqlAlchemyPromptsUnitOfWork(session_factory())
        original_close = unit_of_work.close

        def close() -> None:
            original_close()
            events.append("business-close")

        unit_of_work.close = close  # type: ignore[method-assign]
        return unit_of_work

    app = FastAPI()
    app.include_router(
        build_prompts_router(
            uow_factory=business_factory,
            resolve_active_actor=resolve_active,
            resolve_administrator=resolve_admin,
        )
    )

    def send(path: str, token: str):
        async def request():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://prompts-boundary.test",
            ) as client:
                return await client.get(path, headers={"authorization": f"Bearer {token}"})

        return asyncio.run(request())

    def send_admin(token: str):
        async def request():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://prompts-boundary.test",
            ) as client:
                return await client.post(
                    "/api/prompts/not-found/promote-to-system",
                    headers={"authorization": f"Bearer {token}"},
                )

        return asyncio.run(request())

    try:
        response = send("/api/prompts", "premium")
        assert response.status_code == 200
        assert events == ["auth-open", "auth-close", "business-open", "business-close"]

        events.clear()
        admin_response = send_admin("free-admin")
        assert admin_response.status_code == 404
        assert events == ["auth-open", "auth-close", "business-open", "business-close"]

        events.clear()
        denied = send("/api/prompts", "free-user")
        assert denied.status_code == 403
        assert events == ["auth-open", "auth-rollback", "auth-close"]

        events.clear()
        denied_admin = send_admin("free-user")
        assert denied_admin.status_code == 403
        assert events == ["auth-open", "auth-rollback", "auth-close"]
    finally:
        engine.dispose()
