from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    insert,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.public_site_config.http import build_public_site_config_router
from haoai_backend.app import create_app
from haoai_backend.public_site_config.persistence import (
    public_site_config_unit_of_work_factory,
)


class TrackingSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def send_request(
    app: FastAPI, method: str, url: str, *, headers: dict[str, str] | None = None
) -> httpx.Response:
    async def perform() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, url, headers=headers)

    return asyncio.run(perform())


def make_database(tmp_path: Path):
    metadata = MetaData()
    configs = Table(
        "system_configs",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("muse_token", String(500)),
        Column("site_title", String(200)),
        Column("site_tagline", String(500)),
        Column("storyboard_cost_multiplier", Integer),
        Column("membership_benefits", Text),
        Column("payments_enabled", Integer),
        Column("tutorial_url", String(500)),
        Column("purchase_qr_code_url", String(500)),
        Column("purchase_contact_info", Text),
        Column("oss_access_key_secret", String(200)),
    )
    packages = Table(
        "purchase_packages",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("category", String(20), nullable=False),
        Column("name", String(100), nullable=False),
        Column("price", Integer, nullable=False),
        Column("credits", Integer),
        Column("days", Integer),
        Column("sort_order", Integer, nullable=False),
        Column("is_active", Boolean, nullable=False),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
    )
    models = Table(
        "system_models",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("category", String(20), nullable=False),
        Column("name", String(255), nullable=False),
        Column("provider", String(50), nullable=False),
        Column("api_key", String(500), nullable=False),
        Column("model", String(255)),
        Column("base_url", String(500)),
        Column("cost_per_call", Integer, nullable=False),
        Column("description", Text),
        Column("is_active", Boolean, nullable=False),
        Column("sort_order", Integer, nullable=False),
        Column("params", Text),
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'public-site-config-http.sqlite'}")
    metadata.create_all(engine)
    base_factory = sessionmaker(
        bind=engine,
        class_=TrackingSession,
        autoflush=True,
        expire_on_commit=False,
    )
    sessions: list[TrackingSession] = []

    def open_session() -> TrackingSession:
        session = base_factory()
        sessions.append(session)
        return session

    return engine, open_session, sessions, configs, packages, models


def test_public_routes_are_anonymous_keep_legacy_shapes_and_hide_model_secrets(
    tmp_path: Path,
) -> None:
    (
        engine,
        session_factory,
        sessions,
        configs,
        packages,
        models,
    ) = make_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            insert(configs).values(
                id="config",
                muse_token="private-config-token",
                site_title="公开标题",
                site_tagline="公开短句",
                storyboard_cost_multiplier=0,
                membership_benefits="<p>原样 HTML</p>",
                payments_enabled=0,
                tutorial_url="",
                purchase_qr_code_url="/buy.png",
                purchase_contact_info="联系方式",
                oss_access_key_secret="private-config-secret",
            )
        )
        connection.execute(
            insert(packages),
            [
                {
                    "id": "credits",
                    "category": "credits",
                    "name": "积分包",
                    "price": 10,
                    "credits": 100,
                    "days": None,
                    "sort_order": 2,
                    "is_active": True,
                },
                {
                    "id": "membership",
                    "category": "membership",
                    "name": "月卡",
                    "price": 30,
                    "credits": None,
                    "days": 30,
                    "sort_order": 1,
                    "is_active": True,
                },
                {
                    "id": "disabled",
                    "category": "credits",
                    "name": "隐藏",
                    "price": 1,
                    "credits": 1,
                    "days": None,
                    "sort_order": 0,
                    "is_active": False,
                },
            ],
        )
        connection.execute(
            insert(models),
            [
                {
                    "id": "video-model",
                    "category": "video",
                    "name": "Video",
                    "provider": "provider-b",
                    "api_key": "secret-key-b",
                    "model": None,
                    "base_url": "https://private-b.invalid",
                    "cost_per_call": 0,
                    "description": None,
                    "is_active": True,
                    "sort_order": 1,
                    "params": "secret-params",
                },
                {
                    "id": "image-model",
                    "category": "image",
                    "name": "Image",
                    "provider": "provider-a",
                    "api_key": "secret-key-a",
                    "model": "image-v1",
                    "base_url": "https://private-a.invalid",
                    "cost_per_call": 8,
                    "description": "图片模型",
                    "is_active": False,
                    "sort_order": 1,
                    "params": "secret-params",
                },
            ],
        )

    app = FastAPI()
    app.include_router(
        build_public_site_config_router(
            uow_factory=public_site_config_unit_of_work_factory(session_factory)
        )
    )
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        config = send_request(app, "GET", "/api/site-config")
        all_packages = send_request(app, "GET", "/api/purchase-packages?category=")
        filtered_packages = send_request(
            app,
            "GET",
            "/api/purchase-packages?category=membership&category=credits",
        )
        whitespace_category = send_request(
            app,
            "GET",
            "/api/purchase-packages?category=%20credits%20",
        )
        system_models = send_request(app, "GET", "/api/system-models")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        engine.dispose()

    assert config.status_code == 200
    assert config.json() == {
        "site_title": "公开标题",
        "site_tagline": "公开短句",
        "storyboard_cost_multiplier": 2,
        "membership_benefits": "<p>原样 HTML</p>",
        "payments_enabled": False,
        "tutorial_url": "",
        "purchase_qr_code_url": "/buy.png",
        "purchase_contact_info": "联系方式",
    }
    assert all_packages.status_code == 200
    assert [item["id"] for item in all_packages.json()] == [
        "membership",
        "credits",
    ]
    assert all_packages.json()[0]["credits"] is None
    assert [item["id"] for item in filtered_packages.json()] == ["credits"]
    assert whitespace_category.json() == []
    assert system_models.status_code == 200
    assert [item["id"] for item in system_models.json()] == [
        "image-model",
        "video-model",
    ]
    assert set(system_models.json()[0]) == {
        "id",
        "category",
        "name",
        "provider",
        "model",
        "cost_per_call",
        "description",
        "is_active",
        "sort_order",
    }
    assert system_models.json()[1]["model"] is None
    assert system_models.json()[1]["description"] == ""
    response_body = repr((config.json(), all_packages.json(), system_models.json()))
    assert all(
        secret not in response_body
        for secret in (
            "private-config-token",
            "private-config-secret",
            "secret-key-a",
            "secret-key-b",
            "private-a.invalid",
            "secret-params",
        )
    )
    assert all(session.close_calls == 1 for session in sessions)
    assert not any(
        statement.lstrip().startswith(("insert ", "update ", "delete "))
        for statement in statements
    )
    model_selects = [
        statement
        for statement in statements
        if statement.startswith("select system_models.")
    ]
    assert len(model_selects) == 1
    assert all(
        secret_column not in model_selects[0]
        for secret_column in ("api_key", "base_url", "params")
    )


