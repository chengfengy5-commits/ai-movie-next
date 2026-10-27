import type { MediaExportSourceKind } from "../../../shared/api/mediaExport";

const RECORDS_PREFIX = "haoai.media-export.records.v1";
const PREFERENCES_PREFIX = "haoai.media-export.preferences.v1";

export interface MediaExportRecordScope {
  readonly actorId: string;
  readonly apiBaseUrl: string;
  readonly seriesId: string;
  readonly chapterId: string;
}

export interface MediaExportPreferenceScope {
  readonly actorId: string;
  readonly apiBaseUrl: string;
}

export interface MediaExportRecordCandidate {
  readonly sourceKind: MediaExportSourceKind;
  readonly assetId: string | null;
  readonly sourceRevision: string | number | null;
  readonly sourceUrl: string | null;
  readonly sourceText: string;
  readonly filename: string;
}

export interface SavedMediaExportRecord {
  readonly sourceKind: MediaExportSourceKind;
  readonly assetId: string;
  readonly sourceRevision: string | number;
  readonly sourceFingerprint: string;
}

export interface SavedMediaExportRecords {
  readonly generation: number;
  readonly records: readonly SavedMediaExportRecord[];
}

export interface MediaExportStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export type StorageRead<T> =
  | { readonly status: "ready"; readonly value: T }
  | { readonly status: "unavailable" };

function encodeScopePart(value: string): string {
  return encodeURIComponent(value);
}

function apiIdentity(value: string): string {
  try {
    const url = new URL(value);
    url.username = "";
    url.password = "";
    url.hash = "";
    return url.toString();
  } catch {
    return value.split("#", 1)[0] ?? value;
  }
}

export function mediaExportRecordsStorageKey(scope: MediaExportRecordScope): string {
  return [
    RECORDS_PREFIX,
    encodeScopePart(scope.actorId),
    encodeScopePart(apiIdentity(scope.apiBaseUrl)),
    encodeScopePart(scope.seriesId),
    encodeScopePart(scope.chapterId),
  ].join(":");
}

export function mediaExportPreferencesStorageKey(scope: MediaExportPreferenceScope): string {
  return [
    PREFERENCES_PREFIX,
    encodeScopePart(scope.actorId),
    encodeScopePart(apiIdentity(scope.apiBaseUrl)),
  ].join(":");
}

export function getMediaExportStorage(): MediaExportStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function mediaExportRecordIdentity(
  sourceKind: MediaExportSourceKind,
  assetId: string,
): string {
  return JSON.stringify([sourceKind, assetId]);
}

function hasStableRevision(value: string | number | null): value is string | number {
  return typeof value === "number"
    ? Number.isFinite(value)
    : typeof value === "string" && value.trim() !== "";
}

function isRecord(value: unknown): value is SavedMediaExportRecord {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<SavedMediaExportRecord>;
  return (candidate.sourceKind === "chapter" || candidate.sourceKind === "rough-cut")
    && typeof candidate.assetId === "string"
    && candidate.assetId.trim() !== ""
    && hasStableRevision(candidate.sourceRevision ?? null)
    && typeof candidate.sourceFingerprint === "string"
    && /^sha256:[\da-f]{64}$/u.test(candidate.sourceFingerprint);
}

function parseSavedMediaExportRecords(raw: string | null): StorageRead<SavedMediaExportRecords> {
  if (raw === null) return { status: "ready", value: { generation: 0, records: [] } };
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return { status: "unavailable" };
    const value = parsed as { version?: unknown; generation?: unknown; records?: unknown };
    if (
      value.version !== 1
      || !Number.isSafeInteger(value.generation)
      || (value.generation as number) < 0
      || !Array.isArray(value.records)
      || !value.records.every(isRecord)
    ) {
      return { status: "unavailable" };
    }
    return {
      status: "ready",
      value: { generation: value.generation as number, records: value.records },
    };
  } catch {
    return { status: "unavailable" };
  }
}

export function readSavedMediaExportRecords(
  storage: MediaExportStorage | null,
  scope: MediaExportRecordScope,
): StorageRead<SavedMediaExportRecords> {
  if (storage === null) return { status: "unavailable" };
  try {
    return parseSavedMediaExportRecords(storage.getItem(mediaExportRecordsStorageKey(scope)));
  } catch {
    return { status: "unavailable" };
  }
}

export function readSkipSavedPreference(
  storage: MediaExportStorage | null,
  scope: MediaExportPreferenceScope,
): StorageRead<boolean> {
  if (storage === null) return { status: "unavailable" };
  try {
    const raw = storage.getItem(mediaExportPreferencesStorageKey(scope));
    if (raw === null) return { status: "ready", value: false };
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return { status: "unavailable" };
    const value = parsed as { version?: unknown; skipConfirmedSaved?: unknown };
    if (value.version !== 1 || typeof value.skipConfirmedSaved !== "boolean") {
      return { status: "unavailable" };
    }
    return { status: "ready", value: value.skipConfirmedSaved };
  } catch {
    return { status: "unavailable" };
  }
}

