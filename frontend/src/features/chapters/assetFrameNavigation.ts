import type { AssetLibraryType, Chapter, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";

export interface ChapterFrameNavigationTarget {
  storyboardAssetId: string;
  category: AssetLibraryType;
  assetId: string;
}

function categoryReferenceField(category: AssetLibraryType): keyof StoryboardFrame | null {
  switch (category) {
    case "characters":
      return "character";
    case "scenes":
      return "scene";
    case "props":
      return "prop";
    default:
      return null;
  }
}

function firstStoryboardId(frame: unknown): string | null {
  if (typeof frame !== "object" || frame === null || Array.isArray(frame)) {
    return null;
  }
  const record = frame as Record<string, unknown>;
  if (!Object.prototype.hasOwnProperty.call(record, "storyboard") || !Array.isArray(record.storyboard)) {
    return null;
  }
  const firstId: unknown = record.storyboard[0];
  return typeof firstId === "string" && firstId.trim() !== "" ? firstId : null;
}

/** Finds the exact, unique frame represented by storyboard[0] and verifies its live category reference. */
export function findChapterFramePosition(
  chapter: Chapter,
  target: ChapterFrameNavigationTarget,
): number | null {
  if (
    target.storyboardAssetId.trim() === ""
    || target.assetId.trim() === ""
    || !Array.isArray(chapter.content)
  ) {
    return null;
  }
  const positions = chapter.content.flatMap((frame, index) => (
    firstStoryboardId(frame) === target.storyboardAssetId ? [index + 1] : []
  ));
  if (positions.length !== 1) {
    return null;
  }

  const position = positions[0];
  if (position === undefined) {
    return null;
  }
  const frame = chapter.content[position - 1];
  if (typeof frame !== "object" || frame === null || Array.isArray(frame)) {
    return null;
  }
  const record = frame as Record<string, unknown>;
  const field = categoryReferenceField(target.category);
  if (field === null) {
    return null;
  }
  const references = record[field];
  if (
    !Object.prototype.hasOwnProperty.call(record, field)
    || !Array.isArray(references)
    || !references.includes(target.assetId)
  ) {
    return null;
  }
  return position;
}

/** Matches the response identity to storyboard[0], independently of the category asset reference. */
export function findUniqueChapterStoryboardAsset(
  assets: readonly StoryboardAsset[],
  seriesId: string,
  chapterId: string,
  target: ChapterFrameNavigationTarget,
): StoryboardAsset | null {
  if (target.storyboardAssetId.trim() === "") {
    return null;
  }
  const matches = assets.filter((asset) => asset.id === target.storyboardAssetId);
  if (matches.length !== 1) {
    return null;
  }
  const [asset] = matches;
  return asset !== undefined && asset.series_id === seriesId && asset.chapter_id === chapterId
    ? asset
    : null;
}
