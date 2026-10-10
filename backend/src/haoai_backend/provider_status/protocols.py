"""The fixed one-GET status protocols supported by the legacy providers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping


StatusReader = Callable[[Any], tuple[str, Any]]
ResultReader = Callable[[Any, str], Any]


@dataclass(frozen=True, slots=True)
class ProviderProtocol:
    """Immutable URL, headers, status and result rules for one provider."""

    provider: str
    default_base_url: str
    timeout_seconds: float
    use_ipv4_connector: bool
    extra_headers: tuple[tuple[str, str], ...]
    status_reader: StatusReader
    result_reader: ResultReader
    poll_endpoint: str = "/v1/videos/{external_task_id}"
    content_endpoint: str | None = None

    def headers(self, api_key: str) -> Mapping[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            **dict(self.extra_headers),
        }

    def poll_url(self, base_url: str, external_task_id: str) -> str:
        return self._api_base(base_url) + self.poll_endpoint.format(
            external_task_id=external_task_id
        )

    def content_url(self, base_url: str, external_task_id: str) -> str:
        if self.content_endpoint is None:
            return ""
        return self._api_base(base_url) + self.content_endpoint.format(
            external_task_id=external_task_id
        )

    def parse_status(self, response_data: Any) -> tuple[str, Any]:
        return self.status_reader(response_data)

    def extract_result(self, response_data: Any, content_url: str) -> Any:
        return self.result_reader(response_data, content_url)

    def _api_base(self, base_url: str) -> str:
        api_base = (base_url or self.default_base_url).rstrip("/")
        if api_base.endswith("/v1"):
            api_base = api_base[:-3]
        return api_base.rstrip("/")


def _exact_statuses(
    completed: tuple[str, ...], failed: tuple[str, ...]
) -> StatusReader:
    def read(data: Any) -> tuple[str, Any]:
        status = data.get("status", "")
        if status in completed:
            return "completed", data
        if status in failed:
            return "failed", data
        return "pending", data

    return read


def _lower_statuses(
    completed: tuple[str, ...], failed: tuple[str, ...]
) -> StatusReader:
    def read(data: Any) -> tuple[str, Any]:
        status = (data.get("status") or "").lower()
        if status in completed:
            return "completed", data
        if status in failed:
            return "failed", data
        return "pending", data

    return read


def _zhangyuge_status(data: Any) -> tuple[str, Any]:
    return _exact_statuses(("completed",), ("failed",))(data)


def _manxiaobai_status(data: Any) -> tuple[str, Any]:
    return _exact_statuses(
        ("completed", "done", "SUCCESS", "succeeded"), ("failed", "FAILURE")
    )(data)


def _xai_status(data: Any) -> tuple[str, Any]:
    status = data.get("status", "")
    if status == "done":
        return "completed", data
    if status == "failed":
        return "failed", data
    if status == "expired":
        return "failed", {"error": "请求已过期"}
    return "pending", data


def _manxueapi_status(data: Any) -> tuple[str, Any]:
    return _exact_statuses(
        ("completed", "success", "succeeded"), ("failed", "error", "cancelled")
    )(data)


def _geeknow_status(data: Any) -> tuple[str, Any]:
    return _lower_statuses(
        ("completed", "success", "succeeded"),
        ("failed", "failure", "error", "cancelled", "expired"),
    )(data)


def _snumom_status(data: Any) -> tuple[str, Any]:
    return _exact_statuses(("completed",), ("failed",))(data)


def _biglongxia_status(data: Any) -> tuple[str, Any]:
    return _exact_statuses(("completed",), ("failed",))(data)


def _heima_status(data: Any) -> tuple[str, Any]:
    inner = data.get("data") or {}
    raw_status = (inner.get("status") or data.get("status") or "").lower()
    if raw_status in ("completed", "complete", "succeeded", "success", "done", "finished"):
        return "completed", inner
    if raw_status in (
        "failed",
        "failure",
        "error",
        "cancelled",
        "canceled",
        "expired",
    ):
        return "failed", inner
    return "pending", data


def _yu25_status(data: Any) -> tuple[str, Any]:
    return _lower_statuses(
        ("completed", "complete", "succeeded", "success", "done"),
        ("failed", "failure", "error", "cancelled", "canceled", "expired"),
    )(data)


def _path_value(data: Any, path: str, *, allow_list_indexes: bool) -> Any:
    value = data
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif allow_list_indexes and isinstance(value, list) and part.isdigit():
            index = int(part)
            value = value[index] if index < len(value) else None
        else:
            return None
    return value


_YU25_BEIYONG_STATUS_PATHS = (
    "status",
    "state",
    "data.status",
    "data.state",
    "result.status",
    "data.data.status",
    "data.data.state",
    "result.state",
    "video.status",
    "video.state",
    "output.status",
)


def _yu25_beiyong_status(data: Any) -> tuple[str, Any]:
    status = ""
    for path in _YU25_BEIYONG_STATUS_PATHS:
        value = _path_value(data, path, allow_list_indexes=False)
        if isinstance(value, str) and value.strip():
            status = value.strip().lower()
            break
    if status in ("completed", "complete", "succeeded", "success", "done", "finished", "ready"):
        return "completed", data
    if status in (
        "failed",
        "failure",
        "error",
        "cancelled",
        "canceled",
        "rejected",
        "expired",
    ):
        return "failed", data
    return "pending", data


def _haoai_status(data: Any) -> tuple[str, Any]:
    return _exact_statuses(("completed", "done"), ("failed",))(data)


def _yiyun_status(data: Any) -> tuple[str, Any]:
    return _exact_statuses(
        ("completed", "success", "succeeded"), ("failed", "error", "cancelled")
    )(data)


def _suqing_status(data: Any) -> tuple[str, Any]:
    return _exact_statuses(
        ("completed", "success", "succeeded"), ("failed", "error", "cancelled")
    )(data)


def _default_result(data: Any, _content_url: str) -> Any:
    video_raw = data.get("video", "")
    if isinstance(video_raw, str) and video_raw.startswith("http"):
        return video_raw
    if isinstance(video_raw, dict):
        url = video_raw.get("url", "")
        if url:
            return url
    return data.get("metadata", {}).get("url", "") or data.get("result_url", "")


def _xai_result(data: Any, _content_url: str) -> Any:
    video_obj = data.get("video", {})
    if isinstance(video_obj, dict):
        url = video_obj.get("url", "")
        if url:
            return url
    elif isinstance(video_obj, str) and video_obj.startswith("http"):
        return video_obj
    return None


def _manxueapi_result(data: Any, _content_url: str) -> Any:
    video_obj = data.get("video")
    if isinstance(video_obj, dict):
        url = video_obj.get("url", "")
        if url:
            return url

    video_url = data.get("video_url", "")
    if video_url:
        return video_url

    output = data.get("output")
    if isinstance(output, dict):
        url = output.get("url", "")
        if url:
            return url
    elif isinstance(output, list) and len(output) > 0:
        url = output[0].get("url", "")
        if url:
            return url

    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        url = metadata.get("url", "")
        if url:
            return url

    result = data.get("result")
    if isinstance(result, dict):
        url = result.get("url", "")
        if url:
            return url

    result_url = data.get("result_url", "")
    if result_url:
        return result_url

    url_field = data.get("url", "")
    if url_field and isinstance(url_field, str) and url_field.startswith("http"):
        return url_field
    return None


def _geeknow_result(data: Any, _content_url: str) -> Any:
    content = data.get("content") or {}
    url = content.get("video_url") or content.get("url") or ""
    if not url:
        output = data.get("output")
        if isinstance(output, dict):
            url = output.get("url") or ""
        elif isinstance(output, list) and len(output) > 0:
            url = output[0].get("url") or ""
    if not url:
        url = (
            data.get("video_url")
            or data.get("url")
            or ((data.get("detail") or {}).get("url") or "")
        )
    if not url:
        data_list = data.get("data")
        if isinstance(data_list, list) and len(data_list) > 0:
            url = data_list[0].get("url") or ""
    return url or ""


def _snumom_result(data: Any, _content_url: str) -> Any:
    return data.get("video_url") or data.get("url") or data.get("result_url") or ""


def _biglongxia_result(data: Any, _content_url: str) -> Any:
    return data.get("url") or data.get("video_url") or data.get("result_url") or ""


def _heima_result(_data: Any, content_url: str) -> Any:
    return content_url


def _yu25_result(data: Any, _content_url: str) -> Any:
    result_obj = data.get("result") or {}
    return (
        result_obj.get("video_url")
        or data.get("video_url")
        or data.get("result_url")
        or data.get("url")
        or ""
    )


_YU25_BEIYONG_RESULT_PATHS = (
    "video_url",
    "output_url",
    "download_url",
    "result_url",
    "url",
    "data.video_url",
    "data.output_url",
    "data.download_url",
    "data.result_url",
    "data.url",
    "data.data.video_url",
    "data.data.output_url",
    "data.data.download_url",
    "data.data.result_url",
    "data.data.url",
    "result.video_url",
    "result.output_url",
    "result.download_url",
    "result.url",
    "video.video_url",
    "video.download_url",
    "video.url",
    "output.video_url",
    "output.url",
    "outputs.0.video_url",
    "outputs.0.url",
    "videos.0.video_url",
    "videos.0.url",
    "data.outputs.0.video_url",
    "data.outputs.0.url",
    "choices.0.message.content",
    "choices.0.text",
)


def _yu25_beiyong_result(data: Any, content_url: str) -> Any:
    for path in _YU25_BEIYONG_RESULT_PATHS:
        value = _path_value(data, path, allow_list_indexes=True)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if text.startswith(("http://", "https://", "/")) and " " not in text:
                return text
            match = re.search(r"https?://[^\s<>\"')\]]+", text)
            if match:
                return match.group(0).rstrip(".,;:!?")
    return content_url


def _haoai_result(data: Any, content_url: str) -> Any:
    url = data.get("video_url", "")
    if url:
        return url
    video_obj = data.get("video", "")
    if isinstance(video_obj, dict):
        url = video_obj.get("url", "")
        if url:
            return url
    elif isinstance(video_obj, str) and video_obj.startswith("http"):
        return video_obj
    url = data.get("url", "")
    if url:
        return url
    return content_url


def _yiyun_result(data: Any, _content_url: str) -> Any:
    url = data.get("video_url", "") or data.get("url", "")
    if url:
        return url
    urls = data.get("urls")
    if isinstance(urls, list) and len(urls) > 0:
        first_url = urls[0]
        return first_url.get("url", "") if isinstance(first_url, dict) else str(first_url)
    return ""


def _suqing_result(data: Any, _content_url: str) -> Any:
    return data.get("video_url", "") or data.get("url", "") or ""


_PROTOCOLS: Mapping[str, ProviderProtocol] = {
    "zhangyuge": ProviderProtocol(
        "zhangyuge", "https://otuapi.com", 30, True, (),
        _zhangyuge_status, _default_result,
    ),
    "manxiaobai": ProviderProtocol(
        "manxiaobai", "https://api.manxiaobai.online", 30, False, (),
        _manxiaobai_status, _default_result,
    ),
    "xai": ProviderProtocol(
        "xai", "https://api.x.ai", 30, False,
        (("Content-Type", "application/json"),), _xai_status, _xai_result,
    ),
    "manxueapi": ProviderProtocol(
        "manxueapi", "https://manxueapi.com/v1", 60, False,
        (("Content-Type", "application/json"),), _manxueapi_status, _manxueapi_result,
    ),
    "geeknow": ProviderProtocol(
        "geeknow", "https://www.geeknow.top", 30, False, (),
        _geeknow_status, _geeknow_result,
    ),
    "snumom": ProviderProtocol(
        "snumom", "https://snumom.com", 30, False, (),
        _snumom_status, _snumom_result,
    ),
    "biglongxia": ProviderProtocol(
        "biglongxia", "https://api.biglongxia.cn", 30, False, (),
        _biglongxia_status, _biglongxia_result,
    ),
    "heima": ProviderProtocol(
        "heima", "https://api.mmg.lat", 30, False, (),
        _heima_status, _heima_result, content_endpoint="/v1/videos/{external_task_id}/content",
    ),
    "yu25": ProviderProtocol(
        "yu25", "https://api.yu25.xyz", 30, False,
        (("Accept", "application/json"),), _yu25_status, _yu25_result,
    ),
    "yu25_beiyong": ProviderProtocol(
        "yu25_beiyong", "https://api.yu25.xyz", 30, False,
        (("Accept", "application/json"),), _yu25_beiyong_status, _yu25_beiyong_result,
        content_endpoint="/v1/videos/{external_task_id}/content",
    ),
    "haoai": ProviderProtocol(
        "haoai", "https://cpa.haoai.icu/v1", 30, False,
        (("Content-Type", "application/json"),), _haoai_status, _haoai_result,
        poll_endpoint="/openai/v1/videos/{external_task_id}",
        content_endpoint="/openai/v1/videos/{external_task_id}/content",
    ),
    "yiyun": ProviderProtocol(
        "yiyun", "https://yiyun.xiaoge.uk/v1", 30, False,
        (("Content-Type", "application/json"),), _yiyun_status, _yiyun_result,
    ),
    "suqing": ProviderProtocol(
        "suqing", "https://aifast.site", 30, False,
        (("Content-Type", "application/json"),), _suqing_status, _suqing_result,
    ),
}


def get_provider_protocol(provider: str) -> ProviderProtocol | None:
    """Return an exact, case-sensitive provider protocol if one is supported."""

    return _PROTOCOLS.get(provider)
