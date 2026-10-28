"""Durable create intents and GET-only recovery for HaoAI Group attempts."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    ExternalOperationRef,
)
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.haoai_group_execution_snapshot import (
    PersistedHaoaiGroupExecutionBinding,
    VerifiedHaoaiGroupExecutionInput,
    read_original_haoai_group_execution_input,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    PollReservationRow,
)
from haoai_backend.generation_core.haoai_group.evidence import (
    is_retryable_reference_failure_observation,
)
from haoai_backend.generation_core.haoai_group.persistence import (
    LockedHaoaiGroupGraph,
    lock_haoai_group_graph,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
SnapshotReader = Callable[..., VerifiedHaoaiGroupExecutionInput | None]

_OPERATION = "video.single"
_TASK_TYPE = "video-single"
_ORIGINAL_DEADLINE_SECONDS = HAOAI_ATTEMPT_GROUP.original_deadline_seconds
_QUERY_INTERVAL_SECONDS = 5
_CLAIMABLE_STATUS = frozenset({"processing", "reconciling"})
_RECONCILING_RESULT = json.dumps(
    {"message": "视频生成结果需要恢复确认", "status": "reconciling"},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)


@dataclass(frozen=True, slots=True)
class HaoaiGroupIntentPreparation:
    disposition: Literal[
        "committed",
        "resume_query",
        "resume_media",
        "existing",
        "unresolved",
        "ownership_lost",
    ]
    verified_input: VerifiedHaoaiGroupExecutionInput | None = None
    intent: CommittedIntent | None = None
    external_ref: ExternalOperationRef | None = None


@dataclass(frozen=True, slots=True)
class _StagedIntent:
    binding: PersistedHaoaiGroupExecutionBinding
    verified_input: VerifiedHaoaiGroupExecutionInput
    intent: CommittedIntent
    submission_key: str
    request_snapshot: dict[str, object]


@dataclass(frozen=True, slots=True)
class _StagedQueryResume:
    binding: PersistedHaoaiGroupExecutionBinding
    verified_input: VerifiedHaoaiGroupExecutionInput
    intent: CommittedIntent
    external_ref: ExternalOperationRef
    submission: dict[str, object]
    request_snapshot: dict[str, object]
    media_recovery: bool


@dataclass(slots=True)
class SqlAlchemyHaoaiGroupIntents:
    """Commit each Group step intent once and confirm it in a fresh session."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory = field(default_factory=lambda: secrets.token_urlsafe)
    registry: CredentialRegistry | None = None
    crypto: CredentialCrypto | None = None
    proof_authority: HmacProofAuthority | None = None
    snapshot_reader: SnapshotReader = read_original_haoai_group_execution_input

    def prepare(self, claim: ClaimAuthority) -> HaoaiGroupIntentPreparation:
        if not self._claim_shape_is_valid(claim):
            return HaoaiGroupIntentPreparation("ownership_lost")
        resumed = self._prepare_existing_query(claim)
        if resumed is not None:
            return resumed
        staged = self._stage_and_commit(claim)
        if staged is None:
            return HaoaiGroupIntentPreparation("unresolved")
        confirmed = self._confirm_intent(claim, staged)
        if confirmed is None:
            return HaoaiGroupIntentPreparation("unresolved")
        return HaoaiGroupIntentPreparation(
            "committed", staged.verified_input, confirmed
        )

    def record_submission(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedHaoaiGroupExecutionInput,
        external_ref: ExternalOperationRef,
    ) -> Literal["recorded", "pending", "ownership_lost"]:
        """Persist a returned ID, or seal an unknown create without reposting."""

        if not _valid_external_operation_ref(intent, verified, external_ref):
            return "pending"
        unknown_create = (
            external_ref.create_outcome == "unknown"
            and external_ref.external_id is None
        )
        if external_ref.create_outcome != "submitted" and not unknown_create:
            return "pending"
        written = False
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    fresh_now = self._server_now()
                    if (
                        graph is None
                        or not _claim_is_current(graph.task, claim, fresh_now)
                        or not _intent_matches_graph(graph, claim, intent, verified)
                    ):
                        return "ownership_lost"
                    rows = _current_submission_rows(graph)
                    if len(rows) != 1:
                        return "pending"
                    row = rows[0]
                    desired_status = "unknown" if unknown_create else "submitted"
                    desired_external_id = None if unknown_create else external_ref.external_id
                    if _submission_outcome_matches(
                        graph,
                        row,
                        intent,
                        verified,
                        desired_status,
                        desired_external_id,
                    ):
                        written = True
                    elif (
                        row.get("submission_status") != "submitting"
                        or row.get("external_task_id") is not None
                        or row.get("submitted_at") is not None
                    ):
                        return "pending"
                    else:
                        changed = session.execute(
                            update(ExternalSubmissionRow.__table__)
                            .where(
                                ExternalSubmissionRow.__table__.c.id
                                == intent.external_submission_id,
                                ExternalSubmissionRow.__table__.c.step_id
                                == intent.step_id,
                                ExternalSubmissionRow.__table__.c.submission_key
                                == haoai_group_submission_key(
                                    verified.unit_id, intent.step_id
                                ),
                                ExternalSubmissionRow.__table__.c.provider == "haoai",
                                ExternalSubmissionRow.__table__.c.model_ref
                                == intent.model_id,
                                ExternalSubmissionRow.__table__.c.request_digest
                                == intent.request_digest,
                                ExternalSubmissionRow.__table__.c.submission_status
                                == "submitting",
                                ExternalSubmissionRow.__table__.c.external_task_id.is_(None),
                                ExternalSubmissionRow.__table__.c.submitted_at.is_(None),
                                ExternalSubmissionRow.__table__.c.intent_at
                                == _database_datetime(intent.committed_at),
                                ExternalSubmissionRow.__table__.c.deadline_at
                                == _database_datetime(intent.deadline_at),
                            )
                            .values(
                                submission_status=desired_status,
                                external_task_id=desired_external_id,
                                submitted_at=_database_datetime(fresh_now),
                                updated_at=_database_datetime(fresh_now),
                            )
                        )
                        if changed.rowcount != 1:
                            raise RuntimeError("HaoAI Group submission result CAS lost")
                        if unknown_create:
                            self._mark_create_unknown(
                                session, graph, claim, intent, fresh_now
                            )
                        else:
                            step_update = session.execute(
                                update(ExecutionStepRow.__table__)
                                .where(
                                    ExecutionStepRow.__table__.c.id == intent.step_id,
                                    ExecutionStepRow.__table__.c.billing_unit_id
                                    == verified.unit_id,
                                    ExecutionStepRow.__table__.c.execution_status
                                    == "submitting",
                                )
                                .values(
                                    execution_status="submitted",
                                    updated_at=_database_datetime(fresh_now),
                                )
                            )
                            if step_update.rowcount != 1:
                                raise RuntimeError("HaoAI Group step result CAS lost")
                            group_table = graph_group_table()
                            group_update = session.execute(
                                update(group_table)
                                .where(
                                    group_table.c.unit_id == verified.unit_id,
                                    group_table.c.group_key
                                    == HAOAI_ATTEMPT_GROUP.group_key,
                                    group_table.c.active_attempt
                                    == verified.attempt_index,
                                    group_table.c.status == "active",
                                    group_table.c.outstanding_reservation_ref.is_(None),
                                    group_table.c.sticky_policy_stop_event_ref.is_(None),
                                )
                                .values(
                                    next_poll_at=_database_datetime(
                                        fresh_now
                                        + timedelta(seconds=_QUERY_INTERVAL_SECONDS)
                                    )
                                )
                            )
                            if group_update.rowcount != 1:
                                raise RuntimeError("HaoAI Group first poll schedule CAS lost")
                        written = True
        except Exception:
            # The same intent and row are checked below after an uncertain commit.
            pass
        if not written:
            return "pending"
        return self._confirm_submission_outcome(
            claim,
            intent,
            verified,
            status="unknown" if unknown_create else "submitted",
            external_id=None if unknown_create else external_ref.external_id,
        )

    def _mark_create_unknown(
        self,
        session: Session,
        graph: LockedHaoaiGroupGraph,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        now: datetime,
    ) -> None:
        task_table = AITaskRow.__table__
        task = graph.task
        changed_task = session.execute(
            update(task_table)
            .where(
                task_table.c.id == claim.scope.task_id,
                task_table.c.user_id == claim.scope.user_id,
                task_table.c.type == _TASK_TYPE,
                task_table.c.status == task.get("status"),
                task_table.c.status.in_(_CLAIMABLE_STATUS),
                task_table.c.claimed_by == claim.worker_instance,
                task_table.c.claim_token == claim.token,
                task_table.c.execution_generation == claim.execution_generation,
                task_table.c.user_cancelled_at.is_(None),
                task_table.c.request_data == task.get("request_data"),
                task_table.c.result == task.get("result"),
            )
            .values(
                status="reconciling",
                recovery_status="reconciling",
                result=_RECONCILING_RESULT,
                progress_message="视频任务需要恢复确认",
                updated_at=_database_datetime(now),
            )
        )
        unit_table = BillingUnitRow.__table__
        changed_unit = session.execute(
            update(unit_table)
            .where(
                unit_table.c.id == graph.binding.unit_id,
                unit_table.c.task_id == graph.binding.task_id,
                unit_table.c.operation == _OPERATION,
                unit_table.c.input_digest == intent.request_digest,
                unit_table.c.result_status == "pending",
                unit_table.c.billing_status.in_(("charged", "no_charge")),
                unit_table.c.user_cancelled_at.is_(None),
                unit_table.c.success_confirmed_at.is_(None),
                unit_table.c.first_intent_at == _database_datetime(intent.first_intent_at),
                unit_table.c.deadline_at == _database_datetime(intent.deadline_at),
                unit_table.c.refunded_amount == 0,
            )
            .values(result_status="unknown", updated_at=_database_datetime(now))
        )
        step_table = ExecutionStepRow.__table__
        changed_step = session.execute(
            update(step_table)
            .where(
                step_table.c.id == intent.step_id,
                step_table.c.billing_unit_id == graph.binding.unit_id,
                step_table.c.execution_status == "submitting",
            )
            .values(
                execution_status="reconciling",
                updated_at=_database_datetime(now),
            )
        )
        group_table = graph_group_table()
        changed_group = session.execute(
            update(group_table)
            .where(
                group_table.c.unit_id == graph.binding.unit_id,
                group_table.c.group_key == HAOAI_ATTEMPT_GROUP.group_key,
                group_table.c.active_attempt == graph.binding.attempt_index,
                group_table.c.status == "active",
                group_table.c.outstanding_reservation_ref.is_(None),
                group_table.c.sticky_policy_stop_event_ref.is_(None),
            )
            .values(status="pending_verification")
        )
        if (
            changed_task.rowcount != 1
            or changed_unit.rowcount != 1
            or changed_step.rowcount != 1
            or changed_group.rowcount != 1
        ):
            raise RuntimeError("HaoAI unknown create could not be fenced")

    def _confirm_submission_outcome(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedHaoaiGroupExecutionInput,
        *,
        status: Literal["unknown", "submitted"],
        external_id: str | None,
    ) -> Literal["recorded", "pending", "ownership_lost"]:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    now = self._server_now()
                    if graph is None or not _claim_is_current(graph.task, claim, now):
                        return "ownership_lost"
                    if not _intent_matches_graph(graph, claim, intent, verified):
                        return "pending"
                    rows = _current_submission_rows(graph)
                    if len(rows) != 1 or not _submission_outcome_matches(
                        graph, rows[0], intent, verified, status, external_id
                    ):
                        return "pending"
                    if status == "unknown" and not all(
                        (
                            graph.task.get("status") == "reconciling",
                            graph.task.get("result") == _RECONCILING_RESULT,
                            graph.unit.get("result_status") == "unknown",
                            graph.steps[graph.binding.attempt_index].get(
                                "execution_status"
                            )
                            == "reconciling",
                            graph.group.get("status") == "pending_verification",
                        )
                    ):
                        return "pending"
                    if status == "submitted" and graph.steps[
                        graph.binding.attempt_index
                    ].get("execution_status") != "submitted":
                        return "pending"
                    if status == "submitted" and not _time_matches(
                        graph.group.get("next_poll_at"),
                        _aware_utc(rows[0]["submitted_at"])
                        + timedelta(seconds=_QUERY_INTERVAL_SECONDS),
                    ):
                        return "pending"
                    return "recorded"
        except Exception:
            return "pending"

    def _prepare_existing_query(
        self,
        claim: ClaimAuthority,
    ) -> HaoaiGroupIntentPreparation | None:
        staged: _StagedQueryResume | None = None
        found_current = False
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    now = self._server_now()
                    if graph is None or not _claim_is_current(graph.task, claim, now):
                        return HaoaiGroupIntentPreparation("ownership_lost")
                    current_rows = _current_submission_rows(graph)
                    if not current_rows:
                        return None
                    found_current = True
                    if len(current_rows) != 1:
                        return HaoaiGroupIntentPreparation("unresolved")
                    staged = self._stage_query_resume(session, graph, current_rows[0], now)
        except Exception:
            return HaoaiGroupIntentPreparation("unresolved")
        if not found_current:
            return None
        if staged is None:
            return HaoaiGroupIntentPreparation("unresolved")
        if not self._confirm_query_resume(claim, staged):
            return HaoaiGroupIntentPreparation("unresolved")
        return HaoaiGroupIntentPreparation(
            "resume_media" if staged.media_recovery else "resume_query",
            staged.verified_input,
            staged.intent,
            staged.external_ref,
        )

    def _stage_query_resume(
        self,
        session: Session,
        graph: LockedHaoaiGroupGraph,
        row: dict[str, object],
        now: datetime,
    ) -> _StagedQueryResume | None:
        if not _known_id_submission_is_valid(graph, row, now):
            return None
        verified = self._read_verified(session, graph.binding, "query")
        if verified is None:
            return None
        intent = _intent_from_existing_submission(graph, row, verified)
        if intent is None:
            return None
        request_snapshot = _safe_intent_snapshot(graph, verified)
        if row.get("request_snapshot") != request_snapshot:
            return None
        external_ref = ExternalOperationRef(
            intent_id=intent.external_submission_id,
            attempt_id=intent.attempt_id,
            provider=intent.provider,
            external_id=str(row["external_task_id"]),
            create_outcome="submitted",
        )
        return _StagedQueryResume(
            binding=graph.binding,
            verified_input=verified,
            intent=intent,
            external_ref=external_ref,
            submission=dict(row),
            request_snapshot=request_snapshot,
            media_recovery=(
                graph.steps[graph.binding.attempt_index].get("execution_status")
                == "result_observed"
            ),
        )

    def _confirm_query_resume(
        self,
        claim: ClaimAuthority,
        staged: _StagedQueryResume,
    ) -> bool:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    now = self._server_now()
                    if (
                        graph is None
                        or not _claim_is_current(graph.task, claim, now)
                        or graph.binding != staged.binding
                        or len(_current_submission_rows(graph)) != 1
                        or _current_submission_rows(graph)[0] != staged.submission
                        or not _known_id_submission_is_valid(
                            graph, staged.submission, now
                        )
                        or staged.media_recovery
                        != (
                            graph.steps[graph.binding.attempt_index].get(
                                "execution_status"
                            )
                            == "result_observed"
                        )
                        or staged.external_ref.intent_id
                        != staged.intent.external_submission_id
                        or staged.external_ref.attempt_id != staged.intent.attempt_id
                        or staged.external_ref.provider != "haoai"
                        or staged.external_ref.external_id
                        != staged.submission.get("external_task_id")
                        or staged.external_ref.create_outcome != "submitted"
                        or staged.submission.get("request_snapshot")
                        != staged.request_snapshot
                        or staged.request_snapshot != _safe_intent_snapshot(
                            graph, staged.verified_input
                        )
                    ):
                        return False
                    current = _intent_from_existing_submission(
                        graph, staged.submission, staged.verified_input
                    )
                    return current == staged.intent
        except Exception:
            return False

    def _stage_and_commit(self, claim: ClaimAuthority) -> _StagedIntent | None:
        staged: _StagedIntent | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    if graph is None:
                        return None
                    now = self._server_now()
                    if not _claim_is_current(graph.task, claim, now):
                        return None
                    if not _initial_attempt_can_start(
                        session, graph, now, self.proof_authority
                    ):
                        return None
                    verified = self._read_verified(session, graph.binding, "create")
                    if verified is None:
                        return None
                    if not _claim_is_current(graph.task, claim, self._server_now()):
                        return None

                    now = self._server_now()
                    if graph.binding.attempt_index == 0:
                        first_intent_at = now
                        deadline_at = now + timedelta(
                            seconds=_ORIGINAL_DEADLINE_SECONDS
                        )
                    else:
                        first_intent_at = _aware_utc(graph.unit["first_intent_at"])
                        deadline_at = _aware_utc(graph.unit["deadline_at"])
                    submission_key = haoai_group_submission_key(
                        graph.binding.unit_id, graph.binding.step_id
                    )
                    try:
                        submission_id = self.id_factory()
                    except Exception:
                        return None
                    if not _nonempty(submission_id) or len(submission_id) > 255:
                        return None
                    intent = CommittedIntent(
                        step_id=graph.binding.step_id,
                        attempt_id=graph.binding.step_id,
                        external_submission_id=submission_id,
                        provider="haoai",
                        model_id=verified.snapshot.model_ref.model_id,
                        request_digest=graph.binding.request_digest,
                        snapshot_ref=_execution_snapshot_ref(graph.binding),
                        committed_at=now,
                        first_intent_at=first_intent_at,
                        deadline_at=deadline_at,
                        provider_idempotency_key=submission_key,
                    )
                    request_snapshot = _safe_intent_snapshot(graph, verified)
                    staged = _StagedIntent(
                        binding=graph.binding,
                        verified_input=verified,
                        intent=intent,
                        submission_key=submission_key,
                        request_snapshot=request_snapshot,
                    )

                    task_table = AITaskRow.__table__
                    task_fence = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == claim.scope.task_id,
                            task_table.c.user_id == claim.scope.user_id,
                            task_table.c.type == _TASK_TYPE,
                            task_table.c.status == graph.task.get("status"),
                            task_table.c.status.in_(_CLAIMABLE_STATUS),
                            task_table.c.claimed_by == claim.worker_instance,
                            task_table.c.claim_token == claim.token,
                            task_table.c.execution_generation
                            == claim.execution_generation,
                            task_table.c.user_cancelled_at.is_(None),
                            task_table.c.lease_until
                            == _database_datetime(_aware_utc(graph.task["lease_until"])),
                            task_table.c.lease_until > _database_datetime(now),
                            task_table.c.request_data == graph.task.get("request_data"),
                        )
                        .values(updated_at=task_table.c.updated_at)
                    )
                    if task_fence.rowcount != 1:
                        return None

                    group_table = graph_group_table()
                    group_predicates = [
                        group_table.c.unit_id == graph.binding.unit_id,
                        group_table.c.group_key == HAOAI_ATTEMPT_GROUP.group_key,
                        group_table.c.active_attempt == graph.binding.attempt_index,
                        group_table.c.active_eligibility_version
                        == HAOAI_ATTEMPT_GROUP.registry_version,
                        group_table.c.sticky_policy_stop_event_ref.is_(None),
                        group_table.c.outstanding_reservation_ref.is_(None),
                    ]
                    group_values: dict[str, object]
                    if graph.binding.attempt_index == 0:
                        group_predicates.append(group_table.c.status == "prepared")
                        group_values = {"status": "active"}
                    else:
                        group_predicates.extend(
                            (
                                group_table.c.status == "active",
                                group_table.c.next_poll_at
                                <= _database_datetime(now),
                            )
                        )
                        group_values = {"next_poll_at": None}
                    group_update = session.execute(
                        update(group_table).where(*group_predicates).values(**group_values)
                    )
                    if group_update.rowcount != 1:
                        raise RuntimeError("HaoAI Group changed before intent commit")

                    session.execute(
                        insert(ExternalSubmissionRow.__table__).values(
                            id=submission_id,
                            step_id=graph.binding.step_id,
                            submission_key=submission_key,
                            provider="haoai",
                            model_ref=intent.model_id,
                            request_digest=intent.request_digest,
                            request_snapshot=request_snapshot,
                            intent_at=_database_datetime(now),
                            submitted_at=None,
                            external_task_id=None,
                            submission_status="submitting",
                            deadline_at=_database_datetime(deadline_at),
                            created_at=_database_datetime(now),
                            updated_at=_database_datetime(now),
                        )
                    )
                    step_update = session.execute(
                        update(ExecutionStepRow.__table__)
                        .where(
                            ExecutionStepRow.__table__.c.id == graph.binding.step_id,
                            ExecutionStepRow.__table__.c.billing_unit_id
                            == graph.binding.unit_id,
                            ExecutionStepRow.__table__.c.execution_status == "prepared",
                        )
                        .values(
                            execution_status="submitting",
                            updated_at=_database_datetime(now),
                        )
                    )
                    unit_table = BillingUnitRow.__table__
                    unit_predicates = [
                            unit_table.c.id == graph.binding.unit_id,
                            unit_table.c.task_id == graph.binding.task_id,
                            unit_table.c.operation == _OPERATION,
                            unit_table.c.step_graph_version
                            == HAOAI_ATTEMPT_GROUP.registry_version,
                            unit_table.c.item_key == graph.binding.server_attempt_id,
                            unit_table.c.input_digest == graph.binding.request_digest,
                            unit_table.c.user_cancelled_at.is_(None),
                            unit_table.c.success_confirmed_at.is_(None),
                            unit_table.c.billing_status.in_(("charged", "no_charge")),
                            unit_table.c.result_status == "pending",
                    ]
                    if graph.binding.attempt_index == 0:
                        unit_predicates.extend(
                            (
                                unit_table.c.first_intent_at.is_(None),
                                unit_table.c.deadline_at.is_(None),
                            )
                        )
                        unit_values = {
                            "first_intent_at": _database_datetime(first_intent_at),
                            "deadline_at": _database_datetime(deadline_at),
                            "updated_at": _database_datetime(now),
                        }
                    else:
                        unit_predicates.extend(
                            (
                                unit_table.c.first_intent_at
                                == _database_datetime(first_intent_at),
                                unit_table.c.deadline_at
                                == _database_datetime(deadline_at),
                                unit_table.c.refunded_amount == 0,
                            )
                        )
                        unit_values = {"updated_at": _database_datetime(now)}
                    unit_update = session.execute(
                        update(unit_table).where(*unit_predicates).values(**unit_values)
                    )
                    if step_update.rowcount != 1 or unit_update.rowcount != 1:
                        raise RuntimeError("HaoAI Group intent graph changed")
                    session.flush()
        except Exception:
            return staged
        return staged

    def _confirm_intent(
        self,
        claim: ClaimAuthority,
        staged: _StagedIntent,
    ) -> CommittedIntent | None:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    now = self._server_now()
                    rows = _current_submission_rows(graph) if graph is not None else ()
                    if (
                        graph is None
                        or not _claim_is_current(graph.task, claim, now)
                        or graph.binding != staged.binding
                        or graph.group.get("active_attempt")
                        != staged.binding.attempt_index
                        or graph.group.get("status") != "active"
                        or graph.steps[staged.binding.attempt_index].get(
                            "execution_status"
                        )
                        != "submitting"
                        or len(rows) != 1
                        or not _intent_row_matches(rows[0], staged)
                        or not _time_matches(
                            graph.unit.get("first_intent_at"),
                            staged.intent.first_intent_at,
                        )
                        or not _time_matches(
                            graph.unit.get("deadline_at"), staged.intent.deadline_at
                        )
                        or staged.intent.deadline_at
                        != staged.intent.first_intent_at
                        + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS)
                    ):
                        return None
                    return staged.intent
        except Exception:
            return None

    def _read_verified(
        self,
        session: Session,
        binding: PersistedHaoaiGroupExecutionBinding,
        purpose: Literal["create", "query"],
    ) -> VerifiedHaoaiGroupExecutionInput | None:
        if (
            not isinstance(self.registry, CredentialRegistry)
            or not isinstance(self.crypto, CredentialCrypto)
            or not isinstance(self.proof_authority, HmacProofAuthority)
        ):
            return None
        try:
            verified = self.snapshot_reader(
                session,
                binding,
                registry=self.registry,
                crypto=self.crypto,
                proof_authority=self.proof_authority,
                credential_purpose=purpose,
            )
        except Exception:
            return None
        if (
            type(verified) is not VerifiedHaoaiGroupExecutionInput
            or verified.task_id != binding.task_id
            or verified.unit_id != binding.unit_id
            or verified.step_id != binding.step_id
            or verified.attempt_index != binding.attempt_index
            or verified.server_attempt_id != binding.server_attempt_id
            or verified.snapshot.model_ref.provider != "haoai"
        ):
            return None
        return verified

    def _claim_shape_is_valid(self, claim: object) -> bool:
        return bool(
            isinstance(claim, ClaimAuthority)
            and claim.scope.operation == _OPERATION
            and claim.local_lost_signal is not None
            and not claim.local_lost_signal.is_set()
        )

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("database clock must return an aware UTC datetime")
        return value.astimezone(timezone.utc)


