from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import replace
from datetime import datetime

import pytest

from haoai_backend.fused_generation.domain import (
    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
    FusedGenerationOutput,
    FusedGenerationInlineImage,
    FusedGenerationUnknown,
    FusedServerImageModel,
    FusedTaskClaim,
    PreparedFusedGeneration,
)
from haoai_backend.fused_generation.errors import (
    FusedGenerationDispatchedCancellation,
    ProviderGenerationFailure,
)
from haoai_backend.fused_generation.execution import FusedGenerationExecution
from haoai_backend.generation_contracts import StoredMedia
from haoai_backend.generation_contracts.types import CredentialBinding


SERVER_SECRET = "provider-secret-do-not-persist"


def _prepared() -> PreparedFusedGeneration:
    return PreparedFusedGeneration(
        task_id="task-a",
        message_id="message-a",
        user_id="owner-a",
        series_id="series-a",
        chapter_id=None,
        member_type="character",
        members=({"kind": "asset", "id": "character-a", "name": "林岚"},),
        name="角色融合 1合1",
        size="2K",
        prompt="【角色资产对照】\n图片1 = 林岚",
        reference_images=("https://assets.test/lin.png",),
        model_id="system-model",
        model_name="服务端模型",
        credit_cost=5,
        server_model=FusedServerImageModel(
            id="system-model",
            name="服务端模型",
            cost_per_call=5,
            provider="provider-a",
            wire_model="image-v1",
            api_key=SERVER_SECRET,
        ),
        target_frame=None,
    )


class ExecutionState:
    def __init__(self) -> None:
        self.user_id = "owner-a"
        self.server_owned = True
        self.status = "processing"
        self.cancelled = False
        self.active_sessions = 0
        self.open_count = 0
        self.close_count = 0
        self.claim_count = 0
        self.storage_authorize_count = 0
        self.finish_count = 0
        self.finish_attempt_count = 0
        self.retain_attempt_count = 0
        self.retain_count = 0
        self.retain_known_attempt_count = 0
        self.retain_known_count = 0
        self.commit_count = 0
        self.finish_args = []
        self.events = []
        self.claim_token = None
        self.recovery_status = "ready"
        self.result = None
        self.progress_message = "正在生成融合图..."
        self.fail_retain_commit = False
        self.block_retain = False
        self.retain_started = threading.Event()
        self.retain_release = threading.Event()
        self.claim_started = threading.Event()
        self.claim_release = threading.Event()
        self.claim_closed = threading.Event()
        self.finish_started = threading.Event()
        self.finish_release = threading.Event()
        self.finish_closed = threading.Event()


