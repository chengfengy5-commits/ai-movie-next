import { describe, expect, it } from "vitest";
import {
  clampStoryboardPage,
  readStoryboardBrowsePreferences,
  storyboardChapterStorageKey,
  storyboardPageCount,
  storyboardPageForPosition,
  storyboardPageSizeStorageKey,
  writeStoryboardBrowsePreferences,
  type StoryboardBrowsePreferenceScope,
  type StoryboardBrowseStorage,
} from "./storyboardBrowsePreferences";

const scope: StoryboardBrowsePreferenceScope = {
  userId: "user-a",
  apiBaseUrl: "https://api.example.test/api",
  seriesId: "series-a",
  chapterId: "chapter-a",
};

function memoryStorage(seed: Record<string, string> = {}): StoryboardBrowseStorage {
  const values = new Map(Object.entries(seed));
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

describe("storyboard browse preferences", () => {
  it("scopes page size to the actor/API and page plus scroll to the chapter", () => {
    const storage = memoryStorage();
    expect(writeStoryboardBrowsePreferences(scope, { pageSize: 50, page: 4, scrollTop: 831 }, () => storage)).toBe(true);
    expect(readStoryboardBrowsePreferences(scope, () => storage)).toEqual({
      available: true,
      preferences: { pageSize: 50, page: 4, scrollTop: 831 },
    });
    const otherChapterScope = {
      ...scope,
      seriesId: "series-b",
      chapterId: "chapter-b",
    };
    expect(storyboardPageSizeStorageKey(scope)).toBe(storyboardPageSizeStorageKey(otherChapterScope));
    expect(storyboardChapterStorageKey(scope)).not.toBe(storyboardChapterStorageKey({ ...scope, chapterId: "chapter-b" }));
    expect(storyboardChapterStorageKey(scope)).not.toBe(storyboardChapterStorageKey({ ...scope, userId: "user-b" }));
  });

  it("clamps stale page numbers and safely defaults when storage access fails", () => {
    const storage = memoryStorage({
      [storyboardPageSizeStorageKey(scope)]: "10",
      [storyboardChapterStorageKey(scope)]: JSON.stringify({ page: 99, scrollTop: -20 }),
    });
    const saved = readStoryboardBrowsePreferences(scope, () => storage);
    expect(saved).toEqual({ available: true, preferences: { pageSize: 10, page: 99, scrollTop: 0 } });
    expect(clampStoryboardPage(saved.preferences.page, storyboardPageCount(23, saved.preferences.pageSize))).toBe(3);
    expect(storyboardPageForPosition(21, 10, 3)).toBe(3);
    expect(storyboardPageForPosition(0, 10, 3)).toBeNull();

    const unavailable = readStoryboardBrowsePreferences(scope, () => { throw new Error("blocked"); });
    expect(unavailable).toEqual({ available: false, preferences: { pageSize: 20, page: 1, scrollTop: 0 } });
    expect(writeStoryboardBrowsePreferences(scope, { pageSize: 20, page: 1, scrollTop: 0 }, () => {
      throw new Error("blocked");
    })).toBe(false);
  });
});
