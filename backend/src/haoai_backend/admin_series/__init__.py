"""Isolated administrator access to the legacy series records."""

from .http import build_admin_series_router

__all__ = ["build_admin_series_router"]
