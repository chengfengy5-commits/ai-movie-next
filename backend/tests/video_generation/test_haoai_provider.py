from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, quote_plus

import pytest

from haoai_backend.generation_contracts import (
    BoundCredential,
    CommittedIntent,
    CommittedPollReservation,
    CredentialBinding,
    EndpointPolicy,
    ExternalOperationRef,
    Failure,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    MediaResult,
    ModelReference,
    OwnerTaskScope,
    PollReservation,
    ProviderFailure,
    ProviderObservation,
    ReferenceInput,
    TargetSnapshot,
    TextEvent,
)
from haoai_backend.generation_providers.video.haoai_codec import (
    DEFAULT_HAOAI_VIDEO_MODEL,
)
from haoai_backend.generation_providers.video.haoai_provider import (
    MAX_RESPONSE_BYTES,
    RESPONSE_CHUNK_BYTES,
    HaoaiExecutionBinding,
    HaoaiVideoProvider,
    _safe_response_candidate,
)
from haoai_backend.generation_providers.video import haoai_provider as haoai_provider_module

SECRET = "s3cr3t+/value"
ENDPOINT = "https://cpa.haoai.icu/v1"
NOW = datetime(2026, 10, 12, 3, 4, 5, tzinfo=timezone.utc)


class FakeStream:
    def __init__(
        self,
        chunks: list[object],
        *,
        fail_with: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.chunks = list(chunks)
        self.fail_with = fail_with
        self.close_error = close_error
        self.close_calls = 0

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> object:
        if self.chunks:
            return self.chunks.pop(0)
        if self.fail_with is not None:
            raise self.fail_with
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: bytes = b'{"request_id":"video-1"}',
        *,
        headers: dict[str, str] | None = None,
        chunks: list[object] | None = None,
        iterator_error: BaseException | None = None,
        iterator_close_error: BaseException | None = None,
        response_close_error: BaseException | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.stream = FakeStream(
            chunks if chunks is not None else [body],
            fail_with=iterator_error,
            close_error=iterator_close_error,
        )
        self.response_close_error = response_close_error
        self.close_calls = 0
        self.chunk_sizes: list[int] = []

    async def iter_bytes(self, *, chunk_size: int) -> FakeStream:
        self.chunk_sizes.append(chunk_size)
        return self.stream

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.response_close_error is not None:
            raise self.response_close_error


class DirectIteratorResponse(FakeResponse):
    def iter_bytes(self, *, chunk_size: int) -> FakeStream:
        self.chunk_sizes.append(chunk_size)
        return self.stream


class FakeClient:
    def __init__(
        self,
        responses: list[FakeResponse | BaseException],
        *,
        request_error: BaseException | None = None,
    ) -> None:
        self.responses = list(responses)
        self.request_error = request_error
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.request_error is not None:
            raise self.request_error
        if not self.responses:
            raise AssertionError("unexpected provider request")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeClientManager:
    def __init__(
        self,
        client: FakeClient,
        *,
        exit_error: BaseException | None = None,
    ) -> None:
        self.client = client
        self.exit_error = exit_error
        self.enter_calls = 0
        self.exit_calls = 0
        self.exit_arguments: list[tuple[object, ...]] = []

    async def __aenter__(self) -> FakeClient:
        self.enter_calls += 1
        return self.client

    async def __aexit__(self, *args: object) -> bool:
        self.exit_calls += 1
        self.exit_arguments.append(args)
        if self.exit_error is not None:
            raise self.exit_error
        return False


def make_snapshot(
    *,
    model_params: dict[str, Any] | None = None,
    model_name: str = "wire-model",
    provider: str = "haoai",
    category: str = "video",
    owner_id: str = "user-1",
    actor_id: str = "user-1",
    endpoint: str = ENDPOINT,
) -> FrozenExecutionSnapshot:
    return FrozenExecutionSnapshot(
        model_ref=ModelReference(
            owner_id=owner_id,
            provider=provider,
            model_id="catalog-model-id",
        ),
        model_category=category,
        model_name=model_name,
        model_params=model_params or {},
        prompt_ref="prompt-ref-1",
        effective_prompt="A quiet coast at dawn",
        effective_context="frozen context",
        server_quote=7,
        reference_snapshot_ref="reference-snapshot-ref",
        target_snapshot=TargetSnapshot(kind="none"),
        credential_binding_ref="credential-ref-1",
        grant_snapshot=FrozenGrantSnapshot(actor_id=actor_id),
        adapter_config_versions=(("adapter", "v1"),),
        endpoint_policy=EndpointPolicy(
            provider_origin=endpoint,
            allowed_content_paths=("/openai/v1/videos/*",),
        ),
    )


def make_intent(
    *,
    provider: str = "haoai",
    model_id: str = "catalog-model-id",
    step_id: str = "step-1",
    attempt_id: str = "attempt-1",
    submission_id: str = "submission-1",
    request_digest: str = "digest-1",
    snapshot_ref: str = "execution-snapshot-1",
) -> CommittedIntent:
    return CommittedIntent(
        step_id=step_id,
        attempt_id=attempt_id,
        external_submission_id=submission_id,
        provider=provider,
        model_id=model_id,
        request_digest=request_digest,
        snapshot_ref=snapshot_ref,
        committed_at=NOW,
        first_intent_at=NOW,
        deadline_at=datetime(2026, 10, 12, 5, 4, 5, tzinfo=timezone.utc),
    )


def make_credential_binding(
    *,
    owner_or_system: str = "user-1",
    provider: str = "haoai",
    principal_id: str = "principal-1",
    endpoint: str = ENDPOINT,
    source: str = "user_model",
    row_id: str = "row-1",
    version: str = "binding-v1",
) -> CredentialBinding:
    return CredentialBinding(
        source=source,
        row_id=row_id,
        owner_or_system=owner_or_system,
        provider=provider,
        original_principal_id=principal_id,
        endpoint_origin=endpoint,
        allowed_content_paths=("/openai/v1/videos/*",),
        binding_version=version,
    )


def make_binding(
    *,
    snapshot: FrozenExecutionSnapshot | None = None,
    intent: CommittedIntent | None = None,
    credential_binding: CredentialBinding | None = None,
    owner_scope: OwnerTaskScope | None = None,
) -> HaoaiExecutionBinding:
    actual_snapshot = snapshot or make_snapshot()
    return HaoaiExecutionBinding(
        owner_scope=owner_scope
        or OwnerTaskScope(
            user_id="user-1",
            task_id="task-1",
            operation="video.generate",
        ),
        snapshot=actual_snapshot,
        intent=intent or make_intent(),
        credential_binding=credential_binding or make_credential_binding(),
    )


def make_provider(
    response: FakeResponse | BaseException | None = None,
    *,
    status_code: int = 200,
    body: bytes | None = None,
    binding: HaoaiExecutionBinding | None = None,
    secret: str = SECRET,
    credential: BoundCredential | None = None,
    request_error: BaseException | None = None,
    exit_error: BaseException | None = None,
) -> tuple[HaoaiVideoProvider, dict[str, Any]]:
    if response is None:
        response = FakeResponse(
            status_code,
            body if body is not None else b'{"request_id":"video-1"}',
        )
    responses = [response] if isinstance(response, (FakeResponse, BaseException)) else []
    client = FakeClient(responses, request_error=request_error)
    manager = FakeClientManager(client, exit_error=exit_error)
    counts: dict[str, Any] = {
        "resolver": 0,
        "factory": 0,
        "client": client,
        "manager": manager,
        "credential_calls": [],
    }

    def factory() -> FakeClientManager:
        counts["factory"] += 1
        return manager

    def resolver(
        credential_binding: CredentialBinding, purpose: str
    ) -> BoundCredential | None:
        counts["resolver"] += 1
        counts["credential_calls"].append((credential_binding, purpose))
        return credential or BoundCredential(
            provider=credential_binding.provider,
            principal_id=credential_binding.original_principal_id,
            secret=secret,
        )

    provider = HaoaiVideoProvider(
        binding or make_binding(),
        http_client_factory=factory,
        credential_resolver=resolver,
        utc_clock=lambda: NOW,
    )
    return provider, counts


def make_reference(kind: str, locator: str | None) -> ReferenceInput:
    return ReferenceInput(kind=kind, locator=locator)


def submitted_ref(intent: CommittedIntent | None = None, external_id: str = "video-1"):
    actual_intent = intent or make_intent()
    return ExternalOperationRef(
        intent_id=actual_intent.external_submission_id,
        attempt_id=actual_intent.attempt_id,
        provider="haoai",
        external_id=external_id,
        create_outcome="submitted",
    )


def json_body(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def make_reservation(
    *,
    intent: CommittedIntent | None = None,
    external_id: str = "video-1",
    state: str = "reserved",
) -> CommittedPollReservation:
    actual_intent = intent or make_intent()
    return CommittedPollReservation(
        reservation=PollReservation(
            reservation_id="poll-1",
            group_key="group-1",
            sequence=1,
            attempt_id=actual_intent.attempt_id,
            step_id=actual_intent.step_id,
            submission_id=actual_intent.external_submission_id,
            external_id=external_id,
            payload_digest=actual_intent.request_digest,
            committed_poll_count=0,
            reserved_at=NOW,
            timeout_at=NOW,
            next_poll_at=NOW,
            token="poll-token",
            state=state,  # type: ignore[arg-type]
        ),
        confirmed_at=NOW,
    )


def test_construction_is_inert_and_unsupported_methods_do_not_create_resources() -> None:
    provider, counts = make_provider()

    assert counts["resolver"] == 0
    assert counts["factory"] == 0

    stream = provider.stream_text(make_snapshot(), object())
    assert asyncio.run(anext(stream)).kind == "text_error"
    assert isinstance(provider.optimize_frame(make_snapshot(), "frame", make_intent()), ProviderFailure)
    assert isinstance(provider.generate_sync_image(make_snapshot(), (), make_intent()), Failure)
    assert counts["resolver"] == 0
    assert counts["factory"] == 0


def test_bound_content_fetch_uses_original_id_path_and_credential() -> None:
    body = b"synthetic video bytes"
    response = FakeResponse(
        body=body,
        headers={
            "Content-Type": "video/mp4; charset=binary",
            "Content-Length": str(len(body)),
        },
    )
    provider, counts = make_provider(response)

    fetched = provider.fetch_provider_content(
        make_snapshot(), submitted_ref(external_id="video/one")
    )

    request = counts["client"].calls[0]
    assert fetched == ReferenceInput(
        kind="bytes", locator=None, content=body, media_type="video/mp4"
    )
    assert request["method"] == "GET"
    assert request["url"] == (
        "https://cpa.haoai.icu/openai/v1/videos/video%2Fone/content"
    )
    assert request["headers"] == {
        "Authorization": f"Bearer {SECRET}",
        "Accept": "video/*",
    }
    assert request["follow_redirects"] is False
    assert request["timeout_seconds"] == 600.0
    assert counts["credential_calls"][0][1] == "content"
    assert response.stream.close_calls == 1
    assert response.close_calls == 1
    assert counts["manager"].exit_calls == 1


@pytest.mark.parametrize(
    "slow_stage", ["body", "cleanup", "enter", "combined"]
)
def test_bound_content_deadline_covers_body_and_resource_lifecycle(
    monkeypatch, slow_stage: str
) -> None:
    budget = 0.02 if slow_stage == "combined" else 0.005
    monkeypatch.setattr(haoai_provider_module, "MAX_BOUND_CONTENT_SECONDS", budget)
    response = FakeResponse(
        body=b"synthetic video bytes",
        headers={"Content-Type": "video/mp4"},
    )

    class SlowBody(FakeStream):
        async def __anext__(self) -> object:
            if self.chunks:
                await asyncio.sleep(0.012 if slow_stage == "combined" else 0.02)
            return await super().__anext__()

    class SlowCleanup(FakeStream):
        async def aclose(self) -> None:
            self.close_calls += 1
            await asyncio.sleep(0.012 if slow_stage == "combined" else 0.02)

    class SlowBodyAndCleanup(SlowBody):
        async def aclose(self) -> None:
            self.close_calls += 1
            await asyncio.sleep(0.012)

    if slow_stage == "body":
        response.stream = SlowBody([b"synthetic video bytes"])
    elif slow_stage == "cleanup":
        response.stream = SlowCleanup([b"synthetic video bytes"])
    elif slow_stage == "combined":
        response.stream = SlowBodyAndCleanup([b"synthetic video bytes"])

    original_enter = FakeClientManager.__aenter__
    if slow_stage == "enter":
        async def slow_enter(manager: FakeClientManager) -> FakeClient:
            await asyncio.sleep(0.02)
            return await original_enter(manager)

        monkeypatch.setattr(FakeClientManager, "__aenter__", slow_enter)

    provider, counts = make_provider(response)
    started = time.monotonic()
    with pytest.raises(ValueError, match="provider content is unavailable"):
        provider.fetch_provider_content(make_snapshot(), submitted_ref())
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert counts["factory"] == 1
    if slow_stage == "enter":
        assert counts["client"].calls == []
        assert counts["manager"].exit_calls == 0
    else:
        assert len(counts["client"].calls) == 1
        assert response.stream.close_calls == 1
        assert response.close_calls == 1
        assert counts["manager"].exit_calls == 1


def test_bound_content_external_cancellation_still_propagates() -> None:
    cancellation = asyncio.CancelledError("external cancellation")
    provider, counts = make_provider(request_error=cancellation)

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.fetch_provider_content(make_snapshot(), submitted_ref())

    assert raised.value is cancellation
    assert len(counts["client"].calls) == 1
    assert counts["manager"].exit_calls == 1


@pytest.mark.parametrize(
    "status_code,headers",
    [
        (302, {"Content-Type": "video/mp4"}),
        (200, {"Content-Type": "application/json"}),
        (200, {"Content-Type": "video/mp4", "Content-Length": "52428801"}),
    ],
)
def test_bound_content_rejects_redirect_wrong_type_and_oversize(
    status_code: int, headers: dict[str, str]
) -> None:
    response = FakeResponse(status_code, b"not-a-video", headers=headers)
    provider, counts = make_provider(response)

    with pytest.raises(ValueError, match="provider content is unavailable"):
        provider.fetch_provider_content(make_snapshot(), submitted_ref())

    assert len(counts["client"].calls) == 1
    assert counts["client"].calls[0]["follow_redirects"] is False
    assert response.stream.close_calls == 1
    assert response.close_calls == 1
    assert counts["manager"].exit_calls == 1


def test_running_loop_rejects_create_and_query_before_credential_or_client() -> None:
    provider, counts = make_provider()

    async def call_inside_loop() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            provider.submit_async_media(make_snapshot(), (), make_intent())
        with pytest.raises(RuntimeError, match="running event loop"):
            provider.query_existing(make_snapshot(), submitted_ref())

    asyncio.run(call_inside_loop())
    assert counts["resolver"] == 0
    assert counts["factory"] == 0


@pytest.mark.parametrize(
    ("bad_snapshot", "bad_intent"),
    [
        (make_snapshot(category="image"), make_intent()),
        (make_snapshot(provider="other"), make_intent()),
        (make_snapshot(owner_id="user-2"), make_intent()),
        (make_snapshot(actor_id="user-2"), make_intent()),
        (make_snapshot(), make_intent(provider="other")),
        (make_snapshot(), make_intent(model_id="other-model")),
        (make_snapshot(), make_intent(step_id="other-step")),
        (make_snapshot(), make_intent(attempt_id="other-attempt")),
        (make_snapshot(), make_intent(submission_id="other-submission")),
        (make_snapshot(), make_intent(request_digest="other-digest")),
        (make_snapshot(), make_intent(snapshot_ref="other-execution-snapshot")),
    ],
)
def test_foreign_execution_values_fail_before_effects(
    bad_snapshot: FrozenExecutionSnapshot, bad_intent: CommittedIntent
) -> None:
    provider, counts = make_provider()
    with pytest.raises(ValueError, match="binding is invalid"):
        provider.submit_async_media(bad_snapshot, (), bad_intent)
    assert counts["resolver"] == 0
    assert counts["factory"] == 0


def test_reconstructed_equal_bindings_are_accepted_without_snapshot_identity_aliasing() -> None:
    snapshot = make_snapshot()
    intent = make_intent(snapshot_ref="execution-snapshot-distinct-from-reference-ref")
    credential_binding = make_credential_binding()
    binding = make_binding(
        snapshot=snapshot,
        intent=intent,
        credential_binding=credential_binding,
    )
    reconstructed = make_binding(
        snapshot=replace(snapshot, model_params=dict(snapshot.model_params)),
        intent=replace(intent),
        credential_binding=replace(credential_binding),
        owner_scope=replace(binding.owner_scope),
    )
    provider, counts = make_provider(
        FakeResponse(body=b'{"id":"video-1","status":"queued"}'),
        binding=reconstructed,
    )

    result = provider.submit_async_media(
        replace(snapshot, model_params=dict(snapshot.model_params)),
        (),
        replace(intent),
    )

    assert result.intent_id == intent.external_submission_id
    assert result.attempt_id == intent.attempt_id
    assert result.external_id == "video-1"
    assert result.create_outcome == "submitted"
    assert result.last_safe_provider_state == "queued"
    assert counts["resolver"] == 1
    assert counts["factory"] == 1


def test_direct_credential_resolution_rejects_foreign_binding_before_resolver() -> None:
    provider, counts = make_provider()

    with pytest.raises(ValueError, match="binding is invalid"):
        provider.resolve_original_credential(
            replace(make_credential_binding(), row_id="foreign-row"), "create"
        )

    assert counts["resolver"] == 0
    assert counts["factory"] == 0


def test_direct_credential_resolution_rejects_foreign_owner_scope_before_resolver() -> None:
    foreign_scope = OwnerTaskScope(
        user_id="user-2",
        task_id="task-1",
        operation="video.generate",
    )
    binding = make_binding(owner_scope=foreign_scope)
    provider, counts = make_provider(binding=binding)

    with pytest.raises(ValueError, match="binding is invalid"):
        provider.resolve_original_credential(binding.credential_binding, "create")

    assert counts["resolver"] == 0
    assert counts["factory"] == 0


@pytest.mark.parametrize(
    "changed_binding",
    [
        replace(make_credential_binding(), source="other_source"),
        replace(make_credential_binding(), owner_or_system="user-2"),
        replace(make_credential_binding(), provider="other"),
        replace(
            make_credential_binding(),
            original_principal_id="principal-2",
        ),
        replace(make_credential_binding(), row_id="row-2"),
        replace(
            make_credential_binding(),
            endpoint_origin="https://other.example/v1",
        ),
        replace(
            make_credential_binding(),
            binding_version="binding-v2",
        ),
    ],
)
def test_direct_credential_resolution_rejects_every_foreign_original_binding_field(
    changed_binding: CredentialBinding,
) -> None:
    provider, counts = make_provider()

    with pytest.raises(ValueError, match="binding is invalid"):
        provider.resolve_original_credential(changed_binding, "query")

    assert counts["resolver"] == 0
    assert counts["factory"] == 0


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://cpa.haoai.icu/v1",
        "https://user@cpa.haoai.icu/v1",
        "https://cpa.haoai.icu/v1?token=unexpected",
        "https://cpa.haoai.icu/v1#fragment",
    ],
)
def test_untrusted_endpoint_shape_fails_before_credential_or_client_effects(
    endpoint: str,
) -> None:
    snapshot = make_snapshot(endpoint=endpoint)
    credential_binding = make_credential_binding(endpoint=endpoint)
    provider, counts = make_provider(
        binding=make_binding(
            snapshot=snapshot,
            credential_binding=credential_binding,
        )
    )

    with pytest.raises(ValueError, match="binding is invalid"):
        provider.submit_async_media(snapshot, (), make_intent())

    assert counts["resolver"] == 0
    assert counts["factory"] == 0


