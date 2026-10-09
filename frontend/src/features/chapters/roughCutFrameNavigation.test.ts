import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import type { PersonalRoughCutFrame, PersonalRoughCutSnapshot } from "../../shared/api/personalRoughCut";
import { findRoughCutFramePosition } from "./roughCutFrameNavigation";

const chapterId = "chapter-rough-cut-navigation";
const seriesId = "series-rough-cut-navigation";

function makeChapter(content: Chapter["content"] = [
  { storyboard: ["first-frame"], text: "First chapter frame" },
  { storyboard: ["target-frame", "other-storyboard-entry"], text: "Target chapter frame" },
]): Chapter {
  return {
    id: chapterId,
    series_id: seriesId,
    title: "Navigation chapter",
    content,
    order: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

function makeRow(overrides: Partial<PersonalRoughCutFrame> = {}): PersonalRoughCutFrame {
  return {
    asset_id: "target-frame",
    frame_index: 0,
    text: "Rough-cut row appears first, but belongs to chapter frame two.",
    preview_url: "https://remote.example.invalid/private-video.mp4",
    missing_reason: null,
    included: false,
    pending: true,
    ...overrides,
  };
}

function makeSnapshot(frames: PersonalRoughCutFrame[] = [makeRow()]): PersonalRoughCutSnapshot {
  return {
    chapter_id: chapterId,
    revision: 2,
    saved: true,
    frames,
    removed_asset_ids: [],
  };
}

function makeAsset(overrides: Partial<StoryboardAsset> = {}): StoryboardAsset {
  return {
    id: "target-frame",
    series_id: seriesId,
    chapter_id: chapterId,
    frame_index: 900,
    name: "Target asset",
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

describe("rough-cut frame navigation identity", () => {
  it("uses the unique first storyboard ID position, not rough order or frame_index", () => {
    const row = makeRow({ frame_index: 999, included: false, pending: true, preview_url: null });
    expect(findRoughCutFramePosition(makeChapter(), seriesId, makeSnapshot([row]), row, [makeAsset()]))
      .toBe(2);
  });

  it("requires the actual row and a non-empty ID unique in the rough snapshot", () => {
    const row = makeRow();
    const duplicate = makeRow({ text: "duplicate" });
    expect(findRoughCutFramePosition(makeChapter(), seriesId, makeSnapshot([row, duplicate]), row, [makeAsset()]))
      .toBeNull();
    expect(findRoughCutFramePosition(makeChapter(), seriesId, makeSnapshot([row]), makeRow(), [makeAsset()]))
      .toBeNull();
    const empty = makeRow({ asset_id: "   " });
    expect(findRoughCutFramePosition(makeChapter(), seriesId, makeSnapshot([empty]), empty, [makeAsset()]))
      .toBeNull();
  });

  it("rejects a missing, duplicate, or non-first chapter identity", () => {
    const row = makeRow();
    expect(findRoughCutFramePosition(makeChapter([{ storyboard: ["other", "target-frame"] }]), seriesId, makeSnapshot([row]), row, [makeAsset()]))
      .toBeNull();
    expect(findRoughCutFramePosition(makeChapter([{ storyboard: ["target-frame"] }, { storyboard: ["target-frame"] }]), seriesId, makeSnapshot([row]), row, [makeAsset()]))
      .toBeNull();
    expect(findRoughCutFramePosition(makeChapter(null), seriesId, makeSnapshot([row]), row, [makeAsset()]))
      .toBeNull();
  });

  it("counts every returned asset before checking its ownership", () => {
    const row = makeRow();
    expect(findRoughCutFramePosition(makeChapter(), seriesId, makeSnapshot([row]), row, [
      makeAsset(),
      makeAsset({ series_id: "foreign-series" }),
    ])).toBeNull();
    expect(findRoughCutFramePosition(makeChapter(), seriesId, makeSnapshot([row]), row, [
      makeAsset({ chapter_id: "foreign-chapter" }),
    ])).toBeNull();
    expect(findRoughCutFramePosition(makeChapter(), seriesId, makeSnapshot([row]), row, [])).toBeNull();
  });

  it("requires the exact snapshot chapter and series", () => {
    const row = makeRow();
    expect(findRoughCutFramePosition(makeChapter(), "other-series", makeSnapshot([row]), row, [makeAsset()]))
      .toBeNull();
    expect(findRoughCutFramePosition(makeChapter(), seriesId, makeSnapshot([row]), row, [makeAsset({ series_id: "other-series" })]))
      .toBeNull();
    expect(findRoughCutFramePosition(makeChapter(), seriesId, { ...makeSnapshot([row]), chapter_id: "other-chapter" }, row, [makeAsset()]))
      .toBeNull();
  });
});
