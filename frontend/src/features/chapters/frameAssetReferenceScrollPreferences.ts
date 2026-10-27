import type { Chapter, StoryboardFrame } from "../../shared/api/contracts";
import type { FrameAssetReferenceCategory } from "./frameAssetReferences";

const STORAGE_KEY_PREFIX = "haoai.frame-asset-reference-scroll.v1";

export interface FrameAssetReferenceScrollScope {
  readonly userId: string;
  readonly apiBaseUrl: string;
  readonly seriesId: string;
  readonly chapterId: string;
  readonly storyboardId: string;
  readonly category: FrameAssetReferenceCategory;
}

export type FrameAssetReferenceScrollReadResult =
  | { readonly kind: "stored"; readonly scrollTop: number }
  | { readonly kind: "missing" | "unavailable" | "invalid-scope" };

export type FrameAssetReferenceScrollWriteResult =
  | "saved"
  | "unavailable"
  | "invalid-scope"
  | "invalid-position";

export type FrameAssetReferenceScrollStorage = Pick<Storage, "getItem" | "setItem">;

function firstStoryboardId(frame: StoryboardFrame): string | null {
  const storyboard = frame.storyboard;
  if (!Array.isArray(storyboard)) return null;
  const first = storyboard[0];
  return typeof first === "string" && first.trim() !== "" ? first : null;
}

export function uniqueStoryboardFrameId(chapter: Chapter, frame: StoryboardFrame): string | null {
  if (!Array.isArray(chapter.content)) return null;
  const id = firstStoryboardId(frame);
  if (id === null) return null;

  let matches = 0;
  for (const candidate of chapter.content) {
    if (firstStoryboardId(candidate) !== id) continue;
    matches += 1;
    if (matches > 1) return null;
  }
  return matches === 1 ? id : null;
}

function storageKey(scope: FrameAssetReferenceScrollScope): string | null {
  const parts = [scope.userId, scope.apiBaseUrl, scope.seriesId, scope.chapterId, scope.storyboardId];
  if (parts.some((part) => typeof part !== "string" || part.trim() === "")) return null;
  if (scope.category !== "characters" && scope.category !== "scenes" && scope.category !== "props") return null;
  return `${STORAGE_KEY_PREFIX}:${encodeURIComponent(JSON.stringify([...parts, scope.category]))}`;
}

function browserStorage(): FrameAssetReferenceScrollStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function resolveStorage(
  storage: FrameAssetReferenceScrollStorage | null | undefined,
): FrameAssetReferenceScrollStorage | null {
  return storage === undefined ? browserStorage() : storage;
}

export function readFrameAssetReferenceScrollTop(
  scope: FrameAssetReferenceScrollScope,
  storage?: FrameAssetReferenceScrollStorage | null,
): FrameAssetReferenceScrollReadResult {
  const key = storageKey(scope);
  if (key === null) return { kind: "invalid-scope" };
  const source = resolveStorage(storage);
  if (source === null) return { kind: "unavailable" };

  let raw: string | null;
  try {
    raw = source.getItem(key);
  } catch {
    return { kind: "unavailable" };
  }
  if (raw === null) return { kind: "missing" };

  try {
    const parsed: unknown = JSON.parse(raw);
    return typeof parsed === "number" && Number.isSafeInteger(parsed) && parsed >= 0
      ? { kind: "stored", scrollTop: parsed }
      : { kind: "missing" };
  } catch {
    return { kind: "missing" };
  }
}

export function writeFrameAssetReferenceScrollTop(
  scope: FrameAssetReferenceScrollScope,
  scrollTop: number,
  storage?: FrameAssetReferenceScrollStorage | null,
): FrameAssetReferenceScrollWriteResult {
  const key = storageKey(scope);
  if (key === null) return "invalid-scope";
  if (!Number.isFinite(scrollTop) || scrollTop < 0) return "invalid-position";
  const source = resolveStorage(storage);
  if (source === null) return "unavailable";

  try {
    source.setItem(key, JSON.stringify(Math.floor(scrollTop)));
    return "saved";
  } catch {
    return "unavailable";
  }
}
