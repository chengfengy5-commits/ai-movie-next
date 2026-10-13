"""End-to-end coverage for app-level redemption and upload composition."""

from __future__ import annotations

import asyncio
import inspect
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute
from jose import jwt
from sqlalchemy import create_engine, event, insert, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    users,
)
from haoai_backend.redemption_codes.persistence import (
    Base as RedemptionBase,
    CreditLogRow,
    RedemptionCodeRow,
    UserCreditRow,
    UserRow,
    billing_units_reference,
)
from haoai_backend.upload_files.persistence import (
    metadata as upload_files_metadata,
    system_configs,
)

SECRET = "redemption-upload-composition-test-secret"
BUSINESS_NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.info["events"].append(
            (self.info["kind"], "open", id(self), threading.get_ident())
        )

    def commit(self) -> None:
        self.info["events"].append(
            (self.info["kind"], "commit", id(self), threading.get_ident())
        )
        super().commit()

    def rollback(self) -> None:
        self.info["events"].append(
            (self.info["kind"], "rollback", id(self), threading.get_ident())
        )
        super().rollback()

    def close(self) -> None:
        self.info["events"].append(
            (self.info["kind"], "close", id(self), threading.get_ident())
        )
        super().close()


def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://composition.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def _assert_authentication_closed_before_business(
    events: list[tuple[str, str, int, int]],
) -> None:
    authentication_close = next(
        index
        for index, (kind, action, _session_id, _thread_id) in enumerate(events)
        if kind == "authentication" and action == "close"
    )
    business_open = next(
        index
        for index, (kind, action, _session_id, _thread_id) in enumerate(events)
        if kind == "business" and action == "open"
    )
    assert authentication_close < business_open


def _assert_no_business_session(events: list[tuple[str, str, int, int]]) -> None:
    assert not any(
        kind == "business" and action == "open"
        for kind, action, _session_id, _thread_id in events
    )


def _user_row(
    user_id: str,
    *,
    administrator: bool,
    membership: str,
) -> dict[str, Any]:
    return {
        "id": user_id,
        "username": user_id,
        "email": f"{user_id}@example.test",
        "hashed_password": "unused",
        "is_superuser": administrator,
        "membership_type": membership,
        "membership_expires_at": None,
        "avatar_url": None,
        "bio": None,
        "created_at": BUSINESS_NOW,
        "password_updated_at": None,
    }


