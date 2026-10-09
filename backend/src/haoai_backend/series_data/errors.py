"""Framework-independent errors for series source-data use cases."""

from haoai_backend.shared.errors import BusinessError


class SeriesDataError(BusinessError):
    """Base class for expected series-data HTTP failures."""


class SeriesDataNotFound(SeriesDataError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class SeriesDataForbidden(SeriesDataError):
    def __init__(self, detail: str) -> None:
        super().__init__(403, detail)


class SeriesDataBadRequest(SeriesDataError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class SeriesDataUnavailable(SeriesDataError):
    def __init__(self, detail: str = "来源数据服务尚未接线") -> None:
        super().__init__(503, detail)


class SeriesDataConflict(SeriesDataError):
    def __init__(self, detail: str = "来源数据已变化，请重新读取") -> None:
        super().__init__(409, detail)
