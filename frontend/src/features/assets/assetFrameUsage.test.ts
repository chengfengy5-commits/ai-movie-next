import type { AssetLibraryType, Chapter, Character, Prop, Scene, StoryboardFrame } from "../../shared/api/contracts";
import { describe, expect, it } from "vitest";
import { projectAssetFrameUsage } from "./assetFrameUsage";

const seriesId = "usage-series";

function chapter(
  id: string,
  title: string,
  content: StoryboardFrame[] | null,
  order = 1,
  chapterSeriesId = seriesId,
): Chapter {
  return {
    id,
    series_id: chapterSeriesId,
    title,
    content,
    order,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    lock: null,
  };
}

function character(id = "asset-id", assetSeriesId = seriesId): Character {
  return {
    id,
    series_id: assetSeriesId,
    name: "沈照",
    gender: null,
    age: null,
    role: null,
    appearance: null,
    description: null,
    image_url: null,
    audio_url: null,
    voice_ref: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}

function scene(id = "asset-id", assetSeriesId = seriesId): Scene {
  return {
    id,
    series_id: assetSeriesId,
    title: "盐仓码头",
    description: null,
    image_url: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}

function prop(id = "asset-id", assetSeriesId = seriesId): Prop {
  return {
    id,
    series_id: assetSeriesId,
    name: "旧铜钥匙",
    description: null,
    image_url: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}

function project(
  chapters: unknown,
  category: AssetLibraryType = "characters",
  item: Character | Scene | Prop = character(),
) {
  return projectAssetFrameUsage(chapters, seriesId, category, item);
}

describe("asset frame usage projection", () => {
  it("preserves chapter and frame response order and counts repeated references once per frame", () => {
    const result = project([
      chapter("chapter-later", "响应中的第一章", [
        { character: ["asset-id", "asset-id"], text: "第一处" },
        { character: ["asset-id"], text: "第二处" },
      ], 50),
      chapter("chapter-earlier", "响应中的第二章", [{ character: ["asset-id"] }], 1),
    ]);

    expect(result).toMatchObject({
      status: "matches",
      frameCount: 3,
      referenceCount: 4,
      chapters: [
        {
          position: 1,
          title: "响应中的第一章",
          frames: [
            { chapterPosition: 1, framePosition: 1, referenceCount: 2 },
            { chapterPosition: 1, framePosition: 2, referenceCount: 1 },
          ],
        },
        {
          position: 2,
          title: "响应中的第二章",
          frames: [{ chapterPosition: 2, framePosition: 1, referenceCount: 1 }],
        },
      ],
    });
  });

  it("matches only the selected raw category field and does not trim or use aliases", () => {
    const chapters = [chapter("chapter-exact", "精确匹配", [
      { character: ["asset-id"], scene: ["other-id"], prop: ["asset-id"] },
      { character: [" asset-id "] },
    ])];

    expect(project(chapters, "characters", character())).toMatchObject({ status: "matches", frameCount: 1 });
    expect(project(chapters, "scenes", scene())).toMatchObject({ status: "empty", hasUncertainReferences: false });
    expect(project(chapters, "props", prop())).toMatchObject({ status: "matches", frameCount: 1 });
  });

  it("keeps confirmed rows when the selected reference field contains malformed siblings", () => {
    const result = project([
      chapter("chapter-mixed", "混合引用", [
        { character: ["asset-id", null, 7, "", "another-id"], text: "已确认镜头" },
        { character: { id: "asset-id" } },
      ]),
    ]);

    expect(result).toMatchObject({
      status: "matches",
      frameCount: 1,
      referenceCount: 1,
      hasUncertainReferences: true,
      chapters: [{ frames: [{ framePosition: 1 }] }],
    });
  });

  it("treats missing, undefined, null, and empty reference arrays as no usage while flagging unreadable shapes", () => {
    const frameWithUndefinedReference = { character: undefined } as unknown as StoryboardFrame;
    expect(project([chapter("chapter-none", "无引用", [
      { text: "a" },
      frameWithUndefinedReference,
      { character: null },
      { character: [] },
    ])]))
      .toEqual({ status: "empty", hasUncertainReferences: false });
    expect(project([chapter("chapter-invalid", "坏引用", [{ character: "asset-id" }])]))
      .toEqual({ status: "empty", hasUncertainReferences: true });
    expect(project([chapter("chapter-null-content", "空内容", null)]))
      .toEqual({ status: "empty", hasUncertainReferences: false });
  });

  it("preserves literal text and distinguishes missing and invalid text fields", () => {
    const result = project([chapter("chapter-text", "   ", [
      { character: ["asset-id"], text: "<script>no()</script>", original_text: "<b>原文</b>" },
      { character: ["asset-id"], text: 17, original_text: null },
      { character: ["asset-id"] },
    ])]);

    expect(result).toMatchObject({
      status: "matches",
      chapters: [{
        position: 1,
        title: "未命名章节",
        frames: [
          {
            text: { kind: "value", value: "<script>no()</script>" },
            originalText: { kind: "value", value: "<b>原文</b>" },
          },
          { text: { kind: "invalid" }, originalText: { kind: "missing" } },
          { text: { kind: "missing" }, originalText: { kind: "missing" } },
        ],
      }],
    });
    expect(JSON.stringify(result)).not.toMatch(/asset-id|https?:|canonical|voice|image_url|audio_url/);
  });

  it("rejects invalid asset or chapter ownership and duplicate chapter identities", () => {
    expect(project([chapter("chapter-1", "章节", [{ character: ["asset-id"] }])], "characters", character("asset-id", "other-series")))
      .toEqual({ status: "unreadable" });
    expect(project([chapter("chapter-1", "章节", [{ character: ["asset-id"] }], 1, "other-series")]))
      .toEqual({ status: "unreadable" });
    expect(project([
      chapter("duplicate", "第一章", []),
      chapter("duplicate", "重复身份", []),
    ])).toEqual({ status: "unreadable" });
    expect(project({})).toEqual({ status: "unreadable" });
    expect(project([chapter("chapter-1", "章节", [])], "characters", character("  ")))
      .toEqual({ status: "unreadable" });
  });
});
