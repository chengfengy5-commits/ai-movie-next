from __future__ import annotations

import asyncio
import json
import socket
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import pytest

from haoai_backend.fused_generation.domain import (
    FusedGenerationInput,
    FusedGenerationOutput,
    FusedGenerationUnknown,
    FusedServerImageModel,
)
from haoai_backend.fused_generation.errors import (
    FusedGenerationDispatchedCancellation,
    ProviderGenerationFailure,
)
from haoai_backend.fused_generation.openai_runtime import FusedOpenAIImageRuntime
from haoai_backend.generation_contracts import PreparedReference, TransferFailure
from haoai_backend.media_storage.pinned_http import (
    EncodedRequestTarget,
    PinnedPublicMediaFetcher,
)
from haoai_backend.media_storage.public_fetch_policy import PublicFetchPolicy
from haoai_backend.media_storage.transfer_types import MediaTypeRule, SafeMediaPolicy


class FakeResponse:
    def __init__(self, status_code: int, body: bytes = b"", headers=None) -> None:
        self.status_code = status_code
        self.headers = headers or {"content-length": str(len(body))}
        self.body = body
        self.chunk_sizes: list[int] = []

    async def iter_bytes(self, *, chunk_size: int):
        self.chunk_sizes.append(chunk_size)

        async def chunks():
            if self.body:
                yield self.body

        return chunks()


class BlockingBodyResponse:
    status_code = 200
    headers = {}

    async def iter_bytes(self, *, chunk_size: int):
        async def chunks():
            await asyncio.Event().wait()
            yield b"unreachable"

        return chunks()


class FakeHttpClient:
    def __init__(self, response: FakeResponse | BaseException) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class FakeHttpContext(AbstractAsyncContextManager):
    def __init__(self, client: FakeHttpClient) -> None:
        self.client = client
        self.closed = False

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True


class FakePublicMediaFetcher:
    def __init__(self, results: dict[str, Any]) -> None:
        self.results = results
        self.calls: list[tuple[str, int, float]] = []

    async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
        self.calls.append((reference.locator, max_bytes, timeout_seconds))
        result = self.results[reference.locator]
        return result(self) if callable(result) else result


class FakePinnedResponse:
    status = 200
    headers = {"content-type": "image/png", "content-length": "8"}

    def iter_chunks(self, chunk_size: int):
        assert chunk_size == 64 * 1024

        async def chunks():
            yield b"\x89PNG\r\n\x1a\n"

        return chunks()

    async def close(self) -> None:
        return None


class FakePinnedConnector:
    def __init__(self, resolver) -> None:
        self.resolver = resolver

    async def close(self) -> None:
        return None


class FakePinnedSession:
    def __init__(self, connector, response: FakePinnedResponse) -> None:
        self.connector = connector
        self.response = response
        self.request = None
        self.dialed = None

    async def get(self, target, *, headers, timeout_seconds, allow_redirects):
        self.request = (target, dict(headers), timeout_seconds, allow_redirects)
        parsed = urlsplit(target.raw_url)
        self.dialed = await self.connector.resolver.resolve(
            target.tls_hostname,
            parsed.port or 443,
        )
        return self.response

    async def close(self) -> None:
        return None


class FakePinnedHttpLibrary:
    def __init__(self) -> None:
        self.response = FakePinnedResponse()
        self.session_value: FakePinnedSession | None = None

    def encoded_url(self, raw_url: str, *, preserve_encoded: bool):
        parsed = urlsplit(raw_url)
        return EncodedRequestTarget(
            raw_url,
            parsed.netloc,
            parsed.hostname or "",
            preserve_encoded,
        )

    def connector(self, resolver, *, tls_hostname, verify_tls, use_dns_cache):
        assert verify_tls is True
        assert use_dns_cache is False
        assert tls_hostname == "media.example"
        return FakePinnedConnector(resolver)

    def session(
        self,
        connector,
        *,
        trust_env,
        cookies_enabled,
        auto_decompress,
        connector_owned_by_session,
    ):
        assert trust_env is False
        assert cookies_enabled is False
        assert auto_decompress is True
        assert connector_owned_by_session is False
        self.session_value = FakePinnedSession(connector, self.response)
        return self.session_value


def _request(*, base_url: str | None = "https://api.openai.com/v1") -> FusedGenerationInput:
    return FusedGenerationInput(
        task_id="task-fused",
        series_id="series-a",
        member_type="character",
        members=({"id": "member-a"},),
        name="融合角色",
        size="16:9",
        prompt="保持原有神态",
        reference_images=("https://media.example/a.png", "https://media.example/a.png"),
        server_model=FusedServerImageModel(
            id="model-a",
            name="服务端模型",
            cost_per_call=4,
            provider="openai",
            wire_model="selected-image-model",
            api_key="provider-secret",
            base_url=base_url,
        ),
    )


