import { describe, expect, it } from "vitest";
import type { StorageLike } from "../../shared/api/storage";
import {
  removeFusedGenerationSubmissionLocator,
  readFusedGenerationSubmissionLocator,
  writeFusedGenerationSubmissionLocator,
  type FusedGenerationSubmissionLocator,
} from "./fusedGenerationSubmissions";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();
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
});
