import { describe, expect, it } from "vitest";
import type { StorageLike } from "../../shared/api/storage";
import {
  removeFusedGenerationSubmissionLocator,
  readFusedGenerationSubmissionRegistry,
  readFusedGenerationSubmissionLocator,
  writeFusedGenerationSubmissionLocator,
  writeFusedGenerationSubmissionRecord,
  type FusedGenerationSubmissionLocator,
  type FusedGenerationSubmissionRecord,
} from "./fusedGenerationSubmissions";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();
  get length(): number { return this.values.size; }
  key(index: number): string | null { return [...this.values.keys()][index] ?? null; }
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

const locator: FusedGenerationSubmissionLocator = {
  version: 1,
  userId: "actor-1",
  apiBaseUrl: "http://127.0.0.1:4175/api",
  seriesId: "series-1",
  memberType: "character",
  operation: "fused.image",
  idempotencyKey: "original-submission-key",
  taskId: "task-1",
};

describe("fused generation submission locator", () => {
  it("stores only the scoped lookup locator and restores it for the same actor scope", () => {
    const storage = new MemoryStorage();
    expect(writeFusedGenerationSubmissionLocator(storage, locator)).toBe(true);

    const restored = readFusedGenerationSubmissionLocator(storage, locator);

    expect(restored).toEqual({ status: "found", locator });
    expect(JSON.stringify(restored)).not.toContain("prompt");
    expect(JSON.stringify(restored)).not.toContain("image_url");
    expect(JSON.stringify(restored)).not.toContain("provider_secret");
    expect(readFusedGenerationSubmissionLocator(storage, {
      ...locator,
      userId: "other-actor",
    })).toEqual({ status: "missing" });
    expect(removeFusedGenerationSubmissionLocator(storage, locator)).toBe(true);
    expect(readFusedGenerationSubmissionLocator(storage, locator)).toEqual({ status: "missing" });
  });

  it("fails closed when private request data is mixed into a stored locator", () => {
    const storage = new MemoryStorage();
    expect(writeFusedGenerationSubmissionLocator(storage, locator)).toBe(true);
    const key = "haoai:fused-submission:v1:actor-1:http%3A%2F%2F127.0.0.1%3A4175%2Fapi:series-1:character";
    storage.setItem(key, JSON.stringify({ ...locator, prompt: "private prompt" }));

    const restored = readFusedGenerationSubmissionLocator(storage, locator);

    expect(restored).toEqual({ status: "invalid" });
  });

  it("distinguishes a missing key from corrupt and unreadable storage", () => {
    const storage = new MemoryStorage();
    expect(readFusedGenerationSubmissionLocator(storage, locator)).toEqual({ status: "missing" });
    storage.setItem("haoai:fused-submission:v1:actor-1:http%3A%2F%2F127.0.0.1%3A4175%2Fapi:series-1:character", "not-json");
    expect(readFusedGenerationSubmissionLocator(storage, locator)).toEqual({ status: "invalid" });

    const unreadable: StorageLike = {
      getItem: () => { throw new Error("storage unavailable"); },
      setItem: () => { throw new Error("storage unavailable"); },
      removeItem: () => { throw new Error("storage unavailable"); },
    };
    expect(readFusedGenerationSubmissionLocator(unreadable, locator)).toEqual({ status: "unavailable" });
  });

  it("keeps one immutable safe v2 record per original key and discovers all scoped records", () => {
    const storage = new MemoryStorage();
    const first: FusedGenerationSubmissionRecord = {
      version: 2,
      userId: "actor-1",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      seriesId: "series-1",
      memberType: "character",
      operation: "fused.image",
      idempotencyKey: "first-key",
      target: { kind: "frame", chapterId: "chapter-1", storyboardAssetId: "shot-a", frameIndex: 1 },
    };
    const second: FusedGenerationSubmissionRecord = {
      ...first,
      memberType: "prop",
      idempotencyKey: "second-key",
      target: { kind: "standalone" },
    };

    expect(writeFusedGenerationSubmissionRecord(storage, first)).toBe(true);
    expect(writeFusedGenerationSubmissionRecord(storage, second)).toBe(true);
    expect(writeFusedGenerationSubmissionRecord(storage, { ...first, target: { kind: "standalone" } })).toBe(false);
    const registry = readFusedGenerationSubmissionRegistry(storage, {
      userId: "actor-1",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      seriesId: "series-1",
    });

    expect(registry.status).toBe("available");
    expect(registry.records.map((record) => record.idempotencyKey)).toEqual(["first-key", "second-key"]);
    expect(registry.invalidKeys).toEqual([]);
    expect(readFusedGenerationSubmissionRegistry(storage, {
      userId: "another-actor",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      seriesId: "series-1",
    }).records).toEqual([]);
    const saved = [...Array(storage.length)].map((_, index) => storage.getItem(storage.key(index)!) ?? "").join("\n");
    expect(saved).not.toContain("prompt");
    expect(saved).not.toContain("https://");
    expect(saved).not.toContain("expected_frame");
    expect(saved).not.toContain("expected_content");
  });

  it("isolates conflicting v2 owners for one key without last-write-wins", () => {
    const storage = new MemoryStorage();
    const characterRecord: FusedGenerationSubmissionRecord = {
      version: 2,
      userId: locator.userId,
      apiBaseUrl: locator.apiBaseUrl,
      seriesId: locator.seriesId,
      memberType: "character",
      operation: "fused.image",
      idempotencyKey: "shared-original-key",
      target: { kind: "standalone" },
    };
    const propRecord: FusedGenerationSubmissionRecord = {
      ...characterRecord,
      memberType: "prop",
      target: { kind: "frame", chapterId: "chapter-b", storyboardAssetId: "shot-b", frameIndex: 2 },
    };
    expect(writeFusedGenerationSubmissionRecord(storage, characterRecord)).toBe(true);
    expect(writeFusedGenerationSubmissionRecord(storage, propRecord)).toBe(true);

    const registry = readFusedGenerationSubmissionRegistry(storage, locator);

    expect(registry.records).toEqual([characterRecord]);
    expect(registry.conflictingKeys).toEqual(["shared-original-key"]);
    expect(storage.length).toBe(2);
  });

  it("copies a v1 record to v2 only after readback and leaves the v1 source intact", () => {
    const storage = new MemoryStorage();
    expect(writeFusedGenerationSubmissionLocator(storage, locator)).toBe(true);

    const registry = readFusedGenerationSubmissionRegistry(storage, {
      userId: locator.userId,
      apiBaseUrl: locator.apiBaseUrl,
      seriesId: locator.seriesId,
    });

    expect(registry.records).toHaveLength(1);
    expect(registry.records[0]).toMatchObject({ version: 2, idempotencyKey: locator.idempotencyKey, taskId: "task-1" });
    expect(readFusedGenerationSubmissionLocator(storage, locator)).toMatchObject({ status: "found" });
    expect(storage.length).toBe(2);
  });

  it("surfaces a damaged v1 source without removing or replacing its raw value", () => {
    const storage = new MemoryStorage();
    const key = "haoai:fused-submission:v1:actor-1:http%3A%2F%2F127.0.0.1%3A4175%2Fapi:series-1:character";
    storage.setItem(key, "not-json");
    const registry = readFusedGenerationSubmissionRegistry(storage, {
      userId: "actor-1",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      seriesId: "series-1",
    });
    expect(registry.unresolvedLegacyKeys).toContain(key);
    expect(storage.getItem(key)).toBe("not-json");
  });

  it("does not accept a v2 write when storage cannot read back its value", () => {
    const storage: StorageLike = {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    };
    const record: FusedGenerationSubmissionRecord = {
      version: 2,
      userId: "actor-1",
      apiBaseUrl: "https://api.example.test",
      seriesId: "series-1",
      memberType: "character",
      operation: "fused.image",
      idempotencyKey: "write-fails-readback",
      target: { kind: "standalone" },
    };
    expect(writeFusedGenerationSubmissionRecord(storage, record)).toBe(false);
  });
});
