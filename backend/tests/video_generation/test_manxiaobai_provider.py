from __future__ import annotations

import asyncio
import json
import time
import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from haoai_backend.generation_contracts import (
    BoundCredential,
    CommittedIntent,
    CommittedPollReservation,
    CredentialBinding,
    EndpointPolicy,
    ExternalOperationRef,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    GenerationError,
    ModelReference,
    OwnerTaskScope,
    PollReservation,
    ReferenceInput,
    TargetSnapshot,
)
from haoai_backend.generation_providers.video.manxiaobai_provider import (
    CREATE_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    PROVIDER_ID,
    QUERY_TIMEOUT_SECONDS,
    RESPONSE_CHUNK_BYTES,
    ManxiaobaiVideoProvider,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
ORIGIN = "https://api.manxiaobai.online"
SECRET = "sk_live/a+b=123"


class AsyncChunkIterator:
    def __init__(
        self,
        chunks: list[bytes],
        owner: FakeResponse | None = None,
        close_error: BaseException | None = None,
        next_error: BaseException | None = None,
        next_delay: float = 0.0,
    ) -> None:
        self._chunks = list(chunks)
        self._owner = owner
        self._close_error = close_error
        self._next_error = next_error
        self._next_delay = next_delay
        self.closed = False
        self.close_attempts = 0

    def __aiter__(self) -> AsyncChunkIterator:
        return self

    async def __anext__(self) -> bytes:
        if self._next_error is not None:
            raise self._next_error
        if not self._chunks:
            raise StopAsyncIteration
        if self._next_delay > 0:
            await asyncio.sleep(self._next_delay)
        return self._chunks.pop(0)

    async def aclose(self) -> None:
        self.close_attempts += 1
        self.closed = True
        if self._owner is not None:
            self._owner.iterator_closed = True
        if self._close_error is not None:
            raise self._close_error


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        body: bytes = b"",
        *,
        chunks: list[bytes] | None = None,
        iterator_mode: str = "awaitable",
        headers: dict[str, str] | None = None,
        iterator_close_error: BaseException | None = None,
        iterator_next_error: BaseException | None = None,
        response_close_error: BaseException | None = None,
        iterator_next_delay: float = 0.0,
        response_close_delay: float = 0.0,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks if chunks is not None else [body]
        self.iterator_mode = iterator_mode
        self.iterator_close_error = iterator_close_error
        self.iterator_next_error = iterator_next_error
        self.response_close_error = response_close_error
        self.iterator_next_delay = iterator_next_delay
        self.response_close_delay = response_close_delay
        self.iterator_closed = False
        self.closed = False
        self.close_attempts = 0
        self.iterator: AsyncChunkIterator | None = None
        self.chunk_size: int | None = None

    def iter_bytes(self, *, chunk_size: int):
        self.chunk_size = chunk_size
        self.iterator = AsyncChunkIterator(
            self._chunks,
            self,
            close_error=self.iterator_close_error,
            next_error=self.iterator_next_error,
            next_delay=self.iterator_next_delay,
        )
        if self.iterator_mode == "direct":
            return self.iterator

        async def resolve_iterator() -> AsyncChunkIterator:
            return self.iterator  # type: ignore[return-value]

        return resolve_iterator()

    async def aclose(self) -> None:
        self.close_attempts += 1
        try:
            if self.response_close_delay > 0:
                await asyncio.sleep(self.response_close_delay)
        finally:
            self.closed = True
        if self.response_close_error is not None:
            raise self.response_close_error


class FakeClient:
    def __init__(
        self,
        response: FakeResponse | None = None,
        *,
        error: BaseException | None = None,
        close_error: BaseException | None = None,
        request_delay: float = 0.0,
        close_delay: float = 0.0,
    ) -> None:
        self.response = response
        self.error = error
        self.close_error = close_error
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False
        self.close_attempts = 0
        self.request_delay = request_delay
        self.close_delay = close_delay

    async def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if self.request_delay > 0:
            await asyncio.sleep(self.request_delay)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    async def aclose(self) -> None:
        self.close_attempts += 1
        try:
            if self.close_delay > 0:
                await asyncio.sleep(self.close_delay)
        finally:
            self.closed = True
        if self.close_error is not None:
            raise self.close_error


class ManxiaobaiVideoProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = self.make_snapshot()
        self.intent = self.make_intent()
        self.binding = self.make_binding()
        self.scope = OwnerTaskScope(
            user_id="user-17",
            task_id="task-original-9",
            operation="video.single",
        )
        self.credential_calls: list[tuple[CredentialBinding, str]] = []
        self.clients: list[FakeClient] = []
        self.providers: list[ManxiaobaiVideoProvider] = []

    def make_snapshot(
        self,
        *,
        model_params: dict[str, object] | None = None,
        endpoint_origin: str = ORIGIN,
        model_category: str = "video",
    ) -> FrozenExecutionSnapshot:
        model_ref = ModelReference(
            owner_id="user-17",
            provider=PROVIDER_ID,
            model_id="catalog-model-id",
        )
        return FrozenExecutionSnapshot(
            model_ref=model_ref,
            model_category=model_category,
            model_name="Catalog display name",
            model_params=model_params or {},
            prompt_ref="prompt-original-1",
            effective_prompt="A moonlit train crosses the valley",
            effective_context="frozen context",
            server_quote=10,
            reference_snapshot_ref="reference-set-original",
            target_snapshot=TargetSnapshot(kind="none"),
            credential_binding_ref="credential-binding-ref-1",
            grant_snapshot=FrozenGrantSnapshot(actor_id="user-17"),
            adapter_config_versions=(("manxiaobai", "cfg-v1"),),
            endpoint_policy=EndpointPolicy(provider_origin=endpoint_origin),
        )

    @staticmethod
    def make_intent() -> CommittedIntent:
        return CommittedIntent(
            step_id="step-original-4",
            attempt_id="attempt-original-3",
            external_submission_id="submission-original-2",
            provider=PROVIDER_ID,
            model_id="catalog-model-id",
            request_digest="request-digest-original",
            snapshot_ref="execution-snapshot-ref-1",
            committed_at=NOW,
            first_intent_at=NOW,
            deadline_at=NOW + timedelta(minutes=10),
        )

    @staticmethod
    def make_binding(*, endpoint_origin: str = ORIGIN) -> CredentialBinding:
        return CredentialBinding(
            source="configured_provider_key",
            row_id="credential-row-1",
            owner_or_system="user-17",
            provider=PROVIDER_ID,
            original_principal_id="principal-original-1",
            endpoint_origin=endpoint_origin,
            allowed_content_paths=(),
            binding_version="binding-v1",
        )

    def make_provider(
        self,
        *,
        response: FakeResponse | None = None,
        client_error: BaseException | None = None,
        client_close_error: BaseException | None = None,
        resolver_result: BoundCredential | str = BoundCredential(
            provider=PROVIDER_ID,
            principal_id="principal-original-1",
            secret=SECRET,
        ),
        resolver_error: Exception | None = None,
        snapshot: FrozenExecutionSnapshot | None = None,
        binding: CredentialBinding | None = None,
        model_reference: ModelReference | None = None,
        owner_scope: OwnerTaskScope | None = None,
        wire_model: str | None = "grok-video-1.5-preview",
        client_factory: object | None = None,
        client_request_delay: float = 0.0,
        client_close_delay: float = 0.0,
        intent: CommittedIntent | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> ManxiaobaiVideoProvider:
        selected_snapshot = snapshot or self.snapshot
        selected_binding = binding or self.binding
        selected_model_reference = model_reference or selected_snapshot.model_ref
        selected_scope = owner_scope or self.scope
        selected_intent = intent or self.intent

        def resolver(
            original: CredentialBinding, purpose: str
        ) -> BoundCredential | str:
            self.credential_calls.append((original, purpose))
            if resolver_error is not None:
                raise resolver_error
            return resolver_result

        def default_client_factory() -> FakeClient:
            client = FakeClient(
                response,
                error=client_error,
                close_error=client_close_error,
                request_delay=client_request_delay,
                close_delay=client_close_delay,
            )
            self.clients.append(client)
            return client

        factory = client_factory or default_client_factory
        provider = ManxiaobaiVideoProvider(
            owner_scope=selected_scope,
            snapshot_ref="execution-snapshot-ref-1",
            frozen_snapshot=selected_snapshot,
            committed_intent=selected_intent,
            original_model_reference=selected_model_reference,
            trusted_model_owner_id=selected_model_reference.owner_id,
            credential_binding_ref=selected_snapshot.credential_binding_ref,
            original_credential_binding=selected_binding,
            trusted_binding_owner_or_system=selected_binding.owner_or_system,
            trusted_binding_principal_id=selected_binding.original_principal_id,
            wire_model=wire_model,
            credential_resolver=resolver,  # type: ignore[arg-type]
            http_client_factory=factory,  # type: ignore[arg-type]
            clock=clock or (lambda: NOW),
        )
        self.providers.append(provider)
        return provider

    def poll_reservation(
        self, external_id: str = "provider-video-1"
    ) -> CommittedPollReservation:
        poll = PollReservation(
            reservation_id="poll-reservation-1",
            group_key="group-original-1",
            sequence=1,
            attempt_id=self.intent.attempt_id,
            step_id=self.intent.step_id,
            submission_id=self.intent.external_submission_id,
            external_id=external_id,
            payload_digest=self.intent.request_digest,
            committed_poll_count=1,
            reserved_at=NOW,
            timeout_at=NOW,
            next_poll_at=NOW,
            token="poll-token-opaque",
            state="reserved",
        )
        return CommittedPollReservation(reservation=poll, confirmed_at=NOW)

    def submitted_ref(self, external_id: str = "provider-video-1") -> ExternalOperationRef:
        return ExternalOperationRef(
            intent_id=self.intent.external_submission_id,
            attempt_id=self.intent.attempt_id,
            provider=PROVIDER_ID,
            external_id=external_id,
            create_outcome="submitted",
            last_safe_provider_state="submitted",
        )

    def test_constructor_is_inert_and_create_uses_legacy_json_defaults(self) -> None:
        response = FakeResponse(200, b'{"id":"provider-video-1"}')
        provider = self.make_provider(response=response, wire_model=None)
        self.assertEqual(self.credential_calls, [])
        self.assertEqual(self.clients, [])

        result = provider.submit_async_media(self.snapshot, (), self.intent)

        self.assertEqual(result.external_id, "provider-video-1")
        self.assertEqual(result.create_outcome, "submitted")
        client = self.clients[0]
        method, url, options = client.calls[0]
        self.assertEqual((method, url), ("POST", f"{ORIGIN}/v1/videos"))
        self.assertEqual(options["timeout_seconds"], CREATE_TIMEOUT_SECONDS)
        self.assertFalse(options["follow_redirects"])
        self.assertEqual(options["headers"], {
            "Authorization": f"Bearer {SECRET}",
            "Content-Type": "application/json",
        })
        self.assertEqual(
            json.loads(options["content"]),
            {
                "model": "grok-video-1.0",
                "prompt": self.snapshot.effective_prompt,
                "seconds": "6",
                "aspect_ratio": "16:9",
                "resolution": "720p",
            },
        )
        self.assertEqual(self.credential_calls, [(self.binding, "create")])
        self.assertTrue(response.iterator_closed)
        self.assertTrue(response.closed)
        self.assertTrue(client.closed)

    def test_create_deadline_bounds_body_read_and_normal_resource_cleanup(self) -> None:
        cases = (
            ("body", FakeResponse(200, b'{"id":"slow-body"}', iterator_next_delay=0.2), 0.0),
            ("cleanup", FakeResponse(200, b'{"id":"slow-cleanup"}'), 0.2),
        )
        for stage, response, close_delay in cases:
            with self.subTest(stage=stage):
                self.clients.clear()
                self.credential_calls.clear()
                intent = replace(
                    self.intent, deadline_at=NOW + timedelta(milliseconds=40)
                )
                provider = self.make_provider(
                    response=response,
                    intent=intent,
                    client_close_delay=close_delay,
                )

                result = provider.submit_async_media(self.snapshot, (), intent)

                if stage == "cleanup":
                    self.assertEqual(result.create_outcome, "submitted")
                    self.assertEqual(result.external_id, "slow-cleanup")
                    repeated = provider.submit_async_media(
                        self.snapshot, (), intent
                    )
                    self.assertEqual(repeated.external_id, "slow-cleanup")
                else:
                    self.assertEqual(result.create_outcome, "unknown")
                    self.assertIsNone(result.external_id)
                self.assertEqual(len(self.clients), 1)
                self.assertEqual(len(self.clients[0].calls), 1)
                self.assertEqual(self.clients[0].calls[0][0], "POST")
                self.assertTrue(response.closed)
                self.assertTrue(self.clients[0].closed)

    def test_body_timeout_bounds_following_normal_resource_cleanup(self) -> None:
        response = FakeResponse(
            200,
            b'{"id":"body-did-not-finish"}',
            iterator_next_delay=0.2,
            response_close_delay=0.2,
        )
        intent = replace(
            self.intent, deadline_at=NOW + timedelta(milliseconds=40)
        )
        provider = self.make_provider(
            response=response,
            intent=intent,
            client_close_delay=0.2,
        )

        started_at = time.perf_counter()
        result = provider.submit_async_media(self.snapshot, (), intent)
        elapsed = time.perf_counter() - started_at

        self.assertEqual(result.create_outcome, "unknown")
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(len(self.clients[0].calls), 1)
        self.assertGreaterEqual(response.close_attempts, 1)
        self.assertGreaterEqual(self.clients[0].close_attempts, 1)
        self.assertLess(elapsed, 0.15)

    def test_create_deadline_bounds_headers_body_and_all_normal_cleanup(self) -> None:
        cases = (
            ("headers", FakeResponse(200, b'{"id":"unused"}'), 0.2, 0.0),
            ("body", FakeResponse(200, b'{"id":"unused"}', iterator_next_delay=0.2), 0.0, 0.0),
            ("response cleanup", FakeResponse(200, b'{"id":"unused"}', response_close_delay=0.2), 0.0, 0.0),
            ("client cleanup", FakeResponse(200, b'{"id":"unused"}'), 0.0, 0.2),
        )
        for stage, response, request_delay, client_close_delay in cases:
            with self.subTest(stage=stage):
                self.clients.clear()
                self.credential_calls.clear()
                intent = replace(
                    self.intent, deadline_at=NOW + timedelta(milliseconds=50)
                )
                provider = self.make_provider(
                    response=response,
                    intent=intent,
                    client_request_delay=request_delay,
                    client_close_delay=client_close_delay,
                )

                result = provider.submit_async_media(self.snapshot, (), intent)

                if stage in ("response cleanup", "client cleanup"):
                    self.assertEqual(result.create_outcome, "submitted")
                    self.assertEqual(result.external_id, "unused")
                    repeated = provider.submit_async_media(
                        self.snapshot, (), intent
                    )
                    self.assertEqual(repeated.external_id, "unused")
                else:
                    self.assertEqual(result.create_outcome, "unknown")
                    self.assertIsNone(result.external_id)
                self.assertEqual(len(self.clients), 1)
                self.assertEqual(len(self.clients[0].calls), 1)
                self.assertEqual(self.clients[0].calls[0][0], "POST")
                self.assertAlmostEqual(
                    self.clients[0].calls[0][2]["timeout_seconds"], 0.05
                )
                if stage != "headers":
                    self.assertTrue(response.closed)
                self.assertTrue(self.clients[0].closed)

    def test_query_deadline_bounds_body_and_cleanup_while_preserving_original_id(self) -> None:
        cases = (
            ("body", FakeResponse(200, b'{"status":"processing"}', iterator_next_delay=0.2), 0.0),
            ("response cleanup", FakeResponse(200, b'{"status":"processing"}', response_close_delay=0.2), 0.0),
            ("client cleanup", FakeResponse(200, b'{"status":"processing"}'), 0.2),
        )
        for stage, response, client_close_delay in cases:
            with self.subTest(stage=stage):
                self.clients.clear()
                self.credential_calls.clear()
                intent = replace(
                    self.intent, deadline_at=NOW + timedelta(milliseconds=50)
                )
                provider = self.make_provider(
                    response=response,
                    intent=intent,
                    client_close_delay=client_close_delay,
                )
                original_id = "original/video-id"
                original_ref = self.submitted_ref(original_id)

                with self.assertRaises(GenerationError) as raised:
                    provider.query_existing(self.snapshot, original_ref)

                self.assertEqual(raised.exception.safe_detail, "provider query is unavailable")
                self.assertEqual(len(self.clients), 1)
                client = self.clients[0]
                self.assertEqual(len(client.calls), 1)
                method, url, options = client.calls[0]
                self.assertEqual(method, "GET")
                self.assertEqual(url, f"{ORIGIN}/v1/videos/{quote(original_id, safe='')}")
                self.assertEqual(options["headers"], {"Authorization": f"Bearer {SECRET}"})
                self.assertAlmostEqual(options["timeout_seconds"], 0.05)
                self.assertEqual(
                    self.credential_calls,
                    [(self.binding, "query")],
                )
                self.assertTrue(response.closed)
                self.assertTrue(client.closed)

    def test_deadline_caps_create_and_query_timeout_arguments(self) -> None:
        intent = replace(self.intent, deadline_at=NOW + timedelta(seconds=12))
        create = self.make_provider(
            response=FakeResponse(200, b'{"id":"created"}'), intent=intent
        )
        create.submit_async_media(self.snapshot, (), intent)
        self.assertEqual(
            self.clients[0].calls[0][2]["timeout_seconds"], 12.0
        )

        self.clients.clear()
        query = self.make_provider(
            response=FakeResponse(200, b'{"status":"processing"}'), intent=intent
        )
        query.query_existing(self.snapshot, self.submitted_ref("original-id"))
        self.assertEqual(
            self.clients[0].calls[0][2]["timeout_seconds"], 12.0
        )

    def test_invalid_or_expired_deadlines_reject_before_client_creation(self) -> None:
        def broken_clock() -> datetime:
            raise RuntimeError("private clock details")

        cases = (
            ("broken clock", NOW + timedelta(minutes=1), broken_clock),
            ("naive clock", NOW + timedelta(minutes=1), lambda: datetime(2026, 10, 12, 12, 0)),
            ("naive deadline", datetime(2026, 10, 12, 12, 1), lambda: NOW),
            ("expired deadline", NOW, lambda: NOW),
        )
        for name, deadline, clock in cases:
            with self.subTest(case=name):
                self.clients.clear()
                self.credential_calls.clear()
                intent = replace(self.intent, deadline_at=deadline)
                provider = self.make_provider(
                    response=FakeResponse(200, b'{"id":"unused"}'),
                    intent=intent,
                    clock=clock,
                )

                result = provider.submit_async_media(self.snapshot, (), intent)
                self.assertEqual(result.create_outcome, "unknown")
                with self.assertRaises(GenerationError):
                    provider.query_existing(self.snapshot, self.submitted_ref("original-id"))

                self.assertEqual(self.clients, [])

    def test_create_http_408_is_unknown_and_never_reposted(self) -> None:
        response = FakeResponse(408, b'{"id":"not-known"}')
        provider = self.make_provider(response=response)

        result = provider.submit_async_media(self.snapshot, (), self.intent)
        repeated = provider.submit_async_media(self.snapshot, (), self.intent)

        self.assertEqual(result.create_outcome, "unknown")
        self.assertIsNone(result.external_id)
        self.assertEqual(repeated, result)
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(len(self.clients[0].calls), 1)
        self.assertFalse(response.iterator_closed)

    def test_create_keeps_catalog_identity_separate_from_wire_model_and_settings(self) -> None:
        snapshot = self.make_snapshot(
            model_params={"duration": 8, "size": "9:16", "resolution": "1080p"}
        )
        response = FakeResponse(200, b'{"task_id":"provider-task-2"}')
        provider = self.make_provider(snapshot=snapshot, response=response)

        result = provider.submit_async_media(snapshot, (), self.intent)

        self.assertEqual(snapshot.model_ref.model_id, "catalog-model-id")
        self.assertEqual(result.external_id, "provider-task-2")
        request = self.clients[0].calls[0]
        payload = json.loads(request[2]["content"])
        self.assertEqual(payload["model"], "grok-video-1.5-preview")
        self.assertNotEqual(payload["model"], snapshot.model_ref.model_id)
        self.assertEqual(payload["seconds"], "8")
        self.assertEqual(payload["aspect_ratio"], "9:16")
        self.assertEqual(payload["resolution"], "1080p")

    def test_create_preserves_reference_input_shape_without_uploading(self) -> None:
        refs = (
            ReferenceInput(kind="managed_url", locator="https://cdn.example/a.png"),
            ReferenceInput(kind="public_http", locator="https://cdn.example/b.png"),
        )
        response = FakeResponse(200, b'{"id":"provider-video-3"}')
        provider = self.make_provider(response=response)

        provider.submit_async_media(self.snapshot, refs, self.intent)

        payload = json.loads(self.clients[0].calls[0][2]["content"])
        self.assertEqual(
            payload["input_reference"], "https://cdn.example/a.png"
        )
        self.assertEqual(len(self.clients[0].calls), 1)
        self.assertEqual(self.clients[0].calls[0][0], "POST")

    def test_create_keeps_seven_references_for_non_15_wire_model(self) -> None:
        refs = tuple(
            ReferenceInput(kind="public_http", locator=f"https://cdn.example/{i}.png")
            for i in range(8)
        )
        response = FakeResponse(200, b'{"id":"provider-video-4"}')
        provider = self.make_provider(response=response, wire_model="grok-video-1.0")

        provider.submit_async_media(self.snapshot, refs, self.intent)

        payload = json.loads(self.clients[0].calls[0][2]["content"])
        self.assertEqual(
            payload["input_reference"],
            [f"https://cdn.example/{i}.png" for i in range(7)],
        )

    def test_intent_mismatch_fails_before_resolver_or_http(self) -> None:
        provider = self.make_provider()
        altered_intent = CommittedIntent(
            step_id=self.intent.step_id,
            attempt_id="different-attempt",
            external_submission_id=self.intent.external_submission_id,
            provider=self.intent.provider,
            model_id=self.intent.model_id,
            request_digest=self.intent.request_digest,
            snapshot_ref=self.intent.snapshot_ref,
            committed_at=self.intent.committed_at,
            first_intent_at=self.intent.first_intent_at,
            deadline_at=self.intent.deadline_at,
        )

        with self.assertRaises(GenerationError):
            provider.submit_async_media(self.snapshot, (), altered_intent)

        self.assertEqual(self.credential_calls, [])
        self.assertEqual(self.clients, [])

    def test_public_resolver_accepts_original_binding_without_content_fallback(self) -> None:
        provider = self.make_provider()

        credential = provider.resolve_original_credential(self.binding, "query")

        self.assertEqual(credential.provider, PROVIDER_ID)
        self.assertEqual(credential.principal_id, self.binding.original_principal_id)
        self.assertEqual(self.credential_calls, [(self.binding, "query")])
        with self.assertRaises(GenerationError):
            provider.resolve_original_credential(self.binding, "content")
        self.assertEqual(self.credential_calls, [(self.binding, "query")])
        self.assertEqual(self.clients, [])

    def test_unsupported_reference_kinds_fail_before_resolver_or_http(self) -> None:
        provider = self.make_provider()
        refs = (
            ReferenceInput(kind="bytes", content=b"image bytes", media_type="image/png"),
        )

        with self.assertRaises(GenerationError) as raised:
            provider.submit_async_media(self.snapshot, refs, self.intent)

        self.assertEqual(raised.exception.code, "invalid_generation_request")
        self.assertEqual(self.credential_calls, [])
        self.assertEqual(self.clients, [])

    def test_create_uses_id_priority_and_never_aliases_request_id(self) -> None:
        response = FakeResponse(
            200,
            b'{"id":"video-id","task_id":"task-id","request_id":"request-id"}',
        )
        provider = self.make_provider(response=response)
        result = provider.submit_async_media(self.snapshot, (), self.intent)
        self.assertEqual(result.external_id, "video-id")

        second_response = FakeResponse(200, b'{"request_id":"request-id"}')
        second = self.make_provider(response=second_response)
        unknown = second.submit_async_media(self.snapshot, (), self.intent)
        self.assertEqual(unknown.create_outcome, "unknown")
        self.assertIsNone(unknown.external_id)

    def test_ambiguous_create_is_unknown_and_is_never_reposted(self) -> None:
        provider = self.make_provider(client_error=TimeoutError("opaque timeout"))

        result = provider.submit_async_media(self.snapshot, (), self.intent)
        repeated = provider.submit_async_media(self.snapshot, (), self.intent)

        self.assertEqual(result.create_outcome, "unknown")
        self.assertIsNone(result.external_id)
        self.assertEqual(repeated, result)
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(len(self.clients[0].calls), 1)
        self.assertEqual(self.clients[0].calls[0][0], "POST")
        self.assertTrue(self.clients[0].closed)

    def test_malformed_missing_or_secret_echo_create_ids_are_unknown(self) -> None:
        bodies = (
            b"not-json",
            b'{"id":""}',
            json.dumps({"id": SECRET}).encode(),
        )
        for body in bodies:
            with self.subTest(body=body):
                self.clients.clear()
                provider = self.make_provider(response=FakeResponse(200, body))
                result = provider.submit_async_media(self.snapshot, (), self.intent)
                self.assertEqual(result.create_outcome, "unknown")
                self.assertIsNone(result.external_id)

    def test_known_create_4xx_is_definitive_without_reading_body(self) -> None:
        response = FakeResponse(422, b'{"message":"private provider detail"}')
        provider = self.make_provider(response=response)

        result = provider.submit_async_media(self.snapshot, (), self.intent)

        self.assertEqual(result.create_outcome, "definitive_failure")
        self.assertIsNone(result.external_id)
        self.assertIsNone(response.iterator)
        self.assertTrue(response.closed)
        self.assertTrue(self.clients[0].closed)

    def test_create_loop_guard_runs_before_resolver_and_client(self) -> None:
        provider = self.make_provider(response=FakeResponse(200, b'{"id":"id"}'))

        async def submit_inside_loop() -> None:
            with self.assertRaises(GenerationError):
                provider.submit_async_media(self.snapshot, (), self.intent)

        asyncio.run(submit_inside_loop())
        self.assertEqual(self.credential_calls, [])
        self.assertEqual(self.clients, [])

    def test_query_uses_known_original_id_and_projects_signed_locator(self) -> None:
        signed_url = "https://cdn.example.test/out.mp4?signature=AbC%2F1&expires=42"
        response = FakeResponse(
            200,
            json.dumps({"status": "done", "video": signed_url}).encode(),
            iterator_mode="direct",
        )
        provider = self.make_provider(response=response)

        observation = provider.query_existing(
            self.snapshot,
            self.submitted_ref("provider/video +1"),
            self.poll_reservation("provider/video +1"),
        )

        method, url, options = self.clients[0].calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, f"{ORIGIN}/v1/videos/{quote('provider/video +1', safe='')}")
        self.assertEqual(options["timeout_seconds"], QUERY_TIMEOUT_SECONDS)
        self.assertFalse(options["follow_redirects"])
        self.assertEqual(options["content"], None)
        self.assertEqual(options["headers"], {"Authorization": f"Bearer {SECRET}"})
        self.assertEqual(observation.owner_scope, self.scope)
        self.assertEqual(observation.intent_id, self.intent.external_submission_id)
        self.assertEqual(observation.attempt_id, self.intent.attempt_id)
        self.assertEqual(observation.external_ref.external_id, "provider/video +1")
        self.assertEqual(observation.status, "completed")
        self.assertEqual(observation.media_locator, signed_url)
        self.assertEqual(observation.payload["source_state"], "completed")
        self.assertEqual(observation.payload["response_digest"], observation.payload_digest)
        self.assertEqual(response.chunk_size, RESPONSE_CHUNK_BYTES)
        self.assertTrue(response.iterator_closed)
        self.assertTrue(response.closed)
        self.assertTrue(self.clients[0].closed)

    def test_query_preserves_independent_signed_query_keys_byte_for_byte(self) -> None:
        locators = (
            "https://cdn.example/out.mp4?access_token=cdn-only&expires=42",
            "https://cdn.example/out.mp4?api_key=cdn-key&signature=A%2Fb",
        )
        for locator in locators:
            with self.subTest(locator=locator):
                response = FakeResponse(
                    200, json.dumps({"status": "done", "video": locator}).encode()
                )
                provider = self.make_provider(response=response)

                observation = provider.query_existing(
                    self.snapshot, self.submitted_ref()
                )

                self.assertEqual(observation.media_locator, locator)
                self.assertNotIn("locator_rejected", observation.payload)

    def test_nonvideo_trusted_anchors_are_rejected_before_any_effect(self) -> None:
        nonvideo_snapshot = self.make_snapshot(model_category="image")
        provider = self.make_provider(
            snapshot=nonvideo_snapshot,
            response=FakeResponse(200, b'{"status":"pending"}'),
        )

        with self.assertRaises(GenerationError):
            provider.submit_async_media(nonvideo_snapshot, (), self.intent)
        with self.assertRaises(GenerationError):
            provider.query_existing(nonvideo_snapshot, self.submitted_ref())
        with self.assertRaises(GenerationError):
            provider.resolve_original_credential(self.binding, "query")

        self.assertEqual(self.credential_calls, [])
        self.assertEqual(self.clients, [])

    def test_equal_reconstructed_snapshot_and_intent_remain_accepted(self) -> None:
        create_provider = self.make_provider(
            response=FakeResponse(200, b'{"id":"created-video"}')
        )
        reconstructed_snapshot = replace(self.snapshot)
        reconstructed_intent = replace(self.intent)

        created = create_provider.submit_async_media(
            reconstructed_snapshot, (), reconstructed_intent
        )
        query_provider = self.make_provider(
            response=FakeResponse(200, b'{"status":"pending"}')
        )
        observation = query_provider.query_existing(
            replace(self.snapshot), self.submitted_ref()
        )

        self.assertEqual(created.create_outcome, "submitted")
        self.assertEqual(observation.status, "pending")
        self.assertEqual(len(self.credential_calls), 2)
        self.assertEqual(len(self.clients), 2)

    def test_query_never_treats_provider_state_as_task_or_financial_truth(self) -> None:
        response = FakeResponse(
            200,
            b'{"status":"failed","error":{"message":"provider secret detail"},'
            b'"fail_reason":"raw reason","message":"raw message"}',
        )
        provider = self.make_provider(response=response)

        observation = provider.query_existing(self.snapshot, self.submitted_ref())

        self.assertEqual(observation.status, "failed")
        self.assertEqual(observation.safe_error, "provider reported failure")
        self.assertNotIn("provider secret detail", repr(observation))
        self.assertNotIn("raw reason", repr(observation))
        self.assertNotIn("raw message", repr(observation))
        self.assertNotIn(SECRET, repr(observation))
        self.assertIsNone(observation.media_locator)
        self.assertNotIn("validated", repr(observation).lower())

    def test_query_source_completion_order_and_no_url_fact_are_preserved(self) -> None:
        response = FakeResponse(
            200,
            b'{"status":"failed","code":1,'
            b'"video":"https://cdn.example.test/video.mp4",'
            b'"error":{"message":"not persisted"}}',
        )
        provider = self.make_provider(response=response)

        observation = provider.query_existing(self.snapshot, self.submitted_ref())

        self.assertEqual(observation.status, "completed")
        self.assertFalse(observation.payload["bound_content_needed"])
        self.assertEqual(observation.payload["source_code"], 1)
        self.assertEqual(
            observation.media_locator, "https://cdn.example.test/video.mp4"
        )

        no_url = FakeResponse(200, b'{"status":"completed","video":{}}')
        other = self.make_provider(response=no_url)
        no_url_observation = other.query_existing(self.snapshot, self.submitted_ref())
        self.assertEqual(no_url_observation.status, "completed")
        self.assertTrue(no_url_observation.payload["bound_content_needed"])
        self.assertIsNone(no_url_observation.media_locator)

    def test_query_drops_plain_percent_and_double_percent_secret_echo(self) -> None:
        encoded = quote(SECRET, safe="")
        double_encoded = quote(encoded, safe="")
        for leaked in (SECRET, encoded, double_encoded):
            with self.subTest(leaked=leaked):
                response = FakeResponse(
                    200,
                    json.dumps(
                        {
                            "status": "completed",
                            "video": f"https://cdn.example/{leaked}",
                        }
                    ).encode(),
                )
                provider = self.make_provider(response=response)
                observation = provider.query_existing(self.snapshot, self.submitted_ref())
                self.assertIsNone(observation.media_locator)
                self.assertTrue(observation.payload["locator_rejected"])
                self.assertNotIn(SECRET, repr(observation))

    def test_query_rejects_invalid_url_shapes_but_keeps_signature_parameters(self) -> None:
        bad_urls = (
            "ftp://cdn.example/out.mp4",
            "https://user:pass@cdn.example/out.mp4",
            "https://cdn.example/out.mp4#fragment",
            "https://cdn.example/out.mp4\nsecond-line",
            "https://cdn.example/Authorization%3A%20Bearer%20token",
            "https://cdn.example/%7B%22error%22%3A%22provider failure%22%7D",
            "https://cdn.example/" + "x" * 8200,
        )
        for locator in bad_urls:
            with self.subTest(locator=locator[:80]):
                response = FakeResponse(
                    200,
                    json.dumps({"status": "completed", "video": locator}).encode(),
                )
                provider = self.make_provider(response=response)
                observation = provider.query_existing(self.snapshot, self.submitted_ref())
                self.assertIsNone(observation.media_locator)
                self.assertTrue(observation.payload["locator_rejected"])

        good_url = "https://cdn.example/out.mp4?X-Amz-Signature=abc%2Fdef&expires=1"
        response = FakeResponse(200, json.dumps({"status": "done", "video": good_url}).encode())
        provider = self.make_provider(response=response)
        observation = provider.query_existing(self.snapshot, self.submitted_ref())
        self.assertEqual(observation.media_locator, good_url)

    def test_query_unknown_or_mismatched_identity_fails_before_resolver_and_get(self) -> None:
        provider = self.make_provider()
        invalid_refs = (
            ExternalOperationRef(
                intent_id="another-submission",
                attempt_id=self.intent.attempt_id,
                provider=PROVIDER_ID,
                external_id="provider-video-1",
                create_outcome="submitted",
            ),
            ExternalOperationRef(
                intent_id=self.intent.external_submission_id,
                attempt_id=self.intent.attempt_id,
                provider=PROVIDER_ID,
                external_id=None,
                create_outcome="unknown",
            ),
        )
        for external_ref in invalid_refs:
            with self.subTest(external_ref=external_ref):
                with self.assertRaises(GenerationError):
                    provider.query_existing(self.snapshot, external_ref)
        self.assertEqual(self.credential_calls, [])
        self.assertEqual(self.clients, [])

        sanitized = self.make_provider(response=FakeResponse(200, b'{"status":"pending"}'))
        unsafe_state_ref = ExternalOperationRef(
            intent_id=self.intent.external_submission_id,
            attempt_id=self.intent.attempt_id,
            provider=PROVIDER_ID,
            external_id="provider-video-1",
            create_outcome="submitted",
            last_safe_provider_state=f"Bearer {SECRET}",
        )
        observation = sanitized.query_existing(self.snapshot, unsafe_state_ref)
        self.assertEqual(observation.external_ref.last_safe_provider_state, "pending")
        self.assertNotIn(SECRET, repr(observation))

    def test_query_rejects_wrong_snapshot_reservation_and_binding_before_effects(self) -> None:
        provider = self.make_provider()
        changed_snapshot = self.make_snapshot(model_params={"duration": 10})
        with self.assertRaises(GenerationError):
            provider.query_existing(changed_snapshot, self.submitted_ref())
        with self.assertRaises(GenerationError):
            provider.query_existing(
                self.snapshot,
                self.submitted_ref(),
                self.poll_reservation("different-video-id"),
            )
        self.assertEqual(self.credential_calls, [])
        self.assertEqual(self.clients, [])

        wrong_binding = self.make_binding()
        wrong_binding = CredentialBinding(
            source=wrong_binding.source,
            row_id="other-row",
            owner_or_system=wrong_binding.owner_or_system,
            provider=wrong_binding.provider,
            original_principal_id=wrong_binding.original_principal_id,
            endpoint_origin=wrong_binding.endpoint_origin,
            allowed_content_paths=wrong_binding.allowed_content_paths,
            binding_version=wrong_binding.binding_version,
        )
        with self.assertRaises(GenerationError):
            provider.resolve_original_credential(wrong_binding, "query")
        self.assertEqual(self.credential_calls, [])

    def test_query_known_http_error_is_constrained_and_closes_client(self) -> None:
        response = FakeResponse(503, b'{"error":"private body"}')
        provider = self.make_provider(response=response)

        with self.assertRaises(GenerationError) as raised:
            observation = provider.query_existing(self.snapshot, self.submitted_ref())
        self.assertEqual(raised.exception.safe_detail, "provider query returned HTTP 503")
        self.assertNotIn("private body", str(raised.exception))
        self.assertIsNone(response.iterator)
        self.assertTrue(response.closed)
        self.assertTrue(self.clients[0].closed)

    def test_wrong_resolved_principal_and_origin_fail_before_http(self) -> None:
        provider = self.make_provider(
            resolver_result=BoundCredential(
                provider=PROVIDER_ID,
                principal_id="different-principal",
                secret=SECRET,
            ),
            response=FakeResponse(200, b'{"id":"unused"}'),
        )
        with self.assertRaises(GenerationError):
            provider.submit_async_media(self.snapshot, (), self.intent)
        self.assertEqual(self.clients, [])

        wrong_origin = self.make_snapshot(endpoint_origin="https://other.example.test")
        mismatched = self.make_provider(snapshot=wrong_origin)
        with self.assertRaises(GenerationError):
            mismatched.submit_async_media(wrong_origin, (), self.intent)
        self.assertEqual(self.credential_calls, [(self.binding, "create")])

    def test_response_limit_and_cancellation_close_all_resources(self) -> None:
        chunks = [
            b"x" * RESPONSE_CHUNK_BYTES
            for _ in range(MAX_RESPONSE_BYTES // RESPONSE_CHUNK_BYTES + 1)
        ]
        response = FakeResponse(200, chunks=chunks, iterator_mode="direct")
        provider = self.make_provider(response=response)
        result = provider.submit_async_media(self.snapshot, (), self.intent)
        self.assertEqual(result.create_outcome, "unknown")
        self.assertTrue(response.iterator_closed)
        self.assertTrue(response.closed)
        self.assertTrue(self.clients[0].closed)

        cancelled = self.make_provider(client_error=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            cancelled.submit_async_media(self.snapshot, (), self.intent)
        self.assertTrue(self.clients[0].closed)
        repeated = cancelled.submit_async_media(self.snapshot, (), self.intent)
        self.assertEqual(repeated.create_outcome, "unknown")
        self.assertEqual(len(self.clients[0].calls), 1)

    def test_query_transport_failure_is_safe_and_never_retried(self) -> None:
        provider = self.make_provider(client_error=TimeoutError("private timeout text"))

        with self.assertRaises(GenerationError) as raised:
            provider.query_existing(self.snapshot, self.submitted_ref())

        self.assertEqual(raised.exception.safe_detail, "provider query is unavailable")
        self.assertNotIn("private timeout text", str(raised.exception))
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(len(self.clients[0].calls), 1)
        self.assertEqual(self.clients[0].calls[0][0], "GET")
        self.assertTrue(self.clients[0].closed)

    def test_close_cancellation_attempts_all_cleanup_without_reposting(self) -> None:
        cases = (
            (
                "response",
                FakeResponse(
                    200,
                    b'{"id":"created-video"}',
                    response_close_error=asyncio.CancelledError(),
                ),
            ),
            (
                "iterator",
                FakeResponse(
                    200,
                    b'{"id":"created-video"}',
                    iterator_close_error=asyncio.CancelledError(),
                ),
            ),
        )
        for name, response in cases:
            with self.subTest(resource=name):
                self.clients.clear()
                self.credential_calls.clear()
                provider = self.make_provider(response=response)

                with self.assertRaises(asyncio.CancelledError):
                    provider.submit_async_media(self.snapshot, (), self.intent)

                self.assertEqual(len(self.clients), 1)
                client = self.clients[0]
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(client.close_attempts, 1)
                self.assertTrue(client.closed)
                self.assertTrue(response.closed)
                if response.iterator is not None:
                    self.assertTrue(response.iterator_closed)

                unknown = provider.submit_async_media(
                    self.snapshot, (), self.intent
                )
                self.assertEqual(unknown.create_outcome, "unknown")
                self.assertEqual(len(client.calls), 1)

    def test_failed_response_then_close_cancellation_propagates(self) -> None:
        for method in ("create", "query"):
            with self.subTest(method=method):
                self.clients.clear()
                self.credential_calls.clear()
                response = FakeResponse(
                    200,
                    headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)},
                    response_close_error=asyncio.CancelledError(
                        "response close cancelled"
                    ),
                )
                provider = self.make_provider(response=response)

                with self.assertRaises(asyncio.CancelledError):
                    if method == "create":
                        provider.submit_async_media(self.snapshot, (), self.intent)
                    else:
                        provider.query_existing(self.snapshot, self.submitted_ref())

                self.assertEqual(len(self.clients), 1)
                client = self.clients[0]
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(
                    client.calls[0][0],
                    "POST" if method == "create" else "GET",
                )
                self.assertEqual(client.close_attempts, 1)
                self.assertTrue(client.closed)
                self.assertTrue(response.closed)
                self.assertIsNone(response.iterator)

                if method == "create":
                    unknown = provider.submit_async_media(
                        self.snapshot, (), self.intent
                    )
                    self.assertEqual(unknown.create_outcome, "unknown")
                    self.assertEqual(len(self.clients), 1)
                    self.assertEqual(len(client.calls), 1)

    def test_original_request_cancellation_keeps_priority_over_cleanup_cancel(self) -> None:
        original_cancel = asyncio.CancelledError("request cancelled")
        cleanup_cancel = asyncio.CancelledError("client close cancelled")
        provider = self.make_provider(
            client_error=original_cancel,
            client_close_error=cleanup_cancel,
        )

        with self.assertRaises(asyncio.CancelledError) as raised:
            provider.submit_async_media(self.snapshot, (), self.intent)

        self.assertIs(raised.exception, original_cancel)
        self.assertEqual(len(self.clients), 1)
        client = self.clients[0]
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.close_attempts, 1)
        self.assertTrue(client.closed)
        unknown = provider.submit_async_media(self.snapshot, (), self.intent)
        self.assertEqual(unknown.create_outcome, "unknown")
        self.assertEqual(len(client.calls), 1)

    def test_iterator_read_cancellation_keeps_priority_over_close_cancel(self) -> None:
        for method in ("create", "query"):
            with self.subTest(method=method):
                self.clients.clear()
                self.credential_calls.clear()
                original_cancel = asyncio.CancelledError(
                    f"{method} iterator read cancelled"
                )
                close_cancel = asyncio.CancelledError(
                    f"{method} iterator close cancelled"
                )
                body = (
                    b'{"id":"created-video"}'
                    if method == "create"
                    else b'{"status":"pending"}'
                )
                response = FakeResponse(
                    200,
                    body,
                    iterator_next_error=original_cancel,
                    iterator_close_error=close_cancel,
                )
                provider = self.make_provider(response=response)

                with self.assertRaises(asyncio.CancelledError) as raised:
                    if method == "create":
                        provider.submit_async_media(self.snapshot, (), self.intent)
                    else:
                        provider.query_existing(self.snapshot, self.submitted_ref())

                self.assertIs(raised.exception, original_cancel)
                self.assertEqual(len(self.clients), 1)
                client = self.clients[0]
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(
                    client.calls[0][0],
                    "POST" if method == "create" else "GET",
                )
                self.assertEqual(response.iterator.close_attempts, 1)
                self.assertTrue(response.iterator.closed)
                self.assertEqual(response.close_attempts, 1)
                self.assertTrue(response.closed)
                self.assertEqual(client.close_attempts, 1)
                self.assertTrue(client.closed)

                if method == "create":
                    unknown = provider.submit_async_media(
                        self.snapshot, (), self.intent
                    )
                    self.assertEqual(unknown.create_outcome, "unknown")
                    self.assertEqual(len(self.clients), 1)
                    self.assertEqual(len(client.calls), 1)

    def test_iterator_close_cancellation_still_wins_after_non_cancel_read_error(self) -> None:
        for method in ("create", "query"):
            with self.subTest(method=method):
                self.clients.clear()
                self.credential_calls.clear()
                read_error = ValueError("malformed body chunk")
                close_cancel = asyncio.CancelledError(
                    f"{method} iterator close cancelled"
                )
                body = (
                    b'{"id":"created-video"}'
                    if method == "create"
                    else b'{"status":"pending"}'
                )
                response = FakeResponse(
                    200,
                    body,
                    iterator_next_error=read_error,
                    iterator_close_error=close_cancel,
                )
                provider = self.make_provider(response=response)

                with self.assertRaises(asyncio.CancelledError) as raised:
                    if method == "create":
                        provider.submit_async_media(self.snapshot, (), self.intent)
                    else:
                        provider.query_existing(self.snapshot, self.submitted_ref())

                self.assertIs(raised.exception, close_cancel)
                self.assertEqual(len(self.clients), 1)
                client = self.clients[0]
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(
                    client.calls[0][0],
                    "POST" if method == "create" else "GET",
                )
                self.assertEqual(response.iterator.close_attempts, 1)
                self.assertEqual(response.close_attempts, 1)
                self.assertEqual(client.close_attempts, 1)

                if method == "create":
                    unknown = provider.submit_async_media(
                        self.snapshot, (), self.intent
                    )
                    self.assertEqual(unknown.create_outcome, "unknown")
                    self.assertEqual(len(client.calls), 1)

    def test_unsupported_provider_methods_have_no_side_effects(self) -> None:
        provider = self.make_provider()
        unsupported_calls = (
            (provider.stream_text, (self.snapshot, None)),
            (provider.optimize_frame, (self.snapshot, "", self.intent)),
            (provider.generate_sync_image, (self.snapshot, (), self.intent)),
            (provider.fetch_provider_content, (self.snapshot, self.submitted_ref())),
        )
        for method, args in unsupported_calls:
            with self.subTest(method=method.__name__):
                with self.assertRaises(GenerationError):
                    method(*args)
        self.assertEqual(self.credential_calls, [])
        self.assertEqual(self.clients, [])


if __name__ == "__main__":
    unittest.main()