class FakeUnitOfWork:
    def __init__(self, state: ExecutionState) -> None:
        self.state = state
        state.active_sessions += 1
        state.open_count += 1
        state.events.append("open")

    def current_time(self):
        return datetime(2026, 10, 15, 12, 0, 0)

    def new_id(self):
        return "generated-asset"

    def require_series_access(self, actor, series_id):
        raise AssertionError("execution does not re-run admission")

    def load_member_asset(self, member_type, series_id, asset_id):
        raise AssertionError("execution does not re-read member assets")

    def resolve_and_charge_model(self, user_id, model_id):
        raise AssertionError("execution does not charge again")

    def resolve_image_prompt(self, user_id, prompt_id):
        raise AssertionError("execution does not resolve prompts again")

    def load_chapter(self, chapter_id):
        raise AssertionError("fake terminal persistence does not attach a frame")

    def stage_request(self, prepared, user_id):
        raise AssertionError("execution does not stage a second task")

    def claim_task(self, task_id, claim_token, *, expected_user_id):
        self.state.claim_count += 1
        if (
            expected_user_id != self.state.user_id
            or self.state.status != "processing"
            or self.state.claim_token is not None
        ):
            return None
        self.state.claim_token = claim_token
        return FusedTaskClaim(cancelled=self.state.cancelled)

    def retain_unknown(self, task_id, user_id, claim_token, safe_reason):
        self.state.retain_attempt_count += 1
        if self.state.block_retain:
            self.state.retain_started.set()
            if not self.state.retain_release.wait(timeout=5):
                raise AssertionError("retain worker was not released")
        if (
            self.state.status not in {"processing", "cancelling"}
            or self.state.claim_token != claim_token
        ):
            return False
        self.state.retain_count += 1
        self.state.recovery_status = "provider_result_unknown"
        self.state.result = safe_reason
        self.state.progress_message = (
            "图片已返回，保存结果待核对"
            if safe_reason == FUSED_MEDIA_STORAGE_UNKNOWN_REASON
            else "图片生成结果待核对"
        )
        return True

    def retain_known_media_result(self, task_id, user_id, claim_token, result_url):
        self.state.retain_known_attempt_count += 1
        if (
            self.state.user_id != user_id
            or self.state.status not in {"processing", "cancelling"}
            or self.state.claim_token != claim_token
            or self.state.recovery_status != "ready"
        ):
            return False
        self.state.retain_known_count += 1
        self.state.recovery_status = "provider_result_unknown"
        self.state.result = result_url
        self.state.progress_message = "融合图已生成，转存结果待核对"
        return True

    def authorize_media_storage(self, task_id, user_id, claim_token):
        self.state.storage_authorize_count += 1
        return (
            self.state.server_owned
            and user_id == self.state.user_id
            and self.state.status == "processing"
            and self.state.claim_token == claim_token
            and self.state.recovery_status != "provider_result_unknown"
        )

    def finish_task(
        self,
        prepared,
        claim_token,
        *,
        image_url,
        error_message,
        preserve_if_cancelled=False,
    ):
        self.state.finish_attempt_count += 1
        if (
            self.state.status != "processing"
            or self.state.claim_token != claim_token
            or self.state.recovery_status == "provider_result_unknown"
        ):
            return False
        self.state.finish_count += 1
        self.state.finish_args.append((image_url, error_message))
        self.state.status = "completed" if image_url else "failed"
        self.state.claim_token = None
        return True

    def commit(self):
        self.state.commit_count += 1
        self.state.events.append("commit")
        if self.state.fail_retain_commit and self.state.recovery_status == "provider_result_unknown":
            raise OSError("lost retain commit acknowledgement")

    def rollback(self):
        pass

    def close(self):
        self.state.active_sessions -= 1
        self.state.close_count += 1
        self.state.events.append("close")


class Factory:
    def __init__(self, state: ExecutionState) -> None:
        self.state = state

    def __call__(self):
        return FakeUnitOfWork(self.state)


class BlockingClaimUnitOfWork(FakeUnitOfWork):
    def claim_task(self, task_id, claim_token, *, expected_user_id):
        self.state.claim_started.set()
        if not self.state.claim_release.wait(timeout=5):
            raise AssertionError("claim worker was not released")
        return super().claim_task(
            task_id, claim_token, expected_user_id=expected_user_id
        )

    def close(self):
        super().close()
        self.state.events.append("claim-close")
        self.state.claim_closed.set()


class BlockingClaimFactory:
    def __init__(self, state: ExecutionState) -> None:
        self.state = state
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls == 1:
            return BlockingClaimUnitOfWork(self.state)
        return FakeUnitOfWork(self.state)


class BlockingFinishUnitOfWork(FakeUnitOfWork):
    def finish_task(
        self,
        prepared,
        claim_token,
        *,
        image_url,
        error_message,
        preserve_if_cancelled=False,
    ):
        self.state.events.append("finish-start")
        self.state.finish_started.set()
        if not self.state.finish_release.wait(timeout=5):
            raise AssertionError("finish worker was not released")
        return super().finish_task(
            prepared,
            claim_token,
            image_url=image_url,
            error_message=error_message,
            preserve_if_cancelled=preserve_if_cancelled,
        )

    def commit(self):
        super().commit()
        self.state.events.append("finish-commit")

    def close(self):
        super().close()
        self.state.events.append("finish-close")
        self.state.finish_closed.set()


class BlockingFinishFactory:
    def __init__(self, state: ExecutionState) -> None:
        self.state = state
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls == 2:
            return BlockingFinishUnitOfWork(self.state)
        return FakeUnitOfWork(self.state)


