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

export interface FusedGenerationSubmissionTargetLocator {
  readonly kind: "standalone" | "frame";
  readonly chapterId?: string;
  readonly storyboardAssetId?: string;
  readonly frameIndex?: number;
}

export interface FusedGenerationSubmissionRecord {
  readonly version: 2;
  readonly userId: string;
  readonly apiBaseUrl: string;
  readonly seriesId: string;
  readonly memberType: FusedGenerationMemberType;
  readonly operation: "fused.image";
  readonly idempotencyKey: string;
  readonly target: FusedGenerationSubmissionTargetLocator;
  readonly taskId?: string;
}

export interface FusedGenerationSubmissionScope {
  readonly userId: string;
  readonly apiBaseUrl: string;
  readonly seriesId: string;
}

export interface FusedGenerationSubmissionRegistryRead {
  readonly status: "available" | "unavailable";
  readonly records: readonly FusedGenerationSubmissionRecord[];
  readonly invalidKeys: readonly string[];
  readonly conflictingKeys: readonly string[];
  readonly unresolvedLegacyKeys: readonly string[];
}

interface EnumerableStorage extends StorageLike {
  readonly length: number;
  key(index: number): string | null;
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

function recordStorageKey(locator: Pick<
  FusedGenerationSubmissionRecord,
  "userId" | "apiBaseUrl" | "seriesId" | "memberType" | "idempotencyKey"
>): string {
  return `haoai:fused-submission:v2:${encodeURIComponent(locator.userId)}:${encodeURIComponent(locator.apiBaseUrl)}:${encodeURIComponent(locator.seriesId)}:${locator.memberType}:${encodeURIComponent(locator.idempotencyKey)}`;
}

function recordPrefix(scope: FusedGenerationSubmissionScope): string {
  return `haoai:fused-submission:v2:${encodeURIComponent(scope.userId)}:${encodeURIComponent(scope.apiBaseUrl)}:${encodeURIComponent(scope.seriesId)}:`;
}

function isMemberType(value: unknown): value is FusedGenerationMemberType {
  return value === "character" || value === "prop";
}

function isSafeId(value: unknown, maxLength = 255): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= maxLength
    && value.trim() === value;
}

function isSafeSubmissionKey(value: unknown): value is string {
  return isSafeId(value, 255) && !/[\u0000-\u001f\u007f]/u.test(value);
}

function sameTarget(
  left: FusedGenerationSubmissionTargetLocator,
  right: FusedGenerationSubmissionTargetLocator,
): boolean {
  return left.kind === right.kind && left.chapterId === right.chapterId
    && left.storyboardAssetId === right.storyboardAssetId && left.frameIndex === right.frameIndex;
}

function sameRecordIdentity(
  left: FusedGenerationSubmissionRecord,
  right: FusedGenerationSubmissionRecord,
): boolean {
  return left.memberType === right.memberType && sameTarget(left.target, right.target)
    && left.taskId === right.taskId;
}

function parseRecord(
  raw: string,
  scope: FusedGenerationSubmissionScope,
): FusedGenerationSubmissionRecord | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const row = parsed as Record<string, unknown>;
  const allowedKeys = new Set([
    "version", "userId", "apiBaseUrl", "seriesId", "memberType", "operation",
    "idempotencyKey", "target", "taskId",
  ]);
  if (Object.keys(row).some((key) => !allowedKeys.has(key))
    || row.version !== 2 || row.userId !== scope.userId || row.apiBaseUrl !== scope.apiBaseUrl
    || row.seriesId !== scope.seriesId || !isMemberType(row.memberType)
    || row.operation !== "fused.image" || !isSafeSubmissionKey(row.idempotencyKey)) return null;

  const target = row.target;
  if (target === null || typeof target !== "object" || Array.isArray(target)) return null;
  const targetRow = target as Record<string, unknown>;
  let normalizedTarget: FusedGenerationSubmissionTargetLocator;
  if (targetRow.kind === "standalone" && Object.keys(targetRow).length === 1) {
    normalizedTarget = { kind: "standalone" };
  } else if (targetRow.kind === "frame"
    && isSafeId(targetRow.chapterId)
    && Object.keys(targetRow).every((key) => ["kind", "chapterId", "storyboardAssetId", "frameIndex"].includes(key))
    && (targetRow.storyboardAssetId === undefined || isSafeId(targetRow.storyboardAssetId))
    && (targetRow.frameIndex === undefined
      || (Number.isSafeInteger(targetRow.frameIndex) && (targetRow.frameIndex as number) >= 0))) {
    normalizedTarget = {
      kind: "frame",
      chapterId: targetRow.chapterId,
      ...(typeof targetRow.storyboardAssetId === "string" ? { storyboardAssetId: targetRow.storyboardAssetId } : {}),
      ...(typeof targetRow.frameIndex === "number" ? { frameIndex: targetRow.frameIndex } : {}),
    };
  } else {
    return null;
  }
  if (row.taskId !== undefined && !isSafeId(row.taskId, 128)) return null;
  return Object.freeze({
    version: 2,
    userId: scope.userId,
    apiBaseUrl: scope.apiBaseUrl,
    seriesId: scope.seriesId,
    memberType: row.memberType,
    operation: "fused.image",
    idempotencyKey: row.idempotencyKey,
    target: Object.freeze(normalizedTarget),
    ...(typeof row.taskId === "string" ? { taskId: row.taskId } : {}),
  });
}

