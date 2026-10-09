import { InvalidResponseError } from "./contracts";

export interface PersonalRoughCutFrame {
  asset_id: string;
  frame_index: number;
  text: string;
  preview_url: string | null;
  missing_reason: string | null;
  included: boolean;
  pending: boolean;
}

export interface PersonalRoughCutSnapshot {
  chapter_id: string;
  revision: number;
  saved: boolean;
  frames: PersonalRoughCutFrame[];
  removed_asset_ids: string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOwn(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function requiredOwn(record: Record<string, unknown>, key: string, label: string): unknown {
  if (!hasOwn(record, key)) {
    throw new InvalidResponseError(`${label}缺少必需的 ${key} 字段。`);
  }
  return record[key];
}

function requiredNonEmptyString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new InvalidResponseError(`${label}字段格式无效。`);
  }
  return value;
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new InvalidResponseError(`${label}字段格式无效。`);
  }
  return value;
}

function requiredStringOrNull(value: unknown, label: string): string | null {
  if (value !== null && typeof value !== "string") {
    throw new InvalidResponseError(`${label}字段格式无效。`);
  }
  return value;
}

function requiredSafeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new InvalidResponseError(`${label}字段格式无效。`);
  }
  return value;
}

function parseFrame(value: unknown): PersonalRoughCutFrame {
  if (!isRecord(value)) {
    throw new InvalidResponseError("粗剪草稿中的镜头格式无效。");
  }

  const assetId = requiredString(requiredOwn(value, "asset_id", "粗剪镜头"), "asset_id");
  const frameIndex = requiredSafeInteger(
    requiredOwn(value, "frame_index", "粗剪镜头"),
    "frame_index",
  );
  const text = requiredString(requiredOwn(value, "text", "粗剪镜头"), "text");
  const previewUrl = requiredStringOrNull(
    requiredOwn(value, "preview_url", "粗剪镜头"),
    "preview_url",
  );
  const missingReason = requiredStringOrNull(
    requiredOwn(value, "missing_reason", "粗剪镜头"),
    "missing_reason",
  );
  const included = requiredOwn(value, "included", "粗剪镜头");
  const pending = requiredOwn(value, "pending", "粗剪镜头");

  if (typeof included !== "boolean" || typeof pending !== "boolean") {
    throw new InvalidResponseError("粗剪镜头中的 included 或 pending 字段格式无效。");
  }

  return {
    asset_id: assetId,
    frame_index: frameIndex,
    text,
    preview_url: previewUrl,
    missing_reason: missingReason,
    included,
    pending,
  };
}

