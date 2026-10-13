"""Stable business failures for series asset-maintenance routes."""

from haoai_backend.shared.errors import BusinessError


class SeriesMaintenanceBadRequest(BusinessError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class SeriesMaintenanceForbidden(BusinessError):
    def __init__(self, detail: str = "无权访问该剧集") -> None:
        super().__init__(403, detail)


class SeriesMaintenanceNotFound(BusinessError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class SeriesMaintenanceUnavailable(BusinessError):
    def __init__(self, detail: str = "剧集资产整理服务尚未接线") -> None:
        super().__init__(503, detail)
