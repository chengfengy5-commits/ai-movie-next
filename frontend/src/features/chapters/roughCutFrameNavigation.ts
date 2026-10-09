import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type { PersonalRoughCutFrame, PersonalRoughCutSnapshot } from "../../shared/api/personalRoughCut";

function firstStoryboardId(frame: StoryboardFrame): string | null {
  const storyboard = frame.storyboard;
  if (!Array.isArray(storyboard)) {
    return null;
  }
  const id = storyboard[0];
  return typeof id === "string" && id.trim() !== "" ? id : null;
}

/** Resolve a rough-cut row only through its stable ID in all three current snapshots. */
export function findRoughCutFramePosition(
  chapter: Chapter,
  seriesId: string,
  snapshot: PersonalRoughCutSnapshot,
  row: PersonalRoughCutFrame,
  assets: StoryboardAsset[],
): number | null {
  if (
    chapter.id.trim() === ""
    || chapter.series_id !== seriesId
    || snapshot.chapter_id !== chapter.id
    || !snapshot.frames.includes(row)
    || typeof row.asset_id !== "string"
    || row.asset_id.trim() === ""
  ) {
    return null;
  }

  const assetId = row.asset_id;
  if (snapshot.frames.filter((candidate) => candidate.asset_id === assetId).length !== 1) {
    return null;
  }

  const frames = chapter.content ?? [];
  const matchingPositions = frames.flatMap((frame, index) => (
    firstStoryboardId(frame) === assetId ? [index + 1] : []
  ));
  if (matchingPositions.length !== 1) {
    return null;
  }

  const matchingAssets = assets.filter((asset) => asset.id === assetId);
  if (matchingAssets.length !== 1) {
    return null;
  }
  const [asset] = matchingAssets;
  if (asset === undefined || asset.series_id !== seriesId || asset.chapter_id !== chapter.id) {
    return null;
  }

  return matchingPositions[0] ?? null;
}
