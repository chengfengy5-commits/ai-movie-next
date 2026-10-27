import type { Character, FusedAsset, Prop, Scene, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import { safeCanvasImageReferenceName } from "../canvas/image-generation/canvasImageReferences";

export type QuickReferenceMode = "image" | "video";
export type QuickReferenceField = "storyboard" | "character" | "scene" | "prop" | "fused" | "characterVoice" | "matchDuration";
export type QuickReferencePreferenceKey = `${QuickReferenceMode}:${QuickReferenceField}`;

export interface QuickReferenceModeSettings {
  readonly storyboard: boolean;
  readonly character: boolean;
  readonly scene: boolean;
  readonly prop: boolean;
  readonly fused: boolean;
  readonly characterVoice: boolean;
  readonly matchDuration: boolean;
}

export interface QuickReferenceSettings {
  readonly image: QuickReferenceModeSettings;
  readonly video: QuickReferenceModeSettings;
}

export type QuickReferenceTarget =
  | { readonly kind: "frame_message"; readonly storyboardAssetId: string }
  | { readonly kind: "asset_message"; readonly assetType: "character" | "scene" | "prop" | "storyboard"; readonly assetId: string };

export interface QuickReferenceEntry {
  readonly url: string;
  readonly label: string;
}

export interface QuickReferencePlan {
  readonly baseDescription: string;
  readonly imageReferences: readonly string[];
  readonly imageLabels: readonly QuickReferenceEntry[];
  readonly audioReferences: readonly string[];
  readonly audioLabels: readonly QuickReferenceEntry[];
  readonly voiceNotes: readonly string[];
}

export interface BuildQuickReferencePlanInput {
  readonly mode: QuickReferenceMode;
  readonly target: QuickReferenceTarget;
  readonly settings: QuickReferenceSettings[QuickReferenceMode];
  readonly chapterContent: readonly StoryboardFrame[] | null;
  readonly storyboardAssets: readonly StoryboardAsset[];
  readonly characters: readonly Character[];
  readonly scenes: readonly Scene[];
  readonly props: readonly Prop[];
  readonly fusedAssets: readonly FusedAsset[];
}

export interface QuickReferenceDurationMatch {
  readonly targetDuration: number | null;
  readonly duration: number | null;
  readonly context: string;
  readonly message: string | null;
}

export const QUICK_REFERENCE_FIELDS: Readonly<Record<QuickReferenceMode, readonly QuickReferenceField[]>> = {
  image: ["storyboard", "character", "scene", "prop", "fused"],
  video: ["storyboard", "character", "scene", "prop", "characterVoice", "matchDuration", "fused"],
};

const IMAGE_FIELDS: readonly QuickReferenceField[] = ["storyboard", "character", "scene", "prop", "fused"];
const LEGACY_FIELDS = ["character", "scene", "prop", "storyboard", "characterVoice", "matchDuration"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function defaultModeSettings(): QuickReferenceModeSettings {
  return {
    storyboard: true,
    character: true,
    scene: true,
    prop: true,
    fused: true,
    characterVoice: true,
    matchDuration: true,
  };
}

function hasLegacySettings(value: Record<string, unknown>): boolean {
  return LEGACY_FIELDS.some((field) => Object.prototype.hasOwnProperty.call(value, field));
}

function migrateLegacySettings(value: Record<string, unknown>): Record<string, unknown> {
  const image: Record<string, unknown> = {};
  const video: Record<string, unknown> = {};
  for (const field of IMAGE_FIELDS) {
    image[field] = value[field] !== false;
    video[field] = value[field] !== false;
  }
  video.characterVoice = value.characterVoice !== false;
  video.matchDuration = value.matchDuration !== false;
  return { ...value, image, video };
}

export function defaultQuickReferenceSettings(): QuickReferenceSettings {
  return { image: defaultModeSettings(), video: defaultModeSettings() };
}

function normalizeModeSettings(value: unknown, fallback: Record<string, unknown>): QuickReferenceModeSettings {
  const saved = isRecord(value) ? value : {};
  return {
    storyboard: saved.storyboard === undefined ? fallback.storyboard !== false : saved.storyboard !== false,
    character: saved.character === undefined ? fallback.character !== false : saved.character !== false,
    scene: saved.scene === undefined ? fallback.scene !== false : saved.scene !== false,
    prop: saved.prop === undefined ? fallback.prop !== false : saved.prop !== false,
    fused: saved.fused === undefined ? fallback.fused !== false : saved.fused !== false,
    characterVoice: saved.characterVoice === undefined ? fallback.characterVoice !== false : saved.characterVoice !== false,
    matchDuration: saved.matchDuration === undefined ? fallback.matchDuration !== false : saved.matchDuration !== false,
  };
}

export function restoreQuickReferenceSettings(value: unknown): QuickReferenceSettings {
  const defaults = defaultQuickReferenceSettings();
  if (!isRecord(value)) return defaults;
  const hasNestedMode = isRecord(value.image) || isRecord(value.video);
  if (hasNestedMode) {
    const fallback = hasLegacySettings(value) ? migrateLegacySettings(value) : defaults as unknown as Record<string, unknown>;
    return {
      image: normalizeModeSettings(value.image, fallback.image as Record<string, unknown>),
      video: normalizeModeSettings(value.video, fallback.video as Record<string, unknown>),
    };
  }
  if (!hasLegacySettings(value)) return defaults;

  const flat = value;
  return {
    image: {
      ...defaults.image,
      character: flat.character !== false,
      scene: flat.scene !== false,
      prop: flat.prop !== false,
      storyboard: flat.storyboard !== false,
      fused: flat.fused !== false,
    },
    video: {
      ...defaults.video,
      character: flat.character !== false,
      characterVoice: flat.characterVoice !== false,
      scene: flat.scene !== false,
      prop: flat.prop !== false,
      storyboard: flat.storyboard !== false,
      matchDuration: flat.matchDuration !== false,
      fused: flat.fused !== false,
    },
  };
}

function normalizedPreferenceBase(value: unknown): Record<string, unknown> {
  const saved = isRecord(value) ? value : {};
  if (isRecord(saved.image) || isRecord(saved.video)) {
    if (hasLegacySettings(saved)) {
      const migrated = migrateLegacySettings(saved);
      return {
        ...saved,
        image: { ...(migrated.image as Record<string, unknown>), ...(isRecord(saved.image) ? saved.image : {}) },
        video: { ...(migrated.video as Record<string, unknown>), ...(isRecord(saved.video) ? saved.video : {}) },
      };
    }
    return {
      ...saved,
      image: { ...(isRecord(saved.image) ? saved.image : {}) },
      video: { ...(isRecord(saved.video) ? saved.video : {}) },
    };
  }
  if (!hasLegacySettings(saved)) {
    return { ...saved, image: {}, video: {} };
  }
  return migrateLegacySettings(saved);
}

export function findQuickReferenceFrame(
  chapterContent: readonly StoryboardFrame[] | null,
  storyboardAssetId: string,
): StoryboardFrame | null {
  return chapterContent?.find((candidate) => (
    isRecord(candidate) && idList(candidate.storyboard).includes(storyboardAssetId)
  )) ?? null;
}

export function mergeQuickReferencePreferences(
  latestPreferences: Record<string, unknown>,
  settings: QuickReferenceSettings,
  edits: ReadonlySet<QuickReferencePreferenceKey>,
): Record<string, unknown> {
  const savedSettings = normalizedPreferenceBase(latestPreferences.quick_ref_settings);
  for (const key of edits) {
    const separator = key.indexOf(":");
    const mode = key.slice(0, separator) as QuickReferenceMode;
    const field = key.slice(separator + 1) as QuickReferenceField;
    const savedMode = isRecord(savedSettings[mode]) ? savedSettings[mode] as Record<string, unknown> : {};
    savedSettings[mode] = { ...savedMode, [field]: settings[mode][field] };
  }
  return { quick_ref_settings: savedSettings };
}

export function quickReferenceEditsMatch(
  latestPreferences: Record<string, unknown>,
  settings: QuickReferenceSettings,
  edits: ReadonlySet<QuickReferencePreferenceKey>,
): boolean {
  const savedSettings = isRecord(latestPreferences.quick_ref_settings)
    ? latestPreferences.quick_ref_settings
    : null;
  if (savedSettings === null) return false;
  return [...edits].every((key) => {
    const separator = key.indexOf(":");
    const mode = key.slice(0, separator) as QuickReferenceMode;
    const field = key.slice(separator + 1) as QuickReferenceField;
    const savedMode = isRecord(savedSettings[mode]) ? savedSettings[mode] as Record<string, unknown> : null;
    return savedMode !== null
      && Object.prototype.hasOwnProperty.call(savedMode, field)
      && savedMode[field] === settings[mode][field];
  });
}

function stringId(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value
    : typeof value === "number" && Number.isSafeInteger(value) ? String(value)
      : null;
}

function idList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const id = stringId(item);
    return id === null ? [] : [id];
  });
}

