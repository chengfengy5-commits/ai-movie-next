"""Expected failures of the guarded Canvas source API."""

from haoai_backend.shared.errors import BusinessError


class CanvasSourceUnavailable(BusinessError):
    def __init__(self, detail: str = "画布来源服务暂不可用") -> None:
        super().__init__(503, detail)


class CanvasSourceNotFound(BusinessError):
    def __init__(self, detail: str = "章节不存在") -> None:
        super().__init__(404, detail)


class CanvasSourceForbidden(BusinessError):
    def __init__(self, detail: str = "无权访问该剧集") -> None:
        super().__init__(403, detail)


class CanvasSourceConflict(BusinessError):
    def __init__(self, detail: str = "画布来源已变化，请重新读取") -> None:
        super().__init__(409, detail)
