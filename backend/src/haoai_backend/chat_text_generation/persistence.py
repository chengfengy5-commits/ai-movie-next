"""Legacy-compatible SQL admission and stream settlement for chat text."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.admin_credit_queries.tables import CreditLogRow
from haoai_backend.authentication.tables import user_credits, users
from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_task_concurrency import (
    IN_FLIGHT_STATES as ACTIVE_TASK_STATES,
    SOURCE_WORKER_TYPES as CONCURRENT_TASK_TYPES,
    count_user_in_flight_tasks,
    session_uses_fresh_postgres_statements,
)
from haoai_backend.generation_core.fee_phases import (
    StreamFeePlanInput,
    build_fee_phase_plan,
)
from haoai_backend.generation_core.schema import StreamFeeRecordRow
from haoai_backend.model_configs.tables import (
    ModelConfigRow,
    SystemModelRow,
    UserModelOrderRow,
)
from haoai_backend.prompts.tables import prompt_configs, system_prompts
from haoai_backend.series_access.application import (
    require_series_access,
)
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text

from .domain import (
    PreparedStream,
    batch_request_data,
    review_request_data,
    stream_provenance,
    with_task,
)
from .errors import ChatTextGenerationError
from .ports import (
    ApiKeyDecryptor,
    ChatTextModel,
    SessionFactory,
)

chapter_table = Table(
    "chapters",
    MetaData(),
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("title", String(255), nullable=False),
    Column("content", Text, nullable=True),
    Column("order", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

BATCH_RUNNING_TASK_STATES = ("queued", "processing", "cancelling")


def _begin_write(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _new_id(factory: Callable[[], str] | None) -> str:
    try:
        value = factory() if factory is not None else str(uuid4())
    except Exception as error:
        raise ChatTextGenerationError(503, "服务暂不可用") from error
    if not isinstance(value, str) or not value:
        raise ChatTextGenerationError(503, "服务暂不可用")
    return value


def _now(clock: Callable[[], datetime] | None) -> datetime:
    try:
        value = clock() if clock is not None else datetime.now(timezone.utc)
        if not isinstance(value, datetime):
            raise TypeError("clock must return datetime")
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    except Exception as error:
        raise ChatTextGenerationError(503, "服务暂不可用") from error


def _model_value(row: Any, *, is_system: bool) -> ChatTextModel:
    return ChatTextModel(
        id=row.id,
        name=row.name,
        provider=row.provider,
        api_key=row.api_key,
        model=row.model,
        base_url=row.base_url,
        cost_per_call=int(getattr(row, "cost_per_call", 0) or 0) if is_system else 0,
        is_system=is_system,
    )


def _resolve_model(
    session: Session,
    user_id: str,
    model_id: str,
    *,
    automatic_text: bool = False,
) -> ChatTextModel:
    if automatic_text:
        system_rows = list(
            session.execute(
                select(SystemModelRow)
                .where(SystemModelRow.category == "text", SystemModelRow.is_active.is_(True))
                .order_by(SystemModelRow.sort_order)
            ).scalars()
        )
        personal_rows = list(
            session.execute(
                select(ModelConfigRow)
                .where(
                    ModelConfigRow.user_id == user_id,
                    ModelConfigRow.category == "text",
                )
                .order_by(ModelConfigRow.sort_order)
            ).scalars()
        )
        choices: list[tuple[Any, bool]] = [
            *((row, True) for row in system_rows),
            *((row, False) for row in personal_rows),
        ]
        order = list(
            session.execute(
                select(UserModelOrderRow.model_id)
                .where(
                    UserModelOrderRow.user_id == user_id,
                    UserModelOrderRow.category == "text",
                )
                .order_by(UserModelOrderRow.sort_order)
            ).scalars()
        )
        if order:
            choices.sort(
                key=lambda item: (
                    order.index(item[0].id) if item[0].id in order else len(order)
                )
            )
        if not choices:
            raise ChatTextGenerationError(
                400,
                "当前没有可用的文本模型，请先到「模型设置」里添加一个文本模型后再试",
            )
        row, is_system = choices[0]
        return _model_value(row, is_system=is_system)

    system = session.execute(
        select(SystemModelRow).where(
            SystemModelRow.id == model_id,
            SystemModelRow.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if system is not None:
        return _model_value(system, is_system=True)

    personal = session.execute(
        select(ModelConfigRow).where(
            ModelConfigRow.id == model_id,
            ModelConfigRow.user_id == user_id,
        )
    ).scalar_one_or_none()
    if personal is not None:
        return _model_value(personal, is_system=False)
    raise ChatTextGenerationError(404, "模型不存在或已禁用")


def _resolve_prompt(session: Session, user_id: str, prompt_id: str) -> None:
    if not prompt_id:
        return
    system_id = session.execute(
        select(system_prompts.c.id).where(
            system_prompts.c.id == prompt_id,
            system_prompts.c.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if system_id is not None:
        return
    personal_id = session.execute(
        select(prompt_configs.c.id).where(
            prompt_configs.c.id == prompt_id,
            prompt_configs.c.user_id == user_id,
        )
    ).scalar_one_or_none()
    if personal_id is None:
        raise ChatTextGenerationError(
            400,
            "所选提示词无法引用（不存在或已停用），请重新选择后再试",
        )


def _series_id_for_chapter(session: Session, user_id: str, chapter_id: str) -> str:
    row = session.execute(
        select(chapter_table.c.series_id).where(chapter_table.c.id == chapter_id)
    ).scalar_one_or_none()
    if row is None:
        raise ChatTextGenerationError(404, "章节不存在")
    try:
        require_series_access(
            SqlAlchemySeriesAccessReader(session),
            ActorIdentity(user_id=user_id),
            row,
        )
    except SeriesNotFound as error:
        raise ChatTextGenerationError(404, str(error)) from error
    except SeriesAccessDenied as error:
        raise ChatTextGenerationError(403, str(error)) from error
    return row


def _check_credit(session: Session, user_id: str, quote: int) -> int:
    balance = session.execute(
        select(user_credits.c.credits).where(user_credits.c.user_id == user_id)
    ).scalar_one_or_none()
    current = int(balance or 0)
    if current < quote:
        raise ChatTextGenerationError(
            402,
            f"积分不足，需要 {quote} 积分，当前剩余 {current} 积分，请联系管理员充值",
        )
    return current


def _load_message_scope(session: Session, user_id: str, message_id: str | None) -> str | None:
    if not message_id:
        return None
    row = session.execute(
        select(chat_messages.c.chapter_id).where(chat_messages.c.id == message_id)
    ).scalar_one_or_none()
    if row is None:
        raise ChatTextGenerationError(404, "关联消息不存在")
    _series_id_for_chapter(session, user_id, row)
    return row


def prepare_stream_and_commit_fee(
    session_factory: SessionFactory | None,
    user_id: str,
    request: Any,
    *,
    decrypt_api_key: ApiKeyDecryptor | None,
    id_factory: Callable[[], str] | None,
    clock: Callable[[], datetime] | None,
) -> PreparedStream:
    if session_factory is None:
        raise ChatTextGenerationError(503, "聊天服务暂不可用")

    messages = tuple({"role": item.role, "content": item.content} for item in request.messages)
    if not request.model_id.strip():
        raise ChatTextGenerationError(400, "模型ID不能为空")
    if not messages:
        raise ChatTextGenerationError(400, "消息列表不能为空")

    attempt_id = _new_id(id_factory)
    now = _now(clock)
    session = session_factory()
    model: ChatTextModel | None = None
    key = ""
    try:
        _begin_write(session)
        model = _resolve_model(session, user_id, request.model_id.strip())
        message_id = request.message_id or None
        message_chapter_id = _load_message_scope(session, user_id, message_id)

        key = model.api_key or ""
        if key.startswith("gAAAAA"):
            if decrypt_api_key is None:
                raise ChatTextGenerationError(503, "模型凭据暂不可用")
            try:
                key = decrypt_api_key(key)
            except Exception as error:
                raise ChatTextGenerationError(503, "模型凭据暂不可用") from error
        prepared_model = ChatTextModel(
            id=model.id,
            name=model.name,
            provider=model.provider,
            api_key=key,
            model=model.model,
            base_url=model.base_url,
            cost_per_call=model.cost_per_call,
            is_system=model.is_system,
        )

        quote = model.cost_per_call if model.is_system else 0
        if quote < 0:
            raise ChatTextGenerationError(503, "模型报价不可用")
        if not model.is_system:
            disposition = "byok"
        elif quote <= 0:
            disposition = "zero"
        elif request.skip_credit_check:
            disposition = "skipped"
        else:
            disposition = "debit"

        actual_debit = 0
        original_log_id: str | None = None
        business_key: str | None = None
        if disposition in {"debit", "skipped"}:
            balance = _check_credit(session, user_id, quote)
        else:
            balance = 0

        if disposition == "debit":
            business_key = "stream:debit:" + attempt_id
            existing = session.execute(
                select(CreditLogRow.id).where(CreditLogRow.business_key == business_key)
            ).scalar_one_or_none()
            if existing is not None:
                raise ChatTextGenerationError(503, "聊天提交结果暂时无法安全确认")
            updated = session.execute(
                update(user_credits)
                .where(
                    user_credits.c.user_id == user_id,
                    user_credits.c.credits >= quote,
                )
                .values(credits=user_credits.c.credits - quote)
                .returning(user_credits.c.credits)
            ).scalar_one_or_none()
            if updated is None:
                raise ChatTextGenerationError(
                    402, f"积分不足，需要 {quote} 积分"
                )
            actual_debit = quote
            original_log_id = _new_id(id_factory)
            session.execute(
                insert(CreditLogRow.__table__).values(
                    id=original_log_id,
                    user_id=user_id,
                    amount=-quote,
                    balance_after=updated,
                    type="usage",
                    description=f"使用 {model.name} 消耗积分",
                    task_id=None,
                    business_key=business_key,
                    billing_unit_id=None,
                    related_debit_id=None,
                    created_at=now,
                )
            )

        build_fee_phase_plan(
            "text.stream",
            stream=StreamFeePlanInput(
                server_attempt_id=attempt_id,
                server_quote=quote,
                disposition=disposition,
                message_id=message_id,
            ),
        )
        session.add(
            StreamFeeRecordRow(
                server_attempt_id=attempt_id,
                owner_id=user_id,
                operation="text.stream",
                frozen_model_provenance=stream_provenance(model, user_id),
                quoted_amount=quote,
                disposition=disposition,
                actual_debited_amount=actual_debit,
                original_debit_log_id=original_log_id,
                original_business_key=business_key,
                settlement_state="committed",
                start_state="ready_to_start",
                start_token=None,
                task_id=None,
                message_id=message_id,
                created_at=now,
                updated_at=now,
            )
        )
        commit_error: Exception | None = None
        try:
            session.commit()
        except Exception as error:
            commit_error = error
            session.rollback()
        finally:
            session.close()
            session = None
        if not confirm_stream_fee(
            session_factory,
            attempt_id=attempt_id,
            user_id=user_id,
            model=model,
            quote=quote,
            disposition=disposition,
            actual_debit=actual_debit,
            original_log_id=original_log_id,
            business_key=business_key,
            message_id=message_id,
        ):
            raise ChatTextGenerationError(
                503, "聊天提交结果暂时无法确认"
            ) from commit_error
        return PreparedStream(
            actor_id=user_id,
            attempt_id=attempt_id,
            model=prepared_model,
            messages=messages,
            enable_thinking=request.enable_thinking,
            task_id=None,
            message_id=message_id,
            original_key=key,
            message_chapter_id=message_chapter_id,
            quoted_amount=quote,
            disposition=disposition,
            actual_debited_amount=actual_debit,
            original_debit_log_id=original_log_id,
            original_business_key=business_key,
        )
    except ChatTextGenerationError:
        if session is not None:
            session.rollback()
        raise
    except SQLAlchemyError as error:
        if session is not None:
            session.rollback()
        raise ChatTextGenerationError(503, "聊天服务暂不可用") from error
    finally:
        if session is not None:
            session.close()


def confirm_stream_fee(
    session_factory: SessionFactory | None,
    *,
    attempt_id: str,
    user_id: str,
    model: ChatTextModel,
    quote: int,
    disposition: str,
    actual_debit: int,
    original_log_id: str | None,
    business_key: str | None,
    message_id: str | None,
) -> bool:
    if session_factory is None:
        return False
    session = session_factory()
    try:
        proof = session.get(StreamFeeRecordRow, attempt_id)
        if proof is None or (
            proof.owner_id != user_id
            or proof.operation != "text.stream"
            or proof.frozen_model_provenance != stream_provenance(model, user_id)
            or proof.quoted_amount != quote
            or proof.disposition != disposition
            or proof.actual_debited_amount != actual_debit
            or proof.original_debit_log_id != original_log_id
            or proof.original_business_key != business_key
            or proof.settlement_state != "committed"
            or proof.task_id is not None
            or proof.message_id != message_id
        ):
            return False
        if actual_debit:
            if (
                disposition != "debit"
                or actual_debit != quote
                or business_key != "stream:debit:" + attempt_id
            ):
                return False
            log = session.execute(
                select(CreditLogRow).where(CreditLogRow.business_key == business_key)
            ).scalar_one_or_none()
            if log is None or (
                log.id != original_log_id
                or log.user_id != user_id
                or log.amount != -actual_debit
                or log.type != "usage"
                or log.task_id is not None
                or log.related_debit_id is not None
            ):
                return False
        elif original_log_id is not None or business_key is not None:
            return False
        elif _usage_log_exists(session, attempt_id):
            return False
        refund = session.execute(
            select(CreditLogRow.id).where(
                CreditLogRow.business_key == "stream:refund:" + attempt_id
            )
        ).scalar_one_or_none()
        if refund is not None:
            return False
        return True
    finally:
        session.close()


def bind_stream_task(
    session_factory: SessionFactory | None,
    prepared: PreparedStream,
    task_type: str,
    *,
    id_factory: Callable[[], str] | None,
    clock: Callable[[], datetime] | None,
) -> PreparedStream:
    if not prepared.message_id:
        return prepared
    if session_factory is None:
        raise ChatTextGenerationError(503, "聊天服务暂不可用")
    task_id = _new_id(id_factory)
    now = _now(clock)
    task_request_data = prepared.messages[-1]["content"][:500] if prepared.messages else ""
    session = session_factory()
    try:
        _begin_write(session)
        proof = session.get(StreamFeeRecordRow, prepared.attempt_id)
        if proof is None or proof.owner_id != prepared.actor_id or proof.task_id is not None:
            raise ChatTextGenerationError(503, "聊天提交结果暂时无法安全确认")
        task = AITaskRow(
            id=task_id,
            user_id=prepared.actor_id,
            type=task_type or "chat",
            status="processing",
            execution_generation=0,
            claim_token=None,
            message_id=prepared.message_id,
            credit_cost=proof.quoted_amount,
            model_name=prepared.model.name,
            request_data=task_request_data,
            progress=0,
            progress_message=None,
            result=None,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        linked = session.execute(
            update(StreamFeeRecordRow)
            .where(
                StreamFeeRecordRow.server_attempt_id == prepared.attempt_id,
                StreamFeeRecordRow.owner_id == prepared.actor_id,
                StreamFeeRecordRow.task_id.is_(None),
                StreamFeeRecordRow.settlement_state == "committed",
            )
            .values(task_id=task_id, updated_at=now)
        ).rowcount
        if linked != 1:
            raise ChatTextGenerationError(503, "聊天任务提交结果暂时无法确认")
        commit_error: Exception | None = None
        try:
            session.commit()
        except Exception as error:
            commit_error = error
            session.rollback()
        finally:
            session.close()
            session = None
        if not confirm_stream_task(
            session_factory,
            prepared,
            task_id,
            task_type=task_type or "chat",
            request_data=task_request_data,
        ):
            raise ChatTextGenerationError(
                503, "聊天任务提交结果暂时无法确认"
            ) from commit_error
        return with_task(
            prepared,
            task_id,
            task_type=task_type or "chat",
            execution_generation=0,
            claim_token=None,
        )
    except ChatTextGenerationError:
        if session is not None:
            session.rollback()
        raise
    except SQLAlchemyError as error:
        if session is not None:
            session.rollback()
        raise ChatTextGenerationError(503, "聊天任务提交失败") from error
    finally:
        if session is not None:
            session.close()


def confirm_stream_task(
    session_factory: SessionFactory | None,
    prepared: PreparedStream,
    task_id: str,
    *,
    task_type: str,
    request_data: str,
) -> bool:
    if session_factory is None:
        return False
    session = session_factory()
    try:
        task = session.get(AITaskRow, task_id)
        proof = session.get(StreamFeeRecordRow, prepared.attempt_id)
        expected = with_task(
            prepared,
            task_id,
            task_type=task_type,
            execution_generation=0,
            claim_token=None,
        )
        if task is None or proof is None or (
            task.user_id != prepared.actor_id
            or task.message_id != prepared.message_id
            or task.status != "processing"
            or task.execution_generation != 0
            or task.claim_token is not None
            or task.type != task_type
            or task.request_data != request_data
            or task.credit_cost != prepared.quoted_amount
            or task.model_name != prepared.model.name
            or proof.settlement_state != "committed"
            or proof.start_state != "ready_to_start"
            or not _proof_matches_prepared(proof, expected)
        ):
            return False
        if prepared.actual_debited_amount > 0:
            return _verified_usage_log(session, proof, prepared) is not None
        return (
            session.execute(
                select(CreditLogRow.id).where(
                    CreditLogRow.business_key == "stream:debit:" + prepared.attempt_id
                )
            ).scalar_one_or_none()
            is None
        )
    finally:
        session.close()


def _lock_account(session: Session, user_id: str) -> None:
    session.execute(
        select(users.c.id).where(users.c.id == user_id).with_for_update()
    ).first()


def _check_concurrency(
    session: Session,
    user_id: str,
    max_user_concurrency: int,
    *,
    account_locked: bool = False,
) -> None:
    if max_user_concurrency <= 0:
        return
    if not account_locked:
        _lock_account(session, user_id)
    if not session_uses_fresh_postgres_statements(session):
        raise ChatTextGenerationError(503, "数据库隔离级别无法安全确认用户并发限额")
    current = count_user_in_flight_tasks(session, user_id)
    if current + 1 > max_user_concurrency:
        raise ChatTextGenerationError(
            429,
            f"您同时在处理的任务已达上限（{max_user_concurrency}），请等待部分完成后再提交",
        )


def _check_queue_balance(session: Session, user_id: str, model: ChatTextModel) -> None:
    if model.is_system and model.cost_per_call > 0:
        _check_credit(session, user_id, model.cost_per_call)


def admit_ai_review(
    session_factory: SessionFactory | None,
    user_id: str,
    request: Any,
    *,
    max_user_concurrency: int,
    id_factory: Callable[[], str] | None,
    clock: Callable[[], datetime] | None,
) -> dict[str, Any]:
    if not request.chapter_id:
        raise ChatTextGenerationError(400, "缺少章节ID")
    if request.frame_index is None or request.frame_index < 0:
        raise ChatTextGenerationError(400, "缺少分镜索引")
    task_id = _new_id(id_factory)
    message_id = _new_id(id_factory)
    now = _now(clock)
    session = _open(session_factory)
    try:
        _begin_write(session)
        _series_id_for_chapter(session, user_id, request.chapter_id)
        _resolve_prompt(session, user_id, request.prompt_id)
        model_id = (request.model_id or "").strip()
        model = (
            _resolve_model(session, user_id, model_id)
            if model_id
            else _resolve_model(session, user_id, "", automatic_text=True)
        )
        _check_queue_balance(session, user_id, model)
        _check_concurrency(session, user_id, max_user_concurrency)
        task = _queue_task(
            task_id,
            user_id,
            "ai-review",
            message_id,
            model.name,
            review_request_data(request, model),
            now,
        )
        session.add(task)
        _commit_queue(session, task)
        return {
            "task_id": task_id,
            "model_name": model.name,
            "is_system": model.is_system,
            "credit_cost": model.cost_per_call,
        }
    except ChatTextGenerationError:
        session.rollback()
        raise
    except SQLAlchemyError as error:
        session.rollback()
        raise ChatTextGenerationError(503, "提交 AI 复核任务失败") from error
    finally:
        if not session.info.get("chat_text_closed_for_confirmation"):
            session.close()


def admit_batch_optimize(
    session_factory: SessionFactory | None,
    user_id: str,
    request: Any,
    *,
    max_user_concurrency: int,
    id_factory: Callable[[], str] | None,
    clock: Callable[[], datetime] | None,
) -> dict[str, Any]:
    if not request.frames:
        raise ChatTextGenerationError(400, "请至少选择一个分镜")
    if len(request.frames) > 30:
        raise ChatTextGenerationError(400, "一次最多优化30个分镜")
    if not 3 <= request.concurrency <= 10:
        raise ChatTextGenerationError(400, "并发数需在 3~10 之间")
    if not request.model_id.strip():
        raise ChatTextGenerationError(400, "模型ID不能为空")
    task_id = _new_id(id_factory)
    message_id = _new_id(id_factory)
    now = _now(clock)
    session = _open(session_factory)
    try:
        _begin_write(session)
        _series_id_for_chapter(session, user_id, request.chapter_id)
        _lock_account(session, user_id)
        running_data = session.execute(
            select(AITaskRow.request_data).where(
                AITaskRow.user_id == user_id,
                AITaskRow.type == "batch-optimize",
                AITaskRow.status.in_(BATCH_RUNNING_TASK_STATES),
            )
        ).scalars()
        if any(_request_targets_chapter(value, request.chapter_id) for value in running_data):
            raise ChatTextGenerationError(
                409,
                "该章节已有批量优化任务正在运行，请等待其完成或先停止，再发起新的优化",
            )
        model = _resolve_model(session, user_id, request.model_id.strip())
        _resolve_prompt(session, user_id, request.prompt_id)
        _check_queue_balance(session, user_id, model)
        _check_concurrency(
            session, user_id, max_user_concurrency, account_locked=True
        )
        task = _queue_task(
            task_id,
            user_id,
            "batch-optimize",
            message_id,
            model.name,
            batch_request_data(request, model),
            now,
        )
        session.add(task)
        _commit_queue(session, task)
        return {
            "task_id": task_id,
            "chapter_id": request.chapter_id,
            "frame_count": len(request.frames),
        }
    except ChatTextGenerationError:
        session.rollback()
        raise
    except SQLAlchemyError as error:
        session.rollback()
        raise ChatTextGenerationError(503, "提交后台批量优化失败") from error
    finally:
        if not session.info.get("chat_text_closed_for_confirmation"):
            session.close()


def _request_targets_chapter(request_data: str | None, chapter_id: str) -> bool:
    try:
        decoded = json.loads(request_data or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(decoded, dict) and decoded.get("chapter_id") == chapter_id


def _open(session_factory: SessionFactory | None) -> Session:
    if session_factory is None:
        raise ChatTextGenerationError(503, "聊天服务暂不可用")
    return session_factory()


def _queue_task(
    task_id: str,
    user_id: str,
    task_type: str,
    message_id: str,
    model_name: str,
    request_data: str,
    now: datetime,
) -> AITaskRow:
    return AITaskRow(
        id=task_id,
        user_id=user_id,
        type=task_type,
        status="queued",
        message_id=message_id,
        credit_cost=0,
        model_name=model_name,
        progress=0,
        progress_message="任务已提交，排队等待 Worker 认领...",
        request_data=request_data,
        result=None,
        created_at=now,
        updated_at=now,
    )


def _commit_queue(session: Session, task: AITaskRow) -> None:
    expected = {
        "id": task.id,
        "user_id": task.user_id,
        "type": task.type,
        "message_id": task.message_id,
        "model_name": task.model_name,
        "request_data": task.request_data,
        "credit_cost": task.credit_cost,
        "result": task.result,
        "status": task.status,
    }
    try:
        session.commit()
    except Exception as error:
        bind = session.get_bind()
        session.rollback()
        session.info["chat_text_closed_for_confirmation"] = True
        session.close()
        check = bind.connect()
        try:
            row = check.execute(
                select(
                    AITaskRow.id,
                    AITaskRow.user_id,
                    AITaskRow.type,
                    AITaskRow.status,
                    AITaskRow.message_id,
                    AITaskRow.credit_cost,
                    AITaskRow.model_name,
                    AITaskRow.request_data,
                    AITaskRow.result,
                ).where(AITaskRow.id == expected["id"])
            ).first()
        finally:
            check.close()
        if row is None or (
            row.user_id != expected["user_id"]
            or row.type != expected["type"]
            or row.status != "queued"
            or row.message_id != expected["message_id"]
            or row.credit_cost != 0
            or expected["credit_cost"] != 0
            or row.model_name != expected["model_name"]
            or row.request_data != expected["request_data"]
            or row.result is not None
            or expected["result"] is not None
            or expected["status"] != "queued"
        ):
            raise ChatTextGenerationError(503, "任务提交结果暂时无法确认") from error


def mark_stream_started(
    session_factory: SessionFactory | None,
    prepared: PreparedStream,
    *,
    clock: Callable[[], datetime] | None,
) -> bool:
    if session_factory is None:
        return False
    now = _now(clock)
    session = session_factory()
    try:
        _begin_write(session)
        proof = session.get(StreamFeeRecordRow, prepared.attempt_id)
        if proof is None or not _proof_matches_prepared(proof, prepared):
            return False
        if proof.start_state == "start_exposed":
            session.rollback()
            session.close()
            session = None
            return _stream_started(session_factory, prepared)
        if proof.start_state != "ready_to_start" or proof.settlement_state != "committed":
            return False
        changed = session.execute(
            update(StreamFeeRecordRow)
            .where(
                StreamFeeRecordRow.server_attempt_id == prepared.attempt_id,
                StreamFeeRecordRow.owner_id == prepared.actor_id,
                StreamFeeRecordRow.start_state == "ready_to_start",
                StreamFeeRecordRow.settlement_state == "committed",
            )
            .values(start_state="start_exposed", updated_at=now)
        ).rowcount
        if changed != 1:
            session.rollback()
            return False
        commit_error: Exception | None = None
        try:
            session.commit()
        except Exception as error:
            commit_error = error
            session.rollback()
        finally:
            session.close()
            session = None
        confirmed = _stream_started(session_factory, prepared)
        if not confirmed and commit_error is not None:
            raise ChatTextGenerationError(
                503, "聊天任务启动结果暂时无法确认"
            ) from commit_error
        return confirmed
    finally:
        if session is not None:
            session.close()


def _stream_started(session_factory: SessionFactory, prepared: PreparedStream) -> bool:
    session = session_factory()
    try:
        proof = session.get(StreamFeeRecordRow, prepared.attempt_id)
        if (
            proof is None
            or not _proof_matches_prepared(proof, prepared)
            or proof.start_state != "start_exposed"
            or proof.settlement_state != "committed"
        ):
            return False
        if prepared.task_id is None:
            return proof.task_id is None
        task = session.get(AITaskRow, prepared.task_id)
        return bool(
            task is not None
            and task.user_id == prepared.actor_id
            and task.message_id == prepared.message_id
            and task.type == prepared.task_type
            and task.status == "processing"
            and task.execution_generation == prepared.task_execution_generation
            and task.claim_token == prepared.task_claim_token
            and task.result is None
            and proof.task_id == prepared.task_id
        )
    finally:
        session.close()


def settle_stream(
    session_factory: SessionFactory | None,
    prepared: PreparedStream,
    *,
    succeeded: bool,
    result: str,
    clock: Callable[[], datetime] | None,
) -> bool:
    if session_factory is None:
        return False
    now = _now(clock)
    session = session_factory()
    try:
        _begin_write(session)
        proof = session.execute(
            select(StreamFeeRecordRow)
            .where(
                StreamFeeRecordRow.server_attempt_id == prepared.attempt_id,
                StreamFeeRecordRow.owner_id == prepared.actor_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if proof is None or not _proof_matches_prepared(proof, prepared):
            return False
        next_state = _settlement_state(prepared, succeeded)
        if proof.settlement_state != "committed":
            session.rollback()
            session.close()
            session = None
            return _settlement_confirmed(
                session_factory,
                prepared,
                next_state,
                succeeded=succeeded,
                result=result,
            )

        debit = _verified_usage_log(session, proof, prepared)
        if proof.actual_debited_amount and debit is None:
            raise ChatTextGenerationError(503, "费用流水暂时无法核实")
        if not proof.actual_debited_amount and _usage_log_exists(session, prepared.attempt_id):
            raise ChatTextGenerationError(503, "费用流水暂时无法核实")

        refund_key = "stream:refund:" + prepared.attempt_id
        existing_refund = session.execute(
            select(CreditLogRow).where(CreditLogRow.business_key == refund_key)
        ).scalar_one_or_none()
        if existing_refund is not None:
            raise ChatTextGenerationError(503, "退款流水暂时无法核实")

        if prepared.message_id:
            message_chapter = session.execute(
                select(chat_messages.c.chapter_id).where(
                    chat_messages.c.id == prepared.message_id
                )
            ).scalar_one_or_none()
            if message_chapter != prepared.message_chapter_id:
                session.rollback()
                return False

        if prepared.task_id:
            task = session.execute(
                select(AITaskRow).where(
                    AITaskRow.id == prepared.task_id,
                    AITaskRow.user_id == prepared.actor_id,
                    AITaskRow.message_id == prepared.message_id,
                    AITaskRow.type == prepared.task_type,
                    AITaskRow.status == "processing",
                    AITaskRow.execution_generation == prepared.task_execution_generation,
                    AITaskRow.result.is_(None),
                    AITaskRow.claim_token.is_(None)
                    if prepared.task_claim_token is None
                    else AITaskRow.claim_token == prepared.task_claim_token,
                )
            ).scalar_one_or_none()
            if task is None or proof.task_id != prepared.task_id:
                session.rollback()
                return False
            task_changed = session.execute(
                update(AITaskRow)
                .where(
                    AITaskRow.id == prepared.task_id,
                    AITaskRow.user_id == prepared.actor_id,
                    AITaskRow.message_id == prepared.message_id,
                    AITaskRow.type == prepared.task_type,
                    AITaskRow.status == "processing",
                    AITaskRow.execution_generation == prepared.task_execution_generation,
                    AITaskRow.result.is_(None),
                    AITaskRow.claim_token.is_(None)
                    if prepared.task_claim_token is None
                    else AITaskRow.claim_token == prepared.task_claim_token,
                )
                .values(
                    status="completed" if succeeded else "failed",
                    result=result,
                    progress=100,
                    progress_message=None,
                    updated_at=now,
                )
            ).rowcount
            if task_changed != 1:
                session.rollback()
                return False
        elif proof.task_id is not None:
            session.rollback()
            return False

        if next_state == "refunded":
            assert debit is not None
            updated_balance = session.execute(
                update(user_credits)
                .where(user_credits.c.user_id == prepared.actor_id)
                .values(credits=user_credits.c.credits + proof.actual_debited_amount)
                .returning(user_credits.c.credits)
            ).scalar_one_or_none()
            if updated_balance is None:
                raise ChatTextGenerationError(503, "积分账户暂时无法核实")
            session.execute(
                insert(CreditLogRow.__table__).values(
                    id=str(uuid4()),
                    user_id=prepared.actor_id,
                    amount=proof.actual_debited_amount,
                    balance_after=updated_balance,
                    type="refund",
                    description=f"任务失败退回 {proof.actual_debited_amount} 积分",
                    task_id=prepared.task_id,
                    business_key=refund_key,
                    billing_unit_id=None,
                    related_debit_id=debit.id,
                    created_at=now,
                )
            )

        if prepared.message_id and prepared.message_chapter_id:
            message_changed = session.execute(
                update(chat_messages)
                .where(
                    chat_messages.c.id == prepared.message_id,
                    chat_messages.c.chapter_id == prepared.message_chapter_id,
                )
                .values(content=_message_result(result))
            ).rowcount
            if message_changed != 1:
                session.rollback()
                return False
        changed = session.execute(
            update(StreamFeeRecordRow)
            .where(
                StreamFeeRecordRow.server_attempt_id == prepared.attempt_id,
                StreamFeeRecordRow.owner_id == prepared.actor_id,
                StreamFeeRecordRow.settlement_state == "committed",
                StreamFeeRecordRow.task_id.is_(None)
                if prepared.task_id is None
                else StreamFeeRecordRow.task_id == prepared.task_id,
                StreamFeeRecordRow.message_id.is_(None)
                if prepared.message_id is None
                else StreamFeeRecordRow.message_id == prepared.message_id,
            )
            .values(settlement_state=next_state, updated_at=now)
        ).rowcount
        if changed != 1:
            session.rollback()
            return False
        commit_error: Exception | None = None
        try:
            session.commit()
        except Exception as error:
            commit_error = error
            session.rollback()
            session.close()
            session = None
        else:
            session.close()
            session = None
        confirmed = _settlement_confirmed(
            session_factory,
            prepared,
            next_state,
            succeeded=succeeded,
            result=result,
        )
        if not confirmed and commit_error is not None:
            raise ChatTextGenerationError(
                503, "任务结果暂时无法确认"
            ) from commit_error
        return confirmed
    finally:
        if session is not None:
            session.close()


def _settlement_confirmed(
    session_factory: SessionFactory,
    prepared: PreparedStream,
    expected_state: str,
    *,
    succeeded: bool,
    result: str,
) -> bool:
    session = session_factory()
    try:
        proof = session.get(StreamFeeRecordRow, prepared.attempt_id)
        if (
            proof is None
            or not _proof_matches_prepared(proof, prepared)
            or proof.settlement_state != expected_state
        ):
            return False
        debit = _verified_usage_log(session, proof, prepared)
        if (proof.actual_debited_amount > 0) != (debit is not None):
            return False
        if proof.actual_debited_amount == 0 and _usage_log_exists(
            session, prepared.attempt_id
        ):
            return False

        refund = session.execute(
            select(CreditLogRow).where(
                CreditLogRow.business_key == "stream:refund:" + prepared.attempt_id
            )
        ).scalar_one_or_none()
        should_refund = expected_state == "refunded"
        if should_refund:
            if debit is None or refund is None or (
                refund.user_id != prepared.actor_id
                or refund.amount != proof.actual_debited_amount
                or refund.type != "refund"
                or refund.task_id != prepared.task_id
                or refund.related_debit_id != debit.id
                or refund.description != f"任务失败退回 {proof.actual_debited_amount} 积分"
                or refund.business_key != "stream:refund:" + prepared.attempt_id
            ):
                return False
        elif refund is not None:
            return False

        if prepared.task_id:
            task = session.get(AITaskRow, prepared.task_id)
            if task is None or (
                task.user_id != prepared.actor_id
                or task.message_id != prepared.message_id
                or task.type != prepared.task_type
                or task.status != ("completed" if succeeded else "failed")
                or task.execution_generation != prepared.task_execution_generation
                or task.claim_token != prepared.task_claim_token
                or task.result != result
                or task.credit_cost != proof.quoted_amount
                or task.model_name != prepared.model.name
            ):
                return False
        elif proof.task_id is not None:
            return False

        if prepared.message_id and prepared.message_chapter_id:
            message = session.execute(
                select(chat_messages.c.content).where(
                    chat_messages.c.id == prepared.message_id,
                    chat_messages.c.chapter_id == prepared.message_chapter_id,
                )
            ).scalar_one_or_none()
            if message != _message_result(result):
                return False
        return True
    finally:
        session.close()


def _proof_matches_prepared(proof: StreamFeeRecordRow, prepared: PreparedStream) -> bool:
    return bool(
        proof.server_attempt_id == prepared.attempt_id
        and proof.owner_id == prepared.actor_id
        and proof.operation == "text.stream"
        and proof.frozen_model_provenance
        == stream_provenance(prepared.model, prepared.actor_id)
        and proof.quoted_amount == prepared.quoted_amount
        and proof.disposition == prepared.disposition
        and proof.actual_debited_amount == prepared.actual_debited_amount
        and proof.original_debit_log_id == prepared.original_debit_log_id
        and proof.original_business_key == prepared.original_business_key
        and proof.task_id == prepared.task_id
        and proof.message_id == prepared.message_id
    )


def _verified_usage_log(
    session: Session,
    proof: StreamFeeRecordRow,
    prepared: PreparedStream,
) -> CreditLogRow | None:
    if proof.actual_debited_amount <= 0:
        return None
    if (
        proof.disposition != "debit"
        or proof.actual_debited_amount != proof.quoted_amount
        or proof.original_debit_log_id is None
        or proof.original_business_key != "stream:debit:" + prepared.attempt_id
    ):
        return None
    debit = session.execute(
        select(CreditLogRow).where(
            CreditLogRow.id == proof.original_debit_log_id,
            CreditLogRow.business_key == proof.original_business_key,
            CreditLogRow.user_id == prepared.actor_id,
            CreditLogRow.type == "usage",
            CreditLogRow.amount == -proof.actual_debited_amount,
            CreditLogRow.task_id.is_(None),
            CreditLogRow.related_debit_id.is_(None),
        )
    ).scalar_one_or_none()
    return debit


def _usage_log_exists(session: Session, attempt_id: str) -> bool:
    return (
        session.execute(
            select(CreditLogRow.id).where(
                CreditLogRow.business_key == "stream:debit:" + attempt_id
            )
        ).scalar_one_or_none()
        is not None
    )


def _settlement_state(prepared: PreparedStream, succeeded: bool) -> str:
    if succeeded:
        return "success_retained"
    return "refunded" if prepared.actual_debited_amount > 0 else "no_debit_terminal"


def _message_result(result: str) -> str:
    return result if result else "处理失败: 未知错误"
