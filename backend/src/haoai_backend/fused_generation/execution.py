"""One-shot asynchronous driver for admitted fused-image tasks."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import TypeVar
from collections.abc import Mapping
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from haoai_backend.generation_contracts import StoredMedia
from haoai_backend.generation_contracts.types import BoundCredential
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    EndpointPolicy,
    HmacProofAuthority,
)

from .domain import (
    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
    FUSED_UNKNOWN_REASON,
    FusedGenerationInput,
    FusedGenerationInlineImage,
    FusedGenerationStorageUnknown,
    FusedGenerationOutput,
    FusedGenerationUnknown,
    FusedGenerationUnknownReason,
    FusedTaskClaim,
    PreparedFusedGeneration,
)
from .errors import FusedGenerationDispatchedCancellation, ProviderGenerationFailure
from .durable_application import (
    load_durable_prepared_snapshot,
    load_durable_provider_result,
)
from .durable_values import decode_result, encode_result, result_digest, seal_json_value
from .ports import (
    FusedGenerationImageStorage,
    FusedGenerationRuntime,
    FusedGenerationStorage,
    FusedGenerationUnitOfWorkFactory,
)


@dataclass(frozen=True, slots=True)
class FusedGenerationExecution:
    runtime: FusedGenerationRuntime | None
    uow_factory: FusedGenerationUnitOfWorkFactory | None
    storage: FusedGenerationStorage | None = None
    inline_image_storage: FusedGenerationImageStorage | None = None
    credential_registry: CredentialRegistry | None = None
    credential_crypto: CredentialCrypto | None = None
    proof_authority: HmacProofAuthority | None = None

    async def execute_submission(self, submission_id: str, owner_id: str) -> None:
        """Run or safely recover one accepted durable submission."""

        if (
            self.runtime is None
            or self.uow_factory is None
            or self.credential_registry is None
            or self.credential_crypto is None
            or self.proof_authority is None
        ):
            return
        row = await run_in_threadpool(
            _read_durable_submission,
            self.uow_factory,
            submission_id,
            owner_id,
        )
        if row is None:
            return
        if row.get("stage") == "snapshot_ready":
            accepting = asyncio.create_task(
                run_in_threadpool(
                    _accept_durable_snapshot_for_recovery,
                    self.uow_factory,
                    submission_id,
                    owner_id,
                    self.credential_crypto,
                    self.proof_authority,
                )
            )
            try:
                _response, cancellation_requested = await _drain_owned_worker(accepting)
            except Exception:
                return
            if cancellation_requested:
                raise asyncio.CancelledError
            row = await run_in_threadpool(
                _read_durable_submission,
                self.uow_factory,
                submission_id,
                owner_id,
            )
            if row is None:
                return
        if row.get("stage") == "dispatching":
            await _retain_interrupted_dispatch(self.uow_factory, submission_id)
            return
        if row.get("stage") == "media_started":
            await _retain_interrupted_media(self.uow_factory, submission_id)
            return
        if row.get("stage") not in {"accepted", "result_known"}:
            return

        prepared = load_durable_prepared_snapshot(
            row,
            crypto=self.credential_crypto,
            proof_authority=self.proof_authority,
        )
        if prepared is None:
            return
        claim_token = token_urlsafe(32)
        claim, cancellation_requested = await _durable_claim(
            self.uow_factory,
            prepared,
            claim_token,
        )
        if cancellation_requested:
            if claim is not None:
                await _finish_after_cancellation(
                    self.uow_factory,
                    prepared,
                    claim_token,
                    "任务被取消",
                )
            raise asyncio.CancelledError
        if claim is None or claim.generation is None:
            return
        if claim.cancelled:
            await self._finish(prepared, claim_token, image_url=None, error_message="任务被取消")
            return

        if claim.stage == "result_known":
            result_document = load_durable_provider_result(
                row,
                crypto=self.credential_crypto,
            )
            output = decode_result(result_document)
            if output is None or result_digest(output) != row.get("result_digest"):
                await self._retain_unknown(
                    prepared,
                    claim_token,
                    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
                )
                return
            await self._deliver_durable_result(
                prepared,
                claim_token,
                claim.generation,
                output,
            )
            return

        await self._create_durable_result(prepared, claim_token, claim.generation)

    async def _create_durable_result(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        generation: int,
    ) -> None:
        assert self.runtime is not None
        assert self.uow_factory is not None
        assert self.credential_registry is not None
        assert self.credential_crypto is not None
        binding = prepared.credential_binding
        if binding is None:
            await self._finish(
                prepared,
                claim_token,
                image_url=None,
                error_message="模型凭据尚未完成受控登记",
            )
            return
        if not _frozen_endpoint_matches_binding(prepared):
            await self._finish(
                prepared,
                claim_token,
                image_url=None,
                error_message="模型 endpoint 与原始凭据登记不一致",
            )
            return

        resolving = asyncio.create_task(
            run_in_threadpool(
                _resolve_bound_credential,
                self.credential_registry,
                binding,
            )
        )
        try:
            credential, cancelled = await _drain_owned_worker(resolving)
        except asyncio.CancelledError:
            await _finish_after_cancellation(
                self.uow_factory,
                prepared,
                claim_token,
                "任务被取消",
            )
            raise
        except Exception:
            credential, cancelled = None, False
        if cancelled:
            await _finish_after_cancellation(
                self.uow_factory,
                prepared,
                claim_token,
                "任务被取消",
            )
            raise asyncio.CancelledError
        if not isinstance(credential, BoundCredential) or (
            credential.provider != binding.provider
            or credential.principal_id != binding.original_principal_id
            or not credential.secret
        ):
            await self._finish(
                prepared,
                claim_token,
                image_url=None,
                error_message="模型凭据已撤销或无法核对",
            )
            return

        dispatch_nonce = str(uuid4())

        async def authorize_dispatch(wire_digest: str) -> bool:
            allowed, cancellation = await _persist_dispatch_gate(
                self.uow_factory,
                prepared,
                claim_token,
                generation,
                dispatch_nonce,
                wire_digest,
            )
            return allowed and not cancellation

        try:
            output = await self.runtime.generate(
                FusedGenerationInput(
                    task_id=prepared.task_id,
                    series_id=prepared.series_id,
                    member_type=prepared.member_type,
                    members=prepared.members,
                    name=prepared.name,
                    size=prepared.size,
                    prompt=prepared.prompt,
                    reference_images=prepared.reference_images,
                    server_model=prepared.server_model,
                    provider_secret=credential.secret,
                    dispatch_gate=authorize_dispatch,
                )
            )
        except FusedGenerationDispatchedCancellation:
            await self._retain_unknown(prepared, claim_token, FUSED_UNKNOWN_REASON)
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            await _finish_after_cancellation(
                self.uow_factory,
                prepared,
                claim_token,
                "任务被取消",
            )
            raise
        except ProviderGenerationFailure as failure:
            await self._finish(
                prepared,
                claim_token,
                image_url=None,
                error_message=_safe_error(failure.detail, credential.secret),
            )
            return
        except Exception:
            await self._retain_unknown(prepared, claim_token, FUSED_UNKNOWN_REASON)
            return

        if isinstance(output, FusedGenerationUnknown):
            await self._retain_unknown(prepared, claim_token, FUSED_UNKNOWN_REASON)
            return
        if not isinstance(output, (FusedGenerationOutput, FusedGenerationInlineImage)):
            await self._retain_unknown(prepared, claim_token, FUSED_UNKNOWN_REASON)
            return

        result_document, digest = encode_result(output)
        encrypted, nonce, _key_id, _algorithm = seal_json_value(
            result_document,
            crypto=self.credential_crypto,
            submission_id=prepared.submission_id or "",
            owner_id=prepared.user_id,
            request_digest=prepared.request_digest or "",
            task_id=prepared.task_id,
            binding=binding,
            purpose="fused-provider-result-v1",
        )
        persisted, cancellation = await _persist_provider_result(
            self.uow_factory,
            prepared,
            claim_token,
            generation,
            encrypted_result=encrypted,
            result_nonce=nonce,
            result_kind=str(result_document.get("kind", "")),
            result_digest=digest,
        )
        if not persisted:
            if isinstance(output, FusedGenerationOutput):
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    output.image_url,
                )
            else:
                await self._retain_unknown(
                    prepared,
                    claim_token,
                    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
                )
            return
        if cancellation:
            await self._retain_result_after_cancellation(
                prepared,
                claim_token,
                output,
            )
            raise asyncio.CancelledError
        await self._deliver_durable_result(
            prepared,
            claim_token,
            generation,
            output,
        )

    async def _deliver_durable_result(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        generation: int,
        output: FusedGenerationOutput | FusedGenerationInlineImage,
    ) -> None:
        assert self.uow_factory is not None
        if isinstance(output, FusedGenerationOutput) and self.storage is None:
            await self._finish(
                prepared,
                claim_token,
                image_url=output.image_url,
                error_message=None,
                preserve_if_cancelled=True,
            )
            return
        if isinstance(output, FusedGenerationInlineImage) and self.inline_image_storage is None:
            await self._retain_unknown(
                prepared,
                claim_token,
                FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
            )
            return

        began, cancelled = await _begin_durable_media_storage(
            self.uow_factory,
            prepared,
            claim_token,
            generation,
        )
        if cancelled:
            await self._retain_result_after_cancellation(prepared, claim_token, output)
            raise asyncio.CancelledError
        if not began:
            await self._retain_result_after_cancellation(prepared, claim_token, output)
            return

        final_url: str | None = None
        known_locator: str | None = None
        if isinstance(output, FusedGenerationOutput):
            try:
                storage_result = await self.storage.store_generated_image(output.image_url)  # type: ignore[union-attr]
            except asyncio.CancelledError:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    output.image_url,
                )
                raise
            except Exception:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    output.image_url,
                )
                return
            if isinstance(storage_result, FusedGenerationStorageUnknown):
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    output.image_url,
                )
                if storage_result.cancellation_requested:
                    raise asyncio.CancelledError
                return
            if storage_result is None:
                final_url = output.image_url
            elif type(storage_result) is str and storage_result.strip():
                final_url = storage_result
                known_locator = storage_result
            else:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    output.image_url,
                )
                return
        else:
            storing = asyncio.create_task(
                run_in_threadpool(
                    _store_inline_image,
                    self.inline_image_storage,  # type: ignore[arg-type]
                    output,
                )
            )
            try:
                stored, cancellation = await _drain_owned_worker(storing)
            except asyncio.CancelledError:
                await self._retain_unknown(
                    prepared,
                    claim_token,
                    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
                )
                raise
            except Exception:
                await self._retain_unknown(
                    prepared,
                    claim_token,
                    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
                )
                return
            if cancellation:
                await self._retain_unknown(
                    prepared,
                    claim_token,
                    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
                )
                raise asyncio.CancelledError
            if not _stored_media_matches(stored, output):
                await self._retain_unknown(
                    prepared,
                    claim_token,
                    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
                )
                return
            final_url = stored.locator
            known_locator = stored.locator

        try:
            finished = await self._finish(
                prepared,
                claim_token,
                image_url=final_url,
                error_message=None,
                preserve_if_cancelled=True,
            )
        except asyncio.CancelledError:
            if known_locator is not None:
                await self._retain_known_media_result(prepared, claim_token, known_locator)
            else:
                await self._retain_result_after_cancellation(prepared, claim_token, output)
            raise
        if not finished:
            if known_locator is not None:
                await self._retain_known_media_result(prepared, claim_token, known_locator)
            else:
                await self._retain_result_after_cancellation(prepared, claim_token, output)

    async def _retain_result_after_cancellation(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        output: FusedGenerationOutput | FusedGenerationInlineImage,
    ) -> None:
        if isinstance(output, FusedGenerationOutput):
            await self._retain_known_media_result(prepared, claim_token, output.image_url)
        else:
            await self._retain_unknown(
                prepared,
                claim_token,
                FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
            )

    async def execute(self, prepared: PreparedFusedGeneration) -> None:
        if self.runtime is None or self.uow_factory is None:
            return
        claim_token = token_urlsafe(32)
        claim_task = asyncio.create_task(
            run_in_threadpool(
                _claim,
                self.uow_factory,
                prepared.task_id,
                claim_token,
                prepared.user_id,
            )
        )
        claim, cancellation_requested = await _drain_owned_worker(claim_task)
        if cancellation_requested:
            if claim is not None:
                await _finish_after_cancellation(
                    self.uow_factory,
                    prepared,
                    claim_token,
                    "任务被取消",
                )
            raise asyncio.CancelledError
        if claim is None:
            return
        if claim.cancelled:
            await self._finish(prepared, claim_token, image_url=None, error_message="任务被取消")
            return

        try:
            output = await self.runtime.generate(
                FusedGenerationInput(
                    task_id=prepared.task_id,
                    series_id=prepared.series_id,
                    member_type=prepared.member_type,
                    members=prepared.members,
                    name=prepared.name,
                    size=prepared.size,
                    prompt=prepared.prompt,
                    reference_images=prepared.reference_images,
                    server_model=prepared.server_model,
                )
            )
            if not isinstance(output, (FusedGenerationUnknown, FusedGenerationInlineImage)):
                provider_url = output.image_url if output is not None else None
                if not isinstance(provider_url, str) or not provider_url.strip():
                    raise ProviderGenerationFailure("未获取到图片")
        except FusedGenerationDispatchedCancellation:
            await self._retain_unknown(
                prepared,
                claim_token,
                FUSED_UNKNOWN_REASON,
            )
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            await _finish_after_cancellation(
                self.uow_factory,
                prepared,
                claim_token,
                "任务被取消",
            )
            raise
        except ProviderGenerationFailure as failure:
            await self._finish(
                prepared,
                claim_token,
                image_url=None,
                error_message=_safe_error(failure.detail, prepared.server_model.api_key),
            )
            return
        except Exception:
            await self._finish(
                prepared,
                claim_token,
                image_url=None,
                error_message="图片生成失败，请稍后重试",
            )
            return

        if isinstance(output, FusedGenerationUnknown):
            await self._retain_unknown(
                prepared,
                claim_token,
                FUSED_UNKNOWN_REASON,
            )
            return

        if isinstance(output, FusedGenerationInlineImage):
            await self._store_inline_image(prepared, claim_token, output)
            return

        final_url = provider_url
        if self.storage is not None:
            try:
                authorized, cancelled = await _authorize_media_storage(
                    self.uow_factory,
                    prepared.task_id,
                    prepared.user_id,
                    claim_token,
                )
            except asyncio.CancelledError:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                raise
            if cancelled:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                raise asyncio.CancelledError
            if not authorized:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                return
            try:
                storage_result = await self.storage.store_generated_image(provider_url)
            except asyncio.CancelledError:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                raise
            except Exception:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                return

            if isinstance(storage_result, FusedGenerationStorageUnknown):
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                if storage_result.cancellation_requested:
                    raise asyncio.CancelledError
                return
            if storage_result is None:
                pass
            elif type(storage_result) is str and storage_result.strip():
                final_url = storage_result
            else:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    provider_url,
                )
                return

            try:
                authorized, cancelled = await _authorize_media_storage(
                    self.uow_factory,
                    prepared.task_id,
                    prepared.user_id,
                    claim_token,
                )
            except asyncio.CancelledError:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    final_url,
                )
                raise
            if cancelled:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    final_url,
                )
                raise asyncio.CancelledError
            if not authorized:
                await self._retain_known_media_result(
                    prepared,
                    claim_token,
                    final_url,
                )
                return

        try:
            finished = await self._finish(
                prepared,
                claim_token,
                image_url=final_url,
                error_message=None,
                preserve_if_cancelled=True,
            )
        except asyncio.CancelledError:
            await self._retain_known_media_result(
                prepared,
                claim_token,
                final_url,
            )
            raise
        if not finished:
            await self._retain_known_media_result(
                prepared,
                claim_token,
                final_url,
            )

    async def _store_inline_image(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        image: FusedGenerationInlineImage,
    ) -> None:
        retention_attempted = False

        async def retain_pending() -> None:
            nonlocal retention_attempted
            if retention_attempted:
                return
            retention_attempted = True
            await self._retain_unknown(
                prepared,
                claim_token,
                FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
            )

        try:
            if self.inline_image_storage is None:
                await retain_pending()
                return

            authorized, cancelled = await _authorize_media_storage(
                self.uow_factory,
                prepared.task_id,
                prepared.user_id,
                claim_token,
            )
            if cancelled:
                raise asyncio.CancelledError
            if not authorized:
                await retain_pending()
                return

            storing = asyncio.create_task(
                run_in_threadpool(_store_inline_image, self.inline_image_storage, image)
            )
            try:
                stored, cancelled = await _drain_owned_worker(storing)
            except asyncio.CancelledError:
                raise
            except Exception:
                await retain_pending()
                return
            if cancelled:
                raise asyncio.CancelledError
            if not _stored_media_matches(stored, image):
                await retain_pending()
                return

            authorized, cancelled = await _authorize_media_storage(
                self.uow_factory,
                prepared.task_id,
                prepared.user_id,
                claim_token,
            )
            if cancelled:
                raise asyncio.CancelledError
            if not authorized:
                await retain_pending()
                return
            finished = await self._finish(
                prepared,
                claim_token,
                image_url=stored.locator,
                error_message=None,
                preserve_if_cancelled=True,
            )
            if not finished:
                await retain_pending()
        except asyncio.CancelledError:
            await retain_pending()
            raise
        except Exception:
            await retain_pending()

    async def _finish(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        *,
        image_url: str | None,
        error_message: str | None,
        preserve_if_cancelled: bool = False,
    ) -> bool:
        if self.uow_factory is None:
            return False
        # Finalization is one transaction and is never automatically retried:
        # a lost commit acknowledgement must not duplicate an asset or refund.
        finishing = asyncio.create_task(
            run_in_threadpool(
                _finish,
                self.uow_factory,
                prepared,
                claim_token,
                image_url,
                _safe_error(error_message, prepared.server_model.api_key) if error_message else None,
                preserve_if_cancelled,
            )
        )
        try:
            finished, cancellation_requested = await _drain_owned_worker(finishing)
        except Exception:
            return False
        if cancellation_requested:
            if preserve_if_cancelled and finished is True:
                # A committed inline result is terminal; do not overwrite it with retention.
                return True
            # An ambiguous finalization is never retried.
            raise asyncio.CancelledError
        return finished is True

    async def _retain_unknown(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        safe_reason: FusedGenerationUnknownReason,
    ) -> None:
        if self.uow_factory is None:
            return
        retaining = asyncio.create_task(
            run_in_threadpool(
                _retain_unknown,
                self.uow_factory,
                prepared.task_id,
                prepared.user_id,
                claim_token,
                safe_reason,
            )
        )
        try:
            _, cancellation_requested = await _drain_owned_worker(retaining)
        except Exception:
            # A failed or ambiguous retention commit must never fall through to
            # terminal finalization or be retried automatically.
            return
        if cancellation_requested:
            raise asyncio.CancelledError

    async def _retain_known_media_result(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        result_url: str,
    ) -> None:
        if self.uow_factory is None:
            return
        retaining = asyncio.create_task(
            run_in_threadpool(
                _retain_known_media_result,
                self.uow_factory,
                prepared.task_id,
                prepared.user_id,
                claim_token,
                result_url,
            )
        )
        try:
            _, cancellation_requested = await _drain_owned_worker(retaining)
        except Exception:
            # A failed or ambiguous retention commit must not fall through to
            # terminal finalization or trigger another transfer attempt.
            return
        if cancellation_requested:
            raise asyncio.CancelledError


def _read_durable_submission(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    submission_id: str,
    owner_id: str,
) -> Mapping[str, object] | None:
    uow = uow_factory()
    try:
        return uow.durable_submission_by_id(submission_id, owner_id=owner_id)
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


async def _durable_claim(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
) -> tuple[FusedTaskClaim | None, bool]:
    assert prepared.submission_id is not None
    claiming = asyncio.create_task(
        run_in_threadpool(
            _claim_durable,
            uow_factory,
            prepared.task_id,
            prepared.submission_id,
            prepared.user_id,
            claim_token,
        )
    )
    try:
        claim, cancelled = await _drain_owned_worker(claiming)
    except Exception:
        return None, False
    return claim, cancelled


def _claim_durable(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    task_id: str,
    submission_id: str,
    user_id: str,
    claim_token: str,
) -> FusedTaskClaim | None:
    uow = uow_factory()
    try:
        claim = uow.claim_durable_task(
            task_id,
            submission_id,
            claim_token,
            expected_user_id=user_id,
        )
        if claim is None:
            uow.rollback()
            return None
        try:
            uow.commit()
        except Exception:
            # An unconfirmed lease is never allowed to reach the provider.
            uow.rollback()
            return None
        return claim
    finally:
        uow.close()


def _accept_durable_snapshot_for_recovery(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    submission_id: str,
    owner_id: str,
    crypto: CredentialCrypto | None,
    proof_authority: HmacProofAuthority | None,
):
    if crypto is None or proof_authority is None:
        return None
    from .durable_application import accept_durable_snapshot_for_recovery

    return accept_durable_snapshot_for_recovery(
        uow_factory,
        submission_id,
        owner_id,
        crypto=crypto,
        proof_authority=proof_authority,
    )


def _resolve_bound_credential(
    registry: CredentialRegistry,
    binding,
) -> BoundCredential | None:
    try:
        credential = registry.resolve_original_credential(binding, "create")
    except Exception:
        return None
    return credential if isinstance(credential, BoundCredential) else None


def _frozen_endpoint_matches_binding(prepared: PreparedFusedGeneration) -> bool:
    binding = prepared.credential_binding
    model = prepared.server_model
    base_url = model.base_url
    if (
        binding is None
        or type(base_url) is not str
        or not base_url
        or model.id != prepared.model_id
        or binding.row_id != model.id
        or binding.provider != model.provider
        or binding.source not in {"system", "user"}
        or binding.owner_or_system
        != ("system" if binding.source == "system" else prepared.user_id)
    ):
        return False
    try:
        policy = EndpointPolicy.from_base_url(base_url)
    except ValueError:
        return False
    return policy.provider_origin == binding.endpoint_origin


async def _persist_dispatch_gate(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    generation: int,
    dispatch_nonce: str,
    wire_digest: str,
) -> tuple[bool, bool]:
    assert prepared.submission_id is not None
    writing = asyncio.create_task(
        run_in_threadpool(
            _persist_dispatch_gate_sync,
            uow_factory,
            prepared,
            claim_token,
            generation,
            dispatch_nonce,
            wire_digest,
        )
    )
    try:
        inserted, cancelled = await _drain_owned_worker(writing)
    except Exception:
        return False, False
    if inserted is not True:
        return False, cancelled
    checking = asyncio.create_task(
        run_in_threadpool(
            _dispatch_gate_matches_sync,
            uow_factory,
            prepared,
            claim_token,
            generation,
            dispatch_nonce,
            wire_digest,
        )
    )
    try:
        confirmed, check_cancelled = await _drain_owned_worker(checking)
    except Exception:
        return False, cancelled
    return confirmed is True, cancelled or check_cancelled


def _persist_dispatch_gate_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    generation: int,
    dispatch_nonce: str,
    wire_digest: str,
) -> bool:
    uow = uow_factory()
    try:
        inserted = uow.persist_dispatch_gate(
            task_id=prepared.task_id,
            submission_id=prepared.submission_id or "",
            user_id=prepared.user_id,
            claim_token=claim_token,
            generation=generation,
            dispatch_nonce=dispatch_nonce,
            wire_digest=wire_digest,
        )
        if not inserted:
            uow.rollback()
            return False
        try:
            uow.commit()
        except Exception:
            # Even when the write became visible, an unknown commit ACK never
            # grants this process permission to dispatch the provider request.
            # The durable marker makes later recovery stop at unknown.
            uow.rollback()
            return False
        return True
    except Exception:
        uow.rollback()
        return False
    finally:
        uow.close()


def _dispatch_gate_matches_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    generation: int,
    dispatch_nonce: str,
    wire_digest: str,
) -> bool:
    uow = uow_factory()
    try:
        return uow.dispatch_gate_matches(
            task_id=prepared.task_id,
            submission_id=prepared.submission_id or "",
            user_id=prepared.user_id,
            claim_token=claim_token,
            generation=generation,
            dispatch_nonce=dispatch_nonce,
            wire_digest=wire_digest,
        )
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


async def _persist_provider_result(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    generation: int,
    *,
    encrypted_result: str,
    result_nonce: str,
    result_kind: str,
    result_digest: str,
) -> tuple[bool, bool]:
    writing = asyncio.create_task(
        run_in_threadpool(
            _persist_provider_result_sync,
            uow_factory,
            prepared,
            claim_token,
            generation,
            encrypted_result,
            result_nonce,
            result_kind,
            result_digest,
        )
    )
    try:
        inserted, cancelled = await _drain_owned_worker(writing)
    except Exception:
        return False, False
    if inserted is not True:
        return False, cancelled
    checking = asyncio.create_task(
        run_in_threadpool(
            _provider_result_matches_sync,
            uow_factory,
            prepared,
            claim_token,
            generation,
            result_digest,
        )
    )
    try:
        confirmed, check_cancelled = await _drain_owned_worker(checking)
    except Exception:
        return False, cancelled
    return confirmed is True, cancelled or check_cancelled


def _persist_provider_result_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    generation: int,
    encrypted_result: str,
    result_nonce: str,
    result_kind: str,
    result_digest: str,
) -> bool:
    uow = uow_factory()
    try:
        inserted = uow.persist_provider_result(
            task_id=prepared.task_id,
            submission_id=prepared.submission_id or "",
            claim_token=claim_token,
            generation=generation,
            encrypted_result=encrypted_result,
            result_nonce=result_nonce,
            result_kind=result_kind,
            result_digest=result_digest,
        )
        if not inserted:
            uow.rollback()
            return False
        try:
            uow.commit()
        except Exception:
            return True
        return True
    except Exception:
        uow.rollback()
        return False
    finally:
        uow.close()


def _provider_result_matches_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    generation: int,
    result_digest: str,
) -> bool:
    uow = uow_factory()
    try:
        return uow.result_record_matches(
            prepared.submission_id or "",
            prepared.task_id,
            claim_token,
            generation,
            result_digest,
        )
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


async def _begin_durable_media_storage(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    generation: int,
) -> tuple[bool, bool]:
    writing = asyncio.create_task(
        run_in_threadpool(
            _begin_durable_media_storage_sync,
            uow_factory,
            prepared,
            claim_token,
            generation,
        )
    )
    try:
        inserted, cancelled = await _drain_owned_worker(writing)
    except Exception:
        return False, False
    if inserted is not True:
        return False, cancelled
    checking = asyncio.create_task(
        run_in_threadpool(
            _durable_media_started_matches_sync,
            uow_factory,
            prepared,
            claim_token,
            generation,
        )
    )
    try:
        confirmed, check_cancelled = await _drain_owned_worker(checking)
    except Exception:
        return False, cancelled
    return confirmed is True, cancelled or check_cancelled


def _begin_durable_media_storage_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    generation: int,
) -> bool:
    uow = uow_factory()
    try:
        inserted = uow.begin_durable_media_storage(
            prepared.task_id,
            prepared.submission_id or "",
            prepared.user_id,
            claim_token,
            generation,
        )
        if not inserted:
            uow.rollback()
            return False
        try:
            uow.commit()
        except Exception:
            return True
        return True
    except Exception:
        uow.rollback()
        return False
    finally:
        uow.close()


def _durable_media_started_matches_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    generation: int,
) -> bool:
    uow = uow_factory()
    try:
        return uow.durable_media_storage_started_matches(
            prepared.task_id,
            prepared.submission_id or "",
            prepared.user_id,
            claim_token,
            generation,
        )
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


async def _retain_interrupted_dispatch(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    submission_id: str,
) -> None:
    worker = asyncio.create_task(
        run_in_threadpool(_retain_interrupted_dispatch_sync, uow_factory, submission_id)
    )
    try:
        await _drain_owned_worker(worker)
    except Exception:
        return


def _retain_interrupted_dispatch_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    submission_id: str,
) -> None:
    uow = uow_factory()
    try:
        changed = uow.retain_dispatch_unknown(submission_id)
        if changed:
            uow.commit()
        else:
            uow.rollback()
    except Exception:
        uow.rollback()
    finally:
        uow.close()


async def _retain_interrupted_media(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    submission_id: str,
) -> None:
    worker = asyncio.create_task(
        run_in_threadpool(_retain_interrupted_media_sync, uow_factory, submission_id)
    )
    try:
        await _drain_owned_worker(worker)
    except Exception:
        return


def _retain_interrupted_media_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    submission_id: str,
) -> None:
    uow = uow_factory()
    try:
        changed = uow.retain_interrupted_media_write(submission_id)
        if changed:
            uow.commit()
        else:
            uow.rollback()
    except Exception:
        uow.rollback()
    finally:
        uow.close()


def _claim(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    task_id: str,
    claim_token: str,
    expected_user_id: str,
) -> FusedTaskClaim | None:
    uow = uow_factory()
    try:
        claim = uow.claim_task(task_id, claim_token, expected_user_id=expected_user_id)
        if claim is None:
            uow.rollback()
            return None
        uow.commit()
        return claim
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


async def _authorize_media_storage(
    uow_factory: FusedGenerationUnitOfWorkFactory | None,
    task_id: str,
    user_id: str,
    claim_token: str,
) -> tuple[bool, bool]:
    if uow_factory is None:
        return False, False
    checking = asyncio.create_task(
        run_in_threadpool(
            _authorize_media_storage_sync,
            uow_factory,
            task_id,
            user_id,
            claim_token,
        )
    )
    try:
        authorized, cancellation_requested = await _drain_owned_worker(checking)
    except Exception:
        return False, False
    return authorized is True, cancellation_requested


def _authorize_media_storage_sync(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    task_id: str,
    user_id: str,
    claim_token: str,
) -> bool:
    uow = uow_factory()
    try:
        return uow.authorize_media_storage(task_id, user_id, claim_token)
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


def _store_inline_image(
    storage: FusedGenerationImageStorage,
    image: FusedGenerationInlineImage,
) -> StoredMedia:
    return storage.store_inline_image(image)


def _stored_media_matches(
    stored: StoredMedia | None,
    image: FusedGenerationInlineImage,
) -> bool:
    if (
        type(stored) is not StoredMedia
        or type(stored.locator) is not str
        or not stored.locator.strip()
        or type(stored.content_digest) is not str
        or type(stored.size_bytes) is not int
        or type(stored.media_type) is not str
        or type(image.content) is not bytes
    ):
        return False
    return (
        stored.content_digest == hashlib.sha256(image.content).hexdigest()
        and stored.size_bytes == len(image.content)
        and stored.media_type == image.media_type
    )


def _finish(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    image_url: str | None,
    error_message: str | None,
    preserve_if_cancelled: bool = False,
) -> bool:
    uow = uow_factory()
    try:
        finished = uow.finish_task(
            prepared,
            claim_token,
            image_url=image_url,
            error_message=error_message,
            preserve_if_cancelled=preserve_if_cancelled,
        )
        uow.commit()
        return finished is True
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _retain_unknown(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    task_id: str,
    user_id: str,
    claim_token: str,
    safe_reason: FusedGenerationUnknownReason,
) -> None:
    uow = uow_factory()
    try:
        uow.retain_unknown(task_id, user_id, claim_token, safe_reason)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _retain_known_media_result(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    task_id: str,
    user_id: str,
    claim_token: str,
    result_url: str,
) -> None:
    uow = uow_factory()
    try:
        uow.retain_known_media_result(
            task_id,
            user_id,
            claim_token,
            result_url,
        )
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


_WorkerResult = TypeVar("_WorkerResult")


async def _drain_owned_worker(
    worker: asyncio.Task[_WorkerResult],
) -> tuple[_WorkerResult | None, bool]:
    """Keep an owned SQL worker alive until its thread has closed its Session."""
    cancellation_requested = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            if worker.cancelled():
                if cancellation_requested:
                    return None, True
                raise
            cancellation_requested = True
        except Exception:
            if cancellation_requested:
                return None, True
            raise

    try:
        return worker.result(), cancellation_requested
    except asyncio.CancelledError:
        if cancellation_requested:
            return None, True
        raise
    except Exception:
        if cancellation_requested:
            return None, True
        raise


async def _finish_after_cancellation(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    reason: str,
) -> None:
    finishing = asyncio.create_task(
        run_in_threadpool(
            _finish,
            uow_factory,
            prepared,
            claim_token,
            None,
            _safe_error(reason, prepared.server_model.api_key),
        )
    )
    try:
        await _drain_owned_worker(finishing)
    except Exception:
        # Keep the original cancellation and never retry a possibly committed finish.
        pass


def _safe_error(detail: str, secret: str) -> str:
    safe_detail = detail.strip()
    if secret:
        safe_detail = safe_detail.replace(secret, "[已隐藏]")
    return safe_detail[:2000] or "图片生成失败"
