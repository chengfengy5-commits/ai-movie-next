import { describe, expect, it } from "vitest";
import type { Chapter } from "../../../shared/api/contracts";
import { toAssetReplacementType } from "../../../shared/api/assetReplacement";
import { assetSnapshotMatches, buildAssetReplacementPlan, chapterContentMatches } from "./assetReplacement";

const snapshot = [{
  id: "chapter-real-id",
  series_id: "series-1",
  title: "第一章",
  content: [
    {
      character: ["old-id", "old-id", "new-id", "new-id"],
      scene: ["old-id"],
      text: "原始分镜",
      original_text: "原始稿",
      preview: { key: "preserve-me" },
      extra: { unknown: true },
    },
    { character: ["old-id", "other-id"], text: "第二镜" },
  ],
}];

describe("asset replacement plan", () => {
  it("maps each library category to the existing single-chapter API type", () => {
    expect(toAssetReplacementType("characters")).toBe("character");
    expect(toAssetReplacementType("scenes")).toBe("scene");
    expect(toAssetReplacementType("props")).toBe("prop");
  });

  it("previews exact character changes while preserving duplicate targets and other fields", () => {
    const plan = buildAssetReplacementPlan(snapshot, "series-1", "characters", "old-id", "new-id");
    expect(plan.status).toBe("ready");
    if (plan.status !== "ready") return;

    expect(plan.chapters).toHaveLength(1);
    expect(plan.chapters[0]).toMatchObject({
      chapterId: "chapter-real-id",
      status: "ready",
      referenceCount: 3,
      frames: [
        { framePosition: 1, referenceCount: 2, text: "原始分镜", originalText: "原始稿" },
        { framePosition: 2, referenceCount: 1, text: "第二镜", originalText: null },
      ],
    });
    expect(plan.chapters[0]?.expectedContent).toEqual([
      {
        character: ["new-id", "new-id"],
        scene: ["old-id"],
        text: "原始分镜",
        original_text: "原始稿",
        preview: { key: "preserve-me" },
        extra: { unknown: true },
      },
      { character: ["new-id", "other-id"], text: "第二镜" },
    ]);
  });

  it("matches identity by exact ID and leaves same-name assets unrelated", () => {
    const chapters = [
      ...snapshot,
      {
        id: "same-name-only",
        series_id: "series-1",
        title: "第二章",
        content: [{ character: ["different-id"], text: "与素材同名不构成身份" }],
      },
    ];
    const plan = buildAssetReplacementPlan(chapters, "series-1", "characters", "old-id", "new-id");
    expect(plan.status).toBe("ready");
    if (plan.status !== "ready") return;
    expect(plan.chapters.map((chapter) => chapter.chapterId)).toEqual(["chapter-real-id"]);
  });

  it("marks a chapter with uncertain references unreadable and excludes it from submission", () => {
    const plan = buildAssetReplacementPlan([{
      id: "chapter-unknown",
      series_id: "series-1",
      title: "未知引用",
      content: [{ character: ["old-id", ""], text: "无法确认完整引用" }],
    }], "series-1", "characters", "old-id", "new-id");
    expect(plan.status).toBe("ready");
    if (plan.status !== "ready") return;
    expect(plan.chapters).toEqual([expect.objectContaining({
      chapterId: "chapter-unknown",
      status: "unreadable",
      expectedContent: null,
      reason: "分类素材引用包含无法识别的值。",
    })]);
  });

  it("marks null chapter content unreadable instead of treating it as zero references", () => {
    const plan = buildAssetReplacementPlan([{
      id: "chapter-empty-content",
      series_id: "series-1",
      title: "内容缺失",
      content: null,
    }], "series-1", "characters", "old-id", "new-id");
    expect(plan).toMatchObject({
      status: "ready",
      chapters: [expect.objectContaining({
        chapterId: "chapter-empty-content",
        status: "unreadable",
        reason: "章节内容为空，无法确认是否包含旧素材引用。",
      })],
    });
  });

  it("rejects duplicate or cross-series chapter identity and invalid asset identities", () => {
    expect(buildAssetReplacementPlan(snapshot, "other-series", "characters", "old-id", "new-id").status)
      .toBe("unreadable");
    expect(buildAssetReplacementPlan([snapshot[0], snapshot[0]], "series-1", "characters", "old-id", "new-id"))
      .toMatchObject({ status: "unreadable", reason: "章节列表包含重复身份，不能安全生成预览。" });
    expect(buildAssetReplacementPlan(snapshot, "series-1", "characters", "old-id", "old-id").status)
      .toBe("unreadable");
  });

  it("compares reread JSON content independent of object-key order", () => {
    const chapter = { content: [{ text: "keep", extra: { b: 2, a: 1 } }] } as unknown as Chapter;
    expect(chapterContentMatches(chapter, [{ extra: { a: 1, b: 2 }, text: "keep" }])).toBe(true);
    expect(chapterContentMatches(chapter, [{ text: "changed", extra: { b: 2, a: 1 } }])).toBe(false);
    expect(chapterContentMatches(undefined, chapter.content)).toBe(false);
  });

  it("compares full selected asset snapshots independent of object-key order", () => {
    const selected = {
      id: "new-id",
      series_id: "series-1",
      name: "新角色",
      appearance: "深色外套",
      description: "档案馆管理员",
    };
    expect(assetSnapshotMatches(selected, {
      description: "档案馆管理员",
      appearance: "深色外套",
      name: "新角色",
      series_id: "series-1",
      id: "new-id",
    })).toBe(true);
    expect(assetSnapshotMatches(selected, { ...selected, name: "改名后的角色" })).toBe(false);
    expect(assetSnapshotMatches(selected, { ...selected, description: "更新后的角色介绍" })).toBe(false);
  });
});
