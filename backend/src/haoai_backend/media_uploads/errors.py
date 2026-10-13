"""HTTP-safe failures for the media upload compatibility boundary."""

from __future__ import annotations


class MediaUploadsError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class InvalidMediaUpload(MediaUploadsError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class MediaUploadStorageFailed(MediaUploadsError):
    def __init__(self, detail: str = "云端上传失败") -> None:
        super().__init__(502, detail)


class RetryUnsafeURL(MediaUploadsError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class RetryTooLarge(MediaUploadsError):
    def __init__(self) -> None:
        super().__init__(413, "文件大小超过限制（最大 50MB）")


class RetryDownloadFailed(MediaUploadsError):
    def __init__(self) -> None:
        super().__init__(502, "下载失败")


class RetryTransferFailed(MediaUploadsError):
    def __init__(self) -> None:
        super().__init__(502, "转存失败")