function canEnumerate(storage: StorageLike): storage is EnumerableStorage {
  return typeof (storage as Partial<EnumerableStorage>).length === "number"
    && typeof (storage as Partial<EnumerableStorage>).key === "function";
}

function readLegacyForScope(
  storage: StorageLike,
  scope: FusedGenerationSubmissionScope,
  memberType: FusedGenerationMemberType,
): FusedGenerationSubmissionLocatorRead {
  return readFusedGenerationSubmissionLocator(storage, { ...scope, memberType });
}

function legacyAsRecord(locator: FusedGenerationSubmissionLocator): FusedGenerationSubmissionRecord {
  return Object.freeze({
    version: 2,
    userId: locator.userId,
    apiBaseUrl: locator.apiBaseUrl,
    seriesId: locator.seriesId,
    memberType: locator.memberType,
    operation: "fused.image",
    idempotencyKey: locator.idempotencyKey,
    target: Object.freeze({ kind: "standalone" }),
    ...(locator.taskId === undefined ? {} : { taskId: locator.taskId }),
  });
}

export function writeFusedGenerationSubmissionRecord(
  storage: StorageLike,
  record: FusedGenerationSubmissionRecord,
): boolean {
  const key = recordStorageKey(record);
  const serialized = JSON.stringify(record);
  try {
    const existing = storage.getItem(key);
    if (existing !== null) {
      const parsed = parseRecord(existing, record);
      return parsed !== null && parsed.idempotencyKey === record.idempotencyKey
        && parsed.memberType === record.memberType && sameTarget(parsed.target, record.target)
        && parsed.taskId === record.taskId;
    }
    storage.setItem(key, serialized);
    const readback = storage.getItem(key);
    if (readback === null) return false;
    const parsed = parseRecord(readback, record);
    return parsed !== null && parsed.idempotencyKey === record.idempotencyKey
      && parsed.memberType === record.memberType && sameTarget(parsed.target, record.target)
      && parsed.taskId === record.taskId;
  } catch {
    return false;
  }
}

export function readFusedGenerationSubmissionRegistry(
  storage: StorageLike,
  scope: FusedGenerationSubmissionScope,
): FusedGenerationSubmissionRegistryRead {
  if (!canEnumerate(storage)) {
    return { status: "unavailable", records: [], invalidKeys: [], conflictingKeys: [], unresolvedLegacyKeys: [] };
  }
  const prefix = recordPrefix(scope);
  const records = new Map<string, FusedGenerationSubmissionRecord>();
  const invalidKeys: string[] = [];
  const conflictingKeys = new Set<string>();
  try {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key === null || !key.startsWith(prefix)) continue;
      const raw = storage.getItem(key);
      const record = raw === null ? null : parseRecord(raw, scope);
      if (record === null || recordStorageKey(record) !== key) {
        invalidKeys.push(key);
        continue;
      }
      const existing = records.get(record.idempotencyKey);
      if (existing === undefined) records.set(record.idempotencyKey, record);
      else if (!sameRecordIdentity(existing, record)) conflictingKeys.add(record.idempotencyKey);
    }
  } catch {
    return { status: "unavailable", records: [], invalidKeys, conflictingKeys: [], unresolvedLegacyKeys: [] };
  }

  const unresolvedLegacyKeys: string[] = [];
  for (const memberType of ["character", "prop"] as const) {
    const legacy = readLegacyForScope(storage, scope, memberType);
    if (legacy.status === "found") {
      const candidate = legacyAsRecord(legacy.locator);
      const existing = records.get(candidate.idempotencyKey);
      if (existing === undefined) {
        if (writeFusedGenerationSubmissionRecord(storage, candidate)) {
          records.set(candidate.idempotencyKey, candidate);
        } else {
          records.set(candidate.idempotencyKey, candidate);
          unresolvedLegacyKeys.push(locatorStorageKey(scope.userId, scope.apiBaseUrl, scope.seriesId, memberType));
        }
      } else if (!sameRecordIdentity(existing, candidate)) {
        conflictingKeys.add(candidate.idempotencyKey);
        unresolvedLegacyKeys.push(locatorStorageKey(scope.userId, scope.apiBaseUrl, scope.seriesId, memberType));
      }
    } else if (legacy.status === "invalid" || legacy.status === "unavailable") {
      unresolvedLegacyKeys.push(locatorStorageKey(scope.userId, scope.apiBaseUrl, scope.seriesId, memberType));
    }
  }
  return {
    status: "available",
    records: [...records.values()].sort((left, right) => left.idempotencyKey.localeCompare(right.idempotencyKey)),
    invalidKeys,
    conflictingKeys: [...conflictingKeys].sort(),
    unresolvedLegacyKeys,
  };
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
    || !isSafeSubmissionKey(row.idempotencyKey)
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
