import type { AssetLibraryType, Chapter, Character, Prop, Scene, StoryboardFrame } from "../../shared/api/contracts";

export type AssetFrameUsageAsset = Character | Scene | Prop;

export type AssetFrameUsageText =
  | { kind: "value"; value: string }
  | { kind: "missing" }
  | { kind: "invalid" };

export interface AssetFrameUsageFrame {
  chapterPosition: number;
  chapterTitle: string;
  framePosition: number;
  referenceCount: number;
  text: AssetFrameUsageText;
  originalText: AssetFrameUsageText;
}

export type AssetFrameUsageProjection =
  | { status: "unreadable" }
  | { status: "empty"; hasUncertainReferences: boolean }
  | {
      status: "matches";
      chapters: Array<{
        position: number;
        title: string;
        frames: AssetFrameUsageFrame[];
      }>;
      frameCount: number;
      referenceCount: number;
      hasUncertainReferences: boolean;
    };

const categoryField: Record<AssetLibraryType, keyof StoryboardFrame> = {
  characters: "character",
  scenes: "scene",
  props: "prop",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readText(frame: StoryboardFrame, key: "text" | "original_text"): AssetFrameUsageText {
  if (!Object.prototype.hasOwnProperty.call(frame, key)) {
    return { kind: "missing" };
  }
  const value = frame[key];
  if (value === null || value === undefined) {
    return { kind: "missing" };
  }
  return typeof value === "string" ? { kind: "value", value } : { kind: "invalid" };
}

function isValidChapterSnapshot(value: unknown, seriesId: string): value is Chapter[] {
  if (!Array.isArray(value)) {
    return false;
  }

  const seenIds = new Set<string>();
  for (const candidate of value) {
    if (!isRecord(candidate)) {
      return false;
    }
    if (
      typeof candidate.id !== "string"
      || candidate.id.length === 0
      || typeof candidate.series_id !== "string"
      || candidate.series_id !== seriesId
      || typeof candidate.title !== "string"
      || (candidate.content !== null && !Array.isArray(candidate.content))
    ) {
      return false;
    }
    if (seenIds.has(candidate.id)) {
      return false;
    }
    seenIds.add(candidate.id);
    if (Array.isArray(candidate.content) && candidate.content.some((frame) => !isRecord(frame))) {
      return false;
    }
  }
  return true;
}

/** Projects only textual references for one exact asset identity. */
export function projectAssetFrameUsage(
  chapterSnapshot: unknown,
  seriesId: string,
  category: AssetLibraryType,
  asset: AssetFrameUsageAsset,
): AssetFrameUsageProjection {
  if (
    typeof asset.id !== "string"
    || asset.id.trim() === ""
    || asset.series_id !== seriesId
    || !isValidChapterSnapshot(chapterSnapshot, seriesId)
  ) {
    return { status: "unreadable" };
  }

  const field = categoryField[category];
  const projectedChapters: Array<{
    position: number;
    title: string;
    frames: AssetFrameUsageFrame[];
  }> = [];
  let frameCount = 0;
  let referenceCount = 0;
  let hasUncertainReferences = false;

  for (const [chapterIndex, chapter] of chapterSnapshot.entries()) {
    const projectedFrames: AssetFrameUsageFrame[] = [];
    for (const [frameIndex, frame] of (chapter.content ?? []).entries()) {
      if (!Object.prototype.hasOwnProperty.call(frame, field)) {
        continue;
      }
      const references = frame[field];
      if (references === null || references === undefined) {
        continue;
      }
      if (!Array.isArray(references)) {
        hasUncertainReferences = true;
        continue;
      }

      let matches = 0;
      for (const reference of references) {
        if (typeof reference !== "string" || reference.trim() === "") {
          hasUncertainReferences = true;
        } else if (reference === asset.id) {
          matches += 1;
        }
      }
      if (matches === 0) {
        continue;
      }

      projectedFrames.push({
        chapterPosition: chapterIndex + 1,
        chapterTitle: chapter.title.trim() === "" ? "未命名章节" : chapter.title,
        framePosition: frameIndex + 1,
        referenceCount: matches,
        text: readText(frame, "text"),
        originalText: readText(frame, "original_text"),
      });
      frameCount += 1;
      referenceCount += matches;
    }

    if (projectedFrames.length > 0) {
      projectedChapters.push({
        position: chapterIndex + 1,
        title: chapter.title.trim() === "" ? "未命名章节" : chapter.title,
        frames: projectedFrames,
      });
    }
  }

  if (projectedChapters.length === 0) {
    return { status: "empty", hasUncertainReferences };
  }

  return {
    status: "matches",
    chapters: projectedChapters,
    frameCount,
    referenceCount,
    hasUncertainReferences,
  };
}
