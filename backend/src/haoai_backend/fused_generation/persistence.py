"""SQLAlchemy Core unit of work for fused-image generation."""

from __future__ import annotations

import json
import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import and_, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import user_credits
from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_catalog.tables import (
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    CreditLogRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_core.schema import (
    FusedGenerationSubmissionRow,
    FusedMediaWriteRow,
)
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.binding_capture_ports import (
    CapturedCredentialBinding,
)
from haoai_backend.generation_credentials.domain import CredentialScope
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data.tables import chapters, characters, props
from haoai_backend.series_exchange.tables import fused_assets
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    FUSED_DELIVERY_PENDING_PROGRESS_MESSAGE,
    FUSED_MEDIA_STORAGE_UNKNOWN_PROGRESS_MESSAGE,
    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
    FUSED_UNKNOWN_PROGRESS_MESSAGE,
    FUSED_UNKNOWN_REASON,
    FusedGenerationUnknownReason,
    FUSED_UNKNOWN_REASONS,
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
from .ownership import FUSED_SERVER_CONTROL_MARKER, is_server_controlled_fused_task

_LEGACY_FERNET_PREFIX = "gAAAAA"
_user_credits = user_credits
_fused_submissions = FusedGenerationSubmissionRow.__table__
_fused_media_writes = FusedMediaWriteRow.__table__
_task_submissions = TaskSubmissionRow.__table__


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

    def resolve_durable_model_identity(
        self,
        user_id: str,
        model_id: str,
    ) -> tuple[FusedServerImageModel, CredentialScope]:
        """Read selected model identity without selecting legacy API-key material."""

        system_row = self._session.execute(
            select(
                system_models.c.id,
                system_models.c.name,
                system_models.c.provider,
                system_models.c.model,
                system_models.c.base_url,
                system_models.c.params,
                system_models.c.cost_per_call,
            ).where(system_models.c.id == model_id, system_models.c.is_active.is_(True))
        ).mappings().one_or_none()
        if system_row is not None:
            model = self._identity_model(system_row, is_system=True)
            return model, CredentialScope("system", model.id, "system", model.provider)

        owned_row = self._session.execute(
            select(
                model_configs.c.id,
                model_configs.c.name,
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
        model = self._identity_model(owned_row, is_system=False)
        return model, CredentialScope("user", model.id, user_id, model.provider)

    def _identity_model(
        self,
        row: Mapping[str, Any],
        *,
        is_system: bool,
    ) -> FusedServerImageModel:
        provider = row.get("provider")
        if type(provider) is not str or not provider.strip():
            raise FusedGenerationUnavailable("模型执行配置不完整，请联系管理员")
        wire_model = row.get("model")
        if wire_model is not None and type(wire_model) is not str:
            raise FusedGenerationUnavailable("模型执行配置不完整，请联系管理员")
        base_url = row.get("base_url")
        params = row.get("params")
        if base_url is not None and type(base_url) is not str:
            raise FusedGenerationUnavailable("模型执行配置不完整，请联系管理员")
        if params is not None and type(params) is not str:
            raise FusedGenerationUnavailable("模型执行配置不完整，请联系管理员")
        cost = row.get("cost_per_call", 0) if is_system else 0
        if type(cost) is not int or cost < 0:
            raise FusedGenerationUnavailable("模型费用配置无效，请联系管理员")
        name = row.get("name")
        model_id = row.get("id")
        if type(name) is not str or type(model_id) is not str:
            raise FusedGenerationUnavailable("模型执行配置不完整，请联系管理员")
        return FusedServerImageModel(
            id=model_id,
            name=name,
            cost_per_call=cost,
            provider=provider,
            wire_model=wire_model,
            api_key="",
            base_url=base_url,
            params=params,
            is_system=is_system,
        )

    def capture_durable_binding(
        self,
        registry: CredentialRegistry,
        scope: CredentialScope,
    ) -> CapturedCredentialBinding | None:
        return registry.capture_original_binding(self._session, scope)

    def find_durable_submission(
        self,
        owner_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object] | None:
        row = self._session.execute(
            select(_fused_submissions).where(
                _fused_submissions.c.owner_id == owner_id,
                _fused_submissions.c.operation == "fused.image",
                _fused_submissions.c.idempotency_key == idempotency_key,
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def find_durable_submission_receipt(
        self,
        owner_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object] | None:
        row = self._session.execute(
            select(
                _fused_submissions.c.stage,
                _fused_submissions.c.fee_disposition,
                _fused_submissions.c.fee_amount,
                _fused_submissions.c.task_id,
                _fused_submissions.c.message_id,
                _fused_submissions.c.response_status,
                _fused_submissions.c.response_json,
            ).where(
                _fused_submissions.c.owner_id == owner_id,
                _fused_submissions.c.operation == "fused.image",
                _fused_submissions.c.idempotency_key == idempotency_key,
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def durable_submission_by_id(
        self,
        submission_id: str,
        *,
        owner_id: str | None = None,
    ) -> Mapping[str, object] | None:
        statement = select(_fused_submissions).where(
            _fused_submissions.c.submission_id == submission_id
        )
        if owner_id is not None:
            statement = statement.where(_fused_submissions.c.owner_id == owner_id)
        row = self._session.execute(statement).mappings().one_or_none()
        return dict(row) if row is not None else None

    def recoverable_durable_submissions(
        self,
        *,
        limit: int = 32,
    ) -> tuple[Mapping[str, object], ...]:
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("recovery scan limit is invalid")
        rows = self._session.execute(
            select(
                _fused_submissions.c.submission_id,
                _fused_submissions.c.owner_id,
            )
            .where(
                _fused_submissions.c.stage.in_(
                    (
                        "snapshot_ready",
                        "accepted",
                        "dispatching",
                        "result_known",
                        "media_started",
                    )
                )
            )
            .order_by(_fused_submissions.c.updated_at, _fused_submissions.c.submission_id)
            .limit(limit)
        ).mappings().all()
        return tuple(dict(row) for row in rows)

    def reserve_durable_submission(
        self,
        *,
        submission_id: str,
        owner_id: str,
        idempotency_key: str,
        request_digest: str,
        model: FusedServerImageModel,
        scope: CredentialScope,
        captured_binding: CapturedCredentialBinding,
        task_id: str,
        message_id: str | None,
        encrypted_request: str,
        request_nonce: str,
        seal_key_id: str,
        crypto_algorithm: str,
    ) -> None:
        """Atomically reserve the original key and commit its one-time fee."""

        now = self.current_time()
        disposition = (
            "debit" if model.is_system and model.cost_per_call > 0
            else "byok" if not model.is_system
            else "zero"
        )
        fee_amount = model.cost_per_call if disposition == "debit" else 0
        debit_log_id = self.new_id() if fee_amount > 0 else None
        self._session.execute(
            insert(TaskSubmissionRow.__table__).values(
                id=submission_id,
                user_id=owner_id,
                operation="fused.image",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                task_ids=[],
                task_quote_id=None,
                status="fee_committed",
                created_at=now,
            )
        )
        self._session.execute(
            insert(_fused_submissions).values(
                submission_id=submission_id,
                owner_id=owner_id,
                operation="fused.image",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                stage="reserved",
                fee_disposition=disposition,
                fee_amount=fee_amount,
                debit_log_id=debit_log_id,
                task_id=task_id,
                message_id=message_id,
                encrypted_request=encrypted_request,
                request_nonce=request_nonce,
                seal_key_id=seal_key_id,
                crypto_algorithm=crypto_algorithm,
                model_source=scope.source,
                model_row_id=scope.row_id,
                provider=scope.provider,
                principal_id=captured_binding.binding.original_principal_id,
                binding_id=captured_binding.binding_id,
                binding_version=captured_binding.binding.binding_version,
                endpoint_origin=captured_binding.binding.endpoint_origin,
                allowed_content_paths=json.dumps(
                    captured_binding.binding.allowed_content_paths,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                rotation_provenance=captured_binding.binding.rotation_provenance,
                created_at=now,
                updated_at=now,
            )
        )
        if fee_amount > 0:
            updated_balance = self._session.execute(
                update(_user_credits)
                .where(
                    _user_credits.c.user_id == owner_id,
                    _user_credits.c.credits >= fee_amount,
                )
                .values(credits=_user_credits.c.credits - fee_amount)
                .returning(_user_credits.c.credits)
            ).scalar_one_or_none()
            if updated_balance is None:
                current_balance = self._session.execute(
                    select(_user_credits.c.credits).where(
                        _user_credits.c.user_id == owner_id
                    )
                ).scalar_one_or_none()
                raise FusedGenerationPaymentRequired(
                    f"积分不足，需要 {fee_amount} 积分，当前剩余 {current_balance or 0} 积分，请联系管理员充值"
                )
            self._session.execute(
                insert(CreditLogRow.__table__).values(
                    id=debit_log_id,
                    user_id=owner_id,
                    amount=-fee_amount,
                    balance_after=updated_balance,
                    type="usage",
                    description=f"使用 {model.name} 消耗积分",
                    task_id=None,
                    business_key=f"fused-debit:{submission_id}",
                    created_at=now,
                )
            )
        self._session.execute(
            update(_fused_submissions)
            .where(_fused_submissions.c.submission_id == submission_id)
            .values(stage="fee_committed", updated_at=now)
        )

    def save_durable_snapshot(
        self,
        *,
        submission_id: str,
        encrypted_snapshot: str,
        snapshot_nonce: str,
        snapshot_proof_key_id: str,
        snapshot_proof: str,
    ) -> bool:
        result = self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.stage == "fee_committed",
                _fused_submissions.c.encrypted_snapshot.is_(None),
            )
            .values(
                stage="snapshot_ready",
                encrypted_snapshot=encrypted_snapshot,
                snapshot_nonce=snapshot_nonce,
                snapshot_proof_key_id=snapshot_proof_key_id,
                snapshot_proof=snapshot_proof,
                updated_at=self.current_time(),
            )
        )
        return result.rowcount == 1

    def reject_durable_submission(
        self,
        *,
        submission_id: str,
        status_code: int,
        response_json: str,
    ) -> bool:
        result = self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.stage == "fee_committed",
            )
            .values(
                stage="rejected_after_charge",
                response_status=status_code,
                response_json=response_json,
                updated_at=self.current_time(),
            )
        )
        if result.rowcount == 1:
            self._session.execute(
                update(_task_submissions)
                .where(_task_submissions.c.id == submission_id)
                .values(status="rejected_after_charge")
            )
        return result.rowcount == 1

    def accept_durable_submission(
        self,
        prepared: PreparedFusedGeneration,
        *,
        response_status: int = 200,
        response_json: str,
    ) -> bool:
        if prepared.submission_id is None:
            return False
        now = self.current_time()
        if prepared.message_id is not None:
            if prepared.chapter_id is None:
                raise ValueError("a fused placeholder message requires a validated chapter")
            self._session.execute(
                insert(chat_messages).values(
                    id=prepared.message_id,
                    chapter_id=prepared.chapter_id,
                    frame_index=None,
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
            "server_controlled_execution": FUSED_SERVER_CONTROL_MARKER,
            "fused_submission_id": prepared.submission_id,
        }
        self._session.execute(
            insert(AITaskRow.__table__).values(
                id=prepared.task_id,
                user_id=prepared.user_id,
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
        self._session.execute(
            update(_task_submissions)
            .where(_task_submissions.c.id == prepared.submission_id)
            .values(task_ids=[prepared.task_id], status="accepted")
        )
        updated = self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == prepared.submission_id,
                _fused_submissions.c.stage == "snapshot_ready",
                _fused_submissions.c.task_id == prepared.task_id,
            )
            .values(
                stage="accepted",
                response_status=response_status,
                response_json=response_json,
                updated_at=now,
            )
        )
        return updated.rowcount == 1

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
            "server_controlled_execution": FUSED_SERVER_CONTROL_MARKER,
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

    def claim_task(
        self,
        task_id: str,
        claim_token: str,
        *,
        expected_user_id: str,
    ) -> FusedTaskClaim | None:
        task_table = AITaskRow.__table__
        task = self._session.execute(
            select(
                task_table.c.type,
                task_table.c.user_id,
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.request_data,
            )
            .where(task_table.c.id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["type"] != "fused"
            or task["user_id"] != expected_user_id
            or task["status"] != "processing"
            or task["claim_token"] is not None
            or not is_server_controlled_fused_task(task["type"], task["request_data"])
        ):
            return None

        result = self._session.execute(
            update(task_table)
            .where(
                AITaskRow.id == task_id,
                task_table.c.type == "fused",
                task_table.c.user_id == expected_user_id,
                AITaskRow.status == "processing",
                AITaskRow.claim_token.is_(None),
                task_table.c.request_data == task["request_data"],
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
            select(AITaskRow.user_cancelled_at).where(
                AITaskRow.id == task_id,
                task_table.c.user_id == expected_user_id,
                task_table.c.claim_token == claim_token,
            )
        ).scalar_one_or_none()
        return FusedTaskClaim(cancelled=cancelled is not None)

    def claim_durable_task(
        self,
        task_id: str,
        submission_id: str,
        claim_token: str,
        *,
        expected_user_id: str,
    ) -> FusedTaskClaim | None:
        task_table = AITaskRow.__table__
        now = self.current_time()
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.owner_id == expected_user_id,
                _fused_submissions.c.stage.in_(("accepted", "result_known")),
            )
            .with_for_update()
        ).mappings().one_or_none()
        task = self._session.execute(
            select(
                task_table.c.type,
                task_table.c.user_id,
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.lease_until,
                task_table.c.execution_generation,
                task_table.c.request_data,
                task_table.c.recovery_status,
                task_table.c.user_cancelled_at,
            )
            .where(task_table.c.id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            ledger is None
            or task is None
            or task["type"] != "fused"
            or task["user_id"] != expected_user_id
            or task["status"] != "processing"
            or task["recovery_status"] != "ready"
            or not is_server_controlled_fused_task(task["type"], task["request_data"])
            or (ledger["stage"] == "accepted" and ledger["dispatch_nonce"] is not None)
            or (
                task["claim_token"] is not None
                and (
                    task["lease_until"] is None
                    or task["lease_until"] >= now
                )
            )
        ):
            return None
        generation = task["execution_generation"] + 1
        expires = now + timedelta(minutes=10)
        updated = self._session.execute(
            update(task_table)
            .where(
                task_table.c.id == task_id,
                task_table.c.type == "fused",
                task_table.c.user_id == expected_user_id,
                task_table.c.status == "processing",
                task_table.c.claim_token == task["claim_token"]
                if task["claim_token"] is not None
                else task_table.c.claim_token.is_(None),
                task_table.c.execution_generation == task["execution_generation"],
                task_table.c.request_data == task["request_data"],
            )
            .values(
                claim_token=claim_token,
                execution_generation=generation,
                lease_until=expires,
                updated_at=now,
            )
        )
        if updated.rowcount != 1:
            return None
        return FusedTaskClaim(
            cancelled=task["user_cancelled_at"] is not None,
            submission_id=submission_id,
            generation=generation,
            stage=ledger["stage"],
        )

    def persist_dispatch_gate(
        self,
        *,
        task_id: str,
        submission_id: str,
        user_id: str,
        claim_token: str,
        generation: int,
        dispatch_nonce: str,
        wire_digest: str,
    ) -> bool:
        task_table = AITaskRow.__table__
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.owner_id == user_id,
                _fused_submissions.c.stage == "accepted",
                _fused_submissions.c.dispatch_nonce.is_(None),
            )
            .with_for_update()
        ).mappings().one_or_none()
        if ledger is None:
            return False
        task = self._session.execute(
            select(
                task_table.c.type,
                task_table.c.user_id,
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.execution_generation,
                task_table.c.request_data,
                task_table.c.user_cancelled_at,
            )
            .where(task_table.c.id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["type"] != "fused"
            or task["user_id"] != user_id
            or task["status"] != "processing"
            or task["claim_token"] != claim_token
            or task["execution_generation"] != generation
            or task["user_cancelled_at"] is not None
            or not is_server_controlled_fused_task(task["type"], task["request_data"])
        ):
            return False
        result = self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.owner_id == user_id,
                _fused_submissions.c.stage == "accepted",
                _fused_submissions.c.dispatch_nonce.is_(None),
            )
            .values(
                stage="dispatching",
                dispatch_nonce=dispatch_nonce,
                dispatch_owner=claim_token,
                dispatch_generation=generation,
                wire_digest=wire_digest,
                updated_at=self.current_time(),
            )
        )
        return result.rowcount == 1

    def dispatch_gate_matches(
        self,
        *,
        task_id: str,
        submission_id: str,
        user_id: str,
        claim_token: str,
        generation: int,
        dispatch_nonce: str,
        wire_digest: str,
    ) -> bool:
        row = self._session.execute(
            select(
                _fused_submissions.c.stage,
                _fused_submissions.c.dispatch_nonce,
                _fused_submissions.c.dispatch_owner,
                _fused_submissions.c.dispatch_generation,
                _fused_submissions.c.wire_digest,
                _fused_submissions.c.task_id,
                _fused_submissions.c.owner_id,
            ).where(_fused_submissions.c.submission_id == submission_id)
        ).mappings().one_or_none()
        task = self._session.execute(
            select(
                AITaskRow.type,
                AITaskRow.user_id,
                AITaskRow.status,
                AITaskRow.claim_token,
                AITaskRow.execution_generation,
                AITaskRow.request_data,
            ).where(AITaskRow.id == task_id)
        ).mappings().one_or_none()
        return bool(
            row is not None
            and task is not None
            and row["stage"] == "dispatching"
            and row["dispatch_nonce"] == dispatch_nonce
            and row["dispatch_owner"] == claim_token
            and row["dispatch_generation"] == generation
            and row["wire_digest"] == wire_digest
            and row["task_id"] == task_id
            and row["owner_id"] == user_id
            and task["type"] == "fused"
            and task["user_id"] == user_id
            and task["status"] == "processing"
            and task["claim_token"] == claim_token
            and task["execution_generation"] == generation
            and is_server_controlled_fused_task(task["type"], task["request_data"])
        )

    def persist_provider_result(
        self,
        *,
        task_id: str,
        submission_id: str,
        claim_token: str,
        generation: int,
        encrypted_result: str,
        result_nonce: str,
        result_kind: str,
        result_digest: str,
    ) -> bool:
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.stage == "dispatching",
                _fused_submissions.c.dispatch_owner == claim_token,
                _fused_submissions.c.dispatch_generation == generation,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if ledger is None:
            return False
        task = self._session.execute(
            select(AITaskRow.status, AITaskRow.claim_token, AITaskRow.execution_generation)
            .where(AITaskRow.id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["status"] != "processing"
            or task["claim_token"] != claim_token
            or task["execution_generation"] != generation
        ):
            return False
        result = self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.stage == "dispatching",
                _fused_submissions.c.dispatch_owner == claim_token,
                _fused_submissions.c.dispatch_generation == generation,
            )
            .values(
                stage="result_known",
                encrypted_result=encrypted_result,
                result_nonce=result_nonce,
                result_kind=result_kind,
                result_digest=result_digest,
                updated_at=self.current_time(),
            )
        )
        return result.rowcount == 1

    def result_record_matches(
        self,
        submission_id: str,
        task_id: str,
        claim_token: str,
        generation: int,
        result_digest: str,
    ) -> bool:
        row = self._session.execute(
            select(
                _fused_submissions.c.stage,
                _fused_submissions.c.task_id,
                _fused_submissions.c.dispatch_owner,
                _fused_submissions.c.dispatch_generation,
                _fused_submissions.c.result_digest,
            ).where(_fused_submissions.c.submission_id == submission_id)
        ).mappings().one_or_none()
        return bool(
            row is not None
            and row["stage"] == "result_known"
            and row["task_id"] == task_id
            and row["dispatch_owner"] == claim_token
            and row["dispatch_generation"] == generation
            and row["result_digest"] == result_digest
        )

    def begin_durable_media_storage(
        self,
        task_id: str,
        submission_id: str,
        user_id: str,
        claim_token: str,
        generation: int,
    ) -> bool:
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.owner_id == user_id,
                _fused_submissions.c.stage == "result_known",
            )
            .with_for_update()
        ).mappings().one_or_none()
        if ledger is None:
            return False
        task_table = AITaskRow.__table__
        task = self._session.execute(
            select(
                task_table.c.type,
                task_table.c.user_id,
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.execution_generation,
                task_table.c.request_data,
                task_table.c.user_cancelled_at,
            )
            .where(task_table.c.id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["type"] != "fused"
            or task["user_id"] != user_id
            or task["status"] != "processing"
            or task["claim_token"] != claim_token
            or task["execution_generation"] != generation
            or task["user_cancelled_at"] is not None
            or not is_server_controlled_fused_task(task["type"], task["request_data"])
        ):
            return False
        result = self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.owner_id == user_id,
                _fused_submissions.c.stage == "result_known",
            )
            .values(stage="media_started", updated_at=self.current_time())
        )
        return result.rowcount == 1

    def durable_media_storage_started_matches(
        self,
        task_id: str,
        submission_id: str,
        user_id: str,
        claim_token: str,
        generation: int,
    ) -> bool:
        row = self._session.execute(
            select(
                _fused_submissions.c.stage,
                _fused_submissions.c.task_id,
                _fused_submissions.c.owner_id,
            ).where(_fused_submissions.c.submission_id == submission_id)
        ).mappings().one_or_none()
        task = self._session.execute(
            select(
                AITaskRow.type,
                AITaskRow.user_id,
                AITaskRow.status,
                AITaskRow.claim_token,
                AITaskRow.execution_generation,
                AITaskRow.request_data,
            ).where(AITaskRow.id == task_id)
        ).mappings().one_or_none()
        return bool(
            row is not None
            and task is not None
            and row["stage"] == "media_started"
            and row["task_id"] == task_id
            and row["owner_id"] == user_id
            and task["type"] == "fused"
            and task["user_id"] == user_id
            and task["status"] == "processing"
            and task["claim_token"] == claim_token
            and task["execution_generation"] == generation
            and is_server_controlled_fused_task(task["type"], task["request_data"])
        )

    def retain_interrupted_media_write(self, submission_id: str) -> bool:
        row = self._session.execute(
            select(
                _fused_submissions.c.task_id,
                _fused_submissions.c.owner_id,
                _fused_submissions.c.stage,
            )
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.stage == "media_started",
            )
            .with_for_update()
        ).mappings().one_or_none()
        if row is None:
            return False
        task_table = AITaskRow.__table__
        task = self._session.execute(
            select(task_table.c.status, task_table.c.claim_token, task_table.c.lease_until)
            .where(
                task_table.c.id == row["task_id"],
                task_table.c.user_id == row["owner_id"],
                task_table.c.type == "fused",
            )
            .with_for_update()
        ).mappings().one_or_none()
        now = self.current_time()
        if (
            task is None
            or task["status"] not in {"processing", "cancelling"}
            or not task["claim_token"]
            or task["lease_until"] is None
            or task["lease_until"] >= now
        ):
            return False
        changed = self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.stage == "media_started",
            )
            .values(stage="media_ack_unknown", updated_at=now)
        )
        if changed.rowcount != 1:
            return False
        updated = self._session.execute(
            update(task_table)
            .where(
                task_table.c.id == row["task_id"],
                task_table.c.user_id == row["owner_id"],
                task_table.c.status.in_(("processing", "cancelling")),
                task_table.c.claim_token == task["claim_token"],
            )
            .values(
                recovery_status="provider_result_unknown",
                result=FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
                progress_message=FUSED_MEDIA_STORAGE_UNKNOWN_PROGRESS_MESSAGE,
                updated_at=now,
            )
        )
        return updated.rowcount == 1

    def fused_media_write_by_submission(
        self, submission_id: str, *, slot: str = "primary"
    ) -> Mapping[str, object] | None:
        row = self._session.execute(
            select(_fused_media_writes).where(
                _fused_media_writes.c.submission_id == submission_id,
                _fused_media_writes.c.slot == slot,
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def persist_fused_media_plan(
        self,
        *,
        submission_id: str,
        task_id: str,
        owner_id: str,
        claim_token: str,
        generation: int,
        result_digest: str,
        result_kind: str,
        material_sha256: str,
        media_type: str,
        size_bytes: int,
        config_fingerprint: str,
        config_version: int,
        bucket: str,
        object_key: str,
        public_base: str,
        write_id: str,
        plan_version: int,
        encrypted_material: str,
        material_nonce: str,
        material_key_id: str,
        material_algorithm: str,
    ) -> bool:
        ledger, task = self._lock_fused_media_owner_task(
            submission_id,
            task_id,
            owner_id,
            claim_token,
            generation,
            allowed_stages=("result_known",),
        )
        if ledger is None or task is None or ledger["result_digest"] != result_digest:
            return False
        key_digest = hashlib.sha256(object_key.encode("utf-8")).hexdigest()
        existing = self._session.execute(
            select(_fused_media_writes)
            .where(
                _fused_media_writes.c.submission_id == submission_id,
                _fused_media_writes.c.slot == "primary",
            )
            .with_for_update()
        ).mappings().one_or_none()
        values = {
            "write_id": write_id,
            "submission_id": submission_id,
            "owner_id": owner_id,
            "task_id": task_id,
            "slot": "primary",
            "provider_result_digest": result_digest,
            "result_kind": result_kind,
            "material_sha256": material_sha256,
            "media_type": media_type,
            "size_bytes": size_bytes,
            "config_fingerprint": config_fingerprint,
            "config_version": config_version,
            "bucket": bucket,
            "object_key": object_key,
            "object_key_digest": key_digest,
            "public_base": public_base,
            "plan_version": plan_version,
            "encrypted_material": encrypted_material,
            "material_nonce": material_nonce,
            "material_key_id": material_key_id,
            "material_algorithm": material_algorithm,
        }
        if existing is not None:
            return all(existing.get(key) == value for key, value in values.items())
        now = self.current_time()
        self._session.execute(
            insert(_fused_media_writes).values(
                **values,
                phase="planned",
                revision=1,
                next_verify_at=now,
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        return True

    def claim_fused_media_put(
        self,
        *,
        submission_id: str,
        task_id: str,
        owner_id: str,
        claim_token: str,
        generation: int,
        write_nonce: str,
    ) -> bool:
        ledger, task = self._lock_fused_media_owner_task(
            submission_id,
            task_id,
            owner_id,
            claim_token,
            generation,
            allowed_stages=("result_known", "media_started"),
        )
        if ledger is None or task is None:
            return False
        write = self._session.execute(
            select(_fused_media_writes)
            .where(
                _fused_media_writes.c.submission_id == submission_id,
                _fused_media_writes.c.task_id == task_id,
                _fused_media_writes.c.owner_id == owner_id,
                _fused_media_writes.c.phase == "planned",
            )
            .with_for_update()
        ).mappings().one_or_none()
        if write is None:
            return False
        changed = self._session.execute(
            update(_fused_media_writes)
            .where(
                _fused_media_writes.c.write_id == write["write_id"],
                _fused_media_writes.c.phase == "planned",
                _fused_media_writes.c.revision == write["revision"],
            )
            .values(
                phase="put_started",
                revision=write["revision"] + 1,
                put_nonce=write_nonce,
                put_owner=claim_token,
                put_generation=generation,
                updated_at=self.current_time(),
            )
        )
        if changed.rowcount != 1:
            return False
        if ledger["stage"] == "result_known":
            changed_ledger = self._session.execute(
                update(_fused_submissions)
                .where(
                    _fused_submissions.c.submission_id == submission_id,
                    _fused_submissions.c.stage == "result_known",
                )
                .values(stage="media_started", updated_at=self.current_time())
            )
            if changed_ledger.rowcount != 1:
                return False
        return True

    def fused_media_put_claim_matches(
        self,
        *,
        submission_id: str,
        task_id: str,
        owner_id: str,
        claim_token: str,
        generation: int,
        write_nonce: str,
    ) -> bool:
        ledger = self._session.execute(
            select(_fused_submissions.c.stage, _fused_submissions.c.task_id, _fused_submissions.c.owner_id)
            .where(_fused_submissions.c.submission_id == submission_id)
        ).mappings().one_or_none()
        task = self._session.execute(
            select(
                AITaskRow.type,
                AITaskRow.user_id,
                AITaskRow.status,
                AITaskRow.claim_token,
                AITaskRow.execution_generation,
                AITaskRow.request_data,
                AITaskRow.user_cancelled_at,
            ).where(AITaskRow.id == task_id)
        ).mappings().one_or_none()
        write = self._session.execute(
            select(
                _fused_media_writes.c.phase,
                _fused_media_writes.c.put_nonce,
                _fused_media_writes.c.put_owner,
                _fused_media_writes.c.put_generation,
                _fused_media_writes.c.task_id,
                _fused_media_writes.c.owner_id,
            ).where(
                _fused_media_writes.c.submission_id == submission_id,
                _fused_media_writes.c.slot == "primary",
            )
        ).mappings().one_or_none()
        return bool(
            ledger is not None
            and task is not None
            and write is not None
            and ledger["stage"] == "media_started"
            and ledger["task_id"] == task_id
            and ledger["owner_id"] == owner_id
            and write["phase"] == "put_started"
            and write["put_nonce"] == write_nonce
            and write["put_owner"] == claim_token
            and write["put_generation"] == generation
            and write["task_id"] == task_id
            and write["owner_id"] == owner_id
            and task["type"] == "fused"
            and task["user_id"] == owner_id
            and task["status"] == "processing"
            and task["claim_token"] == claim_token
            and task["execution_generation"] == generation
            and task["user_cancelled_at"] is None
            and is_server_controlled_fused_task(task["type"], task["request_data"])
        )

    def record_fused_media_put_outcome(
        self, submission_id: str, write_id: str, *, unknown: bool
    ) -> bool:
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(_fused_submissions.c.submission_id == submission_id)
            .with_for_update()
        ).mappings().one_or_none()
        if ledger is None:
            return False
        task = self._session.execute(
            select(AITaskRow.id)
            .where(AITaskRow.id == ledger["task_id"])
            .with_for_update()
        ).first()
        if task is None:
            return False
        write = self._session.execute(
            select(_fused_media_writes)
            .where(
                _fused_media_writes.c.write_id == write_id,
                _fused_media_writes.c.submission_id == submission_id,
                _fused_media_writes.c.phase == "put_started",
            )
            .with_for_update()
        ).mappings().one_or_none()
        if write is None:
            return False
        if not unknown:
            return True
        now = self.current_time()
        delay = _media_verify_backoff(write["attempt_count"])
        changed = self._session.execute(
            update(_fused_media_writes)
            .where(
                _fused_media_writes.c.write_id == write_id,
                _fused_media_writes.c.phase == "put_started",
            )
            .values(
                phase="ack_unknown",
                revision=write["revision"] + 1,
                next_verify_at=now + timedelta(seconds=delay),
                updated_at=now,
            )
        )
        if changed.rowcount != 1:
            return False
        self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.stage == "media_started",
            )
            .values(stage="media_ack_unknown", updated_at=now)
        )
        return True

    def claim_fused_media_verification(
        self,
        *,
        submission_id: str,
        owner_id: str,
        verifier_token: str,
        lease_seconds: int,
        allow_active_task: bool = False,
        expected_claim_token: str | None = None,
        expected_generation: int | None = None,
    ) -> Mapping[str, object] | None:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("media verification lease is invalid")
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.owner_id == owner_id,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if ledger is None or ledger["stage"] not in {"media_started", "media_ack_unknown"}:
            return None
        task = self._session.execute(
            select(
                AITaskRow.type,
                AITaskRow.user_id,
                AITaskRow.status,
                AITaskRow.claim_token,
                AITaskRow.execution_generation,
                AITaskRow.request_data,
                AITaskRow.user_cancelled_at,
                AITaskRow.lease_until,
            )
            .where(AITaskRow.id == ledger["task_id"])
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["type"] != "fused"
            or task["user_id"] != owner_id
            or task["status"]
            not in ({"processing", "cancelling"} if allow_active_task else {"processing"})
            or (task["user_cancelled_at"] is not None and not allow_active_task)
            or not is_server_controlled_fused_task(task["type"], task["request_data"])
        ):
            return None
        if allow_active_task:
            if (
                expected_claim_token is None
                or expected_generation is None
                or task["claim_token"] != expected_claim_token
                or task["execution_generation"] != expected_generation
            ):
                return None
        elif task["lease_until"] is None or task["lease_until"] >= self.current_time():
            return None
        write = self._session.execute(
            select(_fused_media_writes)
            .where(
                _fused_media_writes.c.submission_id == submission_id,
                _fused_media_writes.c.owner_id == owner_id,
                _fused_media_writes.c.phase.in_(("put_started", "ack_unknown", "verified")),
            )
            .with_for_update()
        ).mappings().one_or_none()
        if write is None or write["put_owner"] != task["claim_token"] or write["put_generation"] != task["execution_generation"]:
            return None
        now = self.current_time()
        if (
            write["phase"] != "verified"
            and write["next_verify_at"] > now
            and not allow_active_task
        ):
            return None
        if write["verify_lease_until"] is not None and write["verify_lease_until"] >= now:
            return None
        changed = self._session.execute(
            update(_fused_media_writes)
            .where(
                _fused_media_writes.c.write_id == write["write_id"],
                _fused_media_writes.c.revision == write["revision"],
                _fused_media_writes.c.phase == write["phase"],
            )
            .values(
                verify_owner=verifier_token,
                verify_lease_until=now + timedelta(seconds=lease_seconds),
                revision=write["revision"] + 1,
                updated_at=now,
            )
        )
        if changed.rowcount != 1:
            return None
        return dict(write)

    def record_fused_media_verification(
        self,
        *,
        submission_id: str,
        write_id: str,
        verifier_token: str,
        status: str,
        observed_sha256: str | None = None,
        observed_size_bytes: int | None = None,
        media_type: str | None = None,
        locator: str | None = None,
        request_id: str | None = None,
    ) -> bool:
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(_fused_submissions.c.submission_id == submission_id)
            .with_for_update()
        ).mappings().one_or_none()
        if ledger is None:
            return False
        task = self._session.execute(
            select(AITaskRow.id)
            .where(AITaskRow.id == ledger["task_id"])
            .with_for_update()
        ).first()
        if task is None:
            return False
        write = self._session.execute(
            select(_fused_media_writes)
            .where(
                _fused_media_writes.c.write_id == write_id,
                _fused_media_writes.c.submission_id == submission_id,
                _fused_media_writes.c.verify_owner == verifier_token,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if write is None:
            return False
        now = self.current_time()
        if status == "verified":
            expected_locator = (
                f"{write['public_base']}/{quote(write['object_key'], safe='/')}"
            )
            if (
                observed_sha256 != write["material_sha256"]
                or observed_size_bytes != write["size_bytes"]
                or media_type != write["media_type"]
                or type(locator) is not str
                or locator != expected_locator
            ):
                return False
            values = {
                "phase": "verified",
                "verified_sha256": observed_sha256,
                "verified_media_type": media_type,
                "verified_size_bytes": observed_size_bytes,
                "verified_at": now,
                "verified_locator": locator,
                "verification_request_id": request_id,
                "next_verify_at": now,
            }
        elif status in {"not_found_pending", "unavailable"}:
            values = {
                "phase": "ack_unknown",
                "verify_owner": None,
                "verify_lease_until": None,
                "next_verify_at": now + timedelta(
                    seconds=_media_verify_backoff(write["attempt_count"])
                ),
            }
        elif status == "content_conflict":
            values = {
                "phase": "content_conflict",
                "verify_owner": None,
                "verify_lease_until": None,
                "next_verify_at": now + timedelta(days=3650),
            }
        else:
            return False
        changed = self._session.execute(
            update(_fused_media_writes)
            .where(
                _fused_media_writes.c.write_id == write_id,
                _fused_media_writes.c.verify_owner == verifier_token,
                _fused_media_writes.c.revision == write["revision"],
            )
            .values(
                **values,
                attempt_count=write["attempt_count"] + 1,
                revision=write["revision"] + 1,
                updated_at=now,
            )
        )
        if changed.rowcount != 1:
            return False
        if status in {"not_found_pending", "unavailable"}:
            self._session.execute(
                update(_fused_submissions)
                .where(
                    _fused_submissions.c.submission_id == submission_id,
                    _fused_submissions.c.stage == "media_started",
                )
                .values(stage="media_ack_unknown", updated_at=now)
            )
        return True

    def complete_verified_fused_media(
        self,
        prepared: PreparedFusedGeneration,
        *,
        write_id: str,
        verifier_token: str,
        locator: str,
    ) -> bool:
        submission_id = prepared.submission_id
        if submission_id is None or not locator:
            return False
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.task_id == prepared.task_id,
                _fused_submissions.c.owner_id == prepared.user_id,
                _fused_submissions.c.stage.in_(("media_started", "media_ack_unknown")),
            )
            .with_for_update()
        ).mappings().one_or_none()
        if ledger is None:
            return False
        task = self._session.execute(
            select(
                AITaskRow.type,
                AITaskRow.user_id,
                AITaskRow.status,
                AITaskRow.claim_token,
                AITaskRow.execution_generation,
                AITaskRow.request_data,
                AITaskRow.user_cancelled_at,
                AITaskRow.message_id,
                AITaskRow.recovery_status,
            )
            .where(AITaskRow.id == prepared.task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["type"] != "fused"
            or task["user_id"] != prepared.user_id
            or task["status"] != "processing"
            or task["user_cancelled_at"] is not None
            or not is_server_controlled_fused_task(task["type"], task["request_data"])
        ):
            return False
        write = self._session.execute(
            select(_fused_media_writes)
            .where(
                _fused_media_writes.c.write_id == write_id,
                _fused_media_writes.c.submission_id == submission_id,
                _fused_media_writes.c.owner_id == prepared.user_id,
                _fused_media_writes.c.task_id == prepared.task_id,
                _fused_media_writes.c.phase == "verified",
                _fused_media_writes.c.verify_owner == verifier_token,
                _fused_media_writes.c.verified_locator == locator,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if (
            write is None
            or task["claim_token"] != write["put_owner"]
            or task["execution_generation"] != write["put_generation"]
            or task["recovery_status"] not in {"ready", "provider_result_unknown"}
        ):
            return False
        now = self.current_time()
        changed = self._session.execute(
            update(AITaskRow.__table__)
            .where(
                AITaskRow.id == prepared.task_id,
                AITaskRow.type == "fused",
                AITaskRow.user_id == prepared.user_id,
                AITaskRow.status == "processing",
                AITaskRow.claim_token == write["put_owner"],
                AITaskRow.execution_generation == write["put_generation"],
                AITaskRow.user_cancelled_at.is_(None),
                AITaskRow.recovery_status.in_(("ready", "provider_result_unknown")),
            )
            .values(
                status="completed",
                result=locator,
                progress=100,
                progress_message="融合图已生成",
                claim_token=None,
                updated_at=now,
            )
        )
        if changed.rowcount != 1:
            return False
        fused_id = self.new_id()
        self._session.execute(
            insert(fused_assets).values(
                id=fused_id,
                series_id=prepared.series_id,
                member_type=prepared.member_type,
                members=json.dumps(prepared.members, ensure_ascii=False),
                name=prepared.name,
                image_url=locator,
                size=prepared.size,
                status="completed",
                error_message=None,
                created_at=now,
                updated_at=now,
            )
        )
        changed_ledger = self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.stage.in_(("media_started", "media_ack_unknown")),
                _fused_submissions.c.result_digest == write["provider_result_digest"],
            )
            .values(stage="completed", asset_id=fused_id, updated_at=now)
        )
        if changed_ledger.rowcount != 1:
            return False
        attached = self._append_to_target_frame(prepared, fused_id)
        message_id = task["message_id"]
        if message_id:
            content = locator
            if not attached and prepared.target_frame is not None:
                content = f"{locator}\n融合资产已保存，但目标分镜已变化，请从融合资产列表中查看。"
            self._session.execute(
                update(chat_messages)
                .where(chat_messages.c.id == message_id)
                .values(content=content)
            )
        changed_write = self._session.execute(
            update(_fused_media_writes)
            .where(
                _fused_media_writes.c.write_id == write_id,
                _fused_media_writes.c.phase == "verified",
                _fused_media_writes.c.verify_owner == verifier_token,
                _fused_media_writes.c.revision == write["revision"],
            )
            .values(
                phase="applied",
                revision=write["revision"] + 1,
                asset_id=fused_id,
                applied_at=now,
                updated_at=now,
            )
        )
        return changed_write.rowcount == 1

    def recoverable_fused_media_writes(
        self, *, limit: int = 32
    ) -> tuple[Mapping[str, object], ...]:
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("media recovery scan limit is invalid")
        now = self.current_time()
        task_table = AITaskRow.__table__
        rows = self._session.execute(
            select(
                _fused_media_writes.c.submission_id,
                _fused_media_writes.c.owner_id,
            )
            .join(
                _fused_submissions,
                and_(
                    _fused_submissions.c.submission_id
                    == _fused_media_writes.c.submission_id,
                    _fused_submissions.c.task_id == _fused_media_writes.c.task_id,
                    _fused_submissions.c.owner_id == _fused_media_writes.c.owner_id,
                ),
            )
            .join(task_table, task_table.c.id == _fused_media_writes.c.task_id)
            .where(
                _fused_media_writes.c.phase.in_(("put_started", "ack_unknown", "verified")),
                _fused_media_writes.c.next_verify_at <= now,
                (
                    _fused_media_writes.c.verify_lease_until.is_(None)
                    | (_fused_media_writes.c.verify_lease_until < now)
                ),
                _fused_submissions.c.stage.in_(
                    ("media_started", "media_ack_unknown")
                ),
                task_table.c.type == "fused",
                task_table.c.user_id == _fused_media_writes.c.owner_id,
                task_table.c.status == "processing",
                task_table.c.user_cancelled_at.is_(None),
                task_table.c.claim_token.is_not(None),
                _fused_media_writes.c.put_owner == task_table.c.claim_token,
                _fused_media_writes.c.put_generation
                == task_table.c.execution_generation,
                task_table.c.lease_until.is_not(None),
                task_table.c.lease_until < now,
                task_table.c.request_data.like(
                    '%"server_controlled_execution": "fused_generation_runtime_v1"%'
                ),
            )
            .order_by(
                _fused_media_writes.c.next_verify_at,
                _fused_media_writes.c.updated_at,
                _fused_media_writes.c.submission_id,
            )
            .limit(limit)
        ).mappings().all()
        return tuple(dict(row) for row in rows)

    def _lock_fused_media_owner_task(
        self,
        submission_id: str,
        task_id: str,
        owner_id: str,
        claim_token: str,
        generation: int,
        *,
        allowed_stages: tuple[str, ...],
    ) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.owner_id == owner_id,
                _fused_submissions.c.stage.in_(allowed_stages),
            )
            .with_for_update()
        ).mappings().one_or_none()
        if ledger is None:
            return None, None
        task = self._session.execute(
            select(
                AITaskRow.type,
                AITaskRow.user_id,
                AITaskRow.status,
                AITaskRow.claim_token,
                AITaskRow.execution_generation,
                AITaskRow.request_data,
                AITaskRow.user_cancelled_at,
                AITaskRow.recovery_status,
            )
            .where(AITaskRow.id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["type"] != "fused"
            or task["user_id"] != owner_id
            or task["status"] != "processing"
            or task["claim_token"] != claim_token
            or task["execution_generation"] != generation
            or task["user_cancelled_at"] is not None
            or task["recovery_status"] == "provider_result_unknown"
            or not is_server_controlled_fused_task(task["type"], task["request_data"])
        ):
            return None, None
        return ledger, task

    def retain_dispatch_unknown(self, submission_id: str) -> bool:
        row = self._session.execute(
            select(
                _fused_submissions.c.task_id,
                _fused_submissions.c.owner_id,
                _fused_submissions.c.stage,
            )
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.stage == "dispatching",
            )
            .with_for_update()
        ).mappings().one_or_none()
        if row is None:
            return False
        task_table = AITaskRow.__table__
        task = self._session.execute(
            select(
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.lease_until,
            )
            .where(
                task_table.c.id == row["task_id"],
                task_table.c.user_id == row["owner_id"],
                task_table.c.type == "fused",
            )
            .with_for_update()
        ).mappings().one_or_none()
        now = self.current_time()
        if (
            task is None
            or task["status"] not in {"processing", "cancelling"}
            or not task["claim_token"]
            or task["lease_until"] is None
            or task["lease_until"] >= now
        ):
            return False
        changed = self._session.execute(
            update(_fused_submissions)
            .where(
                _fused_submissions.c.submission_id == submission_id,
                _fused_submissions.c.stage == "dispatching",
            )
            .values(stage="provider_result_unknown", updated_at=now)
        )
        if changed.rowcount != 1:
            return False
        task_update = self._session.execute(
            update(task_table)
            .where(
                task_table.c.id == row["task_id"],
                task_table.c.user_id == row["owner_id"],
                task_table.c.status.in_(("processing", "cancelling")),
                task_table.c.claim_token == task["claim_token"],
            )
            .values(
                recovery_status="provider_result_unknown",
                result=FUSED_UNKNOWN_REASON,
                progress_message=FUSED_UNKNOWN_PROGRESS_MESSAGE,
                updated_at=now,
            )
        )
        return task_update.rowcount == 1

    def authorize_media_storage(
        self,
        task_id: str,
        user_id: str,
        claim_token: str,
    ) -> bool:
        task_table = AITaskRow.__table__
        task = self._session.execute(
            select(
                task_table.c.type,
                task_table.c.user_id,
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.request_data,
                task_table.c.recovery_status,
                task_table.c.user_cancelled_at,
            )
            .where(task_table.c.id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        return bool(
            task is not None
            and task["type"] == "fused"
            and task["user_id"] == user_id
            and task["status"] == "processing"
            and task["claim_token"] == claim_token
            and task["user_cancelled_at"] is None
            and task["recovery_status"] != "provider_result_unknown"
            and is_server_controlled_fused_task(task["type"], task["request_data"])
        )

    def retain_unknown(
        self,
        task_id: str,
        user_id: str,
        claim_token: str,
        safe_reason: FusedGenerationUnknownReason,
    ) -> bool:
        task_table = AITaskRow.__table__
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.owner_id == user_id,
                _fused_submissions.c.stage.in_(
                    ("accepted", "dispatching", "result_known", "media_started")
                ),
            )
            .with_for_update()
        ).mappings().one_or_none()
        task = self._session.execute(
            select(
                task_table.c.type,
                task_table.c.user_id,
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.request_data,
                task_table.c.recovery_status,
            ).where(task_table.c.id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["user_id"] != user_id
            or task["status"] not in {"processing", "cancelling"}
            or task["claim_token"] != claim_token
            or task["recovery_status"] == "provider_result_unknown"
            or not is_server_controlled_fused_task(task["type"], task["request_data"])
        ):
            return False

        reason = (
            safe_reason
            if type(safe_reason) is str and safe_reason in FUSED_UNKNOWN_REASONS
            else FUSED_UNKNOWN_REASON
        )
        progress_message = (
            FUSED_MEDIA_STORAGE_UNKNOWN_PROGRESS_MESSAGE
            if reason == FUSED_MEDIA_STORAGE_UNKNOWN_REASON
            else FUSED_UNKNOWN_PROGRESS_MESSAGE
        )
        result = self._session.execute(
            update(task_table)
            .where(
                task_table.c.id == task_id,
                task_table.c.user_id == user_id,
                task_table.c.type == "fused",
                task_table.c.status.in_(("processing", "cancelling")),
                task_table.c.claim_token == claim_token,
                task_table.c.request_data == task["request_data"],
                task_table.c.recovery_status != "provider_result_unknown",
            )
            .values(
                recovery_status="provider_result_unknown",
                result=reason,
                progress_message=progress_message,
                updated_at=self.current_time(),
            )
        )
        if result.rowcount != 1:
            return False
        durable_stage = (
            "media_ack_unknown"
            if reason == FUSED_MEDIA_STORAGE_UNKNOWN_REASON
            else "provider_result_unknown"
        )
        if ledger is not None:
            self._session.execute(
                update(_fused_submissions)
                .where(
                    _fused_submissions.c.submission_id == ledger["submission_id"],
                    _fused_submissions.c.stage.in_(
                        ("accepted", "dispatching", "result_known", "media_started")
                    ),
                )
                .values(stage=durable_stage, updated_at=self.current_time())
            )
        return True

    def retain_known_media_result(
        self,
        task_id: str,
        user_id: str,
        claim_token: str,
        result_url: str,
    ) -> bool:
        if (
            type(result_url) is not str
            or not result_url
            or result_url != result_url.strip()
        ):
            return False
        task_table = AITaskRow.__table__
        ledger = self._session.execute(
            select(_fused_submissions)
            .where(
                _fused_submissions.c.task_id == task_id,
                _fused_submissions.c.owner_id == user_id,
                _fused_submissions.c.stage.in_(
                    ("dispatching", "result_known", "media_started")
                ),
            )
            .with_for_update()
        ).mappings().one_or_none()
        task = self._session.execute(
            select(
                task_table.c.type,
                task_table.c.user_id,
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.request_data,
                task_table.c.recovery_status,
            )
            .where(task_table.c.id == task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["type"] != "fused"
            or task["user_id"] != user_id
            or task["status"] not in {"processing", "cancelling"}
            or task["claim_token"] != claim_token
            or task["recovery_status"] != "ready"
            or not is_server_controlled_fused_task(task["type"], task["request_data"])
        ):
            return False

        result = self._session.execute(
            update(task_table)
            .where(
                task_table.c.id == task_id,
                task_table.c.user_id == user_id,
                task_table.c.type == "fused",
                task_table.c.status.in_(("processing", "cancelling")),
                task_table.c.claim_token == claim_token,
                task_table.c.request_data == task["request_data"],
                task_table.c.recovery_status == "ready",
            )
            .values(
                recovery_status="provider_result_unknown",
                result=result_url,
                progress_message=FUSED_DELIVERY_PENDING_PROGRESS_MESSAGE,
                updated_at=self.current_time(),
            )
        )
        if result.rowcount != 1:
            return False
        if ledger is not None:
            self._session.execute(
                update(_fused_submissions)
                .where(
                    _fused_submissions.c.submission_id == ledger["submission_id"],
                    _fused_submissions.c.stage.in_(
                        ("dispatching", "result_known", "media_started")
                    ),
                )
                .values(stage="media_ack_unknown", updated_at=self.current_time())
            )
        return True

    def finish_task(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        *,
        image_url: str | None,
        error_message: str | None,
        preserve_if_cancelled: bool = False,
    ) -> bool:
        task_table = AITaskRow.__table__
        if prepared.submission_id is not None:
            ledger = self._session.execute(
                select(_fused_submissions)
                .where(
                    _fused_submissions.c.submission_id == prepared.submission_id,
                    _fused_submissions.c.task_id == prepared.task_id,
                    _fused_submissions.c.owner_id == prepared.user_id,
                    _fused_submissions.c.stage.in_(
                        ("accepted", "dispatching", "result_known", "media_started")
                    ),
                )
                .with_for_update()
            ).mappings().one_or_none()
            if ledger is None:
                return False
        task = self._session.execute(
            select(
                task_table.c.status,
                task_table.c.claim_token,
                task_table.c.user_cancelled_at,
                task_table.c.message_id,
                task_table.c.recovery_status,
            ).where(task_table.c.id == prepared.task_id)
            .with_for_update()
        ).mappings().one_or_none()
        if (
            task is None
            or task["status"] != "processing"
            or task["claim_token"] != claim_token
            or task["recovery_status"] == "provider_result_unknown"
        ):
            return False

        cancelled = task["user_cancelled_at"] is not None
        if cancelled and preserve_if_cancelled:
            return False
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
                if preserve_if_cancelled:
                    return False
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
            return False

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
        if prepared.submission_id is not None:
            self._session.execute(
                update(_fused_submissions)
                .where(
                    _fused_submissions.c.submission_id == prepared.submission_id,
                    _fused_submissions.c.task_id == prepared.task_id,
                    _fused_submissions.c.stage.in_(
                        (
                            "accepted",
                            "dispatching",
                            "result_known",
                            "media_started",
                        )
                    ),
                )
                .values(
                    stage="completed" if status == "completed" else "failed",
                    asset_id=fused_id,
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
        return True

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
                task_table.c.recovery_status != "provider_result_unknown",
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


def _media_verify_backoff(attempt_count: object) -> int:
    attempts = attempt_count if type(attempt_count) is int and attempt_count >= 0 else 0
    return min(300, 2 ** min(attempts, 8))
