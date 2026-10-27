from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from haoai_backend.generation_contracts import (
    ExternalOperationRef,
    GenerationRuntimeConfig,
    PreparedReference,
    ProviderContentCapability,
    ReferenceInput,
    StoredMedia,
    TransferFailure,
)

from .managed_urls import normalize_managed_url as _normalize_managed_url
from .owned_write_values import (
    OwnedObjectMaterial,
    OwnedObjectPlan,
    OwnedObjectPutResult,
    OwnedObjectVerification,
    OwnedObjectWritePermit,
)
from .owned_media_runtime import OwnedMediaRuntime, OwnedMediaServices
from .public_fetch_policy import UnsafeRemoteURL
from .reference_staging import (
    ReferenceStager,
    _media_type_failure,
    _normalize_media_type,
)
from .transfer_types import MAX_TRANSFER_BYTES, MAX_TRANSFER_SECONDS

PreparationMode = Literal["local_muse", "url_or_inline"]


class MediaStoreOperationError(RuntimeError):
    """Safe typed failure for success-only media-store operations."""

    def __init__(self, failure: TransferFailure) -> None:
        if not isinstance(failure, TransferFailure):
            raise TypeError("a typed media transfer failure is required")
        self.failure = failure
        super().__init__(failure.safe_detail)