def _current_submission_rows(
    graph: LockedHaoaiGroupGraph,
) -> tuple[dict[str, object], ...]:
    return tuple(
        row for row in graph.external_submissions
        if row.get("step_id") == graph.binding.step_id
    )


def _valid_external_operation_ref(
    intent: CommittedIntent,
    verified: VerifiedHaoaiGroupExecutionInput,
    external_ref: ExternalOperationRef,
) -> bool:
    if (
        type(intent) is not CommittedIntent
        or type(verified) is not VerifiedHaoaiGroupExecutionInput
        or type(external_ref) is not ExternalOperationRef
        or verified.snapshot.model_ref.provider != "haoai"
        or intent.provider != "haoai"
        or intent.model_id != verified.snapshot.model_ref.model_id
        or intent.step_id != verified.step_id
        or intent.attempt_id != verified.step_id
        or intent.request_digest == ""
        or external_ref.intent_id != intent.external_submission_id
        or external_ref.attempt_id != intent.attempt_id
        or external_ref.provider != "haoai"
    ):
        return False
    if external_ref.create_outcome == "submitted":
        external_id = external_ref.external_id
        secret = verified.provider_credential.secret
        return bool(
            _safe_provider_id(external_id)
            and isinstance(secret, str)
            and secret
            and secret not in external_id
        )
    return external_ref.create_outcome == "unknown" and external_ref.external_id is None


