import type { Chapter, Character, Prop, Scene, StoryboardAsset } from "../../shared/api/contracts";
import type { ChatAssistantAssetType, ChatAssistantTarget } from "../../shared/api/chatAssistant";
import type { StorageLike } from "../../shared/api/storage";

const STORAGE_KEY_PREFIX = "haoai:chat-assistant-context:v1";

export type ChatContextPreference =
  | { readonly kind: "chapter"; readonly chapterId: string }
  | { readonly kind: "frame"; readonly chapterId: string; readonly storyboardAssetId: string }
  | {
      readonly kind: "asset";
      readonly chapterId: string;
      readonly assetType: ChatAssistantAssetType;
      readonly assetId: string;
    };

export interface ChatContextPreferenceScope {
  readonly apiBaseUrl: string | null;
  readonly userId: string;
  readonly seriesId: string;
}

export type ChatContextPreferenceRead =
  | { readonly status: "ready"; readonly preference: ChatContextPreference | null; readonly malformed: boolean }
  | { readonly status: "unavailable"; readonly reason: "scope" | "storage" };

export type ChatContextPreferenceWrite = "saved" | "unavailable";

export type ChatContextPreferenceFailure =
  | "chapter-missing"
  | "chapter-ambiguous"
  | "frame-id-missing"
  | "frame-id-ambiguous"
  | "frame-target-missing"
  | "storyboard-missing"
  | "storyboard-ambiguous"
  | "storyboard-mismatch"
  | "asset-missing"
  | "asset-ambiguous";

export type ChatContextPreferenceResult =
  | { readonly status: "resolved"; readonly targetKey: string; readonly preference: ChatContextPreference }
  | { readonly status: "invalid"; readonly reason: ChatContextPreferenceFailure };

export function chatContextPreferenceKey(scope: ChatContextPreferenceScope): string {
  return `${STORAGE_KEY_PREFIX}:${encodeURIComponent(scope.apiBaseUrl ?? "")}:${encodeURIComponent(scope.userId)}:${encodeURIComponent(scope.seriesId)}`;
}

export function readChatContextPreference(
  scope: ChatContextPreferenceScope,
  storage?: StorageLike,
): ChatContextPreferenceRead {
  if (!hasUsableScope(scope)) return { status: "unavailable", reason: "scope" };
  try {
    const serialized = getStorage(storage).getItem(chatContextPreferenceKey(scope));
    if (serialized === null) return { status: "ready", preference: null, malformed: false };
    const parsed: unknown = JSON.parse(serialized);
    const preference = parsePreference(parsed);
    return preference === null
      ? { status: "ready", preference: null, malformed: true }
      : { status: "ready", preference, malformed: false };
  } catch {
    return { status: "unavailable", reason: "storage" };
  }
}

export function writeChatContextPreference(
  scope: ChatContextPreferenceScope,
  preference: ChatContextPreference,
  storage?: StorageLike,
): ChatContextPreferenceWrite {
  if (!hasUsableScope(scope)) return "unavailable";
  try {
    getStorage(storage).setItem(chatContextPreferenceKey(scope), JSON.stringify(preference));
    return "saved";
  } catch {
    return "unavailable";
  }
}

export function clearChatContextPreference(
  scope: ChatContextPreferenceScope,
  storage?: StorageLike,
): ChatContextPreferenceWrite {
  if (!hasUsableScope(scope)) return "unavailable";
  try {
    getStorage(storage).removeItem(chatContextPreferenceKey(scope));
    return "saved";
  } catch {
    return "unavailable";
  }
}

