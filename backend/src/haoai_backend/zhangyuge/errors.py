"""Stable business failures for Zhangyuge account monitoring."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class ZhangyugeError(BusinessError):
    pass


class ZhangyugeForbidden(ZhangyugeError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class ZhangyugeUnavailable(ZhangyugeError):
    def __init__(self) -> None:
        super().__init__(503, "章鱼哥服务尚未接线")


class ZhangyugeCredentialsMissing(ZhangyugeError):
    def __init__(self) -> None:
        super().__init__(
            400,
            "未配置章鱼哥AI 的 API Key / PAT，请先在模型设置添加章鱼哥模型",
        )


class ZhangyugeCryptoUnavailable(ZhangyugeError):
    def __init__(self) -> None:
        super().__init__(503, "章鱼哥 PAT 加密配置不可用")
