"""Business errors for administrator SMTP-account operations."""

from haoai_backend.shared.errors import BusinessError


class AdminSmtpAccountNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "账号不存在")


class AdminSmtpAccountInputInvalid(BusinessError):
    def __init__(self) -> None:
        super().__init__(400, "邮箱和授权码不能为空")


class AdminSmtpAccountsForbidden(BusinessError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminSmtpAccountsUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "SMTP账号管理服务尚未接线")


class SmtpCryptoUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "SMTP账号加密服务尚未接线")
