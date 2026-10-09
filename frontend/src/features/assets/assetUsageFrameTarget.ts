import type { AssetLibraryType, Chapter, StoryboardFrame } from "../../shared/api/contracts";

export interface AssetUsageFrameIdentity {
  chapterId: string;
  seriesId: string;
  storyboardAssetId: string;
  category: AssetLibraryType;
  assetId: string;
  framePosition: number;
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readFirstStoryboardId(frame: unknown): string | null {
  if (!isRecord(frame) || !Object.prototype.hasOwnProperty.call(frame, "storyboard")) {
    return null;
  }
  const storyboard = frame.storyboard;
  if (!Array.isArray(storyboard)) {
    return null;
  }
  const firstId: unknown = storyboard[0];
  return typeof firstId === "string" && firstId.trim() !== "" ? firstId : null;
}

/** Resolves an exact frame identity from one chapter-directory snapshot. */
export function resolveAssetUsageFrameTarget(
  chapters: readonly Chapter[],
  seriesId: string,
  chapterPosition: number,
  framePosition: number,
  category: AssetLibraryType,
  assetId: string,
): AssetUsageFrameIdentity | null {
  if (
    !Number.isSafeInteger(chapterPosition)
    || chapterPosition < 1
    || !Number.isSafeInteger(framePosition)
    || framePosition < 1
    || assetId.trim() === ""
  ) {
    return null;
  }

  const chapter = chapters[chapterPosition - 1];
  if (
    chapter === undefined
    || typeof chapter.id !== "string"
    || chapter.id.trim() === ""
    || chapter.series_id !== seriesId
    || chapters.filter((candidate) => candidate.id === chapter.id).length !== 1
    || !Array.isArray(chapter.content)
  ) {
    return null;
  }

  const frame = chapter.content[framePosition - 1];
  const storyboardAssetId = readFirstStoryboardId(frame);
  if (frame === undefined || storyboardAssetId === null) {
    return null;
  }

  const matchingFrames = chapter.content.filter(
    (candidate) => readFirstStoryboardId(candidate) === storyboardAssetId,
  );
  if (matchingFrames.length !== 1 || !isRecord(frame)) {
    return null;
  }

  const referenceField = categoryReferenceField(category);
  if (referenceField === null) {
    return null;
  }
  const categoryReferences = frame[referenceField];
  if (
    !Object.prototype.hasOwnProperty.call(frame, referenceField)
    || !Array.isArray(categoryReferences)
    || !categoryReferences.includes(assetId)
  ) {
    return null;
  }

  return {
    chapterId: chapter.id,
    seriesId: chapter.series_id,
    storyboardAssetId,
    category,
    assetId,
    framePosition,
  };
}
