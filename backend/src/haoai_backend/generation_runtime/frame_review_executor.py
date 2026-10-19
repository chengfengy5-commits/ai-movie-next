"""Claim-gated one-shot execution for the original frame.review intent."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    ContinuationClaimAuthority,
    ProviderFailure,
    TextResult,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    UserRow,
)
from haoai_backend.generation_core.frame_review_claims import (
    SqlAlchemyFrameReviewClaims,
)
from haoai_backend.generation_core.frame_review_intents import (
    SqlAlchemyFrameReviewIntents,
)
from haoai_backend.generation_core.frame_review_results import (
    SqlAlchemyFrameReviewResults,
)
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
)
from haoai_backend.generation_providers.text.openai_provider import (
    build_original_openai_text_provider,
)


class _ExecutionAuthorityLost(RuntimeError):
    pass


@dataclass(slots=True)
class FrameReviewExecutor:
    """Execute only a newly committed review intent under its live task claim."""

    configuration: GenerationRuntimeConfig
    registry_capabilities: RegistryCapabilities
    _execution_port: object | None = None

    def bind_execution_port(self, port: object) -> None:
        if not callable(getattr(port, "claim_next", None)) or not callable(
            getattr(port, "claim_continuation", None)
        ):
            raise TypeError("frame.review executor requires its registered execution port")
        self._execution_port = port

    def execute_claim(self, claim: ClaimAuthority | ContinuationClaimAuthority):
        if isinstance(claim, ContinuationClaimAuthority):
            return "ownership_lost"
        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != "frame.review"
            or claim.local_lost_signal.is_set()
            or self._execution_port is None
        ):
            return "ownership_lost"
        crypto = self.registry_capabilities.crypto
        proof_authority = self.registry_capabilities.proof_authority
        if not isinstance(crypto, CredentialCrypto) or not isinstance(
            proof_authority, HmacProofAuthority
        ):
            return "pending"

        intents = SqlAlchemyFrameReviewIntents(
            self.configuration.session_factory,
            self.configuration.db_clock,
            self.configuration.id_factory,
            crypto,
            proof_authority,
        )
        prepared = intents.prepare(claim)
        if prepared.disposition == "ownership_lost":
            return "ownership_lost"
        if (
            prepared.disposition != "committed"
            or prepared.verified_input is None
            or prepared.intent is None
        ):
            # Existing and unresolved intents are durable no-repost boundaries.
            return "pending"

        verified = prepared.verified_input
        intent = prepared.intent
        guard = _ReviewAuthorityGuard(
            self.configuration, claim, intent, verified.unit_id
        )
        try:
            guard.require_live()
            provider = build_original_openai_text_provider(
                snapshot=verified.snapshot,
                execution_snapshot_ref=intent.snapshot_ref,
                credential_binding=verified.credential_binding,
                intent=intent,
                wire_model=verified.snapshot.model_name,
                http_client_factory=lambda: _GatedHttpContext(
                    self.configuration.http_client_factory, guard
                ),
                credential_resolver=lambda binding, purpose: self._resolve_credential(
                    binding, purpose, guard
                ),
            )
        except _ExecutionAuthorityLost:
            return "ownership_lost"
        except Exception:
            # The intent is already committed. A later claim must reconcile it.
            return "pending" if guard.is_live_before_deadline() else "ownership_lost"

        try:
            result = provider.optimize_frame(
                verified.snapshot,
                verified.snapshot.effective_context,
                intent,
            )
        except Exception:
            return "pending" if guard.is_claim_live() else "ownership_lost"
        if isinstance(result, ProviderFailure):
            return "pending" if guard.is_claim_live() else "ownership_lost"
        if not isinstance(result, TextResult):
            return "pending" if guard.is_claim_live() else "ownership_lost"

        results = SqlAlchemyFrameReviewResults(
            self.configuration.session_factory,
            self.configuration.db_clock,
            self.configuration.id_factory,
            crypto,
            proof_authority,
        )
        return results.persist_success(claim, intent, verified, result)

    def execute_continuation_claim(
        self,
        claim: ContinuationClaimAuthority,
        local_stop_signal: Event | None,
    ):
        consume = getattr(self._execution_port, "consume_continuation", None)
        if (
            not isinstance(claim, ContinuationClaimAuthority)
            or claim.owner_scope.operation != "frame.review"
            or not callable(consume)
            or self._execution_port is None
            or (local_stop_signal is not None and local_stop_signal.is_set())
        ):
            return "ownership_lost"
        crypto = self.registry_capabilities.crypto
        proof_authority = self.registry_capabilities.proof_authority
        if not isinstance(crypto, CredentialCrypto) or not isinstance(
            proof_authority, HmacProofAuthority
        ):
            return "pending"
        return consume(claim, local_stop_signal=local_stop_signal)

    def _resolve_credential(self, binding, purpose, guard: "_ReviewAuthorityGuard"):
        guard.require_live_before_deadline()
        credential = self.configuration.credential_resolver(binding, purpose)
        guard.require_live_before_deadline()
        return credential


class _ReviewAuthorityGuard:
    def __init__(
        self,
        configuration: GenerationRuntimeConfig,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        unit_id: str,
    ) -> None:
        self._configuration = configuration
        self._claim = claim
        self._intent = intent
        self._unit_id = unit_id

    def is_claim_live(self) -> bool:
        return self._check_claim(require_before_deadline=False)

    def is_live_before_deadline(self) -> bool:
        return self._check_claim(require_before_deadline=True)

    def require_live(self) -> None:
        if not self.is_claim_live():
            raise _ExecutionAuthorityLost

    def require_live_before_deadline(self) -> None:
        if not self.is_live_before_deadline():
            raise _ExecutionAuthorityLost

    def _check_claim(self, *, require_before_deadline: bool) -> bool:
        claim = self._claim
        if claim.local_lost_signal.is_set():
            return False
        session: Session | None = None
        try:
            session = self._configuration.session_factory()
            with session.begin():
                user = session.execute(
                    select(UserRow.id)
                    .where(UserRow.id == claim.scope.user_id)
                    .with_for_update(skip_locked=True)
                ).scalar_one_or_none()
                task = session.execute(
                    select(AITaskRow.__table__)
                    .where(
                        AITaskRow.id == claim.scope.task_id,
                        AITaskRow.user_id == claim.scope.user_id,
                        AITaskRow.type == "ai-review",
                    )
                    .with_for_update(skip_locked=True)
                ).mappings().one_or_none()
                if user is None or task is None:
                    return False
                unit = session.execute(
                    select(BillingUnitRow.__table__)
                    .where(
                        BillingUnitRow.__table__.c.id == self._unit_id,
                        BillingUnitRow.__table__.c.task_id == claim.scope.task_id,
                    )
                    .with_for_update()
                ).mappings().one_or_none()
                if unit is None or unit.get("task_id") != claim.scope.task_id:
                    return False
                step = session.execute(
                    select(ExecutionStepRow.__table__)
                    .where(
                        ExecutionStepRow.id == self._intent.step_id,
                        ExecutionStepRow.billing_unit_id == unit["id"],
                    )
                    .with_for_update()
                ).mappings().one_or_none()
                external = session.execute(
                    select(ExternalSubmissionRow.__table__)
                    .where(
                        ExternalSubmissionRow.id
                        == self._intent.external_submission_id,
                        ExternalSubmissionRow.step_id == self._intent.step_id,
                    )
                    .with_for_update()
                ).mappings().one_or_none()
                now = self._configuration.db_clock()
                if (
                    step is None
                    or external is None
                    or not _aware_utc_datetime(now)
                    or not _task_claim_is_live(task, claim, now)
                    or not _intent_rows_match(unit, step, external, self._intent)
                    or (
                        require_before_deadline
                        and _as_utc(now) >= _as_utc(self._intent.deadline_at)
                    )
                ):
                    return False
                task_table = AITaskRow.__table__
                confirmed = session.execute(
                    update(task_table)
                    .where(
                        task_table.c.id == claim.scope.task_id,
                        task_table.c.user_id == claim.scope.user_id,
                        task_table.c.type == "ai-review",
                        task_table.c.status == task["status"],
                        task_table.c.status == "processing",
                        task_table.c.user_cancelled_at.is_(None),
                        task_table.c.claimed_by == claim.worker_instance,
                        task_table.c.claim_token == claim.token,
                        task_table.c.execution_generation
                        == claim.execution_generation,
                        task_table.c.lease_until > _database_datetime(_as_utc(now)),
                    )
                    .values(updated_at=task_table.c.updated_at)
                )
                return confirmed.rowcount == 1 and not claim.local_lost_signal.is_set()
        except Exception:
            return False
        finally:
            if session is not None:
                session.close()

class _GatedHttpClient:
    def __init__(self, client: object, guard: _ReviewAuthorityGuard) -> None:
        self._client = client
        self._guard = guard

    async def request(self, *args: object, **kwargs: object):
        self._guard.require_live_before_deadline()
        return await self._client.request(*args, **kwargs)


class _GatedHttpContext:
    def __init__(self, factory, guard: _ReviewAuthorityGuard) -> None:
        self._factory = factory
        self._guard = guard
        self._context = None

    async def __aenter__(self):
        self._guard.require_live_before_deadline()
        context = self._factory()
        client = await context.__aenter__()
        try:
            self._guard.require_live_before_deadline()
        except BaseException:
            await context.__aexit__(*sys.exc_info())
            raise
        self._context = context
        return _GatedHttpClient(client, self._guard)

    async def __aexit__(self, exc_type, exc, traceback):
        if self._context is None:
            return False
        return await self._context.__aexit__(exc_type, exc, traceback)


def _task_claim_is_live(task, claim: ClaimAuthority, now: datetime) -> bool:
    lease = task.get("lease_until")
    return all(
        (
            task.get("id") == claim.scope.task_id,
            task.get("user_id") == claim.scope.user_id,
            task.get("type") == "ai-review",
            task.get("status") == "processing",
            task.get("user_cancelled_at") is None,
            task.get("claimed_by") == claim.worker_instance,
            task.get("claim_token") == claim.token,
            task.get("execution_generation") == claim.execution_generation,
            isinstance(lease, datetime),
            _as_utc(lease) > _as_utc(now),
        )
    )


def _intent_rows_match(unit, step, external, intent: CommittedIntent) -> bool:
    return all(
        (
            unit.get("first_intent_at") == _database_datetime(_as_utc(intent.first_intent_at)),
            unit.get("deadline_at") == _database_datetime(_as_utc(intent.deadline_at)),
            step.get("id") == intent.step_id,
            step.get("execution_status") == "submitting",
            external.get("id") == intent.external_submission_id,
            external.get("provider") == intent.provider,
            external.get("model_ref") == intent.model_id,
            external.get("intent_at") == _database_datetime(_as_utc(intent.first_intent_at)),
            external.get("deadline_at") == _database_datetime(_as_utc(intent.deadline_at)),
            external.get("submission_status") == "submitting",
        )
    )


def _aware_utc_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timezone.utc.utcoffset(value)
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)


__all__ = ("FrameReviewExecutor",)