@pytest.mark.parametrize(
    ("provider_name", "principal_id", "secret"),
    [
        ("other", "principal-1", SECRET),
        ("haoai", "different-principal", SECRET),
        ("haoai", "principal-1", ""),
        ("haoai", "principal-1", "bad\nsecret"),
    ],
)
def test_resolver_never_accepts_a_foreign_or_malformed_credential(
    provider_name: str, principal_id: str, secret: str
) -> None:
    provider, counts = make_provider(
        credential=BoundCredential(
            provider=provider_name,
            principal_id=principal_id,
            secret=secret,
        )
    )

    result = provider.submit_async_media(make_snapshot(), (), make_intent())

    assert result.create_outcome == "definitive_failure"
    assert result.external_id is None
    assert counts["resolver"] == 1
    assert counts["factory"] == 0


def test_default_reference_cap_is_nine_and_truncates_before_kind_projection() -> None:
    snapshot = make_snapshot()
    refs = tuple(
        make_reference("public_http", f"https://cdn.example/{index}.png")
        for index in range(9)
    ) + (ReferenceInput(kind="bytes", content=b"outside selected cap"),)
    provider, counts = make_provider(
        FakeResponse(body=b'{"id":"video-1"}'),
        binding=make_binding(snapshot=snapshot),
    )

    provider.submit_async_media(snapshot, refs, make_intent())

    payload = json.loads(counts["client"].calls[0]["content"])
    assert len(payload["reference_images"]) == 9
    assert payload["reference_images"][0] == {"url": "https://cdn.example/0.png"}
    assert payload["prompt"].endswith(
        "参考图：" + " ".join(f"<IMAGE_{index}>" for index in range(1, 10))
    )


