"""Stable business errors for fused generation."""

import asyncio

from haoai_backend.shared.errors import BusinessError


class FusedGenerationError(BusinessError):
    """Base for expected request and execution failures."""


class FusedGenerationBadRequest(FusedGenerationError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class FusedGenerationForbidden(FusedGenerationError):
    def __init__(self, detail: str) -> None:
        super().__init__(403, detail)


class FusedGenerationConflict(FusedGenerationError):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


class FusedGenerationNotFound(FusedGenerationError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class FusedGenerationUnavailable(FusedGenerationError):
    def __init__(self, detail: str = "融合生成服务尚未接线") -> None:
        super().__init__(503, detail)


class FusedGenerationPaymentRequired(FusedGenerationError):
    def __init__(self, detail: str) -> None:
        super().__init__(402, detail)


class FusedGenerationModelNotFound(FusedGenerationError):
    def __init__(self) -> None:
        super().__init__(404, "模型不存在或已禁用")


class ProviderGenerationFailure(Exception):
    """A provider failure whose detail is explicitly safe for users and storage."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class FusedGenerationDispatchedCancellation(asyncio.CancelledError):
    """Cancellation after a provider create may have been dispatched."""