class BlockingInlineFinishFactory:
    def __init__(self, state: ExecutionState) -> None:
        self.state = state
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls == 4:
            return BlockingFinishUnitOfWork(self.state)
        return FakeUnitOfWork(self.state)


class WaitingRuntime:
    def __init__(self, state: ExecutionState) -> None:
        self.state = state
        self.started = threading.Event()
        self.calls = 0

    async def generate(self, request):
        assert self.state.active_sessions == 0
        self.calls += 1
        self.started.set()
        await asyncio.Future()


class Runtime:
    def __init__(self, state: ExecutionState, *, output=None, error=None) -> None:
        self.state = state
        self.output = output or FusedGenerationOutput("https://provider.test/result.png")
        self.error = error
        self.calls = 0

    async def generate(self, request):
        assert self.state.active_sessions == 0
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.output


class CredentialRegistrySpy:
    def __init__(self) -> None:
        self.resolve_calls = 0

    def resolve_original_credential(self, binding, purpose):
        self.resolve_calls += 1
        raise AssertionError(
            "mismatched frozen endpoint must fail before secret lookup"
        )


class FakeInlineImageStorage:
    def __init__(self, *, wrong_digest: bool = False, block: bool = False) -> None:
        self.calls = 0
        self.image = None
        self.wrong_digest = wrong_digest
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = block
        self.thread_id = None

    def store_inline_image(self, image):
        self.calls += 1
        self.image = image
        self.thread_id = threading.get_ident()
        self.started.set()
        if self.block and not self.release.wait(timeout=5):
            raise AssertionError("inline storage worker was not released")
        digest = hashlib.sha256(image.content).hexdigest()
        if self.wrong_digest:
            digest = "0" * 64
        return StoredMedia(
            locator="https://media.example.test/fused/owned.png",
            content_digest=digest,
            media_type=image.media_type,
            size_bytes=len(image.content),
        )


class FailingStorage:
    def __init__(self, state: ExecutionState) -> None:
        self.state = state
        self.calls = []

    async def store_generated_image(self, provider_url: str) -> str | None:
        assert self.state.active_sessions == 0
        self.calls.append(provider_url)
        return None


def test_storage_failure_keeps_provider_url_and_duplicate_execution_is_terminal_noop():
    state = ExecutionState()
    factory = Factory(state)
    runtime = Runtime(state)
    storage = FailingStorage(state)
    execution = FusedGenerationExecution(runtime, factory, storage)

    asyncio.run(execution.execute(_prepared()))
    asyncio.run(execution.execute(_prepared()))

    assert runtime.calls == 1
    assert storage.calls == ["https://provider.test/result.png"]
    assert state.status == "completed"
    assert state.finish_args == [("https://provider.test/result.png", None)]


def test_durable_execution_rejects_mismatched_binding_before_secret_lookup() -> None:
    state = ExecutionState()
    state.claim_token = "claim-a"
    factory = Factory(state)
    runtime = Runtime(state)
    registry = CredentialRegistrySpy()
    binding = CredentialBinding(
        source="system",
        row_id="system-model",
        owner_or_system="system",
        provider="provider-a",
        original_principal_id="principal-a",
        endpoint_origin="https://registered.example.test",
        allowed_content_paths=(),
        binding_version="1",
    )
    prepared = replace(
        _prepared(),
        submission_id="submission-a",
        credential_binding=binding,
        server_model=replace(
            _prepared().server_model,
            base_url="https://different.example.test/v1",
        ),
    )
    execution = FusedGenerationExecution(
        runtime=runtime,
        uow_factory=factory,
        credential_registry=registry,
        credential_crypto=object(),
    )

    asyncio.run(execution._create_durable_result(prepared, "claim-a", 1))

    assert registry.resolve_calls == 0
    assert runtime.calls == 0
    assert state.finish_args == [(None, "模型 endpoint 与原始凭据登记不一致")]
    assert state.open_count == state.close_count
    assert state.active_sessions == 0