export function preferenceForChatTarget(
  target: ChatAssistantTarget,
  chapter: Chapter | null,
  seriesId: string,
  storyboards: readonly StoryboardAsset[],
  characters: readonly Character[],
  scenes: readonly Scene[],
  props: readonly Prop[],
): ChatContextPreferenceResult {
  if (chapter === null || chapter.id !== target.chapterId || chapter.series_id !== seriesId) {
    return { status: "invalid", reason: "chapter-missing" };
  }
  if (target.kind === "chapter") {
    return { status: "resolved", targetKey: "chapter", preference: { kind: "chapter", chapterId: chapter.id } };
  }
  if (target.kind === "frame") {
    const storyboard = resolveFrameStoryboard(chapter, storyboards, seriesId, target.frameIndex);
    if (storyboard.status === "invalid") return storyboard;
    return {
      status: "resolved",
      targetKey: `frame:${storyboard.frameIndex}`,
      preference: { kind: "frame", chapterId: chapter.id, storyboardAssetId: storyboard.storyboardAssetId },
    };
  }

  if (!hasUniqueAsset(target.assetType, target.assetId, chapter.id, seriesId, storyboards, characters, scenes, props)) {
    return {
      status: "invalid",
      reason: getAssetMatchCount(target.assetType, target.assetId, chapter.id, seriesId, storyboards, characters, scenes, props) > 1
        ? "asset-ambiguous"
        : "asset-missing",
    };
  }
  return {
    status: "resolved",
    targetKey: `asset:${target.assetType}:${target.assetId}`,
    preference: {
      kind: "asset",
      chapterId: chapter.id,
      assetType: target.assetType,
      assetId: target.assetId,
    },
  };
}

export function resolveChatContextPreference(
  preference: ChatContextPreference,
  chapters: readonly Chapter[],
  seriesId: string,
  storyboards: readonly StoryboardAsset[],
  characters: readonly Character[],
  scenes: readonly Scene[],
  props: readonly Prop[],
): ChatContextPreferenceResult {
  const chapterMatches = chapters.filter((chapter) => chapter.id === preference.chapterId && chapter.series_id === seriesId);
  if (chapterMatches.length === 0) return { status: "invalid", reason: "chapter-missing" };
  if (chapterMatches.length > 1) return { status: "invalid", reason: "chapter-ambiguous" };
  const chapter = chapterMatches[0]!;

  if (preference.kind === "chapter") {
    return { status: "resolved", targetKey: "chapter", preference };
  }
  if (preference.kind === "frame") {
    const storyboard = findFrameForStoryboard(chapter, storyboards, seriesId, preference.storyboardAssetId);
    if (storyboard.status === "invalid") return storyboard;
    return { status: "resolved", targetKey: `frame:${storyboard.frameIndex}`, preference };
  }

  if (!hasUniqueAsset(preference.assetType, preference.assetId, chapter.id, seriesId, storyboards, characters, scenes, props)) {
    return {
      status: "invalid",
      reason: getAssetMatchCount(preference.assetType, preference.assetId, chapter.id, seriesId, storyboards, characters, scenes, props) > 1
        ? "asset-ambiguous"
        : "asset-missing",
    };
  }
  return { status: "resolved", targetKey: `asset:${preference.assetType}:${preference.assetId}`, preference };
}

function resolveFrameStoryboard(
  chapter: Chapter,
  storyboards: readonly StoryboardAsset[],
  seriesId: string,
  frameIndex: number,
): { readonly status: "resolved"; readonly frameIndex: number; readonly storyboardAssetId: string }
  | { readonly status: "invalid"; readonly reason: ChatContextPreferenceFailure } {
  const frames = chapter.content;
  if (!Array.isArray(frames) || !Number.isSafeInteger(frameIndex) || frameIndex < 0 || frameIndex >= frames.length) {
    return { status: "invalid", reason: "frame-id-missing" };
  }
  const currentStoryboardId = firstStoryboardId(frames[frameIndex]);
  if (currentStoryboardId === null) return { status: "invalid", reason: "frame-id-missing" };
  if (frames.filter((frame) => firstStoryboardId(frame) === currentStoryboardId).length !== 1) {
    return { status: "invalid", reason: "frame-id-ambiguous" };
  }
  const assetMatches = storyboards.filter((asset) => asset.id === currentStoryboardId);
  if (assetMatches.length === 0) return { status: "invalid", reason: "storyboard-missing" };
  if (assetMatches.length > 1) return { status: "invalid", reason: "storyboard-ambiguous" };
  const asset = assetMatches[0]!;
  if (asset.series_id !== seriesId || asset.chapter_id !== chapter.id || asset.frame_index !== frameIndex) {
    return { status: "invalid", reason: "storyboard-mismatch" };
  }
  return { status: "resolved", frameIndex, storyboardAssetId: currentStoryboardId };
}

