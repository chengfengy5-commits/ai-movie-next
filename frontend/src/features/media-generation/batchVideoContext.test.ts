import { describe, expect, it } from "vitest";
import type { Character, FusedAsset, Prop, Scene, Series, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import { defaultQuickReferenceSettings } from "./quickReference";
import {
  buildBatchVideoFrameContext,
  estimateBatchVideoCost,
  renderBatchVideoPrompt,
  suggestedBatchVideoDuration,
} from "./batchVideoContext";
import {
  batchVideoPreferenceKey,
  defaultBatchVideoChecks,
  readBatchVideoPreference,
  writeBatchVideoPreference,
} from "./batchVideoPreferences";

const URLS = {
  preview: "https://media.example.test/preview.png",
  storyboard: "https://media.example.test/storyboard.png",
  character: "https://media.example.test/character.png",
  fusion: "https://media.example.test/fusion.png",
  scene: "https://media.example.test/scene.png",
  prop: "https://media.example.test/prop.png",
};

const series: Series = {
  id: "series-1", user_id: "actor-1", name: "示例", description: null, image_url: null,
  style_prompt_id: "style-1", style_prompt: null, style_prompt_name: null, style_prompt_owner_name: null,
  team_id: null, team_name: null, owner_name: null, claimed_by: null, claimed_by_username: null,
  claimed_by_avatar_url: null, can_enter: true, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
};

const storyboard: StoryboardAsset = {
  id: "storyboard-1", series_id: "series-1", chapter_id: "chapter-1", frame_index: 0,
  name: "第一镜", description: null, image_url: URLS.storyboard,
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
};

const character: Character = {
  id: "character-1", series_id: "series-1", name: "林舟", description: null, image_url: URLS.character,
  aliases: null, canonical_key: null, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
  gender: null, age: null, role: null, appearance: null, audio_url: null, voice_ref: null,
};

const scene: Scene = {
  id: "scene-1", series_id: "series-1", title: "雨巷", description: null, image_url: URLS.scene,
  aliases: null, canonical_key: null, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
};

const prop: Prop = {
  id: "prop-1", series_id: "series-1", name: "旧伞", description: null, image_url: URLS.prop,
  aliases: null, canonical_key: null, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
};

function makeFrame(overrides: Record<string, unknown> = {}): StoryboardFrame {
  return {
    storyboard: [storyboard.id], character: [character.id], scene: [scene.id], prop: [prop.id],
    fused: ["fused-1"], preview: URLS.preview, text: "人物在雨巷停下，镜头拉近。", ...overrides,
  };
}

function makeFused(imageUrl: string | null = URLS.fusion): FusedAsset {
  return {
    id: "fused-1", series_id: "series-1", member_type: "character",
    members: [{ kind: "asset", id: character.id, name: character.name }], name: "林舟融合图", image_url: imageUrl,
    size: null, status: "completed", error_message: null,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
  };
}

function makeModel(overrides: Partial<CreatorModel> = {}): CreatorModel {
  return {
    id: "video-model", category: "video", name: "视频", provider: "fixture", model: "fixture",
    base_url: null, params: "{}", sort_order: 0, is_system: true, user_id: "system", ...overrides,
  };
}

function buildContext(input: {
  readonly frame?: StoryboardFrame;
  readonly chapterContent?: readonly StoryboardFrame[] | null;
  readonly checks?: { character: boolean; scene: boolean; prop: boolean; storyboard: boolean };
  readonly characterImage?: string | null;
  readonly fusion?: FusedAsset;
  readonly prompts?: readonly CreatorPrompt[];
} = {}) {
  const settings = defaultQuickReferenceSettings();
  return buildBatchVideoFrameContext({
    storyboardId: storyboard.id,
    chapterContent: input.chapterContent ?? [input.frame ?? makeFrame()],
    storyboardAssets: [storyboard],
    characters: [{ ...character, image_url: input.characterImage === undefined ? character.image_url : input.characterImage }],
    scenes: [scene],
    props: [prop],
    fusedAssets: [input.fusion ?? makeFused()],
    checks: input.checks ?? { character: true, scene: true, prop: true, storyboard: true },
    quickSettings: { ...settings.video, characterVoice: false, matchDuration: false },
    series,
    prompts: input.prompts ?? [],
  });
}

describe("batch video frame context", () => {
  it("stores only shared options in an account and API scoped local preference", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
    };
    const preference = {
      modelId: "video-model", promptId: "video-prompt", size: "16:9", resolution: "720p",
      checks: { ...defaultBatchVideoChecks(), scene: false },
    };
    expect(writeBatchVideoPreference("actor-1", "https://api.example.test/api", preference, storage)).toBe(true);
    expect(readBatchVideoPreference("actor-1", "https://api.example.test/api", storage)).toEqual({ kind: "found", value: preference });
    expect(readBatchVideoPreference("actor-2", "https://api.example.test/api", storage)).toEqual({ kind: "missing" });
    const key = batchVideoPreferenceKey("actor-1", "https://api.example.test/api");
    expect(JSON.parse(values.get(key) ?? "{}")).not.toHaveProperty("duration");
  });

  it("keeps preview and enabled asset references ordered and reuses their final image numbers", () => {
    const prompt: CreatorPrompt = {
      id: "style-1", category: "style", name: "风格",
      user_prompt: JSON.stringify({ video: "电影感", storyboard: "镜头风格", video_image: URLS.fusion }),
      sort_order: 0, is_system: true, user_id: "system",
    };
    const result = buildContext({ prompts: [prompt] });
    expect(result.kind).toBe("ready");
    if (result.kind !== "ready") return;
    expect(result.value.referenceImages).toEqual([
      URLS.preview, URLS.storyboard, URLS.fusion, URLS.scene, URLS.prop,
    ]);
    const rendered = renderBatchVideoPrompt(null, result.value, 0);
    expect(rendered.prompt).toContain("图片1 = 分镜预览图");
    expect(rendered.prompt).toContain("图片3 = 角色融合图：林舟融合图（林舟）");
    expect(rendered.prompt).toContain("图片3 = 风格参考");
    expect(rendered.prompt).toContain("【风格提示词】\n电影感");
    const videoPreview = buildContext({ frame: makeFrame({ preview: "https://media.example.test/finished.m3u8?token=fixture" }) });
    expect(videoPreview.kind).toBe("ready");
    if (videoPreview.kind === "ready") expect(videoPreview.value.referenceImages).not.toContain("https://media.example.test/finished.m3u8?token=fixture");
  });

  it("still blocks a missing referenced member image even when a fusion image covers it", () => {
    const result = buildContext({ characterImage: null });
    expect(result).toMatchObject({ kind: "missing-assets", names: expect.arrayContaining(["林舟"]) });

    const checksOff = buildContext({
      characterImage: null,
      checks: { character: false, scene: false, prop: false, storyboard: false },
    });
    expect(checksOff).toMatchObject({ kind: "ready", value: { referenceImages: [URLS.preview] } });
    expect(buildContext({ chapterContent: [makeFrame(), makeFrame()] })).toEqual({ kind: "missing-frame" });
  });

  it("matches decimal storyboard durations and estimates only supported server pricing rules", () => {
    expect(suggestedBatchVideoDuration("停留 6.5 秒，再快速切镜头", [5, 7, 10])).toBe(7);
    expect(estimateBatchVideoCost(makeModel({ is_system: false,
      params: JSON.stringify({ billing_mode: "per_second", per_second_cost: 8 }), cost_per_call: 200 }), "1080p", [5]))
      .toMatchObject({ amount: 0 });
    expect(estimateBatchVideoCost(makeModel({ cost_per_call: 10,
      params: JSON.stringify({ billing_mode: "fixed", fixed_by_quality: { "1080p": 0 } }) }), "1080p", [5]))
      .toMatchObject({ amount: 10 });
    expect(estimateBatchVideoCost(makeModel({ cost_per_call: 10,
      params: JSON.stringify({ fixed_by_quality: { "720p": 99 } }) }), "720p", [5]))
      .toMatchObject({ amount: 10 });
    expect(estimateBatchVideoCost(makeModel({ cost_per_call: 10,
      params: JSON.stringify({ billing_mode: "per_second", per_second_cost: 7, per_second_by_quality: { "720p": 0 } }) }), "720p", [5]))
      .toMatchObject({ amount: 0 });
    expect(estimateBatchVideoCost(makeModel({ cost_per_call: 0,
      params: JSON.stringify({ aistarslab: { billingType: "per_second", perSecondCredits: 4 } }) }), "720p", [5, 10]))
      .toMatchObject({ amount: 60 });
    expect(estimateBatchVideoCost(makeModel({ params: JSON.stringify({ billing_mode: "unknown" }) }), "720p", [5]))
      .toMatchObject({ amount: null });
    expect(estimateBatchVideoCost(makeModel({ params: JSON.stringify({ aistarslab: { billingType: "other", perSecondCredits: 4 } }) }), "720p", [5]))
      .toMatchObject({ amount: null });
  });
});
