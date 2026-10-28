import { describe, expect, it } from "vitest";
import type { StorageLike } from "../../shared/api/storage";
import {
  canonicalTaskControlApiBaseUrl,
  createTaskControlPendingScope,
  hasTaskControlPendingMarker,
  removeTaskControlPendingMarker,
  TASK_CONTROL_PENDING_STORAGE_KEY,
  TaskControlPendingStorageError,
  writeTaskControlPendingMarker,
} from "./taskControlPending";

class MemoryStorage implements StorageLike {
  readonly values = new Map<string, string>();
  failRead = false;
  failWrite = false;

  getItem(key: string): string | null {
    if (this.failRead) throw new Error("storage read failed");
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    if (this.failWrite) throw new Error("storage write failed");
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

describe("task cancel pending marker", () => {
  it("stores only owner, canonical API and task and isolates other scopes", () => {
    const storage = new MemoryStorage();
    const scope = createTaskControlPendingScope("user-1", "https://api.example.invalid/api/", "task-1");
    if (scope === null) throw new Error("Expected a valid scope.");

    expect(writeTaskControlPendingMarker(scope, storage)).toBe("stored");
    expect(writeTaskControlPendingMarker(scope, storage)).toBe("already_present");
    expect(hasTaskControlPendingMarker(scope, storage)).toBe(true);
    expect(hasTaskControlPendingMarker({ ...scope, userId: "user-2" }, storage)).toBe(false);
    expect(hasTaskControlPendingMarker({ ...scope, apiBaseUrl: "https://other.example.invalid/api" }, storage)).toBe(false);
    expect(hasTaskControlPendingMarker({ ...scope, taskId: "task-2" }, storage)).toBe(false);

    const serialized = storage.values.get(TASK_CONTROL_PENDING_STORAGE_KEY) ?? "";
    expect(serialized).toContain("user-1");
    expect(serialized).toContain("https://api.example.invalid/api");
    expect(serialized).toContain("task-1");
    expect(serialized).not.toMatch(/token|request|media|prompt|secret/iu);
    expect(removeTaskControlPendingMarker(scope, storage)).toBe(true);
    expect(storage.getItem(TASK_CONTROL_PENDING_STORAGE_KEY)).toBeNull();
  });

  it("rejects incomplete or credential-bearing API identities", () => {
    expect(canonicalTaskControlApiBaseUrl(null)).toBeNull();
    expect(canonicalTaskControlApiBaseUrl("https://user:pass@example.invalid/api")).toBeNull();
    expect(canonicalTaskControlApiBaseUrl("https://api.example.invalid/api?token=secret")).toBeNull();
    expect(createTaskControlPendingScope("user", null, "task")).toBeNull();
  });

  it("fails closed on malformed records and storage errors without replacing existing data", () => {
    const storage = new MemoryStorage();
    const scope = createTaskControlPendingScope("user-1", "https://api.example.invalid/api", "task-1");
    if (scope === null) throw new Error("Expected a valid scope.");
    storage.setItem(TASK_CONTROL_PENDING_STORAGE_KEY, "{broken");
    expect(() => writeTaskControlPendingMarker(scope, storage)).toThrow(TaskControlPendingStorageError);
    expect(storage.getItem(TASK_CONTROL_PENDING_STORAGE_KEY)).toBe("{broken");
    storage.failRead = true;
    expect(() => hasTaskControlPendingMarker(scope, storage)).toThrow(TaskControlPendingStorageError);
    storage.failRead = false;
    storage.failWrite = true;
    storage.removeItem(TASK_CONTROL_PENDING_STORAGE_KEY);
    expect(() => writeTaskControlPendingMarker(scope, storage)).toThrow(TaskControlPendingStorageError);
  });
});
