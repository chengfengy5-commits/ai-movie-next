"""Same-Session application of authorized graphless text stream output."""

from __future__ import annotations

import hashlib

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ApplicationOutcome, TextResult, WebStreamAuthority
from haoai_backend.generation_core.base_schema import AITaskRow, CreditLogRow
from haoai_backend.generation_core.schema import StreamFeeRecordRow
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader

from .tables import chapters, chat_messages


def apply_web_text(
    shared_session: Session,
    authority: WebStreamAuthority,
    text_result: TextResult,
) -> ApplicationOutcome:
    """Stage the assistant message update without owning the request transaction."""

    if not isinstance(shared_session, Session):
        raise TypeError("shared_session must be the caller-owned SQLAlchemy Session")
    actor_id = authority.actor.user_id
    attempt_id = authority.fee_authority.server_attempt_id
    receipt = authority.confirmed_receipt.receipt
    capability = authority.start_capability.capability
    if (
        not isinstance(text_result, TextResult)
        or actor_id != authority.actor.trusted_account_identity.user_id
        or authority.fee_authority.actor.user_id != actor_id
        or authority.fee_authority.actor.trusted_account_identity.user_id != actor_id
        or authority.fee_authority.operation != "text.stream"
        or authority.fee_authority.server_attempt_id != attempt_id
        or authority.fee_authority.frozen_quote != receipt.quoted_amount
        or authority.fee_authority.disposition != receipt.disposition
        or receipt.actor_id != actor_id
        or receipt.operation != "text.stream"
        or receipt.server_attempt_id != attempt_id
        or receipt.commit_state != "committed"
        or capability.attempt_id != attempt_id
        or authority.task_id is None
        or authority.message_id is None
        or receipt.task_id != authority.task_id
        or receipt.message_id != authority.message_id
        or authority.cancellation_signal.is_set()
    ):
        return ApplicationOutcome("write_failed", attempt_id, None, None, "text stream authority is incomplete or no longer active")

    stream = shared_session.execute(
        select(StreamFeeRecordRow).where(
            StreamFeeRecordRow.server_attempt_id == attempt_id,
            StreamFeeRecordRow.owner_id == actor_id,
            StreamFeeRecordRow.operation == "text.stream",
        ).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        stream is None
        or stream.start_state != "start_exposed"
        or stream.start_token != capability.token
        or stream.task_id != authority.task_id
        or stream.message_id != authority.message_id
        or stream.quoted_amount != receipt.quoted_amount
        or stream.disposition != receipt.disposition
        or stream.actual_debited_amount != receipt.actual_debited_amount
        or stream.original_debit_log_id != receipt.original_debit_log_id
        or stream.original_business_key != receipt.original_business_key
        or stream.settlement_state not in {"committed", "success_retained"}
    ):
        return ApplicationOutcome("write_failed", attempt_id, None, None, "original stream start is not confirmed")
    if stream.actual_debited_amount == 0:
        if (
            stream.disposition == "debit"
            or stream.original_debit_log_id is not None
            or stream.original_business_key is not None
        ):
            return ApplicationOutcome("write_failed", attempt_id, None, None, "zero-debit stream has an invalid ledger link")
    else:
        if stream.disposition != "debit":
            return ApplicationOutcome("write_failed", attempt_id, None, None, "non-debit stream claims a debit amount")
        debit = shared_session.execute(
            select(CreditLogRow)
            .where(CreditLogRow.id == stream.original_debit_log_id)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            debit is None or debit.user_id != actor_id or debit.type != "usage"
            or debit.amount != -stream.actual_debited_amount
            or debit.business_key != stream.original_business_key
            or debit.task_id is not None or debit.billing_unit_id is not None
        ):
            return ApplicationOutcome("write_failed", attempt_id, None, None, "original stream debit is not owner-bound")

    task = shared_session.execute(
        select(AITaskRow).where(AITaskRow.id == authority.task_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        task is None or task.user_id != actor_id or task.message_id != authority.message_id
        or task.status != "processing"
    ):
        return ApplicationOutcome("write_failed", attempt_id, None, None, "stream task and message binding do not match")
    message = shared_session.execute(
        select(chat_messages).where(chat_messages.c.id == authority.message_id).with_for_update()
    ).mappings().one_or_none()
    if message is None or message["role"] != "assistant":
        return ApplicationOutcome("target_deleted", attempt_id, None, None, "assistant message is unavailable")
    chapter = shared_session.execute(
        select(chapters).where(chapters.c.id == message["chapter_id"]).with_for_update()
    ).mappings().one_or_none()
    if chapter is None:
        return ApplicationOutcome("target_deleted", attempt_id, None, None, "message chapter is unavailable")
    try:
        require_series_access(
            SqlAlchemySeriesAccessReader(shared_session),
            ActorIdentity(actor_id),
            chapter["series_id"],
        )
    except (SeriesAccessDenied, SeriesNotFound):
        return ApplicationOutcome("write_failed", attempt_id, None, None, "current series access is unavailable")

    if authority.cancellation_signal.is_set():
        return ApplicationOutcome("write_failed", attempt_id, None, None, "stream was cancelled before message application")

    content = text_result.text
    if not isinstance(content, str):
        return ApplicationOutcome("write_failed", attempt_id, None, None, "validated text output is invalid")
    if message["content"] != content:
        changed = shared_session.execute(
            update(chat_messages)
            .where(chat_messages.c.id == authority.message_id, chat_messages.c.chapter_id == chapter["id"])
            .values(content=content)
        )
        if changed.rowcount != 1:
            return ApplicationOutcome("stale", attempt_id, None, None, "assistant message changed during application")
    return ApplicationOutcome(
        "applied",
        attempt_id,
        None,
        hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


__all__ = ("apply_web_text",)
