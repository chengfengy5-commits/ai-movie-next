import type { Character, FusedAsset, Prop, Scene, Series, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import { isValidCanvasImageReference } from "../../shared/api/canvasImageGeneration";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import {
  buildQuickReferencePlan,
  formatQuickReferenceDescription,
  matchQuickReferenceDuration,
  type QuickReferencePlan,
  type QuickReferenceSettings,
} from "./quickReference";
import { resolveQuickReferenceStyle, type QuickReferenceStyle } from "./quickReferenceStyle";
import type { BatchVideoChecks } from "./batchVideoPreferences";

export interface BatchVideoModelOptions {
  readonly sizes: readonly string[];
  readonly durations: readonly number[];
  readonly resolutions: readonly string[];
  readonly defaultSize: string;
  readonly defaultResolution: string;
}

export interface BatchVideoPromptParts {
  readonly prompt: string;
  readonly description: string;
  readonly referenceImages: readonly string[];
  readonly referenceAudios: readonly string[];
  readonly trimLevel: 0 | 1 | 2;
}

export interface BatchVideoFrameContext {
  readonly frame: StoryboardFrame;
  readonly plan: QuickReferencePlan;
  readonly style: QuickReferenceStyle;
  readonly referenceImages: readonly string[];
  readonly referenceAudios: readonly string[];
}

export type BatchVideoFrameContextResult =
  | { readonly kind: "ready"; readonly value: BatchVideoFrameContext }
  | { readonly kind: "missing-frame" }
  | { readonly kind: "missing-assets"; readonly names: readonly string[] }
  | { readonly kind: "too-many-references" }
  | { readonly kind: "unsafe-reference" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function ids(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => typeof item === "string" && item !== ""
    ? [item]
    : typeof item === "number" && Number.isSafeInteger(item) ? [String(item)] : []);
}

function safeName(value: string | null | undefined, fallback: string): string {
  const trimmed = value?.trim();
  return trimmed === undefined || trimmed === "" ? fallback : trimmed;
}

function publicReference(value: string | null): value is string {
  return typeof value === "string" && /^https?:\/\//iu.test(value) && isValidCanvasImageReference(value);
}

function isVideoPreview(value: string): boolean {
  try {
    return /\.(?:mp4|webm|mov|m4v|m3u8)$/iu.test(new URL(value).pathname);
  } catch {
    return false;
  }
}

function missingAssetNames<T extends { readonly id: string; readonly image_url: string | null }>(
  frame: StoryboardFrame,
  field: "character" | "scene" | "prop" | "storyboard",
  assets: readonly T[],
  label: (asset: T | undefined) => string,
): string[] {
  const frameRecord = isRecord(frame) ? frame : {};
  const names: string[] = [];
  for (const id of ids(frameRecord[field])) {
    const asset = assets.find((candidate) => candidate.id === id);
    if (asset === undefined || !publicReference(asset.image_url)) names.push(label(asset));
  }
  return names;
}

function assetLabels(frame: StoryboardFrame, category: "character" | "scene" | "prop", assets: readonly (Character | Scene | Prop)[]): string[] {
  const frameRecord = isRecord(frame) ? frame : {};
  return ids(frameRecord[category]).map((id) => {
    const asset = assets.find((candidate) => candidate.id === id);
    if (asset === undefined) return category === "character" ? "角色" : category === "scene" ? "场景" : "道具";
    return category === "scene" ? safeName((asset as Scene).title, "场景") : safeName((asset as Character | Prop).name, category === "character" ? "角色" : "道具");
  });
}

export function batchVideoModelOptions(model: CreatorModel | null): BatchVideoModelOptions {
  let params: Record<string, unknown> = {};
  if (model?.params) {
    try {
      const parsed: unknown = JSON.parse(model.params);
      if (isRecord(parsed)) params = parsed;
    } catch {
      params = {};
    }
  }
  const options = (key: string) => {
    const configured = isRecord(params[key]) ? params[key] as Record<string, unknown> : {};
    if (configured.enabled === false) return { values: [] as unknown[], fallback: undefined as unknown };
    const values = Array.isArray(configured.options) ? [...configured.options] : [];
    const fallback = configured.default;
    if (fallback !== undefined && !values.includes(fallback)) values.push(fallback);
    return { values, fallback };
  };
  const size = options("image_size");
  const duration = options("duration");
  const resolution = options("resolution");
  const strings = (values: unknown[]) => values.filter((value): value is string => typeof value === "string" && value.trim() !== "");
  const durations = duration.values.filter((value): value is number => Number.isSafeInteger(value) && typeof value === "number" && value > 0);
  return {
    sizes: strings(size.values),
    durations,
    resolutions: strings(resolution.values),
    defaultSize: typeof size.fallback === "string" ? size.fallback : "",
    defaultResolution: typeof resolution.fallback === "string" ? resolution.fallback : "",
  };
}

export function suggestedBatchVideoDuration(text: string, durations: readonly number[]): number | null {
  return matchQuickReferenceDuration(text, durations).duration;
}

export function buildBatchVideoFrameContext(input: {
  readonly storyboardId: string;
  readonly chapterContent: readonly StoryboardFrame[] | null;
  readonly storyboardAssets: readonly StoryboardAsset[];
  readonly characters: readonly Character[];
  readonly scenes: readonly Scene[];
  readonly props: readonly Prop[];
  readonly fusedAssets: readonly FusedAsset[];
  readonly checks: BatchVideoChecks;
  readonly quickSettings: QuickReferenceSettings["video"];
  readonly series: Series;
  readonly prompts: readonly CreatorPrompt[];
}): BatchVideoFrameContextResult {
  const matchingFrames = input.chapterContent?.filter((candidate) => (
    ids(isRecord(candidate) ? candidate.storyboard : null).includes(input.storyboardId)
  )) ?? [];
  if (matchingFrames.length !== 1) return { kind: "missing-frame" };
  const frame = matchingFrames[0]!;

  const frameRecord = isRecord(frame) ? frame : {};
  const missing = [
    ...(input.checks.character ? missingAssetNames(frame, "character", input.characters, (asset) => safeName(asset?.name, "未找到角色")) : []),
    ...(input.checks.scene ? missingAssetNames(frame, "scene", input.scenes, (asset) => safeName(asset?.title, "未找到场景")) : []),
    ...(input.checks.prop ? missingAssetNames(frame, "prop", input.props, (asset) => safeName(asset?.name, "未找到道具")) : []),
    ...(input.checks.storyboard ? missingAssetNames(frame, "storyboard", input.storyboardAssets, (asset) => safeName(asset?.name, "未命名分镜")) : []),
    ...((input.checks.character || input.checks.prop) ? ids(frameRecord.fused).flatMap((id) => {
      const fusion = input.fusedAssets.find((candidate) => candidate.id === id);
      const enabled = fusion === undefined
        ? input.checks.character || input.checks.prop
        : fusion.member_type === "prop" ? input.checks.prop : input.checks.character;
      return enabled && (fusion === undefined || !publicReference(fusion.image_url))
        ? [safeName(fusion?.name, "融合图")]
        : [];
    }) : []),
  ];
  if (missing.length > 0) return { kind: "missing-assets", names: [...new Set(missing)] };

  const settings: QuickReferenceSettings["video"] = {
    storyboard: input.checks.storyboard,
    character: input.checks.character,
    scene: input.checks.scene,
    prop: input.checks.prop,
    fused: input.checks.character || input.checks.prop,
    characterVoice: input.checks.character && input.quickSettings.characterVoice,
    matchDuration: input.quickSettings.matchDuration,
  };
  const plan = buildQuickReferencePlan({
    mode: "video",
    target: { kind: "frame_message", storyboardAssetId: input.storyboardId },
    settings,
    chapterContent: input.chapterContent,
    storyboardAssets: input.storyboardAssets,
    characters: input.characters,
    scenes: input.scenes,
    props: input.props,
    fusedAssets: input.fusedAssets,
  });
  if (plan === null) return { kind: "missing-frame" };
  const style = resolveQuickReferenceStyle({
    series: input.series,
    prompts: input.prompts,
    category: "video",
    fallbackCategories: ["storyboard"],
  });
  const preview = typeof frameRecord.preview === "string" && frameRecord.preview.trim() !== ""
    && !isVideoPreview(frameRecord.preview) ? frameRecord.preview : null;
  const planWithPreview: QuickReferencePlan = preview === null || plan.imageReferences.includes(preview)
    ? plan
    : {
      ...plan,
      imageReferences: [preview, ...plan.imageReferences],
      imageLabels: [{ url: preview, label: "分镜预览图" }, ...plan.imageLabels],
    };
  const referenceImages = [...planWithPreview.imageReferences];
  if (style.imageUrl !== null && !referenceImages.includes(style.imageUrl)) referenceImages.push(style.imageUrl);
  if (!referenceImages.every(publicReference) || !planWithPreview.audioReferences.every(publicReference)) {
    return { kind: "unsafe-reference" };
  }
  const anyCategoryOn = input.checks.character || input.checks.scene || input.checks.prop || input.checks.storyboard;
  if (anyCategoryOn && planWithPreview.imageReferences.length === 0) {
    return { kind: "missing-assets", names: ["未绑定任何可提交的带图资产"] };
  }
  if (referenceImages.length > 64 || planWithPreview.audioReferences.length > 5) {
    return { kind: "too-many-references" };
  }
  return {
    kind: "ready",
    value: { frame, plan: planWithPreview, style, referenceImages, referenceAudios: [...planWithPreview.audioReferences] },
  };
}

export function renderBatchVideoPrompt(
  template: CreatorPrompt | null,
  context: BatchVideoFrameContext,
  trimLevel: 0 | 1 | 2,
): BatchVideoPromptParts {
  const templateParts = template === null
    ? []
    : [template.system_prompt, template.user_prompt].filter((value): value is string => typeof value === "string" && value.trim() !== "").map((value) => value.trim());
  const imageIndexes = new Map(context.referenceImages.map((url, index) => [url, index + 1]));
  const audioIndexes = new Map(context.referenceAudios.map((url, index) => [url, index + 1]));
  const planForDisplay: QuickReferencePlan = trimLevel < 2
    ? context.plan
    : { ...context.plan, imageLabels: [], audioLabels: [], voiceNotes: [] };
  const styleForDisplay = trimLevel === 0
    ? context.style
    : { promptText: "", imageUrl: null };
  const description = formatQuickReferenceDescription(planForDisplay, imageIndexes, audioIndexes, styleForDisplay);
  const prompt = [...templateParts, description.trim()].filter(Boolean).join("\n").trim();
  const referenceImages = trimLevel === 0
    ? context.referenceImages
    : context.plan.imageReferences;
  return {
    prompt,
    description,
    referenceImages,
    referenceAudios: context.referenceAudios,
    trimLevel,
  };
}

export function promptCharacterLimit(model: CreatorModel): number {
  try {
    const params: unknown = model.params === null ? null : JSON.parse(model.params);
    const value = isRecord(params) ? params.max_prompt_chars : undefined;
    const parsed = typeof value === "number" ? value
      : typeof value === "string" && /^\d+$/u.test(value) ? Number(value) : NaN;
    return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 4096;
  } catch {
    return 4096;
  }
}

export function estimateBatchVideoCost(
  model: CreatorModel,
  resolution: string,
  durations: readonly (number | null)[],
): { readonly amount: number | null; readonly description: string } {
  if (!model.is_system) {
    return { amount: 0, description: "个人模型的平台积分为 0；不含上游费用" };
  }
  let params: Record<string, unknown> = {};
  try {
    const parsed: unknown = model.params === null ? null : JSON.parse(model.params);
    if (isRecord(parsed)) params = parsed;
  } catch {
    return { amount: null, description: "价格配置无法读取" };
  }
  if (params.billing_mode !== undefined && params.billing_mode !== null
    && params.billing_mode !== "fixed" && params.billing_mode !== "per_second") {
    return { amount: null, description: "计价方式未知" };
  }
  const costValue = (value: unknown): number | null => {
    if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return value;
    if (typeof value === "string" && /^\d+$/u.test(value)) {
      const parsed = Number(value);
      return Number.isSafeInteger(parsed) ? parsed : null;
    }
    return null;
  };
  if (params.aistarslab !== undefined && params.aistarslab !== null && !isRecord(params.aistarslab)) {
    return { amount: null, description: "价格配置无法读取" };
  }
  for (const key of ["fixed_by_quality", "per_second_by_quality"] as const) {
    const rates = params[key];
    if (rates !== undefined && rates !== null && (!isRecord(rates)
      || Object.entries(rates).some(([quality, value]) => quality.trim() === "" || quality.length > 32 || costValue(value) === null))) {
      return { amount: null, description: "价格配置无法读取" };
    }
  }
  if (params.per_second_cost !== undefined && costValue(params.per_second_cost) === null) {
    return { amount: null, description: "价格配置无法读取" };
  }
  const legacyAistarslab = isRecord(params.aistarslab) ? params.aistarslab : {};
  const legacyBillingType = legacyAistarslab.billingType;
  if (legacyBillingType !== undefined && legacyBillingType !== "fixed" && legacyBillingType !== "fixed_total"
    && legacyBillingType !== "per_second") {
    return { amount: null, description: "价格配置无法读取" };
  }
  if (Object.prototype.hasOwnProperty.call(legacyAistarslab, "perSecondCredits")
    && costValue(legacyAistarslab.perSecondCredits) === null) {
    return { amount: null, description: "价格配置无法读取" };
  }
  const legacyPerSecond = params.billing_mode === undefined && legacyAistarslab.billingType === "per_second";
  const perSecond = params.billing_mode === "per_second" || legacyPerSecond;
  const byQualityKey = perSecond ? "per_second_by_quality" : "fixed_by_quality";
  if (!legacyPerSecond && params[byQualityKey] !== undefined && !isRecord(params[byQualityKey])) {
    return { amount: null, description: "价格配置无法读取" };
  }
  const byQuality = isRecord(params[byQualityKey]) ? params[byQualityKey] as Record<string, unknown> : {};
  const canUseByQuality = !legacyPerSecond && (perSecond || params.billing_mode === "fixed");
  const configuredRate = canUseByQuality && resolution !== ""
    ? costValue(byQuality[resolution]) : null;
  if (canUseByQuality && resolution !== "" && Object.prototype.hasOwnProperty.call(byQuality, resolution)
    && configuredRate === null) return { amount: null, description: "价格配置无法读取" };
  const fallbackRate = legacyPerSecond
    ? costValue(legacyAistarslab.perSecondCredits)
    : perSecond ? costValue(params.per_second_cost) : costValue(model.cost_per_call);
  const rate = perSecond ? configuredRate ?? fallbackRate
    : configuredRate !== null && configuredRate > 0 ? configuredRate : fallbackRate;
  if (rate === null) return { amount: null, description: "价格未知" };
  if (perSecond && durations.some((duration) => duration === null)) {
    return { amount: null, description: "尚有镜头未设时长，按秒费用未知" };
  }
  const units = perSecond ? durations.reduce<number>((sum, duration) => sum + (duration ?? 0), 0) : durations.length;
  const amount = rate * units;
  if (!Number.isSafeInteger(amount)) return { amount: null, description: "估算超出安全范围" };
  return {
    amount,
    description: perSecond ? `按秒 ${rate} 积分，共 ${units} 秒` : `按次 ${rate} 积分，共 ${durations.length} 项`,
  };
}

export function batchVideoFrameLabels(frame: StoryboardFrame, characters: readonly Character[], scenes: readonly Scene[], props: readonly Prop[]) {
  return {
    characters: assetLabels(frame, "character", characters),
    scenes: assetLabels(frame, "scene", scenes),
    props: assetLabels(frame, "prop", props),
  };
}
