from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from haoai_backend.generation_contracts import (
    BoundCredential,
    CommittedIntent,
    CredentialBinding,
    EndpointPolicy,
    Failure,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    MediaResult,
    ModelReference,
    ReferenceInput,
    TargetSnapshot,
    UnknownCreate,
)
from haoai_backend.generation_providers.image.openai_provider import (
    OpenAIImageProvider,
    _OriginalImageExecutionBinding,
    _MAX_REQUEST_BODY_BYTES,
    _MAX_RESPONSE_BODY_BYTES,
    _RESPONSE_CHUNK_BYTES,
)


class FakeResponseIterator:
    def __init__(
        self,
        chunks: tuple[bytes, ...],
        *,
        cancel: bool = False,
        error: bool = False,
    ) -> None:
        self._chunks = list(chunks)
        self._cancel = cancel
        self._error = error
        self.closed = False

    def __aiter__(self) -> FakeResponseIterator:
        return self

    async def __anext__(self) -> bytes:
        if self._cancel:
            self._cancel = False
            raise asyncio.CancelledError
        if self._error:
            self._error = False
            raise RuntimeError("fake response iterator failure")
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: bytes = b'{"data":[{"url":"https://media.example/image.png"}]}',
        *,
        headers: Mapping[str, str] | None = None,
        chunks: tuple[bytes, ...] | None = None,
        cancel: bool = False,
        error: bool = False,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.iterator = FakeResponseIterator(
            chunks if chunks is not None else (body,), cancel=cancel, error=error
        )
        self.requested_chunk_size: int | None = None

    def iter_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        self.requested_chunk_size = chunk_size
        return self.iterator


class AsyncReturnFakeResponse(FakeResponse):
    async def iter_bytes(self, *, chunk_size: int) -> FakeResponseIterator:
        self.requested_chunk_size = chunk_size
        return self.iterator


class FakeClient:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or FakeResponse()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        timeout_seconds: float,
        follow_redirects: bool = False,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "content": content,
                "timeout_seconds": timeout_seconds,
                "follow_redirects": follow_redirects,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response


class FakeClientContext:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeClient:
        self.entered = True
        return self.client

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.exited = True


class OpenAIImageProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = CredentialBinding(
            source="model_config",
            row_id="credential-row-17",
            owner_or_system="owner-9",
            provider="openai",
            original_principal_id="principal-3",
            endpoint_origin="https://images.example/v1",
            allowed_content_paths=(),
            binding_version="v4",
        )
        self.snapshot = self.make_snapshot()
        self.intent = self.make_intent(self.snapshot)

    def make_snapshot(
        self,
        *,
        prompt: str = "draw a red kite",
        params: dict[str, object] | None = None,
        provider: str = "openai",
        model_id: str = "catalog-model-row-27",
        model_name: str = "Image Model Display Name",
        owner_id: str = "owner-9",
        reference_ref: str = "references-snapshot-9",
        origin: str | None = "https://images.example/v1",
    ) -> FrozenExecutionSnapshot:
        return FrozenExecutionSnapshot(
            model_ref=ModelReference(owner_id=owner_id, provider=provider, model_id=model_id),
            model_category="image",
            model_name=model_name,
            model_params=params if params is not None else {"size": "1:1"},
            prompt_ref=None,
            effective_prompt=prompt,
            effective_context="",
            server_quote=0,
            reference_snapshot_ref=reference_ref,
            target_snapshot=TargetSnapshot(kind="none"),
            credential_binding_ref="binding-ref-17",
            grant_snapshot=FrozenGrantSnapshot(actor_id="actor-5"),
            adapter_config_versions=(),
            endpoint_policy=EndpointPolicy(provider_origin=origin),
        )

    def make_intent(
        self,
        snapshot: FrozenExecutionSnapshot,
        *,
        snapshot_ref: str = "execution-snapshot-4",
    ) -> CommittedIntent:
        return CommittedIntent(
            step_id="step-1",
            attempt_id="attempt-1",
            external_submission_id="submission-1",
            provider=snapshot.model_ref.provider,
            model_id=snapshot.model_ref.model_id,
            request_digest="request-digest",
            snapshot_ref=snapshot_ref,
            committed_at=datetime.now(timezone.utc),
            first_intent_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
        )

    def make_execution_binding(
        self,
        *,
        wire_model: str | None = "gpt-image-1",
    ) -> _OriginalImageExecutionBinding:
        return _OriginalImageExecutionBinding(
            snapshot=self.snapshot,
            execution_snapshot_ref=self.intent.snapshot_ref,
            credential_binding=self.binding,
            credential_binding_ref=self.snapshot.credential_binding_ref,
            intent=self.intent,
            wire_model=wire_model,
        )

    def make_provider(
        self,
        *,
        response: FakeResponse | None = None,
        request_error: Exception | None = None,
        credential: BoundCredential | None = None,
        wire_model: str | None = "gpt-image-1",
    ) -> tuple[
        OpenAIImageProvider,
        FakeClient,
        FakeClientContext,
        list[tuple[CredentialBinding, str]],
    ]:
        client = FakeClient(response=response, error=request_error)
        context = FakeClientContext(client)
        resolver_calls: list[tuple[CredentialBinding, str]] = []

        def resolve(binding: CredentialBinding, purpose: str) -> BoundCredential | None:
            resolver_calls.append((binding, purpose))
            return credential or BoundCredential(
                provider=self.binding.provider,
                principal_id=self.binding.original_principal_id,
                secret="test-secret",
            )

        provider = OpenAIImageProvider(
            execution=self.make_execution_binding(wire_model=wire_model),
            http_client_factory=lambda: context,
            credential_resolver=resolve,
        )
        return provider, client, context, resolver_calls

    def test_cold_construction_and_single_json_post_use_frozen_values(self) -> None:
        client = FakeClient()
        context = FakeClientContext(client)
        resolver_calls: list[tuple[CredentialBinding, str]] = []
        provider = OpenAIImageProvider(
            execution=self.make_execution_binding(),
            http_client_factory=lambda: context,
            credential_resolver=lambda binding, purpose: (
                resolver_calls.append((binding, purpose))
                or BoundCredential("openai", "principal-3", "test-secret")
            ),
        )
        self.assertEqual(client.calls, [])
        self.assertFalse(context.entered)
        self.assertEqual(resolver_calls, [])

        result = provider.generate_sync_image(self.snapshot, (), self.intent)

        self.assertEqual(
            result,
            MediaResult(
                media_kind="image",
                locator="https://media.example/image.png",
                content_digest=None,
            ),
        )
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://images.example/v1/images/generations")
        self.assertEqual(call["headers"], {
            "Authorization": "Bearer test-secret",
            "Content-Type": "application/json",
        })
        self.assertEqual(call["timeout_seconds"], 1800.0)
        self.assertFalse(call["follow_redirects"])
        self.assertNotEqual(
            self.snapshot.reference_snapshot_ref,
            self.intent.snapshot_ref,
        )
        self.assertEqual(json.loads(call["content"]), {
            "model": "gpt-image-1",
            "prompt": "draw a red kite",
            "n": 1,
            "size": "1024x1024",
        })
        self.assertIs(resolver_calls[0][0], self.binding)
        self.assertEqual(resolver_calls[0][1], "create")
        self.assertTrue(context.exited)
        self.assertTrue(client.response.iterator.closed)
        self.assertEqual(client.response.requested_chunk_size, _RESPONSE_CHUNK_BYTES)

    def test_haoai_quality_and_base64_result_are_kept_raw(self) -> None:
        self.binding = CredentialBinding(
            source="model_config", row_id="r", owner_or_system="owner-9",
            provider="haoai", original_principal_id="p",
            endpoint_origin="https://haoai.example/v1",
            allowed_content_paths=(), binding_version="1",
        )
        self.snapshot = self.make_snapshot(
            provider="haoai",
            params={"size": "16:9", "quality": {"default": "high"}},
            origin="https://haoai.example/v1",
        )
        self.intent = CommittedIntent(
            step_id="s", attempt_id="a", external_submission_id="x",
            provider="haoai", model_id=self.snapshot.model_ref.model_id, request_digest="d",
            snapshot_ref="execution-snapshot-4", committed_at=datetime.now(timezone.utc),
            first_intent_at=datetime.now(timezone.utc), deadline_at=datetime.now(timezone.utc),
        )
        response = FakeResponse(body=b'{"data":[{"url":"","b64_json":"bm90LWRlY29kZWQ="}]}')
        provider, client, _, _ = self.make_provider(
            response=response,
            credential=BoundCredential("haoai", "p", "haoai-test-secret"),
        )

        result = provider.generate_sync_image(self.snapshot, (), self.intent)

        self.assertIsInstance(result, MediaResult)
        self.assertEqual(result.locator, "data:image/png;base64,bm90LWRlY29kZWQ=")
        self.assertNotEqual(self.snapshot.model_ref.model_id, self.snapshot.model_name)
        self.assertEqual(json.loads(client.calls[0]["content"]), {
            "model": "gpt-image-1",
            "prompt": "draw a red kite",
            "n": 1,
            "size": "1792x1024",
            "quality": "high",
        })

    def test_unsupported_reference_returns_before_resolution_or_post(self) -> None:
        provider, client, context, resolver_calls = self.make_provider()

        result = provider.generate_sync_image(
            self.snapshot,
            (ReferenceInput(kind="public_http", locator="https://ref.example/a.png"),),
            self.intent,
        )

        self.assertIsInstance(result, Failure)
        self.assertEqual(result.code, "unsupported_reference_images")
        self.assertEqual(client.calls, [])
        self.assertFalse(context.entered)
        self.assertEqual(resolver_calls, [])

    def test_original_execution_pair_mismatches_fail_before_resolution(self) -> None:
        provider, client, context, resolver_calls = self.make_provider()
        wrong_owner = replace(
            self.snapshot,
            model_ref=replace(self.snapshot.model_ref, owner_id="other-owner"),
        )
        wrong_model = replace(
            self.snapshot,
            model_ref=replace(self.snapshot.model_ref, model_id="other-catalog-row"),
        )
        wrong_prompt = replace(self.snapshot, effective_prompt="changed prompt")
        wrong_settings = replace(self.snapshot, model_params={"size": "16:9"})
        wrong_origin = self.make_snapshot(origin="https://other.example/v1")
        wrong_attempt = replace(self.intent, attempt_id="other-attempt")
        wrong_execution_ref = replace(
            self.intent,
            snapshot_ref="other-execution-snapshot",
        )

        for candidate in (wrong_owner, wrong_model, wrong_prompt, wrong_settings, wrong_origin):
            with self.subTest(snapshot=candidate):
                result = provider.generate_sync_image(candidate, (), self.intent)
                self.assertEqual(
                    result,
                    Failure("frozen_execution_mismatch", "图片请求与原冻结执行快照不匹配"),
                )
        for candidate_intent in (wrong_attempt, wrong_execution_ref):
            with self.subTest(intent=candidate_intent):
                result = provider.generate_sync_image(
                    self.snapshot, (), candidate_intent
                )
                self.assertEqual(
                    result,
                    Failure("committed_intent_mismatch", "图片请求与原提交意图不匹配"),
                )
        self.assertEqual(client.calls, [])
        self.assertFalse(context.entered)
        self.assertEqual(resolver_calls, [])

    def test_consistently_bound_unsupported_provider_has_no_side_effects(self) -> None:
        snapshot = self.make_snapshot(provider="xai")
        binding = replace(self.binding, provider="xai")
        intent = self.make_intent(snapshot)
        execution = _OriginalImageExecutionBinding(
            snapshot=snapshot,
            execution_snapshot_ref=intent.snapshot_ref,
            credential_binding=binding,
            credential_binding_ref=snapshot.credential_binding_ref,
            intent=intent,
            wire_model="xai-image-v1",
        )
        client = FakeClient()
        context = FakeClientContext(client)
        resolver_calls: list[tuple[CredentialBinding, str]] = []
        factory_calls: list[None] = []

        def make_client_context() -> FakeClientContext:
            factory_calls.append(None)
            return context

        def resolve(binding: CredentialBinding, purpose: str) -> BoundCredential | None:
            resolver_calls.append((binding, purpose))
            return BoundCredential("xai", "principal-3", "test-secret")

        provider = OpenAIImageProvider(
            execution=execution,
            http_client_factory=make_client_context,
            credential_resolver=resolve,
        )

        resolved = provider.resolve_original_credential(binding, "create")
        self.assertEqual(resolved, "unavailable")
        self.assertEqual(resolver_calls, [])
        self.assertEqual(factory_calls, [])
        self.assertEqual(client.calls, [])
        self.assertFalse(context.entered)

        result = provider.generate_sync_image(snapshot, (), intent)

        self.assertEqual(
            result,
            Failure("unsupported_provider", "当前图片适配器不支持该 provider"),
        )
        self.assertEqual(resolver_calls, [])
        self.assertEqual(factory_calls, [])
        self.assertEqual(client.calls, [])
        self.assertFalse(context.entered)

    def test_falsey_frozen_wire_model_uses_legacy_default(self) -> None:
        provider, client, _, _ = self.make_provider(wire_model="")
        result = provider.generate_sync_image(self.snapshot, (), self.intent)

        self.assertIsInstance(result, MediaResult)
        self.assertEqual(json.loads(client.calls[0]["content"])["model"], "dall-e-3")

    def test_credential_provider_principal_and_empty_secret_mismatch_do_not_post(self) -> None:
        for credential in (
            BoundCredential("other", "principal-3", "secret"),
            BoundCredential("openai", "other-principal", "secret"),
            BoundCredential("openai", "principal-3", "   "),
        ):
            with self.subTest(
                provider=credential.provider,
                principal=credential.principal_id,
            ):
                provider, client, context, resolver_calls = self.make_provider(
                    credential=credential
                )
                result = provider.generate_sync_image(self.snapshot, (), self.intent)
                self.assertIsInstance(result, Failure)
                self.assertEqual(result.code, "configuration_unavailable")
                self.assertNotIn("secret", result.safe_detail)
                self.assertEqual(client.calls, [])
                self.assertFalse(context.entered)
                self.assertIs(resolver_calls[0][0], self.binding)

    def test_known_http_failure_and_uncertain_server_failure_are_distinct(self) -> None:
        provider, client, context, _ = self.make_provider(response=FakeResponse(status_code=429))
        known = provider.generate_sync_image(self.snapshot, (), self.intent)
        self.assertEqual(known, Failure("provider_rejected", "图片生成失败 (429)"))
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(context.exited)

        provider, client, context, _ = self.make_provider(response=FakeResponse(status_code=500))
        uncertain = provider.generate_sync_image(self.snapshot, (), self.intent)
        self.assertIsInstance(uncertain, UnknownCreate)
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(context.exited)

    def test_timeout_and_malformed_success_are_unknown_create(self) -> None:
        provider, client, context, _ = self.make_provider(request_error=TimeoutError())
        timed_out = provider.generate_sync_image(self.snapshot, (), self.intent)
        self.assertIsInstance(timed_out, UnknownCreate)
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(context.exited)

        provider, client, context, _ = self.make_provider(response=FakeResponse(body=b"{"))
        malformed = provider.generate_sync_image(self.snapshot, (), self.intent)
        self.assertIsInstance(malformed, UnknownCreate)
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(context.exited)
        self.assertTrue(client.response.iterator.closed)

    def test_request_and_response_body_bounds(self) -> None:
        original_snapshot, original_intent = self.snapshot, self.intent
        large_snapshot = self.make_snapshot(prompt="x" * _MAX_REQUEST_BODY_BYTES)
        self.snapshot = large_snapshot
        self.intent = self.make_intent(large_snapshot)
        provider, client, context, _ = self.make_provider()
        too_large_request = provider.generate_sync_image(large_snapshot, (), self.intent)
        self.assertEqual(too_large_request, Failure("request_body_too_large", "图片请求超过本地传输上限"))
        self.assertEqual(client.calls, [])
        self.assertFalse(context.entered)

        self.snapshot, self.intent = original_snapshot, original_intent
        response = FakeResponse(
            body=b"",
            chunks=(b"x" * (_MAX_RESPONSE_BODY_BYTES + 1),),
        )
        provider, client, context, _ = self.make_provider(response=response)
        too_large_response = provider.generate_sync_image(self.snapshot, (), self.intent)
        self.assertIsInstance(too_large_response, UnknownCreate)
        self.assertEqual(client.calls[0]["method"], "POST")
        self.assertTrue(response.iterator.closed)
        self.assertTrue(context.exited)

    def test_cancellation_closes_iterator_and_client_context(self) -> None:
        response = FakeResponse(cancel=True)
        provider, client, context, _ = self.make_provider(response=response)

        result = provider.generate_sync_image(self.snapshot, (), self.intent)

        self.assertIsInstance(result, UnknownCreate)
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(response.iterator.closed)
        self.assertTrue(context.exited)

    def test_async_return_iterator_cleanup_on_success_error_and_cancellation(self) -> None:
        cases = (
            ("success", AsyncReturnFakeResponse(), MediaResult),
            ("error", AsyncReturnFakeResponse(error=True), UnknownCreate),
            ("cancel", AsyncReturnFakeResponse(cancel=True), UnknownCreate),
        )
        for label, response, expected_type in cases:
            with self.subTest(label=label):
                provider, client, context, _ = self.make_provider(response=response)
                result = provider.generate_sync_image(self.snapshot, (), self.intent)
                self.assertIsInstance(result, expected_type)
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(response.requested_chunk_size, _RESPONSE_CHUNK_BYTES)
                self.assertTrue(response.iterator.closed)
                self.assertTrue(context.exited)

    def test_running_event_loop_is_rejected_before_resolver_or_post(self) -> None:
        provider, client, context, resolver_calls = self.make_provider()

        async def invoke() -> object:
            return provider.generate_sync_image(self.snapshot, (), self.intent)

        result = asyncio.run(invoke())

        self.assertEqual(result, Failure(
            "wrong_execution_context", "同步图片生成不能在运行中的事件循环内执行"
        ))
        self.assertEqual(client.calls, [])
        self.assertFalse(context.entered)
        self.assertEqual(resolver_calls, [])


if __name__ == "__main__":
    unittest.main()