def _intent_matches_graph(
    graph: LockedHaoaiGroupGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedHaoaiGroupExecutionInput,
) -> bool:
    binding = graph.binding
    if (
        type(intent) is not CommittedIntent
        or type(verified) is not VerifiedHaoaiGroupExecutionInput
        or verified.task_id != binding.task_id
        or verified.unit_id != binding.unit_id
        or verified.step_id != binding.step_id
        or verified.attempt_index != binding.attempt_index
        or verified.server_attempt_id != binding.server_attempt_id
        or verified.snapshot.model_ref.provider != "haoai"
        or intent.step_id != binding.step_id
        or intent.attempt_id != binding.step_id
        or intent.provider != "haoai"
        or intent.model_id != verified.snapshot.model_ref.model_id
        or intent.request_digest != binding.request_digest
        or intent.snapshot_ref != _execution_snapshot_ref(binding)
        or intent.provider_idempotency_key
        != haoai_group_submission_key(binding.unit_id, binding.step_id)
        or intent.first_intent_at is None
        or not _time_matches(graph.unit.get("first_intent_at"), intent.first_intent_at)
        or not _time_matches(graph.unit.get("deadline_at"), intent.deadline_at)
        or intent.deadline_at
        != intent.first_intent_at + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS)
        or graph.group.get("active_attempt") != binding.attempt_index
        or graph.group.get("status") not in {"active", "pending_verification"}
        or graph.group.get("sticky_policy_stop_event_ref") is not None
        or claim.scope.user_id != graph.owner_id
        or claim.scope.task_id != graph.task_id
    ):
        return False
    return True


