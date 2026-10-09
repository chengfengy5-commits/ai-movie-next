import { describe, expect, it } from "vitest";
import type { Chapter, StoryboardAsset } from "../../../shared/api/contracts";
import {
  parsePersonalRoughCutSaveResponse,
  parsePersonalRoughCutUpdate,
  type PersonalRoughCutSnapshot,
} from "../../../shared/api/personalRoughCut";
import {
  createRoughCutDraft,
  decideRoughCutSave,
  inspectRoughCutSource,
  moveRoughCutFrame,
  setRoughCutIncluded,
} from "./roughCutEdit";

function chapter(ids: readonly string[] | null): Chapter {
  return {
    id: "chapter-edit",
    series_id: "series-edit",
    title: "粗剪编排测试",
    content: ids === null ? null : ids.map((id) => ({ storyboard: [id], text: `正文 ${id}` })),
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    lock: null,
  };
}

function asset(id: string, overrides: Partial<StoryboardAsset> = {}): StoryboardAsset {
  return {
    id,
    series_id: "series-edit",
    chapter_id: "chapter-edit",
    frame_index: 0,
    name: `素材 ${id}`,
    description: null,
    image_url: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    ...overrides,
  };
}

function snapshot(ids: readonly string[], overrides: Partial<PersonalRoughCutSnapshot> = {}): PersonalRoughCutSnapshot {
  return {
    chapter_id: "chapter-edit",
    revision: 4,
    saved: true,
    frames: ids.map((id, frame_index) => ({
      asset_id: id,
      frame_index,
      text: `正文 ${id}`,
      preview_url: null,
      missing_reason: "当前分镜没有可用视频",
      included: true,
      pending: false,
    })),
    removed_asset_ids: [],
    ...overrides,
  };
}