def test_provider_error_is_sanitized_and_finalized_once():
    state = ExecutionState()
    runtime = Runtime(
        state,
        error=ProviderGenerationFailure(f"upstream refused token {SERVER_SECRET}"),
    )
    execution = FusedGenerationExecution(runtime, Factory(state))

    asyncio.run(execution.execute(_prepared()))

    assert state.status == "failed"
    assert state.finish_count == 1
    assert state.finish_args == [(None, "upstream refused token [已隐藏]")]
    assert SERVER_SECRET not in repr(state.finish_args)
    assert state.active_sessions == 0


def test_provider_result_unknown_is_retained_and_duplicate_execution_does_not_recall_runtime():
    state = ExecutionState()
    runtime = Runtime(state, output=FusedGenerationUnknown())
    execution = FusedGenerationExecution(runtime, Factory(state))

    asyncio.run(execution.execute(_prepared()))
    first_claim_token = state.claim_token
    asyncio.run(execution.execute(_prepared()))

    assert runtime.calls == 1
    assert state.status == "processing"
    assert state.claim_token == first_claim_token
    assert state.recovery_status == "provider_result_unknown"
    assert state.progress_message == "图片生成结果待核对"
    assert state.retain_count == 1
    assert state.finish_count == 0


def test_inline_image_requires_owned_media_receipt_before_success_finalization():
    state = ExecutionState()
    image = FusedGenerationInlineImage(b"synthetic-image-bytes", "image/png")
    runtime = Runtime(state, output=image)
    storage = FakeInlineImageStorage()
    execution = FusedGenerationExecution(
        runtime,
        Factory(state),
        inline_image_storage=storage,
    )

    asyncio.run(execution.execute(_prepared()))

    assert runtime.calls == 1
    assert storage.calls == 1
    assert storage.image is image
    assert storage.thread_id != threading.get_ident()
    assert state.storage_authorize_count == 2
    assert state.status == "completed"
    assert state.finish_args == [
        ("https://media.example.test/fused/owned.png", None)
    ]
    assert state.retain_count == 0


def test_inline_image_without_storage_capability_is_retained_without_refund():
    state = ExecutionState()
    image = FusedGenerationInlineImage(b"synthetic-image-bytes", "image/png")
    runtime = Runtime(state, output=image)

    asyncio.run(FusedGenerationExecution(runtime, Factory(state)).execute(_prepared()))

    assert runtime.calls == 1
    assert state.status == "processing"
    assert state.claim_token is not None
    assert state.recovery_status == "provider_result_unknown"
    assert state.result == FUSED_MEDIA_STORAGE_UNKNOWN_REASON
    assert state.progress_message == "图片已返回，保存结果待核对"
    assert state.finish_count == 0
    assert state.retain_count == 1


def test_inline_storage_requires_fresh_owner_and_marker_authorization():
    state = ExecutionState()
    state.server_owned = False
    image = FusedGenerationInlineImage(b"synthetic-image-bytes", "image/png")
    runtime = Runtime(state, output=image)
    storage = FakeInlineImageStorage()
    execution = FusedGenerationExecution(
        runtime,
        Factory(state),
        inline_image_storage=storage,
    )

    asyncio.run(execution.execute(_prepared()))

    assert runtime.calls == 1
    assert state.storage_authorize_count == 1
    assert storage.calls == 0
    assert state.status == "processing"
    assert state.claim_token is not None
    assert state.recovery_status == "provider_result_unknown"
    assert state.finish_count == 0
    assert state.retain_count == 1


def test_inline_storage_mismatch_is_retained_and_not_retried():
    state = ExecutionState()
    image = FusedGenerationInlineImage(b"synthetic-image-bytes", "image/png")
    runtime = Runtime(state, output=image)
    storage = FakeInlineImageStorage(wrong_digest=True)
    execution = FusedGenerationExecution(
        runtime,
        Factory(state),
        inline_image_storage=storage,
    )

    asyncio.run(execution.execute(_prepared()))
    asyncio.run(execution.execute(_prepared()))

    assert runtime.calls == 1
    assert storage.calls == 1
    assert state.status == "processing"
    assert state.claim_token is not None
    assert state.result == FUSED_MEDIA_STORAGE_UNKNOWN_REASON
    assert state.finish_count == 0
    assert state.retain_count == 1


