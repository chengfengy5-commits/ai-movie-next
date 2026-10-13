"""Business errors for provider-quota administration."""

from haoai_backend.shared.errors import BusinessError


class ProviderQuotaNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "监控账号不存在")


class ProviderQuotaInputInvalid(BusinessError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class ProviderQuotaForbidden(BusinessError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class ProviderQuotaUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "供应商额度监控服务尚未接线")


class ProviderQuotaCryptoUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "供应商额度账号加密服务尚未接线")