def _submission_outcome_matches(
    graph: LockedHaoaiGroupGraph,
    row: dict[str, object],
    intent: CommittedIntent,
    verified: VerifiedHaoaiGroupExecutionInput,
    status: Literal["unknown", "submitted"],
    external_id: str | None,
) -> bool:
    if (
        row.get("id") != intent.external_submission_id
        or row.get("step_id") != intent.step_id
        or row.get("submission_key") != intent.provider_idempotency_key
        or row.get("provider") != "haoai"
        or row.get("model_ref") != intent.model_id
        or row.get("request_digest") != intent.request_digest
        or row.get("submission_status") != status
        or row.get("external_task_id") != external_id
        or not isinstance(row.get("submitted_at"), datetime)
        or row.get("request_snapshot") != _safe_intent_snapshot(graph, verified)
        or not _time_matches(row.get("intent_at"), intent.committed_at)
        or not _time_matches(row.get("deadline_at"), intent.deadline_at)
    ):
        return False
    if status == "submitted":
        return _safe_provider_id(external_id)
    return external_id is None


def _initial_attempt_can_start(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    now: datetime,
    proof_authority: HmacProofAuthority | None,
) -> bool:
    attempt_index = graph.binding.attempt_index
    if attempt_index > 0:
        return _retry_attempt_can_start(
            session, graph, now, proof_authority
        )
    task = graph.task
    unit = graph.unit
    group = graph.group
    current = graph.steps[0]
    return bool(
        graph.binding.attempt_index == 0
        and task.get("status") in _CLAIMABLE_STATUS
        and task.get("user_cancelled_at") is None
        and task.get("billing_status") == unit.get("billing_status")
        and task.get("credit_cost") == unit.get("quoted_amount")
        and task.get("result") is None
        and unit.get("user_cancelled_at") is None
        and unit.get("success_confirmed_at") is None
        and unit.get("refunded_amount") == 0
        and unit.get("billing_status") in {"charged", "no_charge"}
        and unit.get("result_status") == "pending"
        and unit.get("first_intent_at") is None
        and unit.get("deadline_at") is None
        and group.get("status") == "prepared"
        and group.get("active_attempt") == 0
        and group.get("active_eligibility_version")
        == HAOAI_ATTEMPT_GROUP.registry_version
        and group.get("max_retries") == HAOAI_ATTEMPT_GROUP.max_retries
        and group.get("selected_evidence_id") is None
        and group.get("poll_used") == 0
        and group.get("poll_sequence") == 0
        and group.get("auth_unavailable_streak") == 0
        and group.get("next_poll_at") is None
        and group.get("outstanding_reservation_ref") is None
        and group.get("terminal_reason") is None
        and group.get("sticky_policy_stop_event_ref") is None
        and current.get("execution_status") == "prepared"
        and all(step.get("execution_status") == "prepared" for step in graph.steps)
        and not graph.external_submissions
        and now.tzinfo is not None
    )


