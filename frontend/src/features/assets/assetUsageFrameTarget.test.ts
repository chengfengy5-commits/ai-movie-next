import { describe, expect, it } from "vitest";
import type { Chapter } from "../../shared/api/contracts";
import { resolveAssetUsageFrameTarget } from "./assetUsageFrameTarget";

function chapter(id: string, seriesId: string, content: unknown[]): Chapter {
  return {
    id,
    series_id: seriesId,
    title: id,
    content: content as Chapter["content"],
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    lock: null,
  };
}

const validFrames = [
  { storyboard: ["shot-b", "ignored-second-id"], character: ["shared-character", "shared-character"] },
  { storyboard: ["shot-a"], character: ["other-character"] },
];

describe("resolveAssetUsageFrameTarget", () => {
  it("uses the matched frame's first storyboard ID and preserves repeated category references", () => {
    expect(resolveAssetUsageFrameTarget(
      [chapter("chapter-1", "series-1", validFrames)],
      "series-1",
      1,
      1,
      "characters",
      "shared-character",
    )).toEqual({
      chapterId: "chapter-1",
      seriesId: "series-1",
      storyboardAssetId: "shot-b",
      category: "characters",
      assetId: "shared-character",
      framePosition: 1,
    });
  });

  it("rejects duplicate raw chapter IDs before filtering foreign chapters", () => {
    expect(resolveAssetUsageFrameTarget(
      [
        chapter("chapter-1", "series-1", validFrames),
        chapter("chapter-1", "series-foreign", []),
      ],
      "series-1",
      1,
      1,
      "characters",
      "shared-character",
    )).toBeNull();
  });

  it("rejects a storyboard[0] ID duplicated in another frame of the chapter", () => {
    expect(resolveAssetUsageFrameTarget(
      [chapter("chapter-1", "series-1", [
        ...validFrames,
        { storyboard: ["shot-b"], character: ["shared-character"] },
      ])],
      "series-1",
      1,
      1,
      "characters",
      "shared-character",
    )).toBeNull();
  });

  it("requires the exact current category reference without treating an alias as identity", () => {
    expect(resolveAssetUsageFrameTarget(
      [chapter("chapter-1", "series-1", validFrames)],
      "series-1",
      1,
      1,
      "characters",
      "alias-for-shared-character",
    )).toBeNull();
  });

  it("rejects empty storyboard IDs and invalid positions", () => {
    const snapshot = [chapter("chapter-1", "series-1", [
      { storyboard: [""], character: ["shared-character"] },
    ])];
    expect(resolveAssetUsageFrameTarget(snapshot, "series-1", 1, 1, "characters", "shared-character")).toBeNull();
    expect(resolveAssetUsageFrameTarget(snapshot, "series-1", 0, 1, "characters", "shared-character")).toBeNull();
    expect(resolveAssetUsageFrameTarget(snapshot, "series-1", 1, 0, "characters", "shared-character")).toBeNull();
  });
});
