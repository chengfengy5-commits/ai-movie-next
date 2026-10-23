import { describe, expect, it } from "vitest";
import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type { CreatorPrompt } from "../../shared/api/creatorSettings";
import {
  buildFrameOptimizeRequest,
  applyFrameOptimizePartialToContent,
  frameOptimizeSourceText,
  frameOptimizeChapterContentAfterWrite,
  parseFrameOptimizePartialResult,
  restoreFrameOptimizeOriginals,
  sameFrameOptimizeAssets,
  sameFrameOptimizeAssetIdentity,
  sameFrameOptimizeAssetsAfterChapterWrite,
  stripFrameOptimizeShotMarkers,
} from "./frameOptimizeContent";

const series = {
  style_prompt_id: "style-1",
  style_prompt: "团队回退风格",
} as Pick<Series, "style_prompt_id" | "style_prompt">;

const prompt = {
  id: "style-1",
  category: "style",
  user_id: "user-1",
  name: "风格",
  user_prompt: JSON.stringify({
    storyboard: "镜头风格",
    video: "视频专用",
    reference_image: "https://example.invalid/image",
  }),
} as CreatorPrompt;

describe("frame optimize content preparation", () => {
  it("prefers raw original text and strips technical shot rows only in marked scripts", () => {
    const frame = { original_text: "  原文保留空格  ", text: "当前内容" } as StoryboardFrame;
    expect(frameOptimizeSourceText(frame)).toBe("  原文保留空格  ");
    expect(stripFrameOptimizeShotMarkers("普通剧情\n景别 / 机位：全景")).toBe("普通剧情\n景别 / 机位：全景");
    expect(stripFrameOptimizeShotMarkers(
      "Shot 4：【0-3秒】\n景别 / 机位：全景\n运镜：推进\n音效：风声\n转场：硬切\n人物争执\n\n\n台词",
    )).toBe("人物争执\n\n台词");
  });

  it("builds ordered all-chapter context without exposing frame ordinals or unrelated style JSON", () => {
    const chapter = {
      id: "chapter-1",
      content: [
        { text: "Shot 1：【0-3秒】\n运镜：推进\n小林打开门", character: ["char-1"], scene: ["scene-1"], prop: ["prop-1"] },
        { text: "第二镜内容" },
      ],
    } as unknown as Chapter;
    const request = buildFrameOptimizeRequest(
      chapter,
      [1, 0, 1],
      series,
      {
        characters: [{ id: "char-1", name: "小林" } as Character],
        scenes: [{ id: "scene-1", title: "旧屋" } as Scene],
        props: [{ id: "prop-1", name: "钥匙" } as Prop],
        prompts: [prompt],
      },
      { modelId: "model-1", promptId: "", enableThinking: true, concurrency: 5 },
    );
    expect(request?.prompt_id).toBe("");
    expect(request?.frames.map((frame) => frame.index)).toEqual([0, 1]);
    expect(request?.frames[0]).toMatchObject({
      index: 0,
      text: "Shot 1：【0-3秒】\n运镜：推进\n小林打开门",
      asset_context: "【风格提示词】\n镜头风格\n\n【当前分镜资产信息】\n角色：小林\n场景：旧屋\n道具：钥匙",
    });
    expect(request?.chapter_texts).toEqual([
      "【剧情片段】\n（角色：小林；场景：旧屋；道具：钥匙）\n小林打开门",
      "【剧情片段】\n第二镜内容",
    ]);
    expect(JSON.stringify(request)).not.toContain("视频专用");
    expect(JSON.stringify(request)).not.toContain("reference_image");
    expect(JSON.stringify(request)).not.toContain("分镜 1");
  });

  it("uses team style fallback and only accepts one unambiguous JSON text when no general key exists", () => {
    const noMatch = {
      ...prompt,
      user_prompt: JSON.stringify({ image_image: "url", character: "角色风格", scene: "场景风格" }),
    } as CreatorPrompt;
    const chapter = { id: "chapter-1", content: [{ text: "内容" }] } as unknown as Chapter;
    const fallback = buildFrameOptimizeRequest(
      chapter, [0], series, { characters: [], scenes: [], props: [], prompts: [noMatch] },
      { modelId: "model-1", promptId: "", enableThinking: false, concurrency: 5 },
    );
    expect(fallback?.frames[0]?.asset_context).toBe("【风格提示词】\n团队回退风格");

    const teamStyle = buildFrameOptimizeRequest(
      chapter, [0], { style_prompt_id: "missing", style_prompt: "团队风格" },
      { characters: [], scenes: [], props: [], prompts: [] },
      { modelId: "model-1", promptId: "", enableThinking: false, concurrency: 5 },
    );
    expect(teamStyle?.frames[0]?.asset_context).toBe("【风格提示词】\n团队风格");
  });

  it("keeps ordinary style text but never injects an unrecognized JSON container", () => {
    const chapter = { id: "chapter-1", content: [{ text: "内容" }] } as unknown as Chapter;
    const buildContext = (userPrompt: string) => buildFrameOptimizeRequest(
      chapter,
      [0],
      { style_prompt_id: "style-1", style_prompt: null },
      { characters: [], scenes: [], props: [], prompts: [{ ...prompt, user_prompt: userPrompt } as CreatorPrompt] },
      { modelId: "model-1", promptId: "", enableThinking: false, concurrency: 5 },
    )?.frames[0]?.asset_context;

    expect(buildContext("普通文本风格")).toBe("【风格提示词】\n普通文本风格");
    expect(buildContext('["不可识别", "JSON"]')).toBe("");
    expect(buildContext('{"unknown":"对象内容"')).toBe("");
    expect(buildContext('{"character":"角色风格","scene":"场景风格"}')).toBe("");
  });

  it("restores selected originals without mutating or dropping unrelated fields", () => {
    const source = [
      { text: "优化内容", original_text: "原文", storyboard: ["asset-1"], note: { kept: true } },
      { text: "没有原文", storyboard: ["asset-2"] },
    ] as StoryboardFrame[];
    const restored = restoreFrameOptimizeOriginals(source, [0, 1, 0]);
    expect(restored?.restoredIndices).toEqual([0]);
    expect(restored?.content[0]).toEqual({ text: "原文", storyboard: ["asset-1"], note: { kept: true } });
    expect(source[0]).toHaveProperty("original_text", "原文");
    expect(source[0]).toHaveProperty("text", "优化内容");
    expect(restoreFrameOptimizeOriginals(source, [4])).toBeNull();
  });

  it("models only the empty storyboard reference normalization performed by chapter writes", () => {
    const source = [{ text: "no asset", custom: { keep: true } }, { text: "has ref", storyboard: ["asset-1"] }] as StoryboardFrame[];
    expect(frameOptimizeChapterContentAfterWrite(source)).toEqual([
      { text: "no asset", custom: { keep: true }, storyboard: [] },
      source[1],
    ]);
    expect(source[0]).not.toHaveProperty("storyboard");
  });

  it("parses partial result entries conservatively and checks source asset identity", () => {
    expect(parseFrameOptimizePartialResult({
      partial: [{ index: 1, optimized_text: "新文字" }],
      error: "one_item_failed",
    })).toEqual({
      partial: [{ index: 1, optimized_text: "新文字" }],
      error: "one_item_failed",
      valid: true,
    });
    expect(parseFrameOptimizePartialResult("invalid")).toMatchObject({ valid: false });
    const sourceContent = [
      { text: "原文", storyboard: ["asset-1"], note: "保留" },
      { text: "已有优化", original_text: "旧原文", storyboard: ["asset-2"] },
    ] as StoryboardFrame[];
    expect(applyFrameOptimizePartialToContent(sourceContent, [
      { index: 0, text: "原文" },
      { index: 1, text: "旧原文" },
    ], [{ index: 0, optimized_text: "新优化" }])).toEqual([
      { text: "新优化", original_text: "原文", storyboard: ["asset-1"], note: "保留" },
      sourceContent[1],
    ]);
    expect(sourceContent[0]).toEqual({ text: "原文", storyboard: ["asset-1"], note: "保留" });
    const asset = {
      id: "asset-1", series_id: "series-1", chapter_id: "chapter-1", frame_index: 0,
      name: "分镜", description: "原文", image_url: null, created_at: "created", updated_at: "old",
    } as StoryboardAsset;
    expect(sameFrameOptimizeAssets(
      [asset],
      [{ ...asset, description: "优化文字", updated_at: "new" }],
      [{ index: 0, optimized_text: "优化文字" }],
    )).toBe(true);
    expect(sameFrameOptimizeAssets([asset], [{ ...asset, name: "其他" }], [])).toBe(false);
    expect(sameFrameOptimizeAssetIdentity([asset], [{ ...asset, description: "旧版任务新描述" }])).toBe(true);
    expect(sameFrameOptimizeAssetIdentity([asset], [{ ...asset, frame_index: 2 }])).toBe(false);
  });

  it("checks the exact asset projection produced by a chapter content write", () => {
    const before = [
      { id: "asset-1", series_id: "series-1", chapter_id: "chapter-1", frame_index: 0, name: "旧名称", description: "优化文字", image_url: "image-1", created_at: "created-1", updated_at: "old" },
      { id: "asset-2", series_id: "series-1", chapter_id: "chapter-1", frame_index: 1, name: "另一旧名", description: "相邻", image_url: null, created_at: "created-2", updated_at: "old" },
    ] as StoryboardAsset[];
    const content = [
      { text: "原文", storyboard: ["asset-1"], note: "保留" },
      { text: "相邻", storyboard: ["asset-2"] },
    ] as StoryboardFrame[];
    const synced = [
      { ...before[0]!, name: "分镜 1", description: "原文", updated_at: "new" },
      { ...before[1]!, name: "分镜 2", description: "相邻", updated_at: "new" },
    ];
    expect(sameFrameOptimizeAssetsAfterChapterWrite(before, synced, content)).toBe(true);
    expect(sameFrameOptimizeAssetsAfterChapterWrite(before, [
      synced[0]!, { ...synced[1]!, description: "任意漂移" },
    ], content)).toBe(false);
    expect(sameFrameOptimizeAssetsAfterChapterWrite(before, [
      synced[0]!, { ...synced[1]!, image_url: "other-image" },
    ], content)).toBe(false);
  });
});