describe("rough-cut editing rules", () => {
  it("checks the complete raw source and counts duplicate directory IDs before ownership", () => {
    const currentChapter = chapter(["A", "B"]);
    const currentSnapshot = snapshot(["B", "A"], {
      frames: [
        { ...snapshot(["A", "B"]).frames[1]!, frame_index: 1 },
        { ...snapshot(["A", "B"]).frames[0]!, frame_index: 0 },
      ],
    });
    expect(inspectRoughCutSource(currentChapter, currentSnapshot, [asset("A"), asset("B")], "ready").valid).toBe(true);

    const duplicateAssetId = inspectRoughCutSource(
      currentChapter,
      currentSnapshot,
      [asset("A"), asset("B"), asset("B", { series_id: "another-series" })],
      "ready",
    );
    expect(duplicateAssetId.valid).toBe(false);

    expect(inspectRoughCutSource(chapter(["A", "A"]), snapshot(["A"]), [asset("A")], "ready").valid)
      .toBe(false);
    expect(inspectRoughCutSource(chapter(["", "B"]), snapshot(["B"]), [asset("B")], "ready").valid)
      .toBe(false);
  });

  it("allows a valid empty chapter without an asset snapshot and keeps raw whitespace IDs unchanged", () => {
    expect(inspectRoughCutSource(chapter(null), snapshot([]), null, "loading").valid).toBe(true);
    const whitespaceChapter = chapter(["  "]);
    const whitespaceSnapshot = snapshot(["  "]);
    expect(inspectRoughCutSource(whitespaceChapter, whitespaceSnapshot, [asset("  ")], "ready").valid)
      .toBe(true);
    const unsavedWhitespaceSnapshot = { ...whitespaceSnapshot, revision: 0, saved: false };
    const decision = decideRoughCutSave(unsavedWhitespaceSnapshot, createRoughCutDraft(unsavedWhitespaceSnapshot),
      inspectRoughCutSource(whitespaceChapter, whitespaceSnapshot, [asset("  ")], "ready"));
    expect(decision.kind).toBe("save");
    if (decision.kind === "save") {
      expect(decision.update.frames[0]?.asset_id).toBe("  ");
    }
  });

  it("swaps only the same included group and toggles inclusion without moving its index", () => {
    const original = [
      { asset_id: "A", included: true },
      { asset_id: "B", included: false },
      { asset_id: "C", included: true },
      { asset_id: "D", included: false },
    ];
    expect(moveRoughCutFrame(original, 2, -1).map((frame) => frame.asset_id)).toEqual(["C", "B", "A", "D"]);
    expect(moveRoughCutFrame(original, 0, 1).map((frame) => frame.asset_id)).toEqual(["C", "B", "A", "D"]);
    const changed = setRoughCutIncluded(original, 0, false);
    expect(changed.map((frame) => frame.asset_id)).toEqual(["A", "B", "C", "D"]);
    expect(changed[0]?.included).toBe(false);
  });

  it("requires explicit full saves for initial, pending, or removed projections but avoids unchanged repeats", () => {
    const saved = snapshot(["A", "B"]);
    const source = inspectRoughCutSource(chapter(["A", "B"]), saved, [asset("A"), asset("B")], "ready");
    expect(decideRoughCutSave(saved, createRoughCutDraft(saved), source)).toEqual({ kind: "unchanged" });

    const pending = snapshot(["A", "B"], {
      frames: snapshot(["A", "B"]).frames.map((frame, index) => ({
        ...frame,
        pending: index === 1,
      })),
      removed_asset_ids: ["retired"],
    });
    const decision = decideRoughCutSave(pending, createRoughCutDraft(pending),
      inspectRoughCutSource(chapter(["A", "B"]), pending, [asset("A"), asset("B")], "ready"));
    expect(decision).toEqual({
      kind: "save",
      update: { expected_revision: 4, frames: [{ asset_id: "A", included: true }, { asset_id: "B", included: true }] },
    });

    const initial = snapshot(["A"], { revision: 0, saved: false });
    expect(decideRoughCutSave(initial, createRoughCutDraft(initial),
      inspectRoughCutSource(chapter(["A"]), initial, [asset("A")], "ready")).kind).toBe("save");
  });

  it("parses a strict complete update including code-point limits and rejects sparse entries", () => {
    expect(parsePersonalRoughCutUpdate({
      expected_revision: 0,
      frames: [{ asset_id: "😀".repeat(36), included: false }],
    })).toEqual({ expected_revision: 0, frames: [{ asset_id: "😀".repeat(36), included: false }] });
    expect(() => parsePersonalRoughCutUpdate({
      expected_revision: 0,
      frames: [{ asset_id: "😀".repeat(37), included: false }],
    })).toThrow();

    const sparse: unknown[] = new Array(1);
    expect(() => parsePersonalRoughCutUpdate({ expected_revision: 0, frames: sparse })).toThrow();
  });

  it("accepts source-maintained fields but rejects a different saved order or inclusion state", () => {
    const update = { expected_revision: 4, frames: [{ asset_id: "A", included: false }] };
    const maintained = {
      chapter_id: "chapter-edit",
      revision: 5,
      saved: true,
      frames: [{
        asset_id: "A",
        frame_index: 0,
        text: "维护后的正文",
        preview_url: null,
        missing_reason: "源视频已移除",
        included: false,
        pending: false,
      }],
      removed_asset_ids: [],
    };
    expect(parsePersonalRoughCutSaveResponse(maintained, "chapter-edit", update).frames[0]?.text)
      .toBe("维护后的正文");
    expect(() => parsePersonalRoughCutSaveResponse({
      ...maintained,
      frames: [{ ...maintained.frames[0], frame_index: 1 }],
    }, "chapter-edit", update)).toThrow();
    expect(() => parsePersonalRoughCutSaveResponse({
      ...maintained,
      frames: [{ ...maintained.frames[0], asset_id: "B" }],
    }, "chapter-edit", update)).toThrow();
    expect(() => parsePersonalRoughCutSaveResponse({
      ...maintained,
      frames: [{ ...maintained.frames[0], pending: true }],
    }, "chapter-edit", update)).toThrow();
  });
});
