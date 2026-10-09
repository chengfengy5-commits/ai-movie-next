"""Expected task-observation failures shared by application and HTTP layers."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class TaskObservationError(BusinessError):
    """Base class for stable task-observation HTTP failures."""


class TaskObservationBadRequest(TaskObservationError):
    def __init__(self, detail: str) -> None:
        super().__init__(422, detail)


class TaskObservationNotFound(TaskObservationError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class TaskObservationForbidden(TaskObservationError):
    def __init__(self, detail: str) -> None:
        super().__init__(403, detail)


class TaskObservationConflict(TaskObservationError):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


class TaskObservationIntegrityError(TaskObservationError):
    def __init__(self, detail: str) -> None:
        super().__init__(500, detail)


class TaskObservationUnavailable(TaskObservationError):
    def __init__(self, detail: str = "任务观察服务尚未接线") -> None:
        super().__init__(503, detail)