def test_explicit_wire_model_is_distinct_from_catalog_id_and_source_defaults_are_preserved() -> None:
    snapshot = make_snapshot(
        model_params={"duration": 0, "size": "", "resolution": ""},
        model_name="",
    )
    provider, counts = make_provider(
        FakeResponse(body=b'{"id":"video-1"}'),
        binding=make_binding(snapshot=snapshot),
    )

    result = provider.submit_async_media(snapshot, (), make_intent())

    payload = json.loads(counts["client"].calls[0]["content"])
    assert result.external_id == "video-1"
    assert payload == {
        "model": DEFAULT_HAOAI_VIDEO_MODEL,
        "prompt": "A quiet coast at dawn",
        "seconds": 4,
        "aspect_ratio": "16:9",
    }
    assert payload["model"] != snapshot.model_ref.model_id


def test_truthy_duration_conversion_resolution_and_multi_reference_wire_shape_match_codec() -> None:
    snapshot = make_snapshot(
        model_params={
            "duration": "5",
            "size": "9:16",
            "resolution": "1080p",
            "max_reference_images": {"default": 2},
        },
        model_name="wire-grok-model",
    )
    refs = (
        make_reference("public_http", "https://cdn.example/one%2f.png?sig=A"),
        make_reference("data_uri", "data:image/png;base64,Zm9v"),
        make_reference("managed_url", "https://cdn.example/three.png"),
    )
    provider, counts = make_provider(
        FakeResponse(body=b'{"request_id":"video-1"}'),
        binding=make_binding(snapshot=snapshot),
    )

    provider.submit_async_media(snapshot, refs, make_intent())

    payload = json.loads(counts["client"].calls[0]["content"])
    assert payload["model"] == "wire-grok-model"
    assert payload["seconds"] == 5
    assert payload["aspect_ratio"] == "9:16"
    assert payload["resolution"] == "1080p"
    assert payload["reference_images"] == [
        {"url": "https://cdn.example/one%2f.png?sig=A"},
        {"url": "data:image/png;base64,Zm9v"},
    ]
    assert payload["prompt"] == (
        "A quiet coast at dawn\n\n参考图：<IMAGE_1> <IMAGE_2>"
    )
    assert all("role" not in item for item in payload["reference_images"])


