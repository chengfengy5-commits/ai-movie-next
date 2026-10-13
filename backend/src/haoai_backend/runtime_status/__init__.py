"""Runtime status routes with explicit configuration and I/O ports."""

from .authentication import (
    RuntimeStatusActor,
    create_account_only_current_user_resolver,
)
from .configuration import RuntimeStatusConfiguration
from .database import create_generation_schema_readiness_probe
from .image_fetcher import (
    PinnedPublicImageFetcher,
    build_aiohttp_public_image_fetcher,
)
from .http import build_runtime_status_router
from .ports import (
    PublicImageFetchResult,
    RemoteContentTooLarge,
    RemoteContentTypeRejected,
    RemoteFetchError,
    UnsafeRemoteURL,
)
from .r2_readiness import (
    Boto3R2HeadBucketClientFactory,
    R2HeadBucketProbe,
    R2HeadBucketSettings,
    build_boto3_r2_head_bucket_factory,
)

__all__ = (
    "Boto3R2HeadBucketClientFactory",
    "PinnedPublicImageFetcher",
    "PublicImageFetchResult",
    "RemoteContentTooLarge",
    "RemoteContentTypeRejected",
    "RemoteFetchError",
    "R2HeadBucketProbe",
    "R2HeadBucketSettings",
    "RuntimeStatusActor",
    "RuntimeStatusConfiguration",
    "UnsafeRemoteURL",
    "build_aiohttp_public_image_fetcher",
    "build_boto3_r2_head_bucket_factory",
    "build_runtime_status_router",
    "create_account_only_current_user_resolver",
    "create_generation_schema_readiness_probe",
)
