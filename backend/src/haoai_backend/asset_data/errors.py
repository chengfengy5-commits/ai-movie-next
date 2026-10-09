"""asset_data 的可预期业务错误。"""

from haoai_backend.shared.errors import BusinessError


class AssetDataUnavailable(BusinessError):
    def __init__(self, detail: str = "素材数据服务暂不可用") -> None:
        super().__init__(503, detail)


class AssetDataNotFound(BusinessError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class AssetDataForbidden(BusinessError):
    def __init__(self, detail: str) -> None:
        super().__init__(403, detail)


class AssetDataWriteConflict(BusinessError):
    def __init__(self, detail: str = "素材数据已发生变化") -> None:
        super().__init__(409, detail)
