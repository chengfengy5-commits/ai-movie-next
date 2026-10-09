"""Read-only access policy for current series and team records."""

from .application import require_series_access
from .domain import ActorIdentity, SeriesAccessRecord, TeamMembership
from .errors import SeriesAccessDenied, SeriesNotFound

__all__ = [
    "ActorIdentity",
    "SeriesAccessDenied",
    "SeriesAccessRecord",
    "SeriesNotFound",
    "TeamMembership",
    "require_series_access",
]
