"""One-shot provider contract for registered frame.optimize intents."""

from __future__ import annotations

import json

from haoai_backend.generation_contracts.types import TextResult
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_providers.text.frame_optimize_provider import (
    FrameOptimizeTextProvider,
)
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    claim_one,
    prepare_item,
)


class _Response:
    status_code = 200

    async def iter_bytes(self, *, chunk_size: int):
        yield json.dumps(
            {"choices": [{"message": {"content": "优化文本"}}]},
            ensure_ascii=False,
        ).encode()


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes]] = []

    async def request(self, method, url, *, headers, content, **_kwargs):
        self.calls.append((method, url, headers, content))
        return _Response()


class _ClientContext:
    def __init__(self, client: _Client) -> None:
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return False


class _AuthorityGuard:
    def __init__(self) -> None:
        self.started = False
        self.status: int | None = None

    def require_live_before_deadline(self) -> None:
        return None

    def mark_request_started(self) -> None:
        self.started = True

    def record_http_status(self, status: int | None) -> None:
        self.status = status


def test_provider_sends_one_original_intent_and_uses_frozen_wire_model(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert prepared.verified_item is not None
        assert prepared.verified_item.snapshot is not None
        client = _Client()
        guard = _AuthorityGuard()
        registry = build_credential_registry(case.capabilities)
        provider = FrameOptimizeTextProvider(
            snapshot=prepared.verified_item.snapshot,
            item=prepared.verified_item,
            intent=prepared.intent,
            http_client_factory=lambda: _ClientContext(client),
            credential_resolver=registry.resolve_original_credential,
            authority_guard=guard,
        )

        result = provider.optimize_frame()

        assert isinstance(result, TextResult)
        assert result.text == "优化文本"
        assert guard.started is True
        assert guard.status == 200
        assert len(client.calls) == 1
        method, url, headers, content = client.calls[0]
        assert method == "POST"
        assert url == "https://provider.example.test/v2/chat/completions"
        assert headers["Authorization"].startswith("Bearer ")
        body = json.loads(content)
        assert body["model"] == prepared.verified_item.snapshot.model_name
        assert body["messages"][1]["content"].endswith("requested frame 0")
    finally:
        case.engine.dispose()
