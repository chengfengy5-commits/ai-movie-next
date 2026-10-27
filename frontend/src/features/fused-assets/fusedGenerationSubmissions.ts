import type { FusedGenerationMemberType } from "../../shared/api/fusedGeneration";
import type { StorageLike } from "../../shared/api/storage";

export interface FusedGenerationSubmissionLocator {
  readonly version: 1;
  readonly userId: string;
  readonly apiBaseUrl: string;
  readonly seriesId: string;
  readonly memberType: FusedGenerationMemberType;
  readonly operation: "fused.image";
  readonly idempotencyKey: string;
  readonly taskId?: string;
}

export type FusedGenerationSubmissionLocatorRead =
  | { readonly status: "missing" }
  | { readonly status: "found"; readonly locator: FusedGenerationSubmissionLocator }
  | { readonly status: "invalid" }
  | { readonly status: "unavailable" };

function locatorStorageKey(
  userId: string,
  apiBaseUrl: string,
  seriesId: string,
  memberType: FusedGenerationMemberType,
): string {
  return `haoai:fused-submission:v1:${encodeURIComponent(userId)}:${encodeURIComponent(apiBaseUrl)}:${encodeURIComponent(seriesId)}:${memberType}`;
}

export function newFusedSubmissionKey(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues !== "function") {
    throw new Error("secure random source is unavailable");
  }
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function readFusedGenerationSubmissionLocator(
  storage: StorageLike,
  expected: Pick<FusedGenerationSubmissionLocator, "userId" | "apiBaseUrl" | "seriesId" | "memberType">,
): FusedGenerationSubmissionLocatorRead {
  let raw: string | null;
  try {
    raw = storage.getItem(locatorStorageKey(
      expected.userId,
      expected.apiBaseUrl,
      expected.seriesId,
      expected.memberType,
    ));
  } catch {
    return { status: "unavailable" };
  }
  if (raw === null) return { status: "missing" };
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return { status: "invalid" };
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return { status: "invalid" };
  }
  const row = value as Record<string, unknown>;
  const allowedKeys = new Set(["version", "userId", "apiBaseUrl", "seriesId", "memberType", "operation", "idempotencyKey", "taskId"]);
  if (Object.keys(row).some((key) => !allowedKeys.has(key))) return { status: "invalid" };
  if (row.version !== 1 || row.userId !== expected.userId
    || row.apiBaseUrl !== expected.apiBaseUrl || row.seriesId !== expected.seriesId
    || row.memberType !== expected.memberType || row.operation !== "fused.image"
    || typeof row.idempotencyKey !== "string" || row.idempotencyKey.length === 0
    || row.idempotencyKey.length > 255 || row.idempotencyKey.trim() !== row.idempotencyKey
    || (row.taskId !== undefined
      && (typeof row.taskId !== "string" || row.taskId.length === 0 || row.taskId.length > 128))) {
    return { status: "invalid" };
  }
  return {
    status: "found",
    locator: Object.freeze({
      version: 1,
      userId: expected.userId,
      apiBaseUrl: expected.apiBaseUrl,
      seriesId: expected.seriesId,
      memberType: expected.memberType,
      operation: "fused.image",
      idempotencyKey: row.idempotencyKey,
      ...(typeof row.taskId === "string" ? { taskId: row.taskId } : {}),
    }),
  };
}

export function writeFusedGenerationSubmissionLocator(
  storage: StorageLike,
  locator: FusedGenerationSubmissionLocator,
): boolean {
  try {
    storage.setItem(
      locatorStorageKey(locator.userId, locator.apiBaseUrl, locator.seriesId, locator.memberType),
      JSON.stringify(locator),
    );
    return true;
  } catch {
    return false;
  }
}

export function removeFusedGenerationSubmissionLocator(
  storage: StorageLike,
  locator: Pick<FusedGenerationSubmissionLocator, "userId" | "apiBaseUrl" | "seriesId" | "memberType">,
): boolean {
  try {
    storage.removeItem(locatorStorageKey(
      locator.userId,
      locator.apiBaseUrl,
      locator.seriesId,
      locator.memberType,
    ));
    return true;
  } catch {
    return false;
  }
}
