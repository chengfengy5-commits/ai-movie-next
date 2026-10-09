"""Chapter asset-reference replacement capability."""

from .application import replace_chapter_asset
from .domain import ChapterAsset, ChapterRecord, ReplaceAssetCommand

__all__ = [
    "ChapterAsset",
    "ChapterRecord",
    "ReplaceAssetCommand",
    "replace_chapter_asset",
]
