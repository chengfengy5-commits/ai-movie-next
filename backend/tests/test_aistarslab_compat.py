from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from haoai_backend.admin_system_config.persistence import (
    SqlAlchemyAdminSystemConfigUnitOfWork,
)
from haoai_backend.admin_system_config.tables import (
    SystemConfigRow,
    metadata as system_config_metadata,
)
from haoai_backend.admin_system_models.persistence import (
    SqlAlchemyAdminSystemModelUnitOfWork,
)
from haoai_backend.admin_system_models.tables import (
    SystemModelRow as PersistedSystemModelRow,
    metadata as admin_system_models_metadata,
)
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.aistarslab.http import build_aistarslab_router
from haoai_backend.aistarslab.readers import (
    build_sqlalchemy_aistarslab_legacy_reader_factory,
)
from haoai_backend.model_configs.persistence import SqlAlchemyModelConfigsUnitOfWork
from haoai_backend.model_configs.tables import (
    ModelConfigRow,
    SystemModelRow as CredentialSystemModelRow,
    UserRow,
    metadata as model_configs_metadata,
)


class FakeCipher:
    def encrypt(self, plain_text: str | None) -> str:
        return "" if not plain_text else f"gAAAAA{plain_text}"

    def decrypt(self, cipher_text: str | None) -> str:
        if not cipher_text:
            return ""
        if cipher_text.startswith("gAAAAAbad"):
            raise ValueError("invalid test token")
        if cipher_text.startswith("gAAAAA"):
            return cipher_text[len("gAAAAA") :]
        return cipher_text


class FakeTransport:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.calls: list[dict[str, Any]] = []

    async def request_text(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        json_body: dict[str, Any] | None,
        timeout: int,
    ) -> str:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json_body": json_body,
                "timeout": timeout,
            }
        )
        if url.endswith("/account/credits"):
            return json.dumps({"code": 0, "data": {"credits": 321}})
        if url.endswith("/generation/config"):
            return json.dumps({"code": 0, "data": self.config})
        raise AssertionError(f"Unexpected fake request URL: {url}")


class FakeAuthentication:
    @contextmanager
    def authenticated(
        self,
        authorization: str,
        *,
        require_membership: bool,
    ):
        if authorization == "Bearer admin":
            user = SimpleNamespace(id="admin", is_superuser=True)
        elif authorization == "Bearer member":
            user = SimpleNamespace(id="member", is_superuser=False)
        elif authorization == "Bearer free":
            raise AuthenticationError(
                403,
                "需要会员才能使用此功能，请前往购买会员",
            )
        elif authorization.partition(" ")[0].lower() != "bearer":
            raise AuthenticationError(
                401,
                "Not authenticated",
                authenticate=True,
            )
        else:
            raise AuthenticationError(
                401,
                "无法验证凭据",
                authenticate=True,
            )
        yield SimpleNamespace(user=user)


