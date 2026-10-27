import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import type { FusedGenerationTargetedOpenIntent, FusedGenerationTargetProof } from "./fusedGenerationEntryIntent";
import { resolveFusedGenerationTarget } from "./fusedGenerationEntryIntent";

function chapter(content: Chapter["content"]): Chapter {
  return {
    id: "chapter-1",
    series_id: "series-1",
    title: "第一场",
    content,
    order: 0,
    created_at: "2026-10-28T00:00:00Z",
    updated_at: "2026-10-28T00:00:00Z",
    lock: null,
  };
}

function storyboard(id: string, frameIndex: number): StoryboardAsset {
  return {
    id,
    series_id: "series-1",
    chapter_id: "chapter-1",
    frame_index: frameIndex,
    name: id,
    description: null,
    image_url: null,
    created_at: "2026-10-28T00:00:00Z",
    updated_at: "2026-10-28T00:00:00Z",
  };
}

function intent(target: FusedGenerationTargetProof): FusedGenerationTargetedOpenIntent {
  return {
    id: "entry-1",
    epoch: 1,
    userId: "actor-1",
    apiBaseUrl: "https://api.example.test",
    seriesId: "series-1",
    sessionGeneration: 1,
    memberType: "character",
    memberAssetIds: ["character-1"],
    target,
  };
}

describe("fused generation entry target resolution", () => {
  it("follows a unique stable frame after reorder and ignores only fused attachments", () => {
    const expected = { storyboard: ["shot-a"], text: "A", extra: { values: [1, true] }, fused: [{ id: "old" }] };
    const actual = { ...expected, fused: [{ id: "new" }] };
    const currentChapter = chapter([
      { storyboard: ["shot-b"], text: "B" },
      actual,
    ]);

    const result = resolveFusedGenerationTarget(
      intent({ chapterId: "chapter-1", frameIndex: 0, storyboardAssetId: "shot-a", expectedFrame: expected }),
      currentChapter,
      [storyboard("shot-a", 1)],
    );

    expect(result).toEqual({
      status: "verified",
      target: {
        chapterId: "chapter-1",
        frameIndex: 1,
        storyboardAssetId: "shot-a",
        expectedFrame: expected,
      },
    });
  });

  it("rejects missing, malformed, repeated, and directory-mismatched stable relationships", () => {
    const expected = { storyboard: ["shot-a"], text: "A" };
    const proof = intent({ chapterId: "chapter-1", frameIndex: 0, storyboardAssetId: "shot-a", expectedFrame: expected });
    const cases = [
      chapter([{ text: "A" }]),
      chapter([{ storyboard: "shot-a", text: "A" } as never]),
      chapter([{ storyboard: ["shot-a", "shot-other"], text: "A" } as never]),
      chapter([expected, expected]),
    ];

    for (const current of cases) {
      expect(resolveFusedGenerationTarget(proof, current, [storyboard("shot-a", 0)])).toMatchObject({ status: "stale" });
    }
    expect(resolveFusedGenerationTarget(proof, chapter([expected]), [storyboard("shot-a", 1)])).toMatchObject({
      status: "stale",
    });
  });

  it("requires a full no-ID snapshot and unique non-fused frame identity", () => {
    const target = { text: "no stable id", props: ["key"], fused: [{ id: "old" }] };
    const actual = { ...target, fused: [{ id: "new" }] };
    const proof = {
      chapterId: "chapter-1",
      frameIndex: 0,
      expectedFrame: target,
      expectedContent: [target],
    };
    expect(resolveFusedGenerationTarget(intent(proof), chapter([actual]), [])).toMatchObject({
      status: "verified",
      target: { chapterId: "chapter-1", frameIndex: 0, expectedFrame: target, expectedContent: [target] },
    });
    expect(resolveFusedGenerationTarget(intent(proof), chapter([actual, actual]), [])).toMatchObject({
      status: "stale",
    });
    expect(resolveFusedGenerationTarget(intent(proof), chapter([{ ...actual, unknown: "changed" }]), [])).toMatchObject({
      status: "stale",
    });
    expect(resolveFusedGenerationTarget(intent(proof), chapter([{ ...actual, storyboard: ["has-id"] }]), [])).toMatchObject({
      status: "stale",
    });
  });
});
