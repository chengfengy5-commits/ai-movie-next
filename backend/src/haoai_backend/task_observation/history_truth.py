"""Pure projections for the independent facts shown in a task history receipt."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from haoai_backend.generation_runtime.registry import OPERATION_POLICIES

_RECOVERY_STATES = frozenset({"ready", "reconciling"})
_UNIT_STATES = frozenset({"pending", "partial", "succeeded", "failed", "unknown"})
_STEP_STATES = frozenset(
    {
        "prepared",
        "submitting",
        "submitted",
        "result_observed",
        "applied",
        "failed",
        "reconciling",
        "deterministic_empty",
    }
)
_SUBMISSION_STATES = frozenset({"submitting", "submitted", "completed", "failed", "unknown"})
_PROVIDER_RESULT_KINDS = frozenset({"provider_image", "provider_video", "provider_text"})
_PROVIDER_FAILURE_KINDS = frozenset({"provider_error"})
_CONTINUATION_PHASES = frozenset(
    {
        "delivery_pending",
        "fee_pending",
        "finalize_pending",
        "done",
        "stopped_cancelled",
        "stopped_insufficient",
        "stopped_delivery_failed",
        "stopped_late_generation",
    }
)
_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _value(row: Mapping[str, Any] | object, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _rows(facts: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = facts.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _capability(facts: Mapping[str, Any], key: str) -> bool:
    capabilities = facts.get("capabilities")
    return isinstance(capabilities, Mapping) and capabilities.get(key) is True


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Any) -> str | None:
    normalized = _utc(value)
    return normalized.isoformat() if normalized is not None else None


def _safe_identifier(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
        return value
    return None


def _registered_graph(operation: Any, graph_version: Any) -> bool:
    if not isinstance(operation, str) or not isinstance(graph_version, str):
        return False
    policy = OPERATION_POLICIES.get(operation)
    return policy is not None and policy.graph_version == graph_version


def _registered_intent(operation: Any) -> bool:
    if not isinstance(operation, str):
        return False
    policy = OPERATION_POLICIES.get(operation)
    return policy is not None and isinstance(policy.graph_version, str) and bool(policy.graph_version)


def _source_observation(
    task: Mapping[str, Any] | object,
    facts: Mapping[str, Any],
    units: list[Mapping[str, Any]],
) -> dict[str, Any]:
    intents = _rows(facts, "intents")
    intent_operations = sorted(
        {
            operation
            for intent in intents
            if isinstance((operation := intent.get("operation")), str) and operation
        }
    )
    source_path = "billing_units.operation_and_step_graph_version"
    conflicts: list[str] = []
    graph_operations = {
        operation
        for unit in units
        if isinstance((operation := unit.get("operation")), str)
        and _registered_graph(operation, unit.get("step_graph_version"))
    }
    if units and _capability(facts, "billing_units") and all(
        _registered_graph(unit.get("operation"), unit.get("step_graph_version")) for unit in units
    ):
        status = "registered"
        if len(intent_operations) > 1:
            conflicts.append("multiple_task_submission_intents")
        elif intent_operations and set(intent_operations) != graph_operations:
            conflicts.append("intent_graph_mismatch")
    elif units and _capability(facts, "billing_units") and all(
        isinstance(unit.get("operation"), str)
        and isinstance(unit.get("step_graph_version"), str)
        and unit.get("operation")
        and unit.get("step_graph_version")
        for unit in units
    ):
        status = "unsupported"
    elif units and _capability(facts, "billing_units"):
        status = "unknown"
    elif intent_operations:
        source_path = "task_submissions.operation"
        if len(intent_operations) > 1:
            status = "unknown"
            conflicts.append("multiple_task_submission_intents")
        elif _registered_intent(intent_operations[0]):
            status = "registered"
        else:
            status = "unsupported"
    elif _value(task, "billing_status") == "legacy_unreconciled":
        source_path = "ai_tasks.billing_status"
        status = "legacy"
    elif not _capability(facts, "billing_units") or not _capability(facts, "task_submission_intent"):
        status = "unsupported"
    else:
        status = "unknown"

    if conflicts:
        status = "unknown"

    if status == "registered" and source_path == "task_submissions.operation":
        operations = [intent_operations[0]]
    else:
        operations = sorted(
            {
                operation
                for unit in units
                if isinstance((operation := unit.get("operation")), str)
                and _registered_graph(operation, unit.get("step_graph_version"))
            }
        )
    registered_count = sum(
        1
        for unit in units
        if _registered_graph(unit.get("operation"), unit.get("step_graph_version"))
    )
    return {
        "status": status,
        "source": source_path,
        "unit_count": len(units) if _capability(facts, "billing_units") else None,
        "registered_unit_count": registered_count,
        "operations": operations,
        "conflicts": conflicts,
    }


def _worker_observation(
    facts: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    worker = facts.get("worker")
    worker = worker if isinstance(worker, Mapping) else {}
    if not _capability(facts, "worker_lease"):
        status = "unsupported"
    else:
        lease_until = _utc(worker.get("lease_until"))
        has_claim_token = worker.get("has_claim_token") is True
        generation = worker.get("execution_generation")
        has_claim = worker.get("has_claimed_by") is True or (
            isinstance(worker.get("claimed_by"), str) and bool(worker.get("claimed_by"))
        )
        has_generation = type(generation) is int and generation > 0

        if not has_claim and lease_until is None and not has_claim_token:
            status = "unclaimed"
        elif has_claim and lease_until is not None and has_claim_token and has_generation:
            status = "active_lease" if lease_until > now else "expired_lease"
        else:
            status = "unknown"

    recovery_status = worker.get("recovery_status")
    if recovery_status not in _RECOVERY_STATES:
        recovery_status = "unknown" if recovery_status is None else "unrecognized"

    if status == "active_lease":
        freshness = "within_lease"
    elif status == "expired_lease":
        freshness = "lease_expired"
    elif status == "unclaimed":
        freshness = "no_active_lease"
    else:
        freshness = "unknown"

    return {
        "status": status,
        "source": "ai_tasks.worker_lease_and_recovery_status",
        "recovery_status": recovery_status,
        "freshness": freshness,
        "lease_until": _iso(worker.get("lease_until")),
        "task_updated_at": _iso(worker.get("updated_at")),
        "evaluated_at": now.isoformat(),
    }


def _provider_unit_facts(
    unit: Mapping[str, Any],
    steps: list[Mapping[str, Any]],
    submissions: list[Mapping[str, Any]],
    evidences: list[Mapping[str, Any]],
    *,
    supported: bool,
) -> dict[str, Any]:
    unit_id = unit.get("id")
    unit_steps = [step for step in steps if step.get("billing_unit_id") == unit_id]
    unit_submissions: list[Mapping[str, Any]] = []
    for step in unit_steps:
        unit_submissions.extend(
            submission for submission in submissions if submission.get("step_id") == step.get("id")
        )

    provider_evidence: list[Mapping[str, Any]] = []
    submission_states: set[str] = set()
    provider_names: set[str] = set()
    for submission in unit_submissions:
        submission_id = submission.get("id")
        step_id = submission.get("step_id")
        submission_state = submission.get("submission_status")
        if submission_state in _SUBMISSION_STATES:
            submission_states.add(submission_state)
        provider_name = _safe_identifier(submission.get("provider"))
        if provider_name is not None:
            provider_names.add(provider_name)

        matching = [
            evidence
            for evidence in evidences
            if evidence.get("submission_id") == submission_id
            and evidence.get("step_id") == step_id
            and evidence.get("source") == "provider"
        ]
        for evidence in matching:
            validity = evidence.get("validity")
            if validity == "valid":
                provider_evidence.append(evidence)

    result_evidence = [
        item
        for item in provider_evidence
        if item.get("evidence_kind") in _PROVIDER_RESULT_KINDS
    ]
    failure_evidence = [
        item
        for item in provider_evidence
        if item.get("evidence_kind") in _PROVIDER_FAILURE_KINDS
    ]
    if not supported:
        status = "unsupported"
    elif result_evidence:
        status = "verified_success"
    elif failure_evidence:
        status = "known_failure"
    elif _registered_graph(unit.get("operation"), unit.get("step_graph_version")):
        status = "unknown"
    else:
        status = "unsupported"

    evidence_timings: list[str] = []
    timely_evidence_count = 0
    late_evidence_count = 0
    unknown_timing_count = 0
    for evidence in result_evidence:
        received_at = _utc(evidence.get("received_at"))
        matching_submission = next(
            (
                submission
                for submission in unit_submissions
                if submission.get("id") == evidence.get("submission_id")
                and submission.get("step_id") == evidence.get("step_id")
            ),
            None,
        )
        deadline = _utc(matching_submission.get("deadline_at")) if matching_submission else None
        if received_at is not None and deadline is not None:
            if received_at <= deadline:
                evidence_timings.append("timely")
                timely_evidence_count += 1
            else:
                evidence_timings.append("late")
                late_evidence_count += 1
        else:
            evidence_timings.append("unknown")
            unknown_timing_count += 1

    timing_values = set(evidence_timings)
    if len(timing_values) > 1:
        timing = "mixed"
    elif timing_values == {"late"}:
        timing = "late"
    elif timing_values == {"timely"}:
        timing = "timely"
    else:
        timing = "unknown"

    step_states = sorted(
        {
            step_state
            for step in unit_steps
            if (step_state := step.get("execution_status")) in _STEP_STATES
        }
    )
    result_state = unit.get("result_status")
    if result_state not in _UNIT_STATES:
        result_state = "unknown" if result_state is None else "unrecognized"

    observed_values = [
        value
        for value in (
            _utc(evidence.get("received_at"))
            for evidence in provider_evidence
        )
        if value is not None
    ]
    if not observed_values:
        observed_values = [
            value
            for value in (
                _utc(submission.get("updated_at"))
                for submission in unit_submissions
            )
            if value is not None
        ]

    return {
        "operation": _safe_identifier(unit.get("operation")),
        "graph_version": (
            _safe_identifier(unit.get("step_graph_version"))
            if _registered_graph(unit.get("operation"), unit.get("step_graph_version"))
            else None
        ),
        "status": status,
        "unit_result_status": result_state,
        "step_statuses": step_states,
        "submission_statuses": sorted(submission_states),
        "providers": sorted(provider_names),
        "provider_evidence_count": len(provider_evidence),
        "timing": timing,
        "timely_evidence_count": timely_evidence_count,
        "late_evidence_count": late_evidence_count,
        "unknown_timing_count": unknown_timing_count,
        "observed_at": max(observed_values).isoformat() if observed_values else None,
        "has_unverified_failed_submission": "failed" in submission_states and not failure_evidence,
    }


def _provider_observation(facts: Mapping[str, Any], units: list[Mapping[str, Any]]) -> dict[str, Any]:
    supported = all(
        _capability(facts, capability)
        for capability in ("execution_steps", "external_submissions", "result_evidence")
    )
    steps = _rows(facts, "steps")
    submissions = _rows(facts, "submissions")
    evidences = _rows(facts, "evidences")
    per_unit = [
        _provider_unit_facts(
            unit,
            steps,
            submissions,
            evidences,
            supported=supported,
        )
        for unit in units
    ]

    statuses = [item["status"] for item in per_unit]
    verified_count = statuses.count("verified_success")
    failed_count = statuses.count("known_failure")
    unsupported_count = statuses.count("unsupported")
    if not supported:
        status = "unsupported"
    elif not statuses:
        status = "unknown"
    elif verified_count == len(statuses):
        status = "verified_success"
    elif failed_count == len(statuses):
        status = "known_failure"
    elif verified_count or failed_count:
        status = "partial"
    elif unsupported_count == len(statuses):
        status = "unsupported"
    else:
        status = "unknown"

    return {
        "status": status,
        "source": "external_submissions_and_linked_result_evidence",
        "unit_count": len(per_unit) if _capability(facts, "billing_units") else None,
        "verified_unit_count": verified_count,
        "known_failure_unit_count": failed_count,
        "unknown_unit_count": statuses.count("unknown"),
        "unsupported_unit_count": unsupported_count,
        "units": per_unit,
    }


def _local_disposition(
    task: Mapping[str, Any] | object,
    facts: Mapping[str, Any],
    units: list[Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    worker = facts.get("worker")
    worker = worker if isinstance(worker, Mapping) else {}
    task_cancelled_at = _value(task, "user_cancelled_at", worker.get("user_cancelled_at"))
    if task_cancelled_at is not None:
        observations.append(
            {
                "status": "cancel_requested",
                "source": "ai_tasks.user_cancelled_at",
                "recorded_at": _iso(task_cancelled_at),
            }
        )

    for unit in units:
        cancelled_at = unit.get("user_cancelled_at")
        if cancelled_at is not None:
            observations.append(
                {
                    "status": "cancel_requested",
                    "source": "billing_units.user_cancelled_at",
                    "recorded_at": _iso(cancelled_at),
                }
            )
        deadline = _utc(unit.get("deadline_at"))
        result_status = unit.get("result_status")
        if (
            deadline is not None
            and deadline <= now
            and result_status in {"pending", "unknown"}
        ):
            observations.append(
                {
                    "status": "deadline_elapsed_unsettled",
                    "source": "billing_units.deadline_at_and_result_status",
                    "recorded_at": deadline.isoformat(),
                }
            )

    for event in _rows(facts, "policy_stops"):
        if event.get("owner_id") != _value(task, "user_id"):
            continue
        if event.get("task_id") != _value(task, "id"):
            continue
        observations.append(
            {
                "status": "policy_stopped",
                "source": "policy_stop_events",
                "reason": (
                    "auth_unavailable_12"
                    if event.get("reason") == "auth_unavailable_12"
                    else "unrecognized"
                ),
                "recorded_at": _iso(event.get("stopped_at")),
            }
        )

    for work in _rows(facts, "continuation_work"):
        if work.get("owner_id") != _value(task, "user_id"):
            continue
        if work.get("task_id") != _value(task, "id"):
            continue
        phase = work.get("phase")
        observations.append(
            {
                "status": "local_continuation",
                "source": "local_continuation_work.phase",
                "phase": phase if phase in _CONTINUATION_PHASES else "unrecognized",
                "recorded_at": _iso(work.get("updated_at")),
            }
        )

    available = [
        source
        for source, capability in (
            ("ai_tasks", "worker_lease"),
            ("billing_units", "billing_units"),
            ("policy_stop_events", "policy_stop_events"),
            ("local_continuation_work", "local_continuation_work"),
        )
        if _capability(facts, capability)
    ]
    all_sources_available = all(
        _capability(facts, capability)
        for capability in ("worker_lease", "billing_units", "policy_stop_events", "local_continuation_work")
    )
    if observations:
        status = "observed"
    elif all_sources_available:
        status = "none_recorded"
    elif not _capability(facts, "billing_units"):
        status = "unsupported"
    else:
        status = "unknown"
    return {
        "status": status,
        "source_coverage": available,
        "unit_count": len(units) if _capability(facts, "billing_units") else None,
        "observations": observations,
    }


def build_task_history_truth(
    task: Mapping[str, Any] | object,
    facts: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize persisted source, worker, provider, and local-disposition facts."""
    observation_time = _utc(now or datetime.now(timezone.utc))
    assert observation_time is not None
    facts = facts if isinstance(facts, Mapping) else {}
    units = _rows(facts, "units")
    source = _source_observation(task, facts, units)
    worker = _worker_observation(facts, observation_time)
    provider = _provider_observation(facts, units)
    local = _local_disposition(task, facts, units, observation_time)
    unit_count = source["unit_count"]
    return {
        "version": 1,
        "source": source,
        "worker": worker,
        "provider": provider,
        "local_disposition": local,
        "billing_scope": {
            "unit_count": unit_count,
            "legacy_quote_scope": (
                "first_unit" if isinstance(unit_count, int) and unit_count > 1
                else "single_unit" if unit_count == 1
                else "task_record" if unit_count == 0
                else "unknown"
            ),
            "aggregated": False,
        },
    }
