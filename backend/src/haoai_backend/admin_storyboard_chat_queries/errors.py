"""Stable errors for administrator storyboard and chat queries."""

from haoai_backend.shared.errors import BusinessError


class StoryboardAssetNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "分镜资产不存在")


class ChatMessageNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "消息不存在")


class AdminStoryboardChatQueriesForbidden(BusinessError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminStoryboardChatQueriesUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "分镜资产与聊天消息查询服务尚未接线")
