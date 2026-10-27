"""One-shot asynchronous driver for admitted fused-image tasks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import TypeVar

from starlette.concurrency import run_in_threadpool

from .domain import (
    FUSED_UNKNOWN_REASON,
    FusedGenerationInput,
    FusedGenerationUnknown,
    FusedTaskClaim,
    PreparedFusedGeneration,
)
from .errors import FusedGenerationDispatchedCancellation, ProviderGenerationFailure
from .ports import (
    FusedGenerationRuntime,
    FusedGenerationStorage,
    FusedGenerationUnitOfWorkFactory,
)


@dataclass(frozen=True, slots=True)
class FusedGenerationExecution:
    runtime: FusedGenerationRuntime | None
    uow_factory: FusedGenerationUnitOfWorkFactory | None
    storage: FusedGenerationStorage | None = None

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
            if not isinstance(output, FusedGenerationUnknown):
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

        final_url = provider_url
        if self.storage is not None:
            try:
                stored_url = await self.storage.store_generated_image(provider_url)
                if isinstance(stored_url, str) and stored_url.strip():
                    final_url = stored_url
            except asyncio.CancelledError:
                await _finish_after_cancellation(
                    self.uow_factory,
                    prepared,
                    claim_token,
                    "任务被取消",
                )
                raise
            except Exception:
                # Keep the valid provider URL when transfer is temporarily unavailable.
                pass

        await self._finish(prepared, claim_token, image_url=final_url, error_message=None)

    async def _finish(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        *,
        image_url: str | None,
        error_message: str | None,
    ) -> None:
        if self.uow_factory is None:
            return
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
            )
        )
        try:
            _, cancellation_requested = await _drain_owned_worker(finishing)
        except Exception:
            return
        if cancellation_requested:
            # The transaction is finished, but an unknown commit result is never retried.
            raise asyncio.CancelledError

    async def _retain_unknown(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        safe_reason: str,
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


def _finish(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    prepared: PreparedFusedGeneration,
    claim_token: str,
    image_url: str | None,
    error_message: str | None,
) -> None:
    uow = uow_factory()
    try:
        uow.finish_task(
            prepared,
            claim_token,
            image_url=image_url,
            error_message=error_message,
        )
        uow.commit()
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
    safe_reason: str,
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
