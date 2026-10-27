import { afterEach, describe, expect, it, vi } from "vitest";
import {
  readSingleMediaModePreference,
  readSingleMediaPreference,
  singleMediaModePreferenceKey,
  singleMediaPreferenceKey,
  writeSingleMediaModePreference,
  writeSingleMediaPreference,
} from "./mediaSelectionPreferences";

const scope = { apiBaseUrl: "https://api.example.test", userId: "actor-a" };

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("single media selection preferences", () => {
  it("defaults legacy selection records without a count to one", () => {
    window.localStorage.setItem(singleMediaPreferenceKey(scope, "image", "scene"), JSON.stringify({
      modelId: "image-model",
      promptId: "image-prompt",
      size: "16:9",
    }));

    expect(readSingleMediaPreference(scope, "image", "scene")).toEqual({
      kind: "found",
      value: { modelId: "image-model", promptId: "image-prompt", size: "16:9", generationCount: 1 },
    });
    window.localStorage.setItem(singleMediaPreferenceKey(scope, "video", "frame"), JSON.stringify({
      modelId: "video-model",
      promptId: "video-prompt",
      size: "16:9",
      duration: "10",
      resolution: "1080p",
    }));
    expect(readSingleMediaPreference(scope, "video", "frame")).toEqual({
      kind: "found",
      value: {
        modelId: "video-model",
        promptId: "video-prompt",
        size: "16:9",
        generationCount: 1,
        duration: "10",
        resolution: "1080p",
      },
    });
  });

  it("writes and validates image/video counts while preserving video options", () => {
    expect(writeSingleMediaPreference(scope, "image", "scene", {
      modelId: "image-model", promptId: "image-prompt", size: "16:9", generationCount: 3,
    })).toBe(true);
    expect(readSingleMediaPreference(scope, "image", "scene")).toMatchObject({
      kind: "found", value: { generationCount: 3 },
    });

    expect(writeSingleMediaPreference(scope, "video", "scene", {
      modelId: "video-model", promptId: "video-prompt", size: "16:9", duration: "5", resolution: "720p", generationCount: 4,
    })).toBe(true);
    expect(readSingleMediaPreference(scope, "video", "scene")).toMatchObject({
      kind: "found", value: { generationCount: 4, duration: "5", resolution: "720p" },
    });
    window.localStorage.setItem(singleMediaPreferenceKey(scope, "image", "general"), JSON.stringify({
      modelId: "image-model", promptId: "", size: "", generationCount: 5,
    }));
    expect(readSingleMediaPreference(scope, "image", "general")).toEqual({ kind: "invalid" });
  });

  it("isolates remembered mode by actor, API and target category", () => {
    expect(writeSingleMediaModePreference(scope, "scene", "image")).toBe(true);
    expect(readSingleMediaModePreference(scope, "scene")).toEqual({ kind: "found", value: "image" });
    expect(readSingleMediaModePreference({ ...scope, userId: "actor-b" }, "scene")).toEqual({ kind: "missing" });
    expect(readSingleMediaModePreference({ ...scope, apiBaseUrl: "https://other.example.test" }, "scene")).toEqual({ kind: "missing" });
    expect(readSingleMediaModePreference(scope, "character")).toEqual({ kind: "missing" });
    expect(singleMediaModePreferenceKey(scope, "scene")).not.toBe(singleMediaModePreferenceKey(scope, "character"));
  });

  it("reports storage accessor and read failures without blocking manual state", () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() { throw new Error("storage disabled"); },
    });
    try {
      expect(readSingleMediaModePreference(scope, "scene")).toEqual({ kind: "unavailable" });
    } finally {
      if (original !== undefined) Object.defineProperty(globalThis, "localStorage", original);
    }

    const storage = {
      getItem: vi.fn(() => { throw new Error("read denied"); }),
      setItem: vi.fn(() => { throw new Error("write denied"); }),
    };
    expect(readSingleMediaPreference(scope, "image", "scene", storage)).toEqual({ kind: "unavailable" });
    expect(writeSingleMediaModePreference(scope, "scene", "video", storage)).toBe(false);
  });
});
