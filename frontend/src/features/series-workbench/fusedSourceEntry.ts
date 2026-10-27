import type {
  Character,
  Prop,
  StoryboardAsset,
  StoryboardFrame,
} from "../../shared/api/contracts";
import type { FusedGenerationMemberType } from "../../shared/api/fusedGeneration";
import type { WorkspaceServices } from "../../shared/api/services";

type MemberDirectory = readonly Character[] | readonly Prop[];

export interface FusedFrameSourceInput {
  readonly seriesId: string;
  readonly chapterId: string;
  readonly frameIndex: number;
  readonly chapterContent: readonly StoryboardFrame[];
  readonly storyboardAssets: readonly StoryboardAsset[];
  readonly memberType: FusedGenerationMemberType;
  readonly memberDirectory: MemberDirectory;
}

export interface FusedFrameTargetProofSnapshot {
  readonly chapterId: string;
  readonly frameIndex: number;
  readonly storyboardAssetId?: string;
  readonly expectedFrame: Readonly<Record<string, unknown>>;
  readonly expectedContent?: readonly unknown[];
}

export interface FusedSourceEntrySelection {
  readonly actorId: string;
  readonly apiBaseUrl: string | null;
  readonly services: WorkspaceServices;
  readonly sessionGeneration: number;
  readonly sourceEpoch: number;
  readonly seriesId: string;
  readonly memberType: FusedGenerationMemberType;
  readonly memberAssetIds: readonly string[];
  readonly target: FusedFrameTargetProofSnapshot | null;
  isCurrent(): boolean;
}

export type FusedFrameSourceResult =
  | {
    readonly status: "ready";
    readonly memberType: FusedGenerationMemberType;
    readonly memberAssetIds: readonly string[];
    readonly target: FusedFrameTargetProofSnapshot;
  }
  | { readonly status: "unavailable"; readonly reason: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isLosslessJsonValue(value: unknown, ancestors = new Set<object>()): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "object" || ancestors.has(value)) return false;

  ancestors.add(value);
  let valid = false;
  if (Array.isArray(value)) {
    const keys = Object.keys(value);
    const expectedKeys = new Set<PropertyKey>([...keys, "length"]);
    valid = keys.length === value.length
      && keys.every((key, index) => key === String(index))
      && Reflect.ownKeys(value).length === expectedKeys.size
      && Reflect.ownKeys(value).every((key) => expectedKeys.has(key));
    if (valid) {
      for (let index = 0; index < value.length; index += 1) {
        if (!isLosslessJsonValue(value[index], ancestors)) {
          valid = false;
          break;
        }
      }
    }
  } else if (isRecord(value)
    && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)) {
    const keys = Object.keys(value);
    const ownKeys = Reflect.ownKeys(value);
    valid = ownKeys.length === keys.length && keys.every((key) => {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      return descriptor !== undefined && "value" in descriptor
        && isLosslessJsonValue(descriptor.value, ancestors);
    });
  }
  ancestors.delete(value);
  return valid;
}

function copyJson<T>(value: T): T | null {
  try {
    if (!isLosslessJsonValue(value)) return null;
    const encoded = JSON.stringify(value);
    if (encoded === undefined) return null;
    return JSON.parse(encoded) as T;
  } catch {
    return null;
  }
}

function freezeJson<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    Object.values(value as Record<string, unknown>).forEach(freezeJson);
    Object.freeze(value);
  }
  return value;
}

function withoutFused(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([key]) => key !== "fused"));
}

function canonicalJson(value: unknown): string | null {
  if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") return Number.isFinite(value) ? JSON.stringify(value) : null;
  if (Array.isArray(value)) {
    const encoded = value.map(canonicalJson);
    return encoded.some((item) => item === null) ? null : `[${encoded.join(",")}]`;
  }
  if (!isRecord(value)) return null;
  const entries = Object.keys(value).sort().map((key) => {
    const item = canonicalJson(value[key]);
    return item === null ? null : `${JSON.stringify(key)}:${item}`;
  });
  return entries.some((entry) => entry === null) ? null : `{${entries.join(",")}}`;
}

function noIdFrameIdentity(frame: StoryboardFrame): boolean {
  if (!("storyboard" in frame)) return true;
  const value = frame.storyboard;
  return Array.isArray(value) && value.every((id) => id === null || id === "");
}

