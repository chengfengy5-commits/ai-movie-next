"""Concrete fused-image adapter over the existing synchronous media facade."""

from __future__ import annotations

from haoai_backend.generation_contracts import ReferenceInput, StoredMedia, TransferFailure
from haoai_backend.media_uploads.domain import (
    MAX_IMAGE_BYTES,
    InvalidMediaUpload,
    upload_spec,
    validate_upload_content,
)
from haoai_backend.media_storage.sync_facade import (
    MediaStoreOperationError,
    SynchronousMediaStore,
)
from haoai_backend.media_storage.transfer_types import MAX_TRANSFER_SECONDS
from haoai_backend.media_storage.owned_write_values import (
    OwnedObjectMaterial,
    OwnedObjectPlan,
    OwnedObjectPutResult,
    OwnedObjectVerification,
    OwnedObjectWritePermit,
)

from .domain import FusedGenerationInlineImage

_IMAGE_FILENAMES_BY_MEDIA_TYPE = {
    "image/bmp": "generated.bmp",
    "image/gif": "generated.gif",
    "image/jpeg": "generated.jpg",
    "image/png": "generated.png",
    "image/webp": "generated.webp",
}


class SynchronousFusedGenerationImageStorage:
    """Store validated provider bytes through the owned-media storage runtime."""

    def __init__(self, media_store: SynchronousMediaStore) -> None:
        if not isinstance(media_store, SynchronousMediaStore):
            raise TypeError("an explicit SynchronousMediaStore is required")
        self._media_store = media_store

    def store_inline_image(self, image: FusedGenerationInlineImage) -> StoredMedia:
        if type(image) is not FusedGenerationInlineImage:
            raise ValueError("inline image is not validated media")
        maximum_bytes = _validate_image_content(image.content, image.media_type)
        return self._media_store.store_bytes_or_provider_content(
            ReferenceInput(
                kind="bytes",
                content=image.content,
                media_type=image.media_type,
            ),
            {"max_bytes": maximum_bytes},
        )


class SynchronousFusedGenerationPlannedMediaStorage:
    """Use the shared media runtime for durable fused object plans."""

    def __init__(self, media_store: SynchronousMediaStore) -> None:
        if not isinstance(media_store, SynchronousMediaStore):
            raise TypeError("an explicit SynchronousMediaStore is required")
        self._media_store = media_store

    def prepare_public_image(
        self,
        image_url: str,
        *,
        owner_id: str,
        submission_id: str,
        result_digest: str,
    ) -> OwnedObjectMaterial:
        if type(image_url) is not str or not image_url:
            raise ValueError("provider image URL is invalid")
        material = self._media_store.prepare_owned_public_media(
            ReferenceInput(kind="public_http", locator=image_url),
            {"max_bytes": MAX_IMAGE_BYTES, "timeout_seconds": min(MAX_TRANSFER_SECONDS, 180)},
            owner_ref=owner_id,
            submission_ref=submission_id,
            slot="primary",
            provider_result_digest=result_digest,
        )
        try:
            _validate_image_content(material.content, material.plan.media_type)
        except ValueError:
            raise MediaStoreOperationError(
                TransferFailure("invalid_image_content", "图片内容无法安全验证")
            ) from None
        return material

    def prepare_inline_image(
        self,
        image: FusedGenerationInlineImage,
        *,
        owner_id: str,
        submission_id: str,
        result_digest: str,
    ) -> OwnedObjectMaterial:
        if type(image) is not FusedGenerationInlineImage:
            raise ValueError("inline image is invalid")
        maximum_bytes = _validate_image_content(image.content, image.media_type)
        return self._media_store.prepare_owned_bytes(
            ReferenceInput(
                kind="bytes",
                content=image.content,
                media_type=image.media_type,
            ),
            {"max_bytes": maximum_bytes},
            owner_ref=owner_id,
            submission_ref=submission_id,
            slot="primary",
            provider_result_digest=result_digest,
        )

    def put_planned(
        self,
        material: OwnedObjectMaterial,
        permit: OwnedObjectWritePermit,
    ) -> OwnedObjectPutResult:
        result = self._media_store.put_planned_media(material, permit)
        if type(result) is OwnedObjectPutResult:
            return result
        return OwnedObjectPutResult("unknown", error_code="storage_unavailable")

    def verify_planned(self, plan: OwnedObjectPlan) -> OwnedObjectVerification:
        return self._media_store.verify_planned_media(plan)


def _validate_image_content(content: object, media_type: object) -> int:
    if (
        type(content) is not bytes
        or not content
        or len(content) > MAX_IMAGE_BYTES
        or type(media_type) is not str
    ):
        raise ValueError("inline image is not validated media")
    filename = _IMAGE_FILENAMES_BY_MEDIA_TYPE.get(media_type)
    if filename is None:
        raise ValueError("inline image media type is not supported")
    try:
        spec = upload_spec("image", filename)
        validate_upload_content(spec, content)
    except (InvalidMediaUpload, ValueError) as error:
        raise ValueError("inline image content is invalid") from error
    if spec.content_type != media_type:
        raise ValueError("inline image media type does not match its content")
    return min(MAX_IMAGE_BYTES, spec.max_bytes)
