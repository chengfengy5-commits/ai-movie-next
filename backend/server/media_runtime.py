"""Shared owned-media resources for explicitly enabled generation families."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from haoai_backend.generation_contracts import PublicMediaFetcher
from haoai_backend.media_storage.owned_media_runtime import OwnedMediaRuntime
from haoai_backend.media_storage.sync_facade import SynchronousMediaStore


@dataclass(frozen=True, slots=True)
class ServerMediaRuntimeResources:
    """Cold shared media owner and its concrete store/fetcher constructors."""

    runtime: OwnedMediaRuntime = field(repr=False)
    media_store_factory: Callable[[], SynchronousMediaStore] = field(repr=False)
    public_media_fetcher_factory: Callable[[], PublicMediaFetcher] = field(repr=False)
