"""Narrow quote, idempotent admission, and settlement for paid two-pass work."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import users
from haoai_backend.generation_catalog.tables import (
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    TaskQuoteRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.public_site_config.tables import system_configs
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.domain import StoryboardPrompt, StoryboardServerModel
from haoai_backend.storyboard_generation.errors import (
    StoryboardGenerationBadRequest,
    StoryboardGenerationForbidden,
    StoryboardGenerationInsufficientCredits,
    StoryboardGenerationNotFound,
    StoryboardGenerationTaskConflict,
    StoryboardGenerationUnavailable,
)
from haoai_backend.storyboard_generation.persistence import (
    SqlAlchemyStoryboardGenerationUnitOfWork,
)

OPERATION = "storyboard.two_pass"
TASK_TYPE = "storyboard-two-pass"
_GRAPH_VERSION = "storyboard-two-pass-v1"
_SUPPORTED_PROVIDERS = frozenset({"openai", "openai-compatible"})
_MAX_CREDITS = 2_147_483_647


class _SettlementConflict(Exception):
    """A concurrent terminal transition won the task/unit compare-and-set."""


class TwoPassQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    series_id: str
    raw_content: str
    model_id: str
    scene_prompt_id: str
    frame_prompt_id: str | None = None
    temperature: FiniteFloat = Field(default=0.7, ge=0.0, le=2.0)
    enable_thinking: bool = False
    skip_frame_generation: bool = False


class TwoPassStreamRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    quote_id: str


@dataclass(frozen=True, slots=True)
class TwoPassExecutionInput:
    actor: TrustedActor
    submission_id: str
    task_id: str
    billing_unit_id: str
    quoted_amount: int
    billing_status: str
    model: StoryboardServerModel = field(repr=False)
    scene_prompt: StoryboardPrompt = field(repr=False)
    frame_prompt: StoryboardPrompt = field(repr=False)
    raw_content: str = field(repr=False)
    series_id: str
    temperature: float
    enable_thinking: bool
    skip_frame_generation: bool


@dataclass(frozen=True, slots=True)
class TwoPassAdmission:
    kind: str
    receipt: Mapping[str, Any]
    execution: TwoPassExecutionInput | None = field(default=None, repr=False)
    result: Mapping[str, Any] | None = None


class TwoPassBillingService:
    """SQL adapter for the existing foundation tables; it creates no schema."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | None,
        *,
        decrypt_api_key: Callable[[str], str] | None = None,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._decrypt_api_key = decrypt_api_key
        self._now = now or _utcnow
        self._new_id = new_id or (lambda: str(uuid4()))

    def create_quote(self, actor: TrustedActor, request: TwoPassQuoteRequest) -> dict[str, Any]:
        session = self._session()
        commit_attempted = False
        try:
            candidate = self._resolve_candidate(session, actor, request)
            now = _naive_utc(self._now())
            expires_at = now + timedelta(minutes=5)
            quote_id = self._new_identifier()
            row = TaskQuoteRow(
                id=quote_id,
                user_id=actor.user_id,
                operation=OPERATION,
                idempotency_key=self._new_identifier(),
                model_source=candidate.model_source,
                model_ref=candidate.model.id,
                model_name=candidate.model.name,
                # The shared quote schema defines unit_price as the effective
                # price for its one billing unit; public output keeps base price.
                unit_price=candidate.quoted_amount,
                unit_count=1,
                total_amount=candidate.quoted_amount,
                input_digest=candidate.input_digest,
                candidate_digest=candidate.candidate_digest,
                unit_manifest=[candidate.manifest],
                status="quoted",
                confirmation_key=self._new_identifier(),
                expires_at=expires_at,
                created_at=now,
            )
            session.add(row)
            session.flush()
            commit_attempted = True
            session.commit()
            return {
                "quote_id": quote_id,
                "operation": OPERATION,
                "model_id": candidate.model.id,
                "model_name": candidate.model.name,
                "model_source": candidate.model_source,
                "unit_price": candidate.unit_price,
                "multiplier": candidate.multiplier,
                "quoted_amount": candidate.quoted_amount,
                "expires_at": expires_at.replace(tzinfo=timezone.utc).isoformat(),
            }
        except (StoryboardGenerationBadRequest, StoryboardGenerationForbidden,
                StoryboardGenerationNotFound, StoryboardGenerationTaskConflict,
                StoryboardGenerationUnavailable):
            if not commit_attempted:
                session.rollback()
            raise
        except Exception:
            if not commit_attempted:
                session.rollback()
                raise StoryboardGenerationUnavailable("报价配置暂时不可用") from None
            raise StoryboardGenerationUnavailable("报价提交结果暂时无法确认") from None
        finally:
            session.close()

    def admit(
        self,
        actor: TrustedActor,
        quote_id: str,
        raw_idempotency_key: str | None,
    ) -> TwoPassAdmission:
        try:
            idempotency_key = validate_idempotency_key(raw_idempotency_key)
        except ValueError as error:
            raise StoryboardGenerationBadRequest(str(error)) from error
        if not isinstance(quote_id, str) or not quote_id.strip():
            raise StoryboardGenerationBadRequest("quote_id 不能为空")
        request_digest = _digest({"operation": OPERATION, "quote_id": quote_id})
        session = self._session()
        commit_attempted = False
        candidate: _Candidate | None = None
        try:
            existing = session.execute(
                select(TaskSubmissionRow).where(
                    TaskSubmissionRow.user_id == actor.user_id,
                    TaskSubmissionRow.operation == OPERATION,
                    TaskSubmissionRow.idempotency_key == idempotency_key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise StoryboardGenerationTaskConflict(
                        "Idempotency-Key 已用于不同的报价请求"
                    )
                return self._replay(session, actor, existing)

            quote = session.execute(
                select(TaskQuoteRow)
                .where(TaskQuoteRow.id == quote_id)
                .with_for_update()
            ).scalar_one_or_none()
            if quote is None or quote.user_id != actor.user_id or quote.operation != OPERATION:
                raise StoryboardGenerationNotFound("报价不存在或无权使用")
            now = _naive_utc(self._now())
            if _naive_utc(quote.expires_at) <= now:
                raise StoryboardGenerationTaskConflict("报价已过期，请重新报价")
            if quote.consumed_at is not None or quote.status not in {"quoted", "confirmed"}:
                raced = session.execute(
                    select(TaskSubmissionRow).where(
                        TaskSubmissionRow.user_id == actor.user_id,
                        TaskSubmissionRow.operation == OPERATION,
                        TaskSubmissionRow.idempotency_key == idempotency_key,
                    )
                ).scalar_one_or_none()
                if raced is not None:
                    if raced.request_digest != request_digest:
                        raise StoryboardGenerationTaskConflict(
                            "Idempotency-Key 已用于不同的报价请求"
                        )
                    return self._replay(session, actor, raced)
                raise StoryboardGenerationTaskConflict("该报价已被使用，请重新报价")
            manifest = _read_manifest(quote.unit_manifest)
            request = _request_from_manifest(manifest)
            candidate = self._resolve_candidate(session, actor, request)
            if (
                candidate.input_digest != quote.input_digest
                or candidate.candidate_digest != quote.candidate_digest
                or candidate.manifest != manifest
                or candidate.model_source != quote.model_source
                or candidate.model.id != quote.model_ref
                or candidate.model.name != quote.model_name
                or candidate.quoted_amount != quote.unit_price
                or candidate.quoted_amount != quote.total_amount
            ):
                raise StoryboardGenerationTaskConflict("模型、提示词或报价已变化，请重新报价")

            task_id = self._new_identifier()
            unit_id = self._new_identifier()
            submission_id = self._new_identifier()
            charged = candidate.quoted_amount > 0
            billing_status = "charged" if charged else "no_charge"
            safe_request_data = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
            task = AITaskRow(
                id=task_id,
                user_id=actor.user_id,
                type=TASK_TYPE,
                status="queued",
                billing_status=billing_status,
                message_id="",
                credit_cost=candidate.quoted_amount,
                result=None,
                request_data=safe_request_data,
                model_name=candidate.model.name,
                progress=0,
                progress_message="等待分镜生成",
                created_at=now,
                updated_at=now,
            )
            unit = BillingUnitRow(
                id=unit_id,
                task_id=task_id,
                item_key="storyboard-two-pass",
                operation=OPERATION,
                step_graph_version=_GRAPH_VERSION,
                input_digest=candidate.input_digest,
                quoted_amount=candidate.quoted_amount,
                debited_amount=candidate.quoted_amount if charged else 0,
                refunded_amount=0,
                billing_status=billing_status,
                result_status="pending",
                first_intent_at=now,
                quote_id=quote.id,
                quote_unit_key="storyboard-two-pass",
                candidate_digest=candidate.candidate_digest,
                created_at=now,
                updated_at=now,
            )
            submission = TaskSubmissionRow(
                id=submission_id,
                user_id=actor.user_id,
                operation=OPERATION,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                task_ids=[task_id],
                task_quote_id=quote.id,
                status="accepted",
                created_at=now,
            )
            session.add_all((task, unit, submission))
            session.flush()

            if charged:
                updated_balance = session.execute(
                    update(UserCreditRow)
                    .where(
                        UserCreditRow.user_id == actor.user_id,
                        UserCreditRow.credits >= candidate.quoted_amount,
                    )
                    .values(credits=UserCreditRow.credits - candidate.quoted_amount)
                    .returning(UserCreditRow.credits)
                ).scalar_one_or_none()
                if updated_balance is None:
                    balance = session.execute(
                        select(UserCreditRow.credits).where(
                            UserCreditRow.user_id == actor.user_id
                        )
                    ).scalar_one_or_none()
                    raise StoryboardGenerationInsufficientCredits(
                        candidate.quoted_amount, balance
                    )
                debit_key = f"debit:{unit_id}"
                if session.execute(
                    select(CreditLogRow.id).where(CreditLogRow.business_key == debit_key)
                ).scalar_one_or_none() is not None:
                    raise StoryboardGenerationUnavailable("扣款记录已占用，无法安全提交")
                session.add(
                    CreditLogRow(
                        id=self._new_identifier(),
                        user_id=actor.user_id,
                        amount=-candidate.quoted_amount,
                        balance_after=updated_balance,
                        type="usage",
                        description="storyboard.two_pass billing unit debit",
                        task_id=task_id,
                        business_key=debit_key,
                        billing_unit_id=unit_id,
                        related_debit_id=None,
                        created_at=now,
                    )
                )

            updated_quote = session.execute(
                update(TaskQuoteRow)
                .where(TaskQuoteRow.id == quote.id, TaskQuoteRow.consumed_at.is_(None))
                .values(
                    status="consumed",
                    confirmation_key=idempotency_key,
                    consumed_at=now,
                )
            )
            if updated_quote.rowcount != 1:
                raise StoryboardGenerationTaskConflict("该报价已被使用，请重新报价")
            claimed = session.execute(
                update(AITaskRow)
                .where(AITaskRow.id == task_id, AITaskRow.status == "queued")
                .values(status="processing", progress_message="开始分镜生成", updated_at=now)
            )
            if claimed.rowcount != 1:
                raise StoryboardGenerationUnavailable("无法确认生成任务状态")

            commit_attempted = True
            session.commit()
        except (StoryboardGenerationBadRequest, StoryboardGenerationForbidden,
                StoryboardGenerationNotFound, StoryboardGenerationTaskConflict,
                StoryboardGenerationUnavailable):
            if not commit_attempted:
                session.rollback()
            raise
        except Exception:
            if not commit_attempted:
                session.rollback()
                replay_session: Session | None = None
                try:
                    replay_session = self._session()
                    raced = replay_session.execute(
                        select(TaskSubmissionRow).where(
                            TaskSubmissionRow.user_id == actor.user_id,
                            TaskSubmissionRow.operation == OPERATION,
                            TaskSubmissionRow.idempotency_key == idempotency_key,
                        )
                    ).scalar_one_or_none()
                    if raced is not None:
                        if raced.request_digest != request_digest:
                            raise StoryboardGenerationTaskConflict(
                                "Idempotency-Key 已用于不同的报价请求"
                            )
                        return self._replay(replay_session, actor, raced)
                except (StoryboardGenerationTaskConflict, StoryboardGenerationUnavailable):
                    raise
                except Exception:
                    pass
                finally:
                    if replay_session is not None:
                        replay_session.close()
                raise StoryboardGenerationUnavailable("无法安全提交生成任务") from None
            raise StoryboardGenerationUnavailable(
                "任务提交结果暂时无法确认，请使用原幂等键查询"
            ) from None
        finally:
            session.close()

        if candidate is None:
            raise StoryboardGenerationUnavailable("报价内容暂时不可用")
        receipt = self._verify_committed_admission(actor.user_id, submission_id, task_id, unit_id)
        if receipt is None:
            raise StoryboardGenerationUnavailable(
                "任务提交结果暂时无法确认，请使用原幂等键查询"
            )
        execution = TwoPassExecutionInput(
            actor=actor,
            submission_id=submission_id,
            task_id=task_id,
            billing_unit_id=unit_id,
            quoted_amount=candidate.quoted_amount,
            billing_status=billing_status,
            model=candidate.model,
            scene_prompt=candidate.scene_prompt,
            frame_prompt=candidate.frame_prompt,
            raw_content=candidate.raw_content,
            series_id=candidate.series_id,
            temperature=candidate.temperature,
            enable_thinking=candidate.enable_thinking,
            skip_frame_generation=candidate.skip_frame_generation,
        )
        return TwoPassAdmission("new", receipt, execution=execution)

    def mark_completed(
        self,
        execution: TwoPassExecutionInput,
        result: Mapping[str, Any],
    ) -> bool:
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        now = _naive_utc(self._now())
        session = self._session()
        commit_attempted = False
        try:
            with session.begin():
                task = session.execute(
                    select(AITaskRow).where(
                        AITaskRow.id == execution.task_id,
                        AITaskRow.user_id == execution.actor.user_id,
                    ).with_for_update()
                ).scalar_one_or_none()
                unit = session.execute(
                    select(BillingUnitRow).where(
                        BillingUnitRow.id == execution.billing_unit_id,
                        BillingUnitRow.task_id == execution.task_id,
                    ).with_for_update()
                ).scalar_one_or_none()
                if task is None or unit is None:
                    return False
                if task.status == "completed":
                    return task.result == encoded
                if task.status != "processing" or unit.result_status != "pending":
                    return False
                submission = session.execute(
                    select(TaskSubmissionRow).where(
                        TaskSubmissionRow.id == execution.submission_id,
                        TaskSubmissionRow.user_id == execution.actor.user_id,
                    ).with_for_update()
                ).scalar_one_or_none()
                if submission is None or submission.status != "accepted":
                    raise _SettlementConflict()
                claimed_task = session.execute(
                    update(AITaskRow)
                    .where(
                        AITaskRow.id == execution.task_id,
                        AITaskRow.user_id == execution.actor.user_id,
                        AITaskRow.status == "processing",
                    )
                    .values(
                        status="completed",
                        result=encoded,
                        progress=100,
                        progress_message="分镜生成完成",
                        updated_at=now,
                    )
                )
                claimed_unit = session.execute(
                    update(BillingUnitRow)
                    .where(
                        BillingUnitRow.id == execution.billing_unit_id,
                        BillingUnitRow.task_id == execution.task_id,
                        BillingUnitRow.result_status == "pending",
                    )
                    .values(
                        result_status="succeeded",
                        success_confirmed_at=now,
                        updated_at=now,
                    )
                )
                claimed_submission = session.execute(
                    update(TaskSubmissionRow)
                    .where(
                        TaskSubmissionRow.id == execution.submission_id,
                        TaskSubmissionRow.status == "accepted",
                    )
                    .values(status="completed")
                )
                if (
                    claimed_task.rowcount != 1
                    or claimed_unit.rowcount != 1
                    or claimed_submission.rowcount != 1
                ):
                    raise _SettlementConflict()
                commit_attempted = True
            return self._verify_task_result(execution.task_id, execution.actor.user_id, encoded)
        except _SettlementConflict:
            return False
        except Exception:
            if commit_attempted:
                return False
            if self._verify_task_result(execution.task_id, execution.actor.user_id, encoded):
                return True
            if self._task_is_terminal(execution.task_id, execution.actor.user_id):
                return False
            raise
        finally:
            session.close()

    def mark_failed(self, execution: TwoPassExecutionInput, detail: str) -> bool:
        now = _naive_utc(self._now())
        safe_detail = detail[:1000] or "分镜生成失败"
        encoded = json.dumps({"error": safe_detail}, ensure_ascii=False, separators=(",", ":"))
        session = self._session()
        commit_attempted = False
        try:
            with session.begin():
                task = session.execute(
                    select(AITaskRow).where(
                        AITaskRow.id == execution.task_id,
                        AITaskRow.user_id == execution.actor.user_id,
                    ).with_for_update()
                ).scalar_one_or_none()
                unit = session.execute(
                    select(BillingUnitRow).where(
                        BillingUnitRow.id == execution.billing_unit_id,
                        BillingUnitRow.task_id == execution.task_id,
                    ).with_for_update()
                ).scalar_one_or_none()
                if task is None or unit is None:
                    return False
                if task.status == "failed":
                    return task.result == encoded
                if task.status != "processing" or unit.result_status != "pending":
                    return False

                submission = session.execute(
                    select(TaskSubmissionRow).where(
                        TaskSubmissionRow.id == execution.submission_id,
                        TaskSubmissionRow.user_id == task.user_id,
                    ).with_for_update()
                ).scalar_one_or_none()
                if submission is None or submission.status != "accepted":
                    raise _SettlementConflict()

                debit = None
                refund_key = f"refund:{unit.id}"
                if unit.debited_amount > 0:
                    debit = session.execute(
                        select(CreditLogRow).where(
                            CreditLogRow.business_key == f"debit:{unit.id}",
                            CreditLogRow.billing_unit_id == unit.id,
                            CreditLogRow.task_id == task.id,
                            CreditLogRow.type == "usage",
                            CreditLogRow.amount == -unit.debited_amount,
                        )
                    ).scalar_one_or_none()
                    if debit is None:
                        raise _SettlementConflict()
                    if session.execute(
                        select(CreditLogRow.id).where(CreditLogRow.business_key == refund_key)
                    ).scalar_one_or_none() is not None:
                        raise _SettlementConflict()
                    unit_billing_status = "refunded"
                    task_billing_status = "refunded"
                else:
                    unit_billing_status = "no_charge"
                    task_billing_status = "no_charge"

                claimed_task = session.execute(
                    update(AITaskRow)
                    .where(
                        AITaskRow.id == task.id,
                        AITaskRow.user_id == execution.actor.user_id,
                        AITaskRow.status == "processing",
                    )
                    .values(
                        status="failed",
                        billing_status=task_billing_status,
                        result=encoded,
                        progress=100,
                        progress_message="分镜生成失败",
                        updated_at=now,
                    )
                )
                claimed_unit = session.execute(
                    update(BillingUnitRow)
                    .where(
                        BillingUnitRow.id == unit.id,
                        BillingUnitRow.task_id == task.id,
                        BillingUnitRow.result_status == "pending",
                    )
                    .values(
                        result_status="failed",
                        billing_status=unit_billing_status,
                        refunded_amount=unit.debited_amount,
                        updated_at=now,
                    )
                )
                claimed_submission = session.execute(
                    update(TaskSubmissionRow)
                    .where(
                        TaskSubmissionRow.id == execution.submission_id,
                        TaskSubmissionRow.status == "accepted",
                    )
                    .values(status="failed")
                )
                if (
                    claimed_task.rowcount != 1
                    or claimed_unit.rowcount != 1
                    or claimed_submission.rowcount != 1
                ):
                    raise _SettlementConflict()

                if unit.debited_amount > 0:
                    balance_after = session.execute(
                        update(UserCreditRow)
                        .where(UserCreditRow.user_id == task.user_id)
                        .values(credits=UserCreditRow.credits + unit.debited_amount)
                        .returning(UserCreditRow.credits)
                    ).scalar_one_or_none()
                    if balance_after is None:
                        raise _SettlementConflict()
                    session.add(
                        CreditLogRow(
                            id=self._new_identifier(),
                            user_id=task.user_id,
                            amount=unit.debited_amount,
                            balance_after=balance_after,
                            type="refund",
                            description="storyboard.two_pass failure refund",
                            task_id=task.id,
                            business_key=refund_key,
                            billing_unit_id=unit.id,
                            related_debit_id=debit.id,
                            created_at=now,
                        )
                    )
                commit_attempted = True
            return self._verify_task_result(execution.task_id, execution.actor.user_id, encoded)
        except _SettlementConflict:
            return False
        except Exception:
            if commit_attempted:
                return False
            if self._verify_task_result(execution.task_id, execution.actor.user_id, encoded):
                return True
            if self._task_is_terminal(execution.task_id, execution.actor.user_id):
                return False
            raise
        finally:
            session.close()

    def _resolve_candidate(
        self,
        session: Session,
        actor: TrustedActor,
        request: TwoPassQuoteRequest,
    ) -> _Candidate:
        series_id = _required_text(request.series_id, "剧集ID不能为空")
        raw_content = _required_text(request.raw_content, "剧本内容不能为空")
        model_id = _required_text(request.model_id, "模型ID不能为空")
        scene_prompt_id = _required_text(request.scene_prompt_id, "请选择章节拆分提示词")
        frame_prompt_id = _optional_text(request.frame_prompt_id)
        if not request.skip_frame_generation and not frame_prompt_id:
            raise StoryboardGenerationBadRequest("请选择分镜提示词")

        self._require_active_member(session, actor.user_id)
        uow = SqlAlchemyStoryboardGenerationUnitOfWork(
            session,
            decrypt_api_key=self._decrypt_api_key,
            now=self._now,
            new_id=self._new_id,
        )
        uow.require_series_access(actor, series_id)

        model_source, model_row, unit_price = self._read_model(session, actor.user_id, model_id)
        if model_row["category"] not in {"text", "reasoning"}:
            raise StoryboardGenerationBadRequest("仅支持文本或推理模型")
        provider = str(model_row["provider"] or "").strip().lower()
        if provider not in _SUPPORTED_PROVIDERS:
            raise StoryboardGenerationUnavailable("当前模型暂不支持两步分镜生成")
        if type(unit_price) is not int or unit_price < 0:
            raise StoryboardGenerationUnavailable("模型报价不可用")
        multiplier = self._read_multiplier(session)
        model = uow.resolve_model(actor, model_id)
        if not isinstance(model.model, str) or not model.model.strip():
            raise StoryboardGenerationUnavailable("模型执行配置不完整，请检查服务配置")
        if model.api_key and any(
            model.api_key in value
            for value in (raw_content, scene_prompt_id, frame_prompt_id or "")
        ):
            raise StoryboardGenerationBadRequest("剧本内容不能包含所选模型凭据")

        scene_prompt, scene_source = self._read_prompt(
            session, actor.user_id, scene_prompt_id, "scene-segmentation"
        )
        frame_prompt = StoryboardPrompt("", "")
        frame_source: str | None = None
        if frame_prompt_id:
            frame_prompt, frame_source = self._read_prompt(
                session, actor.user_id, frame_prompt_id, "storyboard"
            )
            if model.api_key and model.api_key in frame_prompt.system_prompt + frame_prompt.user_prompt:
                raise StoryboardGenerationBadRequest("提示词不能包含所选模型凭据")
        if model.api_key and model.api_key in scene_prompt.system_prompt + scene_prompt.user_prompt:
            raise StoryboardGenerationBadRequest("提示词不能包含所选模型凭据")

        quoted_amount = unit_price * multiplier
        if quoted_amount > _MAX_CREDITS:
            raise StoryboardGenerationUnavailable("模型报价超出有效范围")
        manifest = {
            "series_id": series_id,
            "raw_content": raw_content,
            "model_id": model.id,
            "model_name": model.name,
            "model_source": model_source,
            "category": str(model_row["category"]),
            "base_unit_price": unit_price,
            "multiplier": multiplier,
            "quoted_amount": quoted_amount,
            "scene_prompt_id": scene_prompt_id,
            "scene_prompt_source": scene_source,
            "scene_prompt": {
                "system_prompt": scene_prompt.system_prompt,
                "user_prompt": scene_prompt.user_prompt,
            },
            "frame_prompt_id": frame_prompt_id,
            "frame_prompt_source": frame_source,
            "frame_prompt": {
                "system_prompt": frame_prompt.system_prompt,
                "user_prompt": frame_prompt.user_prompt,
            },
            "temperature": float(request.temperature),
            "enable_thinking": bool(request.enable_thinking),
            "skip_frame_generation": bool(request.skip_frame_generation),
        }
        input_digest = _digest(manifest)
        candidate_digest = _digest(
            {
                "model_id": model.id,
                "model_name": model.name,
                "model_source": model_source,
                "category": model_row["category"],
                "provider": provider,
                "wire_model": model.model,
                "execution_config_digest": _digest(
                    {
                        "api_key": model.api_key,
                        "base_url": model.base_url,
                        "params": model.params,
                    }
                ),
                "unit_price": unit_price,
                "multiplier": multiplier,
                "scene_prompt_id": scene_prompt_id,
                "scene_prompt_source": scene_source,
                "scene_prompt_digest": _digest(manifest["scene_prompt"]),
                "frame_prompt_id": frame_prompt_id,
                "frame_prompt_source": frame_source,
                "frame_prompt_digest": _digest(manifest["frame_prompt"]),
            }
        )
        return _Candidate(
            model=model,
            model_source=model_source,
            unit_price=unit_price,
            multiplier=multiplier,
            quoted_amount=quoted_amount,
            scene_prompt=scene_prompt,
            frame_prompt=frame_prompt,
            series_id=series_id,
            raw_content=raw_content,
            temperature=float(request.temperature),
            enable_thinking=bool(request.enable_thinking),
            skip_frame_generation=bool(request.skip_frame_generation),
            manifest=manifest,
            input_digest=input_digest,
            candidate_digest=candidate_digest,
        )

    def _read_model(
        self,
        session: Session,
        user_id: str,
        model_id: str,
    ) -> tuple[str, Mapping[str, Any], int]:
        row = session.execute(
            select(
                system_models.c.id,
                system_models.c.category,
                system_models.c.name,
                system_models.c.provider,
                system_models.c.model,
                system_models.c.cost_per_call,
            ).where(system_models.c.id == model_id, system_models.c.is_active.is_(True))
        ).mappings().first()
        if row is not None:
            price = row["cost_per_call"]
            return "system", row, price
        row = session.execute(
            select(
                model_configs.c.id,
                model_configs.c.category,
                model_configs.c.name,
                model_configs.c.provider,
                model_configs.c.model,
            ).where(model_configs.c.id == model_id, model_configs.c.user_id == user_id)
        ).mappings().first()
        if row is None:
            raise StoryboardGenerationNotFound("模型不存在或已禁用")
        return "user", row, 0

    @staticmethod
    def _read_prompt(
        session: Session,
        user_id: str,
        prompt_id: str,
        expected_category: str,
    ) -> tuple[StoryboardPrompt, str]:
        row = session.execute(
            select(
                prompt_configs.c.category,
                prompt_configs.c.system_prompt,
                prompt_configs.c.user_prompt,
            ).where(prompt_configs.c.id == prompt_id, prompt_configs.c.user_id == user_id)
        ).mappings().first()
        source = "user"
        if row is None:
            row = session.execute(
                select(
                    system_prompts.c.category,
                    system_prompts.c.system_prompt,
                    system_prompts.c.user_prompt,
                ).where(
                    system_prompts.c.id == prompt_id,
                    system_prompts.c.is_active.is_(True),
                )
            ).mappings().first()
            source = "system"
        if row is None:
            raise StoryboardGenerationNotFound("提示词不存在、已停用或无权引用")
        if row["category"] != expected_category:
            raise StoryboardGenerationBadRequest("所选提示词类别不匹配")
        return StoryboardPrompt(
            system_prompt=row["system_prompt"] or "",
            user_prompt=row["user_prompt"] or "",
        ), source

    def _read_multiplier(self, session: Session) -> int:
        rows = session.execute(
            select(system_configs.c.storyboard_cost_multiplier).limit(2)
        ).scalars().all()
        if not rows:
            return 2
        if len(rows) != 1:
            raise StoryboardGenerationUnavailable("网站计费配置不可用")
        value = rows[0]
        if type(value) is not int or value <= 0:
            raise StoryboardGenerationUnavailable("网站计费配置不可用")
        return value

    def _require_active_member(self, session: Session, user_id: str) -> None:
        row = session.execute(
            select(
                users.c.membership_type,
                users.c.membership_expires_at,
                users.c.is_superuser,
            ).where(users.c.id == user_id)
        ).mappings().one_or_none()
        now = _naive_utc(self._now())
        if row is None or (
            not row["is_superuser"]
            and (
                row["membership_type"] != "premium"
                or (
                    row["membership_expires_at"] is not None
                    and _naive_utc(row["membership_expires_at"]) < now
                )
            )
        ):
            raise StoryboardGenerationForbidden("需要有效会员才能提交两步分镜生成")

    def _replay(
        self,
        session: Session,
        actor: TrustedActor,
        submission: TaskSubmissionRow,
    ) -> TwoPassAdmission:
        task_ids = submission.task_ids if isinstance(submission.task_ids, list) else []
        if len(task_ids) != 1:
            raise StoryboardGenerationUnavailable("任务提交记录不完整")
        task = session.execute(
            select(AITaskRow).where(
                AITaskRow.id == task_ids[0], AITaskRow.user_id == actor.user_id
            )
        ).scalar_one_or_none()
        unit = session.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_ids[0])
        ).scalar_one_or_none()
        if task is None or unit is None or unit.operation != OPERATION:
            raise StoryboardGenerationUnavailable("任务提交回执不完整")
        receipt = _receipt(submission.id, task, unit)
        if task.status in {"queued", "processing"}:
            return TwoPassAdmission("pending", receipt)
        if task.status == "completed":
            return TwoPassAdmission("completed", receipt, result=_decode_object(task.result))
        if task.status == "failed":
            return TwoPassAdmission("failed", receipt, result=_decode_object(task.result))
        return TwoPassAdmission("pending", receipt)

    def _verify_committed_admission(
        self,
        user_id: str,
        submission_id: str,
        task_id: str,
        unit_id: str,
    ) -> dict[str, Any] | None:
        session = self._session()
        try:
            submission = session.get(TaskSubmissionRow, submission_id)
            task = session.get(AITaskRow, task_id)
            unit = session.get(BillingUnitRow, unit_id)
            if (
                submission is None
                or submission.user_id != user_id
                or submission.task_ids != [task_id]
                or task is None
                or task.user_id != user_id
                or task.type != TASK_TYPE
                or task.status != "processing"
                or unit is None
                or unit.task_id != task_id
                or unit.operation != OPERATION
            ):
                return None
            if unit.debited_amount > 0:
                debit = session.execute(
                    select(CreditLogRow).where(
                        CreditLogRow.business_key == f"debit:{unit.id}",
                        CreditLogRow.billing_unit_id == unit.id,
                        CreditLogRow.task_id == task.id,
                        CreditLogRow.user_id == user_id,
                        CreditLogRow.type == "usage",
                        CreditLogRow.amount == -unit.debited_amount,
                    )
                ).scalar_one_or_none()
                if debit is None:
                    return None
            return _receipt(submission_id, task, unit)
        finally:
            session.close()

    def _verify_task_result(self, task_id: str, user_id: str, encoded: str) -> bool:
        session = self._session()
        try:
            task = session.execute(
                select(
                    AITaskRow.status,
                    AITaskRow.result,
                    AITaskRow.billing_status,
                    BillingUnitRow.id.label("unit_id"),
                    BillingUnitRow.result_status.label("unit_result_status"),
                    BillingUnitRow.billing_status.label("unit_billing_status"),
                    BillingUnitRow.quoted_amount.label("unit_quoted_amount"),
                    BillingUnitRow.debited_amount.label("unit_debited_amount"),
                    BillingUnitRow.refunded_amount.label("unit_refunded_amount"),
                )
                .join(BillingUnitRow, BillingUnitRow.task_id == AITaskRow.id)
                .where(
                    AITaskRow.id == task_id,
                    AITaskRow.user_id == user_id,
                )
            ).one_or_none()
            if (
                task is None
                or task.status not in {"completed", "failed"}
                or task.result != encoded
                or task.unit_result_status != ("succeeded" if task.status == "completed" else "failed")
                or task.billing_status != task.unit_billing_status
            ):
                return False
            if task.status == "completed":
                return task.unit_refunded_amount == 0 and task.unit_billing_status in {
                    "charged", "no_charge"
                }
            if task.unit_debited_amount == 0:
                return task.unit_refunded_amount == 0 and task.unit_billing_status == "no_charge"
            if (
                task.unit_refunded_amount != task.unit_debited_amount
                or task.unit_billing_status != "refunded"
            ):
                return False
            debit = session.execute(
                select(CreditLogRow).where(
                    CreditLogRow.business_key == f"debit:{task.unit_id}",
                    CreditLogRow.billing_unit_id == task.unit_id,
                    CreditLogRow.task_id == task_id,
                    CreditLogRow.type == "usage",
                    CreditLogRow.amount == -task.unit_debited_amount,
                )
            ).scalar_one_or_none()
            refund = session.execute(
                select(CreditLogRow).where(
                    CreditLogRow.business_key == f"refund:{task.unit_id}",
                    CreditLogRow.billing_unit_id == task.unit_id,
                    CreditLogRow.task_id == task_id,
                    CreditLogRow.related_debit_id == (debit.id if debit is not None else ""),
                    CreditLogRow.type == "refund",
                    CreditLogRow.amount == task.unit_refunded_amount,
                )
            ).scalar_one_or_none()
            return debit is not None and refund is not None
        finally:
            session.close()

    def _task_is_terminal(self, task_id: str, user_id: str) -> bool:
        session = self._session()
        try:
            status = session.execute(
                select(AITaskRow.status).where(
                    AITaskRow.id == task_id,
                    AITaskRow.user_id == user_id,
                )
            ).scalar_one_or_none()
            return status in {"completed", "failed"}
        finally:
            session.close()

    def _session(self) -> Session:
        if self._session_factory is None:
            raise StoryboardGenerationUnavailable("AI计费服务尚未接线")
        try:
            return self._session_factory()
        except Exception:
            raise StoryboardGenerationUnavailable("AI计费服务尚未接线") from None

    def _new_identifier(self) -> str:
        value = self._new_id()
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 36:
            raise StoryboardGenerationUnavailable("服务端标识生成失败")
        return value


