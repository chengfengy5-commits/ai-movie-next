from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from haoai_backend.fused_generation.application import prepare_fused_generation
from haoai_backend.fused_generation.domain import FusedServerImageModel
from haoai_backend.fused_generation.errors import FusedGenerationBadRequest, FusedGenerationConflict
from haoai_backend.fused_generation.durable_application import canonical_fused_request_document
from haoai_backend.fused_generation.schemas import FusedAssetGenerateRequest
from haoai_backend.fused_generation.durable_values import json_digest
from haoai_backend.generation_catalog.domain import ModelSource, PromptOperation, PromptSelection
from haoai_backend.shared.identity import TrustedActor


NOW = datetime(2026, 10, 15, 12, 0, 0)
SERVER_MODEL = FusedServerImageModel(
    id="server-model",
    name="融合图模型",
    cost_per_call=7,
    provider="provider-a",
    wire_model="image-v1",
    api_key="server-secret",
)


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.assets = {
            ("character", "series-a", "character-with-image"): {
                "id": "character-with-image",
                "name": "林岚",
                "image_url": "https://assets.test/lin.png",
            },
            ("character", "series-a", "character-no-image"): {
                "id": "character-no-image",
                "name": "顾秋",
                "image_url": None,
            },
            ("prop", "series-a", "prop-with-image"): {
                "id": "prop-with-image",
                "name": "银铃",
                "image_url": "https://assets.test/bell.png",
            },
        }
        self.chapter = {
            "id": "chapter-a",
            "series_id": "series-a",
            "content": json.dumps([{"scene": "夜景", "characters": ["林岚"]}]),
        }
        self.prompt: PromptSelection | None = PromptSelection(
            prompt_id="prompt-a",
            operation=PromptOperation.IMAGE_BATCH,
            source=ModelSource.SYSTEM,
            category="fusion",
            display_name="角色融合",
            system_prompt="  保持原有画风  ",
            user_prompt="  重点表现眼神  ",
        )
        self.stage_value = None
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def current_time(self):
        return NOW

    def new_id(self):
        value = f"id-{len(self.events)}"
        self.events.append(("new-id", value))
        return value

    def require_series_access(self, actor, series_id):
        self.events.append(("access", actor.user_id, series_id))

    def load_member_asset(self, member_type, series_id, asset_id):
        self.events.append(("asset", member_type, series_id, asset_id))
        return self.assets.get((member_type, series_id, asset_id))

    def resolve_and_charge_model(self, user_id, model_id):
        self.events.append(("model-and-fee-commit", user_id, model_id))
        return SERVER_MODEL

    def resolve_image_prompt(self, user_id, prompt_id):
        self.events.append(("prompt", user_id, prompt_id))
        return self.prompt

    def load_chapter(self, chapter_id):
        self.events.append(("chapter", chapter_id))
        return self.chapter

    def stage_request(self, prepared, user_id):
        self.events.append(("stage", prepared.task_id, user_id))
        self.stage_value = prepared

    def claim_task(self, task_id, claim_token):
        raise AssertionError("application admission must not claim a task")

    def finish_task(self, *args, **kwargs):
        raise AssertionError("application admission must not finalize a task")

    def commit(self):
        self.events.append("commit")
        self.commit_count += 1

    def rollback(self):
        self.events.append("rollback")
        self.rollback_count += 1

    def close(self):
        self.close_count += 1


def _request(**updates) -> FusedAssetGenerateRequest:
    values = {
        "series_id": "series-a",
        "member_type": "character",
        "members": [
            {"kind": "asset", "id": "character-with-image"},
            {"kind": "asset", "id": "character-no-image"},
            {"kind": "asset", "id": "foreign-or-missing"},
            {"kind": "image", "url": "https://uploads.test/custom.png", "name": "自定义参考"},
            {"kind": "image", "url": "", "name": "空图片"},
        ],
        "model_id": "client-selected-model",
    }
    values.update(updates)
    return FusedAssetGenerateRequest.model_validate(values)


def _factory(uow: FakeUnitOfWork):
    return lambda: uow


def test_prepare_filters_foreign_assets_and_numbers_only_actual_references():
    uow = FakeUnitOfWork()

    prepared = prepare_fused_generation(_factory(uow), TrustedActor("owner-a"), _request(
        prompt_system="  服饰质感自然  ",
        prompt_user=" \n  ",
        prompt_id="must-not-be-read",
        chapter_id="chapter-a",
        frame_index=0,
    ))

    assert prepared.member_type == "character"
    assert [member["id"] for member in prepared.members if member["kind"] == "asset"] == [
        "character-with-image",
        "character-no-image",
    ]
    assert prepared.members[1] == {
        "kind": "asset",
        "id": "character-no-image",
        "name": "顾秋",
    }
    assert prepared.reference_images == (
        "https://assets.test/lin.png",
        "https://uploads.test/custom.png",
    )
    assert prepared.prompt == (
        "服饰质感自然\n【角色资产对照】\n图片1 = 林岚\n图片2 = 自定义参考"
    )
    assert prepared.server_model is SERVER_MODEL
    assert prepared.model_id == "server-model"
    assert prepared.credit_cost == 7
    assert prepared.message_id is not None
    assert prepared.target_frame is not None
    assert uow.stage_value is prepared
    assert uow.commit_count == 1
    assert uow.rollback_count == 0
    assert uow.close_count == 1
    assert not any(isinstance(event, tuple) and event[0] == "prompt" for event in uow.events)


