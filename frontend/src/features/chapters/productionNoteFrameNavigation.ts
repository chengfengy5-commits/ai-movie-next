import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type {
  PersonalProductionFrameView,
  PersonalProductionReadout,
} from "./personal-production/projection";

export interface ProductionNoteFrameTarget {
  position: number;
  storyboardAssetId: string;
  frame: StoryboardFrame;
}

const navigableStatuses = new Set<string>([
  "approved",
  "needs_revision",
  "needs_reconfirmation",
  "unmarked",
]);

function firstStoryboardIdentity(frame: StoryboardFrame): string | null {
  const storyboard = frame.storyboard;
  if (!Array.isArray(storyboard)) {
    return null;
  }

  const identity: unknown = storyboard[0];
  return typeof identity === "string" && identity.trim() !== "" ? identity : null;
}

function countChapterIdentity(chapter: Chapter, identity: string): number {
  if (!Array.isArray(chapter.content)) {
    return 0;
  }
  return chapter.content.reduce(
    (count, frame) => count + (firstStoryboardIdentity(frame) === identity ? 1 : 0),
    0,
  );
}

/** Resolves a readout row only through its current, unique storyboard identity. */
export function resolveProductionNoteFrameTarget(
  chapter: Chapter,
  seriesId: string,
  assets: readonly StoryboardAsset[],
  readout: PersonalProductionReadout,
  row: PersonalProductionFrameView,
): ProductionNoteFrameTarget | null {
  if (
    readout.mediaState !== "ready"
    || readout.noSavedRecord
    || chapter.series_id !== seriesId
    || !Array.isArray(chapter.content)
    || !row.verified
    || !navigableStatuses.has(row.status)
    || !Number.isSafeInteger(row.position)
    || row.position < 1
    || readout.frames.filter((candidate) => candidate === row).length !== 1
    || readout.frames.filter((candidate) => candidate.position === row.position).length !== 1
  ) {
    return null;
  }

  const frame = chapter.content[row.position - 1];
  if (frame === undefined) {
    return null;
  }

  const storyboardAssetId = firstStoryboardIdentity(frame);
  if (
    storyboardAssetId === null
    || countChapterIdentity(chapter, storyboardAssetId) !== 1
  ) {
    return null;
  }

  const matchingAssets = assets.filter((asset) => asset.id === storyboardAssetId);
  if (matchingAssets.length !== 1) {
    return null;
  }

  const [asset] = matchingAssets;
  if (
    asset === undefined
    || asset.chapter_id !== chapter.id
    || asset.series_id !== seriesId
  ) {
    return null;
  }

  return { position: row.position, storyboardAssetId, frame };
}
