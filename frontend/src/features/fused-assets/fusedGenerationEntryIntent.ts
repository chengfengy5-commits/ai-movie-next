import type { FusedGenerationMemberType } from "../../shared/api/fusedGeneration";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";

export interface FusedGenerationTargetProof {
  readonly chapterId: string;
  readonly frameIndex: number;
  readonly storyboardAssetId?: string;
  readonly expectedFrame: Readonly<Record<string, unknown>>;
  readonly expectedContent?: readonly unknown[];
}

export interface FusedGenerationOpenIntent {
  readonly id: string;
  readonly epoch: number;
  readonly userId: string;
  readonly apiBaseUrl: string | null;
  readonly seriesId: string;
  readonly sessionGeneration: number;
  readonly memberType: FusedGenerationMemberType;
  readonly memberAssetIds: readonly string[];
  readonly target: FusedGenerationTargetProof | null;
}

export type FusedGenerationTargetedOpenIntent = Omit<FusedGenerationOpenIntent, "target"> & {
  readonly target: FusedGenerationTargetProof;
};

export type FusedGenerationTargetResolution =
  | { readonly status: "verified"; readonly target: FusedGenerationTargetProof }
  | { readonly status: "stale"; readonly reason: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function withoutFused(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([key]) => key !== "fused"));
}

function isFiniteJson(value: unknown): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isFiniteJson);
  if (isRecord(value)) return Object.values(value).every(isFiniteJson);
  return false;
}

function jsonEqual(left: unknown, right: unknown): boolean {
  if (typeof left === "boolean" || typeof right === "boolean") return left === right && typeof left === "boolean";
  if (typeof left === "number" || typeof right === "number") {
    return typeof left === "number" && typeof right === "number"
      && Number.isFinite(left) && Number.isFinite(right) && left === right;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((value, index) => jsonEqual(value, right[index]));
  }
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return jsonEqual(leftKeys, rightKeys) && leftKeys.every((key) => jsonEqual(left[key], right[key]));
  }
  return typeof left === typeof right && left === right;
}

function frameMatches(actual: unknown, expected: Readonly<Record<string, unknown>>): actual is Record<string, unknown> {
  return isRecord(actual) && isFiniteJson(actual) && isFiniteJson(expected)
    && jsonEqual(withoutFused(actual), withoutFused(expected));
}

function frameStoryboardId(frame: unknown): string | null {
  if (!isRecord(frame) || !Array.isArray(frame.storyboard) || frame.storyboard.length !== 1) return null;
  const [id] = frame.storyboard;
  return typeof id === "string" && id.trim() !== "" ? id : null;
}

function frameHasNoStoryboardIdentity(frame: unknown): boolean {
  if (!isRecord(frame)) return false;
  if (!("storyboard" in frame)) return true;
  return Array.isArray(frame.storyboard)
    && frame.storyboard.every((id) => id === null || id === "");
}

function chapterMatches(actual: readonly unknown[], expected: readonly unknown[]): boolean {
  if (!isFiniteJson(actual) || !isFiniteJson(expected) || actual.length !== expected.length) return false;
  return actual.every((frame, index) => {
    const expectedFrame = expected[index];
    if (isRecord(frame) && isRecord(expectedFrame)) return jsonEqual(withoutFused(frame), withoutFused(expectedFrame));
    return jsonEqual(frame, expectedFrame);
  });
}

export function resolveFusedGenerationTarget(
  intent: FusedGenerationTargetedOpenIntent,
  chapter: Chapter | undefined,
  storyboards: readonly StoryboardAsset[],
): FusedGenerationTargetResolution {
  if (chapter === undefined || chapter.id !== intent.target.chapterId || chapter.series_id !== intent.seriesId
    || !Array.isArray(chapter.content)) {
    return { status: "stale", reason: "来源章节已不存在或不属于当前剧集。" };
  }
  const frames = chapter.content;
  if (intent.target.storyboardAssetId !== undefined) {
    const matches = frames.flatMap((frame, index) => (
      frameStoryboardId(frame) === intent.target.storyboardAssetId ? [index] : []
    ));
    if (matches.length !== 1) {
      return { status: "stale", reason: "来源分镜ID已缺失或重复，请返回章节重新选择。" };
    }
    const frameIndex = matches[0]!;
    const directoryMatches = storyboards.filter((asset) => asset.id === intent.target.storyboardAssetId
      && asset.series_id === intent.seriesId && asset.chapter_id === chapter.id && asset.frame_index === frameIndex);
    if (directoryMatches.length !== 1 || !frameMatches(frames[frameIndex], intent.target.expectedFrame)) {
      return { status: "stale", reason: "来源分镜目录或内容已变化，请返回章节重新选择。" };
    }
    return {
      status: "verified",
      target: {
        chapterId: chapter.id,
        frameIndex,
        storyboardAssetId: intent.target.storyboardAssetId,
        expectedFrame: intent.target.expectedFrame,
      },
    };
  }

  if (intent.target.expectedContent === undefined || !Number.isSafeInteger(intent.target.frameIndex)
    || intent.target.frameIndex < 0 || !chapterMatches(frames, intent.target.expectedContent)) {
    return { status: "stale", reason: "无稳定ID来源的章节快照已变化，请返回章节重新选择。" };
  }
  const frame = frames[intent.target.frameIndex];
  if (!frameMatches(frame, intent.target.expectedFrame) || !frameHasNoStoryboardIdentity(frame)) {
    return { status: "stale", reason: "来源分镜内容已变化，请返回章节重新选择。" };
  }
  const identicalFrames = frames.filter((candidate) => frameMatches(candidate, frame));
  if (identicalFrames.length !== 1) {
    return { status: "stale", reason: "章节中存在无法区分的相同分镜，不能安全指定目标。" };
  }
  return {
    status: "verified",
    target: {
      chapterId: chapter.id,
      frameIndex: intent.target.frameIndex,
      expectedFrame: intent.target.expectedFrame,
      expectedContent: intent.target.expectedContent,
    },
  };
}
