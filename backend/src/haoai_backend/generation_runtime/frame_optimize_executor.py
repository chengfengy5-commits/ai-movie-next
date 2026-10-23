"""Claim-gated execution and phase consumption for frame.optimize batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event

from sqlalchemy import select, update

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    ContinuationClaimAuthority,
    ProviderFailure,
    TextResult,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    UserRow,
)
from haoai_backend.generation_core.frame_optimize_application import (
    SqlAlchemyFrameOptimizeApplications,
)
from haoai_backend.generation_core.frame_optimize_continuation_claims import (
    SqlAlchemyFrameOptimizeContinuationClaims,
)
from haoai_backend.generation_core.frame_optimize_execution import (
    intent_row_matches,
    lock_frame_optimize_batch,
    valid_intent_projection,
)
from haoai_backend.generation_core.frame_optimize_fees import (
    SqlAlchemyFrameOptimizeFees,
)
from haoai_backend.generation_core.frame_optimize_finalization import (
    SqlAlchemyFrameOptimizeFinalization,
)
from haoai_backend.generation_core.frame_optimize_intents import (
    SqlAlchemyFrameOptimizeIntents,
)
from haoai_backend.generation_core.frame_optimize_progress import (
    lock_frame_optimize_progress,
)
from haoai_backend.generation_core.frame_optimize_recovery import (
    SqlAlchemyFrameOptimizeRecovery,
)
from haoai_backend.generation_core.frame_optimize_results import (
    SqlAlchemyFrameOptimizeResults,
)
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
)
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
)
from haoai_backend.generation_providers.text.frame_optimize_provider import (
    FrameOptimizeTextProvider,
)


@dataclass(slots=True)
class FrameOptimizeExecutor:
    """Execute only newly committed intents and consume their original Work phases."""

    configuration: GenerationRuntimeConfig
    registry_capabilities: RegistryCapabilities
    recovery: SqlAlchemyFrameOptimizeRecovery
    continuation_claims: SqlAlchemyFrameOptimizeContinuationClaims
    fees: SqlAlchemyFrameOptimizeFees
    applications: SqlAlchemyFrameOptimizeApplications
    finalization: SqlAlchemyFrameOptimizeFinalization
    _execution_port: object | None = None

    def bind_execution_port(self, port: object) -> None:
        if not callable(getattr(port, "claim_next", None)) or not callable(
            getattr(port, "claim_continuation", None)
        ):
            raise TypeError("frame.optimize executor requires its registered execution port")
        self._execution_port = port

    def execute_claim(self, claim: ClaimAuthority | ContinuationClaimAuthority) -> str:
        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != "frame.optimize"
            or claim.local_lost_signal.is_set()
            or self._execution_port is None
        ):
            return "ownership_lost"
        crypto = self.registry_capabilities.crypto
        proof = self.registry_capabilities.proof_authority
        if type(crypto) is not CredentialCrypto or type(proof) is not HmacProofAuthority:
            return "pending"
        intents = SqlAlchemyFrameOptimizeIntents(
            self.configuration.session_factory,
            self.configuration.db_clock,
            crypto,
            proof,
            self.configuration.id_factory,
        )
        results = SqlAlchemyFrameOptimizeResults(
            self.configuration.session_factory,
            self.configuration.db_clock,
            crypto,
            proof,
            self.configuration.id_factory,
        )
        self._drain_current_work(claim)
        try:
            with self.configuration.session_factory() as session:
                graph = lock_frame_optimize_batch(
                    session,
                    claim.scope.user_id,
                    claim.scope.task_id,
                    crypto=crypto,
                    proof_authority=proof,
                )
        except Exception:
            graph = None
        if graph is None:
            return "pending"

        for item in graph.verified.items:
            if claim.local_lost_signal.is_set():
                return "ownership_lost"
            try:
                prepared = intents.prepare(claim, item.unit_id)
            except Exception:
                continue
            if prepared.disposition == "ownership_lost":
                return "ownership_lost"
            if (
                prepared.disposition != "committed"
                or prepared.verified_batch is None
                or prepared.verified_item is None
                or prepared.intent is None
            ):
                continue
            verified_batch = prepared.verified_batch
            verified_item = prepared.verified_item
            intent = prepared.intent
            guard = _FrameOptimizeAuthorityGuard(
                self.configuration, claim, intent, verified_item.unit_id, crypto, proof
            )
            try:
                registry = build_credential_registry(self.registry_capabilities)
                provider = FrameOptimizeTextProvider(
                    snapshot=verified_item.snapshot,
                    item=verified_item,
                    intent=intent,
                    http_client_factory=self.configuration.http_client_factory,
                    credential_resolver=registry.resolve_original_credential,
                    authority_guard=guard,
                )
                result = provider.optimize_frame()
            except Exception:
                result = ProviderFailure(
                    "provider_outcome_unknown",
                    False,
                    "文本请求结果需要核验",
                )
            if isinstance(result, TextResult):
                persisted = results.persist_success(
                    claim, intent, verified_batch, verified_item, result
                )
                if isinstance(persisted, str):
                    self.recovery.record_observation(
                        claim if guard.is_claim_live() else None,
                        intent,
                        verified_batch,
                        verified_item,
                        code="provider_outcome_unknown",
                        request_started=guard.request_started,
                        http_status=guard.http_status,
                        observed_text=result.text,
                    )
                    if persisted == "ownership_lost":
                        return "ownership_lost"
                self._drain_current_work(claim)
            else:
                self.recovery.record_observation(
                    claim if guard.is_claim_live() else None,
                    intent,
                    verified_batch,
                    verified_item,
                    code=result.code,
                    request_started=guard.request_started,
                    http_status=guard.http_status,
                )
                if not guard.is_claim_live():
                    return "ownership_lost"
        self._drain_current_work(claim)
        return "pending" if claim.local_lost_signal.is_set() else "completed"

    def execute_continuation_claim(
        self,
        claim: ContinuationClaimAuthority,
        local_stop_signal: Event | None,
    ) -> str:
        if (
            not isinstance(claim, ContinuationClaimAuthority)
            or claim.owner_scope.operation != "frame.optimize"
            or self._execution_port is None
            or (local_stop_signal is not None and local_stop_signal.is_set())
        ):
            return "ownership_lost"
        locked = self._read_continuation(claim)
        if locked is None:
            return "ownership_lost"
        phase = locked.work.get("phase")
        if phase == "fee_pending" and claim.phase_version == 0:
            self.fees.consume_fee(claim)
            return "pending"
        if phase == "delivery_pending" and claim.phase_version == 1:
            self.applications.consume_delivery(claim)
            return "pending"
        if phase == "finalize_pending" and claim.phase_version == 2:
            result = self.finalization.consume_finalize(claim)
            return "completed" if result.disposition in {"completed", "already_finalized"} else "pending"
        return "pending"

    def _read_continuation(self, claim: ContinuationClaimAuthority):
        try:
            with self.configuration.session_factory() as session:
                with session.begin():
                    now = self.configuration.db_clock()
                    if claim.phase_version == 2:
                        return self.continuation_claims.lock_current_finalization_claim(
                            session, claim, now
                        )
                    return self.continuation_claims.lock_current_claim(session, claim, now)
        except Exception:
            return None

    def _drain_current_work(self, claim: ClaimAuthority) -> None:
        for _ in range(90):
            if claim.local_lost_signal.is_set():
                return
            progress = self._read_progress(claim)
            if progress is None or progress.cancellation_marker:
                return
            pending = [
                (unit_id, work)
                for unit_id, work in progress.works_by_unit.items()
                if work.get("phase") in {"fee_pending", "delivery_pending", "finalize_pending"}
            ]
            if not pending:
                return
            changed = False
            def order(pair):
                phase = pair[1].get("phase")
                if progress.stopped_insufficient:
                    phase_order = {
                        "delivery_pending": 0,
                        "finalize_pending": 1,
                        "fee_pending": 2,
                    }
                else:
                    phase_order = {
                        "fee_pending": 0,
                        "delivery_pending": 1,
                        "finalize_pending": 2,
                    }
                return (phase_order.get(phase, 3), _item_order(progress.graph, pair[0]))

            for unit_id, work in sorted(pending, key=order):
                work_id = work.get("work_id")
                if not isinstance(work_id, str):
                    return
                phase = work.get("phase")
                if phase == "fee_pending":
                    outcome = self.fees.resolve_current_fee(claim, work_id)
                    changed = outcome.disposition in {
                        "charged", "no_charge", "existing", "stopped_insufficient"
                    }
                elif phase == "delivery_pending":
                    outcome = self.applications.deliver_current(claim, work_id)
                    changed = outcome.kind in {"applied", "already_applied", "target_conflict", "target_deleted"}
                else:
                    outcome = self.finalization.finalize_current(claim, work_id)
                    changed = outcome.disposition in {"work_finalized", "completed", "failed", "already_finalized"}
                if not changed or claim.local_lost_signal.is_set():
                    return
            if not changed:
                return

    def _read_progress(self, claim: ClaimAuthority):
        crypto = self.registry_capabilities.crypto
        proof = self.registry_capabilities.proof_authority
        if type(crypto) is not CredentialCrypto or type(proof) is not HmacProofAuthority:
            return None
        try:
            with self.configuration.session_factory() as session:
                with session.begin():
                    graph = lock_frame_optimize_batch(
                        session,
                        claim.scope.user_id,
                        claim.scope.task_id,
                        crypto=crypto,
                        proof_authority=proof,
                    )
                    if graph is None or not valid_intent_projection(graph):
                        return None
                    progress = lock_frame_optimize_progress(session, graph)
                    if progress is None:
                        return None
                    return _ProgressView(graph, progress)
        except Exception:
            return None


@dataclass(frozen=True, slots=True)
class _ProgressView:
    graph: object
    progress: object

    def __getattr__(self, name: str):
        return getattr(self.progress, name)


class _FrameOptimizeAuthorityGuard:
    def __init__(self, configuration, claim, intent, unit_id, crypto, proof) -> None:
        self.configuration = configuration
        self.claim = claim
        self.intent = intent
        self.unit_id = unit_id
        self.crypto = crypto
        self.proof = proof
        self.request_started = False
        self.http_status: int | None = None

    def mark_request_started(self) -> None:
        self.require_live_before_deadline()
        self.request_started = True

    def record_http_status(self, status: object) -> None:
        self.http_status = status if type(status) is int and 100 <= status <= 599 else None

    def is_claim_live(self) -> bool:
        return self._check(require_before_deadline=False)

    def require_live_before_deadline(self) -> None:
        if not self._check(require_before_deadline=True):
            raise _ExecutionAuthorityLost

    def _check(self, *, require_before_deadline: bool) -> bool:
        claim = self.claim
        if claim.local_lost_signal.is_set():
            return False
        try:
            with self.configuration.session_factory() as session:
                with session.begin():
                    session.execute(
                        select(UserRow.id)
                        .where(UserRow.id == claim.scope.user_id)
                        .with_for_update(skip_locked=True)
                    ).scalar_one_or_none()
                    graph = lock_frame_optimize_batch(
                        session,
                        claim.scope.user_id,
                        claim.scope.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof,
                    )
                    now = self.configuration.db_clock()
                    progress = lock_frame_optimize_progress(session, graph) if graph else None
                    if (
                        graph is None
                        or progress is None
                        or not _claim_is_current(graph.task, claim, now)
                        or not valid_intent_projection(graph)
                        or progress.stopped_insufficient
                        or progress.cancellation_marker is not None
                        or progress.policy_stop_event_refs
                    ):
                        return False
                    unit = graph.units_by_id.get(self.unit_id)
                    step = graph.steps_by_unit.get(self.unit_id)
                    external = graph.external_by_step.get(self.intent.step_id)
                    if (
                        unit is None
                        or step is None
                        or external is None
                        or not intent_row_matches(external, graph, unit, step)
                        or external.get("id") != self.intent.external_submission_id
                    ):
                        return False
                    deadline = _utc(self.intent.deadline_at)
                    current = _utc(now)
                    if deadline is None or current is None:
                        return False
                    if require_before_deadline and current >= deadline:
                        return False
                    task = AITaskRow.__table__
                    changed = session.execute(
                        update(task)
                        .where(
                            task.c.id == claim.scope.task_id,
                            task.c.user_id == claim.scope.user_id,
                            task.c.type == "batch-optimize",
                            task.c.status == "processing",
                            task.c.claimed_by == claim.worker_instance,
                            task.c.claim_token == claim.token,
                            task.c.execution_generation == claim.execution_generation,
                            task.c.lease_until > _db_time(current),
                            task.c.user_cancelled_at.is_(None),
                        )
                        .values(updated_at=task.c.updated_at)
                    )
                    return changed.rowcount == 1 and not claim.local_lost_signal.is_set()
        except Exception:
            return False


class _ExecutionAuthorityLost(RuntimeError):
    pass


def _claim_is_current(task, claim, now) -> bool:
    lease = _utc(task.get("lease_until"))
    current = _utc(now)
    return bool(
        current is not None
        and lease is not None
        and task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == "batch-optimize"
        and task.get("status") == "processing"
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and task.get("user_cancelled_at") is None
        and lease > current
        and not claim.local_lost_signal.is_set()
    )


def _item_order(graph, unit_id: str) -> int:
    item = graph.items_by_unit.get(unit_id)
    return item.order if item is not None else 2**31


def _utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _db_time(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


__all__ = ("FrameOptimizeExecutor",)
