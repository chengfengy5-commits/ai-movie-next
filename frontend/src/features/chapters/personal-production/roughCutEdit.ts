import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../../shared/api/contracts";
import {
  parsePersonalRoughCutUpdate,
  type PersonalRoughCutSnapshot,
  type PersonalRoughCutUpdate,
  type PersonalRoughCutUpdateFrame,
} from "../../../shared/api/personalRoughCut";

export interface RoughCutSourceCheck {
  readonly valid: boolean;
  readonly reason: string | null;
  readonly sourceIds: readonly string[];
}

export type RoughCutSaveDecision =
  | { readonly kind: "blocked"; readonly reason: string }
  | { readonly kind: "unchanged" }
  | { readonly kind: "save"; readonly update: PersonalRoughCutUpdate };

function firstRawStoryboardId(frame: StoryboardFrame): unknown {
  const storyboard = frame.storyboard;
  return Array.isArray(storyboard) ? storyboard[0] : undefined;
}

function isStableAssetId(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const length = Array.from(value).length;
  return length >= 1 && length <= 36;
}

function invalidSource(reason: string, sourceIds: readonly string[] = []): RoughCutSourceCheck {
  return { valid: false, reason, sourceIds };
}

/** Validate a full chapter and canonical rough-cut snapshot without dropping invalid rows. */
export function inspectRoughCutSource(
  chapter: Chapter,
  snapshot: PersonalRoughCutSnapshot,
  assets: readonly StoryboardAsset[] | null,
  assetDirectoryStatus: "loading" | "ready" | "error",
): RoughCutSourceCheck {
  const sourceFrames = chapter.content ?? [];
  if (sourceFrames.length > 500) {
    return invalidSource("当前章节超过 500 个镜头，暂不能保存。", []);
  }
  if (snapshot.chapter_id !== chapter.id) {
    return invalidSource("粗剪草稿与当前章节不一致，暂不能保存。", []);
  }

  const sourceIds: string[] = [];
  const sourcePositions = new Map<string, number>();
  for (let index = 0; index < sourceFrames.length; index += 1) {
    const frame = sourceFrames[index];
    const id = frame === undefined ? undefined : firstRawStoryboardId(frame);
    if (!isStableAssetId(id)) {
      return invalidSource("当前章节包含无法核对的稳定镜头 ID，整份草稿暂不能保存。", sourceIds);
    }
    if (sourcePositions.has(id)) {
      return invalidSource("当前章节包含重复的稳定镜头 ID，整份草稿暂不能保存。", sourceIds);
    }
    sourcePositions.set(id, index);
    sourceIds.push(id);
  }

  if (snapshot.frames.length !== sourceIds.length) {
    return invalidSource("粗剪草稿与当前章节镜头集合不一致，整份草稿暂不能保存。", sourceIds);
  }

  const roughCutRows = new Map<string, number>();
  for (const frame of snapshot.frames) {
    if (!isStableAssetId(frame.asset_id)) {
      return invalidSource("粗剪草稿包含无法核对的稳定镜头 ID，整份草稿暂不能保存。", sourceIds);
    }
    roughCutRows.set(frame.asset_id, (roughCutRows.get(frame.asset_id) ?? 0) + 1);
    if (roughCutRows.get(frame.asset_id) !== 1) {
      return invalidSource("粗剪草稿包含重复的稳定镜头 ID，整份草稿暂不能保存。", sourceIds);
    }
    if (sourcePositions.get(frame.asset_id) !== frame.frame_index) {
      return invalidSource("粗剪草稿的镜头位置与当前章节不一致，整份草稿暂不能保存。", sourceIds);
    }
  }
  for (const id of sourceIds) {
    if (roughCutRows.get(id) !== 1) {
      return invalidSource("粗剪草稿与当前章节镜头集合不一致，整份草稿暂不能保存。", sourceIds);
    }
  }

  if (sourceIds.length === 0) {
    return { valid: true, reason: null, sourceIds };
  }
  if (assetDirectoryStatus !== "ready" || assets === null) {
    return invalidSource("请等待当前章节素材目录读取完成，再保存完整粗剪。", sourceIds);
  }

  const assetCounts = new Map<string, number>();
  for (const asset of assets) {
    if (typeof asset.id === "string") {
      assetCounts.set(asset.id, (assetCounts.get(asset.id) ?? 0) + 1);
    }
  }
  const assetsById = new Map<string, StoryboardAsset>();
  for (const asset of assets) {
    if (typeof asset.id === "string" && !assetsById.has(asset.id)) {
      assetsById.set(asset.id, asset);
    }
  }

  for (const id of sourceIds) {
    if (assetCounts.get(id) !== 1) {
      return invalidSource("素材目录中的镜头身份缺失或重复，整份草稿暂不能保存。", sourceIds);
    }
    const asset = assetsById.get(id);
    if (asset === undefined || asset.series_id !== chapter.series_id || asset.chapter_id !== chapter.id) {
      return invalidSource("素材目录中的镜头不属于当前章节，整份草稿暂不能保存。", sourceIds);
    }
  }

  return { valid: true, reason: null, sourceIds };
}