def _build_app(
    database_path: Path,
    *,
    upload_directory: Path,
    configured_upload_directory: Path,
) -> tuple[Any, FastAPI, list[tuple[str, str, int, int]], dict[str, str]]:
    engine = create_engine(f"sqlite:///{database_path}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _connection_record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    billing_units_reference.create(engine, checkfirst=True)
    authentication_metadata.create_all(engine)
    RedemptionBase.metadata.create_all(
        engine,
        tables=[
            UserRow.__table__,
            UserCreditRow.__table__,
            CreditLogRow.__table__,
            RedemptionCodeRow.__table__,
        ],
    )
    upload_files_metadata.create_all(engine)

    events: list[tuple[str, str, int, int]] = []

    def make_session_factory(kind: str):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=True,
            autoflush=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def factory() -> ObservedSession:
            return maker()

        return factory

    authentication_session_factory = make_session_factory("authentication")
    business_session_factory = make_session_factory("business")

    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                _user_row("admin", administrator=True, membership="free"),
                _user_row("member", administrator=False, membership="premium"),
                _user_row("free", administrator=False, membership="free"),
            ],
        )
        connection.execute(
            insert(system_configs).values(
                id="upload-config",
                upload_dir=str(configured_upload_directory),
            )
        )

    runtime = AuthenticationRuntime(
        session_factory=authentication_session_factory,
        secret_key=SECRET,
        clock=lambda: BUSINESS_NOW,
    )
    app = create_app(
        session_factory=business_session_factory,
        authentication_runtime=runtime,
        upload_directory=upload_directory,
    )
    tokens = {
        user_id: jwt.encode(
            {
                "sub": user_id,
                "exp": int(
                    (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()
                ),
            },
            SECRET,
            algorithm="HS256",
        )
        for user_id in ("admin", "member", "free")
    }
    return engine, app, events, tokens


def test_create_app_composes_redemption_and_dynamic_authenticated_uploads(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "redemption-upload-composition.sqlite"
    configured_directory = tmp_path / "configured-first"
    updated_directory = tmp_path / "configured-second"
    fallback_directory = tmp_path / "explicit-fallback"
    for directory in (configured_directory, updated_directory, fallback_directory):
        directory.mkdir()
    (configured_directory / "probe.txt").write_text("database-first", encoding="utf-8")
    (updated_directory / "probe.txt").write_text("database-updated", encoding="utf-8")
    (fallback_directory / "probe.txt").write_text("explicit-fallback", encoding="utf-8")

    engine, app, events, tokens = _build_app(
        database_path,
        upload_directory=fallback_directory,
        configured_upload_directory=configured_directory,
    )
    try:
        parameter_names = tuple(inspect.signature(create_app).parameters)
        assert parameter_names == (
            "session_factory",
            "resolve_actor",
            "resolve_chapter_lock_actor",
            "series_access_policy",
            "authentication_runtime",
            "download_url_resolver",
            "team_join_quota",
            "resolve_task_account",
            "resolve_task_active",
            "task_cancellation_connection_factory",
            "task_cancellation_signals",
            "resolve_generic_task_active",
            "resolve_admin_task_actor",
            "provider_status_runtime",
            "resolve_user_preferences_account",
            "resolve_prompt_active_actor",
            "resolve_prompt_administrator",
            "model_connection_tester",
            "upload_directory",
            "provider_quota_transport",
            "zhangyuge_balance_transport",
            "aistarslab_transport",
            "runtime_status_configuration",
            "runtime_status_image_fetcher",
            "resolve_runtime_status_account",
            "runtime_status_database_probe",
            "runtime_status_storage_probe",
        )
        assert inspect.signature(create_app).parameters["upload_directory"].default is None
        assert inspect.signature(create_app).parameters["provider_quota_transport"].default is None
        assert inspect.signature(create_app).parameters["zhangyuge_balance_transport"].default is None
        assert inspect.signature(create_app).parameters["aistarslab_transport"].default is None
        assert inspect.signature(create_app).parameters["runtime_status_configuration"].default is None
        assert inspect.signature(create_app).parameters["runtime_status_image_fetcher"].default is None
        assert inspect.signature(create_app).parameters["resolve_runtime_status_account"].default is None
        assert inspect.signature(create_app).parameters["runtime_status_database_probe"].default is None
        assert inspect.signature(create_app).parameters["runtime_status_storage_probe"].default is None

        methods = {
            (method, route.path)
            for route in app.routes
            if isinstance(route, APIRoute)
            for method in route.methods
            if method not in {"HEAD", "OPTIONS"}
        }
        triples = {
            (method, route.path, route.name)
            for route in app.routes
            if isinstance(route, APIRoute)
            for method in route.methods
            if method not in {"HEAD", "OPTIONS"}
        }
        new_routes = {
            ("POST", "/api/redemption-codes/generate", "generate_codes"),
            ("POST", "/api/redemption-codes/redeem", "redeem_code"),
            ("GET", "/uploads/{file_path:path}", "serve_file"),
            ("GET", "/api/provider-quota/accounts", "list_accounts"),
            ("POST", "/api/provider-quota/accounts", "create_account"),
            ("PUT", "/api/provider-quota/accounts/{account_id}", "update_account"),
            ("DELETE", "/api/provider-quota/accounts/{account_id}", "delete_account"),
            ("GET", "/api/provider-quota/accounts/{account_id}/balance", "account_balance"),
            ("POST", "/api/provider-quota/probe", "probe"),
            ("GET", "/api/zhangyuge/balance", "zhangyuge_balance"),
            ("GET", "/api/zhangyuge/pat", "zhangyuge_get_pat"),
            ("POST", "/api/zhangyuge/pat", "zhangyuge_save_pat"),
            ("GET", "/api/aistarslab/config", "config"),
            ("GET", "/api/aistarslab/credits", "credits"),
            ("POST", "/api/aistarslab/add-default", "add_default"),
            ("POST", "/api/aistarslab/build-image-params", "build_image_params"),
            ("POST", "/api/aistarslab/add-image-default", "add_image_default"),
            ("POST", "/api/aistarslab/estimate-cost", "estimate_cost"),
            ("POST", "/api/aistarslab/build-params", "build_video_params"),
            ("POST", "/api/aistarslab/sync-all", "sync_all"),
        }
        runtime_and_maintenance_routes = {
            ("GET", "/api/chat/proxy-image", "proxy_image"),
            ("GET", "/", "read_root"),
            ("GET", "/health", "health_check"),
            ("GET", "/ready", "ready_check"),
            ("POST", "/api/series/{series_id}/assets/orphan-check", "check_orphans"),
            ("GET", "/api/series/{series_id}/assets/duplicates", "list_duplicates"),
            ("POST", "/api/series/{series_id}/assets/duplicates/ignore", "ignore_duplicates"),
            ("GET", "/api/series/{series_id}/assets/duplicates/ignored", "list_ignored"),
            ("POST", "/api/series/{series_id}/assets/merge", "merge_assets"),
        }
        series_exchange_routes = {
            ("GET", "/api/series/{series_id}/fused-assets", "get_fused_assets"),
            ("PUT", "/api/fused-assets/{asset_id}", "put_fused_asset"),
            ("DELETE", "/api/fused-assets/{asset_id}", "remove_fused_asset"),
            ("GET", "/api/series/{series_id}/export", "export_series"),
            ("POST", "/api/series/import", "import_series"),
        }
        storyboard_save_routes = {
            ("POST", "/api/storyboard/filter-and-save-characters-stream", "save_stream"),
            ("POST", "/api/storyboard/filter-and-save-scenes-stream", "save_stream"),
            ("POST", "/api/storyboard/filter-and-save-props-stream", "save_stream"),
        }
        assert len(methods) == 225
        assert len(triples) == 225
        assert len(new_routes) == 20
        assert len(runtime_and_maintenance_routes) == 9
        assert len(series_exchange_routes) == 5
        assert len(storyboard_save_routes) == 3
        assert len(triples - new_routes) == 205
        assert len(
            triples
            - new_routes
            - runtime_and_maintenance_routes
            - series_exchange_routes
        ) == 191
        assert len(
            triples
            - new_routes
            - runtime_and_maintenance_routes
            - series_exchange_routes
            - storyboard_save_routes
        ) == 188
        assert new_routes <= triples
        assert runtime_and_maintenance_routes <= triples
        assert series_exchange_routes <= triples
        assert storyboard_save_routes <= triples
        assert events == []

        body = {"type": "membership", "value": 2, "count": 1}
        start = len(events)
        unauthenticated_generate = _request(
            app,
            "POST",
            "/api/redemption-codes/generate",
            body=body,
        )
        assert unauthenticated_generate.status_code == 401
        _assert_no_business_session(events[start:])

        start = len(events)
        free_generate = _request(
            app,
            "POST",
            "/api/redemption-codes/generate",
            body=body,
            headers={"Authorization": f"Bearer {tokens['free']}"},
        )
        assert free_generate.status_code == 403
        _assert_no_business_session(events[start:])

        start = len(events)
        generated = _request(
            app,
            "POST",
            "/api/redemption-codes/generate",
            body=body,
            headers={"Authorization": f"Bearer {tokens['admin']}"},
        )
        assert generated.status_code == 200, generated.text
        generated_payload = generated.json()
        assert generated_payload["count"] == 1
        assert generated_payload["type"] == "membership"
        code = generated_payload["codes"][0]["code"]
        _assert_authentication_closed_before_business(events[start:])

        start = len(events)
        unauthenticated_upload = _request(app, "GET", "/uploads/probe.txt")
        assert unauthenticated_upload.status_code == 401
        _assert_no_business_session(events[start:])

        start = len(events)
        free_upload_before_redeem = _request(
            app,
            "GET",
            "/uploads/probe.txt",
            headers={"Authorization": f"Bearer {tokens['free']}"},
        )
        assert free_upload_before_redeem.status_code == 403
        _assert_no_business_session(events[start:])

        start = len(events)
        unauthenticated_redeem = _request(
            app,
            "POST",
            "/api/redemption-codes/redeem",
            body={"code": code},
        )
        assert unauthenticated_redeem.status_code == 401
        _assert_no_business_session(events[start:])

        start = len(events)
        redeemed = _request(
            app,
            "POST",
            "/api/redemption-codes/redeem",
            body={"code": code.lower()},
            headers={"Authorization": f"Bearer {tokens['free']}"},
        )
        assert redeemed.status_code == 200, redeemed.text
        assert redeemed.json()["type"] == "membership"
        _assert_authentication_closed_before_business(events[start:])

        start = len(events)
        member_upload = _request(
            app,
            "GET",
            "/uploads/probe.txt",
            headers={"Authorization": f"Bearer {tokens['member']}"},
        )
        assert member_upload.status_code == 200
        assert member_upload.content == b"database-first"
        _assert_authentication_closed_before_business(events[start:])

        redeemed_user_upload = _request(
            app,
            "GET",
            "/uploads/probe.txt",
            headers={"Authorization": f"Bearer {tokens['free']}"},
        )
        assert redeemed_user_upload.status_code == 200
        assert redeemed_user_upload.content == b"database-first"

        with engine.begin() as connection:
            connection.execute(
                update(system_configs)
                .where(system_configs.c.id == "upload-config")
                .values(upload_dir=str(updated_directory))
            )
        updated_config_upload = _request(
            app,
            "GET",
            "/uploads/probe.txt",
            headers={"Authorization": f"Bearer {tokens['member']}"},
        )
        assert updated_config_upload.status_code == 200
        assert updated_config_upload.content == b"database-updated"

        with engine.begin() as connection:
            connection.execute(
                update(system_configs)
                .where(system_configs.c.id == "upload-config")
                .values(upload_dir="")
            )
        fallback_upload = _request(
            app,
            "GET",
            "/uploads/probe.txt",
            headers={"Authorization": f"Bearer {tokens['member']}"},
        )
        assert fallback_upload.status_code == 200
        assert fallback_upload.content == b"explicit-fallback"
    finally:
        engine.dispose()
