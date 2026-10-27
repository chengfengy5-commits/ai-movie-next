from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import io
import json
import re
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import BinaryIO, Callable, Literal, cast
from urllib.parse import quote

from haoai_backend.generation_contracts import PreparedReference, StoredMedia, TransferFailure

from .owned_write_values import (
    OwnedObjectMaterial,
    OwnedObjectPlan,
    OwnedObjectPutResult,
    OwnedObjectVerification,
    OwnedObjectWritePermit,
)
from .transfer_types import (
    IMMUTABLE_CACHE_CONTROL,
    MAX_TRANSFER_BYTES,
    TRANSFER_CHUNK_BYTES,
    S3Client,
    S3ObjectRead,
    OwnedDownload,
    PlannedS3Client,
    R2ClientSettings,
    R2Configuration,
    S3ClientFactory,
    SafeMediaPolicy,
    SecretHandle,
    WriteReceipt,
)


class R2Storage:
    def __init__(
        self,
        configuration: R2Configuration | None,
        media_policy: SafeMediaPolicy | None,
        *,
        access_key: SecretHandle | None,
        secret_key: SecretHandle | None,
        client_factory: S3ClientFactory | None,
        executor: concurrent.futures.Executor | None,
    ) -> None:
        self._configuration = configuration
        self._media_policy = media_policy
        self._access_key = access_key
        self._secret_key = secret_key
        self._client_factory = client_factory
        self._executor = executor
        self._pending_resources: list[tuple[OwnedDownload | None, BinaryIO | S3Client]] = []
        self._pending_lock = threading.RLock()

    @property
    def pending_resource_cleanup_count(self) -> int:
        with self._pending_lock:
            return len(self._pending_resources)

    def has_pending_download_cleanup(self, download: OwnedDownload) -> bool:
        with self._pending_lock:
            return any(owner is download for owner, _resource in self._pending_resources)

    async def store_download(self, download: OwnedDownload) -> StoredMedia | TransferFailure:
        unavailable = self._readiness_failure()
        if unavailable is not None:
            return unavailable
        try:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self._executor, self._write_download, download)
        except (RuntimeError, TypeError):
            return _failure("storage_unavailable", "R2 写入能力未配置")
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancellation:
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            raise cancellation
        except BaseException:
            return _failure("storage_outcome_unverified", "R2 写入结果无法确认")

    def store_prepared(self, reference: PreparedReference) -> StoredMedia | TransferFailure:
        if (
            reference.kind != "bytes"
            or reference.locator is not None
            or not isinstance(reference.content, bytes)
        ):
            return _failure("unsupported_reference", "R2 仅接受已准备的媒体字节")
        if not 1 <= len(reference.content) <= MAX_TRANSFER_BYTES:
            return _failure("reference_too_large", "媒体内容为空或超过大小限制")
        if self._readiness_failure() is not None:
            return _failure("storage_unavailable", "R2 写入能力未配置")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return _failure("storage_unavailable", "R2 同步写入不得阻塞运行中的事件循环")
        try:
            if self._executor is None:
                return _failure("storage_unavailable", "R2 写入能力未配置")
            return self._executor.submit(
                self._write_bytes,
                reference.content,
                reference.media_type,
            ).result()
        except asyncio.CancelledError:
            raise
        except BaseException:
            return _failure("storage_outcome_unverified", "R2 写入结果无法确认")

    def plan_bytes(
        self,
        content: bytes,
        media_type: str | None,
        *,
        owner_ref: str,
        submission_ref: str,
        slot: str,
        provider_result_digest: str,
    ) -> OwnedObjectMaterial | TransferFailure:
        configuration = self._configuration
        policy = self._media_policy
        if configuration is None or policy is None:
            return _failure("storage_unavailable", "R2 媒体计划能力未配置")
        if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_TRANSFER_BYTES:
            return _failure("reference_too_large", "媒体内容为空或超过大小限制")
        rule = policy.resolve(media_type)
        if rule is None:
            return _failure("unsupported_media_type", "媒体类型不受支持")
        try:
            return _plan_content(
                configuration,
                policy,
                content,
                rule.media_type,
                owner_ref=owner_ref,
                submission_ref=submission_ref,
                slot=slot,
                provider_result_digest=provider_result_digest,
            )
        except ValueError:
            return _failure("invalid_plan", "媒体写入计划无效")

    async def prepare_download(
        self,
        download: OwnedDownload,
        *,
        owner_ref: str,
        submission_ref: str,
        slot: str,
        provider_result_digest: str,
        max_bytes: int = MAX_TRANSFER_BYTES,
    ) -> OwnedObjectMaterial | TransferFailure:
        configuration = self._configuration
        policy = self._media_policy
        executor = self._executor
        if configuration is None or policy is None or executor is None:
            return _failure("storage_unavailable", "R2 媒体计划能力未配置")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_TRANSFER_BYTES:
            return _failure("invalid_limits", "公网媒体限制无效")
        try:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                executor,
                self._prepare_download,
                download,
                owner_ref,
                submission_ref,
                slot,
                provider_result_digest,
                max_bytes,
            )
        except (RuntimeError, TypeError):
            return _failure("storage_unavailable", "R2 媒体准备能力未配置")
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancellation:
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            raise cancellation
        except BaseException:
            return _failure("material_unavailable", "公网媒体字节无法安全准备")

    def _prepare_download(
        self,
        download: OwnedDownload,
        owner_ref: str,
        submission_ref: str,
        slot: str,
        provider_result_digest: str,
        max_bytes: int,
    ) -> OwnedObjectMaterial | TransferFailure:
        configuration = self._configuration
        policy = self._media_policy
        if configuration is None or policy is None:
            return _failure("storage_unavailable", "R2 媒体计划能力未配置")
        media_type = policy.resolve(download.media_type)
        size_bytes = download.size_bytes
        if media_type is None or not 1 <= size_bytes <= max_bytes:
            return _failure("unsupported_media_type", "媒体类型或大小不受支持")
        reader: BinaryIO | None = None
        content_parts: list[bytes] = []
        total_bytes = 0
        digest = hashlib.sha256()
        read_error: BaseException | None = None
        cleanup_failed = False
        try:
            reader = download.open_reader()
            while True:
                chunk = reader.read(min(TRANSFER_CHUNK_BYTES, max_bytes + 1 - total_bytes))
                if not isinstance(chunk, bytes):
                    raise OSError("owned download reader returned non-bytes")
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise ValueError("owned download exceeded the media limit")
                digest.update(chunk)
                content_parts.append(chunk)
        except BaseException as exc:
            read_error = exc
        finally:
            if reader is not None:
                try:
                    reader.close()
                except BaseException as exc:
                    cleanup_failed = True
                    self._retain_pending(reader, download)
                    if read_error is None:
                        read_error = exc
        if isinstance(read_error, asyncio.CancelledError):
            raise read_error
        if read_error is not None:
            if cleanup_failed:
                return _failure("cleanup_failure", "公网媒体读取资源清理失败")
            return _failure("material_unavailable", "公网媒体字节无法安全准备")
        content = b"".join(content_parts)
        if (
            cleanup_failed
            or total_bytes != size_bytes
            or digest.hexdigest() != download.content_digest
        ):
            return _failure("material_unavailable", "公网媒体字节事实无法确认")
        try:
            return _plan_content(
                configuration,
                policy,
                content,
                media_type.media_type,
                owner_ref=owner_ref,
                submission_ref=submission_ref,
                slot=slot,
                provider_result_digest=provider_result_digest,
            )
        except ValueError:
            return _failure("invalid_plan", "媒体写入计划无效")

    def put_planned(
        self,
        material: OwnedObjectMaterial,
        permit: OwnedObjectWritePermit,
    ) -> OwnedObjectPutResult | TransferFailure:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return _failure("storage_unavailable", "R2 同步写入不得阻塞运行中的事件循环")
        executor = self._executor
        if executor is None:
            return _failure("storage_unavailable", "R2 写入能力未配置")
        try:
            return executor.submit(self._put_planned, material, permit).result()
        except asyncio.CancelledError:
            raise
        except BaseException:
            return OwnedObjectPutResult("unknown", error_code="storage_worker_error")

    def _put_planned(
        self,
        material: OwnedObjectMaterial,
        permit: OwnedObjectWritePermit,
    ) -> OwnedObjectPutResult | TransferFailure:
        configuration = self._configuration
        policy = self._media_policy
        factory = self._client_factory
        access_key = self._access_key
        secret_key = self._secret_key
        executor = self._executor
        if (
            configuration is None
            or policy is None
            or factory is None
            or access_key is None
            or secret_key is None
            or executor is None
        ):
            return _failure("storage_unavailable", "R2 写入能力未配置")
        try:
            if not _plan_matches_configuration(material.plan, configuration, policy):
                return _failure("configuration_mismatch", "媒体写入配置已变化")
            if (
                len(material.content) != material.plan.size_bytes
                or hashlib.sha256(material.content).hexdigest()
                != material.plan.material_sha256
            ):
                return _failure("material_mismatch", "媒体字节与持久计划不匹配")
            claim = getattr(permit, "claim", None)
            if not callable(claim):
                return _failure("write_not_authorized", "媒体写入许可无效")
        except (AttributeError, TypeError, ValueError):
            return _failure("invalid_plan", "媒体写入计划无效")
        settings = _client_settings(configuration, operation_policy="owned_once")
        client: S3Client | None = None
        body: BinaryIO | None = None
        result: OwnedObjectPutResult | TransferFailure = _failure(
            "storage_unavailable", "R2 写入能力未配置"
        )
        operation_error: BaseException | None = None
        put_attempted = False
        cleanup_failed = False
        cleanup_cancel: asyncio.CancelledError | None = None
        try:
            client = factory.create(settings, access_key.reveal(), secret_key.reveal())
            if not _is_planned_client(client):
                result = _failure("storage_unavailable", "R2 planned-key 能力未配置")
            else:
                planned_client = cast(PlannedS3Client, client)
                body = io.BytesIO(material.content)
                if claim(material.plan) is not True:
                    result = _failure("write_not_authorized", "媒体写入许可已失效")
                else:
                    put_attempted = True
                    receipt = planned_client.put_object_if_absent(
                        Bucket=configuration.bucket,
                        Key=material.plan.object_key,
                        Body=body,
                        ContentType=material.plan.media_type,
                        CacheControl=IMMUTABLE_CACHE_CONTROL,
                    )
                    result = _put_outcome(receipt, material.plan.object_key)
        except BaseException as exc:
            operation_error = exc
        finally:
            if body is not None:
                try:
                    body.close()
                except BaseException as exc:
                    cleanup_failed = True
                    self._retain_pending(body)
                    if isinstance(exc, asyncio.CancelledError) and cleanup_cancel is None:
                        cleanup_cancel = exc
            if client is not None:
                try:
                    client.close()
                except BaseException as exc:
                    cleanup_failed = True
                    self._retain_pending(client)
                    if isinstance(exc, asyncio.CancelledError) and cleanup_cancel is None:
                        cleanup_cancel = exc
        if isinstance(operation_error, asyncio.CancelledError):
            raise operation_error
        if cleanup_cancel is not None:
            raise cleanup_cancel
        if cleanup_failed:
            return OwnedObjectPutResult(
                "unknown",
                error_code="cleanup_failure",
            )
        if operation_error is not None:
            if put_attempted:
                return OwnedObjectPutResult("unknown", error_code="transport_error")
            return _failure("storage_unavailable", "R2 写入能力未配置")
        return result

    def verify_planned(
        self, plan: OwnedObjectPlan
    ) -> OwnedObjectVerification:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return OwnedObjectVerification(
                "unavailable",
                safe_detail="R2 同步核验不得阻塞运行中的事件循环",
            )
        configuration = self._configuration
        policy = self._media_policy
        factory = self._client_factory
        access_key = self._access_key
        secret_key = self._secret_key
        executor = self._executor
        if (
            configuration is None
            or policy is None
            or factory is None
            or access_key is None
            or secret_key is None
            or executor is None
        ):
            return OwnedObjectVerification(
                "unavailable",
                safe_detail="R2 读取核验能力未配置",
            )
        try:
            if not _plan_matches_configuration(plan, configuration, policy):
                return OwnedObjectVerification(
                    "unavailable",
                    safe_detail="媒体写入配置已变化",
                )
            return executor.submit(
                self._verify_planned,
                plan,
                configuration,
                factory,
                access_key,
                secret_key,
            ).result()
        except asyncio.CancelledError:
            raise
        except BaseException:
            return OwnedObjectVerification(
                "unavailable",
                safe_detail="R2 对象内容暂时无法核验",
            )

    def _verify_planned(
        self,
        plan: OwnedObjectPlan,
        configuration: R2Configuration,
        factory: S3ClientFactory,
        access_key: SecretHandle,
        secret_key: SecretHandle,
    ) -> OwnedObjectVerification:
        settings = _client_settings(configuration, operation_policy="owned_once")
        client: S3Client | None = None
        response: S3ObjectRead | None = None
        result: OwnedObjectVerification | None = None
        operation_error: BaseException | None = None
        cleanup_failed = False
        cleanup_cancel: asyncio.CancelledError | None = None
        try:
            client = factory.create(settings, access_key.reveal(), secret_key.reveal())
            if not _is_planned_client(client):
                result = OwnedObjectVerification(
                    "unavailable",
                    safe_detail="R2 object-read capability is unavailable",
                )
            else:
                planned_client = cast(PlannedS3Client, client)
                response = planned_client.get_object_stream(
                    Bucket=configuration.bucket,
                    Key=plan.object_key,
                )
                if not isinstance(response, S3ObjectRead):
                    result = OwnedObjectVerification(
                        "unavailable",
                        safe_detail="R2 object-read response is invalid",
                    )
                    response = None
                else:
                    result = _verify_read_response(response, plan, configuration)
        except BaseException as exc:
            operation_error = exc
        finally:
            if response is not None:
                try:
                    response.body.close()
                except BaseException as exc:
                    cleanup_failed = True
                    self._retain_pending(response.body)
                    if isinstance(exc, asyncio.CancelledError) and cleanup_cancel is None:
                        cleanup_cancel = exc
            if client is not None:
                try:
                    client.close()
                except BaseException as exc:
                    cleanup_failed = True
                    self._retain_pending(client)
                    if isinstance(exc, asyncio.CancelledError) and cleanup_cancel is None:
                        cleanup_cancel = exc
        if isinstance(operation_error, asyncio.CancelledError):
            raise operation_error
        if cleanup_cancel is not None:
            raise cleanup_cancel
        if cleanup_failed:
            return OwnedObjectVerification(
                "unavailable",
                safe_detail="R2 content verification cleanup is unresolved",
            )
        if operation_error is not None:
            return _verification_for_read_error(operation_error)
        return result or OwnedObjectVerification(
            "unavailable",
            safe_detail="R2 object contents could not be verified",
        )

    def release(
        self, stored: StoredMedia
    ) -> Literal["released", "already_released", "not_owned", "failed"]:
        return "failed"

    def _readiness_failure(self) -> TransferFailure | None:
        if (
            self._configuration is None
            or self._media_policy is None
            or self._access_key is None
            or self._secret_key is None
            or self._client_factory is None
            or self._executor is None
        ):
            return _failure("storage_unavailable", "R2 写入能力未配置")
        return None

    def _write_download(self, download: OwnedDownload) -> StoredMedia | TransferFailure:
        configuration = self._configuration
        policy = self._media_policy
        factory = self._client_factory
        access_key = self._access_key
        secret_key = self._secret_key
        if (
            configuration is None
            or policy is None
            or factory is None
            or access_key is None
            or secret_key is None
        ):
            return _failure("storage_unavailable", "R2 写入能力未配置")
        rule = policy.resolve(download.media_type)
        if rule is None or download.size_bytes <= 0:
            return _failure("unsupported_media_type", "媒体类型不受支持")
        key = _object_key(configuration.business_type, rule.extension)
        return self._put(
            configuration,
            factory,
            access_key,
            secret_key,
            key,
            rule.media_type,
            download.size_bytes,
            download.content_digest,
            lambda: download.open_reader(),
            pending_owner=download,
        )

    def _write_bytes(self, content: bytes, media_type: str | None) -> StoredMedia | TransferFailure:
        configuration = self._configuration
        policy = self._media_policy
        factory = self._client_factory
        access_key = self._access_key
        secret_key = self._secret_key
        if (
            configuration is None
            or policy is None
            or factory is None
            or access_key is None
            or secret_key is None
        ):
            return _failure("storage_unavailable", "R2 写入能力未配置")
        rule = policy.resolve(media_type)
        if rule is None:
            return _failure("unsupported_media_type", "媒体类型不受支持")
        key = _object_key(configuration.business_type, rule.extension)
        import hashlib
        import io

        digest = hashlib.sha256(content).hexdigest()
        return self._put(
            configuration,
            factory,
            access_key,
            secret_key,
            key,
            rule.media_type,
            len(content),
            digest,
            lambda: io.BytesIO(content),
        )

    async def retry_pending_resource_cleanup_async(
        self, download: OwnedDownload | None = None
    ) -> tuple[bool, ...]:
        if self._executor is None:
            raise RuntimeError("R2 cleanup executor is unavailable")
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._executor, self.retry_pending_resource_cleanup, download
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancellation:
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            raise cancellation

    def retry_pending_resource_cleanup(
        self, download: OwnedDownload | None = None
    ) -> tuple[bool, ...]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("R2 cleanup retry must run on its injected executor")
        with self._pending_lock:
            pending = tuple(
                item for item in self._pending_resources
                if download is None or item[0] is download
            )
        outcomes: list[bool] = []
        cancellation: asyncio.CancelledError | None = None
        for owner, resource in pending:
            try:
                resource.close()
            except BaseException as exc:
                outcomes.append(False)
                if isinstance(exc, asyncio.CancelledError) and cancellation is None:
                    cancellation = exc
            else:
                outcomes.append(True)
                with self._pending_lock:
                    self._pending_resources = [
                        item for item in self._pending_resources
                        if not (item[0] is owner and item[1] is resource)
                    ]
        if cancellation is not None:
            raise cancellation
        return tuple(outcomes)

    def retry_pending_download_cleanup(self, download: OwnedDownload) -> bool:
        outcomes = self.retry_pending_resource_cleanup(download)
        return all(outcomes) and not self.has_pending_download_cleanup(download)

    def _retain_pending(
        self, resource: BinaryIO | S3Client, owner: OwnedDownload | None = None
    ) -> None:
        with self._pending_lock:
            if all(item[1] is not resource for item in self._pending_resources):
                self._pending_resources.append((owner, resource))

    def _put(
        self,
        configuration: R2Configuration,
        factory: S3ClientFactory,
        access_key: SecretHandle,
        secret_key: SecretHandle,
        key: str,
        media_type: str,
        size_bytes: int,
        digest: str,
        body_factory: Callable[[], BinaryIO],
        *,
        pending_owner: OwnedDownload | None = None,
    ) -> StoredMedia | TransferFailure:
        settings = R2ClientSettings(
            endpoint_url=configuration.endpoint_url,
            bucket=configuration.bucket,
            connect_timeout_seconds=configuration.connect_timeout_seconds,
            read_timeout_seconds=configuration.read_timeout_seconds,
        )
        client: S3Client | None = None
        body: BinaryIO | None = None
        receipt: WriteReceipt | None = None
        operation_error: BaseException | None = None
        cleanup_failed = False
        cleanup_cancel: asyncio.CancelledError | None = None
        try:
            client = factory.create(settings, access_key.reveal(), secret_key.reveal())
            body = body_factory()
            receipt = client.put_object(
                Bucket=configuration.bucket,
                Key=key,
                Body=body,
                ContentType=media_type,
                CacheControl=IMMUTABLE_CACHE_CONTROL,
            )
        except BaseException as exc:
            operation_error = exc
        finally:
            if body is not None:
                try:
                    body.close()
                except BaseException as exc:
                    cleanup_failed = True
                    self._retain_pending(body, pending_owner)
                    if isinstance(exc, asyncio.CancelledError) and cleanup_cancel is None:
                        cleanup_cancel = exc
            if client is not None:
                try:
                    client.close()
                except BaseException as exc:
                    cleanup_failed = True
                    self._retain_pending(client, pending_owner)
                    if isinstance(exc, asyncio.CancelledError) and cleanup_cancel is None:
                        cleanup_cancel = exc
        if isinstance(operation_error, asyncio.CancelledError):
            raise operation_error
        if cleanup_cancel is not None:
            raise cleanup_cancel
        if cleanup_failed:
            return _failure("cleanup_failure", "R2 资源清理结果未确认")
        if (
            operation_error is not None
            or receipt is None
            or not receipt.is_positive
            or receipt.key != key
        ):
            return _failure("storage_outcome_unverified", "R2 写入结果无法确认")
        locator = f"{configuration.public_base}/{quote(key, safe='/')}"
        return StoredMedia(
            locator=locator,
            content_digest=digest,
            media_type=media_type,
            size_bytes=size_bytes,
        )

