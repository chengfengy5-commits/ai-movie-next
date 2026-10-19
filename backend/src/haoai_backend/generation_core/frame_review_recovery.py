"""Persist trusted frame.review request observations and settle expired intents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from threading import Event, RLock
from typing import Literal

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    OwnerTaskScope,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    TaskSubmissionRow,
    UserRow,
)
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    ExecutionAttemptGroupRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    PolicyStopEventRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_core.frame_review_cancellation import (
    CancelOutcome,
    SqlAlchemyFrameReviewCancellation,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.frame_review_snapshot_reader import (
    PersistedFrameReviewExecutionBinding,
    VerifiedFrameReviewExecutionInput,
    read_original_frame_review_execution_input,
)

_TASK_TYPE = "ai-review"
_OPERATION = "frame.review"
_GRAPH_VERSION = "frame-review-v1"
_STEP_KEY = "generate"
_INTENT_KEY_PREFIX = "frame-review-generate-v1:"
_OBSERVATION_KEY_PREFIX = "frame-review-observation-v1:"
_OBSERVATION_TEXT_PREFIX = "frame-review-observation-v1:"
_ORIGINAL_DEADLINE_SECONDS = 7200
_MAX_PAGE_SIZE = 32
_ACTIVE_TASK_STATUSES = ("queued", "processing", "reconciling")

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
class _StageConflict(RuntimeError):
    """A compare-and-set changed after the graph was read; roll back all writes."""


@dataclass(frozen=True, slots=True)
class _ProviderObservation:
    """Private facts emitted only by the registered executor boundary."""

    kind: Literal["known_not_started", "provider_rejected", "outcome_unknown"]
    stage: Literal["before_request", "request"]
    code: str
    request_started: bool | None
    http_status: int | None


@dataclass(frozen=True, slots=True)
class _Candidate:
    owner_id: str
    task_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _ReviewGraph:
    task: dict[str, object]
    unit: dict[str, object]
    step: dict[str, object]
    parent: dict[str, object]
    external: dict[str, object]
    request_data: dict[str, object]
    binding: PersistedFrameReviewExecutionBinding
    verified: VerifiedFrameReviewExecutionInput
    intent: CommittedIntent


@dataclass(frozen=True, slots=True)
class _ObservationStage:
    owner_id: str
    task_id: str
    unit_id: str
    step_id: str
    submission_id: str
    attempt_id: str
    request_digest: str
    kind: str
    evidence_id: str
    evidence_key: str
    payload_text: str
    payload_digest: str
    evidence_observed_at: datetime
    observed_at: datetime
    task_result: str | None
    task_progress_message: str
    task_status: str
    unit_status: str
    step_status: str
    submission_status: str
    execution_generation: int


@dataclass(slots=True)
class SqlAlchemyFrameReviewRecovery:
    """Own one small SQL adapter for observation writes and bounded due recovery."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    cancellation: SqlAlchemyFrameReviewCancellation | None = None
    page_size: int = _MAX_PAGE_SIZE
    _cursor: tuple[datetime, str] | None = None
    _scan_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not callable(self.session_factory)
            or not callable(self.db_clock)
            or not callable(self.id_factory)
            or type(self.crypto) is not CredentialCrypto
            or type(self.proof_authority) is not HmacProofAuthority
            or type(self.page_size) is not int
            or not 1 <= self.page_size <= _MAX_PAGE_SIZE
        ):
            raise ValueError("frame.review recovery dependencies must be explicit")
        if self.cancellation is None:
            self.cancellation = SqlAlchemyFrameReviewCancellation(
                self.session_factory,
                self.db_clock,
                self.id_factory,
                self.crypto,
                self.proof_authority,
            )
        elif not isinstance(self.cancellation, SqlAlchemyFrameReviewCancellation):
            raise ValueError("frame.review cancellation adapter must be explicit")

    def record_observation(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedFrameReviewExecutionInput,
        observation: _ProviderObservation,
    ) -> Literal["pending", "ownership_lost"]:
        """Persist one request fact with its task projection in the same transaction."""

        if not _valid_observation(observation):
            return "pending"
        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != _OPERATION
            or claim.local_lost_signal.is_set()
            or type(intent) is not CommittedIntent
            or type(verified) is not VerifiedFrameReviewExecutionInput
        ):
            return "ownership_lost"

        staged: list[_ObservationStage] = []
        try:
            outcome = self._record_observation_transaction(
                claim, intent, verified, observation, staged
            )
        except Exception:
            outcome = "pending"
        if outcome != "pending" or not staged:
            return "pending" if outcome == "failed" else outcome
        self._confirm_observation(staged[0])
        return "pending"

    def scan_due(
        self, *, stop_signal: Event | None = None
    ) -> tuple[Literal["settled", "retained", "pending", "terminal_projection"], ...]:
        """Inspect at most 32 registered due candidates and check stop between writes."""

        if _is_stopping(stop_signal):
            return ()
        now = self._server_now()
        if now is None:
            return ("pending",)
        with self._scan_lock:
            try:
                candidates = self._next_page(now)
            except Exception:
                return ("pending",)
            outcomes: list[Literal["settled", "retained", "pending", "terminal_projection"]] = []
            for candidate in candidates:
                if _is_stopping(stop_signal):
                    break
                scope = OwnerTaskScope(
                    user_id=candidate.owner_id,
                    task_id=candidate.task_id,
                    operation=_OPERATION,
                )
                outcomes.append(self.consume_cancel_and_due(scope, stop_signal=stop_signal))
            return tuple(outcomes)

    def consume_cancel_and_due(
        self,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> CancelOutcome:
        """Settle a persisted cancellation first, then preserve the original due path."""

        if type(scope) is not OwnerTaskScope or scope.operation != _OPERATION:
            return "pending"
        if _is_stopping(stop_signal) or self.cancellation is None:
            return "pending"
        cancellation = self.cancellation.consume_persisted(
            scope, stop_signal=stop_signal
        )
        if cancellation != "not_selected":
            return (
                cancellation
                if cancellation in {"settled", "retained", "pending", "terminal_projection"}
                else "pending"
            )
        return self.consume_due(scope, stop_signal=stop_signal)

    def consume_due(
        self,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> Literal["settled", "retained", "pending"]:
        """Settle one due review only after reauthenticating its persisted graph."""

        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != _OPERATION
            or _is_stopping(stop_signal)
        ):
            return "pending"
        staged: list[_ObservationStage] = []
        try:
            outcome = self._settle_due_transaction(scope, stop_signal, staged)
        except Exception:
            outcome = "pending"
        if outcome != "pending" or not staged:
            return outcome
        return "settled" if self._confirm_observation(staged[0]) == "failed" else "pending"

    def _record_observation_transaction(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedFrameReviewExecutionInput,
        observation: _ProviderObservation,
        staged: list[_ObservationStage],
    ) -> Literal["pending", "ownership_lost", "failed"]:
        with self.session_factory() as session:
            with session.begin():
                graph = self._lock_graph(
                    session,
                    OwnerTaskScope(
                        user_id=claim.scope.user_id,
                        task_id=claim.scope.task_id,
                        operation=_OPERATION,
                    ),
                    allowed_unit_statuses=("pending", "unknown"),
                    allowed_step_statuses=("submitting", "reconciling"),
                    allowed_submission_statuses=("submitting", "unknown"),
                )
                now = self._server_now()
                if graph is None or now is None:
                    return "pending"
                if not _claim_is_current(graph.task, claim, now):
                    return "ownership_lost"
                if (
                    graph.verified != verified
                    or not _intent_rows_match(graph, intent)
                    or graph.task.get("credit_cost") != 0
                    or not _has_clean_unbilled_accounting(session, graph)
                    or not _has_no_prior_result(session, graph)
                    or claim.local_lost_signal.is_set()
                ):
                    return "pending"
                prior_observations = _all_observations(session, graph)
                if prior_observations is None or prior_observations:
                    return "pending"
                stage = self._stage_observation(graph, observation, now)
                if stage is None:
                    return "pending"
                staged.append(stage)
                if _is_stopping(claim.local_lost_signal):
                    return "ownership_lost"
                if not _stage_observation_rows(session, graph, stage):
                    raise _StageConflict
                return "failed" if stage.task_status == "failed" else "pending"

    def _settle_due_transaction(
        self,
        scope: OwnerTaskScope,
        stop_signal: Event | None,
        staged: list[_ObservationStage],
    ) -> Literal["settled", "retained", "pending"]:
        with self.session_factory() as session:
            with session.begin():
                if _is_stopping(stop_signal):
                    return "pending"
                graph = self._lock_graph(
                    session,
                    scope,
                    allowed_unit_statuses=("pending", "unknown"),
                    allowed_step_statuses=("submitting", "reconciling"),
                    allowed_submission_statuses=("submitting", "unknown"),
                )
                now = self._server_now()
                if graph is None or now is None:
                    return "pending"
                if (
                    graph.task.get("status") not in _ACTIVE_TASK_STATUSES
                    or graph.task.get("user_cancelled_at") is not None
                    or graph.unit.get("result_status") not in {"pending", "unknown"}
                    or graph.step.get("execution_status") not in {"submitting", "reconciling"}
                    or graph.external.get("submission_status") not in {"submitting", "unknown"}
                    or _as_utc(now) < graph.intent.deadline_at
                    or graph.task.get("credit_cost") != 0
                    or not _has_clean_unbilled_accounting(session, graph)
                    or not _has_no_prior_result(session, graph)
                    or _is_stopping(stop_signal)
                ):
                    return "pending"

                prior_observations = _all_observations(session, graph)
                if prior_observations is None:
                    return "pending"
                unknown_observation = next(
                    (item for item in prior_observations if item["kind"] == "outcome_unknown"),
                    None,
                )
                if any(item["kind"] != "outcome_unknown" for item in prior_observations):
                    return "pending"
                if unknown_observation is None:
                    due_fact = _ProviderObservation(
                        kind="outcome_unknown",
                        stage="recovery",
                        code="original_deadline_elapsed",
                        request_started=None,
                        http_status=None,
                    )
                    stage = self._stage_observation(graph, due_fact, now)
                    if stage is None:
                        return "pending"
                    stage = _as_due_projection(stage, now)
                else:
                    stage = _due_projection_stage(graph, unknown_observation, now)
                    if stage is None:
                        return "pending"
                staged.append(stage)
                if _is_stopping(stop_signal):
                    return "pending"
                if not _stage_due_rows(
                    session, graph, stage, stop_signal=stop_signal
                ):
                    raise _StageConflict
                return "settled"

    def _lock_graph(
        self,
        session: Session,
        scope: OwnerTaskScope,
        *,
        allowed_unit_statuses: tuple[str, ...],
        allowed_step_statuses: tuple[str, ...],
        allowed_submission_statuses: tuple[str, ...],
    ) -> _ReviewGraph | None:
        user = session.execute(
            select(UserRow.id)
            .where(UserRow.id == scope.user_id)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if user is None:
            return None
        task_row = session.execute(
            select(AITaskRow.__table__)
            .where(
                AITaskRow.id == scope.task_id,
                AITaskRow.user_id == scope.user_id,
                AITaskRow.type == _TASK_TYPE,
            )
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if task_row is None:
            return None
        task = dict(task_row)
        if task.get("status") not in _ACTIVE_TASK_STATUSES:
            return None

        unit_rows = session.execute(
            select(BillingUnitRow.__table__)
            .where(BillingUnitRow.task_id == scope.task_id)
            .order_by(BillingUnitRow.id)
            .with_for_update()
        ).mappings().all()
        if len(unit_rows) != 1:
            return None
        unit = dict(unit_rows[0])
        step_rows = session.execute(
            select(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.billing_unit_id == unit["id"])
            .order_by(ExecutionStepRow.step_order, ExecutionStepRow.id)
            .with_for_update()
        ).mappings().all()
        if len(step_rows) != 1:
            return None
        step = dict(step_rows[0])
        external_rows = session.execute(
            select(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.step_id == step["id"])
            .order_by(ExternalSubmissionRow.id)
            .with_for_update()
        ).mappings().all()
        if len(external_rows) != 1:
            return None
        external = dict(external_rows[0])
        try:
            request_data = json.loads(task.get("request_data"))
            parent_id = request_data.get("submission_id")
        except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
            return None
        if not isinstance(request_data, dict) or not isinstance(parent_id, str):
            return None
        parent_row = session.execute(
            select(TaskSubmissionRow.__table__)
            .where(
                TaskSubmissionRow.id == parent_id,
                TaskSubmissionRow.user_id == scope.user_id,
                TaskSubmissionRow.operation == _OPERATION,
            )
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if parent_row is None:
            return None
        parent = dict(parent_row)

        groups = session.execute(
            select(ExecutionAttemptGroupRow.__table__)
            .where(ExecutionAttemptGroupRow.__table__.c.unit_id == unit["id"])
            .with_for_update()
        ).mappings().all()
        events = session.execute(
            select(PolicyStopEventRow.__table__)
            .where(PolicyStopEventRow.__table__.c.unit_id == unit["id"])
            .with_for_update()
        ).mappings().all()
        if groups or events:
            return None

        binding = _binding(task, unit, step, parent, request_data)
        if binding is None:
            return None
        verified = read_original_frame_review_execution_input(
            session,
            binding,
            crypto=self.crypto,
            proof_authority=self.proof_authority,
        )
        if verified is None or verified.task_id != scope.task_id:
            return None
        intent = _intent_from_row(unit, step, external, verified, request_data)
        graph = _ReviewGraph(task, unit, step, parent, external, request_data, binding, verified, intent)
        if (
            not _registered_graph_matches(graph)
            or unit.get("result_status") not in allowed_unit_statuses
            or step.get("execution_status") not in allowed_step_statuses
            or external.get("submission_status") not in allowed_submission_statuses
        ):
            return None
        return graph

    def _stage_observation(
        self,
        graph: _ReviewGraph,
        observation: _ProviderObservation,
        now: datetime,
    ) -> _ObservationStage | None:
        if not _valid_datetime(now):
            return None
        try:
            evidence_id = self.id_factory()
        except Exception:
            return None
        if not isinstance(evidence_id, str) or not evidence_id or len(evidence_id) > 36:
            return None
        task_status, unit_status, step_status, submission_status = _projection(observation)
        task_result, progress = _safe_task_result(observation)
        observed_at = _as_utc(now)
        payload = {
            "version": 1,
            "owner_id": graph.task["user_id"],
            "task_id": graph.task["id"],
            "unit_id": graph.unit["id"],
            "step_id": graph.step["id"],
            "submission_id": graph.external["id"],
            "attempt_id": graph.verified.server_attempt_id,
            "request_digest": graph.intent.request_digest,
            "kind": observation.kind,
            "stage": observation.stage,
            "code": observation.code,
            "request_started": observation.request_started,
            "http_status": observation.http_status,
            "observed_at": _iso_utc(observed_at),
        }
        try:
            payload_text = _OBSERVATION_TEXT_PREFIX + json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return None
        payload_digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        return _ObservationStage(
            owner_id=graph.task["user_id"],
            task_id=graph.task["id"],
            unit_id=graph.unit["id"],
            step_id=graph.step["id"],
            submission_id=graph.external["id"],
            attempt_id=graph.verified.server_attempt_id,
            request_digest=graph.intent.request_digest,
            kind=observation.kind,
            evidence_id=evidence_id,
            evidence_key=(
                f"{_OBSERVATION_KEY_PREFIX}{graph.external['id']}:{observation.kind}"
            ),
            payload_text=payload_text,
            payload_digest=payload_digest,
            evidence_observed_at=observed_at,
            observed_at=observed_at,
            task_result=task_result,
            task_progress_message=progress,
            task_status=task_status,
            unit_status=unit_status,
            step_status=step_status,
            submission_status=submission_status,
            execution_generation=int(graph.task["execution_generation"]),
        )

    def _confirm_observation(self, stage: _ObservationStage) -> Literal["failed", "pending"] | None:
        try:
            with self.session_factory() as session:
                task = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == stage.task_id,
                        AITaskRow.user_id == stage.owner_id,
                        AITaskRow.type == _TASK_TYPE,
                    )
                ).mappings().one_or_none()
                unit = session.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.id == stage.unit_id,
                        BillingUnitRow.task_id == stage.task_id,
                    )
                ).mappings().one_or_none()
                step = session.execute(
                    select(ExecutionStepRow.__table__).where(
                        ExecutionStepRow.id == stage.step_id,
                        ExecutionStepRow.billing_unit_id == stage.unit_id,
                    )
                ).mappings().one_or_none()
                external = session.execute(
                    select(ExternalSubmissionRow.__table__).where(
                        ExternalSubmissionRow.id == stage.submission_id,
                        ExternalSubmissionRow.step_id == stage.step_id,
                    )
                ).mappings().one_or_none()
                evidence = session.execute(
                    select(ResultEvidenceRow.__table__).where(
                        ResultEvidenceRow.evidence_key == stage.evidence_key,
                        ResultEvidenceRow.id == stage.evidence_id,
                    )
                ).mappings().one_or_none()
            if not all((task, unit, step, external, evidence)):
                return None
            if not _evidence_matches_stage(evidence, stage):
                return None
            if not _projection_matches(task, unit, step, external, stage):
                return None
            return "failed" if stage.task_status == "failed" else "pending"
        except Exception:
            return None

    def _next_page(self, now: datetime) -> list[_Candidate]:
        page = self._read_candidates(now, self._cursor, self.page_size)
        selected = list(page)
        if len(selected) < self.page_size and self._cursor is not None:
            seen = {candidate.task_id for candidate in selected}
            wrapped = self._read_candidates(now, None, self.page_size)
            selected.extend(item for item in wrapped if item.task_id not in seen)
            selected = selected[: self.page_size]
        if selected:
            last = selected[-1]
            self._cursor = (last.created_at, last.task_id)
        else:
            self._cursor = None
        return selected

    def _read_candidates(
        self,
        now: datetime,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> list[_Candidate]:
        task = AITaskRow.__table__
        unit = BillingUnitRow.__table__
        due = exists(
            select(unit.c.id).where(
                unit.c.task_id == task.c.id,
                unit.c.operation == _OPERATION,
                unit.c.step_graph_version == _GRAPH_VERSION,
                unit.c.result_status.in_(("pending", "unknown")),
                unit.c.first_intent_at.is_not(None),
                unit.c.deadline_at.is_not(None),
                unit.c.deadline_at <= _database_datetime(now),
            )
        )
        persisted_cancel = exists(
            select(unit.c.id).where(
                unit.c.task_id == task.c.id,
                unit.c.operation == _OPERATION,
                unit.c.step_graph_version == _GRAPH_VERSION,
            )
        )
        conditions = [
            task.c.type == _TASK_TYPE,
            or_(
                and_(
                    task.c.status.in_(_ACTIVE_TASK_STATUSES),
                    task.c.user_cancelled_at.is_(None),
                    due,
                ),
                and_(task.c.status == "cancelling", persisted_cancel),
            ),
        ]
        if cursor is not None:
            created_at, task_id = cursor
            conditions.append(
                or_(
                    task.c.created_at > _database_datetime(created_at),
                    and_(
                        task.c.created_at == _database_datetime(created_at),
                        task.c.id > task_id,
                    ),
                )
            )
        with self.session_factory() as session:
            rows = session.execute(
                select(task.c.user_id, task.c.id, task.c.created_at)
                .where(*conditions)
                .order_by(task.c.created_at, task.c.id)
                .limit(limit)
            ).all()
        return [
            _Candidate(row.user_id, row.id, _aware_utc(row.created_at))
            for row in rows
        ]

    def _server_now(self) -> datetime | None:
        try:
            value = self.db_clock()
        except Exception:
            return None
        return _as_utc(value) if _valid_datetime(value) else None


def _provider_observation(
    code: object,
    *,
    request_started: bool,
    http_status: object,
) -> _ProviderObservation:
    """Map the built-in provider's typed result into a narrow safe allowlist."""

    status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
    if (
        request_started
        and code == "provider_rejected"
        and status is not None
        and 400 <= status < 500
    ):
        return _ProviderObservation("provider_rejected", "request", "provider_rejected", True, status)
    if not request_started and code in {
        "configuration_unavailable",
        "provider_transport_unavailable",
        "invalid_frozen_snapshot",
    }:
        safe_code = {
            "configuration_unavailable": "credential_unavailable",
            "provider_transport_unavailable": "transport_unavailable",
            "invalid_frozen_snapshot": "invalid_frozen_request",
        }[code]
        return _ProviderObservation("known_not_started", "before_request", safe_code, False, None)
    return _ProviderObservation(
        "outcome_unknown",
        "request" if request_started else "before_request",
        "provider_outcome_unknown",
        bool(request_started),
        status,
    )


def _unknown_observation(
    *, request_started: bool, http_status: int | None
) -> _ProviderObservation:
    return _ProviderObservation(
        "outcome_unknown",
        "request" if request_started else "before_request",
        "provider_outcome_unknown",
        bool(request_started),
        http_status if type(http_status) is int else None,
    )


def _valid_observation(value: object) -> bool:
    if type(value) is not _ProviderObservation:
        return False
    if value.kind == "known_not_started":
        return (
            value.stage == "before_request"
            and value.request_started is False
            and value.http_status is None
            and value.code in {
                "credential_unavailable",
                "transport_unavailable",
                "invalid_frozen_request",
            }
        )
    if value.kind == "provider_rejected":
        return (
            value.stage == "request"
            and value.request_started is True
            and value.code == "provider_rejected"
            and type(value.http_status) is int
            and 400 <= value.http_status < 500
        )
    return (
        value.kind == "outcome_unknown"
        and value.code in {"provider_outcome_unknown", "original_deadline_elapsed"}
        and (
            (
                value.stage in {"before_request", "request"}
                and value.request_started is (value.stage == "request")
            )
            or (
                value.stage == "recovery"
                and value.code == "original_deadline_elapsed"
                and value.request_started is None
            )
        )
        and (value.http_status is None or type(value.http_status) is int)
        and (
            value.http_status is None
            or 100 <= value.http_status <= 599
        )
    )


def _projection(observation: _ProviderObservation) -> tuple[str, str, str, str]:
    if observation.kind == "outcome_unknown":
        return "queued", "unknown", "reconciling", "unknown"
    return "failed", "failed", "failed", "failed"


def _safe_task_result(
    observation: _ProviderObservation,
) -> tuple[str | None, str]:
    if observation.kind == "outcome_unknown":
        return None, "复核请求结果仍在确认。"
    if observation.kind == "provider_rejected":
        return (
            json.dumps(
                {"error": "文本服务拒绝了原分镜复核请求。", "code": "provider_rejected"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "文本服务拒绝了原分镜复核请求。",
        )
    return (
        json.dumps(
            {"error": "原分镜复核请求未开始。", "code": observation.code},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "原分镜复核请求未开始。",
    )


def _stage_observation_rows(
    session: Session, graph: _ReviewGraph, stage: _ObservationStage
) -> bool:
    session.execute(
        ResultEvidenceRow.__table__.insert().values(
            id=stage.evidence_id,
            step_id=stage.step_id,
            submission_id=stage.submission_id,
            evidence_key=stage.evidence_key,
            source="worker",
            evidence_kind="frame_review_observation_v1",
            payload_digest=stage.payload_digest,
            result_location=stage.payload_text,
            result_digest=None,
            received_at=_database_datetime(stage.observed_at),
            validity="unknown",
            application_status="pending",
            applied_at=None,
        )
    )
    task = AITaskRow.__table__
    task_result = session.execute(
        update(task)
        .where(
            task.c.id == stage.task_id,
            task.c.user_id == stage.owner_id,
            task.c.type == _TASK_TYPE,
            task.c.status == graph.task["status"],
            task.c.claimed_by == graph.task.get("claimed_by"),
            task.c.claim_token == graph.task.get("claim_token"),
            task.c.execution_generation == graph.task["execution_generation"],
            task.c.lease_until == graph.task.get("lease_until"),
            task.c.user_cancelled_at.is_(None),
        )
        .values(
            status=stage.task_status,
            result=stage.task_result,
            progress=100 if stage.task_status == "failed" else graph.task["progress"],
            progress_message=stage.task_progress_message,
            claimed_by=None if stage.task_status == "failed" else graph.task.get("claimed_by"),
            claim_token=None if stage.task_status == "failed" else graph.task.get("claim_token"),
            lease_until=None if stage.task_status == "failed" else graph.task.get("lease_until"),
            updated_at=_database_datetime(stage.observed_at),
        )
    )
    unit = BillingUnitRow.__table__
    unit_result = session.execute(
        update(unit)
        .where(
            unit.c.id == stage.unit_id,
            unit.c.task_id == stage.task_id,
            unit.c.result_status == graph.unit["result_status"],
            unit.c.billing_status == "unbilled",
            unit.c.debited_amount == 0,
            unit.c.refunded_amount == 0,
            unit.c.first_intent_at == graph.unit.get("first_intent_at"),
            unit.c.deadline_at == graph.unit.get("deadline_at"),
        )
        .values(result_status=stage.unit_status, updated_at=_database_datetime(stage.observed_at))
    )
    step = ExecutionStepRow.__table__
    step_result = session.execute(
        update(step)
        .where(
            step.c.id == stage.step_id,
            step.c.billing_unit_id == stage.unit_id,
            step.c.execution_status == graph.step["execution_status"],
            step.c.input_digest == stage.request_digest,
        )
        .values(execution_status=stage.step_status, updated_at=_database_datetime(stage.observed_at))
    )
    external = ExternalSubmissionRow.__table__
    external_result = session.execute(
        update(external)
        .where(
            external.c.id == stage.submission_id,
            external.c.step_id == stage.step_id,
            external.c.submission_status == graph.external["submission_status"],
            external.c.request_digest == stage.request_digest,
            external.c.intent_at == graph.external["intent_at"],
            external.c.deadline_at == graph.external["deadline_at"],
            external.c.external_task_id.is_(None),
        )
        .values(
            submission_status=stage.submission_status,
            updated_at=_database_datetime(stage.observed_at),
        )
    )
    return all(result.rowcount == 1 for result in (task_result, unit_result, step_result, external_result))


def _stage_due_rows(
    session: Session,
    graph: _ReviewGraph,
    stage: _ObservationStage,
    *,
    stop_signal: Event | None = None,
) -> bool:
    if _is_stopping(stop_signal):
        return False
    existing_evidence = session.execute(
        select(ResultEvidenceRow.__table__)
        .where(
            ResultEvidenceRow.__table__.c.id == stage.evidence_id,
            ResultEvidenceRow.__table__.c.evidence_key == stage.evidence_key,
        )
        .with_for_update()
    ).mappings().one_or_none()
    if existing_evidence is None:
        session.execute(
            ResultEvidenceRow.__table__.insert().values(
                id=stage.evidence_id,
                step_id=stage.step_id,
                submission_id=stage.submission_id,
                evidence_key=stage.evidence_key,
                source="worker",
                evidence_kind="frame_review_observation_v1",
                payload_digest=stage.payload_digest,
                result_location=stage.payload_text,
                result_digest=None,
                received_at=_database_datetime(stage.observed_at),
                validity="unknown",
                application_status="pending",
                applied_at=None,
            )
        )
    elif not _evidence_matches_stage(existing_evidence, stage):
        return False
    task = AITaskRow.__table__
    task_result = session.execute(
        update(task)
        .where(
            task.c.id == stage.task_id,
            task.c.user_id == stage.owner_id,
            task.c.type == _TASK_TYPE,
            task.c.status == graph.task["status"],
            task.c.execution_generation == stage.execution_generation,
            task.c.claimed_by == graph.task.get("claimed_by"),
            task.c.claim_token == graph.task.get("claim_token"),
            task.c.lease_until == graph.task.get("lease_until"),
            task.c.user_cancelled_at.is_(None),
        )
        .values(
            status="failed",
            result=json.dumps(
                {"error": "原分镜复核结果在期限内未能确认。", "code": "provider_outcome_unknown"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            progress=100,
            progress_message="原分镜复核结果在期限内未能确认。",
            claimed_by=None,
            claim_token=None,
            lease_until=None,
            updated_at=_database_datetime(stage.observed_at),
        )
    )
    unit = BillingUnitRow.__table__
    unit_result = session.execute(
        update(unit)
        .where(
            unit.c.id == stage.unit_id,
            unit.c.task_id == stage.task_id,
            unit.c.result_status == graph.unit["result_status"],
            unit.c.billing_status == "unbilled",
            unit.c.debited_amount == 0,
            unit.c.refunded_amount == 0,
            unit.c.first_intent_at == graph.unit["first_intent_at"],
            unit.c.deadline_at == graph.unit["deadline_at"],
        )
        .values(result_status="unknown", updated_at=_database_datetime(stage.observed_at))
    )
    step = ExecutionStepRow.__table__
    step_result = session.execute(
        update(step)
        .where(
            step.c.id == stage.step_id,
            step.c.billing_unit_id == stage.unit_id,
            step.c.execution_status == graph.step["execution_status"],
            step.c.input_digest == stage.request_digest,
        )
        .values(execution_status="reconciling", updated_at=_database_datetime(stage.observed_at))
    )
    external = ExternalSubmissionRow.__table__
    external_result = session.execute(
        update(external)
        .where(
            external.c.id == stage.submission_id,
            external.c.step_id == stage.step_id,
            external.c.submission_status == graph.external["submission_status"],
            external.c.request_digest == stage.request_digest,
            external.c.intent_at == graph.external["intent_at"],
            external.c.deadline_at == graph.external["deadline_at"],
            external.c.external_task_id.is_(None),
        )
        .values(submission_status="unknown", updated_at=_database_datetime(stage.observed_at))
    )
    return all(result.rowcount == 1 for result in (task_result, unit_result, step_result, external_result))


def _due_projection_stage(
    graph: _ReviewGraph, existing: dict[str, object], now: datetime
) -> _ObservationStage | None:
    if (
        existing.get("submission_id") != graph.intent.external_submission_id
        or existing.get("kind") != "outcome_unknown"
        or existing.get("evidence") is None
    ):
        return None
    text = existing["evidence"].get("result_location")
    digest = existing["evidence"].get("payload_digest")
    if not isinstance(text, str) or not isinstance(digest, str):
        return None
    task_result = json.dumps(
        {"error": "原分镜复核结果在期限内未能确认。", "code": "provider_outcome_unknown"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _ObservationStage(
        owner_id=graph.task["user_id"],
        task_id=graph.task["id"],
        unit_id=graph.unit["id"],
        step_id=graph.step["id"],
        submission_id=graph.external["id"],
        attempt_id=graph.verified.server_attempt_id,
        request_digest=graph.intent.request_digest,
        kind="outcome_unknown",
        evidence_id=existing["evidence"]["id"],
        evidence_key=existing["evidence"]["evidence_key"],
        payload_text=text,
        payload_digest=digest,
        evidence_observed_at=_aware_utc(existing["evidence"]["received_at"]),
        observed_at=_as_utc(now),
        task_result=task_result,
        task_progress_message="原分镜复核结果在期限内未能确认。",
        task_status="failed",
        unit_status="unknown",
        step_status="reconciling",
        submission_status="unknown",
        execution_generation=int(graph.task["execution_generation"]),
    )


def _as_due_projection(stage: _ObservationStage, now: datetime) -> _ObservationStage:
    return replace(
        stage,
        observed_at=_as_utc(now),
        task_result=json.dumps(
            {"error": "原分镜复核结果在期限内未能确认。", "code": "provider_outcome_unknown"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        task_progress_message="原分镜复核结果在期限内未能确认。",
        task_status="failed",
        unit_status="unknown",
        step_status="reconciling",
        submission_status="unknown",
    )


def _has_clean_unbilled_accounting(session: Session, graph: _ReviewGraph) -> bool:
    unit = graph.unit
    task = graph.task
    if not all(
        (
            task.get("credit_cost") == 0,
            task.get("billing_status") == "unbilled",
            unit.get("billing_status") == "unbilled",
            unit.get("quoted_amount") is not None,
            type(unit.get("debited_amount")) is int,
            unit.get("debited_amount") == 0,
            type(unit.get("refunded_amount")) is int,
            unit.get("refunded_amount") == 0,
        )
    ):
        return False
    fee = session.execute(
        select(UnitFeeDecisionRow.__table__.c.unit_id)
        .where(UnitFeeDecisionRow.__table__.c.unit_id == unit["id"])
        .with_for_update()
    ).first()
    ledger = session.execute(
        select(CreditLogRow.__table__.c.id)
        .where(
            or_(
                CreditLogRow.__table__.c.billing_unit_id == unit["id"],
                CreditLogRow.__table__.c.business_key == f"debit:{unit['id']}",
            )
        )
        .with_for_update()
    ).first()
    return fee is None and ledger is None


def _has_no_prior_result(session: Session, graph: _ReviewGraph) -> bool:
    receipts = session.execute(
        select(GenerationResultReceiptRow.__table__.c.result_ref)
        .where(GenerationResultReceiptRow.__table__.c.unit_id == graph.unit["id"])
        .with_for_update()
    ).first()
    works = session.execute(
        select(LocalContinuationWorkRow.__table__.c.work_id)
        .where(LocalContinuationWorkRow.__table__.c.unit_id == graph.unit["id"])
        .with_for_update()
    ).first()
    applications = session.execute(
        select(ApplicationRecordRow.__table__.c.application_ref)
        .where(ApplicationRecordRow.__table__.c.unit_id == graph.unit["id"])
        .with_for_update()
    ).first()
    evidence = session.execute(
        select(ResultEvidenceRow.__table__)
        .where(
            ResultEvidenceRow.__table__.c.step_id == graph.step["id"],
            ResultEvidenceRow.__table__.c.submission_id == graph.external["id"],
        )
        .with_for_update()
    ).mappings().all()
    return (
        receipts is None
        and works is None
        and applications is None
        and all(row.get("evidence_kind") == "frame_review_observation_v1" for row in evidence)
    )


def _all_observations(session: Session, graph: _ReviewGraph) -> list[dict[str, object]] | None:
    rows = session.execute(
        select(ResultEvidenceRow.__table__)
        .where(
            ResultEvidenceRow.__table__.c.submission_id == graph.external["id"],
            ResultEvidenceRow.__table__.c.evidence_kind == "frame_review_observation_v1",
        )
        .order_by(ResultEvidenceRow.__table__.c.evidence_key)
        .with_for_update()
    ).mappings().all()
    observations: list[dict[str, object]] = []
    for row in rows:
        parsed = _parse_observation_row(row, graph)
        if parsed is None:
            return None
        observations.append(parsed)
    return observations


def _parse_observation_row(row, graph: _ReviewGraph) -> dict[str, object] | None:
    submission_id = graph.external["id"]
    try:
        payload_text = row.get("result_location")
        if (
            isinstance(row.get("id"), str)
            and row.get("source") == "worker"
            and row.get("evidence_key", "").startswith(_OBSERVATION_KEY_PREFIX)
            and row.get("submission_id") == submission_id
            and row.get("evidence_kind") == "frame_review_observation_v1"
            and row.get("validity") == "unknown"
            and row.get("result_digest") is None
            and row.get("application_status") == "pending"
            and row.get("applied_at") is None
            and isinstance(payload_text, str)
            and payload_text.startswith(_OBSERVATION_TEXT_PREFIX)
            and isinstance(row.get("payload_digest"), str)
            and len(row["payload_digest"]) == 64
            and hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
            == row.get("payload_digest")
        ):
            payload = json.loads(payload_text[len(_OBSERVATION_TEXT_PREFIX) :])
        else:
            return None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    expected_keys = {
        "version",
        "owner_id",
        "task_id",
        "unit_id",
        "step_id",
        "submission_id",
        "attempt_id",
        "request_digest",
        "kind",
        "stage",
        "code",
        "request_started",
        "http_status",
        "observed_at",
    }
    kind = payload.get("kind") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("version") != 1
        or payload.get("owner_id") != graph.binding.owner_id
        or payload.get("task_id") != graph.task["id"]
        or payload.get("unit_id") != graph.unit["id"]
        or payload.get("step_id") != graph.step["id"]
        or payload.get("submission_id") != submission_id
        or payload.get("attempt_id") != graph.verified.server_attempt_id
        or payload.get("request_digest") != graph.intent.request_digest
        or not isinstance(kind, str)
        or row.get("evidence_key") != f"{_OBSERVATION_KEY_PREFIX}{submission_id}:{kind}"
    ):
        return None
    observation = _ProviderObservation(
        kind=payload["kind"],
        stage=payload["stage"],
        code=payload["code"],
        request_started=payload["request_started"],
        http_status=payload["http_status"],
    )
    try:
        observed_at = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if (
        not _valid_observation(observation)
        or not _valid_datetime(observed_at)
        or _iso_utc(observed_at) != payload["observed_at"]
        or not _same_time(row.get("received_at"), observed_at)
        or payload_text
        != _OBSERVATION_TEXT_PREFIX
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    ):
        return None
    return {
        "kind": kind,
        "submission_id": submission_id,
        "evidence": dict(row),
        "payload": payload,
    }


def _evidence_matches_stage(evidence, stage: _ObservationStage) -> bool:
    return all(
        (
            evidence.get("id") == stage.evidence_id,
            evidence.get("step_id") == stage.step_id,
            evidence.get("submission_id") == stage.submission_id,
            evidence.get("evidence_key") == stage.evidence_key,
            evidence.get("source") == "worker",
            evidence.get("evidence_kind") == "frame_review_observation_v1",
            evidence.get("payload_digest") == stage.payload_digest,
            evidence.get("result_location") == stage.payload_text,
            evidence.get("result_digest") is None,
            evidence.get("validity") == "unknown",
            evidence.get("application_status") == "pending",
            _same_time(evidence.get("received_at"), stage.evidence_observed_at),
        )
    )


def _projection_matches(task, unit, step, external, stage: _ObservationStage) -> bool:
    return all(
        (
            task.get("status") == stage.task_status,
            task.get("result") == stage.task_result,
            task.get("progress_message") == stage.task_progress_message,
            task.get("execution_generation") == stage.execution_generation,
            task.get("claimed_by") is None if stage.task_status == "failed" else True,
            task.get("claim_token") is None if stage.task_status == "failed" else True,
            task.get("lease_until") is None if stage.task_status == "failed" else True,
            unit.get("result_status") == stage.unit_status,
            unit.get("billing_status") == "unbilled",
            unit.get("debited_amount") == 0,
            unit.get("refunded_amount") == 0,
            step.get("execution_status") == stage.step_status,
            external.get("submission_status") == stage.submission_status,
            external.get("external_task_id") is None,
        )
    )


def _binding(task, unit, step, parent, request_data):
    snapshot = step.get("input_snapshot")
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "snapshot_digest",
        "manifest_snapshot_digest",
    }:
        return None
    try:
        return PersistedFrameReviewExecutionBinding(
            owner_id=task["user_id"],
            task_id=task["id"],
            unit_id=unit["id"],
            step_id=step["id"],
            submission_id=parent["id"],
            raw_key=parent["idempotency_key"],
            task_message_id=task.get("message_id") or None,
            task_model_name=task["model_name"],
            task_model_id=request_data["model_id"],
            task_model_source=request_data["model_source"],
            task_model_owner_id=request_data["model_owner_id"],
            request_digest=request_data["request_digest"],
            rule_digest=request_data["rule_digest"],
            snapshot_digest=request_data["snapshot_digest"],
            manifest_snapshot_digest=request_data["manifest_snapshot_digest"],
            server_attempt_id=snapshot["server_attempt_id"],
            prepared_payload_id=snapshot["prepared_payload_id"],
            protected_ref=snapshot["protected_ref"],
            quoted_amount=unit["quoted_amount"],
        )
    except (KeyError, TypeError):
        return None


def _intent_from_row(unit, step, external, verified, request_data) -> CommittedIntent:
    first = _aware_utc(external["intent_at"])
    return CommittedIntent(
        step_id=step["id"],
        attempt_id=verified.server_attempt_id,
        external_submission_id=external["id"],
        provider=external["provider"],
        model_id=external["model_ref"],
        request_digest=external["request_digest"],
        snapshot_ref=(
            f"{_GRAPH_VERSION}:{verified.prepared_payload_id}:"
            f"{request_data['snapshot_digest']}:{request_data['manifest_snapshot_digest']}"
        ),
        committed_at=first,
        first_intent_at=first,
        deadline_at=_aware_utc(external["deadline_at"]),
    )


def _registered_graph_matches(graph: _ReviewGraph) -> bool:
    task, unit, step, parent, external, request = (
        graph.task,
        graph.unit,
        graph.step,
        graph.parent,
        graph.external,
        graph.request_data,
    )
    verified, intent = graph.verified, graph.intent
    snapshot = step.get("input_snapshot")
    if not isinstance(snapshot, dict):
        return False
    try:
        expected_snapshot = {
            "owner_id": task["user_id"],
            "task_id": task["id"],
            "unit_id": unit["id"],
            "step_id": step["id"],
            "submission_id": parent["id"],
            "operation": _OPERATION,
            "graph_version": _GRAPH_VERSION,
            "attempt_id": snapshot["server_attempt_id"],
            "prepared_payload_id": snapshot["prepared_payload_id"],
            "protected_ref": snapshot["protected_ref"],
            "request_digest": request["request_digest"],
            "rule_digest": request["rule_digest"],
            "snapshot_digest": request["snapshot_digest"],
            "manifest_snapshot_digest": request["manifest_snapshot_digest"],
            "snapshot_ref": intent.snapshot_ref,
            "credential_binding_ref": verified.snapshot.credential_binding_ref,
            "provider": verified.snapshot.model_ref.provider,
            "model_id": verified.snapshot.model_ref.model_id,
        }
        original_deadline = _as_utc(intent.first_intent_at) + timedelta(
            seconds=_ORIGINAL_DEADLINE_SECONDS
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return all(
        (
            task.get("type") == _TASK_TYPE,
            task.get("credit_cost") == 0,
            task.get("user_id") == graph.binding.owner_id,
            request.get("via") == "registered_generation_graph",
            request.get("mode") == "frame_review",
            request.get("operation") == _OPERATION,
            request.get("graph_version") == _GRAPH_VERSION,
            request.get("step_graph_version") == _GRAPH_VERSION,
            request.get("submission_id") == parent.get("id"),
            request.get("billing_unit_id") == unit.get("id"),
            request.get("request_digest") == unit.get("input_digest") == step.get("input_digest"),
            request.get("server_attempt_id") == verified.server_attempt_id,
            request.get("prepared_payload_id") == verified.prepared_payload_id,
            request.get("protected_ref") == snapshot.get("protected_ref"),
            request.get("snapshot_digest") == snapshot.get("snapshot_digest"),
            request.get("manifest_snapshot_digest") == snapshot.get("manifest_snapshot_digest"),
            unit.get("task_id") == task.get("id"),
            unit.get("item_key") == verified.server_attempt_id,
            unit.get("operation") == _OPERATION,
            unit.get("step_graph_version") == _GRAPH_VERSION,
            unit.get("quoted_amount") is not None and unit.get("quoted_amount") >= 0,
            unit.get("billing_status") == "unbilled",
            unit.get("debited_amount") == 0,
            unit.get("refunded_amount") == 0,
            unit.get("user_cancelled_at") is None,
            step.get("id") == verified.step_id,
            step.get("billing_unit_id") == unit.get("id"),
            step.get("step_key") == _STEP_KEY,
            step.get("required") is True,
            step.get("step_order") == 0,
            step.get("dependencies") in ([], ()),
            parent.get("id") == verified.submission_id,
            parent.get("user_id") == task.get("user_id"),
            parent.get("operation") == _OPERATION,
            parent.get("request_digest") == intent.request_digest,
            parent.get("task_ids") == [task.get("id")],
            external.get("step_id") == step.get("id"),
            external.get("submission_key")
            == f"{_INTENT_KEY_PREFIX}{unit.get('id')}:{step.get('id')}",
            external.get("provider") == intent.provider,
            external.get("model_ref") == intent.model_id,
            intent.provider == verified.snapshot.model_ref.provider,
            intent.model_id == verified.snapshot.model_ref.model_id,
            external.get("request_digest") == intent.request_digest,
            external.get("request_snapshot") == expected_snapshot,
            external.get("intent_at") is not None,
            _same_time(external.get("intent_at"), intent.first_intent_at),
            _same_time(unit.get("first_intent_at"), intent.first_intent_at),
            _same_time(external.get("deadline_at"), intent.deadline_at),
            _same_time(unit.get("deadline_at"), intent.deadline_at),
            _as_utc(intent.deadline_at) == original_deadline,
            intent.committed_at == intent.first_intent_at,
            intent.provider_idempotency_key is None,
            external.get("external_task_id") is None,
            external.get("submitted_at") is None,
        )
    )


def _intent_rows_match(graph: _ReviewGraph, expected: CommittedIntent) -> bool:
    return graph.intent == expected and _registered_graph_matches(graph)


def _claim_is_current(task, claim: ClaimAuthority, now: datetime) -> bool:
    lease = task.get("lease_until")
    return all(
        (
            task.get("id") == claim.scope.task_id,
            task.get("user_id") == claim.scope.user_id,
            task.get("type") == _TASK_TYPE,
            task.get("status") in {"processing", "reconciling"},
            task.get("user_cancelled_at") is None,
            task.get("claimed_by") == claim.worker_instance,
            task.get("claim_token") == claim.token,
            task.get("execution_generation") == claim.execution_generation,
            isinstance(lease, datetime),
            _as_utc(lease) > _as_utc(now),
            not claim.local_lost_signal.is_set(),
        )
    )


def _database_datetime(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return _aware_utc(value)


def _same_time(value: object, expected: datetime) -> bool:
    return isinstance(value, datetime) and _as_utc(value) == _as_utc(expected)


def _valid_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timezone.utc.utcoffset(value)
    )


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_stopping(signal: Event | None) -> bool:
    return signal is not None and signal.is_set()


__all__ = (
    "SqlAlchemyFrameReviewRecovery",
    "_ProviderObservation",
    "_provider_observation",
    "_unknown_observation",
)