function safeLabel(value: string | null | undefined, fallback: string): string {
  if (value === null || value === undefined) return fallback;
  return safeCanvasImageReferenceName(value, fallback);
}

function validText(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

function directAssetDescription(
  mode: QuickReferenceMode,
  target: Extract<QuickReferenceTarget, { kind: "asset_message" }>,
  input: BuildQuickReferencePlanInput,
  audioReferences: string[],
  audioLabels: QuickReferenceEntry[],
  voiceNotes: string[],
): string | null {
  const addVoice = (asset: Character, name: string) => {
    if (mode !== "video" || !input.settings.characterVoice) return;
    const voice = validText(asset.voice_ref);
    if (voice !== null) voiceNotes.push(`${name}：${voice}`);
    const audio = validText(asset.audio_url);
    if (audio !== null && !audioReferences.includes(audio)) {
      audioReferences.push(audio);
      audioLabels.push({ url: audio, label: `${name}音频` });
    }
  };

  if (target.assetType === "character") {
    const asset = input.characters.find((item) => item.id === target.assetId);
    if (asset === undefined) return null;
    const name = safeLabel(asset.name, "角色");
    const parts = [`角色：${name}`];
    const gender = validText(asset.gender);
    const age = validText(asset.age);
    const appearance = validText(asset.appearance);
    const description = validText(asset.description);
    if (gender !== null) parts.push(`性别：${gender}`);
    if (age !== null) parts.push(`年龄：${age}`);
    if (appearance !== null) parts.push(`外貌：${appearance}`);
    if (description !== null) parts.push(`描述：${description}`);
    addVoice(asset, name);
    return parts.join("\n");
  }
  if (target.assetType === "scene") {
    const asset = input.scenes.find((item) => item.id === target.assetId);
    if (asset === undefined) return null;
    const parts = [`场景：${safeLabel(asset.title, "场景")}`];
    const description = validText(asset.description);
    if (description !== null) parts.push(`描述：${description}`);
    return parts.join("\n");
  }
  if (target.assetType === "prop") {
    const asset = input.props.find((item) => item.id === target.assetId);
    if (asset === undefined) return null;
    const parts = [`道具：${safeLabel(asset.name, "道具")}`];
    const description = validText(asset.description);
    if (description !== null) parts.push(`描述：${description}`);
    return parts.join("\n");
  }
  const storyboard = input.storyboardAssets.find((item) => item.id === target.assetId);
  if (storyboard === undefined) return null;
  const frame = input.chapterContent?.find((candidate) => idList(isRecord(candidate) ? candidate.storyboard : null).includes(target.assetId));
  return validText(isRecord(frame) ? frame.text : null)
    ?? [storyboard.name, storyboard.description].filter((part) => validText(part) !== null).join("\n");
}

export function requiresQuickReferenceFusedRead(
  frame: unknown,
  settings: QuickReferenceSettings[QuickReferenceMode],
): boolean {
  return settings.fused && (settings.character || settings.prop)
    && isRecord(frame) && idList(frame.fused).length > 0;
}

export function buildQuickReferencePlan(input: BuildQuickReferencePlanInput): QuickReferencePlan | null {
  const imageReferences: string[] = [];
  const imageLabels: QuickReferenceEntry[] = [];
  const audioReferences: string[] = [];
  const audioLabels: QuickReferenceEntry[] = [];
  const voiceNotes: string[] = [];
  const addImage = (url: string | null, label: string) => {
    if (url === null || url.trim() === "") return;
    if (!imageReferences.includes(url)) imageReferences.push(url);
    imageLabels.push({ url, label });
  };
  const addAudio = (url: string | null, label: string) => {
    if (url === null || url.trim() === "") return;
    if (!audioReferences.includes(url)) audioReferences.push(url);
    audioLabels.push({ url, label });
  };

  if (input.target.kind === "asset_message" && input.target.assetType !== "storyboard") {
    const description = directAssetDescription(input.mode, input.target, input, audioReferences, audioLabels, voiceNotes);
    if (description === null) return null;
    return { baseDescription: description, imageReferences, imageLabels, audioReferences, audioLabels, voiceNotes };
  }

  const storyboardId = input.target.kind === "frame_message"
    ? input.target.storyboardAssetId
    : input.target.assetId;
  const frame = input.chapterContent?.find((candidate) => (
    idList(isRecord(candidate) ? candidate.storyboard : null).includes(storyboardId)
  )) ?? null;
  if (frame === null) {
    if (input.target.kind === "frame_message") return null;
    const storyboard = input.storyboardAssets.find((item) => item.id === storyboardId);
    if (storyboard === undefined) return null;
    const description = [storyboard.name, storyboard.description]
      .filter((part) => validText(part) !== null)
      .join("\n");
    return { baseDescription: description, imageReferences, imageLabels, audioReferences, audioLabels, voiceNotes };
  }

  const frameRecord = isRecord(frame) ? frame : {};
  const baseDescription = validText(frameRecord.text)
    ?? (input.target.kind === "asset_message"
      ? [input.storyboardAssets.find((item) => item.id === storyboardId)?.name,
        input.storyboardAssets.find((item) => item.id === storyboardId)?.description]
        .filter((part) => validText(part) !== null).join("\n")
      : "");
  const frameStoryboardIds = idList(frameRecord.storyboard);
  const excludedStoryboardId = input.target.kind === "asset_message" ? input.target.assetId : null;
  if (input.settings.storyboard) {
    for (const id of frameStoryboardIds) {
      if (id === excludedStoryboardId) continue;
      const asset = input.storyboardAssets.find((candidate) => candidate.id === id);
      if (asset !== undefined) addImage(asset.image_url, safeLabel(asset.name, "分镜"));
    }
  }

  const frameFuseIds = idList(frameRecord.fused);
  const fuseByType: Record<"character" | "prop", FusedAsset[]> = { character: [], prop: [] };
  if (input.settings.fused && frameFuseIds.length > 0) {
    for (const id of frameFuseIds) {
      const asset = input.fusedAssets.find((candidate) => candidate.id === id);
      if (asset !== undefined && (asset.member_type === "character" || asset.member_type === "prop")
        && asset.image_url !== null && asset.image_url.trim() !== "") {
        fuseByType[asset.member_type].push(asset);
      }
    }
  }

  const processFusedMembers = (type: "character" | "prop", ids: readonly string[]) => {
    if (type === "character" ? !input.settings.character : !input.settings.prop) return new Set<string>();
    const covered = new Set<string>();
    for (const asset of fuseByType[type]) {
      const members = Array.isArray(asset.members) ? asset.members : [];
      const memberNames = members.flatMap((value) => (
        isRecord(value) && value.kind === "asset" && typeof value.id === "string" && ids.includes(value.id)
          ? [safeLabel(typeof value.name === "string" ? value.name : null, value.id)]
          : []
      ));
      const name = safeLabel(asset.name, "融合图");
      const typeName = type === "character" ? "角色" : "道具";
      addImage(asset.image_url, `${typeName}融合图：${name}${memberNames.length > 0 ? `（${memberNames.join("、")}）` : ""}`);
      for (const value of members) {
        if (isRecord(value) && value.kind === "asset" && typeof value.id === "string") covered.add(value.id);
      }
    }
    return covered;
  };

  const collectImages = <T extends { readonly id: string; readonly image_url: string | null }>(
    ids: readonly string[], assets: readonly T[], enabled: boolean, excludedId: string | null,
    label: (asset: T) => string, covered: ReadonlySet<string> = new Set(),
  ) => {
    if (!enabled) return;
    for (const id of ids) {
      if (id === excludedId || covered.has(id)) continue;
      const asset = assets.find((candidate) => candidate.id === id);
      if (asset !== undefined && asset.image_url !== null && asset.image_url.trim() !== "") {
        addImage(asset.image_url, label(asset));
      }
    }
  };

  const characterIds = idList(frameRecord.character);
  const sceneIds = idList(frameRecord.scene);
  const propIds = idList(frameRecord.prop);
  const coveredCharacters = processFusedMembers("character", characterIds);
  collectImages(characterIds, input.characters, input.settings.character,
    input.target.kind === "asset_message" && input.target.assetType === "character" ? input.target.assetId : null,
    (asset) => safeLabel(asset.name, "角色"), coveredCharacters);

  if (input.mode === "video" && input.settings.characterVoice) {
    for (const id of characterIds) {
      const asset = input.characters.find((candidate) => candidate.id === id);
      if (asset === undefined) continue;
      const name = safeLabel(asset.name, "角色");
      const voice = validText(asset.voice_ref);
      if (voice !== null) voiceNotes.push(`${name}：${voice}`);
      addAudio(validText(asset.audio_url), `${name}音频`);
    }
  }

  collectImages(sceneIds, input.scenes, input.settings.scene, null,
    (asset) => safeLabel(asset.title, "场景"));
  const coveredProps = processFusedMembers("prop", propIds);
  collectImages(propIds, input.props, input.settings.prop,
    input.target.kind === "asset_message" && input.target.assetType === "prop" ? input.target.assetId : null,
    (asset) => safeLabel(asset.name, "道具"), coveredProps);

  return { baseDescription, imageReferences, imageLabels, audioReferences, audioLabels, voiceNotes };
}

export function formatQuickReferenceDescription(
  plan: QuickReferencePlan,
  finalImageIndexes: ReadonlyMap<string, number>,
  finalAudioIndexes: ReadonlyMap<string, number>,
): string {
  const sections = [plan.baseDescription.trim()].filter((section) => section !== "");
  const imageLines = plan.imageLabels.flatMap(({ url, label }) => {
    const index = finalImageIndexes.get(url);
    return index === undefined ? [] : [`图片${index} = ${label}`];
  });
  if (imageLines.length > 0) sections.push(`【图片参考】\n${imageLines.join("\n")}`);
  if (plan.voiceNotes.length > 0) sections.push(`【角色音色】\n${plan.voiceNotes.join("\n")}`);
  const audioLines = plan.audioLabels.flatMap(({ url, label }) => {
    const index = finalAudioIndexes.get(url);
    return index === undefined ? [] : [`音频${index} = ${label}`];
  });
  if (audioLines.length > 0) sections.push(`【音频参考】\n${audioLines.join("\n")}`);
  return sections.join("\n\n");
}

export function matchQuickReferenceDuration(
  text: string,
  availableDurations: readonly number[],
): QuickReferenceDurationMatch {
  const pattern = /\b(\d+\.\d+)\b/gu;
  const hits: Array<{ value: number; index: number }> = [];
  for (const match of text.matchAll(pattern)) {
    const value = Number.parseFloat(match[1] ?? "");
    const index = match.index;
    if (Number.isFinite(value) && value >= 0.5 && value <= 300 && index !== undefined) hits.push({ value, index });
  }
  if (hits.length === 0) {
    return { targetDuration: null, duration: null, context: "", message: "未匹配到镜头时长，保留当前手动时长。" };
  }
  const best = hits.reduce((left, right) => left.value > right.value ? left : right);
  const targetDuration = Math.round(best.value);
  const lineStart = text.lastIndexOf("\n", best.index - 1) + 1;
  const foundLineEnd = text.indexOf("\n", best.index);
  const lineEnd = foundLineEnd < 0 ? text.length : foundLineEnd;
  const context = text.slice(lineStart, lineEnd).trim().slice(0, 80);
  const durations = [...new Set(availableDurations.filter((value) => Number.isSafeInteger(value) && value > 0))].sort((a, b) => a - b);
  if (durations.length === 0) {
    return { targetDuration, duration: null, context, message: "当前视频模型没有可用时长选项，保留当前手动时长。" };
  }
  const duration = durations.find((value) => value >= targetDuration) ?? durations[durations.length - 1]!;
  const adjusted = duration === targetDuration ? "" : `，按模型支持的选项调整为 ${duration} 秒`;
  return {
    targetDuration,
    duration,
    context,
    message: `镜头时长识别为 ${targetDuration} 秒${adjusted}。`,
  };
}