def _runtime(
    client: FakeHttpClient,
    fetcher: FakePublicMediaFetcher | None = None,
    *,
    provider_timeout_seconds: float = 180.0,
    allowed_provider_origins: tuple[str, ...] = ("https://api.openai.com",),
) -> tuple[FusedOpenAIImageRuntime, FakeHttpContext, FakePublicMediaFetcher]:
    context = FakeHttpContext(client)
    public_media_fetcher = fetcher or FakePublicMediaFetcher(
        {
            "https://media.example/a.png": PreparedReference(
                kind="bytes",
                locator=None,
                content=b"first-synthetic-png",
                media_type="image/png",
            )
        }
    )
    runtime = FusedOpenAIImageRuntime(
        http_client_factory=lambda: context,
        public_media_fetcher=public_media_fetcher,
        provider_timeout_seconds=provider_timeout_seconds,
        allowed_provider_origins=allowed_provider_origins,
    )
    return runtime, context, public_media_fetcher


def _success_body(url: str = "https://cdn.openai.example/generated.png") -> bytes:
    return json.dumps({"data": [{"url": url}]}).encode("utf-8")


def test_runtime_sends_one_ordered_multipart_request_with_selected_model_and_size():
    first = PreparedReference(
        kind="bytes", locator=None, content=b"first-reference", media_type="image/png"
    )
    second = PreparedReference(
        kind="bytes", locator=None, content=b"second-reference", media_type="image/jpeg"
    )
    fetcher = FakePublicMediaFetcher(
        {
            "https://media.example/a.png": lambda current: (
                first if len(current.calls) == 1 else second
            )
        }
    )
    client = FakeHttpClient(FakeResponse(200, _success_body()))
    runtime, context, _ = _runtime(client, fetcher)

    result = asyncio.run(runtime.generate(_request()))

    assert result == FusedGenerationOutput("https://cdn.openai.example/generated.png")
    assert len(fetcher.calls) == 2
    assert [call[0] for call in fetcher.calls] == [
        "https://media.example/a.png",
        "https://media.example/a.png",
    ]
    assert all(call[1] == 10 * 1024 * 1024 for call in fetcher.calls)
    assert len(client.calls) == 1
    post = client.calls[0]
    assert post["method"] == "POST"
    assert post["url"] == "https://api.openai.com/v1/images/edits"
    assert post["headers"]["Authorization"] == "Bearer provider-secret"
    assert post["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert post["content"].count(b'name="image"') == 2
    assert post["content"].index(b"first-reference") < post["content"].index(
        b"second-reference"
    )
    assert b"selected-image-model" in post["content"]
    assert b"1792x1024" in post["content"]
    assert post["timeout_seconds"] > 0
    assert post["follow_redirects"] is False
    assert context.closed


def test_runtime_uses_existing_pinned_fetcher_for_public_reference_without_provider_key():
    async def resolve_public_address(host, port, *, family, type):
        return (
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),
        )

    media_library = FakePinnedHttpLibrary()
    public_fetcher = PinnedPublicMediaFetcher(
        PublicFetchPolicy(resolve_public_address),
        SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
        http_library=media_library,
    )
    provider_client = FakeHttpClient(FakeResponse(200, _success_body()))
    runtime, _, _ = _runtime(provider_client, public_fetcher)
    request = replace(
        _request(),
        reference_images=("https://media.example/a.png",),
    )

    result = asyncio.run(runtime.generate(request))

    assert result == FusedGenerationOutput("https://cdn.openai.example/generated.png")
    assert len(provider_client.calls) == 1
    assert media_library.session_value is not None
    target, headers, timeout_seconds, allow_redirects = media_library.session_value.request
    assert target.raw_url == "https://media.example/a.png"
    assert headers == {"Host": "media.example"}
    assert timeout_seconds == 30.0
    assert allow_redirects is False
    assert media_library.session_value.dialed[0].host == "8.8.8.8"
    assert media_library.session_value.dialed[0].flags == socket.AI_NUMERICHOST
    provider_post = provider_client.calls[0]
    assert provider_post["headers"]["Authorization"] == "Bearer provider-secret"
    assert b"\x89PNG\r\n\x1a\n" in provider_post["content"]


