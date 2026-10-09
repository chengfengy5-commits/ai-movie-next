"""Expected team-domain errors shared by the family HTTP adapters."""

from haoai_backend.shared.errors import BusinessError


class TeamError(BusinessError):
    """A business error that keeps its route-level status and detail."""


class TeamsUnavailable(TeamError):
    def __init__(self, detail: str = "团队服务尚未接线") -> None:
        super().__init__(503, detail)


class TeamNotFound(TeamError):
    def __init__(self, detail: str = "团队不存在") -> None:
        super().__init__(404, detail)


class TeamForbidden(TeamError):
    def __init__(self, detail: str = "你不是该团队成员") -> None:
        super().__init__(403, detail)


class TeamBadRequest(TeamError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class TeamLimitExceeded(TeamError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class JoinQuotaExceeded(TeamError):
    def __init__(self) -> None:
        super().__init__(429, "Rate limit exceeded: 10 per 1 hour")


class StaleTeamWrite(RuntimeError):
    """A dirty primary-key UPDATE matched no row, like ORM stale-row detection."""


class UnitOfWorkClosed(RuntimeError):
    """A family tried to use a completed request-scoped team unit of work."""
