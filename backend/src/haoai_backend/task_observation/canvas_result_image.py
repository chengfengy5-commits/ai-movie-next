"""Fetch bounded bytes for an owner-verified Canvas image receipt."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.runtime_status.ports import (
    PublicImageFetchResult,
    PublicImageFetcher,
    RemoteContentTooLarge,
    RemoteContentTypeRejected,
    RemoteFetchError,
    UnsafeRemoteURL,
)
from haoai_backend.shared.identity import TrustedActor

from .canvas_result_lookup import (
    CanvasResultSourceFacts,
    SessionFactory,
    load_canvas_result_source_facts,
)
from .errors import (
    TaskObservationBadGateway,
    TaskObservationConflict,
    TaskObservationForbidden,
    TaskObservationNotFound,
    TaskObservationTooLarge,
    TaskObservationUnavailable,
    TaskObservationUnsupportedMedia,
)

MAX_CANVAS_IMAGE_BYTES = 10 * 1024 * 1024
CANVAS_IMAGE_TIMEOUT_SECONDS = 30
CANVAS_IMAGE_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")
_LOWER_SHA256 = frozenset("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class CanvasResultImage:
    content: bytes
    media_type: str


def load_canvas_result_image_receipt(
    session_factory: SessionFactory | None,
    actor: TrustedActor,
    *,
    chapter_id: str,
    result_ref: str,
    task_id: str,
) -> GenerationResultReceipt:
    """Read the canonical image receipt and release its Session before fetch."""

    if session_factory is None:
        raise TaskObservationUnavailable("画布图片预览服务尚未接线")

    session: Session | None = None
    facts: CanvasResultSourceFacts | None = None
    cleanup_error: Exception | None = None
    try:
        candidate = session_factory()
        if not isinstance(candidate, Session):
            raise TaskObservationUnavailable("画布图片预览服务尚未接线")
        session = candidate
        with session.no_autoflush:
            facts = load_canvas_result_source_facts(
                session,
                actor,
                chapter_id=chapter_id,
                result_ref=result_ref,
                task_id=task_id,
            )
    except SQLAlchemyError as exc:
        raise TaskObservationUnavailable("数据库暂时不可用") from exc
    finally:
        if session is not None:
            try:
                session.rollback()
            except Exception as exc:
                cleanup_error = exc
            try:
                session.close()
            except Exception as exc:
                cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                raise TaskObservationUnavailable("数据库暂时不可用") from cleanup_error

    if facts is None:
        raise TaskObservationUnavailable("画布图片预览服务尚未接线")
    return facts.receipt


async def fetch_canvas_result_image(
    fetcher: PublicImageFetcher | None,
    receipt: GenerationResultReceipt,
) -> CanvasResultImage:
    """Fetch the server-stored locator and verify bytes against its receipt."""

    if fetcher is None:
        raise TaskObservationUnavailable("画布图片预览服务尚未接线")
    if (
        receipt.operation not in {"image.single", "image.batch"}
        or not isinstance(receipt.recoverable_locator, str)
        or not receipt.recoverable_locator
        or not _is_lower_sha256(receipt.content_digest)
    ):
        raise TaskObservationConflict("画布生成结果尚未具备可验证的图片回执")

    try:
        result = await fetcher.fetch(
            receipt.recoverable_locator,
            max_bytes=MAX_CANVAS_IMAGE_BYTES,
            timeout_seconds=CANVAS_IMAGE_TIMEOUT_SECONDS,
            allowed_content_prefixes=CANVAS_IMAGE_MEDIA_TYPES,
        )
    except RemoteContentTooLarge as exc:
        raise TaskObservationTooLarge("图片超过预览大小限制") from exc
    except RemoteContentTypeRejected as exc:
        raise TaskObservationUnsupportedMedia("规范图片格式不受支持") from exc
    except (UnsafeRemoteURL, RemoteFetchError, asyncio.TimeoutError, TimeoutError) as exc:
        raise TaskObservationBadGateway("无法读取已保存的画布图片") from exc
    except Exception as exc:
        raise TaskObservationBadGateway("无法读取已保存的画布图片") from exc

    if not isinstance(result, PublicImageFetchResult) or not isinstance(result.content, bytes):
        raise TaskObservationBadGateway("无法读取已保存的画布图片")
    if len(result.content) > MAX_CANVAS_IMAGE_BYTES:
        raise TaskObservationTooLarge("图片超过预览大小限制")
    if result.content_type not in CANVAS_IMAGE_MEDIA_TYPES:
        raise TaskObservationUnsupportedMedia("规范图片格式不受支持")
    if not result.content or hashlib.sha256(result.content).hexdigest() != receipt.content_digest:
        raise TaskObservationBadGateway("无法验证已保存的画布图片")
    return CanvasResultImage(result.content, result.content_type)


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _LOWER_SHA256 for character in value)
    )


__all__ = (
    "CANVAS_IMAGE_MEDIA_TYPES",
    "CANVAS_IMAGE_TIMEOUT_SECONDS",
    "MAX_CANVAS_IMAGE_BYTES",
    "CanvasResultImage",
    "fetch_canvas_result_image",
    "load_canvas_result_image_receipt",
)
