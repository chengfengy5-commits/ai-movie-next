import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import {
  addContinuousDraftFrame,
  buildGuardedChapterUpdate,
  createContinuousChapterDraft,
  continuousWriteReadbackMatches,
  deleteContinuousDraftFrame,
  mergeContinuousDraftFrames,
  moveContinuousDraftFrame,
  splitContinuousDraftFrame,
  updateContinuousDraftFrame,
} from "./continuousChapterEditing";

const chapter: Chapter = {
  id: "chapter-a",
  series_id: "series-a",
  title: "第一章",
  content: [
    {
      text: "甲段  ",
      storyboard: ["shot-a"],
      original_text: "甲原文",
      preview: null,
      unknown: { values: [1, true, null] },
      character: ["character-a"],
      scene: [],
      prop: [],
    },
    { text: "乙段", storyboard: ["shot-b"], character: [], scene: [], prop: [] },
  ],
  order: 2,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
  lock: null,
};

const assets: StoryboardAsset[] = [
  {
    id: "shot-a", series_id: "series-a", chapter_id: "chapter-a", frame_index: 0,
    name: "分镜 1", description: "甲段", image_url: null,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "shot-b", series_id: "series-a", chapter_id: "chapter-a", frame_index: 1,
    name: "分镜 2", description: "乙段", image_url: null,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  },
];

function draft() {
  return createContinuousChapterDraft(chapter, assets, null);
}

describe("continuous chapter editing source map", () => {
  it("keeps untouched frame fields, trims no text, and emits the explicit guard", () => {
    const snapshot = draft();
    const update = buildGuardedChapterUpdate(snapshot, snapshot.frames);

    expect(update.content?.[0]).toMatchObject({
      text: "甲段  ",
      original_text: "甲原文",
      preview: null,
      unknown: { values: [1, true, null] },
      storyboard: ["shot-a"],
    });
    expect(update.chapter_edit_guard?.frame_sources).toEqual([
      { kind: "retained", source_index: 0, storyboard_id: "shot-a" },
      { kind: "retained", source_index: 1, storyboard_id: "shot-b" },
    ]);
    expect(update.chapter_edit_guard?.expected_storyboards).toEqual(assets.map(({ id, series_id, chapter_id, frame_index }) => ({
      id, series_id, chapter_id, frame_index,
    })));
    expect(update.chapter_edit_guard?.deleted_source_indexes).toEqual([]);
  });

  it("keeps the retained source through reorder and maps a deleted source explicitly", () => {
    const snapshot = draft();
    const updated = buildGuardedChapterUpdate(
      snapshot,
      [snapshot.frames[1]!, snapshot.frames[0]!],
    );
    expect(updated.chapter_edit_guard?.frame_sources).toEqual([
      { kind: "retained", source_index: 1, storyboard_id: "shot-b" },
      { kind: "retained", source_index: 0, storyboard_id: "shot-a" },
    ]);

    const afterDelete = deleteContinuousDraftFrame(snapshot.frames, 0);
    expect(buildGuardedChapterUpdate(snapshot, afterDelete).chapter_edit_guard?.deleted_source_indexes).toEqual([0]);
  });

  it("creates a new block without inheriting original or unknown fields", () => {
    const snapshot = draft();
    const frames = addContinuousDraftFrame(snapshot.frames, 0, "draft-new");
    frames[1] = updateContinuousDraftFrame(frames, 1, { text: "新镜头" })[1]!;
    const update = buildGuardedChapterUpdate(snapshot, frames);
    expect(update.content?.[1]).toEqual({
      text: "新镜头", character: [], scene: [], prop: [], storyboard: [], preview: null,
    });
    expect(update.chapter_edit_guard?.frame_sources[1]).toEqual({ kind: "new" });
  });

  it("makes the split source choice explicit and lets merge retain either source", () => {
    const snapshot = draft();
    const split = splitContinuousDraftFrame(snapshot.frames, 0, 1, "draft-split");
    expect(split[0]?.sourceIndex).toBe(0);
    expect(split[1]?.sourceIndex).toBeNull();

    const keepLeft = mergeContinuousDraftFrames(split, 0, "left");
    expect(keepLeft[0]?.sourceIndex).toBe(0);
    expect(keepLeft[0]?.text).toBe("甲\n\n段  ");

    const keepRight = mergeContinuousDraftFrames(split, 0, "right");
    expect(keepRight[0]?.sourceIndex).toBeNull();
    expect(buildGuardedChapterUpdate(snapshot, keepRight).chapter_edit_guard?.deleted_source_indexes).toEqual([0]);
  });

  it("allows the old workflow's explicit delete-all result", () => {
    const snapshot = draft();
    const update = buildGuardedChapterUpdate(snapshot, []);
    expect(update.content).toEqual([]);
    expect(update.chapter_edit_guard?.frame_sources).toEqual([]);
    expect(update.chapter_edit_guard?.deleted_source_indexes).toEqual([0, 1]);
  });

  it("moves blocks without changing their source identities", () => {
    const snapshot = draft();
    const moved = moveContinuousDraftFrame(snapshot.frames, 0, 1);
    expect(moved.map((frame) => frame.sourceIndex)).toEqual([1, 0]);
  });

  it("confirms a guarded write only after every readback frame has one matching asset row", () => {
    const snapshot = draft();
    const saved = buildGuardedChapterUpdate(snapshot, addContinuousDraftFrame(snapshot.frames, 1, "new-frame"));
    const readbackChapter: Chapter = {
      ...chapter,
      content: (saved.content ?? []).map((frame, index) => index < 2
        ? frame
        : { ...frame, storyboard: ["shot-new"] }),
    };
    const readbackAssets = [...assets, {
      ...assets[0]!, id: "shot-new", frame_index: 2,
    }];
    expect(continuousWriteReadbackMatches(saved.content ?? [], readbackChapter, readbackAssets, chapter.series_id)).toBe(true);
    expect(continuousWriteReadbackMatches(saved.content ?? [], readbackChapter, readbackAssets.slice(0, 2), chapter.series_id)).toBe(false);
  });
});
