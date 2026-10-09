"""Framework-independent ports for the shared teams contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from haoai_backend.shared.identity import TrustedActor

from .domain import AssignmentPlan, TeamConfig, TeamMembershipRecord, TeamRecord, UserRecord

if TYPE_CHECKING:
    from .management.ports import ManagementUnitOfWorkFactory
    from .series.ports import SeriesUnitOfWorkFactory


class Clock(Protocol):
    def now_utc_naive(self) -> datetime: ...


class EpochClock(Protocol):
    def epoch_seconds(self) -> float: ...


class IdSource(Protocol):
    def new_id(self) -> str: ...


class InviteCodeSource(Protocol):
    def new_code(self) -> str: ...


class JoinQuota(Protocol):
    def check(self, remote_ip: str | None, pathname: str) -> None: ...


class TeamPolicyReader(Protocol):
    def load_team(self, team_id: str) -> TeamRecord | None: ...

    def load_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembershipRecord | None: ...

    def load_users(self, user_ids: Sequence[str]) -> Mapping[str, UserRecord]: ...


class ConfigReader(Protocol):
    def load_first_config(self) -> TeamConfig: ...


@runtime_checkable
class TeamsUnitOfWork(TeamPolicyReader, ConfigReader, Protocol):
    """One request-owned business Session with shared mutation semantics.

    Read queries may use execute_immediate(). ORM-like intentions must use
    stage_insert(), stage_assignment(), and stage_delete(); source bulk DML
    uses execute_immediate() at its original call site.
    """

    @property
    def session(self) -> Any: ...

    def now_utc_naive(self) -> datetime: ...

    def epoch_seconds(self) -> float: ...

    def new_id(self) -> str: ...

    def new_invite_code(self) -> str: ...

    def invite_code_exists(self, code: str) -> bool: ...

    def execute_immediate(self, statement: Any, parameters: Any = None) -> Any: ...

    def load_row(
        self,
        table: Any,
        primary_key: Mapping[str, Any],
        *conditions: Any,
    ) -> Mapping[str, Any] | None: ...

    def load_rows(
        self,
        table: Any,
        *conditions: Any,
        order_by: Sequence[Any] = (),
        limit: int | None = None,
    ) -> list[Mapping[str, Any]]: ...

    def refresh_row(
        self,
        table: Any,
        primary_key: Mapping[str, Any],
    ) -> Mapping[str, Any] | None: ...

    def stage_insert(self, table: Any, values: Mapping[str, Any]) -> None: ...

    def stage_assignment(self, plan: AssignmentPlan) -> None: ...

    def stage_delete(self, table: Any, primary_key: Mapping[str, Any]) -> None: ...

    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...

    def ensure_clean(self) -> None: ...

    def is_integrity_error(self, error: BaseException) -> bool: ...


TeamsUnitOfWorkFactory = Callable[[], TeamsUnitOfWork]


@dataclass(frozen=True, slots=True)
class TeamFamilyFactories:
    management_uow_factory: ManagementUnitOfWorkFactory | None
    series_uow_factory: SeriesUnitOfWorkFactory | None
    reporting_uow_factory: TeamsUnitOfWorkFactory | None
    resolve_actor: Callable[..., Any] | None
    join_quota: JoinQuota | None


@dataclass(frozen=True, slots=True)
class TeamApplicationContext:
    uow: TeamsUnitOfWork
    actor: TrustedActor