class SynchronousMediaStore:
    """Server-selected synchronous facade over one owned media runtime."""

    def __init__(
        self,
        runtime: OwnedMediaRuntime,
        *,
        mode: PreparationMode,
        reference_stager: ReferenceStager | None,
        staging_directory: Path | None,
        managed_origin_map: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(runtime, OwnedMediaRuntime):
            raise TypeError("an explicit owned media runtime is required")
        if mode not in {"local_muse", "url_or_inline"}:
            raise ValueError("unsupported media preparation mode")
        if reference_stager is not None and not isinstance(
            reference_stager, ReferenceStager
        ):
            raise TypeError("reference_stager must be an explicit ReferenceStager")
        if mode == "local_muse" and (
            reference_stager is None
            or staging_directory is None
            or not Path(staging_directory).is_absolute()
        ):
            raise ValueError("local_muse requires an approved stager and absolute directory")
        if managed_origin_map is not None and not isinstance(
            managed_origin_map, Mapping
        ):
            raise TypeError("managed origin map must be a mapping")
        self._runtime = runtime
        self._mode = mode
        self._reference_stager = reference_stager
        self._staging_directory = staging_directory
        self._managed_origin_map = dict(managed_origin_map or {})

    def normalize_managed_url(
        self, url: str, explicit_origin_map: Mapping[str, str]
    ) -> str | Literal["unmanaged"]:
        self._runtime.assert_sync_caller()
        normalized = _normalize_managed_url(url, explicit_origin_map)
        return normalized if normalized is not None else "unmanaged"

    def prepare_reference(
        self,
        input: ReferenceInput,
        capability: ProviderContentCapability | None,
        config: GenerationRuntimeConfig,
    ) -> PreparedReference:
        self._runtime.assert_sync_caller()
        if not isinstance(input, ReferenceInput) or not isinstance(
            config, GenerationRuntimeConfig
        ):
            raise MediaStoreOperationError(
                _failure("invalid_reference", "媒体参考配置无效")
            )
        if input.kind == "provider_content":
            raise MediaStoreOperationError(
                _failure(
                    "configuration_unavailable",
                    "绑定媒体读取能力尚未由生产绑定适配器提供",
                )
            )

        if self._mode == "local_muse":
            if input.kind not in {"bytes", "data_uri", "local_muse_path"}:
                raise MediaStoreOperationError(
                    _failure("unsupported_reference_kind", "当前来源不支持本地准备模式")
                )
            stager = self._reference_stager
            directory = self._staging_directory
            if stager is None or directory is None:
                raise MediaStoreOperationError(
                    _failure("configuration_unavailable", "本地媒体准备能力未配置")
                )
            with self._runtime.admit_local_effect():
                try:
                    result = stager.stage_local_reference(input, directory)
                except Exception:
                    raise MediaStoreOperationError(
                        _failure("staging_failure", "本地媒体准备失败")
                    ) from None
            if isinstance(result, TransferFailure):
                raise MediaStoreOperationError(result)
            return result

        if input.kind in {"bytes", "data_uri"}:
            return self._prepare_inline(input)
        if input.kind == "managed_url":
            return self._prepare_managed_url(input)
        if input.kind == "public_http":
            try:
                return self._runtime.run(
                    lambda services: self._prepare_public_url(services, input)
                )
            except MediaStoreOperationError:
                raise
            except Exception:
                raise MediaStoreOperationError(
                    _failure("transport_unavailable", "公网媒体安全能力不可用")
                ) from None
        raise MediaStoreOperationError(
            _failure("unsupported_reference_kind", "当前来源不支持 URL/内联准备模式")
        )

    def transfer_public_url(
        self, input: ReferenceInput, guard_config: object
    ) -> StoredMedia | TransferFailure:
        self._runtime.assert_sync_caller()
        options = _bounded_options(guard_config, "公网媒体策略")
        if isinstance(options, TransferFailure):
            return options
        max_bytes, timeout_seconds = options
        if not isinstance(input, ReferenceInput):
            return _failure("invalid_reference", "媒体参考格式无效")

        async def transfer(services: OwnedMediaServices) -> StoredMedia | TransferFailure:
            if services.remote_transfer is None:
                return _failure("transport_unavailable", "公网媒体传输能力未配置")
            return await services.remote_transfer.transfer(
                input,
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
            )

        try:
            return self._runtime.run(transfer)
        except Exception:
            return _failure("transfer_failure", "公网媒体传输失败")

    def store_bytes_or_provider_content(
        self, input: ReferenceInput, object_policy: object
    ) -> StoredMedia:
        self._runtime.assert_sync_caller()
        maximum = _object_policy_limit(object_policy)
        if isinstance(maximum, TransferFailure):
            raise MediaStoreOperationError(maximum)
        if not isinstance(input, ReferenceInput):
            raise MediaStoreOperationError(
                _failure("invalid_reference", "媒体参考格式无效")
            )
        if input.kind == "provider_content":
            raise MediaStoreOperationError(
                _failure(
                    "configuration_unavailable",
                    "绑定媒体读取能力尚未由生产绑定适配器提供",
                )
            )
        if (
            input.kind != "bytes"
            or input.locator is not None
            or input.provider_operation_ref is not None
            or not isinstance(input.content, bytes)
            or not input.content
        ):
            raise MediaStoreOperationError(
                _failure("unsupported_reference", "仅支持已准备的媒体字节")
            )
        if len(input.content) > maximum:
            raise MediaStoreOperationError(
                _failure("reference_too_large", "媒体内容超过大小限制")
            )
        prepared = PreparedReference(
            kind="bytes",
            locator=None,
            content=input.content,
            media_type=input.media_type,
        )
        try:
            result = self._runtime.call_blocking_under_shared_slot(
                lambda services: services.storage.store_prepared(prepared)
            )
        except Exception:
            raise MediaStoreOperationError(
                _failure("storage_outcome_unverified", "R2 写入结果无法确认")
            ) from None
        if isinstance(result, TransferFailure):
            raise MediaStoreOperationError(result)
        return result

    def prepare_owned_bytes(
        self,
        input: ReferenceInput,
        object_policy: object,
        *,
        owner_ref: str,
        submission_ref: str,
        slot: str,
        provider_result_digest: str,
    ) -> OwnedObjectMaterial:
        self._runtime.assert_sync_caller()
        maximum = _object_policy_limit(object_policy)
        if isinstance(maximum, TransferFailure):
            raise MediaStoreOperationError(maximum)
        stager = self._reference_stager
        if not isinstance(input, ReferenceInput):
            raise MediaStoreOperationError(
                _failure("invalid_reference", "媒体参考格式无效")
            )
        if input.kind == "bytes":
            if (
                input.locator is not None
                or input.content is None
                or input.provider_operation_ref is not None
            ):
                prepared = _failure("invalid_reference", "字节参考包含冲突字段")
            elif not isinstance(input.content, bytes) or not input.content:
                prepared = _failure("invalid_reference", "字节参考内容为空或格式无效")
            else:
                media_type = _normalize_media_type(input.media_type)
                if media_type is None:
                    prepared = _media_type_failure(input.media_type)
                else:
                    prepared = (input.content, media_type)
        elif input.kind == "data_uri":
            if stager is None:
                prepared = _failure(
                    "configuration_unavailable",
                    "Data URI 媒体验证能力未配置",
                )
            else:
                prepared = stager._decode_data_uri(input)
        else:
            raise MediaStoreOperationError(
                _failure("unsupported_reference", "仅支持已验证的内联媒体")
            )
        if isinstance(prepared, TransferFailure):
            raise MediaStoreOperationError(prepared)
        content, media_type = prepared
        if len(content) > maximum:
            raise MediaStoreOperationError(
                _failure("reference_too_large", "媒体内容超过大小限制")
            )
        try:
            result = self._runtime.call_blocking_under_shared_slot(
                lambda services: services.storage.plan_bytes(
                    content,
                    media_type,
                    owner_ref=owner_ref,
                    submission_ref=submission_ref,
                    slot=slot,
                    provider_result_digest=provider_result_digest,
                )
            )
        except Exception:
            raise MediaStoreOperationError(
                _failure("storage_unavailable", "R2 媒体计划能力不可用")
            ) from None
        if isinstance(result, TransferFailure):
            raise MediaStoreOperationError(result)
        return result

    def prepare_owned_public_media(
        self,
        input: ReferenceInput,
        object_policy: object,
        *,
        owner_ref: str,
        submission_ref: str,
        slot: str,
        provider_result_digest: str,
    ) -> OwnedObjectMaterial:
        self._runtime.assert_sync_caller()
        options = _bounded_options(object_policy, "媒体对象策略")
        if isinstance(options, TransferFailure):
            raise MediaStoreOperationError(options)
        max_bytes, timeout_seconds = options
        if not isinstance(input, ReferenceInput):
            raise MediaStoreOperationError(
                _failure("invalid_reference", "媒体参考格式无效")
            )

        async def prepare(
            services: OwnedMediaServices,
        ) -> OwnedObjectMaterial | TransferFailure:
            transfer = services.remote_transfer
            if transfer is None:
                return _failure("transport_unavailable", "公网媒体安全能力未配置")
            return await transfer.prepare_owned(
                input,
                owner_ref=owner_ref,
                submission_ref=submission_ref,
                slot=slot,
                provider_result_digest=provider_result_digest,
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
            )

        try:
            result = self._runtime.run(prepare)
        except Exception:
            raise MediaStoreOperationError(
                _failure("transfer_failure", "公网媒体准备失败")
            ) from None
        if isinstance(result, TransferFailure):
            raise MediaStoreOperationError(result)
        return result

    def put_planned_media(
        self,
        material: OwnedObjectMaterial,
        permit: OwnedObjectWritePermit,
    ) -> OwnedObjectPutResult | TransferFailure:
        self._runtime.assert_sync_caller()
        if not isinstance(material, OwnedObjectMaterial):
            return _failure("invalid_plan", "媒体写入计划无效")
        try:
            result = self._runtime.call_blocking_under_shared_slot(
                lambda services: services.storage.put_planned(material, permit)
            )
        except Exception:
            return OwnedObjectPutResult("unknown", error_code="storage_call_unverified")
        return result

    def verify_planned_media(
        self, plan: OwnedObjectPlan
    ) -> OwnedObjectVerification:
        self._runtime.assert_sync_caller()
        if not isinstance(plan, OwnedObjectPlan):
            return OwnedObjectVerification(
                "unavailable",
                safe_detail="媒体写入计划无效",
            )
        try:
            result = self._runtime.call_blocking_under_shared_slot(
                lambda services: services.storage.verify_planned(plan)
            )
        except Exception:
            return OwnedObjectVerification(
                "unavailable",
                safe_detail="R2 对象内容暂时无法核验",
            )
        return result

    def release_prepared_reference(
        self, handle: PreparedReference
    ) -> Literal["released", "already_released", "not_owned", "failed"]:
        self._runtime.assert_sync_caller()
        stager = self._reference_stager
        if stager is None:
            return "not_owned"
        try:
            return stager.release_prepared_reference(handle)
        except Exception:
            return "failed"

    def fetch_bound_content(
        self,
        external_ref: ExternalOperationRef,
        capability: ProviderContentCapability,
    ) -> PreparedReference:
        self._runtime.assert_sync_caller()
        raise MediaStoreOperationError(
            _failure(
                "configuration_unavailable",
                "绑定媒体读取能力尚未由生产绑定适配器提供",
            )
        )

    def _prepare_inline(self, input: ReferenceInput) -> PreparedReference:
        stager = self._reference_stager
        if stager is None:
            raise MediaStoreOperationError(
                _failure("configuration_unavailable", "内联媒体校验能力未配置")
            )
        if input.kind == "bytes":
            validation = stager._validate_bytes_reference(input)
            if isinstance(validation, TransferFailure):
                raise MediaStoreOperationError(validation)
            content, media_type = validation
            return PreparedReference(
                kind="bytes",
                locator=None,
                content=content,
                media_type=media_type,
            )
        validation = stager._decode_data_uri(input)
        if isinstance(validation, TransferFailure):
            raise MediaStoreOperationError(validation)
        _content, media_type = validation
        return PreparedReference(
            kind="data_uri",
            locator=input.locator,
            content=None,
            media_type=media_type,
        )

    def _prepare_managed_url(self, input: ReferenceInput) -> PreparedReference:
        if (
            not isinstance(input.locator, str)
            or input.content is not None
            or input.provider_operation_ref is not None
            or input.media_type is not None
        ):
            raise MediaStoreOperationError(
                _failure("invalid_reference", "托管媒体地址格式无效")
            )
        normalized = _normalize_managed_url(input.locator, self._managed_origin_map)
        if normalized is None:
            raise MediaStoreOperationError(
                _failure("unsafe_url", "媒体地址不属于已配置的托管来源")
            )
        return PreparedReference(
            kind="managed_url",
            locator=normalized,
            content=None,
            media_type=None,
        )

    async def _prepare_public_url(
        self,
        services: OwnedMediaServices,
        input: ReferenceInput,
    ) -> PreparedReference:
        if (
            not isinstance(input.locator, str)
            or input.content is not None
            or input.provider_operation_ref is not None
            or input.media_type is not None
        ):
            raise MediaStoreOperationError(
                _failure("invalid_reference", "公网媒体地址格式无效")
            )
        if services.public_policy is None or services.remote_transfer is None:
            raise MediaStoreOperationError(
                _failure("transport_unavailable", "公网媒体安全能力未配置")
            )
        try:
            async with asyncio.timeout(MAX_TRANSFER_SECONDS):
                async with services.shared_slots:
                    resolved = await services.public_policy.resolve(input.locator)
        except UnsafeRemoteURL:
            raise MediaStoreOperationError(
                _failure("unsafe_url", "公网媒体地址未通过安全检查")
            ) from None
        except TimeoutError:
            raise MediaStoreOperationError(
                _failure("timeout", "公网媒体地址检查超时")
            ) from None
        return PreparedReference(
            kind="public_http",
            locator=resolved.target.url,
            content=None,
            media_type=None,
        )


def _bounded_options(
    value: object, label: str
) -> tuple[int, float] | TransferFailure:
    if not isinstance(value, Mapping):
        return _failure("invalid_limits", f"{label}格式无效")
    if any(key not in {"max_bytes", "timeout_seconds"} for key in value):
        return _failure("invalid_limits", f"{label}包含不支持的安全字段")
    max_bytes = value.get("max_bytes", MAX_TRANSFER_BYTES)
    timeout = value.get("timeout_seconds", MAX_TRANSFER_SECONDS)
    if (
        type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_TRANSFER_BYTES
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= MAX_TRANSFER_SECONDS
    ):
        return _failure("invalid_limits", f"{label}超出固定安全上限")
    return max_bytes, float(timeout)


def _object_policy_limit(value: object) -> int | TransferFailure:
    if not isinstance(value, Mapping) or any(key != "max_bytes" for key in value):
        return _failure("invalid_limits", "R2 媒体策略格式无效")
    maximum = value.get("max_bytes", MAX_TRANSFER_BYTES)
    if type(maximum) is not int or not 1 <= maximum <= MAX_TRANSFER_BYTES:
        return _failure("invalid_limits", "R2 媒体策略超出固定安全上限")
    return maximum


def _failure(code: str, detail: str) -> TransferFailure:
    return TransferFailure(code=code, safe_detail=detail)