function findFrameForStoryboard(
  chapter: Chapter,
  storyboards: readonly StoryboardAsset[],
  seriesId: string,
  storyboardAssetId: string,
): { readonly status: "resolved"; readonly frameIndex: number }
  | { readonly status: "invalid"; readonly reason: ChatContextPreferenceFailure } {
  if (storyboardAssetId.trim() === "") return { status: "invalid", reason: "frame-id-missing" };
  const frames = chapter.content;
  if (!Array.isArray(frames)) return { status: "invalid", reason: "frame-target-missing" };
  const matches = frames.flatMap((frame, index) => firstStoryboardId(frame) === storyboardAssetId ? [index] : []);
  if (matches.length === 0) return { status: "invalid", reason: "frame-target-missing" };
  if (matches.length > 1) return { status: "invalid", reason: "frame-id-ambiguous" };

  const assetMatches = storyboards.filter((asset) => asset.id === storyboardAssetId);
  if (assetMatches.length === 0) return { status: "invalid", reason: "storyboard-missing" };
  if (assetMatches.length > 1) return { status: "invalid", reason: "storyboard-ambiguous" };
  const asset = assetMatches[0]!;
  if (asset.series_id !== seriesId || asset.chapter_id !== chapter.id || asset.frame_index !== matches[0]) {
    return { status: "invalid", reason: "storyboard-mismatch" };
  }
  return { status: "resolved", frameIndex: matches[0]! };
}

function firstStoryboardId(frame: unknown): string | null {
  if (!isRecord(frame) || !Array.isArray(frame.storyboard)) return null;
  const first = frame.storyboard[0];
  return typeof first === "string" && first.trim() !== "" ? first : null;
}

function hasUniqueAsset(
  type: ChatAssistantAssetType,
  id: string,
  chapterId: string,
  seriesId: string,
  storyboards: readonly StoryboardAsset[],
  characters: readonly Character[],
  scenes: readonly Scene[],
  props: readonly Prop[],
): boolean {
  return getAssetMatchCount(type, id, chapterId, seriesId, storyboards, characters, scenes, props) === 1;
}

function getAssetMatchCount(
  type: ChatAssistantAssetType,
  id: string,
  chapterId: string,
  seriesId: string,
  storyboards: readonly StoryboardAsset[],
  characters: readonly Character[],
  scenes: readonly Scene[],
  props: readonly Prop[],
): number {
  if (type === "storyboard") {
    return storyboards.filter((asset) => asset.id === id
      && asset.series_id === seriesId && asset.chapter_id === chapterId).length;
  }
  if (type === "character") return characters.filter((asset) => asset.id === id && asset.series_id === seriesId).length;
  if (type === "scene") return scenes.filter((asset) => asset.id === id && asset.series_id === seriesId).length;
  return props.filter((asset) => asset.id === id && asset.series_id === seriesId).length;
}

function parsePreference(value: unknown): ChatContextPreference | null {
  if (!isRecord(value) || typeof value.chapterId !== "string" || value.chapterId.trim() === "") return null;
  if (value.kind === "chapter" && hasExactKeys(value, ["kind", "chapterId"])) {
    return { kind: "chapter", chapterId: value.chapterId };
  }
  if (value.kind === "frame" && hasExactKeys(value, ["kind", "chapterId", "storyboardAssetId"])
    && typeof value.storyboardAssetId === "string" && value.storyboardAssetId.trim() !== "") {
    return { kind: "frame", chapterId: value.chapterId, storyboardAssetId: value.storyboardAssetId };
  }
  if (value.kind === "asset" && hasExactKeys(value, ["kind", "chapterId", "assetType", "assetId"])
    && isAssetType(value.assetType) && typeof value.assetId === "string" && value.assetId.trim() !== "") {
    return { kind: "asset", chapterId: value.chapterId, assetType: value.assetType, assetId: value.assetId };
  }
  return null;
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  return Object.keys(value).length === expected.length && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function isAssetType(value: unknown): value is ChatAssistantAssetType {
  return value === "character" || value === "scene" || value === "prop" || value === "storyboard";
}

function hasUsableScope(scope: ChatContextPreferenceScope): boolean {
  return scope.apiBaseUrl !== null && scope.apiBaseUrl.trim() !== ""
    && scope.userId.trim() !== "" && scope.seriesId.trim() !== "";
}

function getStorage(storage?: StorageLike): StorageLike {
  if (storage !== undefined) return storage;
  if (typeof window === "undefined") throw new Error("Browser storage is unavailable.");
  return window.localStorage;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