def test_cancellation_during_inline_storage_drains_worker_and_retains_unknown():
    state = ExecutionState()
    image = FusedGenerationInlineImage(b"synthetic-image-bytes", "image/png")
    runtime = Runtime(state, output=image)
    storage = FakeInlineImageStorage(block=True)
    execution = FusedGenerationExecution(
        runtime,
        Factory(state),
        inline_image_storage=storage,
    )

    async def exercise():
        task = asyncio.create_task(execution.execute(_prepared()))
        assert await asyncio.to_thread(storage.started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert state.finish_count == 0
        storage.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    assert runtime.calls == 1
    assert storage.calls == 1
    assert state.status == "processing"
    assert state.claim_token is not None
    assert state.recovery_status == "provider_result_unknown"
    assert state.result == FUSED_MEDIA_STORAGE_UNKNOWN_REASON
    assert state.finish_count == 0
    assert state.retain_count == 1


def test_cancel_during_inline_finish_accepts_committed_terminal_without_retention():
    state = ExecutionState()
    image = FusedGenerationInlineImage(b"synthetic-image-bytes", "image/png")
    runtime = Runtime(state, output=image)
    storage = FakeInlineImageStorage()
    execution = FusedGenerationExecution(
        runtime,
        BlockingInlineFinishFactory(state),
        inline_image_storage=storage,
    )

    async def exercise():
        task = asyncio.create_task(execution.execute(_prepared()))
        assert await asyncio.to_thread(state.finish_started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        state.finish_release.set()
        await task

    asyncio.run(exercise())

    assert runtime.calls == storage.calls == 1
    assert state.status == "completed"
    assert state.claim_token is None
    assert state.finish_count == 1
    assert state.retain_attempt_count == 0
    assert state.retain_count == 0
    assert state.open_count == state.close_count
    assert state.active_sessions == 0


def test_cancel_during_url_finish_retains_known_url_when_cas_does_not_terminalize():
    state = ExecutionState()
    runtime = Runtime(state)
    execution = FusedGenerationExecution(runtime, BlockingFinishFactory(state))

    async def exercise():
        task = asyncio.create_task(execution.execute(_prepared()))
        assert await asyncio.to_thread(state.finish_started.wait, 2)
        state.status = "cancelling"
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        state.finish_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    assert state.status == "cancelling"
    assert state.claim_token is not None
    assert state.recovery_status == "provider_result_unknown"
    assert state.result == "https://provider.test/result.png"
    assert state.progress_message == "融合图已生成，转存结果待核对"
    assert state.retain_known_attempt_count == 1
    assert state.retain_known_count == 1
    assert state.finish_count == 0
    assert state.open_count == state.close_count
    assert state.active_sessions == 0


def test_dispatched_cancellation_retains_unknown_before_propagating_cancellation():
    state = ExecutionState()
    runtime = Runtime(state, error=FusedGenerationDispatchedCancellation())
    execution = FusedGenerationExecution(runtime, Factory(state))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execution.execute(_prepared()))

    assert state.recovery_status == "provider_result_unknown"
    assert state.claim_token is not None
    assert state.finish_count == 0
    assert state.retain_count == 1


def test_ambiguous_retain_commit_is_not_retried_or_finalized():
    state = ExecutionState()
    state.fail_retain_commit = True
    runtime = Runtime(state, output=FusedGenerationUnknown())

    asyncio.run(FusedGenerationExecution(runtime, Factory(state)).execute(_prepared()))

    assert runtime.calls == 1
    assert state.retain_attempt_count == 1
    assert state.finish_count == 0
    assert state.claim_token is not None


def test_cancellation_while_retaining_unknown_does_not_fall_through_to_refund_finalizer():
    state = ExecutionState()
    state.block_retain = True
    runtime = Runtime(state, output=FusedGenerationUnknown())
    execution = FusedGenerationExecution(runtime, Factory(state))

    async def exercise():
        task = asyncio.create_task(execution.execute(_prepared()))
        assert await asyncio.to_thread(state.retain_started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert state.finish_count == 0
        state.retain_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    assert state.recovery_status == "provider_result_unknown"
    assert state.retain_count == 1
    assert state.finish_count == 0


def test_task_cancelled_before_provider_call_is_finalized_as_failure():
    state = ExecutionState()
    state.cancelled = True
    runtime = Runtime(state)

    asyncio.run(FusedGenerationExecution(runtime, Factory(state)).execute(_prepared()))

    assert runtime.calls == 0
    assert state.status == "failed"
    assert state.finish_args == [(None, "任务被取消")]
    assert state.active_sessions == 0


def test_async_cancellation_persists_failure_and_drains_short_session_workers():
    state = ExecutionState()
    runtime = Runtime(state, error=asyncio.CancelledError())
    execution = FusedGenerationExecution(runtime, Factory(state))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execution.execute(_prepared()))

    assert state.status == "failed"
    assert state.finish_args == [(None, "任务被取消")]
    assert state.open_count == state.close_count
    assert state.active_sessions == 0


def test_repeated_cancellation_drains_blocked_claim_worker_before_failure_finalization():
    state = ExecutionState()
    runtime = Runtime(state)
    execution = FusedGenerationExecution(runtime, BlockingClaimFactory(state))

    async def exercise():
        task = asyncio.create_task(execution.execute(_prepared()))
        completed_while_blocked = True
        claim_closed_while_blocked = True
        outcome = None
        try:
            assert await asyncio.to_thread(state.claim_started.wait, 2)
            assert state.active_sessions == 1
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            completed_while_blocked = task.done()
            claim_closed_while_blocked = state.claim_closed.is_set()
        finally:
            state.claim_release.set()
            if not task.done() and not task.cancelling():
                task.cancel()
            outcome = await asyncio.wait_for(
                asyncio.gather(task, return_exceptions=True), timeout=8
            )
            assert await asyncio.to_thread(state.claim_closed.wait, 3)
        return completed_while_blocked, claim_closed_while_blocked, outcome

    completed, claim_closed, outcome = asyncio.run(exercise())

    assert completed is False
    assert claim_closed is False
    assert len(outcome) == 1 and isinstance(outcome[0], asyncio.CancelledError)
    assert state.status == "failed"
    assert state.claim_count == 1
    assert state.finish_attempt_count == state.finish_count == 1
    assert state.finish_args == [(None, "任务被取消")]
    assert runtime.calls == 0
    assert state.commit_count == 2
    assert state.open_count == state.close_count == 2
    assert state.active_sessions == 0
    assert state.events == ["open", "commit", "close", "claim-close", "open", "commit", "close"]


def test_repeated_cancellation_drains_blocked_failure_terminal_worker():
    state = ExecutionState()
    runtime = WaitingRuntime(state)
    execution = FusedGenerationExecution(runtime, BlockingFinishFactory(state))

    async def exercise():
        task = asyncio.create_task(execution.execute(_prepared()))
        completed_while_blocked = True
        finish_closed_while_blocked = True
        outcome = None
        try:
            assert await asyncio.to_thread(runtime.started.wait, 2)
            task.cancel()
            assert await asyncio.to_thread(state.finish_started.wait, 2)
            assert state.active_sessions == 1
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            completed_while_blocked = task.done()
            finish_closed_while_blocked = state.finish_closed.is_set()
        finally:
            state.finish_release.set()
            if not task.done() and not task.cancelling():
                task.cancel()
            outcome = await asyncio.wait_for(
                asyncio.gather(task, return_exceptions=True), timeout=8
            )
            assert await asyncio.to_thread(state.finish_closed.wait, 3)
        return completed_while_blocked, finish_closed_while_blocked, outcome

    completed, finish_closed, outcome = asyncio.run(exercise())

    assert completed is False
    assert finish_closed is False
    assert len(outcome) == 1 and isinstance(outcome[0], asyncio.CancelledError)
    assert state.status == "failed"
    assert state.finish_attempt_count == state.finish_count == 1
    assert state.finish_args == [(None, "任务被取消")]
    assert runtime.calls == 1
    assert state.commit_count == 2
    assert state.open_count == state.close_count == 2
    assert state.active_sessions == 0
    assert state.events == [
        "open",
        "commit",
        "close",
        "open",
        "finish-start",
        "commit",
        "finish-commit",
        "close",
        "finish-close",
    ]