def _retry_attempt_can_start(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    now: datetime,
    proof_authority: HmacProofAuthority | None,
) -> bool:
    attempt_index = graph.binding.attempt_index
    group = graph.group
    unit = graph.unit
    task = graph.task
    if (
        attempt_index not in {1, 2}
        or attempt_index > HAOAI_ATTEMPT_GROUP.max_retries
        or group.get("status") != "active"
        or group.get("active_attempt") != attempt_index
        or group.get("active_eligibility_version")
        != HAOAI_ATTEMPT_GROUP.registry_version
        or group.get("max_retries") != HAOAI_ATTEMPT_GROUP.max_retries
        or group.get("sticky_policy_stop_event_ref") is not None
        or group.get("outstanding_reservation_ref") is not None
        or group.get("selected_evidence_id") is not None
        or type(group.get("poll_used")) is not int
        or not 0 < group.get("poll_used") <= HAOAI_ATTEMPT_GROUP.max_polls
        or type(group.get("auth_unavailable_streak")) is not int
        or group.get("auth_unavailable_streak") >= 12
        or task.get("status") not in _CLAIMABLE_STATUS
        or task.get("user_cancelled_at") is not None
        or task.get("billing_status") != unit.get("billing_status")
        or task.get("credit_cost") != unit.get("quoted_amount")
        or task.get("result") is not None
        or unit.get("user_cancelled_at") is not None
        or unit.get("success_confirmed_at") is not None
        or unit.get("refunded_amount") != 0
        or unit.get("billing_status") not in {"charged", "no_charge"}
        or unit.get("result_status") != "pending"
        or not isinstance(unit.get("first_intent_at"), datetime)
        or not isinstance(unit.get("deadline_at"), datetime)
        or not isinstance(group.get("next_poll_at"), datetime)
    ):
        return False
    try:
        first_intent = _aware_utc(unit["first_intent_at"])
        deadline = _aware_utc(unit["deadline_at"])
        next_poll = _aware_utc(group["next_poll_at"])
    except (TypeError, ValueError):
        return False
    steps = graph.steps
    if (
        now < next_poll
        or now >= deadline
        or deadline != first_intent + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS)
        or steps[attempt_index].get("execution_status") != "prepared"
        or steps[attempt_index - 1].get("execution_status") != "result_observed"
        or any(
            step.get("execution_status") != "prepared"
            for step in steps[attempt_index + 1 :]
        )
        or len(graph.external_submissions) != attempt_index
    ):
        return False
    prior_step = steps[attempt_index - 1]
    prior_submissions = tuple(
        row
        for row in graph.external_submissions
        if row.get("step_id") == prior_step.get("id")
    )
    if (
        len(prior_submissions) != 1
        or prior_submissions[0].get("submission_status") != "submitted"
        or not _safe_provider_id(prior_submissions[0].get("external_task_id"))
    ):
        return False
    for index, step in enumerate(steps[:attempt_index]):
        rows = tuple(
            row
            for row in graph.external_submissions
            if row.get("step_id") == step.get("id")
        )
        if len(rows) != 1 or rows[0].get("submission_status") != "submitted":
            return False
        if index < attempt_index - 1 and step.get("execution_status") not in {
            "submitted",
            "result_observed",
        }:
            return False
    polls = session.execute(
        select(PollReservationRow.__table__)
        .where(
            PollReservationRow.__table__.c.unit_id == graph.binding.unit_id,
            PollReservationRow.__table__.c.group_key == graph.binding.group_key,
            PollReservationRow.__table__.c.step_id == prior_step.get("id"),
            PollReservationRow.__table__.c.state == "accepted",
        )
        .order_by(PollReservationRow.__table__.c.sequence.desc())
        .limit(1)
    ).mappings().all()
    if len(polls) != 1:
        return False
    poll = polls[0]
    evidence_ref = poll.get("observation_ref")
    if not isinstance(evidence_ref, str):
        return False
    evidence = session.execute(
        select(ResultEvidenceRow.__table__).where(
            ResultEvidenceRow.__table__.c.id == evidence_ref
        )
    ).mappings().one_or_none()
    if evidence is None:
        return False
    prior_submission = prior_submissions[0]
    return is_retryable_reference_failure_observation(
        evidence,
        proof_authority,
        owner_id=graph.owner_id,
        task_id=graph.task_id,
        unit_id=graph.binding.unit_id,
        group_key=graph.binding.group_key,
        step_id=str(prior_step.get("id")),
        submission_id=str(prior_submission.get("id")),
        attempt_id=str(prior_step.get("id")),
        external_id=str(prior_submission.get("external_task_id")),
        request_digest=graph.binding.request_digest,
        reservation_id=str(poll.get("reservation_id")),
        sequence=int(poll.get("sequence")),
    )


