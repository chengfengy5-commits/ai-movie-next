import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset, StoryboardFrame } from "../../../shared/api/contracts";
import { parsePersonalProductionSnapshot } from "../../../shared/api/personalProductionNotes";
import type { PersonalRoughCutSnapshot } from "../../../shared/api/personalRoughCut";
import {
  buildInsertedFrameContent,
  areFrameStructureMappingsValid,
  isFrameStructureReadbackValid,
  resolveDeletableFrameTarget,
} from "./contract";

function makeChapter(content: StoryboardFrame[]): Chapter {
  return {
    id: "chapter-1",
    series_id: "series-1",
    title: "第一章",
    content,
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

function makeAsset(id: string, frameIndex: number, changes: Partial<StoryboardAsset> = {}): StoryboardAsset {
  return {
    id,
    series_id: "series-1",
    chapter_id: "chapter-1",
    frame_index: frameIndex,
    name: `分镜 ${frameIndex + 1}`,
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...changes,
  };
}

describe("frame structure contract", () => {
  it("inserts an opening frame and preserves every existing field", () => {
    const opening: StoryboardFrame = {
      text: "原镜头",
      original_text: "保留原文",
      preview: "preview://original",
      fused: { members: ["asset-a"] },
      storyboard: ["asset-a"],
      future_field: { keep: true },
    };
    const draft = {
      text: "新开场",
      characterAssetIds: ["character-a"],
      sceneAssetIds: ["scene-a"],
      propAssetIds: ["prop-a"],
    };

    const result = buildInsertedFrameContent([opening], -1, draft);

    expect(result).toEqual([
      {
        text: "新开场",
        character: ["character-a"],
        scene: ["scene-a"],
        prop: ["prop-a"],
        storyboard: [],
        preview: null,
      },
      opening,
    ]);
    expect(result?.[1]).toBe(opening);
    expect(draft.characterAssetIds).toEqual(["character-a"]);
  });

  it("inserts after only an in-range position and accepts an empty chapter at the start", () => {
    const frames = [{ text: "一", storyboard: ["asset-a"] }, { text: "二", storyboard: ["asset-b"] }];
    const draft = { text: "插入", characterAssetIds: [], sceneAssetIds: [], propAssetIds: [] };

    expect(buildInsertedFrameContent(frames, 0, draft)?.map((frame) => frame.text)).toEqual(["一", "插入", "二"]);
    expect(buildInsertedFrameContent([], -1, draft)).toHaveLength(1);
    expect(buildInsertedFrameContent(frames, -2, draft)).toBeNull();
    expect(buildInsertedFrameContent(frames, 2, draft)).toBeNull();
  });

  it("resolves a delete target only through one current stable ID and same-chapter asset", () => {
    const chapter = makeChapter([
      { text: "一", storyboard: ["asset-a"] },
      { text: "二", storyboard: ["asset-b"] },
    ]);

    expect(resolveDeletableFrameTarget(chapter, 1, [makeAsset("asset-a", 0), makeAsset("asset-b", 1)]))
      .toEqual({ frameIndex: 1, storyboardAssetId: "asset-b" });
    expect(resolveDeletableFrameTarget(
      makeChapter([{ text: "一", storyboard: ["asset-a"] }, { text: "二", storyboard: ["asset-a"] }]),
      1,
      [makeAsset("asset-a", 1)],
    )).toBeNull();
    expect(resolveDeletableFrameTarget(chapter, 1, [
      makeAsset("asset-a", 0),
      makeAsset("asset-b", 1),
      makeAsset("asset-b", 1),
    ])).toBeNull();
    expect(resolveDeletableFrameTarget(chapter, 1, [
      makeAsset("asset-a", 0),
      makeAsset("asset-b", 1, { chapter_id: "chapter-other" }),
    ])).toBeNull();
    expect(resolveDeletableFrameTarget(chapter, 1, [makeAsset("asset-a", 0), makeAsset("asset-b", 0)])).toBeNull();
  });

  it("confirms generated identities while retaining full neighboring frame data", () => {
    const expected = [
      {
        text: "前镜",
        original_text: "前镜原文",
        preview: "preview://first",
        fused: { member_ids: ["asset-a"] },
        storyboard: ["asset-a"],
        future_data: { preserve: [1, 2] },
      },
      {
        text: "插入镜头",
        character: ["character-a"],
        scene: [],
        prop: ["prop-a"],
        storyboard: [],
        preview: null,
      },
      { text: "后镜", original_text: "后镜原文", storyboard: ["asset-b"], fused: false },
    ];
    const actual = [
      expected[0]!,
      { ...expected[1]!, storyboard: ["asset-new"] },
      expected[2]!,
    ];
    const assets = [makeAsset("asset-a", 0), makeAsset("asset-new", 1), makeAsset("asset-b", 2)];

    expect(isFrameStructureReadbackValid(expected, actual, assets, "series-1", "chapter-1")).toBe(true);
    expect(isFrameStructureReadbackValid(expected, actual, [
      makeAsset("asset-a", 0), makeAsset("asset-new", 1), makeAsset("asset-b", 1),
    ], "series-1", "chapter-1")).toBe(false);
    expect(isFrameStructureReadbackValid(expected, [
      actual[0]!, actual[1]!, { ...actual[2]!, fused: true },
    ], assets, "series-1", "chapter-1")).toBe(false);
  });

  it("checks current notes and rough-cut mappings by stable ID without depending on approval state", () => {
    const chapter = makeChapter([
      { text: "前镜", storyboard: ["asset-a"] },
      { text: "新镜", storyboard: ["asset-new"] },
      { text: "后镜", storyboard: ["asset-b"] },
    ]);
    const notes = parsePersonalProductionSnapshot({
      chapter_id: chapter.id,
      revision: 3,
      media_state: "ready",
      frames: [
        { frame_index: 0, storyboard_asset_id: "asset-a", media_revision: 2, source_valid: true,
          asset_image_digest: null, preview_digest: null, invalid_reason: null },
        { frame_index: 1, storyboard_asset_id: "asset-new", media_revision: 1, source_valid: true,
          asset_image_digest: null, preview_digest: null, invalid_reason: null },
        { frame_index: 2, storyboard_asset_id: "asset-b", media_revision: 1, source_valid: true,
          asset_image_digest: null, preview_digest: null, invalid_reason: null },
      ],
      frame_notes: { "legacy-history-id": { status: "needs_revision", note: "保留历史记录" } },
      resume_frame_id: "asset-a",
    }, chapter.id);
    const roughCut: PersonalRoughCutSnapshot = {
      chapter_id: chapter.id,
      revision: 2,
      saved: true,
      frames: [
        { asset_id: "asset-a", frame_index: 0, text: "前镜", preview_url: null, missing_reason: null,
          included: true, pending: false },
        { asset_id: "asset-b", frame_index: 2, text: "后镜", preview_url: null, missing_reason: null,
          included: false, pending: false },
        { asset_id: "asset-new", frame_index: 1, text: "新镜", preview_url: null, missing_reason: null,
          included: false, pending: true },
      ],
      removed_asset_ids: [],
    };

    expect(areFrameStructureMappingsValid(chapter, notes, roughCut)).toBe(true);
    expect(areFrameStructureMappingsValid(chapter, notes, {
      ...roughCut,
      frames: roughCut.frames.map((frame) => frame.asset_id === "asset-new"
        ? { ...frame, frame_index: 0 }
        : frame),
    })).toBe(false);
  });

  it("allows historical notes and resume IDs for deleted frames while validating current mappings", () => {
    const chapter = makeChapter([
      { text: "保留镜头", storyboard: ["asset-a"] },
      { text: "后续镜头", storyboard: ["asset-c"] },
    ]);
    const notes = parsePersonalProductionSnapshot({
      chapter_id: chapter.id,
      revision: 7,
      media_state: "ready",
      frames: [
        { frame_index: 0, storyboard_asset_id: "asset-a", media_revision: 2, source_valid: true,
          asset_image_digest: null, preview_digest: null, invalid_reason: null },
        { frame_index: 1, storyboard_asset_id: "asset-c", media_revision: 1, source_valid: true,
          asset_image_digest: null, preview_digest: null, invalid_reason: null },
      ],
      frame_notes: {
        "asset-deleted": { status: "unmarked", needs_reconfirmation: true },
        "asset-a": { status: "approved" },
      },
      resume_frame_id: "asset-deleted",
    }, chapter.id);
    const roughCut: PersonalRoughCutSnapshot = {
      chapter_id: chapter.id,
      revision: 3,
      saved: true,
      frames: [
        { asset_id: "asset-c", frame_index: 1, text: "后续镜头", preview_url: null, missing_reason: null,
          included: true, pending: false },
        { asset_id: "asset-a", frame_index: 0, text: "保留镜头", preview_url: null, missing_reason: null,
          included: false, pending: false },
      ],
      removed_asset_ids: ["asset-deleted"],
    };

    expect(areFrameStructureMappingsValid(chapter, notes, roughCut)).toBe(true);
    expect(areFrameStructureMappingsValid(chapter, {
      ...notes,
      frames: notes.frames.map((frame, index) => ({ ...frame, frame_index: index + 1 })),
    }, roughCut)).toBe(false);
    expect(areFrameStructureMappingsValid(chapter, notes, {
      ...roughCut,
      frames: roughCut.frames.filter((frame) => frame.asset_id !== "asset-c"),
    })).toBe(false);
  });

  it("rejects a delete readback that retained or reordered a stable frame identity", () => {
    const expected = [
      { text: "相邻镜头", storyboard: ["asset-a"], preview: "preview://keep" },
      { text: "后续镜头", storyboard: ["asset-c"], original_text: "原文" },
    ];
    const assets = [makeAsset("asset-a", 0), makeAsset("asset-c", 1)];

    expect(isFrameStructureReadbackValid(expected, expected, assets, "series-1", "chapter-1")).toBe(true);
    expect(isFrameStructureReadbackValid(
      expected,
      [{ ...expected[1]!, storyboard: ["asset-c"] }, expected[0]!],
      [makeAsset("asset-a", 1), makeAsset("asset-c", 0)],
      "series-1",
      "chapter-1",
    )).toBe(false);
  });
});
