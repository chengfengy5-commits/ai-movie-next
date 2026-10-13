"""Failures raised by the AIStarsLab compatibility adapter."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AistarslabError(BusinessError):
    pass


class AistarslabBadRequest(AistarslabError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class AistarslabForbidden(AistarslabError):
    def __init__(self, detail: str) -> None:
        super().__init__(403, detail)


class AistarslabNotFound(AistarslabError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class AistarslabUnavailable(AistarslabError):
    def __init__(self, detail: str = "AIStarsLab 模型服务尚未接线") -> None:
        super().__init__(503, detail)
