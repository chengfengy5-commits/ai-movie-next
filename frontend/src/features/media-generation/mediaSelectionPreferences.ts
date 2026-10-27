export type SingleMediaPreferenceMode = "image" | "video";
export type SingleMediaPreferenceCategory = "general" | "frame" | "character" | "scene" | "prop" | "storyboard";

export interface SingleMediaPreferenceScope {
  readonly apiBaseUrl: string | null;
  readonly userId: string;
}

export interface ImageSelectionPreference {
  readonly modelId: string;
  readonly promptId: string;
  readonly size: string;
}

export interface VideoSelectionPreference extends ImageSelectionPreference {
  readonly duration: string;
  readonly resolution: string;
}

export type SingleMediaSelectionPreference = ImageSelectionPreference | VideoSelectionPreference;

export type PreferenceReadResult<T> =
  | { readonly kind: "found"; readonly value: T }
  | { readonly kind: "missing" }
  | { readonly kind: "unavailable" }
  | { readonly kind: "invalid" };

type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;

const storageKeyPrefix = "haoai:media-generation-selection:v1";

export function singleMediaPreferenceKey(
  scope: SingleMediaPreferenceScope,
  mode: SingleMediaPreferenceMode,
  category: SingleMediaPreferenceCategory,
): string {
  return [
    storageKeyPrefix,
    encodeURIComponent(scope.apiBaseUrl ?? ""),
    encodeURIComponent(scope.userId),
    mode,
    category,
  ].join(":");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readPreference<T extends SingleMediaSelectionPreference>(
  scope: SingleMediaPreferenceScope,
  mode: SingleMediaPreferenceMode,
  category: SingleMediaPreferenceCategory,
  storage?: PreferenceStorage,
): PreferenceReadResult<T> {
  let raw: string | null;
  try {
    const source = storage ?? globalThis.localStorage;
    raw = source.getItem(singleMediaPreferenceKey(scope, mode, category));
  } catch {
    return { kind: "unavailable" };
  }
  if (raw === null) return { kind: "missing" };

  try {
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value)
      || typeof value.modelId !== "string"
      || typeof value.promptId !== "string"
      || typeof value.size !== "string") {
      return { kind: "invalid" };
    }
    if (mode === "image") {
      return {
        kind: "found",
        value: { modelId: value.modelId, promptId: value.promptId, size: value.size } as T,
      };
    }
    if (typeof value.duration !== "string" || typeof value.resolution !== "string") {
      return { kind: "invalid" };
    }
    return {
      kind: "found",
      value: {
        modelId: value.modelId,
        promptId: value.promptId,
        size: value.size,
        duration: value.duration,
        resolution: value.resolution,
      } as T,
    };
  } catch {
    return { kind: "invalid" };
  }
}

export function readSingleMediaPreference(
  scope: SingleMediaPreferenceScope,
  mode: "image",
  category: SingleMediaPreferenceCategory,
  storage?: PreferenceStorage,
): PreferenceReadResult<ImageSelectionPreference>;
export function readSingleMediaPreference(
  scope: SingleMediaPreferenceScope,
  mode: "video",
  category: SingleMediaPreferenceCategory,
  storage?: PreferenceStorage,
): PreferenceReadResult<VideoSelectionPreference>;
export function readSingleMediaPreference(
  scope: SingleMediaPreferenceScope,
  mode: SingleMediaPreferenceMode,
  category: SingleMediaPreferenceCategory,
  storage?: PreferenceStorage,
): PreferenceReadResult<SingleMediaSelectionPreference> {
  return readPreference(scope, mode, category, storage);
}

export function writeSingleMediaPreference(
  scope: SingleMediaPreferenceScope,
  mode: "image",
  category: SingleMediaPreferenceCategory,
  value: ImageSelectionPreference,
  storage?: PreferenceStorage,
): boolean;
export function writeSingleMediaPreference(
  scope: SingleMediaPreferenceScope,
  mode: "video",
  category: SingleMediaPreferenceCategory,
  value: VideoSelectionPreference,
  storage?: PreferenceStorage,
): boolean;
export function writeSingleMediaPreference(
  scope: SingleMediaPreferenceScope,
  mode: SingleMediaPreferenceMode,
  category: SingleMediaPreferenceCategory,
  value: SingleMediaSelectionPreference,
  storage?: PreferenceStorage,
): boolean {
  const storedValue = mode === "image"
    ? { modelId: value.modelId, promptId: value.promptId, size: value.size }
    : {
      modelId: value.modelId,
      promptId: value.promptId,
      size: value.size,
      duration: (value as VideoSelectionPreference).duration,
      resolution: (value as VideoSelectionPreference).resolution,
    };
  try {
    const destination = storage ?? globalThis.localStorage;
    destination.setItem(singleMediaPreferenceKey(scope, mode, category), JSON.stringify(storedValue));
    return true;
  } catch {
    return false;
  }
}