@dataclass(frozen=True, slots=True)
class _Candidate:
    model: StoryboardServerModel = field(repr=False)
    model_source: str
    unit_price: int
    multiplier: int
    quoted_amount: int
    scene_prompt: StoryboardPrompt = field(repr=False)
    frame_prompt: StoryboardPrompt = field(repr=False)
    series_id: str
    raw_content: str = field(repr=False)
    temperature: float
    enable_thinking: bool
    skip_frame_generation: bool
    manifest: dict[str, Any] = field(repr=False)
    input_digest: str
    candidate_digest: str


def validate_idempotency_key(value: str | None) -> str:
    if value is None or not value.strip() or len(value) > 255:
        raise ValueError("Idempotency-Key 必须为 1 到 255 个非空白字符")
    return value


def parse_generated_frames(
    content: str,
    segments: list[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Require one nonempty step-two frame per segment; never fall back to input."""
    if not isinstance(content, str) or not content.strip() or not segments:
        raise ValueError("AI 返回的分镜格式为空")
    parts = content.split("_::~RECORD::~_")
    if len(parts) != len(segments) or any(not part.strip() for part in parts):
        raise ValueError("AI 返回的分镜数量或格式不匹配")
    return [{"text": part.strip(), "duration": 5} for part in parts]


def _request_from_manifest(manifest: Mapping[str, Any]) -> TwoPassQuoteRequest:
    try:
        return TwoPassQuoteRequest.model_validate(
            {
                "series_id": manifest["series_id"],
                "raw_content": manifest["raw_content"],
                "model_id": manifest["model_id"],
                "scene_prompt_id": manifest["scene_prompt_id"],
                "frame_prompt_id": manifest.get("frame_prompt_id"),
                "temperature": manifest["temperature"],
                "enable_thinking": manifest["enable_thinking"],
                "skip_frame_generation": manifest["skip_frame_generation"],
            }
        )
    except Exception:
        raise StoryboardGenerationUnavailable("报价内容暂时不可用") from None


def _read_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise StoryboardGenerationUnavailable("报价内容暂时不可用")
    manifest = value[0]
    if any(key in manifest for key in ("api_key", "base_url", "params", "credential")):
        raise StoryboardGenerationUnavailable("报价内容包含不安全字段")
    return manifest


def _receipt(submission_id: str, task: AITaskRow, unit: BillingUnitRow) -> dict[str, Any]:
    return {
        "submission_id": submission_id,
        "operation": OPERATION,
        "task_id": task.id,
        "status": task.status,
        "quoted_amount": unit.quoted_amount,
        "billing_status": unit.billing_status,
    }


def _decode_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _required_text(value: object, detail: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StoryboardGenerationBadRequest(detail)
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StoryboardGenerationBadRequest("提示词ID无效")
    cleaned = value.strip()
    return cleaned or None


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


__all__ = (
    "OPERATION",
    "TASK_TYPE",
    "TwoPassAdmission",
    "TwoPassBillingService",
    "TwoPassExecutionInput",
    "TwoPassQuoteRequest",
    "TwoPassStreamRequest",
    "parse_generated_frames",
    "validate_idempotency_key",
)