def test_single_prepared_reference_uses_image_url_and_preserves_signed_query_bytes() -> None:
    locator = "https://CDN.example/a%2Fb?api_key=ordinary&access_token=short&signature=AbC%2f"
    snapshot = make_snapshot()
    provider, counts = make_provider(
        FakeResponse(body=b'{"id":"video-1"}'),
        binding=make_binding(snapshot=snapshot),
    )

    provider.submit_async_media(
        snapshot,
        (make_reference("managed_url", locator),),
        make_intent(),
    )

    payload = json.loads(counts["client"].calls[0]["content"])
    assert payload["input_reference"] == {"image_url": locator}
    assert "reference_images" not in payload


def test_unprepared_reference_is_rejected_before_resolver_or_http() -> None:
    snapshot = make_snapshot()
    provider, counts = make_provider(binding=make_binding(snapshot=snapshot))

    result = provider.submit_async_media(
        snapshot,
        (ReferenceInput(kind="bytes", locator=None, content=b"raw"),),
        make_intent(),
    )

    assert result.create_outcome == "definitive_failure"
    assert result.external_id is None
    assert counts["resolver"] == 0
    assert counts["factory"] == 0


def test_truthy_non_integer_duration_fails_before_provider_effects() -> None:
    snapshot = make_snapshot(model_params={"duration": "5.9"})
    provider, counts = make_provider(binding=make_binding(snapshot=snapshot))

    result = provider.submit_async_media(snapshot, (), make_intent())

    assert result.create_outcome == "definitive_failure"
    assert result.last_safe_provider_state == "invalid_request"
    assert counts["resolver"] == 0
    assert counts["factory"] == 0


def test_create_targets_exact_https_url_with_bearer_json_no_redirects_and_120_seconds() -> None:
    snapshot = make_snapshot()
    provider, counts = make_provider(binding=make_binding(snapshot=snapshot))

    result = provider.submit_async_media(snapshot, (), make_intent())

    call = counts["client"].calls[0]
    assert result.create_outcome == "submitted"
    assert call["method"] == "POST"
    assert call["url"] == "https://cpa.haoai.icu/openai/v1/videos"
    assert call["headers"] == {
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json",
    }
    assert call["timeout_seconds"] == 120.0
    assert call["follow_redirects"] is False
    assert counts["manager"].enter_calls == 1
    assert counts["manager"].exit_calls == 1


def test_terminal_v1_is_normalized_once_for_a_trusted_origin() -> None:
    endpoint = "https://gateway.example/proxy/v1/v1"
    snapshot = make_snapshot(endpoint=endpoint)
    credential_binding = make_credential_binding(endpoint=endpoint)
    provider, counts = make_provider(
        binding=make_binding(
            snapshot=snapshot,
            credential_binding=credential_binding,
        )
    )

    provider.submit_async_media(snapshot, (), make_intent())

    assert counts["client"].calls[0]["url"] == (
        "https://gateway.example/proxy/v1/openai/v1/videos"
    )


@pytest.mark.parametrize(
    ("status", "body", "outcome", "external_id"),
    [
        (200, b'{"request_id":"preferred","id":"fallback"}', "submitted", "preferred"),
        (200, b'{"request_id":"","id":"fallback"}', "submitted", "fallback"),
        (200, b'{"request_id":7,"id":"fallback"}', "unknown", None),
        (200, b'{"id":"fallback"}', "submitted", "fallback"),
        (200, b'{"status":"queued"}', "unknown", None),
        (200, b'[]', "unknown", None),
        (200, b'{"id":', "unknown", None),
        (201, b'{"id":"created"}', "unknown", None),
        (202, b'{"id":"accepted"}', "unknown", None),
        (400, b'{"error":"secret details"}', "definitive_failure", None),
        (429, b'{"error":"secret details"}', "definitive_failure", None),
        (500, b'{"id":"must-not-be-success"}', "unknown", None),
    ],
)
def test_create_projection_requires_exact_200_and_preserves_id_selection(
    status: int, body: bytes, outcome: str, external_id: str | None
) -> None:
    provider, _counts = make_provider(FakeResponse(status, body))

    result = provider.submit_async_media(make_snapshot(), (), make_intent())

    assert result.create_outcome == outcome
    assert result.external_id == external_id


