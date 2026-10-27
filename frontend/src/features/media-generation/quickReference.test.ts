import { describe, expect, it } from "vitest";
import type { Character, FusedAsset, Prop, Scene, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type { CreatorPrompt } from "../../shared/api/creatorSettings";
import {
  buildQuickReferencePlan,
  defaultQuickReferenceSettings,
  formatQuickReferenceDescription,
  matchQuickReferenceDuration,
  mergeQuickReferencePreferences,
  quickReferenceEditsMatch,
  restoreQuickReferenceSettings,
} from "./quickReference";
import { resolveQuickReferenceStyle } from "./quickReferenceStyle";

describe("quick reference preferences and context", () => {
  it("resolves owner and shared style text and images by the requested category chain", () => {
    const prompt: CreatorPrompt = {
      id: "style-prompt",
      category: "style",
      name: "剧集风格",
      user_prompt: JSON.stringify({
        video: "视频专属风格",
        storyboard: "分镜回退风格",
        general: "通用风格",
        video_image: "https://cdn.example.test/video-style.png",
        storyboard_image: "/uploads/storyboard-style.png",
        character_image: "https://cdn.example.test/character-style.png",
      }),
      system_prompt: "系统提示词不优先",
      sort_order: 0,
      user_id: "owner",
    };
    const ownerSeries = { style_prompt_id: prompt.id, style_prompt: "剧集共享兜底" };

    expect(resolveQuickReferenceStyle({
      series: ownerSeries,
      prompts: [prompt],
      category: "video",
      fallbackCategories: ["storyboard"],
    })).toEqual({ promptText: "【风格提示词】\n视频专属风格", imageUrl: "https://cdn.example.test/video-style.png" });
    expect(resolveQuickReferenceStyle({
      series: ownerSeries,
      prompts: [{ ...prompt, user_prompt: JSON.stringify({ storyboard: "分镜回退风格", storyboard_image: "/uploads/storyboard-style.png" }) }],
      category: "video",
      fallbackCategories: ["storyboard"],
    })).toEqual({ promptText: "【风格提示词】\n分镜回退风格", imageUrl: "/uploads/storyboard-style.png" });
    expect(resolveQuickReferenceStyle({
      series: ownerSeries,
      prompts: [{ ...prompt, user_prompt: "", system_prompt: "纯文本系统风格" }],
      category: "scene",
    })).toEqual({ promptText: "【风格提示词】\n纯文本系统风格", imageUrl: null });
    expect(resolveQuickReferenceStyle({
      series: { style_prompt_id: "shared-style-prompt", style_prompt: JSON.stringify({ default: "共享风格", scene_image: "https://cdn.example.test/scene-style.png" }) },
      prompts: [],
      category: "scene",
    })).toEqual({ promptText: "【风格提示词】\n共享风格", imageUrl: "https://cdn.example.test/scene-style.png" });
  });

  it("does not use style text without a style ID or fall back to image-only JSON", () => {
    const styleText = JSON.stringify({ character_image: "https://cdn.example.test/character.png" });
    expect(resolveQuickReferenceStyle({
      series: { style_prompt_id: null, style_prompt: styleText },
      prompts: [],
      category: "character",
    })).toEqual({ promptText: "", imageUrl: null });
    expect(resolveQuickReferenceStyle({
      series: { style_prompt_id: "", style_prompt: styleText },
      prompts: [],
      category: "character",
    })).toEqual({ promptText: "", imageUrl: null });
    expect(resolveQuickReferenceStyle({
      series: { style_prompt_id: "style", style_prompt: null },
      prompts: [{
        id: "style", category: "style", name: "风格", user_prompt: styleText, sort_order: 0, user_id: "owner",
      }],
      category: "character",
    })).toEqual({ promptText: "", imageUrl: "https://cdn.example.test/character.png" });
    expect(resolveQuickReferenceStyle({
      series: { style_prompt_id: "style", style_prompt: null },
      prompts: [{
        id: "style", category: "style", name: "风格",
        user_prompt: JSON.stringify({ character: "角色文本", scene: "场景文本" }), sort_order: 0, user_id: "owner",
      }],
      category: "prop",
    })).toEqual({ promptText: "", imageUrl: null });
  });

  it("migrates legacy flat leaves and preserves other mode values and unknown keys on a leaf update", () => {
    const latest = {
      quick_ref_settings: { scene: false, characterVoice: false, custom_setting: "keep" },
      batch_gen_prefs: { extension: "keep-batch" },
    };
    const restored = restoreQuickReferenceSettings(latest.quick_ref_settings);
    expect(restored.image.scene).toBe(false);
    expect(restored.video.scene).toBe(false);
    expect(restored.video.characterVoice).toBe(false);

    const nextSettings = { ...restored, image: { ...restored.image, prop: false } };
    const update = mergeQuickReferencePreferences(latest, nextSettings, new Set(["image:prop"]));
    expect(update).toEqual({ quick_ref_settings: {
      scene: false,
      characterVoice: false,
      custom_setting: "keep",
      image: expect.objectContaining({ scene: false, prop: false }),
      video: expect.objectContaining({ scene: false, characterVoice: false }),
    } });
    expect(latest.batch_gen_prefs).toEqual({ extension: "keep-batch" });
    expect(quickReferenceEditsMatch({ quick_ref_settings: { image: { prop: false } } }, nextSettings, new Set(["image:prop"]))).toBe(true);
    expect(quickReferenceEditsMatch({ quick_ref_settings: { image: {} } }, nextSettings, new Set(["image:prop"]))).toBe(false);
  });

  it("keeps references ordered and deduplicated while fused members and voice remain independent", () => {
    const settings = defaultQuickReferenceSettings();
    const sharedUrl = "https://cdn.example.test/story-and-character.png";
    const character: Character = {
      id: "character-covered", series_id: "series", name: "林舟", description: null, image_url: "https://cdn.example.test/covered.png",
      aliases: null, canonical_key: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
      gender: null, age: null, role: null, appearance: null, audio_url: "https://cdn.example.test/voice.mp3", voice_ref: "低沉平静",
    };
    const uncovered: Character = { ...character, id: "character-uncovered", name: "阿遥", image_url: sharedUrl, audio_url: null, voice_ref: null };
    const scene: Scene = {
      id: "scene", series_id: "series", title: "雨巷", description: null, image_url: "https://cdn.example.test/scene.png",
      aliases: null, canonical_key: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    };
    const prop: Prop = {
      id: "prop", series_id: "series", name: "纸伞", description: null, image_url: "https://cdn.example.test/prop.png",
      aliases: null, canonical_key: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    };
    const storyboard: StoryboardAsset = {
      id: "storyboard", series_id: "series", chapter_id: "chapter", frame_index: 2, name: "转场", description: "巷口",
      image_url: sharedUrl, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    };
    const fused: FusedAsset = {
      id: "fused-character", series_id: "series", member_type: "character",
      members: [{ kind: "asset", id: "character-covered", name: "林舟" }], name: "林舟融合", image_url: "https://cdn.example.test/fused.png",
      size: null, status: "completed", error_message: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    };
    const frame = {
      storyboard: [storyboard.id], character: [character.id, uncovered.id], scene: [scene.id], prop: [prop.id], fused: [fused.id],
      text: "镜头时长 7.2 秒",
    } as unknown as StoryboardFrame;
    const plan = buildQuickReferencePlan({
      mode: "video", target: { kind: "frame_message", storyboardAssetId: storyboard.id }, settings: settings.video,
      chapterContent: [frame], storyboardAssets: [storyboard], characters: [character, uncovered], scenes: [scene], props: [prop], fusedAssets: [fused],
    });

    expect(plan?.imageReferences).toEqual([
      sharedUrl,
      "https://cdn.example.test/fused.png",
      "https://cdn.example.test/scene.png",
      "https://cdn.example.test/prop.png",
    ]);
    expect(plan?.audioReferences).toEqual(["https://cdn.example.test/voice.mp3"]);
    expect(plan?.voiceNotes).toEqual(["林舟：低沉平静"]);
    const description = formatQuickReferenceDescription(plan!, new Map([
      [sharedUrl, 1],
      ["https://cdn.example.test/fused.png", 2],
      ["https://cdn.example.test/scene.png", 3],
      ["https://cdn.example.test/prop.png", 4],
    ]), new Map([["https://cdn.example.test/voice.mp3", 1]]));
    expect(description.match(/图片1 = /gu)).toHaveLength(2);
    expect(description).toContain("镜头时长 7.2 秒");

    const voiceOnlyPlan = buildQuickReferencePlan({
      mode: "video", target: { kind: "frame_message", storyboardAssetId: storyboard.id },
      settings: { ...settings.video, character: false }, chapterContent: [frame], storyboardAssets: [storyboard],
      characters: [character, uncovered], scenes: [scene], props: [prop], fusedAssets: [fused],
    });
    expect(voiceOnlyPlan?.imageReferences).not.toContain("https://cdn.example.test/fused.png");
    expect(voiceOnlyPlan?.audioReferences).toEqual(["https://cdn.example.test/voice.mp3"]);
    const voiceDisabledPlan = buildQuickReferencePlan({
      mode: "video", target: { kind: "frame_message", storyboardAssetId: storyboard.id },
      settings: { ...settings.video, characterVoice: false }, chapterContent: [frame], storyboardAssets: [storyboard],
      characters: [character, uncovered], scenes: [scene], props: [prop], fusedAssets: [fused],
    });
    expect(voiceDisabledPlan?.audioReferences).toEqual([]);
    expect(voiceDisabledPlan?.voiceNotes).toEqual([]);
  });

  it("matches the largest decimal duration to a supported option and leaves unmatched text alone", () => {
    expect(matchQuickReferenceDuration("第1镜 5.2秒\n第2镜 7.2秒", [5, 10])).toMatchObject({
      targetDuration: 7,
      duration: 10,
      context: "第2镜 7.2秒",
    });
    expect(matchQuickReferenceDuration("没有时长数字", [5, 10]).duration).toBeNull();
  });

  it("uses original frame text before the existing storyboard fallback", () => {
    const settings = defaultQuickReferenceSettings();
    const storyboard: StoryboardAsset = {
      id: "storyboard", series_id: "series", chapter_id: "chapter", frame_index: 0,
      name: "镜头资产名", description: "镜头资产描述", image_url: null,
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    };
    const build = (frame: StoryboardFrame) => buildQuickReferencePlan({
      mode: "image",
      target: { kind: "asset_message", assetType: "storyboard", assetId: storyboard.id },
      settings: settings.image,
      chapterContent: [frame],
      storyboardAssets: [storyboard],
      characters: [], scenes: [], props: [], fusedAssets: [],
    });

    expect(build({ storyboard: [storyboard.id], text: "", original_text: "原始镜头正文" } as StoryboardFrame)?.baseDescription)
      .toBe("原始镜头正文");
    expect(build({ storyboard: [storyboard.id], text: "", original_text: "" } as StoryboardFrame)?.baseDescription)
      .toBe("镜头资产名\n镜头资产描述");
  });
});
