import type { Chapter, ReorderChaptersInput, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";

export type FrameTextUpdate =
  | { ok: true; changed: boolean; content: StoryboardFrame[] }
  | { ok: false; message: string };

export function updateStoryboardFrameText(
  content: StoryboardFrame[] | null,
  position: number,
  text: string,
): FrameTextUpdate {
  if (!Array.isArray(content) || !Number.isSafeInteger(position) || position < 1 || position > content.length) {
    return { ok: false, message: "镜头位置已变化，请重新读取章节。" };
  }
  const index = position - 1;
  const target = content[index];
  if (!isRecord(target)) {
    return { ok: false, message: "当前镜头结构无法识别，未保存修改。" };
  }
  const currentText = target.text;
  if (currentText !== undefined && currentText !== null && typeof currentText !== "string") {
    return { ok: false, message: "当前镜头文字不是可编辑的文本，未覆盖原内容。" };
  }
  if ((currentText ?? "") === text) {
    return { ok: true, changed: false, content };
  }
  return {
    ok: true,
    changed: true,
    content: content.map((frame, frameIndex) => frameIndex === index
      ? { ...target, text }
      : frame),
  };
}

export function areJsonValuesEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") {
    return false;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
    return left.every((value, index) => areJsonValuesEqual(value, right[index]));
  }
  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index]
      && areJsonValuesEqual(leftRecord[key], rightRecord[key]));
}

export function sameChapterContent(left: StoryboardFrame[] | null, right: StoryboardFrame[] | null): boolean {
  return areJsonValuesEqual(left, right);
}

export function buildChapterReorderInput(
  chapters: Chapter[],
  orderedIds: string[],
  seriesId: string,
): ReorderChaptersInput | null {
  if (chapters.length !== orderedIds.length) return null;
  const chapterById = new Map<string, Chapter>();
  for (const chapter of chapters) {
    if (chapter.series_id !== seriesId || chapter.id.trim() === "" || chapterById.has(chapter.id)) return null;
    chapterById.set(chapter.id, chapter);
  }
  if (new Set(orderedIds).size !== orderedIds.length || orderedIds.some((id) => !chapterById.has(id))) return null;
  return { chapters: orderedIds.map((id, index) => ({ id, order: chapters.length - index })) };
}

export function sameChapterOrderSnapshot(left: Chapter[], right: Chapter[], seriesId: string): boolean {
  if (left.length !== right.length) return false;
  if (left.some((chapter, index) => right[index]?.id !== chapter.id)) return false;
  const leftById = uniqueChapterMap(left, seriesId);
  const rightById = uniqueChapterMap(right, seriesId);
  if (leftById === null || rightById === null || leftById.size !== rightById.size) return false;
  for (const [id, chapter] of leftById) {
    const next = rightById.get(id);
    if (next === undefined || next.order !== chapter.order) return false;
  }
  return true;
}

export function inspectStoryboardReferences(
  content: StoryboardFrame[] | null,
  assets: StoryboardAsset[],
  seriesId: string,
  chapterId: string,
): { ok: true; missingPositions: Set<number> } | { ok: false; message: string } {
  if (!Array.isArray(content)) return { ok: false, message: "章节内容结构无法核对，未发送修改。" };
  const assetCounts = new Map<string, number>();
  for (const asset of assets) assetCounts.set(asset.id, (assetCounts.get(asset.id) ?? 0) + 1);
  const referencedIds = new Set<string>();
  const missingPositions = new Set<number>();

  for (let index = 0; index < content.length; index += 1) {
    const frame = content[index];
    if (!isRecord(frame)) return { ok: false, message: `镜头 ${index + 1} 的结构无法识别，未发送修改。` };
    const refs = frame.storyboard;
    if (refs === undefined || refs === null || (Array.isArray(refs) && refs.length === 0)) {
      missingPositions.add(index + 1);
      continue;
    }
    if (!Array.isArray(refs) || typeof refs[0] !== "string" || refs[0].trim() === "") {
      return { ok: false, message: `镜头 ${index + 1} 的素材引用格式异常，未发送修改。` };
    }
    const id = refs[0];
    if (referencedIds.has(id)) {
      return { ok: false, message: "章节中存在重复的分镜素材引用，未发送修改。请先核对素材关系。" };
    }
    referencedIds.add(id);
    const matches = assets.filter((asset) => asset.id === id);
    if (matches.length !== 1 || assetCounts.get(id) !== 1
      || matches[0]?.series_id !== seriesId || matches[0]?.chapter_id !== chapterId) {
      return { ok: false, message: `镜头 ${index + 1} 的素材引用不属于当前章节或无法核对，未发送修改。` };
    }
  }
  return { ok: true, missingPositions };
}

export function isSyncedFrameContentValid(
  base: StoryboardFrame[] | null,
  expected: StoryboardFrame[],
  actual: StoryboardFrame[] | null,
  missingPositions: Set<number>,
  assets: StoryboardAsset[],
  seriesId: string,
  chapterId: string,
  targetPosition: number,
  targetText: string,
): boolean {
  if (!Array.isArray(base) || !Array.isArray(actual)
    || base.length !== expected.length || actual.length !== expected.length) return false;
  const assetIds = new Map<string, StoryboardAsset[]>();
  for (const asset of assets) {
    const matches = assetIds.get(asset.id) ?? [];
    matches.push(asset);
    assetIds.set(asset.id, matches);
  }

  const actualReferenceIds = new Set<string>();
  for (const frame of actual) {
    if (!isRecord(frame)) return false;
    const refs = frame.storyboard;
    if (Array.isArray(refs) && typeof refs[0] === "string" && refs[0].trim() !== "") {
      if (actualReferenceIds.has(refs[0])) return false;
      actualReferenceIds.add(refs[0]);
    }
  }

  for (let index = 0; index < base.length; index += 1) {
    const before = base[index];
    const wanted = expected[index];
    const received = actual[index];
    if (!isRecord(before) || !isRecord(wanted) || !isRecord(received)) return false;
    const position = index + 1;
    if (received.text !== (position === targetPosition ? targetText : wanted.text)) return false;
    if (missingPositions.has(position)) {
      const originalRefs = before.storyboard;
      if (originalRefs !== undefined && originalRefs !== null
        && (!Array.isArray(originalRefs) || originalRefs.length !== 0)) return false;
      const refs = received.storyboard;
      if (!Array.isArray(refs) || refs.length !== 1 || typeof refs[0] !== "string" || refs[0].trim() === "") {
        return false;
      }
      const found = assetIds.get(refs[0]);
      if (found?.length !== 1 || found[0]?.series_id !== seriesId
        || found[0]?.chapter_id !== chapterId || found[0]?.frame_index !== index) return false;
    }

    const keys = new Set([...Object.keys(before), ...Object.keys(received)]);
    for (const key of keys) {
      if (key === "text" || (key === "storyboard" && missingPositions.has(position))) continue;
      if (!areJsonValuesEqual(before[key], received[key])) return false;
    }
  }
  return true;
}

function uniqueChapterMap(chapters: Chapter[], seriesId: string): Map<string, Chapter> | null {
  const result = new Map<string, Chapter>();
  for (const chapter of chapters) {
    if (chapter.series_id !== seriesId || chapter.id.trim() === "" || result.has(chapter.id)) return null;
    result.set(chapter.id, chapter);
  }
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