def _known_id_submission_is_valid(
    graph: LockedHaoaiGroupGraph,
    row: dict[str, object],
    now: datetime,
) -> bool:
    unit = graph.unit
    group = graph.group
    step = graph.steps[graph.binding.attempt_index]
    intent_at = row.get("intent_at")
    submitted_at = row.get("submitted_at")
    deadline_at = row.get("deadline_at")
    first_intent_at = unit.get("first_intent_at")
    unit_deadline_at = unit.get("deadline_at")
    if (
        row.get("submission_status") not in {"submitted", "unknown"}
        or not _safe_provider_id(row.get("external_task_id"))
        or not all(
            isinstance(value, datetime)
            for value in (intent_at, submitted_at, deadline_at, first_intent_at, unit_deadline_at)
        )
        or not _known_id_shape_is_valid(graph, row, step)
    ):
        return False
    try:
        intent_time = _aware_utc(intent_at)
        submitted_time = _aware_utc(submitted_at)
        deadline = _aware_utc(deadline_at)
        first_intent = _aware_utc(first_intent_at)
        unit_deadline = _aware_utc(unit_deadline_at)
    except (TypeError, ValueError):
        return False
    return all(
        (
            group.get("status") in {"active", "pending_verification"},
            group.get("active_attempt") == graph.binding.attempt_index,
            unit.get("result_status") in {"pending", "unknown"},
            unit.get("success_confirmed_at") is None,
            unit.get("user_cancelled_at") is None,
            row.get("step_id") == step.get("id"),
            intent_time >= first_intent,
            intent_time <= submitted_time < deadline,
            deadline == unit_deadline,
            deadline == first_intent + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS),
            now < deadline,
        )
    )