function storyboardId(frame: StoryboardFrame): string | null {
  const value = frame.storyboard;
  if (!Array.isArray(value) || value.length !== 1) return null;
  const [candidate] = value;
  if (typeof candidate !== "string" || candidate.trim() === "" || candidate !== candidate.trim()) return null;
  return candidate;
}

function collectMemberIds(
  frame: StoryboardFrame,
  memberType: FusedGenerationMemberType,
  directory: MemberDirectory,
  seriesId: string,
): { readonly ids: readonly string[] } | { readonly reason: string } {
  const field = memberType === "character" ? "character" : "prop";
  const rawIds = frame[field];
  if (rawIds === undefined || rawIds === null) return { ids: Object.freeze([]) };
  if (!Array.isArray(rawIds)) {
    return { reason: "镜头关联的角色或道具编号无法识别。" };
  }
  if (rawIds.length === 0) return { ids: Object.freeze([]) };
  const ids: string[] = [];
  for (const value of rawIds) {
    if (typeof value !== "string" || value.trim() === "" || value !== value.trim() || ids.includes(value)) {
      return { reason: "镜头关联的角色或道具编号缺失、重复或格式异常。" };
    }
    const matches = directory.filter((asset) => asset.id === value && asset.series_id === seriesId);
    if (matches.length !== 1) {
      return { reason: "当前角色或道具目录无法唯一核对镜头关联素材。" };
    }
    ids.push(value);
  }
  return { ids: Object.freeze(ids) };
}

export function createFusedFrameSource(input: FusedFrameSourceInput): FusedFrameSourceResult {
  const { seriesId, chapterId, frameIndex, chapterContent, storyboardAssets, memberType, memberDirectory } = input;
  if (seriesId.trim() === "" || chapterId.trim() === "" || !Number.isSafeInteger(frameIndex)
    || frameIndex < 0 || frameIndex >= chapterContent.length) {
    return { status: "unavailable", reason: "当前剧集、章节或镜头位置无法核对。" };
  }
  const frame = chapterContent[frameIndex];
  if (frame === undefined || !isRecord(frame)) {
    return { status: "unavailable", reason: "当前镜头内容无法安全核对。" };
  }
  const expectedFrame = copyJson(frame);
  if (expectedFrame === null || !isRecord(expectedFrame)) {
    return { status: "unavailable", reason: "当前镜头内容无法安全冻结为来源快照。" };
  }

  const stableId = storyboardId(frame);
  let target: FusedFrameTargetProofSnapshot;
  if (stableId !== null) {
    const chapterMatches = chapterContent.filter((candidate) => storyboardId(candidate) === stableId);
    const directoryMatches = storyboardAssets.filter((asset) => asset.id === stableId
      && asset.series_id === seriesId && asset.chapter_id === chapterId && asset.frame_index === frameIndex);
    if (chapterMatches.length !== 1 || directoryMatches.length !== 1) {
      return { status: "unavailable", reason: "当前分镜目录关系缺失或重复，不能安全指定目标。" };
    }
    target = {
      chapterId,
      frameIndex,
      storyboardAssetId: stableId,
      expectedFrame: freezeJson(expectedFrame),
    };
  } else {
    if (!noIdFrameIdentity(frame)) {
      return { status: "unavailable", reason: "当前镜头的分镜编号无法唯一识别。" };
    }
    const withoutFusedFrame = canonicalJson(withoutFused(expectedFrame));
    if (withoutFusedFrame === null || chapterContent.some((candidate, index) => index !== frameIndex
      && isRecord(candidate)
      && canonicalJson(withoutFused(candidate)) === withoutFusedFrame)) {
      return { status: "unavailable", reason: "章节中存在无法区分的相同镜头，不能猜测融合目标。" };
    }
    const expectedContent = copyJson(chapterContent);
    if (expectedContent === null || !Array.isArray(expectedContent)) {
      return { status: "unavailable", reason: "无稳定分镜编号时无法冻结完整章节快照。" };
    }
    target = {
      chapterId,
      frameIndex,
      expectedFrame: freezeJson(expectedFrame),
      expectedContent: freezeJson(expectedContent),
    };
  }

  const members = collectMemberIds(frame, memberType, memberDirectory, seriesId);
  if (!("ids" in members)) return { status: "unavailable", reason: members.reason };
  return {
    status: "ready",
    memberType,
    memberAssetIds: members.ids,
    target: Object.freeze(target),
  };
}