def test_stable_target_guard_follows_the_unique_frame_after_reorder():
    target = {
        "storyboard": ["shot-a"],
        "text": "林岚推开门",
        "details": {"lighting": "冷光", "objects": ["门", "钥匙"]},
        "fused": [{"id": "old-result"}],
    }
    current_target = {**target, "fused": [{"id": "new-result"}]}
    other = {"storyboard": ["shot-b"], "text": "走廊空镜", "details": {"objects": []}}
    uow = FakeUnitOfWork()
    uow.chapter = {
        "id": "chapter-a",
        "series_id": "series-a",
        "content": json.dumps([other, current_target], ensure_ascii=False),
    }

    prepared = prepare_fused_generation(
        _factory(uow),
        TrustedActor("owner-a"),
        _request(
            chapter_id="chapter-a",
            frame_index=0,
            storyboard_asset_id="shot-a",
            expected_frame=target,
        ),
    )

    assert prepared.target_frame is not None
    assert prepared.target_frame.frame_index == 1
    assert prepared.target_frame.identity == json.dumps(
        {"storyboard": ["shot-a"], "text": "林岚推开门", "details": {"lighting": "冷光", "objects": ["门", "钥匙"]}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_changed_stable_target_conflicts_before_model_fee():
    uow = FakeUnitOfWork()
    actual = {"storyboard": ["shot-a"], "text": "B"}
    uow.chapter = {"id": "chapter-a", "series_id": "series-a", "content": json.dumps([actual])}

    with pytest.raises(FusedGenerationConflict, match="内容已变化"):
        prepare_fused_generation(
            _factory(uow),
            TrustedActor("owner-a"),
            _request(
                chapter_id="chapter-a",
                storyboard_asset_id="shot-a",
                expected_frame={"storyboard": ["shot-a"], "text": "A"},
                members=[{"kind": "asset", "id": "character-with-image"}],
            ),
        )

    assert not any(isinstance(event, tuple) and event[0] == "model-and-fee-commit" for event in uow.events)


def test_target_guard_rejects_duplicate_ids_and_boolean_number_confusion():
    duplicate_uow = FakeUnitOfWork()
    duplicate = {"storyboard": ["shot-a"], "text": "same"}
    duplicate_uow.chapter = {
        "id": "chapter-a",
        "series_id": "series-a",
        "content": json.dumps([duplicate, duplicate]),
    }
    with pytest.raises(FusedGenerationConflict, match="身份重复"):
        prepare_fused_generation(
            _factory(duplicate_uow),
            TrustedActor("owner-a"),
            _request(
                chapter_id="chapter-a",
                storyboard_asset_id="shot-a",
                expected_frame=duplicate,
                members=[{"kind": "asset", "id": "character-with-image"}],
            ),
        )
    typed_uow = FakeUnitOfWork()
    typed_actual = {"text": "same", "order": 1}
    typed_uow.chapter = {"id": "chapter-a", "series_id": "series-a", "content": json.dumps([typed_actual])}
    with pytest.raises(FusedGenerationConflict, match="内容已变化"):
        prepare_fused_generation(
            _factory(typed_uow),
            TrustedActor("owner-a"),
            _request(
                chapter_id="chapter-a",
                frame_index=0,
                expected_frame={"text": "same", "order": True},
                expected_content=[{"text": "same", "order": True}],
                members=[{"kind": "asset", "id": "character-with-image"}],
            ),
        )


@pytest.mark.parametrize(
    "relationship",
    ["shot-a", ["shot-a", "shot-b"], ["shot-a", "shot-a"]],
)
def test_stable_target_guard_rejects_noncanonical_storyboard_relationships_before_fee(relationship):
    uow = FakeUnitOfWork()
    frame = {"storyboard": relationship, "text": "A"}
    uow.chapter = {"id": "chapter-a", "series_id": "series-a", "content": json.dumps([frame])}

    with pytest.raises(FusedGenerationConflict, match="分镜目标不存在或身份重复"):
        prepare_fused_generation(
            _factory(uow),
            TrustedActor("owner-a"),
            _request(
                chapter_id="chapter-a",
                storyboard_asset_id="shot-a",
                expected_frame=frame,
                members=[{"kind": "asset", "id": "character-with-image"}],
            ),
        )

    assert not any(isinstance(event, tuple) and event[0] == "model-and-fee-commit" for event in uow.events)


def test_no_id_fallback_requires_a_unique_non_fused_frame_identity():
    frame = {"text": "无稳定ID镜头", "characters": ["林岚"], "custom": {"unknown": [1, 2]}}
    uow = FakeUnitOfWork()
    uow.chapter = {
        "id": "chapter-a",
        "series_id": "series-a",
        "content": json.dumps([{**frame, "fused": ["new attachment"]}]),
    }
    prepared = prepare_fused_generation(
        _factory(uow),
        TrustedActor("owner-a"),
        _request(
            chapter_id="chapter-a",
            frame_index=0,
            expected_frame=frame,
            expected_content=[{**frame, "fused": ["old attachment"]}],
            members=[{"kind": "asset", "id": "character-with-image"}],
        ),
    )
    assert prepared.target_frame is not None and prepared.target_frame.frame_index == 0

    duplicate_uow = FakeUnitOfWork()
    duplicate_uow.chapter = {
        "id": "chapter-a",
        "series_id": "series-a",
        "content": json.dumps([frame, frame]),
    }
    with pytest.raises(FusedGenerationConflict, match="无法区分"):
        prepare_fused_generation(
            _factory(duplicate_uow),
            TrustedActor("owner-a"),
            _request(
                chapter_id="chapter-a",
                frame_index=0,
                expected_frame=frame,
                expected_content=[frame, frame],
                members=[{"kind": "asset", "id": "character-with-image"}],
            ),
        )


def test_target_proof_rejects_non_finite_json():
    with pytest.raises(ValidationError, match="有限 JSON"):
        _request(
            chapter_id="chapter-a",
            storyboard_asset_id="shot-a",
            expected_frame={"value": float("nan")},
        )


def test_legacy_canonical_document_keeps_published_fields_and_defaults():
    request = FusedAssetGenerateRequest.model_validate({
        "series_id": "series-a",
        "members": [{"id": "asset-a"}],
        "model_id": "client-model",
    })
    expected = {
        "series_id": "series-a",
        "member_type": "prop",
        "members": [{"kind": "asset", "id": "asset-a", "url": None, "name": None}],
        "model_id": "client-model",
        "prompt_id": None,
        "prompt_system": None,
        "prompt_user": None,
        "size": None,
        "chapter_id": None,
        "frame_index": None,
        "name": None,
    }
    canonical = canonical_fused_request_document(request)
    assert canonical == expected
    assert json_digest(canonical) == json_digest(expected)
    assert canonical_fused_request_document(request.model_copy(update={"expected_frame": None})) == expected
    guarded = canonical_fused_request_document(request.model_copy(update={"expected_frame": {"x": 1}}))
    assert guarded["expected_frame"] == {"x": 1}
    assert json_digest(guarded) != json_digest(expected)


def test_prompt_id_uses_catalog_text_and_mapping_after_system_model_charge():
    uow = FakeUnitOfWork()

    prepared = prepare_fused_generation(
        _factory(uow),
        TrustedActor("owner-a"),
        _request(
            members=[{"kind": "asset", "id": "character-with-image"}],
            prompt_id="prompt-a",
        ),
    )

    assert prepared.prompt == (
        "保持原有画风  \n  重点表现眼神  \n【角色资产对照】\n图片1 = 林岚"
    )
    assert uow.events.index(("model-and-fee-commit", "owner-a", "client-selected-model")) < uow.events.index(
        ("prompt", "owner-a", "prompt-a")
    )
    assert uow.events.index(("prompt", "owner-a", "prompt-a")) < next(
        i for i, event in enumerate(uow.events) if isinstance(event, tuple) and event[0] == "stage"
    )


def test_invalid_prompt_does_not_stage_task_after_legacy_fee_commit():
    uow = FakeUnitOfWork()
    uow.prompt = None

    with pytest.raises(FusedGenerationBadRequest, match="提示词无法引用"):
        prepare_fused_generation(
            _factory(uow),
            TrustedActor("owner-a"),
            _request(members=[{"kind": "asset", "id": "character-with-image"}], prompt_id="missing"),
        )

    assert ("model-and-fee-commit", "owner-a", "client-selected-model") in uow.events
    assert not any(isinstance(event, tuple) and event[0] == "stage" for event in uow.events)
    assert uow.rollback_count == 1
    assert uow.close_count == 1


@pytest.mark.parametrize(
    ("generate_request", "detail"),
    [
        (_request(member_type="scene"), "member_type"),
        (_request(members=[{"kind": "asset", "id": "not-in-series"}]), "没有有效的融合成员"),
        (_request(members=[{"kind": "asset", "id": "character-no-image"}]), "都没有图片"),
        (_request(chapter_id="chapter-a", frame_index=0, members=[{"kind": "asset", "id": "character-with-image"}]), "不属于当前剧集"),
    ],
)
def test_invalid_scope_or_empty_reference_is_rejected_before_model_fee(generate_request, detail):
    uow = FakeUnitOfWork()
    if generate_request.chapter_id == "chapter-a":
        uow.chapter = {**uow.chapter, "series_id": "series-other"}

    with pytest.raises(FusedGenerationBadRequest, match=detail):
        prepare_fused_generation(_factory(uow), TrustedActor("owner-a"), generate_request)

    assert not any(isinstance(event, tuple) and event[0] == "model-and-fee-commit" for event in uow.events)
    assert not any(isinstance(event, tuple) and event[0] == "stage" for event in uow.events)
    assert uow.close_count == 1