def _known_id_shape_is_valid(
    graph: LockedHaoaiGroupGraph,
    row: dict[str, object],
    step: dict[str, object],
    *,
    submission_statuses: frozenset[str] = frozenset({"submitted", "unknown"}),
    step_statuses: frozenset[str] = frozenset(
        {"submitted", "reconciling", "result_observed"}
    ),
) -> bool:
    request_snapshot = row.get("request_snapshot")
    return bool(
        graph.binding.attempt_index == graph.group.get("active_attempt")
        and step.get("execution_status") in step_statuses
        and row.get("step_id") == graph.binding.step_id
        and row.get("submission_key")
        == haoai_group_submission_key(graph.binding.unit_id, graph.binding.step_id)
        and row.get("provider") == "haoai"
        and row.get("model_ref") == graph.request_data.get("model_id")
        and row.get("request_digest") == graph.binding.request_digest
        and row.get("submission_status") in submission_statuses
        and _nonempty(row.get("id"))
        and isinstance(request_snapshot, dict)
        and request_snapshot.get("owner_id") == graph.owner_id
        and request_snapshot.get("task_id") == graph.task_id
        and request_snapshot.get("unit_id") == graph.binding.unit_id
        and request_snapshot.get("step_id") == graph.binding.step_id
        and request_snapshot.get("operation") == _OPERATION
        and request_snapshot.get("graph_version")
        == HAOAI_ATTEMPT_GROUP.registry_version
        and request_snapshot.get("attempt_id") == graph.binding.step_id
        and request_snapshot.get("server_attempt_id")
        == graph.binding.server_attempt_id
        and request_snapshot.get("attempt_index") == graph.binding.attempt_index
        and request_snapshot.get("prepared_payload_id")
        == graph.binding.prepared_payload_id
        and request_snapshot.get("protected_ref") == graph.binding.protected_ref
        and request_snapshot.get("snapshot_digest") == graph.binding.snapshot_digest
        and request_snapshot.get("snapshot_ref") == _execution_snapshot_ref(graph.binding)
        and request_snapshot.get("provider") == "haoai"
        and request_snapshot.get("model_id") == graph.binding.task_model_id
        and _nonempty(request_snapshot.get("credential_binding_ref"))
        and request_snapshot.get("target_snapshot")
        == graph.request_data.get("target_snapshot")
    )


def is_valid_started_group_submission(
    graph: LockedHaoaiGroupGraph,
    row: dict[str, object],
    now: datetime,
) -> bool:
    """Verify the active immutable intent even when its create result is unknown."""

    attempt_index = graph.binding.attempt_index
    if (
        type(attempt_index) is not int
        or not 0 <= attempt_index < len(graph.steps)
        or graph.group.get("status")
        not in {"active", "pending_verification", "cancelled"}
        or graph.unit.get("result_status") not in {"pending", "unknown"}
        or graph.unit.get("success_confirmed_at") is not None
        or (
            graph.unit.get("user_cancelled_at") is not None
            and graph.group.get("status") != "cancelled"
        )
        or graph.unit.get("refunded_amount") != 0
    ):
        return False

    status = row.get("submission_status")
    if status == "submitting":
        status_pair_is_valid = bool(
            row.get("external_task_id") is None
            and row.get("submitted_at") is None
            and graph.steps[attempt_index].get("execution_status") == "submitting"
        )
        submission_statuses = frozenset({"submitting"})
        step_statuses = frozenset({"submitting"})
    elif status == "unknown":
        status_pair_is_valid = bool(
            row.get("external_task_id") is None
            and isinstance(row.get("submitted_at"), datetime)
            and graph.steps[attempt_index].get("execution_status") == "reconciling"
        )
        submission_statuses = frozenset({"unknown"})
        step_statuses = frozenset({"reconciling"})
    elif status == "submitted":
        status_pair_is_valid = bool(
            _safe_provider_id(row.get("external_task_id"))
            and isinstance(row.get("submitted_at"), datetime)
            and graph.steps[attempt_index].get("execution_status")
            in {"submitted", "reconciling", "result_observed"}
        )
        submission_statuses = frozenset({"submitted"})
        step_statuses = frozenset(
            {"submitted", "reconciling", "result_observed"}
        )
    else:
        return False

    first_intent_at = graph.unit.get("first_intent_at")
    unit_deadline_at = graph.unit.get("deadline_at")
    intent_at = row.get("intent_at")
    deadline_at = row.get("deadline_at")
    if (
        not status_pair_is_valid
        or not _known_id_shape_is_valid(
            graph,
            row,
            graph.steps[attempt_index],
            submission_statuses=submission_statuses,
            step_statuses=step_statuses,
        )
        or not all(
            isinstance(value, datetime)
            for value in (first_intent_at, unit_deadline_at, intent_at, deadline_at, now)
        )
    ):
        return False
    try:
        first_intent = _aware_utc(first_intent_at)
        unit_deadline = _aware_utc(unit_deadline_at)
        intent_time = _aware_utc(intent_at)
        deadline = _aware_utc(deadline_at)
        current_time = _aware_utc(now)
        submitted_time = (
            _aware_utc(row["submitted_at"])
            if isinstance(row.get("submitted_at"), datetime)
            else None
        )
    except (KeyError, TypeError, ValueError):
        return False

    return bool(
        first_intent <= intent_time <= current_time < deadline
        and deadline == unit_deadline
        and deadline
        == first_intent + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS)
        and (
            status == "submitting"
            or (
                submitted_time is not None
                and intent_time <= submitted_time <= current_time
                and submitted_time < deadline
            )
        )
    )


