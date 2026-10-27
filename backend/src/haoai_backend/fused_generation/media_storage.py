"""Concrete fused-image adapter over the existing synchronous media facade."""

from __future__ import annotations

from haoai_backend.generation_contracts import ReferenceInput, StoredMedia
from haoai_backend.media_uploads.domain import (
    MAX_IMAGE_BYTES,
    InvalidMediaUpload,
    upload_spec,
    validate_upload_content,
)
from haoai_backend.media_storage.sync_facade import SynchronousMediaStore

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
        if (
            type(image) is not FusedGenerationInlineImage
            or type(image.content) is not bytes
            or not image.content
            or len(image.content) > MAX_IMAGE_BYTES
            or type(image.media_type) is not str
        ):
            raise ValueError("inline image is not validated media")
        filename = _IMAGE_FILENAMES_BY_MEDIA_TYPE.get(image.media_type)
        if filename is None:
            raise ValueError("inline image media type is not supported")
        try:
            spec = upload_spec("image", filename)
            validate_upload_content(spec, image.content)
        except (InvalidMediaUpload, ValueError) as error:
            raise ValueError("inline image content is invalid") from error
        if spec.content_type != image.media_type:
            raise ValueError("inline image media type does not match its content")
        return self._media_store.store_bytes_or_provider_content(
            ReferenceInput(
                kind="bytes",
                content=image.content,
                media_type=image.media_type,
            ),
            {"max_bytes": min(MAX_IMAGE_BYTES, spec.max_bytes)},
        )
