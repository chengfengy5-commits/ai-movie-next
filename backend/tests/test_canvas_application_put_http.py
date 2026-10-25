"""HTTP DTO boundaries for generation application mode on existing PUT routes."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from haoai_backend.asset_data.schemas import CharacterUpdate, PropUpdate, SceneUpdate
from haoai_backend.asset_data.http import build_asset_data_router
from haoai_backend.canvas_applications.schemas import (
    GenerationApplicationRequest,
    TARGET_SNAPSHOT_FIELDS,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.series_data.schemas import ChapterUpdate
from haoai_backend.series_data.http import build_series_data_router


UPDATE_MODELS = (ChapterUpdate, CharacterUpdate, SceneUpdate, PropUpdate)


def _generation_application(
    *, kind: str = "canvas_frame_preview", asset_type: str = "character"
) -> dict[str, object]:
    target: dict[str, object] = {field: None for field in TARGET_SNAPSHOT_FIELDS}
    target.update(
        {
            "kind": kind,
            "chapter_id": "chapter-one",
            "series_id": "series-one",
        }
    )
    if kind == "canvas_frame_preview":
        target.update(
            {
                "stable_storyboard_asset_id": "storyboard-one",
                "full_frame_digest": "a" * 64,
            }
        )
    else:
        target.update(
            {
                "asset_type": asset_type,
                "asset_id": f"{asset_type}-one",
                "same_series_target_digest": "c" * 64,
            }
        )
    return {
        "task_id": "task-one",
        "result_ref": "result-one",
        "expected_target": target,
        "application_key": "b" * 64,
    }


def _target_from(body: dict[str, object]) -> dict[str, object]:
    target = body["expected_target"]
    assert isinstance(target, dict)
    return target


@pytest.mark.parametrize("model", UPDATE_MODELS)
def test_null_generation_application_never_falls_back_to_legacy_update(model) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"generation_application": None})


@pytest.mark.parametrize("model", UPDATE_MODELS)
def test_generation_application_mode_accepts_only_its_wrapper_and_is_not_legacy_data(model) -> None:
    request = model.model_validate({"generation_application": _generation_application()})

    assert request.generation_application is not None
    assert "generation_application" not in request.model_dump()
    assert "generation_application" not in request.model_dump(exclude_unset=True)

    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "generation_application": _generation_application(),
                "image_url": "https://client.invalid/replacement.png",
            }
        )
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "generation_application": _generation_application(),
                "url": "https://client.invalid/replacement.png",
            }
        )


def test_generation_application_rejects_missing_target_fields_and_nested_extras() -> None:
    body = _generation_application()
    del _target_from(body)["full_frame_digest"]
    with pytest.raises(ValidationError):
        ChapterUpdate.model_validate({"generation_application": body})

    body = _generation_application()
    body["unexpected"] = "ignored by the legacy DTO"
    with pytest.raises(ValidationError):
        ChapterUpdate.model_validate({"generation_application": body})


def test_asset_generation_application_requires_its_original_chapter() -> None:
    body = _generation_application(kind="canvas_asset_image")
    _target_from(body)["chapter_id"] = None

    with pytest.raises(ValidationError):
        CharacterUpdate.model_validate({"generation_application": body})


def test_legacy_update_shapes_keep_existing_fields_without_generation_mode() -> None:
    chapter = ChapterUpdate.model_validate({"title": "Revised"})
    character = CharacterUpdate.model_validate({"audio_url": None, "voice_ref": "alto, calm"})

    assert "generation_application" not in chapter.model_dump()
    assert chapter.title == "Revised"
    assert "generation_application" not in character.model_dump(exclude_unset=True)
    assert character.audio_url is None
    assert character.voice_ref == "alto, calm"


def test_chapter_put_returns_exact_application_receipt_without_resource_response_filter(
    monkeypatch,
) -> None:
    from haoai_backend.canvas_applications import application as application_module

    expected_command = object()
    payload = {
        "task_id": "task-one",
        "result_ref": "result-one",
        "status": "applied",
        "application": {
            "application_ref": "application-one",
            "target_revision": "d" * 64,
            "committed_at": "2026-10-25T00:00:00+00:00",
        },
    }
    monkeypatch.setattr(
        GenerationApplicationRequest,
        "to_command",
        lambda _request: expected_command,
    )
    monkeypatch.setattr(
        application_module,
        "apply_chapter_generation",
        lambda _factory, _actor, chapter_id, command: (
            payload if chapter_id == "chapter-one" and command is expected_command else {}
        ),
    )

    app = FastAPI()
    app.include_router(
        build_series_data_router(
            uow_factory=None,
            resolve_actor=lambda _request: TrustedActor("owner-one"),
            generation_session_factory=lambda: object(),
        )
    )

    async def request_receipt() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://canvas-application.test",
        ) as client:
            return await client.put(
                "/api/chapters/chapter-one",
                json={"generation_application": _generation_application()},
            )

    response = asyncio.run(request_receipt())

    assert response.status_code == 200
    assert response.json() == payload


@pytest.mark.parametrize(
    ("asset_type", "route_name"),
    (("character", "characters"), ("scene", "scenes"), ("prop", "props")),
)
def test_asset_put_binds_generation_route_and_returns_exact_application_receipt(
    monkeypatch,
    asset_type: str,
    route_name: str,
) -> None:
    from haoai_backend.canvas_applications import application as application_module

    expected_command = object()
    payload = {
        "task_id": "task-one",
        "result_ref": "result-one",
        "status": "already_applied",
        "application": {
            "application_ref": "application-one",
            "target_revision": "d" * 64,
            "committed_at": "2026-10-25T00:00:00+00:00",
        },
    }
    monkeypatch.setattr(
        GenerationApplicationRequest,
        "to_command",
        lambda _request: expected_command,
    )
    monkeypatch.setattr(
        application_module,
        "apply_asset_generation",
        lambda _factory, _actor, received_type, received_id, command: (
            payload
            if received_type == asset_type
            and received_id == f"{asset_type}-one"
            and command is expected_command
            else {}
        ),
    )
    app = FastAPI()
    app.include_router(
        build_asset_data_router(
            uow_factory=None,
            resolve_actor=lambda _request: TrustedActor("owner-one"),
            generation_session_factory=lambda: object(),
        )
    )

    async def request_receipt() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://canvas-application.test",
        ) as client:
            return await client.put(
                f"/api/{route_name}/{asset_type}-one",
                json={
                    "generation_application": _generation_application(
                        kind="canvas_asset_image",
                        asset_type=asset_type,
                    )
                },
            )

    response = asyncio.run(request_receipt())

    assert response.status_code == 200
    assert response.json() == payload


def test_generation_mode_without_session_factory_never_falls_back_to_legacy_uow(
    monkeypatch,
) -> None:
    called = False

    def legacy_factory():
        nonlocal called
        called = True
        raise AssertionError("generation mode must not invoke the ordinary UoW")

    monkeypatch.setattr(
        GenerationApplicationRequest,
        "to_command",
        lambda _request: object(),
    )
    app = FastAPI()
    app.include_router(
        build_series_data_router(
            uow_factory=legacy_factory,
            resolve_actor=lambda _request: TrustedActor("owner-one"),
            generation_session_factory=None,
        )
    )

    async def send_request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://canvas-application.test",
        ) as client:
            return await client.put(
                "/api/chapters/chapter-one",
                json={"generation_application": _generation_application()},
            )

    response = asyncio.run(send_request())

    assert response.status_code == 503
    assert called is False
