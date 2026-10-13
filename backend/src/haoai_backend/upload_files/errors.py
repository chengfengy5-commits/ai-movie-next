"""Expected failures from the upload-file access boundary."""


class UploadFilesError(Exception):
    status_code = 500
    detail = "文件访问失败"


class UploadFilesUnavailable(UploadFilesError):
    status_code = 503
    detail = "文件服务尚未接线"


class EmptyUploadPath(UploadFilesError):
    status_code = 400
    detail = "文件路径不能为空"


class UnsafeUploadPath(UploadFilesError):
    status_code = 403
    detail = "非法文件路径"


class UploadFileNotFound(UploadFilesError):
    status_code = 404
    detail = "文件不存在"