def _object_key(business_type: str, extension: str) -> str:
    business = _business_folder(business_type)
    now = datetime.now(timezone.utc)
    return f"{now:%Y/%m/%d}/{business}/{uuid.uuid4()}{extension}"


def _business_folder(business_type: str) -> str:
    return re.sub(
        r"[^a-z0-9_-]+", "-", business_type.strip().lower()
    ).strip("-_")[:32] or "media"


def _plan_content(
    configuration: R2Configuration,
    policy: SafeMediaPolicy,
    content: bytes,
    media_type: str,
    *,
    owner_ref: str,
    submission_ref: str,
    slot: str,
    provider_result_digest: str,
) -> OwnedObjectMaterial:
    rule = policy.resolve(media_type)
    if rule is None or rule.media_type != media_type:
        raise ValueError("media type is not canonical under the configured policy")
    write_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    object_key = (
        f"{now:%Y/%m/%d}/{_business_folder(configuration.business_type)}/"
        f"{write_id}{rule.extension}"
    )
    plan = OwnedObjectPlan(
        write_id=write_id,
        owner_ref=owner_ref,
        submission_ref=submission_ref,
        slot=slot,
        provider_result_digest=provider_result_digest,
        material_sha256=hashlib.sha256(content).hexdigest(),
        media_type=rule.media_type,
        size_bytes=len(content),
        config_fingerprint=_configuration_fingerprint(configuration, policy),
        config_version=1,
        bucket=configuration.bucket,
        object_key=object_key,
        public_base=configuration.public_base,
    )
    return OwnedObjectMaterial(plan=plan, content=content)


