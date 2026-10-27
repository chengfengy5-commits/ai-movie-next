// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  clearSavedMediaExportRecords,
  findMatchingSavedMediaExportRecord,
  fingerprintMediaExportCandidate,
  mediaExportPreferencesStorageKey,
  mediaExportRecordIdentity,
  mediaExportRecordsStorageKey,
  readSavedMediaExportRecords,
  readSkipSavedPreference,
  toSavedMediaExportRecord,
  writeSavedMediaExportRecord,
  writeSkipSavedPreference,
  type MediaExportRecordCandidate,
  type MediaExportStorage,
} from "./exportRecords";

class MemoryStorage implements MediaExportStorage {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

const scope = {
  actorId: "actor-a",
  apiBaseUrl: "https://api.example.invalid/v1",
  seriesId: "series-a",
  chapterId: "chapter-a",
} as const;

const preferenceScope = {
  actorId: scope.actorId,
  apiBaseUrl: scope.apiBaseUrl,
} as const;

const sourceUrl = "https://cdn.example.invalid/clip.mp4?signature=do-not-store";

function candidate(assetId: string, sourceIndex: number, revision = "chapter-4"): MediaExportRecordCandidate {
  return {
    sourceKind: "chapter",
    assetId,
    sourceRevision: revision,
    sourceUrl,
    sourceText: `原文镜头 ${sourceIndex}`,
    filename: `${sourceIndex + 1}_章节_剧集.mp4`,
  };
}

const fingerprint = `sha256:${"a".repeat(64)}`;

describe("media export local records", () => {
  it("persists only a confirmed record identity, revision, and fingerprint, then matches the next queue", () => {
    const storage = new MemoryStorage();
    const firstFrame = candidate("asset-a", 0);
    const secondFrame = candidate("asset-b", 1);
    const record = toSavedMediaExportRecord(firstFrame, fingerprint);

    expect(record).not.toBeNull();
    expect(writeSavedMediaExportRecord(storage, scope, record!, 0)).toBe(true);

    const stored = storage.getItem(mediaExportRecordsStorageKey(scope));
    expect(stored).not.toContain(sourceUrl);
    expect(stored).not.toContain(firstFrame.sourceText);
    expect(stored).not.toContain(firstFrame.filename);

    const records = readSavedMediaExportRecords(storage, scope);
    expect(records.status).toBe("ready");
    if (records.status !== "ready") return;
    expect(findMatchingSavedMediaExportRecord(records.value.records, firstFrame, fingerprint)).toEqual(record);
    expect(findMatchingSavedMediaExportRecord(records.value.records, secondFrame, fingerprint)).toBeNull();
  });

  it("does not create a record candidate without a stable item ID and source revision", async () => {
    const withoutId = { ...candidate("asset-a", 0), assetId: null };
    const withoutRevision = { ...candidate("asset-a", 0), sourceRevision: null };
    expect(await fingerprintMediaExportCandidate(withoutId)).toBeNull();
    expect(toSavedMediaExportRecord(withoutId, fingerprint)).toBeNull();
    expect(await fingerprintMediaExportCandidate(withoutRevision)).toBeNull();
    expect(toSavedMediaExportRecord(withoutRevision, fingerprint)).toBeNull();
  });

  it("invalidates on source revision or source fingerprint change", () => {
    const storage = new MemoryStorage();
    const original = candidate("asset-a", 0);
    const record = toSavedMediaExportRecord(original, fingerprint);
    expect(record).not.toBeNull();
    expect(writeSavedMediaExportRecord(storage, scope, record!, 0)).toBe(true);
    const records = readSavedMediaExportRecords(storage, scope);
    expect(records.status).toBe("ready");
    if (records.status !== "ready") return;

    expect(findMatchingSavedMediaExportRecord(records.value.records, candidate("asset-a", 0, "chapter-5"), fingerprint))
      .toBeNull();
    const changedSource = `sha256:${"b".repeat(64)}`;
    expect(findMatchingSavedMediaExportRecord(records.value.records, original, changedSource)).toBeNull();
  });

  it("fingerprints the source without retaining its URL or text", async () => {
    const original = candidate("asset-a", 0);
    const sourceFingerprint = await fingerprintMediaExportCandidate(original);
    expect(sourceFingerprint).toMatch(/^sha256:[\da-f]{64}$/u);
    expect(await fingerprintMediaExportCandidate({ ...original, sourceText: "changed source text" }))
      .not.toBe(sourceFingerprint);
    expect(await fingerprintMediaExportCandidate({ ...original, sourceUrl: "https://cdn.example.invalid/other.mp4" }))
      .not.toBe(sourceFingerprint);
  });

  it("isolates records by actor, API, series, and chapter and preferences by actor and API", () => {
    const storage = new MemoryStorage();
    const record = toSavedMediaExportRecord(candidate("asset-a", 0), fingerprint);
    expect(record).not.toBeNull();
    expect(writeSavedMediaExportRecord(storage, scope, record!, 0)).toBe(true);

    const otherScopes = [
      { ...scope, actorId: "actor-b" },
      { ...scope, apiBaseUrl: "https://other-api.example.invalid/v1" },
      { ...scope, seriesId: "series-b" },
      { ...scope, chapterId: "chapter-b" },
    ];
    for (const otherScope of otherScopes) {
      expect(readSavedMediaExportRecords(storage, otherScope)).toEqual({
        status: "ready",
        value: { generation: 0, records: [] },
      });
    }

    expect(writeSkipSavedPreference(storage, preferenceScope, true)).toBe(true);
    expect(readSkipSavedPreference(storage, preferenceScope)).toEqual({ status: "ready", value: true });
    expect(mediaExportPreferencesStorageKey(preferenceScope)).not.toBe(
      mediaExportPreferencesStorageKey({ ...preferenceScope, actorId: "actor-b" }),
    );
  });

  it("keeps same-URL frames distinct by stable source identity", () => {
    const left = candidate("asset-left", 0);
    const right = candidate("asset-right", 1);
    const leftRecord = toSavedMediaExportRecord(left, fingerprint);
    expect(leftRecord).not.toBeNull();

    expect(mediaExportRecordIdentity("chapter", "asset-left"))
      .not.toBe(mediaExportRecordIdentity("chapter", "asset-right"));
    expect(findMatchingSavedMediaExportRecord([leftRecord!], right, fingerprint)).toBeNull();
  });

  it("gives explicit clear precedence over a late saved receipt", () => {
    const storage = new MemoryStorage();
    const record = toSavedMediaExportRecord(candidate("asset-a", 0), fingerprint);
    expect(record).not.toBeNull();
    expect(writeSavedMediaExportRecord(storage, scope, record!, 0)).toBe(true);

    expect(clearSavedMediaExportRecords(storage, scope)).toBe(1);
    expect(writeSavedMediaExportRecord(storage, scope, record!, 0)).toBe(false);
    expect(readSavedMediaExportRecords(storage, scope)).toEqual({
      status: "ready",
      value: { generation: 1, records: [] },
    });
  });

  it("does not migrate URL-only legacy entries and leaves exports unaffected by storage failures", () => {
    const legacyStorage = new MemoryStorage();
    legacyStorage.setItem("export_videos_done_chapter-a", JSON.stringify([sourceUrl]));
    expect(readSavedMediaExportRecords(legacyStorage, scope)).toEqual({
      status: "ready",
      value: { generation: 0, records: [] },
    });
    expect(legacyStorage.getItem("export_videos_done_chapter-a")).toContain(sourceUrl);

    const brokenStorage: MediaExportStorage = {
      getItem() { throw new Error("blocked"); },
      setItem() { throw new Error("blocked"); },
      removeItem() { throw new Error("blocked"); },
    };
    expect(readSavedMediaExportRecords(brokenStorage, scope)).toEqual({ status: "unavailable" });
    expect(readSkipSavedPreference(brokenStorage, preferenceScope)).toEqual({ status: "unavailable" });
    expect(writeSkipSavedPreference(brokenStorage, preferenceScope, true)).toBe(false);
    expect(writeSavedMediaExportRecord(brokenStorage, scope, toSavedMediaExportRecord(candidate("asset-a", 0), fingerprint)!, 0))
      .toBe(false);
    expect(clearSavedMediaExportRecords(brokenStorage, scope)).toBeNull();
  });
});