export function parsePersonalRoughCutSnapshot(
  value: unknown,
  expectedChapterId: string,
): PersonalRoughCutSnapshot {
  if (!isRecord(value)) {
    throw new InvalidResponseError("服务器返回的粗剪草稿格式无效。");
  }

  const chapterId = requiredNonEmptyString(
    requiredOwn(value, "chapter_id", "粗剪草稿"),
    "chapter_id",
  );
  if (chapterId !== expectedChapterId) {
    throw new InvalidResponseError("服务器返回了不属于当前章节的粗剪草稿。");
  }

  const revision = requiredSafeInteger(
    requiredOwn(value, "revision", "粗剪草稿"),
    "revision",
  );
  const saved = requiredOwn(value, "saved", "粗剪草稿");
  if (typeof saved !== "boolean") {
    throw new InvalidResponseError("粗剪草稿中的 saved 字段格式无效。");
  }
  if ((!saved && revision !== 0) || (saved && revision === 0)) {
    throw new InvalidResponseError("粗剪草稿的保存状态与版本不一致。");
  }

  const rawFrames = requiredOwn(value, "frames", "粗剪草稿");
  if (!Array.isArray(rawFrames)) {
    throw new InvalidResponseError("粗剪草稿中的 frames 字段格式无效。");
  }

  const frames = rawFrames.map(parseFrame);
  const assetIds = new Set<string>();
  const frameIndexes = new Set<number>();
  for (const frame of frames) {
    if (frame.asset_id !== "") {
      if (assetIds.has(frame.asset_id)) {
        throw new InvalidResponseError("粗剪草稿中存在重复的稳定镜头 ID。");
      }
      assetIds.add(frame.asset_id);
    }
    if (frameIndexes.has(frame.frame_index)) {
      throw new InvalidResponseError("粗剪草稿中存在重复的章节镜头位置。");
    }
    frameIndexes.add(frame.frame_index);
  }

  const rawRemovedAssetIds = requiredOwn(value, "removed_asset_ids", "粗剪草稿");
  if (!Array.isArray(rawRemovedAssetIds)) {
    throw new InvalidResponseError("粗剪草稿中的 removed_asset_ids 字段格式无效。");
  }
  const removedAssetIds = rawRemovedAssetIds.map((assetId) =>
    requiredNonEmptyString(assetId, "removed_asset_ids"),
  );

  return {
    chapter_id: chapterId,
    revision,
    saved,
    frames,
    removed_asset_ids: removedAssetIds,
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


export interface PersonalRoughCutUpdateFrame {
  readonly asset_id: string;
  readonly included: boolean;
}

export interface PersonalRoughCutUpdate {
  readonly expected_revision: number;
  readonly frames: readonly PersonalRoughCutUpdateFrame[];
}

function hasExactOwnKeys(record: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Reflect.ownKeys(record);
  return keys.length === expected.length
    && keys.every((key) => typeof key === "string" && expected.includes(key));
}

function isRoughCutAssetId(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const codePointLength = Array.from(value).length;
  return codePointLength >= 1 && codePointLength <= 36;
}

/** Parse the exact two-field request accepted by the rough-cut PUT endpoint. */
export function parsePersonalRoughCutUpdate(value: unknown): PersonalRoughCutUpdate {
  if (!isRecord(value) || !hasExactOwnKeys(value, ["expected_revision", "frames"])) {
    throw new InvalidResponseError("粗剪保存内容必须只包含版本和完整镜头列表。");
  }

  const expectedRevision = requiredSafeInteger(
    requiredOwn(value, "expected_revision", "粗剪保存内容"),
    "expected_revision",
  );
  if (expectedRevision >= Number.MAX_SAFE_INTEGER) {
    throw new InvalidResponseError("粗剪版本无法安全递增。");
  }

  const rawFrames = requiredOwn(value, "frames", "粗剪保存内容");
  if (!Array.isArray(rawFrames) || rawFrames.length > 500) {
    throw new InvalidResponseError("粗剪保存内容的完整镜头列表无效。");
  }

  const seenIds = new Set<string>();
  const frames: PersonalRoughCutUpdateFrame[] = [];
  for (let index = 0; index < rawFrames.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(rawFrames, index)) {
      throw new InvalidResponseError("粗剪保存内容不能缺少镜头条目。");
    }
    const entry: unknown = rawFrames[index];
    if (!isRecord(entry) || !hasExactOwnKeys(entry, ["asset_id", "included"])) {
      throw new InvalidResponseError("粗剪保存镜头必须只包含稳定 ID 和纳入状态。");
    }

    const assetId = requiredOwn(entry, "asset_id", "粗剪保存镜头");
    const included = requiredOwn(entry, "included", "粗剪保存镜头");
    if (!isRoughCutAssetId(assetId) || typeof included !== "boolean") {
      throw new InvalidResponseError("粗剪保存镜头的 ID 或纳入状态无效。");
    }
    if (seenIds.has(assetId)) {
      throw new InvalidResponseError("粗剪保存内容中存在重复的稳定镜头 ID。");
    }
    seenIds.add(assetId);
    frames.push({ asset_id: assetId, included });
  }

  return { expected_revision: expectedRevision, frames };
}

/** Validate the canonical response to an explicit full rough-cut save. */
export function parsePersonalRoughCutSaveResponse(
  value: unknown,
  expectedChapterId: string,
  updateValue: unknown,
): PersonalRoughCutSnapshot {
  const update = parsePersonalRoughCutUpdate(updateValue);
  const snapshot = parsePersonalRoughCutSnapshot(value, expectedChapterId);
  if (
    !snapshot.saved
    || snapshot.revision !== update.expected_revision + 1
    || snapshot.frames.length !== update.frames.length
    || snapshot.removed_asset_ids.length !== 0
  ) {
    throw new InvalidResponseError("服务器返回的粗剪保存结果与本次提交不一致。");
  }

  const returnedPositions = new Set<number>();
  for (let index = 0; index < update.frames.length; index += 1) {
    const submitted = update.frames[index];
    const returned = snapshot.frames[index];
    if (
      submitted === undefined
      || returned === undefined
      || returned.asset_id !== submitted.asset_id
      || returned.frame_index >= snapshot.frames.length
      || returnedPositions.has(returned.frame_index)
      || returned.included !== submitted.included
      || returned.pending
    ) {
      throw new InvalidResponseError("服务器返回的粗剪保存顺序、镜头位置或纳入状态与本次提交不一致。");
    }
    returnedPositions.add(returned.frame_index);
  }

  return snapshot;
}