def test_unsafe_selected_request_id_does_not_fall_back_to_safe_id() -> None:
    provider, counts = make_provider(
        FakeResponse(
            body=json_body(
                {"request_id": quote(SECRET, safe=""), "id": "safe-fallback"}
            )
        )
    )

    result = provider.submit_async_media(make_snapshot(), (), make_intent())

    assert result.create_outcome == "unknown"
    assert result.external_id is None
    assert result.last_safe_provider_state == "provider_identity_unusable"
    assert counts["client"].calls[0]["method"] == "POST"


def test_ambiguous_create_is_unknown_and_repeated_call_does_not_post_again() -> None:
    provider, counts = make_provider(
        request_error=RuntimeError(f"transport failure included {SECRET}")
    )

    first = provider.submit_async_media(make_snapshot(), (), make_intent())
    second = provider.submit_async_media(make_snapshot(), (), make_intent())

    assert first.create_outcome == "unknown"
    assert second == first
    assert counts["resolver"] == 1
    assert counts["factory"] == 1
    assert len(counts["client"].calls) == 1
    assert SECRET not in repr(first)


@pytest.mark.parametrize("expected_outcome", ["unknown", "submitted"])
def test_started_create_result_survives_invalid_reference_retry(
    expected_outcome: str,
) -> None:
    if expected_outcome == "unknown":
        provider, counts = make_provider(
            request_error=RuntimeError("POST outcome may be unknown")
        )
    else:
        provider, counts = make_provider(
            FakeResponse(body=b'{"request_id":"remembered-video"}')
        )

    snapshot = make_snapshot()
    intent = make_intent()
    first = provider.submit_async_media(snapshot, (), intent)

    assert first.create_outcome == expected_outcome
    second = provider.submit_async_media(
        snapshot, (make_reference("bytes", None),), intent
    )

    assert second == first
    assert second.intent_id == intent.external_submission_id
    assert second.attempt_id == intent.attempt_id
    assert second.provider == "haoai"
    assert counts["resolver"] == 1
    assert counts["factory"] == 1
    assert len(counts["client"].calls) == 1
    assert counts["client"].calls[0]["method"] == "POST"
    assert counts["manager"].enter_calls == 1
    assert counts["manager"].exit_calls == 1


def test_started_create_result_does_not_override_mismatched_original_binding() -> None:
    provider, counts = make_provider(
        request_error=RuntimeError("POST outcome may be unknown")
    )
    snapshot = make_snapshot()
    intent = make_intent()
    first = provider.submit_async_media(snapshot, (), intent)

    with pytest.raises(ValueError, match="binding"):
        provider.submit_async_media(
            snapshot,
            (make_reference("bytes", None),),
            replace(intent, request_digest="different-digest"),
        )

    assert first.create_outcome == "unknown"
    assert counts["resolver"] == 1
    assert counts["factory"] == 1
    assert len(counts["client"].calls) == 1


def test_started_create_result_survives_concurrent_invalid_preflight() -> None:
    provider, counts = make_provider(
        request_error=RuntimeError("POST outcome may be unknown")
    )
    snapshot = make_snapshot()
    intent = make_intent()
    preflight_started = threading.Event()
    resume_preflight = threading.Event()
    invalid_results: list[object] = []
    thread_errors: list[BaseException] = []

    class BlockingInvalidReferences:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, index: slice) -> tuple[ReferenceInput, ...]:
            preflight_started.set()
            if not resume_preflight.wait(timeout=5):
                raise TimeoutError("reference preflight was not resumed")
            return (make_reference("bytes", None),)

    def submit_invalid() -> None:
        try:
            invalid_results.append(
                provider.submit_async_media(
                    snapshot, BlockingInvalidReferences(), intent  # type: ignore[arg-type]
                )
            )
        except BaseException as exc:
            thread_errors.append(exc)

    worker = threading.Thread(target=submit_invalid, daemon=True)
    worker.start()
    try:
        assert preflight_started.wait(timeout=2)
        first = provider.submit_async_media(snapshot, (), intent)
    finally:
        resume_preflight.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert not thread_errors
    assert first.create_outcome == "unknown"
    assert invalid_results == [first]
    assert counts["resolver"] == 1
    assert counts["factory"] == 1
    assert len(counts["client"].calls) == 1
    assert counts["client"].calls[0]["method"] == "POST"
    assert counts["manager"].enter_calls == 1
    assert counts["manager"].exit_calls == 1


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("request timed out"),
        RuntimeError(f"request failed with {SECRET}"),
    ],
)
def test_timeout_or_transport_exception_returns_unknown_without_retry(
    error: BaseException,
) -> None:
    provider, counts = make_provider(request_error=error)

    result = provider.submit_async_media(make_snapshot(), (), make_intent())

    assert result.create_outcome == "unknown"
    assert result.external_id is None
    assert SECRET not in repr(result)
    assert len(counts["client"].calls) == 1
    assert counts["factory"] == 1


def test_query_encodes_original_external_id_once_and_never_posts() -> None:
    raw_id = "part/one?x=%2f"
    external_ref = submitted_ref(external_id=raw_id)
    provider, counts = make_provider(
        FakeResponse(body=b'{"status":"in_progress","progress":50}')
    )

    observation = provider.query_existing(make_snapshot(), external_ref)

    call = counts["client"].calls[0]
    assert call["method"] == "GET"
    assert call["url"] == (
        "https://cpa.haoai.icu/openai/v1/videos/part%2Fone%3Fx%3D%252f"
    )
    assert call["timeout_seconds"] == 30.0
    assert call["follow_redirects"] is False
    assert call["headers"] == {
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json",
    }
    assert observation.status == "unknown"
    assert counts["resolver"] == 1
    assert counts["factory"] == 1


def test_query_accepts_equal_reservation_and_rejects_mismatched_or_unknown_ref_before_effects() -> None:
    provider, counts = make_provider(
        FakeResponse(body=b'{"status":"queued"}')
    )
    observation = provider.query_existing(
        make_snapshot(),
        submitted_ref(),
        make_reservation(),
    )
    assert observation.status == "unknown"
    assert counts["factory"] == 1

    second, second_counts = make_provider(FakeResponse(body=b'{"status":"queued"}'))
    with pytest.raises(ValueError, match="binding is invalid"):
        second.query_existing(
            make_snapshot(),
            submitted_ref(),
            make_reservation(external_id="different-id"),
        )
    with pytest.raises(ValueError, match="binding is invalid"):
        second.query_existing(
            make_snapshot(),
            ExternalOperationRef(
                intent_id="submission-1",
                attempt_id="attempt-1",
                provider="haoai",
                external_id=None,
                create_outcome="unknown",
            ),
        )
    assert second_counts["resolver"] == 0
    assert second_counts["factory"] == 0


