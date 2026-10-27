export type BatchVideoCheck = "character" | "scene" | "prop" | "storyboard";

export interface BatchVideoChecks extends Record<BatchVideoCheck, boolean> {}

export interface BatchVideoPreference {
  readonly modelId: string;
  readonly promptId: string;
  readonly size: string;
  readonly resolution: string;
  readonly checks: BatchVideoChecks;
}

export type BatchVideoPreferenceRead =
  | { readonly kind: "found"; readonly value: BatchVideoPreference }
  | { readonly kind: "missing" | "invalid" | "unavailable" };

export function defaultBatchVideoChecks(): BatchVideoChecks {
  return { character: true, scene: true, prop: true, storyboard: true };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function batchVideoPreferenceKey(userId: string, apiBaseUrl: string | null): string {
  return [
    "haoai:batch-video-preferences:v1",
    encodeURIComponent(apiBaseUrl ?? ""),
    encodeURIComponent(userId),
  ].join(":");
}

export function readBatchVideoPreference(
  userId: string,
  apiBaseUrl: string | null,
  storage?: Pick<Storage, "getItem">,
): BatchVideoPreferenceRead {
  let raw: string | null;
  try {
    raw = (storage ?? globalThis.localStorage).getItem(batchVideoPreferenceKey(userId, apiBaseUrl));
  } catch {
    return { kind: "unavailable" };
  }
  if (raw === null) return { kind: "missing" };

  try {
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value) || typeof value.modelId !== "string" || typeof value.promptId !== "string"
      || typeof value.size !== "string" || typeof value.resolution !== "string") {
      return { kind: "invalid" };
    }
    const checksValue = isRecord(value.checks) ? value.checks : {};
    const checks = defaultBatchVideoChecks();
    for (const field of Object.keys(checks) as BatchVideoCheck[]) {
      if (typeof checksValue[field] === "boolean") checks[field] = checksValue[field] as boolean;
    }
    return {
      kind: "found",
      value: {
        modelId: value.modelId,
        promptId: value.promptId,
        size: value.size,
        resolution: value.resolution,
        checks,
      },
    };
  } catch {
    return { kind: "invalid" };
  }
}

export function writeBatchVideoPreference(
  userId: string,
  apiBaseUrl: string | null,
  preference: BatchVideoPreference,
  storage?: Pick<Storage, "setItem">,
): boolean {
  try {
    (storage ?? globalThis.localStorage).setItem(
      batchVideoPreferenceKey(userId, apiBaseUrl),
      JSON.stringify({
        modelId: preference.modelId,
        promptId: preference.promptId,
        size: preference.size,
        resolution: preference.resolution,
        checks: { ...preference.checks },
      }),
    );
    return true;
  } catch {
    return false;
  }
}
