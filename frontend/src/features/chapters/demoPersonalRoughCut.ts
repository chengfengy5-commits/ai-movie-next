import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type {
  PersonalRoughCutFrame,
  PersonalRoughCutSnapshot,
} from "../../shared/api/personalRoughCut";

const VIDEO_URL_PATTERN = /\.(mp4|webm|mov|avi|mkv|m4v)(?:\?|$)/i;
const LEGACY_ID_REASON = "缺少唯一有效的稳定分镜 ID，暂不可编排";
const MISSING_VIDEO_REASON = "当前分镜没有可用视频";

interface SourceFrame extends Omit<PersonalRoughCutFrame, "asset_id"> {
  asset_id: string | null;
}

function candidateIdentity(frame: StoryboardFrame): string | null {
  const references = frame.storyboard;
  if (!Array.isArray(references) || typeof references[0] !== "string" || references[0] === "") {
    return null;
  }
  return references[0];
}

function sourceText(frame: StoryboardFrame): string {
  const primary = frame.text;
  const fallback = frame.original_text;
  const value = primary ? primary : fallback ? fallback : "";
  return typeof value === "string" ? value : String(value);
}

function sourcePreview(frame: StoryboardFrame): string | null {
  const preview = frame.preview;
  return typeof preview === "string" && VIDEO_URL_PATTERN.test(preview) ? preview : null;
}

function resolveSourceFrames(chapter: Chapter, assets: StoryboardAsset[]): SourceFrame[] {
  const contentFrames = chapter.content ?? [];
  const candidates = contentFrames.map(candidateIdentity);
  const counts = new Map<string, number>();
  for (const candidate of candidates) {
    if (candidate !== null) {
      counts.set(candidate, (counts.get(candidate) ?? 0) + 1);
    }
  }

  const assetsById = new Map<string, StoryboardAsset>();
  for (const asset of assets) {
    if (asset.chapter_id === chapter.id && asset.series_id === chapter.series_id) {
      assetsById.set(asset.id, asset);
    }
  }

  return contentFrames.map((frame, frameIndex) => {
    const candidate = candidates[frameIndex] ?? null;
    const asset = candidate !== null && counts.get(candidate) === 1
      ? assetsById.get(candidate)
      : undefined;
    const assetId = asset === undefined ? null : candidate;
    const previewUrl = sourcePreview(frame);
    const reasons = [
      ...(assetId === null ? [LEGACY_ID_REASON] : []),
      ...(previewUrl === null ? [MISSING_VIDEO_REASON] : []),
    ];

    return {
      asset_id: assetId,
      frame_index: frameIndex,
      text: sourceText(frame),
      preview_url: previewUrl,
      missing_reason: reasons.length === 0 ? null : reasons.join("；"),
      included: assetId !== null,
      pending: false,
    };
  });
}

function initialProjection(sourceFrames: SourceFrame[]): PersonalRoughCutFrame[] {
  return sourceFrames.map((frame) => ({
    ...frame,
    asset_id: frame.asset_id ?? "",
    included: frame.asset_id !== null,
    pending: false,
  }));
}

function savedOpeningProjection(
  chapter: Chapter,
  sourceFrames: SourceFrame[],
): PersonalRoughCutSnapshot {
  const stableFrames = sourceFrames.filter((frame) => frame.asset_id !== null);
  const savedSource = stableFrames.at(-1);
  const usedIds = new Set<string>();
  const frames: PersonalRoughCutFrame[] = [];

  if (savedSource?.asset_id !== null && savedSource?.asset_id !== undefined) {
    usedIds.add(savedSource.asset_id);
    frames.push({ ...savedSource, asset_id: savedSource.asset_id, included: true, pending: false });
  }

  for (const source of sourceFrames) {
    if (source.asset_id === null) {
      frames.push({ ...source, asset_id: "", included: false, pending: false });
    } else if (!usedIds.has(source.asset_id)) {
      usedIds.add(source.asset_id);
      frames.push({ ...source, asset_id: source.asset_id, included: false, pending: true });
    }
  }

  const retiredAssetId = chapter.id + "-retired-asset";
  return {
    chapter_id: chapter.id,
    revision: 4,
    saved: true,
    frames,
    removed_asset_ids: [retiredAssetId, retiredAssetId],
  };
}

export function getDemoPersonalRoughCut(
  chapter: Chapter,
  assets: StoryboardAsset[],
): PersonalRoughCutSnapshot {
  const sourceFrames = resolveSourceFrames(chapter, assets);
  if (chapter.title.startsWith("第一章")) {
    return savedOpeningProjection(chapter, sourceFrames);
  }
  return {
    chapter_id: chapter.id,
    revision: 0,
    saved: false,
    frames: initialProjection(sourceFrames),
    removed_asset_ids: [],
  };
}

export function clonePersonalRoughCutSnapshot(
  snapshot: PersonalRoughCutSnapshot,
): PersonalRoughCutSnapshot {
  return {
    ...snapshot,
    frames: snapshot.frames.map((frame) => ({ ...frame })),
    removed_asset_ids: [...snapshot.removed_asset_ids],
  };
}