def test_missing_service_wiring_returns_503_before_opening_a_session() -> None:
    app = FastAPI()
    app.include_router(build_public_site_config_router(uow_factory=None))

    response = send_request(app, "GET", "/api/site-config")

    assert response.status_code == 503
    assert response.json() == {"detail": "公开网站配置服务尚未接线"}



def test_create_app_mounts_public_routes_without_auth_and_uses_optional_session_wiring(
    tmp_path: Path,
) -> None:
    (
        engine,
        session_factory,
        sessions,
        configs,
        packages,
        models,
    ) = make_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            insert(configs).values(
                id="config",
                site_title="接线标题",
                site_tagline="公开短句",
                storyboard_cost_multiplier=1,
                membership_benefits="[]",
                payments_enabled=1,
                tutorial_url="/help",
                purchase_qr_code_url="/buy.png",
                purchase_contact_info="contact",
                muse_token="private-token",
                oss_access_key_secret="private-secret",
            )
        )
        connection.execute(
            insert(packages).values(
                id="package",
                category="credits",
                name="积分包",
                price=10,
                credits=100,
                days=None,
                sort_order=1,
                is_active=True,
            )
        )
        connection.execute(
            insert(models).values(
                id="model",
                category="image",
                name="图片模型",
                provider="provider",
                api_key="private-api-key",
                model="image-v1",
                base_url="https://private.invalid",
                cost_per_call=5,
                description="公开描述",
                is_active=True,
                sort_order=1,
                params="private-params",
            )
        )

    resolver_calls: list[str] = []

    def unexpected_resolver(request: Any) -> None:
        resolver_calls.append(request.headers.get("authorization", ""))
        raise AssertionError("public site config routes must not resolve an actor")

    app = create_app(
        session_factory=session_factory,
        resolve_actor=unexpected_resolver,
        series_access_policy=lambda session, actor, series_id: None,
    )
    assert sessions == []
    assert len(app.routes) == 217
    route_pairs = [
        (route.path, frozenset(getattr(route, "methods", set())))
        for route in app.routes
    ]
    for path in ("/api/site-config", "/api/purchase-packages", "/api/system-models"):
        assert route_pairs.count((path, frozenset({"GET"}))) == 1

    responses = []
    for headers in (None, {"Authorization": "Bearer invalid-token"}):
        responses.extend(
            [
                send_request(app, "GET", "/api/site-config", headers=headers),
                send_request(app, "GET", "/api/purchase-packages", headers=headers),
                send_request(app, "GET", "/api/system-models", headers=headers),
            ]
        )
    engine.dispose()

    assert [response.status_code for response in responses] == [200] * 6
    assert responses[0].json()["site_title"] == "接线标题"
    assert responses[1].json()[0]["id"] == "package"
    assert responses[2].json()[0]["id"] == "model"
    bodies = repr([response.json() for response in responses])
    assert all(
        secret not in bodies
        for secret in (
            "private-token",
            "private-secret",
            "private-api-key",
            "private.invalid",
            "private-params",
        )
    )
    assert resolver_calls == []
    assert len(sessions) == 6
    assert all(session.close_calls == 1 for session in sessions)

    unwired_app = create_app()
    unwired_response = send_request(unwired_app, "GET", "/api/site-config")
    assert unwired_response.status_code == 503
    assert unwired_response.json() == {"detail": "公开网站配置服务尚未接线"}
