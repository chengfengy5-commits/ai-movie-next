"""Read minimal, owner-scoped task history truth facts through one Session."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import MetaData, Table, and_, func, inspect, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def _reflected_table(session: Session, name: str) -> Table | None:
    connection = session.connection()
    if not inspect(connection).has_table(name):
        return None
    return Table(name, MetaData(), autoload_with=connection)


def _read_rows(
    session: Session,
    table: Table | None,
    names: Sequence[str],
    *predicates: Any,
) -> list[dict[str, Any]]:
    if table is None:
        return []
    selected = [table.c[name] for name in names if name in table.c]
    if not selected:
        return []
    order_by = [
        table.c[name]
        for name in ("id", "unit_id", "step_id", "phase", "reason", "stopped_at", "updated_at")
        if name in table.c
    ]
    statement = select(*selected).where(*predicates)
    if order_by:
        statement = statement.order_by(*order_by)
    rows = session.execute(statement).mappings().all()
    return [dict(row) for row in rows]


def _submission_task_id_values(session: Session, task_ids_column: Any) -> Any | None:
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "sqlite":
        return func.json_each(task_ids_column).table_valued("key", "value").alias(
            "history_submission_task_id"
        )
    if dialect_name == "postgresql":
        function_name = (
            "jsonb_array_elements_text"
            if isinstance(task_ids_column.type, JSONB)
            else "json_array_elements_text"
        )
        function = getattr(func, function_name)
        return function(task_ids_column).table_valued("value").render_derived(
            name="history_submission_task_id"
        )
    return None


def _capabilities_false() -> dict[str, bool]:
    return {
        "worker_lease": False,
        "task_submission_intent": False,
        "billing_units": False,
        "execution_steps": False,
        "external_submissions": False,
        "result_evidence": False,
        "policy_stop_events": False,
        "local_continuation_work": False,
    }


def load_task_history_facts(
    session: Session,
    *,
    owner_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Read only safe columns and never select raw payloads or claim secrets."""
    facts: dict[str, Any] = {
        "capabilities": _capabilities_false(),
        "worker": {},
        "intents": [],
        "units": [],
        "steps": [],
        "submissions": [],
        "evidences": [],
        "policy_stops": [],
        "continuation_work": [],
    }
    try:
        with session.no_autoflush:
            task_table = _reflected_table(session, "ai_tasks")
            if task_table is None or not {"id", "user_id"}.issubset(task_table.c.keys()):
                return facts

            task_columns = {
                name
                for name in (
                    "lease_until",
                    "execution_generation",
                    "recovery_status",
                    "updated_at",
                    "user_cancelled_at",
                )
                if name in task_table.c
            }
            selected = [task_table.c[name] for name in sorted(task_columns)]
            if "claimed_by" in task_table.c:
                selected.append(
                    and_(
                        task_table.c.claimed_by.is_not(None),
                        task_table.c.claimed_by != "",
                    ).label("has_claimed_by")
                )
            if not selected:
                selected = [task_table.c.id]
            has_claim_token = "claim_token" in task_table.c
            if has_claim_token:
                selected.append(
                    and_(
                        task_table.c.claim_token.is_not(None),
                        task_table.c.claim_token != "",
                    ).label("has_claim_token")
                )
            task_rows = session.execute(
                select(*selected).where(
                    task_table.c.id == task_id,
                    task_table.c.user_id == owner_id,
                )
            ).mappings().one_or_none()
            if task_rows is None:
                return facts
            worker = dict(task_rows)
            if "claimed_by" not in task_table.c:
                worker["has_claimed_by"] = None
            if "claim_token" not in task_table.c:
                worker["has_claim_token"] = None
            facts["worker"] = worker
            facts["capabilities"]["worker_lease"] = {
                "claimed_by",
                "lease_until",
                "execution_generation",
                "recovery_status",
                "updated_at",
                "claim_token",
            }.issubset(task_table.c.keys())

            intents_table = _reflected_table(session, "task_submissions")
            has_intent_columns = (
                intents_table is not None
                and {"user_id", "operation", "task_ids"}.issubset(intents_table.c.keys())
            )
            task_id_values = (
                _submission_task_id_values(session, intents_table.c.task_ids)
                if has_intent_columns and intents_table is not None
                else None
            )
            facts["capabilities"]["task_submission_intent"] = task_id_values is not None
            if intents_table is not None and task_id_values is not None:
                task_id_value = task_id_values.c.get("value")
                if task_id_value is None:
                    task_id_value = task_id_values.c.value
                intent_operations = session.execute(
                    select(intents_table.c.operation)
                    .select_from(intents_table.join(task_id_values, true()))
                    .where(
                        intents_table.c.user_id == owner_id,
                        task_id_value == task_id,
                    )
                    .distinct()
                    .order_by(intents_table.c.operation)
                    .limit(2)
                ).scalars().all()
                facts["intents"] = [{"operation": operation} for operation in intent_operations]

            units_table = _reflected_table(session, "billing_units")
            facts["capabilities"]["billing_units"] = (
                units_table is not None
                and {"id", "task_id", "operation", "step_graph_version"}.issubset(units_table.c.keys())
            )
            if not facts["capabilities"]["billing_units"]:
                return facts

            facts["units"] = _read_rows(
                session,
                units_table,
                (
                    "id",
                    "task_id",
                    "operation",
                    "step_graph_version",
                    "result_status",
                    "first_intent_at",
                    "deadline_at",
                    "success_confirmed_at",
                    "user_cancelled_at",
                    "cancellation_reason",
                    "created_at",
                    "updated_at",
                ),
                units_table.c.task_id == task_id,
            )
            unit_ids = [row["id"] for row in facts["units"] if isinstance(row.get("id"), str)]

            steps_table = _reflected_table(session, "execution_steps")
            facts["capabilities"]["execution_steps"] = (
                steps_table is not None
                and {"id", "billing_unit_id"}.issubset(steps_table.c.keys())
            )
            step_ids: list[str] = []
            if facts["capabilities"]["execution_steps"]:
                facts["steps"] = _read_rows(
                    session,
                    steps_table,
                    (
                        "id",
                        "billing_unit_id",
                        "step_key",
                        "required",
                        "step_order",
                        "execution_status",
                        "created_at",
                        "updated_at",
                    ),
                    steps_table.c.billing_unit_id.in_(unit_ids),
                )
                step_ids = [row["id"] for row in facts["steps"] if isinstance(row.get("id"), str)]

            submissions_table = _reflected_table(session, "external_submissions")
            facts["capabilities"]["external_submissions"] = (
                submissions_table is not None
                and {"id", "step_id", "provider", "submission_status"}.issubset(
                    submissions_table.c.keys()
                )
            )
            if facts["capabilities"]["external_submissions"] and step_ids:
                facts["submissions"] = _read_rows(
                    session,
                    submissions_table,
                    (
                        "id",
                        "step_id",
                        "provider",
                        "submission_status",
                        "submitted_at",
                        "deadline_at",
                        "created_at",
                        "updated_at",
                    ),
                    submissions_table.c.step_id.in_(step_ids),
                )
            evidence_table = _reflected_table(session, "result_evidence")
            facts["capabilities"]["result_evidence"] = (
                evidence_table is not None
                and {"id", "step_id", "source", "validity"}.issubset(evidence_table.c.keys())
            )
            if facts["capabilities"]["result_evidence"] and step_ids:
                facts["evidences"] = _read_rows(
                    session,
                    evidence_table,
                    (
                        "id",
                        "step_id",
                        "submission_id",
                        "source",
                        "evidence_kind",
                        "received_at",
                        "validity",
                        "application_status",
                        "applied_at",
                    ),
                    evidence_table.c.step_id.in_(step_ids),
                )

            policy_table = _reflected_table(session, "policy_stop_events")
            facts["capabilities"]["policy_stop_events"] = (
                policy_table is not None
                and {"owner_id", "task_id", "unit_id", "reason", "stopped_at"}.issubset(
                    policy_table.c.keys()
                )
            )
            if facts["capabilities"]["policy_stop_events"]:
                facts["policy_stops"] = _read_rows(
                    session,
                    policy_table,
                    ("owner_id", "task_id", "unit_id", "reason", "stopped_at"),
                    policy_table.c.owner_id == owner_id,
                    policy_table.c.task_id == task_id,
                    policy_table.c.unit_id.in_(unit_ids),
                )

            continuation_table = _reflected_table(session, "local_continuation_work")
            facts["capabilities"]["local_continuation_work"] = (
                continuation_table is not None
                and {"owner_id", "task_id", "unit_id", "phase"}.issubset(
                    continuation_table.c.keys()
                )
            )
            if facts["capabilities"]["local_continuation_work"]:
                facts["continuation_work"] = _read_rows(
                    session,
                    continuation_table,
                    (
                        "owner_id",
                        "task_id",
                        "unit_id",
                        "phase",
                        "updated_at",
                    ),
                    continuation_table.c.owner_id == owner_id,
                    continuation_table.c.task_id == task_id,
                    continuation_table.c.unit_id.in_(unit_ids),
                )
    except SQLAlchemyError:
        # A partially deployed optional observation table must not hide the old receipt.
        return {
            "capabilities": _capabilities_false(),
            "worker": {},
            "units": [],
            "steps": [],
            "submissions": [],
            "evidences": [],
            "policy_stops": [],
            "continuation_work": [],
        }
    return facts
