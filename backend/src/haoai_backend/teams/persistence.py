"""SQLAlchemy Core shared UoW, policy readers and mutation-intention queue."""

from __future__ import annotations

import random
import uuid
import warnings
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.exc import IntegrityError, SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.sql.schema import Table

from .domain import (
    AssignmentPlan,
    INVITE_CODE_ALPHABET,
    TeamConfig,
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from .errors import StaleTeamWrite, UnitOfWorkClosed
from . import tables


def now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def epoch_seconds() -> float:
    from time import time

    return time()


def new_id() -> str:
    return str(uuid.uuid4())


def new_invite_code() -> str:
    return "".join(random.choice(INVITE_CODE_ALPHABET) for _ in range(8))


def assignment_plan(
    table: Table,
    primary_key: Mapping[str, Any],
    first_values: Mapping[str, Any],
    assigned_values: Mapping[str, Any],
) -> AssignmentPlan:
    """Freeze the first loaded row and its initially dirty fields."""
    first = dict(first_values)
    assigned = dict(assigned_values)
    changed = {
        field: value
        for field, value in assigned.items()
        if field not in first or value != first[field]
    }
    return AssignmentPlan(
        table=table,
        primary_key=dict(primary_key),
        first_values=first,
        assigned_values=assigned,
        changed_values=changed,
    )


class _PendingInsert:
    __slots__ = ("table", "values")

    def __init__(self, table: Table, values: Mapping[str, Any]) -> None:
        self.table = table
        self.values = dict(values)


class _PendingAssignment:
    __slots__ = ("table", "primary_key", "first_values", "assigned_values")

    def __init__(self, plan: AssignmentPlan) -> None:
        self.table = plan.table
        self.primary_key = dict(plan.primary_key)
        self.first_values = dict(plan.first_values)
        self.assigned_values = dict(plan.assigned_values)

    def apply(self, assigned_values: Mapping[str, Any]) -> None:
        self.assigned_values.update(assigned_values)

    @property
    def changed_values(self) -> dict[str, Any]:
        return {
            field: value
            for field, value in self.assigned_values.items()
            if field not in self.first_values or value != self.first_values[field]
        }


class _PendingDelete:
    __slots__ = ("table", "primary_key")

    def __init__(self, table: Table, primary_key: Mapping[str, Any]) -> None:
        self.table = table
        self.primary_key = dict(primary_key)


def _cache_key(table: Table, primary_key: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]]:
    pk_names = [column.name for column in table.primary_key.columns]
    missing = [name for name in pk_names if name not in primary_key]
    if missing:
        raise ValueError(f"missing primary key columns for {table.name}: {missing}")
    return table.name, tuple(primary_key[name] for name in pk_names)


def _primary_key_from_row(table: Table, row: Mapping[str, Any]) -> dict[str, Any]:
    return {column.name: row[column.name] for column in table.primary_key.columns}


