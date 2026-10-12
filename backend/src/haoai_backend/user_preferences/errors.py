"""Infrastructure failures for the user preferences adapter."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class UserPreferencesUnavailable(BusinessError):
    def __init__(self, detail: str = "用户偏好设置服务尚未接线") -> None:
        super().__init__(503, detail)
