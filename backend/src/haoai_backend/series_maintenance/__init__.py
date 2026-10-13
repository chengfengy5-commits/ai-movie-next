"""Five legacy asset-maintenance routes as an independently wired slice."""

from .http import build_series_maintenance_router, mount_series_maintenance_routes
from .persistence import SqlAlchemySeriesMaintenanceUnitOfWork, series_maintenance_uow_factory

__all__ = [
    "SqlAlchemySeriesMaintenanceUnitOfWork",
    "build_series_maintenance_router",
    "mount_series_maintenance_routes",
    "series_maintenance_uow_factory",
]
