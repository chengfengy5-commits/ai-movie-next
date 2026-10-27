import { describe, expect, it } from "vitest";
import type { Chapter, Character, StoryboardAsset } from "../../shared/api/contracts";
import type { StorageLike } from "../../shared/api/storage";
import {
  chatContextPreferenceKey,
  preferenceForChatTarget,
  readChatContextPreference,
  resolveChatContextPreference,
  writeChatContextPreference,
} from "./chatContextPreferences";

const scope = { apiBaseUrl: "https://api.example.test/api", userId: "actor-1059", seriesId: "series-1059" };

const chapter: Chapter = {
  id: "chapter-1059",
  series_id: scope.seriesId,
  title: "第 1 集",
  content: [
    { description: "第一镜", storyboard: ["board-first"] },
    { description: "第二镜", storyboard: ["board-target"] },
  ],
  order: 1,
  created_at: "2026-10-27T00:00:00Z",
  updated_at: "2026-10-27T00:00:00Z",
  lock: null,
};

function storyboard(id: string, frameIndex: number, chapterId = chapter.id): StoryboardAsset {
  return {
    id,
    series_id: scope.seriesId,
    chapter_id: chapterId,
    frame_index: frameIndex,
    name: id,
    description: null,
    image_url: null,
    created_at: "2026-10-27T00:00:00Z",
    updated_at: "2026-10-27T00:00:00Z",
  };
}

class MemoryStorage implements StorageLike {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

describe("chat context preferences", () => {
  it("isolates preferences by actor, API and series while storing only the selected target identity", () => {
    const storage = new MemoryStorage();
    const preference = { kind: "frame", chapterId: chapter.id, storyboardAssetId: "board-target" } as const;

    expect(writeChatContextPreference(scope, preference, storage)).toBe("saved");
    expect(readChatContextPreference(scope, storage)).toMatchObject({ status: "ready", preference, malformed: false });
    expect(JSON.parse(storage.values.get(chatContextPreferenceKey(scope))!)).toEqual(preference);
    expect(readChatContextPreference({ ...scope, userId: "another-actor" }, storage)).toMatchObject({ preference: null });
    expect(readChatContextPreference({ ...scope, apiBaseUrl: "https://other.example.test/api" }, storage))
      .toMatchObject({ preference: null });
    expect(readChatContextPreference({ ...scope, seriesId: "another-series" }, storage)).toMatchObject({ preference: null });
  });

  it("recomputes a saved frame index from its stable ID after chapter content is reordered", () => {
    const reorderedChapter: Chapter = {
      ...chapter,
      content: [
        { description: "新排第一", storyboard: ["board-first"] },
        { description: "目标镜头移到第二", storyboard: ["board-target"] },
      ],
    };

    expect(resolveChatContextPreference(
      { kind: "frame", chapterId: chapter.id, storyboardAssetId: "board-target" },
      [reorderedChapter],
      scope.seriesId,
      [storyboard("board-first", 0), storyboard("board-target", 1)],
      [],
      [],
      [],
    )).toMatchObject({ status: "resolved", targetKey: "frame:1" });
  });

  it("saves only the stable storyboard identity for a selected frame", () => {
    expect(preferenceForChatTarget(
      { kind: "frame", chapterId: chapter.id, frameIndex: 1 },
      chapter,
      scope.seriesId,
      [storyboard("board-first", 0), storyboard("board-target", 1)],
      [],
      [],
      [],
    )).toEqual({
      status: "resolved",
      targetKey: "frame:1",
      preference: { kind: "frame", chapterId: chapter.id, storyboardAssetId: "board-target" },
    });
  });

  it("rejects frames without a stable ID or with duplicate content and directory matches", () => {
    const noIdChapter: Chapter = { ...chapter, content: [{ description: "无稳定 ID" }] };
    const duplicateChapter: Chapter = {
      ...chapter,
      content: [
        { description: "重复引用 1", storyboard: ["board-target"] },
        { description: "重复引用 2", storyboard: ["board-target"] },
      ],
    };
    const preference = { kind: "frame", chapterId: chapter.id, storyboardAssetId: "board-target" } as const;

    expect(resolveChatContextPreference(preference, [noIdChapter], scope.seriesId, [], [], [], []))
      .toMatchObject({ status: "invalid", reason: "frame-target-missing" });
    expect(preferenceForChatTarget({ kind: "frame", chapterId: noIdChapter.id, frameIndex: 0 },
      noIdChapter, scope.seriesId, [], [], [], []))
      .toMatchObject({ status: "invalid", reason: "frame-id-missing" });
    expect(resolveChatContextPreference(preference, [duplicateChapter], scope.seriesId,
      [storyboard("board-target", 0)], [], [], []))
      .toMatchObject({ status: "invalid", reason: "frame-id-ambiguous" });
    expect(resolveChatContextPreference(preference, [chapter], scope.seriesId, [], [], [], []))
      .toMatchObject({ status: "invalid", reason: "storyboard-missing" });
    expect(resolveChatContextPreference(preference, [chapter], scope.seriesId,
      [storyboard("board-target", 1), storyboard("board-target", 1)], [], [], []))
      .toMatchObject({ status: "invalid", reason: "storyboard-ambiguous" });
  });

  it("validates asset identity against the selected series and asset type", () => {
    const character: Character = {
      id: "character-1059",
      series_id: scope.seriesId,
      description: null,
      image_url: null,
      created_at: "2026-10-27T00:00:00Z",
      updated_at: "2026-10-27T00:00:00Z",
      aliases: null,
      canonical_key: null,
      name: "主角",
      gender: null,
      age: null,
      role: null,
      appearance: null,
      audio_url: null,
      voice_ref: null,
    };
    const preference = { kind: "asset", chapterId: chapter.id, assetType: "character", assetId: character.id } as const;
    const wrongTypePreference = { ...preference, assetType: "scene" } as const;

    expect(resolveChatContextPreference(preference, [chapter], scope.seriesId, [], [character], [], []))
      .toMatchObject({ status: "resolved", targetKey: `asset:character:${character.id}` });
    expect(resolveChatContextPreference(wrongTypePreference, [chapter], scope.seriesId, [], [character], [], []))
      .toMatchObject({ status: "invalid", reason: "asset-missing" });
    expect(resolveChatContextPreference(preference, [chapter], scope.seriesId, [],
      [{ ...character, series_id: "another-series" }], [], []))
      .toMatchObject({ status: "invalid", reason: "asset-missing" });
  });

  it("reports storage read and write failures without throwing", () => {
    const unavailable: StorageLike = {
      getItem: () => { throw new Error("storage unavailable"); },
      setItem: () => { throw new Error("storage unavailable"); },
      removeItem: () => { throw new Error("storage unavailable"); },
    };

    expect(readChatContextPreference(scope, unavailable)).toMatchObject({ status: "unavailable", reason: "storage" });
    expect(writeChatContextPreference(scope, { kind: "chapter", chapterId: chapter.id }, unavailable)).toBe("unavailable");
  });

  it("reports a failing browser storage getter without throwing", () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
    try {
      Object.defineProperty(window, "localStorage", {
        configurable: true,
        get: () => { throw new Error("storage getter unavailable"); },
      });
      expect(readChatContextPreference(scope)).toMatchObject({ status: "unavailable", reason: "storage" });
      expect(writeChatContextPreference(scope, { kind: "chapter", chapterId: chapter.id })).toBe("unavailable");
    } finally {
      if (descriptor !== undefined) Object.defineProperty(window, "localStorage", descriptor);
    }
  });
});