def _configuration_fingerprint(
    configuration: R2Configuration, policy: SafeMediaPolicy
) -> str:
    policy_rules = sorted(
        (rule.media_type, rule.extension) for rule in policy.rules
    )
    payload = {
        "endpoint_url": configuration.endpoint_url,
        "bucket": configuration.bucket,
        "public_base": configuration.public_base,
        "business_type": configuration.business_type,
        "environment": configuration.environment,
        "key_format_version": 1,
        "media_policy_rules": policy_rules,
        "media_policy_missing_fallback": (
            None
            if policy.missing_fallback is None
            else (policy.missing_fallback.media_type, policy.missing_fallback.extension)
        ),
        "media_policy_generic_fallback": (
            None
            if policy.generic_fallback is None
            else (policy.generic_fallback.media_type, policy.generic_fallback.extension)
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _plan_matches_configuration(
    plan: OwnedObjectPlan,
    configuration: R2Configuration,
    policy: SafeMediaPolicy,
) -> bool:
    if not isinstance(plan, OwnedObjectPlan):
        return False
    if (
        plan.config_version != 1
        or plan.config_fingerprint != _configuration_fingerprint(configuration, policy)
        or plan.bucket != configuration.bucket
        or plan.public_base != configuration.public_base
        or not 1 <= plan.size_bytes <= MAX_TRANSFER_BYTES
    ):
        return False
    rule = policy.resolve(plan.media_type)
    if rule is None or rule.media_type != plan.media_type:
        return False
    key_pattern = (
        rf"\d{{4}}/\d{{2}}/\d{{2}}/{re.escape(_business_folder(configuration.business_type))}/"
        rf"{re.escape(plan.write_id)}{re.escape(rule.extension)}"
    )
    return re.fullmatch(key_pattern, plan.object_key) is not None


def _client_settings(
    configuration: R2Configuration,
    *,
    operation_policy: Literal["legacy_transfer", "owned_once"],
) -> R2ClientSettings:
    attempts = 1 if operation_policy == "owned_once" else 3
    return R2ClientSettings(
        endpoint_url=configuration.endpoint_url,
        bucket=configuration.bucket,
        connect_timeout_seconds=configuration.connect_timeout_seconds,
        read_timeout_seconds=configuration.read_timeout_seconds,
        total_max_attempts=attempts,
        operation_policy=operation_policy,
    )


def _is_planned_client(client: object) -> bool:
    return callable(getattr(client, "put_object_if_absent", None)) and callable(
        getattr(client, "get_object_stream", None)
    )


def _put_outcome(receipt: object, key: str) -> OwnedObjectPutResult:
    if not isinstance(receipt, WriteReceipt):
        return OwnedObjectPutResult("unknown", error_code="invalid_write_receipt")
    if receipt.is_positive and receipt.key == key:
        return OwnedObjectPutResult(
            "created",
            request_id=receipt.request_id,
        )
    if receipt.status_code == 412:
        return OwnedObjectPutResult(
            "already_exists",
            request_id=receipt.request_id or None,
            error_code="precondition_failed",
        )
    if receipt.status_code == 409:
        return OwnedObjectPutResult(
            "unknown",
            request_id=receipt.request_id or None,
            error_code="conditional_conflict",
        )
    return OwnedObjectPutResult(
        "unknown",
        request_id=receipt.request_id or None,
        error_code="write_ack_unverified",
    )


def _verify_read_response(
    response: S3ObjectRead,
    plan: OwnedObjectPlan,
    configuration: R2Configuration,
) -> OwnedObjectVerification:
    request_id = response.request_id
    if response.status_code in {404}:
        return OwnedObjectVerification(
            "not_found_pending",
            request_id=request_id,
            safe_detail="原对象在本次读取时不存在，仍待后续核验",
        )
    if response.status_code in {206} or response.content_range is not None:
        return OwnedObjectVerification(
            "content_conflict",
            request_id=request_id,
            safe_detail="对象读取不是完整内容",
        )
    if response.status_code != 200:
        return OwnedObjectVerification(
            "unavailable",
            request_id=request_id,
            safe_detail="R2 对象读取未成功",
        )
    content_type = response.content_type
    if (
        not isinstance(content_type, str)
        or content_type != plan.media_type
        or type(response.content_length) is not int
        or response.content_length != plan.size_bytes
    ):
        return OwnedObjectVerification(
            "content_conflict",
            request_id=request_id,
            safe_detail="对象类型或长度与持久计划不匹配",
        )
    digest = hashlib.sha256()
    observed_bytes = 0
    if not callable(getattr(response.body, "read", None)):
        return OwnedObjectVerification(
            "unavailable",
            request_id=request_id,
            safe_detail="R2 对象流不可读取",
        )
    while True:
        try:
            chunk = response.body.read(TRANSFER_CHUNK_BYTES)
        except asyncio.CancelledError:
            raise
        except Exception:
            return OwnedObjectVerification(
                "unavailable",
                request_id=request_id,
                safe_detail="R2 对象内容读取中断",
            )
        if not isinstance(chunk, bytes):
            return OwnedObjectVerification(
                "unavailable",
                request_id=request_id,
                safe_detail="R2 对象流返回了无效数据",
            )
        if not chunk:
            break
        observed_bytes += len(chunk)
        if observed_bytes > plan.size_bytes:
            return OwnedObjectVerification(
                "content_conflict",
                request_id=request_id,
                observed_size_bytes=observed_bytes,
                safe_detail="对象内容超出持久计划长度",
            )
        digest.update(chunk)
    observed_digest = digest.hexdigest()
    if (
        observed_bytes != plan.size_bytes
        or observed_digest != plan.material_sha256
    ):
        return OwnedObjectVerification(
            "content_conflict",
            request_id=request_id,
            observed_sha256=observed_digest,
            observed_size_bytes=observed_bytes,
            safe_detail="对象真实字节与持久计划不匹配",
        )
    locator = f"{configuration.public_base}/{quote(plan.object_key, safe='/')}"
    stored = StoredMedia(
        locator=locator,
        content_digest=observed_digest,
        media_type=plan.media_type,
        size_bytes=observed_bytes,
    )
    return OwnedObjectVerification(
        "verified",
        stored_media=stored,
        observed_sha256=observed_digest,
        observed_size_bytes=observed_bytes,
        request_id=request_id,
        safe_detail="对象内容已按持久计划完整核验",
    )


def _verification_for_read_error(error: BaseException) -> OwnedObjectVerification:
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        error_detail = response.get("Error")
        metadata = response.get("ResponseMetadata")
        code = error_detail.get("Code") if isinstance(error_detail, Mapping) else None
        status = (
            metadata.get("HTTPStatusCode")
            if isinstance(metadata, Mapping)
            else None
        )
        request_id = (
            metadata.get("RequestId") if isinstance(metadata, Mapping) else None
        )
        if status == 404 or code in {"NoSuchKey", "404", "NotFound"}:
            return OwnedObjectVerification(
                "not_found_pending",
                request_id=request_id if isinstance(request_id, str) else None,
                safe_detail="原对象在本次读取时不存在，仍待后续核验",
            )
    return OwnedObjectVerification(
        "unavailable",
        safe_detail="R2 对象内容暂时无法核验",
    )


def _failure(code: str, safe_detail: str) -> TransferFailure:
    return TransferFailure(code=code, safe_detail=safe_detail)
