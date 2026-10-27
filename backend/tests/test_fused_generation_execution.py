from __future__ import annotations

import asyncio
import threading
from datetime import datetime

import pytest

from haoai_backend.fused_generation.domain import (
    FusedGenerationOutput,
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
        self.status = "processing"
        self.cancelled = False
        self.active_sessions = 0
        self.open_count = 0
        self.close_count = 0
        self.claim_count = 0
        self.finish_count = 0
        self.finish_attempt_count = 0
        self.retain_attempt_count = 0
        self.retain_count = 0
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
        self.state.progress_message = "图片生成结果待核对"
        return True

    def finish_task(self, prepared, claim_token, *, image_url, error_message):
        self.state.finish_attempt_count += 1
        if (
            self.state.status != "processing"
            or self.state.claim_token != claim_token
            or self.state.recovery_status == "provider_result_unknown"
        ):
            return
        self.state.finish_count += 1
        self.state.finish_args.append((image_url, error_message))
        self.state.status = "completed" if image_url else "failed"
        self.state.claim_token = None

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
    def finish_task(self, prepared, claim_token, *, image_url, error_message):
        self.state.events.append("finish-start")
        self.state.finish_started.set()
        if not self.state.finish_release.wait(timeout=5):
            raise AssertionError("finish worker was not released")
        return super().finish_task(
            prepared,
            claim_token,
            image_url=image_url,
            error_message=error_message,
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


class FailingStorage:
    def __init__(self, state: ExecutionState) -> None:
        self.state = state
        self.calls = []

    async def store_generated_image(self, provider_url: str) -> str | None:
        assert self.state.active_sessions == 0
        self.calls.append(provider_url)
        raise OSError("temporary storage failure")


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
