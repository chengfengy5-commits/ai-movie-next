"""Team series routes and application services."""

from .application import (
    claim_series,
    create_team_series,
    list_team_series,
    share_series_to_team,
    transfer_series_claim,
    unclaim_series,
    unshare_series,
)
from .http import build_series_router
from .persistence import SeriesSqlAlchemyUnitOfWork, create_series_uow_factory
from .ports import SeriesUnitOfWork, SeriesUnitOfWorkFactory

__all__ = [
    "build_series_router",
    "create_series_uow_factory",
    "SeriesSqlAlchemyUnitOfWork",
    "SeriesUnitOfWork",
    "SeriesUnitOfWorkFactory",
    "claim_series",
    "create_team_series",
    "list_team_series",
    "share_series_to_team",
    "transfer_series_claim",
    "unclaim_series",
    "unshare_series",
]
