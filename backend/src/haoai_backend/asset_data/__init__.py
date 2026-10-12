"""角色、场景、道具与分镜素材的迁移模块。"""

from importlib import import_module
from typing import Any

from .ports import AssetGenerationWriter

__all__ = ("AssetGenerationWriter", "digest_asset_target", "stage_asset_image")

_GENERATION_EXPORTS = frozenset({"digest_asset_target", "stage_asset_image"})


def __getattr__(name: str) -> Any:
    if name not in _GENERATION_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".generation", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
