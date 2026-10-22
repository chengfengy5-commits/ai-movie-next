import type { AssetLibraryType, Chapter, StoryboardFrame } from "../../../shared/api/contracts";

export interface AssetReplacementFramePreview {
  framePosition: number;
  referenceCount: number;
  text: string | null;
  originalText: string | null;
}

export interface AssetReplacementChapterPlan {
  chapterId: string;
  title: string;
  status: "ready" | "unreadable";
  reason: string | null;
  baselineContent: StoryboardFrame[] | null;
  expectedContent: StoryboardFrame[] | null;
  frames: AssetReplacementFramePreview[];
  referenceCount: number;
}

export type AssetReplacementPlan =
  | { status: "ready"; chapters: AssetReplacementChapterPlan[] }
  | { status: "unreadable"; chapters: []; reason: string };

const referenceField: Record<AssetLibraryType, "character" | "scene" | "prop"> = {
  characters: "character",
  scenes: "scene",
  props: "prop",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function displayText(frame: Record<string, unknown>, field: "text" | "original_text"): string | null {
  const value = frame[field];
  return typeof value === "string" ? value : null;
}

function unreadableChapter(chapterId: string, title: string, reason: string): AssetReplacementChapterPlan {
  return {
    chapterId,
    title,
    status: "unreadable",
    reason,
    baselineContent: null,
    expectedContent: null,
    frames: [],
    referenceCount: 0,
  };
}

export function buildAssetReplacementPlan(
  snapshot: unknown,
  seriesId: string,
  category: AssetLibraryType,
  oldAssetId: string,
  newAssetId: string,
): AssetReplacementPlan {
  if (oldAssetId.trim() === "" || newAssetId.trim() === "" || oldAssetId === newAssetId) {
    return { status: "unreadable", chapters: [], reason: "旧素材与新素材身份无效，无法生成替换预览。" };
  }
  if (!Array.isArray(snapshot)) {
    return { status: "unreadable", chapters: [], reason: "章节列表无法核对，不能生成替换预览。" };
  }

  const seenIds = new Set<string>();
  const chapters: AssetReplacementChapterPlan[] = [];
  const field = referenceField[category];

  for (const candidate of snapshot) {
    if (!isRecord(candidate)
      || typeof candidate.id !== "string"
      || candidate.id.trim() === ""
      || candidate.series_id !== seriesId
      || typeof candidate.title !== "string"
      || (candidate.content !== null && !Array.isArray(candidate.content))) {
      return { status: "unreadable", chapters: [], reason: "章节列表包含身份或内容无法核对的项目。" };
    }
    if (seenIds.has(candidate.id)) {
      return { status: "unreadable", chapters: [], reason: "章节列表包含重复身份，不能安全生成预览。" };
    }
    seenIds.add(candidate.id);

    const title = candidate.title.trim() === "" ? "未命名章节" : candidate.title;
    const rawContent = candidate.content;
    if (rawContent === null) {
      chapters.push(unreadableChapter(candidate.id, title, "章节内容为空，无法确认是否包含旧素材引用。"));
      continue;
    }
    const frames: StoryboardFrame[] = [];
    const previews: AssetReplacementFramePreview[] = [];
    let referenceCount = 0;
    let unreadableReason: string | null = null;

    for (const [frameIndex, value] of rawContent.entries()) {
      if (!isRecord(value)) {
        unreadableReason = "分镜内容无法核对。";
        break;
      }
      const references = value[field];
      if (references === undefined || references === null) {
        frames.push(value as StoryboardFrame);
        continue;
      }
      if (!Array.isArray(references)
        || references.some((reference) => typeof reference !== "string" || reference.trim() === "")) {
        unreadableReason = "分类素材引用包含无法识别的值。";
        break;
      }

      const matches = references.filter((reference) => reference === oldAssetId).length;
      if (matches === 0) {
        frames.push(value as StoryboardFrame);
        continue;
      }

      const nextReferences = references.includes(newAssetId)
        ? references.filter((reference) => reference !== oldAssetId)
        : references.map((reference) => reference === oldAssetId ? newAssetId : reference);
      frames.push({ ...value, [field]: nextReferences });
      referenceCount += matches;
      previews.push({
        framePosition: frameIndex + 1,
        referenceCount: matches,
        text: displayText(value, "text"),
        originalText: displayText(value, "original_text"),
      });
    }

    if (unreadableReason !== null) {
      chapters.push(unreadableChapter(candidate.id, title, unreadableReason));
    } else if (referenceCount > 0) {
      chapters.push({
        chapterId: candidate.id,
        title,
        status: "ready",
        reason: null,
        baselineContent: rawContent as StoryboardFrame[],
        expectedContent: frames,
        frames: previews,
        referenceCount,
      });
    }
  }

  return { status: "ready", chapters };
}

export function chapterContentMatches(chapter: Chapter | undefined, content: StoryboardFrame[] | null): boolean {
  return chapter !== undefined && jsonValueMatches(chapter.content, content);
}

export function assetSnapshotMatches(current: unknown, snapshot: unknown): boolean {
  return jsonValueMatches(current, snapshot);
}

function jsonValueMatches(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => jsonValueMatches(value, right[index]));
  }
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => key === rightKeys[index] && jsonValueMatches(left[key], right[key]));
  }
  return false;
}
