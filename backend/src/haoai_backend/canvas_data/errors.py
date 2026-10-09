"""Expected business failures for chapter canvas operations."""

from haoai_backend.shared.errors import BusinessError


class CanvasDataError(BusinessError):
    """Base class translated by the canvas HTTP adapter."""


class CanvasDataNotFound(CanvasDataError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class CanvasDataForbidden(CanvasDataError):
    def __init__(self, detail: str) -> None:
        super().__init__(403, detail)


class CanvasDataBadRequest(CanvasDataError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class CanvasDataConflict(CanvasDataError):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


class CanvasDataUnavailable(CanvasDataError):
    def __init__(self, detail: str = "画布数据服务尚未接线") -> None:
        super().__init__(503, detail)