def _intent_from_existing_submission(
    graph: LockedHaoaiGroupGraph,
    row: dict[str, object],
    verified: VerifiedHaoaiGroupExecutionInput,
) -> CommittedIntent | None:
    intent_at = row.get("intent_at")
    deadline_at = row.get("deadline_at")
    first_intent_at = graph.unit.get("first_intent_at")
    submitted_at = row.get("submitted_at")
    if not all(
        isinstance(value, datetime)
        for value in (intent_at, deadline_at, first_intent_at, submitted_at)
    ):
        return None
    try:
        committed_at = _aware_utc(intent_at)
        deadline = _aware_utc(deadline_at)
        first_intent = _aware_utc(first_intent_at)
        submitted = _aware_utc(submitted_at)
    except (TypeError, ValueError):
        return None
    if (
        row.get("id") is None
        or row.get("step_id") != graph.binding.step_id
        or row.get("submission_key")
        != haoai_group_submission_key(graph.binding.unit_id, graph.binding.step_id)
        or row.get("provider") != "haoai"
        or row.get("model_ref") != verified.snapshot.model_ref.model_id
        or row.get("request_digest") != graph.binding.request_digest
        or deadline != first_intent + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS)
        or not first_intent <= committed_at <= submitted < deadline
    ):
        return None
    return CommittedIntent(
        step_id=graph.binding.step_id,
        attempt_id=graph.binding.step_id,
        external_submission_id=str(row["id"]),
        provider="haoai",
        model_id=verified.snapshot.model_ref.model_id,
        request_digest=graph.binding.request_digest,
        snapshot_ref=_execution_snapshot_ref(graph.binding),
        committed_at=committed_at,
        first_intent_at=first_intent,
        deadline_at=deadline,
        provider_idempotency_key=haoai_group_submission_key(
            graph.binding.unit_id, graph.binding.step_id
        ),
    )


def _safe_intent_snapshot(
    graph: LockedHaoaiGroupGraph,
    verified: VerifiedHaoaiGroupExecutionInput,
) -> dict[str, object]:
    target = graph.request_data.get("target_snapshot")
    if not isinstance(target, dict):
        raise ValueError("original target snapshot is unavailable")
    return {
        "owner_id": graph.owner_id,
        "task_id": graph.task_id,
        "unit_id": graph.binding.unit_id,
        "step_id": graph.binding.step_id,
        "operation": _OPERATION,
        "graph_version": HAOAI_ATTEMPT_GROUP.registry_version,
        "attempt_id": graph.binding.step_id,
        "server_attempt_id": graph.binding.server_attempt_id,
        "attempt_index": graph.binding.attempt_index,
        "prepared_payload_id": graph.binding.prepared_payload_id,
        "protected_ref": graph.binding.protected_ref,
        "snapshot_digest": graph.binding.snapshot_digest,
        "snapshot_ref": _execution_snapshot_ref(graph.binding),
        "credential_binding_ref": verified.snapshot.credential_binding_ref,
        "target_snapshot": target,
        "provider": verified.snapshot.model_ref.provider,
        "model_id": verified.snapshot.model_ref.model_id,
    }


def _intent_row_matches(
    row: dict[str, object],
    staged: _StagedIntent,
) -> bool:
    intent = staged.intent
    return all(
        (
            row.get("id") == intent.external_submission_id,
            row.get("step_id") == intent.step_id,
            row.get("submission_key") == staged.submission_key,
            row.get("provider") == intent.provider,
            row.get("model_ref") == intent.model_id,
            row.get("request_digest") == intent.request_digest,
            _time_matches(row.get("intent_at"), intent.committed_at),
            _time_matches(row.get("deadline_at"), intent.deadline_at),
            row.get("submission_status") == "submitting",
            row.get("submitted_at") is None,
            row.get("external_task_id") is None,
            row.get("request_snapshot") == staged.request_snapshot,
        )
    )


def _execution_snapshot_ref(binding: PersistedHaoaiGroupExecutionBinding) -> str:
    return (
        f"{HAOAI_ATTEMPT_GROUP.registry_version}:{binding.prepared_payload_id}:"
        f"{binding.snapshot_digest}:{binding.ordered_manifest_digest}"
    )


def haoai_group_submission_key(unit_id: str, step_id: str) -> str:
    if not _nonempty(unit_id) or not _nonempty(step_id):
        raise ValueError("Group submission identity is unavailable")
    return f"{HAOAI_ATTEMPT_GROUP.registry_version}:{unit_id}:{step_id}"


def graph_group_table():
    return ExecutionAttemptGroupRow.__table__


def _claim_is_current(
    task: dict[str, object], claim: ClaimAuthority, now: datetime
) -> bool:
    lease = task.get("lease_until")
    return bool(
        task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
        and claim.scope.operation == _OPERATION
        and task.get("status") in _CLAIMABLE_STATUS
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and task.get("user_cancelled_at") is None
        and isinstance(lease, datetime)
        and _aware_utc(lease) > now
        and not claim.local_lost_signal.is_set()
    )


def _safe_provider_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value.encode("utf-8")) <= 255
        and not any(ord(character) < 0x20 for character in value)
    )


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _time_matches(value: object, expected: datetime) -> bool:
    return isinstance(value, datetime) and _aware_utc(value) == expected


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("persisted Group intent time is invalid")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


__all__ = (
    "HaoaiGroupIntentPreparation",
    "SqlAlchemyHaoaiGroupIntents",
    "haoai_group_submission_key",
)
