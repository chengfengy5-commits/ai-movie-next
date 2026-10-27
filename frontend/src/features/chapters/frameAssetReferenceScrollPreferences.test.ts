import { describe, expect, it, vi } from "vitest";
import type { Chapter, StoryboardFrame } from "../../shared/api/contracts";
import {
  readFrameAssetReferenceScrollTop,
  uniqueStoryboardFrameId,
  writeFrameAssetReferenceScrollTop,
  type FrameAssetReferenceScrollScope,
  type FrameAssetReferenceScrollStorage,
} from "./frameAssetReferenceScrollPreferences";

function scope(overrides: Partial<FrameAssetReferenceScrollScope> = {}): FrameAssetReferenceScrollScope {
  return {
    userId: "scroll-user",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    seriesId: "scroll-series",
    chapterId: "scroll-chapter",
    storyboardId: "stable-frame-id",
    category: "characters",
    ...overrides,
  };
}

function memoryStorage(): FrameAssetReferenceScrollStorage & { records: Map<string, string> } {
  const records = new Map<string, string>();
  return {
    records,
    getItem: (key) => records.get(key) ?? null,
    setItem: (key, value) => { records.set(key, value); },
  };
}

function storyboardFrame(id: string | null): StoryboardFrame {
  return id === null ? { text: "镜头" } : { storyboard: [id], text: "镜头" };
}

function chapter(content: StoryboardFrame[] | null): Chapter {
  return {
    id: "scroll-chapter",
    series_id: "scroll-series",
    title: "滚动测试章节",
    content,
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

describe("frame asset reference scroll preferences", () => {
  it("isolates saved positions by owner, API, chapter, frame, and category", () => {
    const storage = memoryStorage();
    const original = scope();
    const otherScopes = [
      scope({ userId: "other-user" }),
      scope({ apiBaseUrl: "http://127.0.0.1:4176/api" }),
      scope({ seriesId: "other-series" }),
      scope({ chapterId: "other-chapter" }),
      scope({ storyboardId: "other-frame" }),
      scope({ category: "scenes" }),
    ];

    expect(writeFrameAssetReferenceScrollTop(original, 47.8, storage)).toBe("saved");
    for (const other of otherScopes) {
      expect(readFrameAssetReferenceScrollTop(other, storage)).toEqual({ kind: "missing" });
    }
    expect(readFrameAssetReferenceScrollTop(original, storage)).toEqual({ kind: "stored", scrollTop: 47 });
  });

  it("ignores invalid stored positions and refuses invalid scopes", () => {
    const storage = memoryStorage();
    const current = scope();
    expect(writeFrameAssetReferenceScrollTop(current, 25, storage)).toBe("saved");
    const [key] = storage.records.keys();
    expect(key).toBeDefined();

    for (const raw of ["broken", "-1", "1.5", "9007199254740992", "null"]) {
      storage.records.set(key!, raw);
      expect(readFrameAssetReferenceScrollTop(current, storage)).toEqual({ kind: "missing" });
    }
    expect(writeFrameAssetReferenceScrollTop(current, Number.NaN, storage)).toBe("invalid-position");
    expect(readFrameAssetReferenceScrollTop(scope({ userId: " " }), storage)).toEqual({ kind: "invalid-scope" });
    expect(writeFrameAssetReferenceScrollTop(scope({ storyboardId: "" }), 0, storage)).toBe("invalid-scope");
  });

  it("continues manual use when storage reads or writes fail", () => {
    const failedStorage: FrameAssetReferenceScrollStorage = {
      getItem: vi.fn(() => { throw new Error("Storage access denied."); }),
      setItem: vi.fn(() => { throw new Error("Storage access denied."); }),
    };

    expect(readFrameAssetReferenceScrollTop(scope(), failedStorage)).toEqual({ kind: "unavailable" });
    expect(writeFrameAssetReferenceScrollTop(scope(), 80, failedStorage)).toBe("unavailable");
    expect(failedStorage.getItem).toHaveBeenCalledOnce();
    expect(failedStorage.setItem).toHaveBeenCalledOnce();
  });

  it("accepts only one stable storyboard id in the chapter and follows it across reordering", () => {
    const target = storyboardFrame("stable-frame-id");
    const other = storyboardFrame("other-frame-id");
    expect(uniqueStoryboardFrameId(chapter([other, target]), target)).toBe("stable-frame-id");
    expect(uniqueStoryboardFrameId(chapter([target, other]), target)).toBe("stable-frame-id");
    expect(uniqueStoryboardFrameId(chapter([target, storyboardFrame("stable-frame-id")]), target)).toBeNull();
    expect(uniqueStoryboardFrameId(chapter([storyboardFrame(null)]), storyboardFrame(null))).toBeNull();
    expect(uniqueStoryboardFrameId(chapter(null), target)).toBeNull();
  });
});
