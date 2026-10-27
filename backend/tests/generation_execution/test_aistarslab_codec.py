from __future__ import annotations

import json

import pytest

from haoai_backend.generation_providers.video.aistarslab_codec import (
    AistarslabCodecError,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_CHANNEL,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_MODEL,
    DEFAULT_QUALITY,
    build_create_payload,
    parse_create_response,
    parse_model,
    parse_status_response,
)


def test_model_parser_uses_legacy_test_defaults_and_first_separator() -> None:
    assert parse_model(None).channel == DEFAULT_CHANNEL
    assert parse_model(None).model == DEFAULT_MODEL
    assert parse_model("").channel == DEFAULT_CHANNEL
    assert parse_model("").model == DEFAULT_MODEL
    assert parse_model(" seedance-2.0 ").channel == "test"
    assert parse_model(" seedance-2.0 ").model == "seedance-2.0"
    assert parse_model("47: seedance:2.0 ").channel == "47"
    assert parse_model("47: seedance:2.0 ").model == "seedance:2.0"


def test_create_payload_matches_legacy_defaults_markers_and_group_order() -> None:
    payload = build_create_payload(
        model="47:seedance-2.0",
        prompt="A quiet room",
        images=("https://media.test/i1", "https://media.test/i2"),
        audios=("https://media.test/a1",),
        videos=("https://media.test/v1",),
    )

    assert payload == {
        "channel": "47",
        "model": "seedance-2.0",
        "prompt": "A quiet room @图片1 @图片2 @音频1 @视频1",
        "aspectRatio": DEFAULT_ASPECT_RATIO,
        "quality": DEFAULT_QUALITY,
        "duration": DEFAULT_DURATION_SECONDS,
        "mode": "frames2video",
        "inputImages": ["https://media.test/i1", "https://media.test/i2"],
        "inputAudios": ["https://media.test/a1"],
        "inputVideos": ["https://media.test/v1"],
    }


def test_audio_and_video_references_do_not_change_image_mode() -> None:
    payload = build_create_payload(
        model=None,
        prompt="A quiet room",
        audios=("https://media.test/a1",),
        videos=("https://media.test/v1",),
    )
    assert payload["mode"] == "text2video"

    one_image = build_create_payload(
        model="test-video",
        prompt="A quiet room",
        images=("https://media.test/i1",),
        audios=("https://media.test/a1",),
    )
    assert one_image["mode"] == "image2video"


@pytest.mark.parametrize(
    ("field_name", "values"),
    (
        ("images", tuple(f"https://media.test/i{index}" for index in range(10))),
        ("audios", tuple(f"https://media.test/a{index}" for index in range(6))),
        ("videos", tuple(f"https://media.test/v{index}" for index in range(6))),
    ),
)
def test_create_payload_rejects_reference_groups_over_provider_limits(
    field_name: str,
    values: tuple[str, ...],
) -> None:
    with pytest.raises(AistarslabCodecError):
        build_create_payload(
            model="test-video",
            prompt="A quiet room",
            **{field_name: values},
        )


def test_response_parsers_require_exact_integer_fields_and_original_id() -> None:
    created = parse_create_response(
        200,
        b'{"code":0,"data":{"taskId":"original-id"}}',
    )
    assert created.code == 0
    assert created.task_id == "original-id"

    rejected = parse_create_response(200, b'{"code":12,"msg":"rejected"}')
    assert rejected.code == 12
    assert rejected.task_id is None

    pending = parse_status_response(
        200,
        b'{"code":0,"data":{"status":1}}',
    )
    assert pending.status == 1
    assert pending.output_url is None

    completed = parse_status_response(
        200,
        b'{"code":0,"data":{"status":3,"outputs":["https://media.test/out.mp4"]}}',
    )
    assert completed.status == 3
    assert completed.output_url == "https://media.test/out.mp4"


@pytest.mark.parametrize(
    "body",
    (
        b'{"code":true,"data":{"taskId":"id"}}',
        b'{"code":0,"data":{}}',
        b'{"code":0,"data":{"taskId":4}}',
        b'[]',
        b'{"code":0,"data":{"taskId":"id"},"extra":NaN}',
    ),
)
def test_create_parser_keeps_malformed_success_unknown(
    body: bytes,
) -> None:
    with pytest.raises(AistarslabCodecError):
        parse_create_response(200, body)


@pytest.mark.parametrize(
    "body",
    (
        b'{"code":true,"data":{"status":1}}',
        b'{"code":0,"data":{"status":true}}',
        b'{"code":1,"data":{"status":3,"outputs":["https://media.test/out.mp4"]}}',
        b'{"code":0,"data":{"status":3,"outputs":[]}}',
        b'{"code":0,"data":[]}',
    ),
)
def test_status_parser_rejects_bool_wrong_code_and_missing_output(
    body: bytes,
) -> None:
    with pytest.raises(AistarslabCodecError):
        parse_status_response(200, body)


@pytest.mark.parametrize("status", (1, 2, 3, 4))
def test_status_parser_preserves_supported_integer_states(status: int) -> None:
    data: dict[str, object] = {"status": status}
    if status == 3:
        data["outputs"] = ["https://media.test/out.mp4"]
    result = parse_status_response(
        200,
        json.dumps({"code": 0, "data": data}).encode("utf-8"),
    )
    assert result.status == status
