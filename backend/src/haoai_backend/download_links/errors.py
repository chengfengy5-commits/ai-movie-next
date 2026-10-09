"""Framework-independent failures for the download-link use case."""

from haoai_backend.shared.errors import BusinessError


class DownloadLinksError(BusinessError):
    """Expected download-link failures with stable HTTP semantics."""


class DownloadLinksUnavailable(DownloadLinksError):
    def __init__(self, detail: str = "下载链接服务尚未接线") -> None:
        super().__init__(503, detail)


class DownloadLinksTooManyItems(DownloadLinksError):
    def __init__(self) -> None:
        super().__init__(400, "单次最多 500 条")

