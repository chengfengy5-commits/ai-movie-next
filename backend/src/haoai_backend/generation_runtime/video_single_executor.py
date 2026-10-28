"""Run one newly committed ordinary ``video.single`` intent."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select, update
from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import (
    BoundCredential,
    CancellationSignal,
    ClaimAuthority,
    CommittedIntent,
    ExternalOperationRef,
    PreparedReference,
    ProviderObservation,
    ReferenceInput,
    OwnerTaskScope,
    StoredMedia,
    TransferFailure,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    UserRow,
)
from haoai_backend.generation_core.haoai_group.intents import (
    SqlAlchemyHaoaiGroupIntents,
)
from haoai_backend.generation_core.haoai_group.media import (
    SqlAlchemyHaoaiGroupMedia,
)
from haoai_backend.generation_core.haoai_group.polls import (
    HaoaiGroupPollGrant,
    SqlAlchemyHaoaiGroupPolls,
)
from haoai_backend.generation_core.haoai_group.results import (
    SqlAlchemyHaoaiGroupResults,
)
from haoai_backend.generation_core.video_single_intents import (
    SqlAlchemyVideoSingleIntents,
)
from haoai_backend.generation_core.video_single_results import (
    SqlAlchemyVideoSingleResults,
)
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
)
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
)
from haoai_backend.generation_providers.video.manxiaobai_provider import (
    ManxiaobaiVideoProvider,
)
from haoai_backend.generation_providers.video.aistarslab_codec import (
    MAX_REFERENCE_AUDIOS,
    MAX_REFERENCE_IMAGES,
    MAX_REFERENCE_VIDEOS,
)
from haoai_backend.generation_providers.video.aistarslab_provider import (
    AistarslabReferenceInputs,
    AistarslabVideoProvider,
)
from haoai_backend.generation_providers.video.xai_provider import XaiVideoProvider
from haoai_backend.generation_providers.video.haoai_provider import (
    HaoaiExecutionBinding,
    HaoaiVideoProvider,
)
from haoai_backend.media_uploads.domain import (
    MAX_VIDEO_BYTES,
    InvalidMediaUpload,
    upload_spec,
    validate_upload_content,
)

PollWaiter = Callable[[CancellationSignal, float], bool]
_POLL_POLICY: dict[str, tuple[int, float]] = {
    "xai": (1200, 3.0),
    "manxiaobai": (720, 5.0),
    "aistarslab": (540, 10.0),
}
_PENDING_STATES = frozenset({"pending", "queued", "processing"})
_COMPLETED_STATES = frozenset({"completed", "done"})
_VIDEO_MEDIA_TYPES = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-m4v": ".m4v",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "video/mpeg": ".mpg",
}


def _wait_for_stop(signal: CancellationSignal, seconds: float) -> bool:
    wait = getattr(signal, "wait", None)
    if callable(wait):
        return bool(wait(seconds))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if signal.is_set():
            return True
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    return signal.is_set()


class _ExecutionAuthorityLost(RuntimeError):
    pass


class _AuthorityGuard:
    def __init__(
        self,
        configuration: GenerationRuntimeConfig,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified,
    ) -> None:
        self._configuration = configuration
        self._claim = claim
        self._intent = intent
        self._verified = verified
        self._provider_request_started = False

    @property
    def provider_request_started(self) -> bool:
        return self._provider_request_started

    def mark_provider_request_started(self) -> None:
        self._provider_request_started = True

    def is_live(self) -> bool:
        claim = self._claim
        if claim.local_lost_signal.is_set():
            return False
        try:
            with self._configuration.session_factory() as session:
                with session.begin():
                    user_id = session.execute(
                        select(UserRow.id)
                        .where(UserRow.id == claim.scope.user_id)
                        .with_for_update(skip_locked=True)
                    ).scalar_one_or_none()
                    task_table = AITaskRow.__table__
                    task = session.execute(
                        select(task_table)
                        .where(
                            task_table.c.id == claim.scope.task_id,
                            task_table.c.user_id == claim.scope.user_id,
                            task_table.c.type == "video-single",
                        )
                        .with_for_update(skip_locked=True)
                    ).mappings().one_or_none()
                    unit_table = BillingUnitRow.__table__
                    unit = session.execute(
                        select(unit_table)
                        .where(
                            unit_table.c.id == self._verified.unit_id,
                            unit_table.c.task_id == claim.scope.task_id,
                            unit_table.c.operation == "video.single",
                        )
                        .with_for_update(skip_locked=True)
                    ).mappings().one_or_none()
                    now = self._configuration.db_clock()
                    if (
                        user_id is None
                        or task is None
                        or unit is None
                        or not _aware_datetime(now)
                    ):
                        return False
                    task = dict(task)
                    unit = dict(unit)
                    lease_until = task.get("lease_until")
                    deadline = unit.get("deadline_at")
                    now_utc = _aware_utc(now)
                    live = (
                        not claim.local_lost_signal.is_set()
                        and task.get("status") in {"processing", "reconciling"}
                        and task.get("user_cancelled_at") is None
                        and task.get("claimed_by") == claim.worker_instance
                        and task.get("claim_token") == claim.token
                        and task.get("execution_generation")
                        == claim.execution_generation
                        and isinstance(lease_until, datetime)
                        and _aware_utc(lease_until) > now_utc
                        and unit.get("first_intent_at")
                        == _database_datetime(self._intent.first_intent_at)
                        and deadline == _database_datetime(self._intent.deadline_at)
                        and now_utc < _aware_utc(self._intent.deadline_at)
                        and unit.get("user_cancelled_at") is None
                    )
                    if not live:
                        return False
                    confirmed = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == claim.scope.task_id,
                            task_table.c.user_id == claim.scope.user_id,
                            task_table.c.type == "video-single",
                            task_table.c.status == task.get("status"),
                            task_table.c.status.in_(("processing", "reconciling")),
                            task_table.c.user_cancelled_at.is_(None),
                            task_table.c.claimed_by == claim.worker_instance,
                            task_table.c.claim_token == claim.token,
                            task_table.c.execution_generation
                            == claim.execution_generation,
                            task_table.c.lease_until
                            == _database_datetime(_aware_utc(lease_until)),
                            task_table.c.lease_until
                            > _database_datetime(now_utc),
                        )
                        .values(updated_at=task_table.c.updated_at)
                    )
                    return (
                        confirmed.rowcount == 1
                        and not claim.local_lost_signal.is_set()
                    )
        except Exception:
            return False

    def require_live(self) -> None:
        if not self.is_live():
            raise _ExecutionAuthorityLost


class _GatedHttpClient:
    def __init__(self, client: object, guard: _AuthorityGuard) -> None:
        self._client = client
        self._guard = guard

    async def request(self, *args: object, **kwargs: object):
        self._guard.require_live()
        self._guard.mark_provider_request_started()
        return await self._client.request(*args, **kwargs)


class _GatedHttpContext:
    def __init__(self, factory, guard: _AuthorityGuard) -> None:
        self._factory = factory
        self._guard = guard
        self._context = None

    async def __aenter__(self):
        self._guard.require_live()
        context = self._factory()
        self._guard.require_live()
        client = await context.__aenter__()
        try:
            self._guard.require_live()
        except BaseException:
            await context.__aexit__(*sys.exc_info())
            raise
        self._context = context
        return _GatedHttpClient(client, self._guard)

    async def __aexit__(self, exc_type, exc, traceback):
        if self._context is None:
            return False
        return await self._context.__aexit__(exc_type, exc, traceback)


class _GatedContextClient:
    """Adapt a managed client context to the Manxiaobai direct-client port."""

    def __init__(self, factory, guard: _AuthorityGuard) -> None:
        self._factory = factory
        self._guard = guard
        self._context = None
        self._client = None

    async def request(self, *args: object, **kwargs: object):
        self._guard.require_live()
        if self._client is None:
            context = self._factory()
            self._guard.require_live()
            client = await context.__aenter__()
            try:
                self._guard.require_live()
            except BaseException:
                await context.__aexit__(*sys.exc_info())
                raise
            self._context = context
            self._client = client
        self._guard.require_live()
        self._guard.mark_provider_request_started()
        return await self._client.request(*args, **kwargs)

    async def aclose(self) -> None:
        context = self._context
        self._context = None
        self._client = None
        if context is not None:
            await context.__aexit__(None, None, None)


class _GatedPublicMediaFetcher:
    def __init__(self, fetcher: object, guard: _AuthorityGuard) -> None:
        self._fetcher = fetcher
        self._guard = guard

    async def fetch_public(self, *args: object, **kwargs: object):
        self._guard.require_live()
        result = await self._fetcher.fetch_public(*args, **kwargs)
        self._guard.require_live()
        return result


class VideoSingleExecutor:
    """Execute one fresh normal-video intent without retrying a create."""

    def __init__(
        self,
        configuration: GenerationRuntimeConfig,
        registry_capabilities: RegistryCapabilities,
        *,
        poll_waiter: PollWaiter = _wait_for_stop,
    ) -> None:
        if not isinstance(configuration, GenerationRuntimeConfig):
            raise TypeError("video.single runtime configuration is required")
        if not isinstance(registry_capabilities, RegistryCapabilities):
            raise TypeError("video.single registry capabilities are required")
        if not callable(poll_waiter):
            raise TypeError("poll_waiter must be callable")
        self._configuration = configuration
        self._registry_capabilities = registry_capabilities
        self._poll_waiter = poll_waiter
        self._execution_port: object | None = None

    def bind_execution_port(self, execution_port: object) -> None:
        if not all(
            callable(getattr(execution_port, name, None))
            for name in ("claim_next", "renew_or_revoke", "release_claim")
        ):
            raise TypeError("video.single executor requires its claim port")
        self._execution_port = execution_port

    def execute_claim(self, claim: ClaimAuthority):
        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != "video.single"
            or claim.local_lost_signal.is_set()
            or self._execution_port is None
        ):
            return "ownership_lost"
        if _is_haoai_group_claim(self._execution_port, claim):
            return self._execute_haoai_group_claim(claim)
        crypto = self._registry_capabilities.crypto
        proof_authority = self._registry_capabilities.proof_authority
        if not isinstance(crypto, CredentialCrypto) or not isinstance(
            proof_authority, HmacProofAuthority
        ):
            return "pending"

        registry = build_credential_registry(self._registry_capabilities)
        intents = SqlAlchemyVideoSingleIntents(
            self._configuration.session_factory,
            self._configuration.db_clock,
            self._configuration.id_factory,
            registry,
            crypto,
            proof_authority,
        )
        results = SqlAlchemyVideoSingleResults(
            self._configuration.session_factory,
            self._configuration.db_clock,
            self._configuration.id_factory,
        )
        prepared = intents.prepare(claim)
        if prepared.disposition == "ownership_lost":
            return "ownership_lost"
        if prepared.disposition == "resume_query":
            intent = prepared.intent
            verified = prepared.verified_input
            external_ref = prepared.external_ref
            if (
                intent is None
                or verified is None
                or external_ref is None
                or not _external_ref_matches(external_ref, intent)
                or external_ref.create_outcome != "submitted"
                or not _safe_provider_id(external_ref.external_id)
            ):
                return "pending"
            guard = _AuthorityGuard(self._configuration, claim, intent, verified)
            try:
                guard.require_live()
                provider = self._build_provider(verified, intent, guard)
                guard.require_live()
            except _ExecutionAuthorityLost:
                return "ownership_lost"
            except Exception:
                return "pending" if guard.is_live() else "ownership_lost"
            return self._poll_and_store(
                claim,
                intent,
                verified,
                external_ref,
                provider,
                results,
                guard,
            )
        if (
            prepared.disposition != "committed"
            or prepared.intent is None
            or prepared.verified_input is None
        ):
            # Existing intents are durable facts, not permission to create again.
            return "pending"

        intent = prepared.intent
        verified = prepared.verified_input
        guard = _AuthorityGuard(self._configuration, claim, intent, verified)
        try:
            guard.require_live()
            references = self._prepare_references(verified, guard)
        except _ExecutionAuthorityLost:
            return "ownership_lost"
        except Exception:
            if not guard.is_live():
                return "ownership_lost"
            return results.fail(
                claim,
                intent,
                verified,
                code="provider_transport_unavailable",
                safe_detail="视频参考输入未能安全准备",
                provider_request_started=False,
            )
        if isinstance(references, TransferFailure):
            if not guard.is_live():
                return "ownership_lost"
            return results.fail(
                claim,
                intent,
                verified,
                code=references.code,
                safe_detail=references.safe_detail,
                provider_request_started=False,
            )

        try:
            guard.require_live()
            provider = self._build_provider(verified, intent, guard)
            guard.require_live()
        except _ExecutionAuthorityLost:
            return "ownership_lost"
        except Exception:
            if not guard.is_live():
                return "ownership_lost"
            return results.fail(
                claim,
                intent,
                verified,
                code="configuration_unavailable",
                safe_detail="原视频服务配置暂不可用",
                provider_request_started=False,
            )

        try:
            external_ref = provider.submit_async_media(
                verified.snapshot,
                references,
                intent,
            )
        except Exception:
            if not guard.is_live():
                return "ownership_lost"
            if guard.provider_request_started:
                return results.mark_unknown(claim, intent, verified)
            return results.fail(
                claim,
                intent,
                verified,
                code="configuration_unavailable",
                safe_detail="视频生成请求未能安全发出",
                provider_request_started=False,
            )

        if not guard.is_live():
            return "ownership_lost"
        if not _external_ref_matches(external_ref, intent):
            return results.mark_unknown(claim, intent, verified)
        if external_ref.create_outcome == "definitive_failure":
            return results.fail(
                claim,
                intent,
                verified,
                code="provider_rejected",
                safe_detail="视频服务拒绝了本次请求",
                provider_request_started=True,
            )
        if external_ref.create_outcome == "unknown":
            return results.record_submission(
                claim, intent, verified, external_ref
            )
        if (
            external_ref.create_outcome != "submitted"
            or not _safe_provider_id(external_ref.external_id)
        ):
            return results.mark_unknown(claim, intent, verified)

        recorded = results.record_submission(
            claim, intent, verified, external_ref
        )
        if recorded != "recorded":
            return recorded
        return self._poll_and_store(
            claim,
            intent,
            verified,
            external_ref,
            provider,
            results,
            guard,
        )

    def _execute_haoai_group_claim(self, claim: ClaimAuthority):
        crypto = self._registry_capabilities.crypto
        proof_authority = self._registry_capabilities.proof_authority
        if not isinstance(crypto, CredentialCrypto) or not isinstance(
            proof_authority, HmacProofAuthority
        ):
            return "pending"

        configuration = self._configuration
        registry = build_credential_registry(self._registry_capabilities)
        intents = SqlAlchemyHaoaiGroupIntents(
            configuration.session_factory,
            configuration.db_clock,
            configuration.id_factory,
            registry,
            crypto,
            proof_authority,
        )
        results = SqlAlchemyHaoaiGroupResults(
            configuration.session_factory,
            configuration.db_clock,
            proof_authority,
            configuration.id_factory,
        )
        polls = SqlAlchemyHaoaiGroupPolls(
            configuration.session_factory,
            configuration.db_clock,
            configuration.id_factory,
            registry,
            crypto,
            proof_authority,
        )
        media = SqlAlchemyHaoaiGroupMedia(
            configuration.session_factory,
            configuration.db_clock,
            proof_authority,
            results,
            configuration.id_factory,
        )

        prepared = intents.prepare(claim)
        if prepared.disposition == "ownership_lost":
            return "ownership_lost"
        if prepared.disposition in {"unresolved", "existing"}:
            return "pending"
        intent = prepared.intent
        verified = prepared.verified_input
        if intent is None or verified is None:
            return "pending"

        guard = _AuthorityGuard(configuration, claim, intent, verified)
        if prepared.disposition == "committed":
            try:
                guard.require_live()
                references = self._prepare_references(verified, guard)
            except _ExecutionAuthorityLost:
                return "ownership_lost"
            except Exception:
                return "pending" if guard.is_live() else "ownership_lost"
            if isinstance(references, TransferFailure):
                return "pending" if guard.is_live() else "ownership_lost"
            try:
                guard.require_live()
                provider = self._build_provider(verified, intent, guard)
                guard.require_live()
            except _ExecutionAuthorityLost:
                return "ownership_lost"
            except Exception:
                return "pending" if guard.is_live() else "ownership_lost"
            try:
                guard.require_live()
                external_ref = provider.submit_async_media(
                    verified.snapshot,
                    references,
                    intent,
                )
            except _ExecutionAuthorityLost:
                return "ownership_lost"
            except Exception:
                if not guard.is_live():
                    return "ownership_lost"
                external_ref = ExternalOperationRef(
                    intent_id=intent.external_submission_id,
                    attempt_id=intent.attempt_id,
                    provider=intent.provider,
                    external_id=None,
                    create_outcome="unknown",
                )
            if not guard.is_live():
                return "ownership_lost"
            if not _external_ref_matches(external_ref, intent):
                return "pending"
            recorded = intents.record_submission(
                claim, intent, verified, external_ref
            )
            if recorded != "recorded":
                return recorded
            if (
                external_ref.create_outcome != "submitted"
                or not _safe_provider_id(external_ref.external_id)
            ):
                return "pending"
        elif prepared.disposition == "resume_query":
            external_ref = prepared.external_ref
            if (
                external_ref is None
                or not _external_ref_matches(external_ref, intent)
                or external_ref.create_outcome != "submitted"
                or not _safe_provider_id(external_ref.external_id)
            ):
                return "pending"
            try:
                guard.require_live()
                provider = self._build_provider(verified, intent, guard)
                guard.require_live()
            except _ExecutionAuthorityLost:
                return "ownership_lost"
            except Exception:
                return "pending" if guard.is_live() else "ownership_lost"
        elif prepared.disposition == "resume_media":
            external_ref = prepared.external_ref
            if (
                external_ref is None
                or not _external_ref_matches(external_ref, intent)
                or external_ref.create_outcome != "submitted"
                or not _safe_provider_id(external_ref.external_id)
            ):
                return "pending"
            try:
                guard.require_live()
                provider = self._build_provider(verified, intent, guard)
                guard.require_live()
                store = configuration.media_store_factory()
                guard.require_live()
            except _ExecutionAuthorityLost:
                return "ownership_lost"
            except Exception:
                return "pending" if guard.is_live() else "ownership_lost"
            outcome = media.recover_completed_materialization(
                claim,
                intent,
                verified,
                external_ref,
                provider,
                store,
            )
            if outcome.disposition == "completed" and outcome.receipt is not None:
                return outcome.receipt
            return outcome.disposition
        else:
            return "pending"

        try:
            guard.require_live()
            grant = polls.reserve_query(claim, intent, verified, external_ref)
            if not isinstance(grant, HaoaiGroupPollGrant):
                return (
                    "ownership_lost"
                    if grant.disposition == "ownership_lost"
                    else "pending"
                )
            guard.require_live()
            poll_outcome = polls.query_and_accept(grant, provider)
        except _ExecutionAuthorityLost:
            return "ownership_lost"
        except Exception:
            return "pending" if guard.is_live() else "ownership_lost"
        if not guard.is_live():
            return "ownership_lost"
        if poll_outcome.disposition != "accepted" or poll_outcome.observation is None:
            return "pending"
        if poll_outcome.observation.status == "failed":
            return (
                None
                if results.settle_known_failure(claim.scope) == "settled"
                else "pending"
            )
        if poll_outcome.observation.status not in {"completed", "done"}:
            return "pending"
        try:
            guard.require_live()
            store = configuration.media_store_factory()
            guard.require_live()
        except _ExecutionAuthorityLost:
            return "ownership_lost"
        except Exception:
            return "pending" if guard.is_live() else "ownership_lost"
        outcome = media.materialize(
            claim,
            intent,
            verified,
            grant,
            poll_outcome,
            provider,
            store,
        )
        if outcome.disposition == "completed" and outcome.receipt is not None:
            return outcome.receipt
        return outcome.disposition

    def _prepare_references(self, verified, guard: _AuthorityGuard):
        if verified.snapshot.model_ref.provider == "aistarslab":
            return self._prepare_aistarslab_references(verified, guard)

        image_references = verified.reference_images
        if not image_references:
            return ()

        store = self._configuration.media_store_factory()
        prepared: list[ReferenceInput] = []
        for locator in image_references:
            if not _valid_reference_url(locator):
                return TransferFailure(
                    "invalid_frozen_snapshot",
                    "视频参考地址无法通过安全检查",
                )
            guard.require_live()
            reference = ReferenceInput(kind="public_http", locator=locator)
            normalized = store.prepare_reference(
                reference,
                None,
                self._configuration,
            )
            guard.require_live()
            if (
                not isinstance(normalized, PreparedReference)
                or normalized.kind not in {"public_http", "managed_url"}
                or not _valid_reference_url(normalized.locator)
                or normalized.content is not None
            ):
                return TransferFailure(
                    "invalid_frozen_snapshot",
                    "视频参考地址无法通过安全检查",
                )
            prepared.append(
                ReferenceInput(
                    kind=normalized.kind,
                    locator=normalized.locator,
                )
            )
        return tuple(prepared)

    def _prepare_aistarslab_references(
        self,
        verified,
        guard: _AuthorityGuard,
    ) -> AistarslabReferenceInputs | TransferFailure:
        groups = (
            (verified.reference_images, MAX_REFERENCE_IMAGES),
            (verified.reference_audios, MAX_REFERENCE_AUDIOS),
            (verified.reference_videos, MAX_REFERENCE_VIDEOS),
        )
        if any(len(references) > maximum for references, maximum in groups):
            return TransferFailure(
                "invalid_frozen_snapshot",
                "视频参考素材数量超过 AIStarsLab 支持范围",
            )

        if not any(references for references, _maximum in groups):
            return AistarslabReferenceInputs()

        store = self._configuration.media_store_factory()
        prepared_groups: list[tuple[ReferenceInput, ...]] = []
        for locators, _maximum in groups:
            prepared: list[ReferenceInput] = []
            for locator in locators:
                guard.require_live()
                if not _valid_reference_url(locator):
                    return TransferFailure(
                        "invalid_frozen_snapshot",
                        "视频参考地址无法通过安全检查",
                    )
                normalized = store.prepare_reference(
                    ReferenceInput(kind="public_http", locator=locator),
                    None,
                    self._configuration,
                )
                guard.require_live()
                if (
                    not isinstance(normalized, PreparedReference)
                    or normalized.kind not in {"public_http", "managed_url"}
                    or not _valid_reference_url(normalized.locator)
                    or normalized.content is not None
                ):
                    return TransferFailure(
                        "invalid_frozen_snapshot",
                        "视频参考地址无法通过安全检查",
                    )
                prepared.append(
                    ReferenceInput(
                        kind=normalized.kind,
                        locator=normalized.locator,
                    )
                )
            prepared_groups.append(tuple(prepared))

        return AistarslabReferenceInputs(
            images=prepared_groups[0],
            audios=prepared_groups[1],
            videos=prepared_groups[2],
        )

    def _build_provider(self, verified, intent, guard: _AuthorityGuard):
        snapshot = verified.snapshot
        binding = verified.credential_binding
        if snapshot.model_ref.provider in {"xai", "aistarslab", "haoai"}:
            http_factory = lambda: _GatedHttpContext(
                self._configuration.http_client_factory,
                guard,
            )
        else:
            http_factory = lambda: _GatedContextClient(
                self._configuration.http_client_factory,
                guard,
            )
        resolver = lambda original, purpose: self._resolve_credential(
            original,
            purpose,
            guard,
        )
        if snapshot.model_ref.provider == "aistarslab":
            return AistarslabVideoProvider(
                owner_scope=OwnerTaskScope(
                    snapshot.grant_snapshot.actor_id,
                    verified.task_id,
                    "video.single",
                ),
                frozen_snapshot=snapshot,
                committed_intent=intent,
                original_model_reference=snapshot.model_ref,
                original_credential_binding=binding,
                credential_binding_ref=snapshot.credential_binding_ref,
                wire_model=snapshot.model_name,
                http_client_factory=http_factory,
                credential_resolver=resolver,
                clock=self._configuration.db_clock,
            )
        if snapshot.model_ref.provider == "xai":
            return XaiVideoProvider(
                owner_scope=OwnerTaskScope(
                    verified.snapshot.grant_snapshot.actor_id,
                    verified.task_id,
                    "video.single",
                ),
                original_snapshot=snapshot,
                original_intent=intent,
                credential_binding=binding,
                credential_binding_ref=snapshot.credential_binding_ref,
                snapshot_ref=intent.snapshot_ref,
                model_ref=snapshot.model_ref,
                wire_model=snapshot.model_name,
                http_client_factory=http_factory,
                credential_resolver=resolver,
                clock=self._configuration.db_clock,
            )
        if snapshot.model_ref.provider == "manxiaobai":
            return ManxiaobaiVideoProvider(
                owner_scope=OwnerTaskScope(
                    snapshot.grant_snapshot.actor_id,
                    verified.task_id,
                    "video.single",
                ),
                snapshot_ref=intent.snapshot_ref,
                frozen_snapshot=snapshot,
                committed_intent=intent,
                original_model_reference=snapshot.model_ref,
                trusted_model_owner_id=snapshot.model_ref.owner_id,
                credential_binding_ref=snapshot.credential_binding_ref,
                original_credential_binding=binding,
                trusted_binding_owner_or_system=binding.owner_or_system,
                trusted_binding_principal_id=binding.original_principal_id,
                wire_model=snapshot.model_name,
                credential_resolver=resolver,
                http_client_factory=http_factory,
                clock=self._configuration.db_clock,
            )
        if snapshot.model_ref.provider == "haoai":
            haoai_binding = HaoaiExecutionBinding(
                owner_scope=OwnerTaskScope(
                    snapshot.grant_snapshot.actor_id,
                    verified.task_id,
                    "video.single",
                ),
                snapshot=snapshot,
                intent=intent,
                credential_binding=binding,
            )
            return HaoaiVideoProvider(
                binding=haoai_binding,
                http_client_factory=http_factory,
                credential_resolver=resolver,
                utc_clock=self._configuration.db_clock,
            )
        raise GenerationError("unsupported_provider", "video provider is unsupported")

    def _resolve_credential(self, binding, purpose, guard: _AuthorityGuard):
        guard.require_live()
        try:
            credential = self._configuration.credential_resolver(binding, purpose)
        except Exception:
            return "unavailable"
        guard.require_live()
        if credential is None:
            return "unavailable"
        if not isinstance(credential, BoundCredential):
            return "unavailable"
        return credential

    def _poll_and_store(
        self,
        claim,
        intent,
        verified,
        external_ref: ExternalOperationRef,
        provider,
        results: SqlAlchemyVideoSingleResults,
        guard: _AuthorityGuard,
    ):
        policy = _POLL_POLICY.get(intent.provider)
        if policy is None:
            return results.mark_unknown(claim, intent, verified)
        max_queries, interval_seconds = policy
        for index in range(max_queries):
            if not guard.is_live():
                return "ownership_lost"
            try:
                observation = provider.query_existing(
                    verified.snapshot,
                    external_ref,
                    None,
                )
            except Exception:
                return (
                    results.mark_unknown(claim, intent, verified)
                    if guard.is_live()
                    else "ownership_lost"
                )
            if not guard.is_live():
                return "ownership_lost"
            if not _observation_matches(observation, claim, intent, external_ref):
                return results.mark_unknown(claim, intent, verified)
            status = observation.status
            if status == "failed":
                return results.fail(
                    claim,
                    intent,
                    verified,
                    code="provider_rejected",
                    safe_detail="视频服务报告本次任务失败",
                    provider_request_started=True,
                )
            if status in _COMPLETED_STATES:
                if not _valid_video_locator(observation.media_locator):
                    return results.mark_unknown(claim, intent, verified)
                return self._fetch_and_store(
                    claim,
                    intent,
                    verified,
                    observation,
                    results,
                    guard,
                )
            if status not in _PENDING_STATES:
                return results.mark_unknown(claim, intent, verified)
            if index + 1 >= max_queries:
                break
            try:
                stopped = self._poll_waiter(
                    claim.local_lost_signal,
                    interval_seconds,
                )
            except Exception:
                return (
                    results.mark_unknown(claim, intent, verified)
                    if guard.is_live()
                    else "ownership_lost"
                )
            if stopped or not guard.is_live():
                return "ownership_lost"
        return results.mark_unknown(claim, intent, verified)

    def _fetch_and_store(
        self,
        claim,
        intent,
        verified,
        observation: ProviderObservation,
        results: SqlAlchemyVideoSingleResults,
        guard: _AuthorityGuard,
    ):
        locator = observation.media_locator
        if not _valid_video_locator(locator):
            return results.mark_unknown(claim, intent, verified)
        try:
            guard.require_live()
            fetcher = self._configuration.public_media_fetcher_factory()
            guard.require_live()
            fetched = asyncio.run(
                _GatedPublicMediaFetcher(fetcher, guard).fetch_public(
                    ReferenceInput(kind="public_http", locator=locator),
                    max_bytes=MAX_VIDEO_BYTES,
                    timeout_seconds=(
                        self._configuration.timeout_settings.public_media_fetch_seconds
                    ),
                )
            )
        except _ExecutionAuthorityLost:
            return "ownership_lost"
        except Exception:
            return (
                results.mark_unknown(claim, intent, verified)
                if guard.is_live()
                else "ownership_lost"
            )
        if (
            not isinstance(fetched, PreparedReference)
            or fetched.kind != "bytes"
            or fetched.locator is not None
            or type(fetched.content) is not bytes
            or not isinstance(fetched.media_type, str)
        ):
            return results.mark_unknown(claim, intent, verified)
        validated = _validate_video(fetched.content, fetched.media_type)
        if validated is None:
            return results.mark_unknown(claim, intent, verified)
        content, media_type = validated
        digest = hashlib.sha256(content).hexdigest()
        reference = ReferenceInput(
            kind="bytes",
            content=content,
            media_type=media_type,
        )
        try:
            guard.require_live()
            store = self._configuration.media_store_factory()
            guard.require_live()
            # Check immediately before the irreversible managed-media write.
            guard.require_live()
            stored = store.store_bytes_or_provider_content(
                reference,
                {"max_bytes": MAX_VIDEO_BYTES},
            )
        except _ExecutionAuthorityLost:
            self._record_late_video(claim, intent, verified, results, digest)
            return "ownership_lost"
        except Exception:
            self._record_late_video(claim, intent, verified, results, digest)
            return (
                results.mark_unknown(claim, intent, verified)
                if guard.is_live()
                else "ownership_lost"
            )
        if not isinstance(stored, StoredMedia) or not _valid_stored_media(
            stored,
            digest,
            media_type,
            len(content),
        ):
            self._record_late_video(claim, intent, verified, results, digest)
            return results.mark_unknown(claim, intent, verified)
        if not guard.is_live():
            self._record_late_video(claim, intent, verified, results, digest)
            return "ownership_lost"
        outcome = results.complete(claim, intent, verified, stored)
        if not _is_result_receipt(outcome):
            self._record_late_video(claim, intent, verified, results, digest)
        return outcome

    @staticmethod
    def _record_late_video(claim, intent, verified, results, digest: str) -> None:
        results.record_unknown_observation(
            claim,
            intent,
            verified,
            payload_digest=digest,
            result_location=None,
            evidence_kind="provider_video_bytes",
        )


def _external_ref_matches(value: object, intent: CommittedIntent) -> bool:
    return (
        type(value) is ExternalOperationRef
        and value.intent_id == intent.external_submission_id
        and value.attempt_id == intent.attempt_id
        and value.provider == intent.provider
    )


def _is_haoai_group_claim(port: object, claim: ClaimAuthority) -> bool:
    classifier = getattr(port, "is_group_claim", None)
    if not callable(classifier):
        return False
    try:
        return classifier(claim) is True
    except Exception:
        return False


def _observation_matches(
    value: object,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    external_ref: ExternalOperationRef,
) -> bool:
    if type(value) is not ProviderObservation:
        return False
    observed_ref = value.external_ref
    return (
        value.owner_scope == claim.scope
        and value.intent_id == intent.external_submission_id
        and value.attempt_id == intent.attempt_id
        and type(observed_ref) is ExternalOperationRef
        and observed_ref.intent_id == external_ref.intent_id
        and observed_ref.attempt_id == external_ref.attempt_id
        and observed_ref.provider == external_ref.provider
        and observed_ref.external_id == external_ref.external_id
        and observed_ref.create_outcome == "submitted"
        and isinstance(value.status, str)
        and len(value.status) <= 32
        and not any(ord(character) < 0x20 for character in value.status)
    )


def _safe_provider_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value.encode("utf-8")) <= 255
        and not any(ord(character) < 0x20 for character in value)
    )


def _valid_reference_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
        or "\\" in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
        _ = parsed.port
        return valid
    except ValueError:
        return False


def _valid_video_locator(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or value != value.strip()
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
        or "\\" in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
        _ = parsed.port
        return valid
    except ValueError:
        return False


def _validate_video(content: bytes, media_type: str) -> tuple[bytes, str] | None:
    extension = _VIDEO_MEDIA_TYPES.get(media_type)
    if not isinstance(content, bytes) or not content or len(content) > MAX_VIDEO_BYTES:
        return None
    if extension is None:
        return None
    try:
        spec = upload_spec("video", f"generated-video{extension}")
        if spec.content_type != media_type:
            return None
        validate_upload_content(spec, content)
    except (InvalidMediaUpload, ValueError):
        return None
    return content, media_type


def _valid_stored_media(
    stored: StoredMedia,
    digest: str,
    media_type: str,
    size_bytes: int,
) -> bool:
    try:
        parsed = urlsplit(stored.locator)
        safe_locator = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
        _ = parsed.port
    except ValueError:
        safe_locator = False
    return (
        safe_locator
        and bool(stored.locator)
        and not stored.locator.startswith("data:")
        and stored.content_digest == digest
        and stored.media_type == media_type
        and stored.size_bytes == size_bytes
    )


def _is_result_receipt(value: object) -> bool:
    from haoai_backend.generation_contracts.records import GenerationResultReceipt

    return isinstance(value, GenerationResultReceipt)


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        # SQLite may return UTC database timestamps without timezone metadata.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


__all__ = ("VideoSingleExecutor",)
