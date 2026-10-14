"""SQLAlchemy Core unit of work for fused-image generation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import user_credits
from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_catalog.tables import (
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.generation_core.base_schema import AITaskRow, CreditLogRow
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data.tables import chapters, characters, props
from haoai_backend.series_exchange.tables import fused_assets
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    FusedPromptText,
    FusedServerImageModel,
    FusedTaskClaim,
    PreparedFusedGeneration,
)
from .errors import (
    FusedGenerationForbidden,
    FusedGenerationModelNotFound,
    FusedGenerationNotFound,
    FusedGenerationPaymentRequired,
    FusedGenerationUnavailable,
)

_LEGACY_FERNET_PREFIX = "gAAAAA"
_user_credits = user_credits


class SqlAlchemyFusedGenerationUnitOfWork:
    """One short-lived Session for preparation, claim, or finalization."""

    def __init__(
        self,
        session: Session,
        *,
        decrypt_api_key: Callable[[str], str] | None = None,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._session = session
        self._decrypt_api_key = decrypt_api_key or (lambda value: value)
        self._now = now or (lambda: datetime.now(timezone.utc).replace(tzinfo=None))
        self._new_id = new_id or (lambda: str(uuid4()))

    def current_time(self) -> datetime:
        return self._now()

    def new_id(self) -> str:
        return self._new_id()

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        try:
            require_series_access(
                SqlAlchemySeriesAccessReader(self._session),
                ActorIdentity(actor.user_id),
                series_id,
            )
        except SeriesNotFound as exc:
            raise FusedGenerationNotFound("剧集不存在") from exc
        except SeriesAccessDenied as exc:
            raise FusedGenerationForbidden(exc.detail) from exc

    def load_member_asset(
        self, member_type: str, series_id: str, asset_id: str
    ) -> Mapping[str, object] | None:
        table = characters if member_type == "character" else props
        row = self._session.execute(
            select(table.c.id, table.c.series_id, table.c.name, table.c.image_url).where(
                table.c.id == asset_id,
                table.c.series_id == series_id,
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def resolve_and_charge_model(
        self, user_id: str, model_id: str
    ) -> FusedServerImageModel:
        system_row = self._session.execute(
            select(
                system_models.c.id,
                system_models.c.name,
                system_models.c.api_key,
                system_models.c.provider,
                system_models.c.model,
                system_models.c.base_url,
                system_models.c.params,
                system_models.c.cost_per_call,
            )
            .where(
                system_models.c.id == model_id,
                system_models.c.is_active.is_(True),
            )
        ).mappings().one_or_none()
        if system_row is not None:
            model = self._server_model(system_row, is_system=True)
            if model.cost_per_call > 0:
                self._charge_system_model(user_id, model)
            return model

        owned_row = self._session.execute(
            select(
                model_configs.c.id,
                model_configs.c.name,
                model_configs.c.api_key,
                model_configs.c.provider,
                model_configs.c.model,
                model_configs.c.base_url,
                model_configs.c.params,
            ).where(
                model_configs.c.id == model_id,
                model_configs.c.user_id == user_id,
            )
        ).mappings().one_or_none()
        if owned_row is None:
            raise FusedGenerationModelNotFound()
        return self._server_model(owned_row, is_system=False)

    def _server_model(
        self, row: Mapping[str, Any], *, is_system: bool
    ) -> FusedServerImageModel:
        api_key = row.get("api_key")
        if isinstance(api_key, str) and api_key.startswith(_LEGACY_FERNET_PREFIX):
            try:
                api_key = self._decrypt_api_key(api_key)
            except Exception as exc:
                raise FusedGenerationUnavailable("模型凭据不可用，请联系管理员") from exc
        values = (api_key, row.get("provider"))
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise FusedGenerationUnavailable("模型执行配置不完整，请联系管理员")
        wire_model = row.get("model")
        if wire_model is not None and not isinstance(wire_model, str):
            raise FusedGenerationUnavailable("模型执行配置不完整，请联系管理员")
        cost = row.get("cost_per_call", 0) if is_system else 0
        if not isinstance(cost, int) or cost < 0:
            raise FusedGenerationUnavailable("模型费用配置无效，请联系管理员")
        return FusedServerImageModel(
            id=row["id"],
            name=row["name"],
            cost_per_call=cost,
            provider=row["provider"],
            wire_model=wire_model,
            api_key=api_key,
            base_url=row.get("base_url"),
            params=row.get("params"),
            is_system=is_system,
        )

    def _charge_system_model(self, user_id: str, model: FusedServerImageModel) -> None:
        credit_row = self._session.execute(
            select(_user_credits.c.credits).where(_user_credits.c.user_id == user_id)
        ).scalar_one_or_none()
        balance = credit_row if isinstance(credit_row, int) else 0
        if balance < model.cost_per_call:
            raise FusedGenerationPaymentRequired(
                f"积分不足，需要 {model.cost_per_call} 积分，当前剩余 {balance} 积分，请联系管理员充值"
            )
        updated_balance = self._session.execute(
            update(_user_credits)
            .where(
                _user_credits.c.user_id == user_id,
                _user_credits.c.credits >= model.cost_per_call,
            )
            .values(credits=_user_credits.c.credits - model.cost_per_call)
            .returning(_user_credits.c.credits)
        ).scalar_one_or_none()
        if updated_balance is None:
            current_balance = self._session.execute(
                select(_user_credits.c.credits).where(_user_credits.c.user_id == user_id)
            ).scalar_one_or_none()
            raise FusedGenerationPaymentRequired(
                f"积分不足，需要 {model.cost_per_call} 积分，当前剩余 {current_balance or 0} 积分，请联系管理员充值"
            )
        self._session.execute(
            insert(CreditLogRow.__table__).values(
                id=self.new_id(),
                user_id=user_id,
                amount=-model.cost_per_call,
                balance_after=updated_balance,
                type="usage",
                description=f"使用 {model.name} 消耗积分",
                task_id=None,
                created_at=self.current_time(),
            )
        )
        # Legacy behavior commits usage before prompt resolution and task creation.
        self.commit()

    def resolve_image_prompt(self, user_id: str, prompt_id: str) -> FusedPromptText | None:
        system_row = self._session.execute(
            select(system_prompts.c.system_prompt, system_prompts.c.user_prompt).where(
                system_prompts.c.id == prompt_id,
                system_prompts.c.is_active.is_(True),
            )
        ).mappings().one_or_none()
        if system_row is None:
            system_row = self._session.execute(
                select(prompt_configs.c.system_prompt, prompt_configs.c.user_prompt).where(
                    prompt_configs.c.id == prompt_id,
                    prompt_configs.c.user_id == user_id,
                )
            ).mappings().one_or_none()
        if system_row is None:
            return None
        return FusedPromptText(
            system_prompt=system_row["system_prompt"] or "",
            user_prompt=system_row["user_prompt"] or "",
        )

    def load_chapter(self, chapter_id: str) -> Mapping[str, object] | None:
        row = self._session.execute(
            select(chapters.c.id, chapters.c.series_id, chapters.c.content).where(
                chapters.c.id == chapter_id
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def stage_request(self, prepared: PreparedFusedGeneration, user_id: str) -> None:
        now = self.current_time()
        if prepared.message_id is not None:
            chapter_id = prepared.chapter_id
            if chapter_id is None:
                raise ValueError("a fused placeholder message requires a validated chapter")
            self._session.execute(
                insert(chat_messages).values(
                    id=prepared.message_id,
                    chapter_id=chapter_id,
                    frame_index=(
                        prepared.target_frame.frame_index if prepared.target_frame else None
                    ),
                    asset_type=None,
                    asset_id=None,
                    chat_mode="image",
                    role="assistant",
                    content="正在生成融合图...",
                    model_name=prepared.model_name,
                    created_at=now,
                )
            )

        request_data = {
            "member_type": prepared.member_type,
            "member_count": len(prepared.members),
            "size": prepared.size,
            "has_reference": bool(prepared.reference_images),
            "model_id": prepared.server_model.id,
        }
        self._session.execute(
            insert(AITaskRow.__table__).values(
                id=prepared.task_id,
                user_id=user_id,
                type="fused",
                status="processing",
                message_id=prepared.message_id or "",
                credit_cost=prepared.credit_cost,
                model_name=prepared.server_model.name,
                request_data=json.dumps(request_data, ensure_ascii=False),
                billing_status="legacy_unreconciled",
                recovery_status="ready",
                progress=0,
                progress_message="正在生成融合图...",
                created_at=now,
                updated_at=now,
            )
        )

    def claim_task(self, task_id: str, claim_token: str) -> FusedTaskClaim | None:
        result = self._session.execute(
            update(AITaskRow.__table__)
            .where(
                AITaskRow.id == task_id,
                AITaskRow.status == "processing",
                AITaskRow.claim_token.is_(None),
            )
            .values(
                claim_token=claim_token,
                execution_generation=AITaskRow.execution_generation + 1,
                updated_at=self.current_time(),
            )
        )
        if result.rowcount != 1:
            return None
        cancelled = self._session.execute(
            select(AITaskRow.user_cancelled_at).where(AITaskRow.id == task_id)
        ).scalar_one_or_none()
        return FusedTaskClaim(cancelled=cancelled is not None)

    def finish_task(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        *,
        image_url: str | None,
        error_message: str | None,
    ) -> None:
        task_table = AITaskRow.__table__
        task = self._session.execute(
            select(
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.user_cancelled_at,
                task_table.c.message_id,
            ).where(task_table.c.id == prepared.task_id)
        ).mappings().one_or_none()
        if task is None or task["status"] != "processing" or task["claim_token"] != claim_token:
            return

        cancelled = task["user_cancelled_at"] is not None
        final_error = "任务被取消" if cancelled else error_message
        final_url = None if final_error else image_url
        status = "completed" if final_url else "failed"
        now = self.current_time()
        changed = self._mark_terminal(
            task_table,
            prepared.task_id,
            claim_token,
            status=status,
            result=final_url or final_error or "融合生成失败",
            progress_message=(
                "融合图已生成" if status == "completed" else (final_error or "融合生成失败")[:2000]
            ),
            updated_at=now,
            cancelled=cancelled,
        )
        if changed != 1 and not cancelled:
            # A cancellation may have committed after the read above. Convert
            # that race to a failure rather than allowing stale success through.
            latest = self._session.execute(
                select(
                    task_table.c.status,
                    task_table.c.claim_token,
                    task_table.c.user_cancelled_at,
                ).where(task_table.c.id == prepared.task_id)
            ).mappings().one_or_none()
            if (
                latest is not None
                and latest["status"] == "processing"
                and latest["claim_token"] == claim_token
                and latest["user_cancelled_at"] is not None
            ):
                final_error = "任务被取消"
                final_url = None
                status = "failed"
                changed = self._mark_terminal(
                    task_table,
                    prepared.task_id,
                    claim_token,
                    status="failed",
                    result=final_error,
                    progress_message=final_error,
                    updated_at=now,
                    cancelled=True,
                )
        if changed != 1:
            return

        fused_id = self.new_id()
        self._session.execute(
            insert(fused_assets).values(
                id=fused_id,
                series_id=prepared.series_id,
                member_type=prepared.member_type,
                members=json.dumps(prepared.members, ensure_ascii=False),
                name=prepared.name,
                image_url=final_url,
                size=prepared.size,
                status=status,
                error_message=(final_error or None)[:2000] if final_error else None,
                created_at=now,
                updated_at=now,
            )
        )
        attached = self._append_to_target_frame(prepared, fused_id)

        if status == "failed" and prepared.credit_cost > 0 and prepared.server_model.is_system:
            balance_after = self._session.execute(
                update(_user_credits)
                .where(_user_credits.c.user_id == prepared.user_id)
                .values(credits=_user_credits.c.credits + prepared.credit_cost)
                .returning(_user_credits.c.credits)
            ).scalar_one_or_none()
            if balance_after is not None:
                self._session.execute(
                    insert(CreditLogRow.__table__).values(
                        id=self.new_id(),
                        user_id=prepared.user_id,
                        amount=prepared.credit_cost,
                        balance_after=balance_after,
                        type="refund",
                        description=f"任务失败退回 {prepared.credit_cost} 积分",
                        task_id=prepared.task_id,
                        business_key=f"fused-refund:{prepared.task_id}",
                        created_at=now,
                    )
                )
        message_id = task["message_id"]
        if message_id:
            message_content = final_url if status == "completed" else (final_error or "融合生成失败")[:2000]
            if status == "completed" and not attached and prepared.target_frame is not None:
                message_content = f"{final_url}\n融合资产已保存，但目标分镜已变化，请从融合资产列表中查看。"
            self._session.execute(
                update(chat_messages)
                .where(chat_messages.c.id == message_id)
                .values(content=message_content)
            )

    def _append_to_target_frame(
        self, prepared: PreparedFusedGeneration, fused_id: str
    ) -> bool:
        target = prepared.target_frame
        if target is None:
            return False
        row = self.load_chapter(target.chapter_id)
        if row is None or row["series_id"] != prepared.series_id:
            return False
        original_content = row["content"]
        frames = _chapter_frames(original_content)
        matches = [
            frame
            for frame in frames
            if isinstance(frame, dict) and _frame_identity(frame) == target.identity
        ]
        if len(matches) != 1:
            return False
        frame = matches[0]
        attached = frame.get("fused")
        if not isinstance(attached, list):
            attached = []
            frame["fused"] = attached
        if fused_id not in attached:
            attached.append(fused_id)
        statement = update(chapters).where(
            chapters.c.id == target.chapter_id,
            chapters.c.series_id == prepared.series_id,
        )
        statement = (
            statement.where(chapters.c.content.is_(None))
            if original_content is None
            else statement.where(chapters.c.content == original_content)
        )
        result = self._session.execute(
            statement.values(content=json.dumps(frames, ensure_ascii=False))
        )
        return result.rowcount == 1

    def _mark_terminal(
        self,
        task_table: Any,
        task_id: str,
        claim_token: str,
        *,
        status: str,
        result: str,
        progress_message: str,
        updated_at: datetime,
        cancelled: bool,
    ) -> int:
        cancellation_predicate = (
            task_table.c.user_cancelled_at.is_not(None)
            if cancelled
            else task_table.c.user_cancelled_at.is_(None)
        )
        return self._session.execute(
            update(task_table)
            .where(
                task_table.c.id == task_id,
                task_table.c.status == "processing",
                task_table.c.claim_token == claim_token,
                cancellation_predicate,
            )
            .values(
                status=status,
                result=result,
                progress=100,
                progress_message=progress_message,
                claim_token=None,
                updated_at=updated_at,
            )
        ).rowcount

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def fused_generation_uow_factory(
    session_factory: Callable[[], Session],
    *,
    decrypt_api_key: Callable[[str], str] | None = None,
):
    def make_uow() -> SqlAlchemyFusedGenerationUnitOfWork:
        return SqlAlchemyFusedGenerationUnitOfWork(
            session_factory(),
            decrypt_api_key=decrypt_api_key,
        )

    return make_uow


def _chapter_frames(content: object) -> list[object]:
    if isinstance(content, list):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _frame_identity(frame: Mapping[str, object]) -> str:
    stable_frame = {key: value for key, value in frame.items() if key != "fused"}
    return json.dumps(stable_frame, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