export function writeSkipSavedPreference(
  storage: MediaExportStorage | null,
  scope: MediaExportPreferenceScope,
  skipConfirmedSaved: boolean,
): boolean {
  if (storage === null) return false;
  try {
    storage.setItem(mediaExportPreferencesStorageKey(scope), JSON.stringify({
      version: 1,
      skipConfirmedSaved,
    }));
    return true;
  } catch {
    return false;
  }
}

export function clearSavedMediaExportRecords(
  storage: MediaExportStorage | null,
  scope: MediaExportRecordScope,
): number | null {
  if (storage === null) return null;
  try {
    const key = mediaExportRecordsStorageKey(scope);
    const current = readSavedMediaExportRecords(storage, scope);
    if (current.status !== "ready") return null;
    const nextGeneration = current.value.generation + 1;
    storage.setItem(key, JSON.stringify({ version: 1, generation: nextGeneration, records: [] }));
    return nextGeneration;
  } catch {
    return null;
  }
}

export async function fingerprintMediaExportCandidate(
  candidate: MediaExportRecordCandidate,
): Promise<string | null> {
  if (
    candidate.assetId === null
    || candidate.assetId.trim() === ""
    || candidate.sourceUrl === null
    || !hasStableRevision(candidate.sourceRevision)
  ) {
    return null;
  }
  const subtle = globalThis.crypto?.subtle;
  if (subtle === undefined) return null;
  try {
    const bytes = new TextEncoder().encode(JSON.stringify([
      candidate.sourceUrl,
      candidate.sourceText,
      candidate.filename,
    ]));
    const digest = await subtle.digest("SHA-256", bytes);
    const hex = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `sha256:${hex}`;
  } catch {
    return null;
  }
}

export function toSavedMediaExportRecord(
  candidate: MediaExportRecordCandidate,
  sourceFingerprint: string | null,
): SavedMediaExportRecord | null {
  if (
    candidate.assetId === null
    || candidate.assetId.trim() === ""
    || !hasStableRevision(candidate.sourceRevision)
    || sourceFingerprint === null
    || !/^sha256:[\da-f]{64}$/u.test(sourceFingerprint)
  ) {
    return null;
  }
  return {
    sourceKind: candidate.sourceKind,
    assetId: candidate.assetId,
    sourceRevision: candidate.sourceRevision,
    sourceFingerprint,
  };
}

export function findMatchingSavedMediaExportRecord(
  records: readonly SavedMediaExportRecord[],
  candidate: MediaExportRecordCandidate,
  sourceFingerprint: string | null,
): SavedMediaExportRecord | null {
  if (candidate.assetId === null || sourceFingerprint === null || !hasStableRevision(candidate.sourceRevision)) {
    return null;
  }
  const identity = mediaExportRecordIdentity(candidate.sourceKind, candidate.assetId);
  return records.find((record) => (
    mediaExportRecordIdentity(record.sourceKind, record.assetId) === identity
    && record.sourceRevision === candidate.sourceRevision
    && record.sourceFingerprint === sourceFingerprint
  )) ?? null;
}

export function countStaleMediaExportRecords(
  records: readonly SavedMediaExportRecord[],
  candidates: readonly MediaExportRecordCandidate[],
  fingerprints: ReadonlyMap<string, string>,
): number {
  const candidatesByIdentity = new Map(candidates.flatMap((candidate) => (
    candidate.assetId === null
      ? []
      : [[mediaExportRecordIdentity(candidate.sourceKind, candidate.assetId), candidate] as const]
  )));
  return records.filter((record) => {
    const identity = mediaExportRecordIdentity(record.sourceKind, record.assetId);
    const candidate = candidatesByIdentity.get(identity);
    const fingerprint = fingerprints.get(identity);
    return candidate === undefined
      || candidate.sourceRevision !== record.sourceRevision
      || fingerprint !== undefined && fingerprint !== record.sourceFingerprint;
  }).length;
}

export function writeSavedMediaExportRecord(
  storage: MediaExportStorage | null,
  scope: MediaExportRecordScope,
  record: SavedMediaExportRecord,
  expectedGeneration: number,
): boolean {
  if (storage === null) return false;
  try {
    const key = mediaExportRecordsStorageKey(scope);
    const current = readSavedMediaExportRecords(storage, scope);
    if (current.status !== "ready" || current.value.generation !== expectedGeneration) return false;
    const identity = mediaExportRecordIdentity(record.sourceKind, record.assetId);
    const nextRecords = current.value.records.filter((candidate) => (
      mediaExportRecordIdentity(candidate.sourceKind, candidate.assetId) !== identity
    ));
    nextRecords.push(record);
    storage.setItem(key, JSON.stringify({ version: 1, generation: expectedGeneration, records: nextRecords }));
    return true;
  } catch {
    return false;
  }
}
