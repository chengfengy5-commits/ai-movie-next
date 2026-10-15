import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../../shared/api/contracts";
import type { PersonalProductionSnapshot } from "../../../shared/api/personalProductionNotes";
import type { PersonalRoughCutSnapshot } from "../../../shared/api/personalRoughCut";
import { areJsonValuesEqual } from "../chapterEditing";

export interface FrameStructureDraft {
  text: string;
  characterAssetIds: string[];
  sceneAssetIds: string[];
  propAssetIds: string[];
}

export interface DeletableFrameTarget {
  frameIndex: number;
  storyboardAssetId: string;
}

export function buildInsertedFrameContent(
  content: StoryboardFrame[] | null,
  afterIndex: number,
  draft: FrameStructureDraft,
): StoryboardFrame[] | null {
  const frames = content ?? [];
  if (afterIndex < -1 || afterIndex >= frames.length) return null;
  const inserted: StoryboardFrame = {
    text: draft.text,
    character: [...draft.characterAssetIds],
    scene: [...draft.sceneAssetIds],
    prop: [...draft.propAssetIds],
    storyboard: [],
    preview: null,
  };
  const insertionIndex = afterIndex + 1;
  return [...frames.slice(0, insertionIndex), inserted, ...frames.slice(insertionIndex)];
}

export function resolveDeletableFrameTarget(
  chapter: Chapter,
  frameIndex: number,
  assets: StoryboardAsset[],
): DeletableFrameTarget | null {
  const content = chapter.content;
  if (!Array.isArray(content) || content.length <= 1
    || !Number.isSafeInteger(frameIndex) || frameIndex < 0 || frameIndex >= content.length) return null;
  const storyboardAssetId = firstStoryboardAssetId(content[frameIndex]);
  if (storyboardAssetId === null) return null;

  const frameIdentityCount = content.reduce(
    (count, frame) => count + (firstStoryboardAssetId(frame) === storyboardAssetId ? 1 : 0),
    0,
  );
  const matches = assets.filter((asset) => asset.id === storyboardAssetId);
  if (frameIdentityCount !== 1 || matches.length !== 1
    || matches[0]?.series_id !== chapter.series_id || matches[0]?.chapter_id !== chapter.id
    || matches[0]?.frame_index !== frameIndex) return null;

  return { frameIndex, storyboardAssetId };
}

export function isFrameStructureReadbackValid(
  expectedContent: StoryboardFrame[],
  actualContent: StoryboardFrame[] | null,
  assets: StoryboardAsset[],
  seriesId: string,
  chapterId: string,
): boolean {
  if (!Array.isArray(actualContent) || expectedContent.length !== actualContent.length) return false;
  const assetsById = new Map<string, StoryboardAsset[]>();
  for (const asset of assets) {
    const matches = assetsById.get(asset.id) ?? [];
    matches.push(asset);
    assetsById.set(asset.id, matches);
  }

  const seenIds = new Set<string>();
  for (let index = 0; index < expectedContent.length; index += 1) {
    const expected = expectedContent[index];
    const actual = actualContent[index];
    if (!isRecord(expected) || !isRecord(actual)) return false;
    const expectedId = firstStoryboardAssetId(expected);
    const actualId = firstStoryboardAssetId(actual);
    if (actualId === null || seenIds.has(actualId)) return false;
    seenIds.add(actualId);

    const matchingAssets = assetsById.get(actualId);
    const asset = matchingAssets?.length === 1 ? matchingAssets[0] : undefined;
    if (asset === undefined || asset.series_id !== seriesId || asset.chapter_id !== chapterId
      || asset.frame_index !== index) return false;

    const expectedKeys = Object.keys(expected).filter((key) => key !== "storyboard").sort();
    const actualKeys = Object.keys(actual).filter((key) => key !== "storyboard").sort();
    if (expectedKeys.length !== actualKeys.length
      || expectedKeys.some((key, keyIndex) => key !== actualKeys[keyIndex]
        || !areJsonValuesEqual(expected[key], actual[key]))) return false;

    if (expectedId !== null) {
      if (actualId !== expectedId || !areJsonValuesEqual(expected.storyboard, actual.storyboard)) return false;
    } else {
      const actualReferences = actual.storyboard;
      if (!Array.isArray(actualReferences) || actualReferences.length !== 1 || actualReferences[0] !== actualId) {
        return false;
      }
    }
  }
  return true;
}

export function firstStoryboardAssetId(frame: StoryboardFrame | undefined): string | null {
  if (frame === undefined || !Array.isArray(frame.storyboard)) return null;
  const value = frame.storyboard[0];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export function areFrameStructureMappingsValid(
  chapter: Chapter,
  notes: PersonalProductionSnapshot,
  roughCut: PersonalRoughCutSnapshot,
): boolean {
  const content = chapter.content;
  if (!Array.isArray(content) || notes.chapter_id !== chapter.id || roughCut.chapter_id !== chapter.id) return false;
  const activeIds = content.map((frame) => firstStoryboardAssetId(frame));
  if (activeIds.some((id) => id === null) || new Set(activeIds).size !== activeIds.length) return false;
  const activeIdSet = new Set(activeIds as string[]);

  if (notes.media_state !== "ready" || notes.frames.length !== activeIds.length
    || notes.frames.some((frame, index) => frame.frame_index !== index
      || frame.storyboard_asset_id !== activeIds[index] || !frame.source_valid)) {
    return false;
  }
  const roughCutIds = new Set<string>();
  const roughCutIndexes = new Set<number>();
  for (const frame of roughCut.frames) {
    if (frame.asset_id === "") continue;
    if (!Number.isSafeInteger(frame.frame_index) || frame.frame_index < 0 || frame.frame_index >= activeIds.length
      || !activeIdSet.has(frame.asset_id) || activeIds[frame.frame_index] !== frame.asset_id
      || roughCutIds.has(frame.asset_id) || roughCutIndexes.has(frame.frame_index)) return false;
    roughCutIds.add(frame.asset_id);
    roughCutIndexes.add(frame.frame_index);
  }
  if (roughCutIds.size !== activeIdSet.size || [...activeIdSet].some((id) => !roughCutIds.has(id))) return false;
  if (roughCut.removed_asset_ids.some((id) => activeIdSet.has(id))) return false;
  return true;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
