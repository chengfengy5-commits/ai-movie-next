import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import { parsePersonalRoughCutSnapshot } from "./personalRoughCut";

const chapterId = "chapter-rough-cut";

function frame(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    asset_id: "asset-a",
    frame_index: 0,
    text: "当前镜头文字",
    preview_url: null,
    missing_reason: "当前没有可用视频",
    included: true,
    pending: false,
    ...overrides,
  };
}

function snapshot(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    chapter_id: chapterId,
    revision: 1,
    saved: true,
    frames: [frame()],
    removed_asset_ids: [],
    ...overrides,
  };
}

describe("personal rough-cut contract", () => {
  it("preserves wire order, empty identities, duplicate removed references and lists larger than the save limit", () => {
    const frames = Array.from({ length: 501 }, (_, index) => frame({
      asset_id: index < 2 ? "" : `asset-${index}`,
      frame_index: index,
      text: index === 0 ? "" : `镜头 ${index + 1}`,
      preview_url: index === 1 ? "https://media.example.invalid/clip.mp4" : null,
      missing_reason: index === 1 ? null : "原始视频引用仅保留在数据对象中",
      included: index % 2 === 0,
      pending: index === 2,
    }));
    const parsed = parsePersonalRoughCutSnapshot(snapshot({
      frames,
      removed_asset_ids: ["retired-frame", "retired-frame"],
    }), chapterId);

    expect(parsed.frames).toHaveLength(501);
    expect(parsed.frames[0]).toMatchObject({ asset_id: "", frame_index: 0, text: "" });
    expect(parsed.frames[1]?.asset_id).toBe("");
    expect(parsed.removed_asset_ids).toEqual(["retired-frame", "retired-frame"]);
    expect(parsed.frames[1]?.preview_url).toBe("https://media.example.invalid/clip.mp4");
  });

  it("requires each top-level and frame field to be an own property", () => {
    const missingTopLevel = ["chapter_id", "revision", "saved", "frames", "removed_asset_ids"];
    for (const key of missingTopLevel) {
      const value = snapshot();
      delete value[key];
      expect(() => parsePersonalRoughCutSnapshot(value, chapterId), key)
        .toThrow(InvalidResponseError);
    }

    const frameFields = [
      "asset_id",
      "frame_index",
      "text",
      "preview_url",
      "missing_reason",
      "included",
      "pending",
    ];
    for (const key of frameFields) {
      const value = frame();
      delete value[key];
      expect(() => parsePersonalRoughCutSnapshot(snapshot({ frames: [value] }), chapterId), key)
        .toThrow(InvalidResponseError);
    }

    const inherited = Object.assign(Object.create({ chapter_id: chapterId }), {
      revision: 0,
      saved: false,
      frames: [],
      removed_asset_ids: [],
    });
    expect(() => parsePersonalRoughCutSnapshot(inherited, chapterId)).toThrow(InvalidResponseError);
  });

  it("distinguishes null values from missing values and requires the exact chapter", () => {
    expect(parsePersonalRoughCutSnapshot(snapshot({
      revision: 0,
      saved: false,
      frames: [frame({ preview_url: null, missing_reason: null })],
    }), chapterId).frames[0]).toMatchObject({ preview_url: null, missing_reason: null });

    expect(() => parsePersonalRoughCutSnapshot(snapshot({ chapter_id: "another-chapter" }), chapterId))
      .toThrow(/不属于当前章节/);
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ frames: [frame({ preview_url: undefined })] }), chapterId))
      .toThrow(InvalidResponseError);
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ frames: [frame({ missing_reason: 1 })] }), chapterId))
      .toThrow(InvalidResponseError);
  });

  it("requires revision and saved state to agree and validates safe integers and strict booleans", () => {
    for (const revision of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1, "1"]) {
      expect(() => parsePersonalRoughCutSnapshot(snapshot({ revision }), chapterId))
        .toThrow(InvalidResponseError);
    }
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ revision: 0, saved: true }), chapterId))
      .toThrow(InvalidResponseError);
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ revision: 1, saved: false }), chapterId))
      .toThrow(InvalidResponseError);
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ frames: [frame({ included: 1 })] }), chapterId))
      .toThrow(InvalidResponseError);
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ frames: [frame({ pending: "false" })] }), chapterId))
      .toThrow(InvalidResponseError);
  });

  it("rejects duplicate stable IDs and chapter positions without trimming valid source strings", () => {
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ frames: [
      frame({ asset_id: "asset-a", frame_index: 0 }),
      frame({ asset_id: "asset-a", frame_index: 1 }),
    ] }), chapterId)).toThrow(/重复的稳定镜头 ID/);
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ frames: [
      frame({ asset_id: "asset-a", frame_index: 2 }),
      frame({ asset_id: "asset-b", frame_index: 2 }),
    ] }), chapterId)).toThrow(/重复的章节镜头位置/);

    const parsed = parsePersonalRoughCutSnapshot(snapshot({
      frames: [frame({ asset_id: " asset-a ", text: "<b>原样文本</b>" })],
      removed_asset_ids: [" retired-id "],
    }), chapterId);
    expect(parsed.frames[0]?.asset_id).toBe(" asset-a ");
    expect(parsed.frames[0]?.text).toBe("<b>原样文本</b>");
    expect(parsed.removed_asset_ids).toEqual([" retired-id "]);
  });

  it("rejects duplicate removed references only when an entry is not a nonempty string", () => {
    expect(parsePersonalRoughCutSnapshot(snapshot({ removed_asset_ids: ["old", "old"] }), chapterId)
      .removed_asset_ids).toEqual(["old", "old"]);
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ removed_asset_ids: [""] }), chapterId))
      .toThrow(InvalidResponseError);
    expect(() => parsePersonalRoughCutSnapshot(snapshot({ removed_asset_ids: [null] }), chapterId))
      .toThrow(InvalidResponseError);
  });
});
