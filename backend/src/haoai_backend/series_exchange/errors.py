"""Stable business failures for series exchange routes."""

from haoai_backend.shared.errors import BusinessError


class SeriesExchangeBadRequest(BusinessError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class SeriesExchangeForbidden(BusinessError):
    def __init__(self, detail: str) -> None:
        super().__init__(403, detail)


class SeriesExchangeNotFound(BusinessError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class SeriesExchangeWriteConflict(BusinessError):
    def __init__(self, detail: str = "融合资产或章节内容已发生变化") -> None:
        super().__init__(409, detail)


class SeriesExchangeUnavailable(BusinessError):
    def __init__(self, detail: str = "剧集交换服务尚未接线") -> None:
        super().__init__(503, detail)