@pytest.mark.parametrize(
    "foreign_ref",
    [
        ExternalOperationRef(
            intent_id="foreign-submission",
            attempt_id="attempt-1",
            provider="haoai",
            external_id="video-1",
            create_outcome="submitted",
        ),
        ExternalOperationRef(
            intent_id="submission-1",
            attempt_id="foreign-attempt",
            provider="haoai",
            external_id="video-1",
            create_outcome="submitted",
        ),
        ExternalOperationRef(
            intent_id="submission-1",
            attempt_id="attempt-1",
            provider="other",
            external_id="video-1",
            create_outcome="submitted",
        ),
        ExternalOperationRef(
            intent_id="submission-1",
            attempt_id="attempt-1",
            provider="haoai",
            external_id="video-1",
            create_outcome="unknown",
        ),
    ],
)
def test_query_rejects_foreign_or_unknown_external_ref_before_effects(
    foreign_ref: ExternalOperationRef,
) -> None:
    provider, counts = make_provider()

    with pytest.raises(ValueError, match="binding is invalid"):
        provider.query_existing(make_snapshot(), foreign_ref)

    assert counts["resolver"] == 0
    assert counts["factory"] == 0


@pytest.mark.parametrize(
    ("source_status", "expected_status"),
    [
        ("completed", "completed"),
        ("done", "completed"),
        ("failed", "failed"),
        ("queued", "unknown"),
        ("in_progress", "unknown"),
        ("mystery", "unknown"),
        (None, "unknown"),
        ({"arbitrary": "status"}, "unknown"),
    ],
)
def test_query_projects_only_whitelisted_source_terminal_statuses(
    source_status: object, expected_status: str
) -> None:
    body = json_body({"status": source_status, "progress": {"arbitrary": 99}})
    provider, _counts = make_provider(FakeResponse(body=body))

    observation = provider.query_existing(make_snapshot(), submitted_ref())

    assert observation.status == expected_status
    assert observation.payload.get("source_fact") in {
        "source_completed",
        "source_failed",
        "source_unknown",
    }
    assert "progress" not in observation.payload
    if expected_status == "completed":
        assert observation.payload["source_status"] in ("completed", "done")
    elif expected_status == "failed":
        assert observation.payload["source_status"] == "failed"
    elif source_status in ("queued", "in_progress"):
        assert observation.payload["source_status"] == source_status
    else:
        assert "source_status" not in observation.payload


def test_query_uses_codec_locator_precedence_and_keeps_safe_locator_bytes() -> None:
    locator = "https://CDN.example/video%2f.mp4?api_key=ordinary&signature=A%2f"
    body = json_body(
        {
            "status": "completed",
            "video_url": locator,
            "video": {"url": "https://lower.example/video.mp4"},
            "url": "https://last.example/video.mp4",
            "error": {"message": f"ignored {SECRET}"},
            "progress": SECRET,
        }
    )
    provider, counts = make_provider(FakeResponse(body=body))

    observation = provider.query_existing(make_snapshot(), submitted_ref())

    assert observation.status == "completed"
    assert observation.media_locator == locator
    assert observation.payload["locator_source"] == "video_url"
    assert SECRET not in repr(observation)
    assert counts["factory"] == 1


@pytest.mark.parametrize(
    ("payload", "expected_locator"),
    [
        (
            {
                "status": "completed",
                "video_url": "",
                "video": {"url": "https://cdn.example/nested.mp4"},
            },
            "https://cdn.example/nested.mp4",
        ),
        (
            {
                "status": "done",
                "video": "https://cdn.example/string.mp4",
                "url": "https://lower.example/last.mp4",
            },
            "https://cdn.example/string.mp4",
        ),
        (
            {
                "status": "done",
                "video_url": "",
                "video": "",
                "url": "https://cdn.example/url.mp4",
            },
            "https://cdn.example/url.mp4",
        ),
    ],
)
def test_query_uses_lower_priority_locator_branch_after_falsey_fields(
    payload: dict[str, object], expected_locator: str
) -> None:
    provider, _counts = make_provider(FakeResponse(body=json_body(payload)))

    observation = provider.query_existing(make_snapshot(), submitted_ref())

    assert observation.status == "completed"
    assert observation.media_locator == expected_locator


def test_query_completion_without_locator_is_bound_content_needed_only() -> None:
    provider, _counts = make_provider(
        FakeResponse(body=b'{"status":"done","video_url":"","video":{},"url":""}')
    )

    observation = provider.query_existing(make_snapshot(), submitted_ref())

    assert observation.status == "completed"
    assert observation.media_locator is None
    assert observation.payload["content_status"] == "bound_content_needed"
    assert observation.payload["content_path_suffix"] == "/content"
    assert observation.safe_error is None


def test_unsafe_selected_locator_is_dropped_without_lower_priority_or_content_fallback() -> None:
    body = json_body(
        {
            "status": "completed",
            "video_url": f"https://cdn.example/video.mp4?token={quote(SECRET, safe='')}",
            "url": "https://safe.example/lower-priority.mp4",
        }
    )
    provider, counts = make_provider(FakeResponse(body=body))

    observation = provider.query_existing(make_snapshot(), submitted_ref())

    assert observation.status == "completed"
    assert observation.media_locator is None
    assert observation.safe_error == "provider media locator is unsafe"
    assert observation.payload["content_status"] == "unsafe_locator"
    assert "content_path_suffix" not in observation.payload
    assert counts["client"].calls[0]["method"] == "GET"


def test_query_body_digest_dedupe_clock_and_safe_payload_are_deterministic() -> None:
    body = json_body(
        {
            "status": "failed",
            "error": {"message": f"raw upstream details {SECRET}"},
            "progress": {"raw": "private"},
        }
    )
    provider, _counts = make_provider(FakeResponse(body=body))

    observation = provider.query_existing(make_snapshot(), submitted_ref())

    assert observation.status == "failed"
    assert observation.safe_error == "provider reported generation failure"
    assert observation.received_at == NOW
    assert len(observation.payload_digest) == 64
    assert len(observation.dedupe_key) == 64
    assert observation.external_ref == submitted_ref()
    serialized = repr(observation)
    assert SECRET not in serialized
    assert "raw upstream details" not in serialized
    assert "progress" not in serialized
    assert observation.payload["source_fact"] == "source_failed"


def test_secret_in_status_is_projected_to_unknown_without_persisting_source_text() -> None:
    provider, _counts = make_provider(
        FakeResponse(body=json_body({"status": SECRET, "progress": SECRET}))
    )

    observation = provider.query_existing(make_snapshot(), submitted_ref())

    assert observation.status == "unknown"
    assert "source_status" not in observation.payload
    assert SECRET not in repr(observation)


