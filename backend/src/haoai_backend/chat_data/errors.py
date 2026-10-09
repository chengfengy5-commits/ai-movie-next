"""Expected chat-data failures shared by the application and HTTP adapter."""

from haoai_backend.shared.errors import BusinessError


class ChatDataError(BusinessError):
    """Base class for expected chat-data failures."""


class ChatDataNotFound(ChatDataError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class ChatDataForbidden(ChatDataError):
    def __init__(self, detail: str) -> None:
        super().__init__(403, detail)


class ChatDataUnavailable(ChatDataError):
    def __init__(self, detail: str = "聊天数据服务尚未接线") -> None:
        super().__init__(503, detail)