export function createRoughCutDraft(
  snapshot: PersonalRoughCutSnapshot,
): PersonalRoughCutUpdateFrame[] {
  return snapshot.frames.map(({ asset_id, included }) => ({ asset_id, included }));
}

export function setRoughCutIncluded(
  draft: readonly PersonalRoughCutUpdateFrame[],
  index: number,
  included: boolean,
): PersonalRoughCutUpdateFrame[] {
  const current = draft[index];
  if (current === undefined || current.included === included) {
    return [...draft];
  }
  return draft.map((frame, frameIndex) => (
    frameIndex === index ? { asset_id: frame.asset_id, included } : frame
  ));
}

/** Swap one row with its adjacent neighbour in the same included group. */
export function moveRoughCutFrame(
  draft: readonly PersonalRoughCutUpdateFrame[],
  index: number,
  direction: -1 | 1,
): PersonalRoughCutUpdateFrame[] {
  const row = draft[index];
  if (row === undefined) {
    return [...draft];
  }

  let neighbour = -1;
  for (let candidate = index + direction; candidate >= 0 && candidate < draft.length; candidate += direction) {
    if (draft[candidate]?.included === row.included) {
      neighbour = candidate;
      break;
    }
  }
  if (neighbour < 0) {
    return [...draft];
  }

  const next = [...draft];
  const neighborRow = next[neighbour];
  if (neighborRow === undefined) {
    return next;
  }
  next[neighbour] = row;
  next[index] = neighborRow;
  return next;
}

export function roughCutDraftDiffers(
  snapshot: PersonalRoughCutSnapshot,
  draft: readonly PersonalRoughCutUpdateFrame[],
): boolean {
  return snapshot.frames.length !== draft.length || snapshot.frames.some((frame, index) => {
    const edited = draft[index];
    return edited === undefined
      || frame.asset_id !== edited.asset_id
      || frame.included !== edited.included;
  });
}

export function decideRoughCutSave(
  snapshot: PersonalRoughCutSnapshot,
  draft: readonly PersonalRoughCutUpdateFrame[],
  sourceCheck: RoughCutSourceCheck,
): RoughCutSaveDecision {
  if (!sourceCheck.valid) {
    return {
      kind: "blocked",
      reason: sourceCheck.reason ?? "当前章节镜头身份无法核对，暂不能保存。",
    };
  }
  if (snapshot.revision >= Number.MAX_SAFE_INTEGER) {
    return { kind: "blocked", reason: "粗剪版本无法安全递增，暂不能保存。" };
  }

  let update: PersonalRoughCutUpdate;
  try {
    update = parsePersonalRoughCutUpdate({
      expected_revision: snapshot.revision,
      frames: draft,
    });
  } catch {
    return { kind: "blocked", reason: "编辑草稿包含无效或不完整的镜头列表，暂不能保存。" };
  }

  const differs = roughCutDraftDiffers(snapshot, update.frames);
  const requiresExplicitSave = !snapshot.saved
    || snapshot.frames.some((frame) => frame.pending)
    || snapshot.removed_asset_ids.length > 0;
  if (!differs && !requiresExplicitSave) {
    return { kind: "unchanged" };
  }

  if (update.frames.length !== sourceCheck.sourceIds.length) {
    return { kind: "blocked", reason: "编辑草稿未包含当前章节的完整镜头集合。" };
  }
  for (let index = 0; index < update.frames.length; index += 1) {
    const frame = update.frames[index];
    if (frame === undefined || !sourceCheck.sourceIds.includes(frame.asset_id)) {
      return { kind: "blocked", reason: "编辑草稿包含不属于当前章节的镜头。" };
    }
  }
  return { kind: "save", update };
}