def test_protocol_marker_membership_is_fixed_and_non200_auth_scan_stops_at_500() -> None:
    provider, _counts = make_provider(
        FakeResponse(
            body=json_body(
                {
                    "status": "failed",
                    "error": {
                    "message": (
                        "image_download; failed to download the provided image; "
                        "downloading the image"
                    )
                    },
                }
            )
        )
    )
    image_observation = provider.query_existing(make_snapshot(), submitted_ref())
    assert image_observation.payload["image_download_markers"] == (
        "image_download",
        "failed to download the provided image",
        "downloading the image",
    )

    auth_provider, _ = make_provider(
        FakeResponse(
            503,
            b"x" * 500 + b" no auth available",
        )
    )
    auth_observation = auth_provider.query_existing(
        make_snapshot(), submitted_ref()
    )
    assert auth_observation.status == "unknown"
    assert "auth_unavailable_markers" not in auth_observation.payload

    first_500_provider, _ = make_provider(
        FakeResponse(503, b"no auth available " + b"x" * 490)
    )
    first_500 = first_500_provider.query_existing(
        make_snapshot(), submitted_ref()
    )
    assert first_500.payload["auth_unavailable_markers"] == (
        "no auth available",
    )


def test_recognized_secret_shadows_are_rejected_and_signed_query_is_preserved() -> None:
    percent_once = quote(SECRET, safe="")
    percent_mixed = percent_once.replace("%2F", "%2f").replace("%2B", "%2b")
    percent_three = SECRET
    for _ in range(3):
        percent_three = quote(percent_three, safe="")
    json_escaped = r"\u0073\u0033cr3t+\/value"
    base64_value = base64.b64encode(SECRET.encode()).decode()
    base64url_value = base64.urlsafe_b64encode(SECRET.encode()).decode().rstrip("=")
    candidates = (
        SECRET,
        percent_mixed,
        percent_three,
        json_escaped,
        f"https://cdn.example/video?token={quote_plus(SECRET)}",
        base64_value,
        base64url_value,
    )

    for candidate in candidates:
        assert not _safe_response_candidate(candidate, SECRET, kind="id")
        assert not _safe_response_candidate(
            f"https://cdn.example/{candidate}", SECRET, kind="url"
        )

    overflow = SECRET
    for _ in range(9):
        overflow = quote(overflow, safe="")
    assert not _safe_response_candidate(overflow, SECRET, kind="id")
    assert not _safe_response_candidate("bad%2", SECRET, kind="id")
    assert not _safe_response_candidate(
        "x" * 8193,
        SECRET,
        kind="id",
    )

    signed = "https://CDN.example/a%2Fb?api_key=ordinary&access_token=short&signature=AbC%2f"
    assert _safe_response_candidate(signed, SECRET, kind="url")


@pytest.mark.parametrize(
    ("secret", "encoded"),
    [
        ("abc", "YWJj"),
        ("ab", "YWI="),
        ("abcd", "YWJjZA"),
        ("00>", "MDA-"),
    ],
)
@pytest.mark.parametrize("projection", ["id", "url"])
def test_short_base64_credential_shadows_are_rejected(
    secret: str, encoded: str, projection: str
) -> None:
    if projection == "id":
        provider, counts = make_provider(
            FakeResponse(
                body=json_body(
                    {"request_id": encoded, "id": "safe-fallback"}
                )
            ),
            secret=secret,
        )

        result = provider.submit_async_media(make_snapshot(), (), make_intent())

        assert result.create_outcome == "unknown"
        assert result.external_id is None
        assert result.last_safe_provider_state == "provider_identity_unusable"
        assert counts["client"].calls[0]["method"] == "POST"
        assert len(counts["client"].calls) == 1
        assert counts["resolver"] == 1
        assert counts["factory"] == 1
        return

    locator = f"https://cdn.example/video.mp4?token={encoded}"
    provider, counts = make_provider(
        FakeResponse(
            body=json_body(
                {"status": "completed", "video_url": locator}
            )
        ),
        secret=secret,
    )

    observation = provider.query_existing(make_snapshot(), submitted_ref())

    assert observation.status == "completed"
    assert observation.media_locator is None
    assert observation.safe_error == "provider media locator is unsafe"
    assert observation.payload["content_status"] == "unsafe_locator"
    assert "content_path_suffix" not in observation.payload
    assert counts["client"].calls[0]["method"] == "GET"
    assert len(counts["client"].calls) == 1
    assert counts["resolver"] == 1
    assert counts["factory"] == 1


def test_short_credentials_preserve_unrelated_signed_locator_bytes() -> None:
    locator = (
        "https://CDN.example/video%2f.mp4?api_key=ordinary&signature=A%2f"
    )
    for secret in ("abc", "ab", "abcd", "00>"):
        provider, counts = make_provider(
            FakeResponse(
                body=json_body(
                    {"status": "completed", "video_url": locator}
                )
            ),
            secret=secret,
        )

        observation = provider.query_existing(make_snapshot(), submitted_ref())

        assert observation.media_locator == locator
        assert counts["client"].calls[0]["method"] == "GET"
        assert len(counts["client"].calls) == 1


def test_content_length_and_aggregate_body_limit_accept_exact_limit_and_reject_one_byte_more() -> None:
    prefix = b'{"id":"video-1","pad":"'
    suffix = b'"}'
    exact_body = prefix + b"a" * (MAX_RESPONSE_BYTES - len(prefix) - len(suffix)) + suffix
    provider, counts = make_provider(FakeResponse(body=exact_body))

    accepted = provider.submit_async_media(make_snapshot(), (), make_intent())

    assert accepted.create_outcome == "submitted"
    assert accepted.external_id == "video-1"
    assert counts["client"].calls[0]["content"] is not None
    assert counts["manager"].exit_calls == 1

    too_large_body = prefix + b"a" * (MAX_RESPONSE_BYTES + 1 - len(prefix) - len(suffix)) + suffix
    overflow_provider, overflow_counts = make_provider(
        FakeResponse(body=too_large_body)
    )
    rejected = overflow_provider.submit_async_media(
        make_snapshot(), (), make_intent()
    )
    assert rejected.create_outcome == "unknown"
    assert rejected.external_id is None
    assert overflow_counts["manager"].exit_calls == 1


def test_declared_oversize_and_malformed_chunks_fail_closed_and_close_all_resources() -> None:
    declared = FakeResponse(
        body=b'{"id":"must-not-read"}',
        headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)},
    )
    provider, counts = make_provider(declared)

    result = provider.submit_async_media(make_snapshot(), (), make_intent())

    assert result.create_outcome == "unknown"
    assert declared.stream.close_calls == 1
    assert declared.close_calls == 1
    assert counts["manager"].exit_calls == 1

    malformed = FakeResponse(
        chunks=[b"not-bytes"],
    )
    malformed_provider, malformed_counts = make_provider(malformed)
    malformed_result = malformed_provider.submit_async_media(
        make_snapshot(), (), make_intent()
    )
    assert malformed_result.create_outcome == "unknown"
    assert malformed.stream.close_calls == 1
    assert malformed.close_calls == 1
    assert malformed_counts["manager"].exit_calls == 1


