import { describe, expect, it } from "vitest";
import type { Character, FusedAsset, Prop, Scene, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import {
  buildQuickReferencePlan,
  defaultQuickReferenceSettings,
  formatQuickReferenceDescription,
  matchQuickReferenceDuration,
  mergeQuickReferencePreferences,
  quickReferenceEditsMatch,
  restoreQuickReferenceSettings,
} from "./quickReference";

describe("quick reference preferences and context", () => {
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
});
