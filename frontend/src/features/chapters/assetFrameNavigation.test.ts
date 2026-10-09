import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import {
  findChapterFramePosition,
  findUniqueChapterStoryboardAsset,
  type ChapterFrameNavigationTarget,
} from "./assetFrameNavigation";

function chapter(content: unknown[], overrides: Partial<Chapter> = {}): Chapter {
  return {
    id: "chapter-target",
    series_id: "series-1",
    title: "目标章节",
    content: content as Chapter["content"],
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    lock: null,
    ...overrides,
  };
}

function asset(overrides: Partial<StoryboardAsset> = {}): StoryboardAsset {
  return {
    id: "storyboard-shot-2",
    series_id: "series-1",
    chapter_id: "chapter-target",
    frame_index: 0,
    name: "响应镜头",
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

const target: ChapterFrameNavigationTarget = {
  storyboardAssetId: "storyboard-shot-2",
  category: "characters",
  assetId: "character-shared",
};

describe("asset frame navigation identity", () => {
  it("uses storyboard[0] and the live category reference as separate identities", () => {
    const snapshot = chapter([
      { storyboard: ["storyboard-shot-1"], character: ["other-character"] },
      { storyboard: ["storyboard-shot-2", "ignored"], character: ["character-shared", "character-shared"] },
    ]);
    expect(findChapterFramePosition(snapshot, target)).toBe(2);
    expect(findUniqueChapterStoryboardAsset([asset()], "series-1", "chapter-target", target)?.id)
      .toBe("storyboard-shot-2");
    expect(findUniqueChapterStoryboardAsset([
      asset({ id: "character-shared" }),
    ], "series-1", "chapter-target", target)).toBeNull();
  });

  it("rejects duplicate first storyboard IDs, missing references, and foreign chapter identity", () => {
    expect(findChapterFramePosition(chapter([
      { storyboard: ["storyboard-shot-2"], character: ["character-shared"] },
      { storyboard: ["storyboard-shot-2"], character: ["character-shared"] },
    ]), target)).toBeNull();
    expect(findChapterFramePosition(chapter([
      { storyboard: ["storyboard-shot-2"], character: ["different-character"] },
    ]), target)).toBeNull();
    expect(findUniqueChapterStoryboardAsset([asset({ chapter_id: "foreign-chapter" })], "series-1", "chapter-target", target))
      .toBeNull();
  });

  it("rejects duplicate response IDs and does not use frame_index as a fallback", () => {
    expect(findUniqueChapterStoryboardAsset([asset(), asset()], "series-1", "chapter-target", target)).toBeNull();
    expect(findUniqueChapterStoryboardAsset([asset({ id: "other-id", frame_index: 1 })], "series-1", "chapter-target", target))
      .toBeNull();
  });
});