def test_non_bytes_response_chunk_is_rejected_and_closes_owned_resources() -> None:
    response = FakeResponse(chunks=["not-bytes"])
    provider, counts = make_provider(response)

    result = provider.submit_async_media(make_snapshot(), (), make_intent())

    assert result.create_outcome == "unknown"
    assert response.stream.close_calls == 1
    assert response.close_calls == 1
    assert counts["manager"].exit_calls == 1


@pytest.mark.parametrize("direct", [False, True])
def test_async_and_direct_iterators_use_64k_and_close_exact_owned_instances(
    direct: bool,
) -> None:
    response = (
        DirectIteratorResponse(body=b'{"id":"video-1"}')
        if direct
        else FakeResponse(body=b'{"id":"video-1"}')
    )
    provider, counts = make_provider(response)

    result = provider.submit_async_media(make_snapshot(), (), make_intent())

    assert result.external_id == "video-1"
    assert response.chunk_sizes == [RESPONSE_CHUNK_BYTES]
    assert response.stream.close_calls == 1
    assert response.close_calls == 1
    assert counts["manager"].exit_calls == 1


def test_original_request_cancellation_wins_over_iterator_response_and_client_cleanup() -> None:
    cancel_a = asyncio.CancelledError("original iterator cancellation")
    cancel_b = asyncio.CancelledError("iterator close cancellation")
    cancel_c = asyncio.CancelledError("response close cancellation")
    cancel_d = asyncio.CancelledError("client close cancellation")
    response = FakeResponse(
        body=b"",
        iterator_error=cancel_a,
        iterator_close_error=cancel_b,
        response_close_error=cancel_c,
    )
    provider, counts = make_provider(response, exit_error=cancel_d)

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.submit_async_media(make_snapshot(), (), make_intent())

    assert raised.value is cancel_a
    assert response.stream.close_calls == 1
    assert response.close_calls == 1
    assert counts["manager"].exit_calls == 1


def test_request_cancellation_wins_over_client_cleanup_cancellation() -> None:
    cancel_a = asyncio.CancelledError("original request cancellation")
    cancel_b = asyncio.CancelledError("client cleanup cancellation")
    provider, counts = make_provider(
        request_error=cancel_a,
        exit_error=cancel_b,
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.submit_async_media(make_snapshot(), (), make_intent())

    assert raised.value is cancel_a
    assert counts["manager"].exit_calls == 1


def test_cleanup_cancellation_propagates_after_non_cancel_request_failure() -> None:
    cleanup_cancel = asyncio.CancelledError("cleanup cancellation")
    provider, counts = make_provider(
        request_error=RuntimeError("ordinary request failure"),
        exit_error=cleanup_cancel,
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.submit_async_media(make_snapshot(), (), make_intent())

    assert raised.value is cleanup_cancel
    assert counts["manager"].exit_calls == 1


def test_success_body_still_attempts_remaining_closes_before_cleanup_cancellation() -> None:
    cleanup_cancel = asyncio.CancelledError("response close cancellation")
    response = FakeResponse(
        body=b'{"id":"video-1"}',
        response_close_error=cleanup_cancel,
    )
    provider, counts = make_provider(response, exit_error=RuntimeError("close error"))

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.submit_async_media(make_snapshot(), (), make_intent())

    assert raised.value is cleanup_cancel
    assert response.stream.close_calls == 1
    assert response.close_calls == 1
    assert counts["manager"].exit_calls == 1


def test_query_iterator_cancellation_wins_over_all_cleanup_cancellations() -> None:
    cancel_a = asyncio.CancelledError("original query iterator cancellation")
    cancel_b = asyncio.CancelledError("query iterator close cancellation")
    cancel_c = asyncio.CancelledError("query response close cancellation")
    cancel_d = asyncio.CancelledError("query client close cancellation")
    response = FakeResponse(
        body=b"",
        iterator_error=cancel_a,
        iterator_close_error=cancel_b,
        response_close_error=cancel_c,
    )
    provider, counts = make_provider(response, exit_error=cancel_d)

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.query_existing(make_snapshot(), submitted_ref())

    assert raised.value is cancel_a
    assert response.stream.close_calls == 1
    assert response.close_calls == 1
    assert counts["manager"].exit_calls == 1
    assert counts["client"].calls[0]["method"] == "GET"
    assert len(counts["client"].calls) == 1
    assert counts["resolver"] == 1
    assert counts["factory"] == 1


def test_query_request_cancellation_wins_over_client_cleanup_cancellation() -> None:
    cancel_a = asyncio.CancelledError("original query request cancellation")
    cancel_b = asyncio.CancelledError("query client cleanup cancellation")
    provider, counts = make_provider(
        request_error=cancel_a,
        exit_error=cancel_b,
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.query_existing(make_snapshot(), submitted_ref())

    assert raised.value is cancel_a
    assert counts["manager"].exit_calls == 1
    assert counts["client"].calls[0]["method"] == "GET"
    assert len(counts["client"].calls) == 1
    assert counts["resolver"] == 1
    assert counts["factory"] == 1


def test_query_cleanup_cancellation_propagates_after_non_cancel_failure() -> None:
    cleanup_cancel = asyncio.CancelledError("query cleanup cancellation")
    provider, counts = make_provider(
        request_error=RuntimeError("query request failed"),
        exit_error=cleanup_cancel,
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.query_existing(make_snapshot(), submitted_ref())

    assert raised.value is cleanup_cancel
    assert counts["manager"].exit_calls == 1
    assert counts["client"].calls[0]["method"] == "GET"
    assert len(counts["client"].calls) == 1


def test_query_success_still_attempts_all_closes_before_cleanup_cancellation() -> None:
    cleanup_cancel = asyncio.CancelledError("query response close cancellation")
    response = FakeResponse(
        body=b'{"status":"in_progress"}',
        response_close_error=cleanup_cancel,
    )
    provider, counts = make_provider(
        response,
        exit_error=RuntimeError("query client close failure"),
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.query_existing(make_snapshot(), submitted_ref())

    assert raised.value is cleanup_cancel
    assert response.stream.close_calls == 1
    assert response.close_calls == 1
    assert counts["manager"].exit_calls == 1
    assert counts["client"].calls[0]["method"] == "GET"
    assert len(counts["client"].calls) == 1


def test_read_failure_followed_by_cleanup_cancellation_propagates_cleanup() -> None:
    cleanup_cancel = asyncio.CancelledError("cleanup cancellation")
    response = FakeResponse(
        body=b"",
        iterator_error=RuntimeError(f"read failed {SECRET}"),
        response_close_error=cleanup_cancel,
    )
    provider, counts = make_provider(response)

    with pytest.raises(asyncio.CancelledError) as raised:
        provider.submit_async_media(make_snapshot(), (), make_intent())

    assert raised.value is cleanup_cancel
    assert response.stream.close_calls == 1
    assert response.close_calls == 1
    assert counts["manager"].exit_calls == 1