def test_custom_provider_origin_requires_explicit_allowlist_entry():
    client = FakeHttpClient(FakeResponse(200, _success_body()))
    runtime, _, _ = _runtime(
        client,
        allowed_provider_origins=("https://image-gateway.example",),
    )

    result = asyncio.run(
        runtime.generate(_request(base_url="https://image-gateway.example/openai/v1"))
    )

    assert result == FusedGenerationOutput("https://cdn.openai.example/generated.png")
    assert len(client.calls) == 1
    assert client.calls[0]["url"] == "https://image-gateway.example/openai/v1/images/edits"


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(408),
        FakeResponse(500),
        FakeResponse(302, headers={"location": "https://other.example/redirect"}),
        FakeResponse(201, _success_body()),
        FakeResponse(200, b"not-json"),
        FakeResponse(
            200,
            json.dumps({"data": [{"b64_json": "cG5n"}]}).encode("utf-8"),
        ),
    ],
)
def test_ambiguous_provider_responses_are_retained_as_unknown(response: FakeResponse):
    client = FakeHttpClient(response)
    runtime, _, _ = _runtime(client)

    result = asyncio.run(runtime.generate(_request()))

    assert isinstance(result, FusedGenerationUnknown)
    assert len(client.calls) == 1


def test_transport_timeout_after_post_is_unknown_and_never_retried():
    client = FakeHttpClient(TimeoutError("timeout with secret provider-secret"))
    runtime, _, _ = _runtime(client)

    result = asyncio.run(runtime.generate(_request()))

    assert isinstance(result, FusedGenerationUnknown)
    assert len(client.calls) == 1
    assert "provider-secret" not in repr(result)


def test_post_and_response_body_share_one_bounded_deadline():
    client = FakeHttpClient(BlockingBodyResponse())
    runtime, _, _ = _runtime(client, provider_timeout_seconds=0.01)

    result = asyncio.run(asyncio.wait_for(runtime.generate(_request()), timeout=0.5))

    assert isinstance(result, FusedGenerationUnknown)
    assert len(client.calls) == 1


def test_signed_provider_image_url_is_returned_byte_for_byte():
    image_url = (
        "https://cdn.openai.example/generated.png?signature=synthetic%2Fvalue&expires=123"
    )
    client = FakeHttpClient(FakeResponse(200, _success_body(image_url)))
    runtime, _, _ = _runtime(client)

    result = asyncio.run(runtime.generate(_request()))

    assert result == FusedGenerationOutput(image_url)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "image_url",
    [
        "https://127.0.0.1/private.png",
        "https://synthetic-user:synthetic-password@cdn.openai.example/image.png",
    ],
)
def test_private_literal_and_credentialed_provider_image_urls_remain_unknown(image_url: str):
    client = FakeHttpClient(FakeResponse(200, _success_body(image_url)))
    runtime, _, _ = _runtime(client)

    result = asyncio.run(runtime.generate(_request()))

    assert isinstance(result, FusedGenerationUnknown)
    assert len(client.calls) == 1


def test_cancelled_post_raises_dispatched_cancellation_for_retention():
    client = FakeHttpClient(asyncio.CancelledError())
    runtime, _, _ = _runtime(client)

    with pytest.raises(FusedGenerationDispatchedCancellation):
        asyncio.run(runtime.generate(_request()))

    assert len(client.calls) == 1


def test_reference_failure_and_unauthorized_origin_fail_before_provider_post():
    failed_fetcher = FakePublicMediaFetcher(
        {
            "https://media.example/a.png": TransferFailure(
                code="timeout", safe_detail="reference timed out"
            )
        }
    )
    client = FakeHttpClient(FakeResponse(200, _success_body()))
    runtime, _, _ = _runtime(client, failed_fetcher)

    with pytest.raises(ProviderGenerationFailure):
        asyncio.run(runtime.generate(_request()))
    assert client.calls == []

    runtime, _, fetcher = _runtime(client)
    with pytest.raises(ProviderGenerationFailure):
        asyncio.run(runtime.generate(_request(base_url="https://untrusted.example/v1")))
    assert fetcher.calls == []
    assert client.calls == []


def test_runtime_does_not_select_a_fallback_model_when_server_model_is_incomplete():
    client = FakeHttpClient(FakeResponse(200, _success_body()))
    runtime, _, fetcher = _runtime(client)
    request = _request()
    request = replace(
        request,
        server_model=replace(request.server_model, wire_model=None),
    )

    with pytest.raises(ProviderGenerationFailure, match="配置不完整"):
        asyncio.run(runtime.generate(request))

    assert fetcher.calls == []
    assert client.calls == []


def test_explicit_provider_rejection_is_a_safe_failure():
    client = FakeHttpClient(FakeResponse(401, b"provider secret response"))
    runtime, _, _ = _runtime(client)

    with pytest.raises(ProviderGenerationFailure, match="HTTP 401") as failure:
        asyncio.run(runtime.generate(_request()))

    assert len(client.calls) == 1
    assert "provider secret response" not in str(failure.value)
