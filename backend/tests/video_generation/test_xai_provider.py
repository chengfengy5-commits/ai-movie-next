"""Fake-only checks for the xAI video transport adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import unittest
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal

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
from haoai_backend.generation_providers.video.xai_provider import XaiVideoProvider


_NOW = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
_MODEL_REF = ModelReference(
    owner_id="catalog-owner-4",
    provider="xai",
    model_id="catalog-video-model-91",
)
_BINDING_REF = "owner-provider-account-ref-3"
_SNAPSHOT_REF = "trusted-snapshot-ref-8"
_WIRE_MODEL = "grok-imagine-video"


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: bytes = b"{}",
        *,
        block: bool = False,
        body_delay: float = 0.0,
    ) -> None:
        self.status_code = status_code
        self.headers: Mapping[str, str] = {}
        self._chunks = tuple(
            body[index : index + 64 * 1024]
            for index in range(0, len(body), 64 * 1024)
        )
        self._block = block
        self._body_delay = body_delay
        self.iter_called = False
        self.iterator_closed = False
        self.iterator_waiting = asyncio.Event()
        self._release_iterator = asyncio.Event()

    async def iter_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        """Match the frozen public async-return AsyncIterator declaration."""
        self.iter_called = True

        async def chunks() -> AsyncIterator[bytes]:
            try:
                if self._block:
                    self.iterator_waiting.set()
                    await self._release_iterator.wait()
                if self._body_delay > 0:
                    await asyncio.sleep(self._body_delay)
                for chunk in self._chunks:
                    yield chunk
            finally:
                self.iterator_closed = True

        return chunks()


class DirectGeneratorResponse(FakeResponse):
    def iter_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        self.iter_called = True

        async def chunks() -> AsyncIterator[bytes]:
            try:
                for chunk in self._chunks:
                    yield chunk
            finally:
                self.iterator_closed = True

        return chunks()


class FakeClient:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: Exception | None = None,
        *,
        request_delay: float = 0.0,
        exit_delay: float = 0.0,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.closed = False
        self.request_delay = request_delay
        self.exit_delay = exit_delay

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
                "headers": headers,
                "content": content,
                "timeout_seconds": timeout_seconds,
                "follow_redirects": follow_redirects,
            }
        )
        if self.request_delay > 0:
            await asyncio.sleep(self.request_delay)
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("fake client has no response")
        return self.response


class FakeClientContext:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    async def __aenter__(self) -> FakeClient:
        return self.client

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
        try:
            if self.client.exit_delay > 0:
                await asyncio.sleep(self.client.exit_delay)
        finally:
            self.client.closed = True
        return False


class FakeClientFactory:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.calls = 0

    def __call__(self) -> FakeClientContext:
        self.calls += 1
        return FakeClientContext(self.client)


class ProviderFixture:
    def __init__(
        self,
        *,
        provider: XaiVideoProvider,
        snapshot: FrozenExecutionSnapshot,
        intent: CommittedIntent,
        owner_scope: OwnerTaskScope,
        credential_binding: CredentialBinding,
        credential: BoundCredential,
        client: FakeClient,
        client_factory: FakeClientFactory,
        resolver_calls: list[tuple[CredentialBinding, str]],
    ) -> None:
        self.provider = provider
        self.snapshot = snapshot
        self.intent = intent
        self.owner_scope = owner_scope
        self.credential_binding = credential_binding
        self.credential = credential
        self.client = client
        self.client_factory = client_factory
        self.resolver_calls = resolver_calls


def _snapshot(
    *,
    model_params: Mapping[str, object] | None = None,
) -> FrozenExecutionSnapshot:
    return FrozenExecutionSnapshot(
        model_ref=_MODEL_REF,
        model_category="video",
        model_name="catalog-label-not-wire-name",
        model_params=model_params or {},
        prompt_ref=None,
        effective_prompt="frozen prompt",
        effective_context="",
        server_quote=4,
        reference_snapshot_ref="reference-snapshot-23",
        target_snapshot=TargetSnapshot(kind="none"),
        credential_binding_ref=_BINDING_REF,
        grant_snapshot=FrozenGrantSnapshot(actor_id="actor-1"),
        adapter_config_versions=(),
        endpoint_policy=EndpointPolicy(provider_origin="https://api.x.ai"),
    )


def _intent() -> CommittedIntent:
    return CommittedIntent(
        step_id="step-14",
        attempt_id="attempt-14",
        external_submission_id="submission-14",
        provider="xai",
        model_id=_MODEL_REF.model_id,
        request_digest="digest-14",
        snapshot_ref=_SNAPSHOT_REF,
        committed_at=_NOW,
        first_intent_at=_NOW,
        deadline_at=_NOW + timedelta(minutes=10),
    )


def _binding() -> CredentialBinding:
    return CredentialBinding(
        source="original-binding-source",
        row_id="credential-row-42",
        owner_or_system="system-owned-model-credential",
        provider="xai",
        original_principal_id="original-principal-3",
        endpoint_origin="https://api.x.ai",
        allowed_content_paths=(),
        binding_version="binding-version-2",
    )


def _owner_scope() -> OwnerTaskScope:
    return OwnerTaskScope(
        user_id="actor-1",
        task_id="task-owned-by-caller-77",
        operation="video.generate",
    )


def _external_ref(
    intent: CommittedIntent,
    *,
    external_id: str = "req/14",
    create_outcome: str = "submitted",
    intent_id: str | None = None,
    attempt_id: str | None = None,
    provider: str | None = None,
) -> ExternalOperationRef:
    return ExternalOperationRef(
        intent_id=intent_id or intent.external_submission_id,
        attempt_id=attempt_id or intent.attempt_id,
        provider=provider or intent.provider,
        external_id=external_id,
        create_outcome=create_outcome,  # type: ignore[arg-type]
    )


def _reservation(
    intent: CommittedIntent,
    *,
    external_id: str = "req/14",
    step_id: str | None = None,
    submission_id: str | None = None,
    attempt_id: str | None = None,
) -> CommittedPollReservation:
    poll = PollReservation(
        reservation_id="reservation-9",
        group_key="group-9",
        sequence=2,
        attempt_id=attempt_id or intent.attempt_id,
        step_id=step_id or intent.step_id,
        submission_id=submission_id or intent.external_submission_id,
        external_id=external_id,
        payload_digest=intent.request_digest,
        committed_poll_count=1,
        reserved_at=_NOW,
        timeout_at=_NOW + timedelta(minutes=2),
        next_poll_at=_NOW,
        token="poll-token-not-used-as-credential",
        state="reserved",
    )
    return CommittedPollReservation(reservation=poll, confirmed_at=_NOW)


def _fixture(
    *,
    response: FakeResponse | None = None,
    request_error: Exception | None = None,
    credential: BoundCredential | Literal["unavailable"] | None = None,
    model_params: Mapping[str, object] | None = None,
    original_intent: CommittedIntent | None = None,
    request_delay: float = 0.0,
    client_exit_delay: float = 0.0,
    clock: Callable[[], datetime] | None = None,
) -> ProviderFixture:
    snapshot = _snapshot(model_params=model_params)
    intent = original_intent or _intent()
    owner_scope = _owner_scope()
    binding = _binding()
    bound_credential = credential or BoundCredential(
        provider="xai",
        principal_id=binding.original_principal_id,
        secret="fake-secret-value",
    )
    client = FakeClient(
        response=response,
        error=request_error,
        request_delay=request_delay,
        exit_delay=client_exit_delay,
    )
    client_factory = FakeClientFactory(client)
    resolver_calls: list[tuple[CredentialBinding, str]] = []

    def resolve(
        original: CredentialBinding, purpose: Literal["create", "query", "content"]
    ) -> BoundCredential | Literal["unavailable"]:
        resolver_calls.append((original, purpose))
        return bound_credential

    provider = XaiVideoProvider(
        owner_scope=owner_scope,
        original_snapshot=snapshot,
        original_intent=intent,
        credential_binding=binding,
        credential_binding_ref=_BINDING_REF,
        snapshot_ref=_SNAPSHOT_REF,
        model_ref=_MODEL_REF,
        wire_model=_WIRE_MODEL,
        http_client_factory=client_factory,
        credential_resolver=resolve,
        clock=clock or (lambda: _NOW),
    )
    return ProviderFixture(
        provider=provider,
        snapshot=snapshot,
        intent=intent,
        owner_scope=owner_scope,
        credential_binding=binding,
        credential=bound_credential
        if isinstance(bound_credential, BoundCredential)
        else BoundCredential(provider="xai", principal_id="unused", secret="unused"),
        client=client,
        client_factory=client_factory,
        resolver_calls=resolver_calls,
    )


class XaiVideoProviderTests(unittest.TestCase):
    def test_construction_is_lazy_and_create_uses_original_frozen_request(self) -> None:
        response = FakeResponse(body=b'{"request_id":"req-created-14"}')
        fixture = _fixture(response=response)

        self.assertEqual(fixture.client_factory.calls, 0)
        self.assertEqual(fixture.resolver_calls, [])

        result = fixture.provider.submit_async_media(
            fixture.snapshot,
            (),
            fixture.intent,
        )

        self.assertEqual(result.create_outcome, "submitted")
        self.assertEqual(result.external_id, "req-created-14")
        self.assertEqual(result.intent_id, fixture.intent.external_submission_id)
        self.assertEqual(result.attempt_id, fixture.intent.attempt_id)
        self.assertEqual(result.provider, "xai")
        self.assertEqual(fixture.client_factory.calls, 1)
        self.assertEqual(len(fixture.client.calls), 1)
        call = fixture.client.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://api.x.ai/v1/videos/generations")
        self.assertEqual(call["timeout_seconds"], 120.0)
        self.assertFalse(call["follow_redirects"])
        self.assertEqual(
            call["headers"],
            {
                "Authorization": "Bearer fake-secret-value",
                "Content-Type": "application/json",
            },
        )
        payload = json.loads(call["content"])
        self.assertEqual(payload["model"], _WIRE_MODEL)
        self.assertNotEqual(payload["model"], fixture.snapshot.model_ref.model_id)
        self.assertEqual(payload["prompt"], "frozen prompt")
        self.assertEqual(payload["aspect_ratio"], "16:9")
        self.assertNotIn("duration", payload)
        self.assertEqual(
            fixture.resolver_calls,
            [(fixture.credential_binding, "create")],
        )
        self.assertIs(fixture.resolver_calls[0][0], fixture.credential_binding)
        self.assertTrue(response.iter_called)
        self.assertTrue(response.iterator_closed)
        self.assertTrue(fixture.client.closed)

    def test_single_and_multiple_reference_payloads_keep_legacy_modes(self) -> None:
        one = _fixture(
            response=FakeResponse(body=b'{"request_id":"one"}'),
            model_params={
                "size": {"default": "9:16"},
                "duration": {"default": 20},
                "resolution": {"default": "720p"},
            },
        )
        one.provider.submit_async_media(
            one.snapshot,
            (ReferenceInput(kind="public_http", locator="https://img.test/first.png"),),
            one.intent,
        )
        one_payload = json.loads(one.client.calls[0]["content"])
        self.assertEqual(one_payload["aspect_ratio"], "9:16")
        self.assertEqual(one_payload["resolution"], "720p")
        self.assertEqual(one_payload["duration"], 15)
        self.assertEqual(one_payload["image"], {"url": "https://img.test/first.png"})
        self.assertNotIn("reference_images", one_payload)

        many = _fixture(
            response=FakeResponse(body=b'{"request_id":"many"}'),
        )
        references = tuple(
            ReferenceInput(kind="managed_url", locator=f"https://img.test/{index}.png")
            for index in range(8)
        )
        many.provider.submit_async_media(many.snapshot, references, many.intent)
        many_payload = json.loads(many.client.calls[0]["content"])
        self.assertEqual(len(many_payload["reference_images"]), 7)
        self.assertEqual(many_payload["duration"], 10)
        self.assertNotIn("image", many_payload)

    def test_invalid_frozen_pairs_and_intents_fail_before_resolver_or_http(self) -> None:
        cases: list[tuple[FrozenExecutionSnapshot, CommittedIntent]] = []
        fixture = _fixture(response=FakeResponse(body=b'{"request_id":"unused"}'))
        cases.append(
            (
                replace(
                    fixture.snapshot,
                    model_ref=ModelReference(
                        owner_id="other-owner",
                        provider="xai",
                        model_id=_MODEL_REF.model_id,
                    ),
                ),
                fixture.intent,
            )
        )
        cases.append(
            (
                replace(
                    fixture.snapshot,
                    grant_snapshot=FrozenGrantSnapshot(actor_id="other-actor"),
                ),
                fixture.intent,
            )
        )
        cases.append(
            (
                replace(
                    fixture.snapshot,
                    credential_binding_ref="another-account-ref",
                ),
                fixture.intent,
            )
        )
        cases.append(
            (
                replace(
                    fixture.snapshot,
                    endpoint_policy=EndpointPolicy(provider_origin="https://other.test"),
                ),
                fixture.intent,
            )
        )
        cases.append(
            (
                replace(
                    fixture.snapshot,
                    model_params={"resolution": {"default": "720p"}},
                ),
                fixture.intent,
            )
        )
        cases.append(
            (
                fixture.snapshot,
                replace(fixture.intent, snapshot_ref="another-snapshot"),
            )
        )
        cases.append(
            (
                fixture.snapshot,
                replace(fixture.intent, model_id="another-catalog-model"),
            )
        )

        for snapshot, intent in cases:
            with self.subTest(snapshot=snapshot, intent=intent):
                with self.assertRaises(GenerationError):
                    fixture.provider.submit_async_media(snapshot, (), intent)
                self.assertEqual(fixture.resolver_calls, [])
                self.assertEqual(fixture.client_factory.calls, 0)
                self.assertEqual(fixture.client.calls, [])

    def test_snapshot_must_match_the_complete_original_frozen_value(self) -> None:
        fixture = _fixture(response=FakeResponse(body=b'{"request_id":"unused"}'))
        equivalent_copy = replace(fixture.snapshot)
        self.assertEqual(equivalent_copy, fixture.snapshot)
        result = fixture.provider.submit_async_media(
            equivalent_copy, (), replace(fixture.intent)
        )
        self.assertEqual(result.external_id, "unused")

        changed_prompt = replace(
            fixture.snapshot, effective_prompt="changed prompt"
        )
        with self.assertRaises(GenerationError):
            fixture.provider.submit_async_media(changed_prompt, (), fixture.intent)
        self.assertEqual(len(fixture.resolver_calls), 1)
        self.assertEqual(fixture.client_factory.calls, 1)

    def test_invalid_or_same_principal_mismatched_credentials_do_not_send(self) -> None:
        bad_credentials = (
            BoundCredential(
                provider="xai",
                principal_id="other-principal",
                secret="secret",
            ),
            BoundCredential(
                provider="other-provider",
                principal_id="original-principal-3",
                secret="secret",
            ),
            BoundCredential(
                provider="xai",
                principal_id="original-principal-3",
                secret="  ",
            ),
        )
        for bad_credential in bad_credentials:
            with self.subTest(credential=bad_credential.provider):
                fixture = _fixture(
                    response=FakeResponse(body=b'{"request_id":"unused"}'),
                    credential=bad_credential,
                )
                with self.assertRaises(GenerationError):
                    fixture.provider.submit_async_media(
                        fixture.snapshot, (), fixture.intent
                    )
                self.assertEqual(
                    fixture.resolver_calls,
                    [(fixture.credential_binding, "create")],
                )
                self.assertIs(
                    fixture.resolver_calls[0][0], fixture.credential_binding
                )
                self.assertEqual(fixture.client_factory.calls, 0)
                self.assertEqual(fixture.client.calls, [])

    def test_resolver_accepts_only_the_exact_binding_and_supported_purpose(self) -> None:
        fixture = _fixture(response=FakeResponse())
        equal_copy = replace(fixture.credential_binding)
        self.assertEqual(equal_copy, fixture.credential_binding)
        self.assertIs(
            fixture.provider.resolve_original_credential(equal_copy, "create"),
            "unavailable",
        )
        self.assertIs(
            fixture.provider.resolve_original_credential(
                fixture.credential_binding, "content"
            ),
            "unavailable",
        )
        self.assertEqual(fixture.resolver_calls, [])

    def test_unsupported_reference_is_rejected_before_resolution(self) -> None:
        fixture = _fixture(response=FakeResponse(body=b'{"request_id":"unused"}'))
        ref = ReferenceInput(kind="data_uri", locator="data:image/png;base64,AA==")
        with self.assertRaises(GenerationError):
            fixture.provider.submit_async_media(fixture.snapshot, (ref,), fixture.intent)
        self.assertEqual(fixture.resolver_calls, [])
        self.assertEqual(fixture.client_factory.calls, 0)

    def test_create_failure_unknown_and_malformed_results_never_retry(self) -> None:
        cases: tuple[tuple[FakeResponse | None, Exception | None, str], ...] = (
            (None, TimeoutError("fake timeout"), "unknown"),
            (FakeResponse(status_code=503, body=b'{"error":"temporary"}'), None, "unknown"),
            (FakeResponse(status_code=400, body=b'{"error":"bad request"}'), None, "definitive_failure"),
            (FakeResponse(body=b"not-json"), None, "unknown"),
            (FakeResponse(body=b'{"id":"wrong-alias"}'), None, "unknown"),
            (FakeResponse(body=b'{"request_id":42}'), None, "unknown"),
            (FakeResponse(body=b'{"request_id":"  "}'), None, "unknown"),
        )
        for response, error, expected in cases:
            with self.subTest(expected=expected, response=response, error=error):
                fixture = _fixture(response=response, request_error=error)
                result = fixture.provider.submit_async_media(
                    fixture.snapshot, (), fixture.intent
                )
                self.assertEqual(result.create_outcome, expected)
                if expected == "unknown":
                    self.assertIsNone(result.external_id)
                if expected == "definitive_failure":
                    self.assertIsNone(result.external_id)
                    self.assertEqual(result.last_safe_provider_state, "400")
                self.assertEqual(fixture.client_factory.calls, 1)
                self.assertEqual(len(fixture.client.calls), 1)
                self.assertEqual(fixture.client.calls[0]["method"], "POST")
                self.assertTrue(fixture.client.closed)

    def test_create_response_body_limit_returns_unknown_and_closes_iterator(self) -> None:
        response = FakeResponse(body=b"x" * (1_048_576 + 1))
        fixture = _fixture(response=response)
        result = fixture.provider.submit_async_media(fixture.snapshot, (), fixture.intent)
        self.assertEqual(result.create_outcome, "unknown")
        self.assertIsNone(result.external_id)
        self.assertTrue(response.iterator_closed)
        self.assertTrue(fixture.client.closed)
        self.assertEqual(len(fixture.client.calls), 1)

    def test_create_deadline_bounds_body_read_and_normal_context_cleanup(self) -> None:
        cases = (
            ("body", FakeResponse(body=b'{"request_id":"slow-body"}', body_delay=0.2), 0.0),
            ("cleanup", FakeResponse(body=b'{"request_id":"slow-cleanup"}'), 0.2),
        )
        for stage, response, exit_delay in cases:
            with self.subTest(stage=stage):
                intent = replace(
                    _intent(), deadline_at=_NOW + timedelta(milliseconds=40)
                )
                fixture = _fixture(
                    response=response,
                    original_intent=intent,
                    client_exit_delay=exit_delay,
                )

                result = fixture.provider.submit_async_media(
                    fixture.snapshot, (), intent
                )

                if stage == "cleanup":
                    self.assertEqual(result.create_outcome, "submitted")
                    self.assertEqual(result.external_id, "slow-cleanup")
                else:
                    self.assertEqual(result.create_outcome, "unknown")
                    self.assertIsNone(result.external_id)
                self.assertEqual(len(fixture.client.calls), 1)
                self.assertEqual(fixture.client.calls[0]["method"], "POST")
                self.assertTrue(fixture.client.closed)

    def test_body_timeout_bounds_following_normal_context_cleanup(self) -> None:
        intent = replace(_intent(), deadline_at=_NOW + timedelta(milliseconds=40))
        response = FakeResponse(
            body=b'{"request_id":"body-did-not-finish"}', body_delay=0.2
        )
        fixture = _fixture(
            response=response,
            original_intent=intent,
            client_exit_delay=0.2,
        )

        started_at = time.perf_counter()
        result = fixture.provider.submit_async_media(
            fixture.snapshot, (), intent
        )
        elapsed = time.perf_counter() - started_at

        self.assertEqual(result.create_outcome, "unknown")
        self.assertEqual(len(fixture.client.calls), 1)
        self.assertTrue(response.iterator_closed)
        self.assertTrue(fixture.client.closed)
        self.assertLess(elapsed, 0.15)

    def test_cleanup_timeout_never_preserves_request_id_echoing_original_secret(self) -> None:
        intent = replace(_intent(), deadline_at=_NOW + timedelta(milliseconds=40))
        fixture = _fixture(
            response=FakeResponse(
                body=b'{"request_id":"fake-secret-value"}'
            ),
            original_intent=intent,
            client_exit_delay=0.2,
        )

        result = fixture.provider.submit_async_media(
            fixture.snapshot, (), intent
        )

        self.assertEqual(result.create_outcome, "unknown")
        self.assertIsNone(result.external_id)
        self.assertNotIn("fake-secret-value", repr(result))
        self.assertEqual(len(fixture.client.calls), 1)
        self.assertEqual(fixture.client.calls[0]["method"], "POST")

    def test_create_deadline_bounds_headers_body_and_context_cleanup(self) -> None:
        cases = (
            ("headers", FakeResponse(body=b'{"request_id":"unused"}'), 0.2, 0.0),
            ("body", FakeResponse(body=b'{"request_id":"unused"}', body_delay=0.2), 0.0, 0.0),
            ("cleanup", FakeResponse(body=b'{"request_id":"unused"}'), 0.0, 0.2),
        )
        for stage, response, request_delay, exit_delay in cases:
            with self.subTest(stage=stage):
                intent = replace(
                    _intent(), deadline_at=_NOW + timedelta(milliseconds=50)
                )
                fixture = _fixture(
                    response=response,
                    original_intent=intent,
                    request_delay=request_delay,
                    client_exit_delay=exit_delay,
                )

                result = fixture.provider.submit_async_media(
                    fixture.snapshot, (), intent
                )

                if stage == "cleanup":
                    self.assertEqual(result.create_outcome, "submitted")
                    self.assertEqual(result.external_id, "unused")
                else:
                    self.assertEqual(result.create_outcome, "unknown")
                    self.assertIsNone(result.external_id)
                self.assertEqual(fixture.client_factory.calls, 1)
                self.assertEqual(len(fixture.client.calls), 1)
                self.assertEqual(fixture.client.calls[0]["method"], "POST")
                self.assertAlmostEqual(fixture.client.calls[0]["timeout_seconds"], 0.05)
                self.assertTrue(fixture.client.closed)

    def test_query_deadline_bounds_body_read_and_context_cleanup_without_losing_original_id(self) -> None:
        cases = (
            ("body", FakeResponse(body=b'{"status":"processing"}', body_delay=0.2), 0.0),
            ("cleanup", FakeResponse(body=b'{"status":"processing"}'), 0.2),
        )
        for stage, response, exit_delay in cases:
            with self.subTest(stage=stage):
                intent = replace(
                    _intent(), deadline_at=_NOW + timedelta(milliseconds=50)
                )
                fixture = _fixture(
                    response=response,
                    original_intent=intent,
                    client_exit_delay=exit_delay,
                )
                original_ref = _external_ref(intent, external_id="req/14")

                with self.assertRaises(GenerationError) as raised:
                    fixture.provider.query_existing(fixture.snapshot, original_ref)

                self.assertEqual(raised.exception.safe_detail, "the xAI query could not be completed")
                self.assertEqual(len(fixture.client.calls), 1)
                call = fixture.client.calls[0]
                self.assertEqual(call["method"], "GET")
                self.assertEqual(call["url"], "https://api.x.ai/v1/videos/req%2F14")
                self.assertEqual(call["headers"], {"Authorization": "Bearer fake-secret-value"})
                self.assertAlmostEqual(call["timeout_seconds"], 0.05)
                self.assertTrue(fixture.client.closed)

    def test_deadline_caps_create_and_query_timeout_arguments(self) -> None:
        intent = replace(_intent(), deadline_at=_NOW + timedelta(seconds=12))
        create_fixture = _fixture(
            response=FakeResponse(body=b'{"request_id":"created"}'),
            original_intent=intent,
        )
        create_fixture.provider.submit_async_media(create_fixture.snapshot, (), intent)
        self.assertEqual(create_fixture.client.calls[0]["timeout_seconds"], 12.0)

        query_fixture = _fixture(
            response=FakeResponse(body=b'{"status":"processing"}'),
            original_intent=intent,
        )
        query_fixture.provider.query_existing(
            query_fixture.snapshot,
            _external_ref(intent, external_id="original-id"),
        )
        self.assertEqual(query_fixture.client.calls[0]["timeout_seconds"], 12.0)

    def test_invalid_or_expired_deadlines_reject_before_http_client_creation(self) -> None:
        def broken_clock() -> datetime:
            raise RuntimeError("private clock details")

        cases = (
            ("broken clock", _NOW + timedelta(minutes=1), broken_clock),
            ("naive clock", _NOW + timedelta(minutes=1), lambda: datetime(2026, 10, 12, 3, 0)),
            ("naive deadline", datetime(2026, 10, 12, 3, 1), lambda: _NOW),
            ("expired deadline", _NOW, lambda: _NOW),
        )
        for name, deadline, clock in cases:
            with self.subTest(case=name):
                intent = replace(_intent(), deadline_at=deadline)
                fixture = _fixture(
                    response=FakeResponse(body=b'{"request_id":"unused"}'),
                    original_intent=intent,
                    clock=clock,
                )

                with self.assertRaises(GenerationError):
                    fixture.provider.submit_async_media(fixture.snapshot, (), intent)
                with self.assertRaises(GenerationError):
                    fixture.provider.query_existing(
                        fixture.snapshot,
                        _external_ref(intent, external_id="original-id"),
                    )

                self.assertEqual(fixture.client_factory.calls, 0)
                self.assertEqual(fixture.client.calls, [])

    def test_create_http_408_is_unknown_without_reading_or_reposting(self) -> None:
        response = FakeResponse(status_code=408, body=b'{"request_id":"not-known"}')
        fixture = _fixture(response=response)

        result = fixture.provider.submit_async_media(
            fixture.snapshot, (), fixture.intent
        )

        self.assertEqual(result.create_outcome, "unknown")
        self.assertIsNone(result.external_id)
        self.assertFalse(response.iter_called)
        self.assertEqual(len(fixture.client.calls), 1)
        self.assertEqual(fixture.client.calls[0]["method"], "POST")

    def test_create_accepts_direct_async_generator_iterator(self) -> None:
        response = DirectGeneratorResponse(body=b'{"request_id":"direct-iterator"}')
        fixture = _fixture(response=response)
        result = fixture.provider.submit_async_media(fixture.snapshot, (), fixture.intent)
        self.assertEqual(result.external_id, "direct-iterator")
        self.assertTrue(response.iterator_closed)

    def test_query_uses_only_known_original_id_and_returns_safe_observation(self) -> None:
        body = b'{"status":"done","video":{"url":"https://media.test/out.mp4"}}'
        response = FakeResponse(body=body)
        fixture = _fixture(response=response)
        original_ref = _external_ref(fixture.intent, external_id="req/14")
        observation = fixture.provider.query_existing(
            replace(fixture.snapshot),
            original_ref,
            _reservation(fixture.intent, external_id="req/14"),
        )

        self.assertEqual(len(fixture.client.calls), 1)
        call = fixture.client.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], "https://api.x.ai/v1/videos/req%2F14")
        self.assertEqual(call["timeout_seconds"], 30.0)
        self.assertFalse(call["follow_redirects"])
        self.assertEqual(call["content"], None)
        self.assertEqual(
            fixture.resolver_calls,
            [(fixture.credential_binding, "query")],
        )
        self.assertIs(fixture.resolver_calls[0][0], fixture.credential_binding)
        self.assertEqual(observation.status, "done")
        self.assertEqual(
            observation.media_locator, "https://media.test/out.mp4"
        )
        self.assertIs(observation.owner_scope, fixture.owner_scope)
        self.assertEqual(
            observation.intent_id, fixture.intent.external_submission_id
        )
        self.assertEqual(observation.attempt_id, fixture.intent.attempt_id)
        self.assertEqual(observation.payload, {"http_status": 200})
        self.assertEqual(
            observation.payload_digest, hashlib.sha256(body).hexdigest()
        )
        self.assertTrue(response.iterator_closed)
        self.assertTrue(fixture.client.closed)

    def test_query_projects_legacy_string_video_locator_with_signed_query(self) -> None:
        locator = (
            "https://media.test/out.mp4?token=provider-token&signature=sig-1"
            "&sig=sig-2&key=key-1%2Fpart"
        )
        body = json.dumps(
            {"status": "done", "video": locator}, separators=(",", ":")
        ).encode()
        response = FakeResponse(body=body)
        fixture = _fixture(response=response)
        observation = fixture.provider.query_existing(
            replace(fixture.snapshot), _external_ref(fixture.intent)
        )

        self.assertEqual(observation.status, "done")
        self.assertEqual(observation.media_locator, locator)
        self.assertEqual(observation.payload, {"http_status": 200})
        self.assertEqual(
            observation.payload_digest, hashlib.sha256(body).hexdigest()
        )
        self.assertEqual(fixture.client.calls[0]["method"], "GET")
        self.assertTrue(response.iterator_closed)
        self.assertTrue(fixture.client.closed)

    def test_query_projects_object_video_locator_with_x_amz_signature(self) -> None:
        locator = (
            "https://media.test/out.mp4?X-Amz-Signature=abcd1234"
            "&X-Amz-Expires=3600"
        )
        body = json.dumps(
            {"status": "done", "video": {"url": locator}},
            separators=(",", ":"),
        ).encode()
        response = FakeResponse(body=body)
        fixture = _fixture(response=response)
        observation = fixture.provider.query_existing(
            replace(fixture.snapshot), _external_ref(fixture.intent)
        )

        self.assertEqual(observation.status, "done")
        self.assertEqual(observation.media_locator, locator)
        self.assertEqual(observation.payload, {"http_status": 200})
        self.assertEqual(
            observation.payload_digest, hashlib.sha256(body).hexdigest()
        )
        self.assertEqual(fixture.client.calls[0]["method"], "GET")
        self.assertTrue(response.iterator_closed)
        self.assertTrue(fixture.client.closed)

    def test_query_rejects_secret_auth_error_and_invalid_media_locators(self) -> None:
        secret = "fake-secret-value"
        raw_error = f"Authorization: Bearer {secret}"
        locators = (
            f"https://media.test/{secret}.mp4",
            "https://media.test/fake%2Dsecret%2Dvalue.mp4",
            "https://media.test/fake%252Dsecret%252Dvalue.mp4",
            "https://user:password@media.test/out.mp4",
            f"https://media.test/out.mp4?token={secret}",
            "https://media.test/out.mp4?token=fake%2Dsecret%2Dvalue",
            (
                "https://media.test/out.mp4?X-Amz-Signature="
                "fake%252Dsecret%252Dvalue"
            ),
            f"https://media.test/out.mp4?sig={secret}",
            raw_error,
            "error: provider generation failed",
            "ftp://media.test/out.mp4",
            "https://media.test/out.mp4#fragment",
            "https://media.test:invalid/out.mp4",
            "not a URL",
            "https://media.test/" + ("a" * 4100),
        )
        for locator in locators:
            with self.subTest(locator=locator):
                body = json.dumps(
                    {"status": "done", "video": {"url": locator}},
                    separators=(",", ":"),
                ).encode()
                response = FakeResponse(body=body)
                fixture = _fixture(response=response)
                observation = fixture.provider.query_existing(
                    fixture.snapshot, _external_ref(fixture.intent)
                )

                self.assertEqual(observation.status, "done")
                self.assertIsNone(observation.media_locator)
                self.assertEqual(observation.payload, {"http_status": 200})
                self.assertEqual(
                    observation.payload_digest, hashlib.sha256(body).hexdigest()
                )
                self.assertNotIn(secret, repr(observation))
                self.assertNotIn(raw_error, repr(observation))
                self.assertEqual(fixture.client.calls[0]["method"], "GET")
                self.assertTrue(response.iterator_closed)
                self.assertTrue(fixture.client.closed)

    def test_query_projects_only_allowlisted_provider_states(self) -> None:
        cases = (
            (
                b'{"status":"failed","error":{"message":"raw failure detail"}}',
                "failed",
                "xAI reported a provider failure",
            ),
            (b'{"status":"future-provider-state"}', "unknown", None),
            (b'{"status":42}', "unknown", None),
        )
        for body, expected_status, expected_error in cases:
            with self.subTest(expected_status=expected_status):
                response = FakeResponse(body=body)
                fixture = _fixture(response=response)
                observation = fixture.provider.query_existing(
                    fixture.snapshot, _external_ref(fixture.intent)
                )
                self.assertEqual(observation.status, expected_status)
                self.assertEqual(observation.safe_error, expected_error)
                self.assertIsNone(observation.media_locator)
                self.assertEqual(observation.payload, {"http_status": 200})
                self.assertEqual(
                    observation.payload_digest, hashlib.sha256(body).hexdigest()
                )
                self.assertEqual(len(fixture.client.calls), 1)
                self.assertEqual(fixture.client.calls[0]["method"], "GET")
                self.assertTrue(response.iterator_closed)
                self.assertTrue(fixture.client.closed)

    def test_query_observation_omits_echoed_secrets_errors_and_extra_fields(self) -> None:
        secret = "fake-secret-value"
        raw_error = f"Authorization: Bearer {secret}"
        bodies = (
            (
                {
                    "status": "failed",
                    "error": {"message": raw_error, "details": secret},
                    "authorization": f"Bearer {secret}",
                    "video": {
                        "url": f"https://media.test/private/{secret}?token={secret}"
                    },
                    "extra": {"credential": secret},
                },
                "failed",
                "xAI reported a provider failure",
            ),
            (
                {
                    "status": f"future-state {raw_error}",
                    "error": {"message": raw_error},
                    "access_token": secret,
                    "video": {"url": f"https://media.test/{secret}"},
                    "extra": {"raw": raw_error},
                },
                "unknown",
                None,
            ),
        )
        for raw_object, expected_status, expected_error in bodies:
            with self.subTest(expected_status=expected_status):
                body = json.dumps(raw_object, separators=(",", ":")).encode()
                response = FakeResponse(body=body)
                fixture = _fixture(response=response)
                observation = fixture.provider.query_existing(
                    fixture.snapshot, _external_ref(fixture.intent)
                )

                self.assertEqual(observation.status, expected_status)
                self.assertEqual(observation.safe_error, expected_error)
                self.assertIsNone(observation.media_locator)
                self.assertEqual(observation.payload, {"http_status": 200})
                self.assertEqual(
                    observation.payload_digest, hashlib.sha256(body).hexdigest()
                )
                self.assertNotIn(secret, repr(observation))
                self.assertNotIn(raw_error, repr(observation))
                self.assertEqual(len(fixture.client.calls), 1)
                self.assertEqual(fixture.client.calls[0]["method"], "GET")
                self.assertTrue(response.iterator_closed)
                self.assertTrue(fixture.client.closed)

        secret_status = "failed"
        response = FakeResponse(body=b'{"status":"failed","error":"failed"}')
        fixture = _fixture(
            response=response,
            credential=BoundCredential(
                provider="xai",
                principal_id="original-principal-3",
                secret=secret_status,
            ),
        )
        observation = fixture.provider.query_existing(
            fixture.snapshot, _external_ref(fixture.intent)
        )
        self.assertEqual(observation.status, "unknown")
        self.assertIsNone(observation.safe_error)
        self.assertNotIn(secret_status, repr(observation))

    def test_query_rejects_invalid_original_intent_pair_before_resolution(self) -> None:
        original = _intent()
        invalid_intents = (
            replace(original, model_id="other-catalog-model"),
            replace(original, provider="other-provider"),
            replace(original, step_id=""),
            replace(original, attempt_id=""),
            replace(original, external_submission_id=""),
        )
        for invalid_intent in invalid_intents:
            for reservation in (None, _reservation(original)):
                with self.subTest(intent=invalid_intent, reservation=reservation):
                    fixture = _fixture(
                        response=FakeResponse(body=b'{"status":"queued"}'),
                        original_intent=invalid_intent,
                    )
                    external_ref = _external_ref(invalid_intent)
                    with self.assertRaises(GenerationError):
                        fixture.provider.query_existing(
                            replace(fixture.snapshot), external_ref, reservation
                        )
                    self.assertEqual(fixture.resolver_calls, [])
                    self.assertEqual(fixture.client_factory.calls, 0)
                    self.assertEqual(fixture.client.calls, [])

    def test_query_http_error_is_observed_without_reading_raw_error_body(self) -> None:
        response = FakeResponse(
            status_code=404,
            body=b'{"error":"raw body is not retained"}',
        )
        fixture = _fixture(response=response)
        observation = fixture.provider.query_existing(
            fixture.snapshot, _external_ref(fixture.intent)
        )
        self.assertEqual(observation.status, "404")
        self.assertEqual(observation.payload["http_status"], 404)
        self.assertFalse(response.iter_called)
        self.assertTrue(fixture.client.closed)
        self.assertEqual(fixture.client.calls[0]["method"], "GET")

    def test_query_rejects_wrong_original_reference_or_poll_before_resolver(self) -> None:
        fixture = _fixture(response=FakeResponse(body=b'{"status":"queued"}'))
        bad_refs = (
            _external_ref(fixture.intent, intent_id="other-submission"),
            _external_ref(fixture.intent, attempt_id="other-attempt"),
            _external_ref(fixture.intent, provider="other-provider"),
            _external_ref(fixture.intent, create_outcome="unknown"),
            _external_ref(fixture.intent, external_id=""),
        )
        for bad_ref in bad_refs:
            with self.subTest(external_ref=bad_ref):
                with self.assertRaises(GenerationError):
                    fixture.provider.query_existing(fixture.snapshot, bad_ref)
                self.assertEqual(fixture.resolver_calls, [])
                self.assertEqual(fixture.client_factory.calls, 0)

        bad_reservations = (
            _reservation(fixture.intent, step_id="other-step"),
            _reservation(fixture.intent, submission_id="other-submission"),
            _reservation(fixture.intent, attempt_id="other-attempt"),
            _reservation(fixture.intent, external_id="other-request"),
        )
        for reservation in bad_reservations:
            with self.subTest(reservation=reservation.reservation):
                with self.assertRaises(GenerationError):
                    fixture.provider.query_existing(
                        fixture.snapshot,
                        _external_ref(fixture.intent),
                        reservation,
                    )
                self.assertEqual(fixture.resolver_calls, [])
                self.assertEqual(fixture.client_factory.calls, 0)
        self.assertEqual(fixture.client.calls, [])

    def test_query_body_limit_and_malformed_json_fail_safely(self) -> None:
        for response in (
            FakeResponse(body=b"x" * (1_048_576 + 1)),
            FakeResponse(body=b"not-json"),
        ):
            with self.subTest(response=response):
                fixture = _fixture(response=response)
                with self.assertRaises(GenerationError):
                    fixture.provider.query_existing(
                        fixture.snapshot, _external_ref(fixture.intent)
                    )
                self.assertTrue(fixture.client.closed)
                self.assertEqual(fixture.client.calls[0]["method"], "GET")

    def test_sync_methods_reject_running_loop_before_resolver_or_http(self) -> None:
        fixture = _fixture(response=FakeResponse(body=b'{"request_id":"unused"}'))

        async def call_inside_loop() -> None:
            with self.assertRaises(RuntimeError):
                fixture.provider.submit_async_media(
                    fixture.snapshot, (), fixture.intent
                )
            with self.assertRaises(RuntimeError):
                fixture.provider.query_existing(
                    fixture.snapshot, _external_ref(fixture.intent)
                )

        asyncio.run(call_inside_loop())
        self.assertEqual(fixture.resolver_calls, [])
        self.assertEqual(fixture.client_factory.calls, 0)
        self.assertEqual(fixture.client.calls, [])

    def test_cancellation_closes_awaitable_response_iterator_and_client(self) -> None:
        response = FakeResponse(block=True)
        fixture = _fixture(response=response)

        async def cancel_request() -> None:
            task = asyncio.create_task(
                fixture.provider._submit_async_media(
                    fixture.snapshot, (), fixture.intent
                )
            )
            await asyncio.wait_for(response.iterator_waiting.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(cancel_request())
        self.assertTrue(response.iterator_closed)
        self.assertTrue(fixture.client.closed)
        self.assertEqual(len(fixture.client.calls), 1)
        self.assertEqual(fixture.client.calls[0]["method"], "POST")


if __name__ == "__main__":
    unittest.main()