class TeamSqlAlchemyUnitOfWork:
    """Request-scoped Core adapter around a family-supplied Session.

    The wrapper never creates an Engine or Session. It makes the legacy
    autoflush=False intent visible: staged inserts, assignments and ORM-like
    deletes remain queued until flush/commit, while execute_immediate runs at
    its call site.
    """

    def __init__(
        self,
        session: Session,
        *,
        utc_clock: Callable[[], datetime] = now_utc_naive,
        epoch_clock: Callable[[], float] = epoch_seconds,
        id_source: Callable[[], str] = new_id,
        invite_code_source: Callable[[], str] = new_invite_code,
    ) -> None:
        self._session = session
        self._session.autoflush = False
        self._utc_clock = utc_clock
        self._epoch_clock = epoch_clock
        self._id_source = id_source
        self._invite_code_source = invite_code_source
        self._snapshots: dict[tuple[str, tuple[Any, ...]], dict[str, Any]] = {}
        self._pending: list[_PendingInsert | _PendingAssignment | _PendingDelete] = []
        self._pending_assignments: dict[tuple[str, tuple[Any, ...]], _PendingAssignment] = {}
        self._closed = False
        self._policy_reader = SqlAlchemyTeamPolicyReader(self)
        self._config_reader = SqlAlchemyTeamConfigReader(self)

    @property
    def session(self) -> Session:
        self._ensure_open()
        return self._session

    @property
    def team_reader(self) -> SqlAlchemyTeamPolicyReader:
        self._ensure_open()
        return self._policy_reader

    @property
    def config_reader(self) -> SqlAlchemyTeamConfigReader:
        self._ensure_open()
        return self._config_reader

    def _ensure_open(self) -> None:
        if self._closed:
            raise UnitOfWorkClosed()

    def ensure_clean(self) -> None:
        self._ensure_open()
        if self._pending:
            raise RuntimeError("new team unit of work contains staged writes")
        if self._session.new or self._session.dirty or self._session.deleted:
            raise RuntimeError("new team unit of work contains ORM changes")

    def now_utc_naive(self) -> datetime:
        self._ensure_open()
        return self._utc_clock()

    def epoch_seconds(self) -> float:
        self._ensure_open()
        return self._epoch_clock()

    def new_id(self) -> str:
        self._ensure_open()
        return self._id_source()

    def new_invite_code(self) -> str:
        self._ensure_open()
        return self._invite_code_source()

    def invite_code_exists(self, code: str) -> bool:
        self._ensure_open()
        statement = select(tables.team_invites.c.id).where(tables.team_invites.c.code == code).limit(1)
        return self._session.execute(statement).first() is not None

    def execute_immediate(self, statement: Any, parameters: Any = None) -> Any:
        self._ensure_open()
        if parameters is None:
            return self._session.execute(statement)
        return self._session.execute(statement, parameters)

    def load_row(
        self,
        table: Table,
        primary_key: Mapping[str, Any],
        *conditions: Any,
    ) -> Mapping[str, Any] | None:
        self._ensure_open()
        key = _cache_key(table, primary_key)
        predicates = [table.c[name] == value for name, value in primary_key.items()]
        predicates.extend(conditions)
        row = self._session.execute(
            select(table).where(and_(*predicates))
        ).mappings().first()
        if row is None:
            return None
        if key not in self._snapshots:
            self._snapshots[key] = dict(row)
        return dict(self._snapshots[key])

    def load_rows(
        self,
        table: Table,
        *conditions: Any,
        order_by: Sequence[Any] = (),
        limit: int | None = None,
    ) -> list[Mapping[str, Any]]:
        self._ensure_open()
        statement = select(table)
        if conditions:
            statement = statement.where(and_(*conditions))
        if order_by:
            statement = statement.order_by(*order_by)
        if limit is not None:
            statement = statement.limit(limit)
        rows = self._session.execute(statement).mappings().all()
        result: list[Mapping[str, Any]] = []
        for row in rows:
            values = dict(row)
            key = _cache_key(table, _primary_key_from_row(table, values))
            if key not in self._snapshots:
                self._snapshots[key] = values
            result.append(dict(self._snapshots[key]))
        return result

    def refresh_row(
        self,
        table: Table,
        primary_key: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        self._ensure_open()
        key = _cache_key(table, primary_key)
        self._snapshots.pop(key, None)
        predicates = [table.c[name] == value for name, value in primary_key.items()]
        row = self._session.execute(
            select(table).where(and_(*predicates))
        ).mappings().first()
        if row is None:
            return None
        values = dict(row)
        self._snapshots[key] = values
        return dict(values)

    def stage_insert(self, table: Table, values: Mapping[str, Any]) -> None:
        self._ensure_open()
        self._pending.append(_PendingInsert(table, values))

    def stage_assignment(self, plan: AssignmentPlan) -> None:
        self._ensure_open()
        key = _cache_key(plan.table, plan.primary_key)
        pending = self._pending_assignments.get(key)
        if pending is None:
            if not plan.assigned_values or not plan.changed_values:
                return
            pending = _PendingAssignment(plan)
            self._pending_assignments[key] = pending
            self._pending.append(pending)
            return
        if pending.first_values != dict(plan.first_values):
            raise ValueError("assignments for one primary key must share the first snapshot")
        pending.apply(plan.assigned_values)

    def stage_delete(self, table: Table, primary_key: Mapping[str, Any]) -> None:
        self._ensure_open()
        self._pending.append(_PendingDelete(table, primary_key))

    def flush(self) -> None:
        self._ensure_open()
        pending = list(self._pending)
        for operation in pending:
            if isinstance(operation, _PendingInsert):
                self._session.execute(insert(operation.table).values(**operation.values))
            elif isinstance(operation, _PendingAssignment):
                changed = operation.changed_values
                if not changed:
                    continue
                predicate = and_(
                    *(operation.table.c[name] == value for name, value in operation.primary_key.items())
                )
                result = self._session.execute(
                    update(operation.table).where(predicate).values(**changed)
                )
                if result.rowcount != 1:
                    raise StaleTeamWrite(
                        f"dirty UPDATE on {operation.table.name} did not match one row"
                    )
            else:
                predicate = and_(
                    *(operation.table.c[name] == value for name, value in operation.primary_key.items())
                )
                result = self._session.execute(delete(operation.table).where(predicate))
                if result.rowcount == 0:
                    warnings.warn(
                        f"DELETE on {operation.table.name} matched no row",
                        SAWarning,
                        stacklevel=2,
                    )
        self._session.flush()
        self._pending.clear()
        self._pending_assignments.clear()

    def commit(self) -> None:
        self._ensure_open()
        self.flush()
        self._session.commit()
        self._snapshots.clear()

    def rollback(self) -> None:
        self._ensure_open()
        self._session.rollback()
        self._pending.clear()
        self._pending_assignments.clear()
        self._snapshots.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._session.close()
        self._closed = True

    def is_integrity_error(self, error: BaseException) -> bool:
        return isinstance(error, IntegrityError)

    def load_team(self, team_id: str) -> TeamRecord | None:
        row = self.load_row(tables.teams, {"id": team_id})
        if row is None:
            return None
        return TeamRecord(
            id=row["id"],
            name=row["name"],
            owner_id=row["owner_id"],
            created_at=row["created_at"],
        )

    def load_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembershipRecord | None:
        self._ensure_open()
        row = self._session.execute(
            select(tables.team_members).where(
                tables.team_members.c.team_id == team_id,
                tables.team_members.c.user_id == user_id,
            )
        ).mappings().first()
        if row is None:
            return None
        values = dict(row)
        key = _cache_key(tables.team_members, {"id": values["id"]})
        if key not in self._snapshots:
            self._snapshots[key] = values
        snapshot = self._snapshots[key]
        return TeamMembershipRecord(
            id=snapshot["id"],
            team_id=snapshot["team_id"],
            user_id=snapshot["user_id"],
            role=snapshot["role"],
            permissions=snapshot["permissions"],
            joined_at=snapshot["joined_at"],
        )

    def load_users(self, user_ids: Sequence[str]) -> Mapping[str, UserRecord]:
        self._ensure_open()
        if not user_ids:
            return {}
        rows = self._session.execute(
            select(tables.users).where(tables.users.c.id.in_(list(user_ids)))
        ).mappings().all()
        users: dict[str, UserRecord] = {}
        for row in rows:
            values = dict(row)
            key = _cache_key(tables.users, {"id": values["id"]})
            if key not in self._snapshots:
                self._snapshots[key] = values
            snapshot = self._snapshots[key]
            users[snapshot["id"]] = UserRecord(
                id=snapshot["id"],
                username=snapshot["username"],
                email=snapshot["email"],
                avatar_url=snapshot["avatar_url"],
            )
        return users

    def load_first_config(self) -> TeamConfig:
        self._ensure_open()
        row = self._session.execute(
            select(tables.system_configs).limit(1)
        ).mappings().first()
        return TeamConfig.from_row(row)


def create_team_uow_factory(
    session_factory: Callable[[], Session],
    *,
    utc_clock: Callable[[], datetime] = now_utc_naive,
    epoch_clock: Callable[[], float] = epoch_seconds,
    id_source: Callable[[], str] = new_id,
    invite_code_source: Callable[[], str] = new_invite_code,
) -> Callable[[], TeamSqlAlchemyUnitOfWork]:
    """Bind explicit shared sources to a family-owned Session factory."""

    def create() -> TeamSqlAlchemyUnitOfWork:
        return TeamSqlAlchemyUnitOfWork(
            session_factory(),
            utc_clock=utc_clock,
            epoch_clock=epoch_clock,
            id_source=id_source,
            invite_code_source=invite_code_source,
        )

    return create


class SqlAlchemyTeamPolicyReader:
    """Same-Session team and member snapshot reader."""

    def __init__(self, uow: TeamSqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    def load_team(self, team_id: str) -> TeamRecord | None:
        return self._uow.load_team(team_id)

    def load_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembershipRecord | None:
        return self._uow.load_membership(team_id, user_id)

    def load_users(self, user_ids: Sequence[str]) -> Mapping[str, UserRecord]:
        return self._uow.load_users(user_ids)


class SqlAlchemyTeamConfigReader:
    """Reads the first system-config row without caching or environment access."""

    def __init__(self, uow: TeamSqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    def load_first_config(self) -> TeamConfig:
        return self._uow.load_first_config()
