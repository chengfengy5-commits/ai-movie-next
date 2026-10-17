"""Fake-transport coverage for non-streaming frame.review Provider calls."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from sqlalchemy.orm import sessionmaker

from haoai_backend.generation_core.frame_review_claims import SqlAlchemyFrameReviewClaims
from haoai_backend.generation_core.frame_review_intents import SqlAlchemyFrameReviewIntents
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_providers.text.openai_provider import (
    build_original_openai_text_provider,
)
from generation_core.test_admission_staging import _G2_JOINT_NOW
from generation_execution.test_frame_review_admission import (
    _build_app,
    _post_review,
)


class _Response:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers = {"content-length": str(len(json.dumps(payload).encode("utf-8")))}
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async def iter_bytes(self, *, chunk_size: int):
        yield self._body


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[dict[str, object]] = []

    async def request(self, method, url, *, headers, content, timeout_seconds, follow_redirects):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "content": content,
                "timeout_seconds": timeout_seconds,
                "follow_redirects": follow_redirects,
            }
        )
        return self.response


class _ClientContext:
    def __init__(self, client: _Client) -> None:
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return False


def _prepared_review(tmp_path, monkeypatch):
    engine, app, token, capabilities, _ = _build_app(tmp_path, monkeypatch)
    response = asyncio.run(_post_review(app, token))
    assert response.status_code == 200, response.text
    sessions = sessionmaker(engine, expire_on_commit=False)
    claim = SqlAlchemyFrameReviewClaims(
        session_factory=sessions,
        db_clock=lambda: _G2_JOINT_NOW,
        id_factory=lambda: "frame-review-provider-claim",
    ).claim_next("ai-review", "provider-test-worker")
    assert claim is not None
    prepared = SqlAlchemyFrameReviewIntents(
        session_factory=sessions,
        db_clock=lambda: _G2_JOINT_NOW,
        id_factory=lambda: "frame-review-provider-intent",
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    ).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.verified_input is not None
    assert prepared.intent is not None
    return engine, capabilities, prepared.verified_input, prepared.intent


def test_openai_text_provider_keeps_the_review_protocol_and_frozen_binding(
    tmp_path, monkeypatch
) -> None:
    engine, capabilities, verified, intent = _prepared_review(tmp_path, monkeypatch)
    response = _Response(
        {"choices": [{"message": {"content": "", "reasoning_content": "  优化后的正文  "}}]}
    )
    client = _Client(response)
    resolved: list[tuple[object, str]] = []
    registry = build_credential_registry(capabilities)

    def resolve_original(binding, purpose):
        resolved.append((binding, purpose))
        return registry.resolve_original_credential(binding, purpose)

    provider = build_original_openai_text_provider(
        snapshot=verified.snapshot,
        execution_snapshot_ref=intent.snapshot_ref,
        credential_binding=verified.credential_binding,
        intent=intent,
        wire_model=verified.model["wire_model"] or verified.model["display_name"],
        http_client_factory=lambda: _ClientContext(client),
        credential_resolver=resolve_original,
    )

    result = provider.optimize_frame(
        verified.snapshot,
        verified.snapshot.effective_context,
        intent,
    )

    assert result.text == "优化后的正文"
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request["method"] == "POST"
    assert request["url"].endswith("/chat/completions")
    assert request["timeout_seconds"] == 300.0
    assert request["follow_redirects"] is False
    assert resolved == [(verified.credential_binding, "create")]
    body = json.loads(request["content"])
    assert body["model"] == verified.model["wire_model"]
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 800
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert "请保持人物和场景连续。" in body["messages"][1]["content"]
    assert "角色站在门口" in body["messages"][1]["content"]
    assert "原始分镜正文" in body["messages"][1]["content"]
    assert "请核实动作方向" in body["messages"][1]["content"]
    assert not body.get("stream", False)
    engine.dispose()


def test_openai_text_provider_rejects_empty_result_without_retry(
    tmp_path, monkeypatch
) -> None:
    engine, capabilities, verified, intent = _prepared_review(tmp_path, monkeypatch)
    client = _Client(_Response({"choices": [{"message": {"content": "  "}}]}))
    registry = build_credential_registry(capabilities)
    provider = build_original_openai_text_provider(
        snapshot=verified.snapshot,
        execution_snapshot_ref=intent.snapshot_ref,
        credential_binding=verified.credential_binding,
        intent=intent,
        wire_model=verified.model["wire_model"] or verified.model["display_name"],
        http_client_factory=lambda: _ClientContext(client),
        credential_resolver=lambda _binding, _purpose: registry.resolve_original_credential(
            _binding, _purpose
        ),
    )

    result = provider.optimize_frame(
        verified.snapshot,
        verified.snapshot.effective_context,
        intent,
    )

    assert result.code == "provider_empty_result"
    assert len(client.requests) == 1
    engine.dispose()


def test_openai_text_provider_empty_user_prompt_keeps_only_target_protocol(
    tmp_path, monkeypatch
) -> None:
    engine, capabilities, verified, intent = _prepared_review(tmp_path, monkeypatch)
    context = json.loads(verified.snapshot.effective_context)
    context["prompt_user"] = ""
    context["request"]["asset_context"] = ""
    snapshot = replace(
        verified.snapshot,
        effective_context=json.dumps(context, ensure_ascii=False, sort_keys=True),
    )
    client = _Client(_Response({"choices": [{"message": {"content": "优化正文"}}]}))
    registry = build_credential_registry(capabilities)
    provider = build_original_openai_text_provider(
        snapshot=snapshot,
        execution_snapshot_ref=intent.snapshot_ref,
        credential_binding=verified.credential_binding,
        intent=intent,
        wire_model=verified.model["wire_model"] or verified.model["display_name"],
        http_client_factory=lambda: _ClientContext(client),
        credential_resolver=lambda _binding, _purpose: registry.resolve_original_credential(
            _binding, _purpose
        ),
    )

    result = provider.optimize_frame(snapshot, snapshot.effective_context, intent)

    assert result.text == "优化正文"
    user_content = json.loads(client.requests[0]["content"])["messages"][1]["content"]
    assert user_content.startswith("【本次要优化的目标分镜正文如下：")
    assert "请优化以下分镜剧情内容" not in user_content
    assert "原始分镜正文" in user_content
    engine.dispose()