def config_data() -> dict[str, Any]:
    return {
        "videoConfig": [
            {
                "channel": "video-main",
                "title": "视频-Model-A",
                "description": "main video route",
                "defaultOption": True,
                "models": [
                    {
                        "model": "model-v",
                        "label": "Video V",
                        "modes": ["t2v", "i2v"],
                        "aspectRatios": ["16:9", "9:16"],
                        "duration": {"min": 4, "max": 8},
                        "inputImagesMax": 3,
                        "inputVideosMax": 1,
                        "inputAudiosMax": 1,
                        "qualities": [
                            {
                                "quality": "720p",
                                "pricing": {
                                    "type": "per_second",
                                    "credits": 3,
                                },
                            },
                            {
                                "quality": "1080p",
                                "pricing": {
                                    "type": "per_second",
                                    "credits": 5,
                                    "inputVideoMultiplier": 2,
                                },
                            },
                        ],
                    }
                ],
            },
            {
                "channel": "video-secondary",
                "title": "视频-Second",
                "description": None,
                "defaultOption": False,
                "models": [
                    {
                        "model": "model-v2",
                        "label": "Video V2",
                        "modes": ["t2v"],
                        "aspectRatios": ["16:9"],
                        "duration": {"options": [5, 10]},
                        "qualities": [
                            {
                                "quality": "720p",
                                "pricing": {
                                    "type": "per_second",
                                    "credits": 2,
                                },
                            }
                        ],
                    }
                ],
            },
        ],
        "imageConfig": [
            {
                "channel": "image-main",
                "title": "图片-Image-A",
                "description": "main image route",
                "defaultOption": True,
                "models": [
                    {
                        "model": "model-i",
                        "label": "Image I",
                        "aspectRatios": ["1:1", "16:9"],
                        "n": {"min": 1, "max": 1},
                        "inputImagesMax": 4,
                        "qualities": [
                            {
                                "quality": "1K",
                                "pricing": {
                                    "type": "fixed_total",
                                    "credits": 7,
                                },
                            },
                            {
                                "quality": "2K",
                                "pricing": {
                                    "type": "fixed_total",
                                    "credits": 9,
                                },
                            },
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture
def harness(tmp_path):
    database_path = tmp_path / "aistarslab.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    model_configs_metadata.create_all(engine)
    admin_system_models_metadata.create_all(engine)
    system_config_metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(
            UserRow(
                id="admin",
                username="admin",
                email="admin@example.invalid",
                hashed_password="unused",
                is_superuser=True,
                membership_type="premium",
            )
        )
        session.add(
            CredentialSystemModelRow(
                id="broken-system-key",
                category="video",
                name="broken provider credential",
                provider="aistarslab",
                api_key="gAAAAAbad-token",
                model="broken-credential",
                base_url="https://should-not-be-used.test/openapi",
                cost_per_call=0,
                description="",
                is_active=True,
                sort_order=0,
                params=None,
            )
        )
        session.add(
            CredentialSystemModelRow(
                id="system-key",
                category="video",
                name="provider credential",
                provider="aistarslab",
                api_key="gAAAAAsystem-token",
                model="credential",
                base_url="https://aistarslab.test/openapi",
                cost_per_call=0,
                description="",
                is_active=True,
                sort_order=1,
                params=None,
            )
        )
        session.add(
            ModelConfigRow(
                id="personal-key",
                user_id="admin",
                category="video",
                name="personal provider credential",
                provider="aistarslab",
                api_key="gAAAAApersonal-token",
                model="personal-credential",
                base_url="https://personal-key.test/openapi",
                sort_order=0,
                params=None,
            )
        )
        session.add(
            SystemConfigRow(
                id="system-config",
                aistarslab_credit_multiplier=1.5,
            )
        )
        session.commit()

    def model_configs_uow_factory():
        return SqlAlchemyModelConfigsUnitOfWork(session_factory())

    def system_config_uow_factory():
        return SqlAlchemyAdminSystemConfigUnitOfWork(session_factory())

    def system_model_uow_factory():
        return SqlAlchemyAdminSystemModelUnitOfWork(session_factory())

    transport = FakeTransport(config_data())
    app = FastAPI()
    app.include_router(
        build_aistarslab_router(
            authentication_service=FakeAuthentication(),
            model_configs_uow_factory=model_configs_uow_factory,
            legacy_reader_factory=build_sqlalchemy_aistarslab_legacy_reader_factory(
                session_factory
            ),
            system_config_uow_factory=system_config_uow_factory,
            system_model_uow_factory=system_model_uow_factory,
            cipher=FakeCipher(),
            transport=transport,
            clock=lambda: 1_000.0,
            id_factory=iter(
                [
                    "added-video",
                    "added-image",
                    "synced-video",
                ]
            ).__next__,
        )
    )

    yield SimpleNamespace(
        app=app,
        engine=engine,
        session_factory=session_factory,
        transport=transport,
    )
    engine.dispose()


def request(app: FastAPI, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_all_eight_routes_keep_legacy_shapes_and_sync_rules(harness):
    authorization = {"Authorization": "Bearer admin"}

    response = request(
        harness.app,
        "GET",
        "/api/aistarslab/config",
        headers=authorization,
    )
    assert response.status_code == 200
    assert response.json()["credits"] is None
    assert response.json()["videoConfig"][0]["models"][0]["qualities"] == [
        "720p",
        "1080p",
    ]
    assert response.json()["imageConfig"][0]["models"][0]["n"] == {
        "min": 1,
        "max": 1,
    }

    credits = request(
        harness.app,
        "GET",
        "/api/aistarslab/credits",
        headers=authorization,
    )
    assert credits.status_code == 200
    assert credits.json() == {"credits": 321}
    credit_call = next(
        call
        for call in harness.transport.calls
        if call["url"].endswith("/account/credits")
    )
    assert credit_call["headers"]["Authorization"] == "Bearer system-token"

    image_params = request(
        harness.app,
        "POST",
        "/api/aistarslab/build-image-params",
        headers=authorization,
        json={"channel": "image-main", "model": "model-i"},
    )
    assert image_params.status_code == 200
    parsed_image_params = json.loads(image_params.json()["params"])
    assert parsed_image_params["image_size"]["default"] == "1K-1:1"
    assert parsed_image_params["fixed_by_quality"] == {
        "1K": 10,
        "2K": 14,
    }

    video_params = request(
        harness.app,
        "POST",
        "/api/aistarslab/build-params",
        headers=authorization,
        json={"channel": "video-main", "model": "model-v"},
    )
    assert video_params.status_code == 200
    parsed_video_params = json.loads(video_params.json()["params"])
    assert parsed_video_params["duration"]["options"] == [4, 5, 6, 7, 8]
    assert parsed_video_params["per_second_cost"] == 4

    estimate = request(
        harness.app,
        "POST",
        "/api/aistarslab/estimate-cost",
        headers=authorization,
        json={
            "channel": "video-main",
            "model": "model-v",
            "quality": "1080p",
            "duration": 5,
            "has_input_video": True,
        },
    )
    assert estimate.status_code == 200
    assert estimate.json() == {"costCredits": 50}

    added_video = request(
        harness.app,
        "POST",
        "/api/aistarslab/add-default",
        headers=authorization,
        json={"api_key": "  override-key  "},
    )
    assert added_video.status_code == 200
    video_model = added_video.json()
    assert video_model["name"] == "AIS-Model-A"
    assert video_model["model"] == "video-main:model-v"
    assert video_model["api_key"] == "gAAAAA  override-key  "
    assert video_model["is_system"] is True
    assert video_model["cost_per_call"] == 18

    added_image = request(
        harness.app,
        "POST",
        "/api/aistarslab/add-image-default",
        headers=authorization,
        json={},
    )
    assert added_image.status_code == 200
    image_model = added_image.json()
    assert image_model["model"] == "image-main:model-i"
    assert image_model["cost_per_call"] == 10

    sync_commits: list[None] = []

    def count_commit(connection):
        sync_commits.append(None)

    event.listen(harness.engine, "commit", count_commit)
    try:
        synced = request(
            harness.app,
            "POST",
            "/api/aistarslab/sync-all",
            headers=authorization,
        )
    finally:
        event.remove(harness.engine, "commit", count_commit)
    assert len(sync_commits) == 1
    assert synced.status_code == 200
    assert synced.json() == {"created": 1, "updated": 2, "total": 3}

    with harness.session_factory() as session:
        rows = session.query(PersistedSystemModelRow).all()
        by_model = {row.model: row for row in rows}
        assert by_model["video-main:model-v"].is_active is True
        assert by_model["image-main:model-i"].is_active is True
        assert by_model["video-secondary:model-v2"].is_active is False
        assert by_model["video-secondary:model-v2"].sort_order == 3

    provider_calls = harness.transport.calls
    assert len(provider_calls) == 5
    assert {call["timeout"] for call in provider_calls} == {30}
    assert all(
        call["headers"]["Authorization"] in {
            "Bearer system-token",
            "Bearer override-key",
        }
        for call in provider_calls
    )
    assert all(
        call["url"].startswith("https://aistarslab.test/openapi/")
        for call in provider_calls
    )


def test_authentication_and_admin_guards_precede_business_work(harness):
    invalid_body = request(
        harness.app,
        "POST",
        "/api/aistarslab/add-default",
        headers={"Authorization": "Bearer invalid"},
        json={"api_key": 7},
    )
    assert invalid_body.status_code == 401
    assert invalid_body.headers["www-authenticate"] == "Bearer"

    valid_but_invalid_body = request(
        harness.app,
        "POST",
        "/api/aistarslab/add-default",
        headers={"Authorization": "Bearer admin"},
        json={"api_key": 7},
    )
    assert valid_but_invalid_body.status_code == 422

    free_member = request(
        harness.app,
        "GET",
        "/api/aistarslab/config",
        headers={"Authorization": "Bearer free"},
    )
    assert free_member.status_code == 403
    assert free_member.json()["detail"] == "需要会员才能使用此功能，请前往购买会员"

    non_admin = request(
        harness.app,
        "POST",
        "/api/aistarslab/add-default",
        headers={"Authorization": "Bearer member"},
        json={},
    )
    assert non_admin.status_code == 403
    assert non_admin.json()["detail"] == "仅管理员可配置 AIStarsLab 兜底渠道"


def test_config_cache_reuses_response_for_ten_minutes(harness):
    first = request(
        harness.app,
        "GET",
        "/api/aistarslab/config",
        headers={"Authorization": "Bearer admin"},
    )
    second = request(
        harness.app,
        "POST",
        "/api/aistarslab/build-params",
        headers={"Authorization": "Bearer admin"},
        json={"channel": "video-main", "model": "model-v"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert sum(
        call["url"].endswith("/generation/config")
        for call in harness.transport.calls
    ) == 1


def test_sync_video_preserves_existing_category_and_image_sync_sets_image(harness):
    existing = PersistedSystemModelRow(
        id="existing-image-model",
        category="image",
        name="Existing image model",
        provider="aistarslab",
        api_key="gAAAAAbad-existing-key",
        model="video-main:model-v",
        base_url="https://aistarslab.test/openapi",
        cost_per_call=1,
        description="before sync",
        is_active=True,
        sort_order=2,
        params="{}",
    )
    with harness.session_factory() as session:
        session.add(existing)
        session.commit()

    harness.transport.config = {
        "videoConfig": [config_data()["videoConfig"][0]],
        "imageConfig": [],
    }
    response = request(
        harness.app,
        "POST",
        "/api/aistarslab/sync-all",
        headers={"Authorization": "Bearer admin"},
    )
    assert response.status_code == 200
    with harness.session_factory() as session:
        row = session.get(PersistedSystemModelRow, "existing-image-model")
        assert row.category == "image"

    with harness.session_factory() as session:
        row = session.get(PersistedSystemModelRow, "existing-image-model")
        row.category = "video"
        session.commit()
    harness.transport.config = {
        "videoConfig": [config_data()["videoConfig"][1]],
        "imageConfig": [
            {
                "channel": "video-main",
                "title": "Image override",
                "models": [{"model": "model-v", "label": "Image V"}],
            }
        ],
    }
    response = request(
        harness.app,
        "POST",
        "/api/aistarslab/sync-all",
        headers={"Authorization": "Bearer admin"},
    )
    assert response.status_code == 200
    with harness.session_factory() as session:
        row = session.get(PersistedSystemModelRow, "existing-image-model")
        assert row.category == "image"


def test_credentials_keep_unordered_system_query_selection(harness):
    with harness.session_factory() as session:
        session.query(CredentialSystemModelRow).filter(
            CredentialSystemModelRow.provider == "aistarslab"
        ).delete(synchronize_session=False)
        session.add_all(
            [
                CredentialSystemModelRow(
                    id="A",
                    category="video",
                    name="Key A",
                    provider="aistarslab",
                    api_key="gAAAAAkey-A",
                    model="credential-A",
                    base_url="https://key-a.test/openapi",
                    cost_per_call=0,
                    description="",
                    is_active=True,
                    sort_order=10,
                    params=None,
                ),
                CredentialSystemModelRow(
                    id="B",
                    category="video",
                    name="Key B",
                    provider="aistarslab",
                    api_key="gAAAAAkey-B",
                    model="credential-B",
                    base_url="https://key-b.test/openapi",
                    cost_per_call=0,
                    description="",
                    is_active=True,
                    sort_order=0,
                    params=None,
                ),
            ]
        )
        session.commit()
        legacy_query_rows = (
            session.query(CredentialSystemModelRow)
            .filter(
                CredentialSystemModelRow.provider == "aistarslab",
                CredentialSystemModelRow.is_active.is_(True),
            )
            .all()
        )
        # Verify this fixture's stream without implying a global order guarantee.
        assert [row.id for row in legacy_query_rows] == ["A", "B"]

    response = request(
        harness.app,
        "GET",
        "/api/aistarslab/credits",
        headers={"Authorization": "Bearer admin"},
    )
    assert response.status_code == 200
    call = next(
        call
        for call in harness.transport.calls
        if call["url"].endswith("/account/credits")
    )
    assert call["headers"]["Authorization"] == "Bearer key-A"
    assert call["url"].startswith("https://key-a.test/openapi/")


def test_sync_uses_last_unordered_duplicate_model_row(harness):
    model = "video-main:model-v"
    with harness.session_factory() as session:
        session.add_all(
            [
                PersistedSystemModelRow(
                    id="duplicate-A",
                    category="video",
                    name="A should remain unchanged",
                    provider="aistarslab",
                    api_key="gAAAAAkey-A",
                    model=model,
                    base_url="https://aistarslab.test/openapi",
                    cost_per_call=1,
                    description="A",
                    is_active=False,
                    sort_order=10,
                    params="{}",
                ),
                PersistedSystemModelRow(
                    id="duplicate-B",
                    category="video",
                    name="B should be updated",
                    provider="aistarslab",
                    api_key="gAAAAAkey-B",
                    model=model,
                    base_url="https://aistarslab.test/openapi",
                    cost_per_call=1,
                    description="B",
                    is_active=False,
                    sort_order=0,
                    params="{}",
                ),
            ]
        )
        session.commit()
        legacy_query_rows = (
            session.query(PersistedSystemModelRow)
            .filter(
                PersistedSystemModelRow.provider == "aistarslab",
                PersistedSystemModelRow.model == model,
            )
            .all()
        )
        # Verify this fixture's stream without implying a global order guarantee.
        assert [row.id for row in legacy_query_rows] == [
            "duplicate-A",
            "duplicate-B",
        ]

    harness.transport.config = {
        "videoConfig": [config_data()["videoConfig"][0]],
        "imageConfig": [],
    }
    response = request(
        harness.app,
        "POST",
        "/api/aistarslab/sync-all",
        headers={"Authorization": "Bearer admin"},
    )
    assert response.status_code == 200
    with harness.session_factory() as session:
        row_a = session.get(PersistedSystemModelRow, "duplicate-A")
        row_b = session.get(PersistedSystemModelRow, "duplicate-B")
        assert row_a.name == "A should remain unchanged"
        assert row_b.name.startswith("AIS-")
