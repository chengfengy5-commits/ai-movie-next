import { useEffect, useMemo, useRef, useState } from "react";
import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import { CanvasDataError } from "../../shared/api/canvasData";
import { isValidCanvasImageReference } from "../../shared/api/canvasImageGeneration";
import {
  MediaGenerationError,
  type MediaGenerationImageInput,
  type MediaGenerationAssetType,
  type MediaGenerationBatchAssetInput,
  type MediaGenerationVideoInput,
  type ImageGenerationReceipt,
  type MediaGenerationLookupExpectation,
  type MediaGenerationOperation,
  type MediaGenerationReceipt,
  type MediaGenerationRun,
  type MediaGenerationSingleTarget,
  type VideoGenerationReceipt,
} from "../../shared/api/mediaGeneration";
import {
  type UserPreferences,
  type UserPreferencesPort,
} from "../../shared/api/userPreferences";
import {
  BATCH_CHECK_FIELDS,
  collectBatchStoryboardContext,
  defaultBatchGenerationChecks,
  mergeBatchReferenceImages,
  restoreBatchGenerationChecks,
  type BatchCheckField,
  type BatchGenerationChecks,
} from "./batchStoryboardContext";
import {
  MediaReferenceUploadError,
  type MediaReferenceKind,
} from "../../shared/api/mediaReferenceUpload";
import { isSafeCanvasUploadPath, readCanvasImageBlobAsDataUrl } from "../canvas/image-generation/canvasImageReferences";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import type { WorkbenchSourceIntent } from "../series-workbench/sourceIntent";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import {
  buildQuickReferencePlan,
  defaultQuickReferenceSettings,
  findQuickReferenceFrame,
  formatQuickReferenceDescription,
  matchQuickReferenceDuration,
  mergeQuickReferencePreferences,
  quickReferenceEditsMatch,
  QUICK_REFERENCE_FIELDS,
  requiresQuickReferenceFusedRead,
  restoreQuickReferenceSettings,
  type QuickReferenceField,
  type QuickReferenceMode,
  type QuickReferencePreferenceKey,
  type QuickReferenceSettings,
  type QuickReferenceTarget,
} from "./quickReference";
import { resolveQuickReferenceStyle } from "./quickReferenceStyle";
import {
  readSingleMediaPreference,
  readSingleMediaModePreference,
  writeSingleMediaPreference,
  writeSingleMediaModePreference,
  type ImageSelectionPreference,
  type SingleMediaPreferenceCategory,
  type SingleMediaPreferenceMode,
  type SingleMediaSelectionPreference,
  type VideoSelectionPreference,
} from "./mediaSelectionPreferences";
import { QuickReferenceSettingsPanel } from "./QuickReferenceSettingsPanel";
import { BatchVideoGenerationPanel } from "./BatchVideoGenerationPanel";
import "./mediaGeneration.css";

type MediaMode = "image" | "video" | "batch" | "batch-video";
type SingleTargetMode = "standalone" | "frame_message" | "asset_message";
type ReferenceUploadStatusKind = "uploading" | "uploaded" | "failed" | "unknown";
type LibraryAsset = Character | Scene | Prop | StoryboardAsset;

interface ReferenceUploadStatus {
  readonly kind: MediaReferenceKind;
  readonly status: ReferenceUploadStatusKind;
  readonly message: string;
}

interface AssetChoice {
  readonly type: MediaGenerationAssetType;
  readonly id: string;
  readonly name: string;
  readonly label: string;
  readonly description: string;
}

type BatchPreferenceField = "modelId" | "promptId" | "size";

interface BatchGenerationSelection {
  readonly modelId: string;
  readonly promptId: string;
  readonly size: string;
}

type BatchGenerationSelections = Record<MediaGenerationAssetType, BatchGenerationSelection>;
type BatchPreferenceEdits = Record<MediaGenerationAssetType, Set<BatchPreferenceField>>;
type BatchPreferenceWarnings = Record<MediaGenerationAssetType, Partial<Record<BatchPreferenceField, string>>>;

const BATCH_ASSET_TYPES: readonly MediaGenerationAssetType[] = ["character", "scene", "prop", "storyboard"];
const BATCH_PROMPT_CATEGORIES: Readonly<Record<MediaGenerationAssetType, string>> = {
  character: "character-design",
  scene: "scene-design",
  prop: "prop-design",
  storyboard: "storyboard-design",
};
const BATCH_PREFERENCE_KEYS: Readonly<Record<BatchPreferenceField, "model" | "prompt" | "size">> = {
  modelId: "model",
  promptId: "prompt",
  size: "size",
};

function emptyBatchSelections(): BatchGenerationSelections {
  return {
    character: { modelId: "", promptId: "", size: "" },
    scene: { modelId: "", promptId: "", size: "" },
    prop: { modelId: "", promptId: "", size: "" },
    storyboard: { modelId: "", promptId: "", size: "" },
  };
}

function emptyBatchEdits(): BatchPreferenceEdits {
  return { character: new Set(), scene: new Set(), prop: new Set(), storyboard: new Set() };
}

function emptyBatchWarnings(): BatchPreferenceWarnings {
  return { character: {}, scene: {}, prop: {}, storyboard: {} };
}

function defaultBatchSelections(
  models: readonly CreatorModel[],
  prompts: readonly CreatorPrompt[],
): BatchGenerationSelections {
  const defaultModel = models[0] ?? null;
  return Object.fromEntries(BATCH_ASSET_TYPES.map((type) => [
    type,
    {
      modelId: defaultModel?.id ?? "",
      promptId: prompts.find((prompt) => prompt.category === BATCH_PROMPT_CATEGORIES[type])?.id ?? "",
      size: batchDefaultSize(defaultModel),
    },
  ])) as BatchGenerationSelections;
}

function batchDefaultSize(model: CreatorModel | null): string {
  const options = modelOptions(model);
  return options.sizes.includes(options.defaultSize) ? options.defaultSize : options.sizes[0] ?? "";
}

function batchSelectionError(
  type: MediaGenerationAssetType,
  selection: BatchGenerationSelection,
  models: readonly CreatorModel[],
  prompts: readonly CreatorPrompt[],
): string | null {
  const model = models.find((candidate) => candidate.id === selection.modelId) ?? null;
  if (model === null) return `请为${ASSET_TYPE_LABELS[type]}选择图片模型。`;
  if (selection.promptId !== "" && !prompts.some((prompt) => (
    prompt.id === selection.promptId && prompt.category === BATCH_PROMPT_CATEGORIES[type]
  ))) return `请为${ASSET_TYPE_LABELS[type]}重新选择可用提示词。`;
  const sizes = modelOptions(model).sizes;
  if (selection.size !== "" && !sizes.includes(selection.size)) return `请为${ASSET_TYPE_LABELS[type]}重新选择当前模型支持的尺寸。`;
  if (sizes.length > 0 && selection.size === "") return `请为${ASSET_TYPE_LABELS[type]}选择图片尺寸。`;
  return null;
}

function batchPreferenceWarningBlocksSubmission(
  type: MediaGenerationAssetType,
  selection: BatchGenerationSelection,
  warnings: BatchPreferenceWarnings,
  models: readonly CreatorModel[],
): boolean {
  const warning = warnings[type];
  if (warning.modelId !== undefined || warning.promptId !== undefined) return true;
  if (warning.size === undefined) return false;
  const model = models.find((candidate) => candidate.id === selection.modelId) ?? null;
  return modelOptions(model).sizes.length > 0;
}

function mergeBatchPreferenceEdits(
  preferences: UserPreferences,
  selections: BatchGenerationSelections,
  edits: BatchPreferenceEdits,
  checks: BatchGenerationChecks,
  checkEdits: ReadonlySet<BatchCheckField>,
): Record<string, unknown> {
  const savedBatch = record(preferences.batch_gen_prefs) ?? {};
  const nextBatch: Record<string, unknown> = { ...savedBatch };
  if (checkEdits.size > 0) {
    const nextChecks: Record<string, unknown> = { ...(record(savedBatch.checks) ?? {}) };
    for (const field of checkEdits) nextChecks[field] = checks[field];
    nextBatch.checks = nextChecks;
  }
  for (const type of BATCH_ASSET_TYPES) {
    if (edits[type].size === 0) continue;
    const savedType = record(savedBatch[type]) ?? {};
    const nextType: Record<string, unknown> = { ...savedType };
    for (const field of edits[type]) {
      nextType[BATCH_PREFERENCE_KEYS[field]] = selections[type][field];
    }
    nextBatch[type] = nextType;
  }
  return { batch_gen_prefs: nextBatch };
}

function batchPreferenceEditsMatch(
  preferences: UserPreferences,
  selections: BatchGenerationSelections,
  edits: BatchPreferenceEdits,
  checks: BatchGenerationChecks,
  checkEdits: ReadonlySet<BatchCheckField>,
): boolean {
  const savedBatch = record(preferences.batch_gen_prefs);
  if (savedBatch === null) return false;
  const selectionEditsMatch = BATCH_ASSET_TYPES.every((type) => {
    const savedType = record(savedBatch[type]);
    return [...edits[type]].every((field) => (
      savedType?.[BATCH_PREFERENCE_KEYS[field]] === selections[type][field]
    ));
  });
  const savedChecks = record(savedBatch.checks);
  const checkEditsMatch = [...checkEdits].every((field) => savedChecks?.[field] === checks[field]);
  return selectionEditsMatch && checkEditsMatch;
}

const ASSET_TYPE_LABELS: Record<MediaGenerationAssetType, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
  storyboard: "分镜",
};
const IMAGE_PROMPT_CATEGORIES = new Set([
  "character-design",
  "scene-design",
  "prop-design",
  "storyboard-design",
  "image",
  "style",
  "fused",
  "fuse",
  "frame",
]);
const REFERENCE_FILE_ACCEPT: Readonly<Record<MediaReferenceKind, string>> = {
  image: ".jpg,.jpeg,.png,.gif,.webp,.bmp",
  audio: ".mp3,.wav,.ogg,.m4a,.aac,.flac,.wma",
  video: ".mp4,.webm,.mov,.m4v,.avi,.mkv",
};
const REFERENCE_KIND_LABELS: Readonly<Record<MediaReferenceKind, string>> = {
  image: "图片",
  audio: "音频",
  video: "视频",
};
const REFERENCE_COUNT_LIMIT: Readonly<Record<MediaReferenceKind, number>> = {
  image: 64,
  audio: 5,
  video: 5,
};

interface ModelOptions {
  readonly sizes: readonly string[];
  readonly durations: readonly number[];
  readonly resolutions: readonly string[];
  readonly defaultSize: string;
  readonly defaultDuration: number | null;
  readonly defaultResolution: string;
}

interface PendingSubmission {
  readonly operation: MediaGenerationOperation;
  readonly idempotencyKey: string;
  readonly expectation: MediaGenerationLookupExpectation;
  readonly run: MediaGenerationRun;
  readonly signature: string;
  readonly submissionMessage: string;
  readonly operationOwnerId: number;
  readonly ownerKey?: string;
  readonly recoveryUnavailable?: boolean;
  readonly singleGroup?: SingleMediaGenerationGroup;
  readonly singleItemIndex?: number;
}

type SingleMediaGenerationInput = MediaGenerationImageInput | MediaGenerationVideoInput;
type SingleMediaGenerationReceipt = ImageGenerationReceipt | VideoGenerationReceipt;
type SingleMediaGenerationStatus = "waiting" | "submitting" | "accepted" | "unknown" | "refused" | "blocked";

interface SingleMediaTaskStatus {
  readonly status: string;
  readonly progress: number | null;
  readonly progressMessage: string | null;
}

interface SingleMediaGenerationItem {
  readonly index: number;
  readonly input: SingleMediaGenerationInput;
  status: SingleMediaGenerationStatus;
  receipt?: SingleMediaGenerationReceipt;
  message?: string;
  taskStatus?: SingleMediaTaskStatus;
  taskStatusMessage?: string | null;
  taskStatusReading?: boolean;
}

interface SingleMediaGenerationGroup {
  readonly mode: SingleMediaPreferenceMode;
  readonly signature: string;
  readonly ownerKey: string;
  ownerServices: WorkspaceServices;
  operationOwnerId: number;
  run: MediaGenerationRun;
  readonly items: SingleMediaGenerationItem[];
}

interface SingleMediaGenerationGroupView {
  readonly mode: SingleMediaPreferenceMode;
  readonly signature: string;
  readonly ownerKey: string;
  readonly ownerServices: WorkspaceServices;
  readonly operationOwnerId: number;
  readonly items: readonly {
    readonly index: number;
    readonly status: SingleMediaGenerationStatus;
    readonly receipt?: SingleMediaGenerationReceipt;
    readonly message?: string;
    readonly taskStatus?: SingleMediaTaskStatus;
    readonly taskStatusMessage?: string | null;
    readonly taskStatusReading?: boolean;
  }[];
  readonly message: string | null;
}

interface SubmissionOutcome {
  readonly signature: string;
  readonly receipt?: MediaGenerationReceipt;
  readonly message?: string;
  readonly pendingLookup?: boolean;
}

interface ResolvedSingleMediaPreference {
  readonly modelId: string | null;
  readonly promptId: string | null;
  readonly size: string | null;
  readonly generationCount: number;
  readonly duration: string | null;
  readonly resolution: string | null;
  readonly warnings: readonly string[];
}

interface SingleMediaPreferenceOffer {
  readonly contextKey: string;
  readonly mode: SingleMediaPreferenceMode;
  readonly preference: SingleMediaSelectionPreference;
  readonly resolved: ResolvedSingleMediaPreference;
}

interface SingleMediaPreferenceCatalogSnapshot {
  readonly contextKey: string;
  readonly models: readonly CreatorModel[];
  readonly prompts: readonly CreatorPrompt[];
}

interface SingleMediaPreferenceFeedback {
  readonly contextKey: string;
  readonly message: string;
}

interface SingleMediaModePreferenceFeedback {
  readonly contextKey: string;
  readonly message: string;
}

interface MediaGenerationPageProps {
  readonly services: WorkspaceServices;
  readonly userId: string;
  readonly sessionGeneration: number;
  onUnauthorized(): void;
  onOpenTasks(): void;
  readonly workbenchSourceIntent?: WorkbenchSourceIntent | null;
}

interface RequestedWorkbenchSource {
  readonly intent: WorkbenchSourceIntent;
  readonly interactionVersion: number;
  readonly needsConfirmation: boolean;
  readonly confirmed: boolean;
}

function record(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function modelOptions(model: CreatorModel | null): ModelOptions {
  const params = model?.params;
  let root: Record<string, unknown> | null = null;
  if (params !== null && params !== undefined) {
    try {
      root = record(JSON.parse(params) as unknown);
    } catch {
      root = null;
    }
  }
  const option = (name: string): { values: unknown[]; defaultValue: unknown } => {
    const configured = record(root?.[name]);
    if (configured?.enabled === false) return { values: [], defaultValue: undefined };
    const options = Array.isArray(configured?.options) ? configured.options : [];
    const defaultValue = configured?.default;
    return {
      values: defaultValue === undefined || options.includes(defaultValue) ? options : [...options, defaultValue],
      defaultValue,
    };
  };
  const size = option("image_size");
  const duration = option("duration");
  const resolution = option("resolution");
  const strings = (values: unknown[]) => values.filter((value): value is string => typeof value === "string" && value.trim() !== "");
  const durations = duration.values.filter((value): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0);
  return {
    sizes: strings(size.values),
    durations,
    resolutions: strings(resolution.values),
    defaultSize: typeof size.defaultValue === "string" ? size.defaultValue : "",
    defaultDuration: typeof duration.defaultValue === "number" && Number.isSafeInteger(duration.defaultValue)
      ? duration.defaultValue
      : null,
    defaultResolution: typeof resolution.defaultValue === "string" ? resolution.defaultValue : "",
  };
}

function singleMediaPreferenceCategory(
  targetMode: SingleTargetMode,
  assetType: MediaGenerationAssetType,
): SingleMediaPreferenceCategory {
  if (targetMode === "standalone") return "general";
  if (targetMode === "frame_message") return "frame";
  return assetType;
}

function resolveOption(
  savedValue: string,
  options: readonly string[],
  defaultValue: string,
  label: string,
  warnings: string[],
): string {
  if (options.length === 0) {
    if (savedValue !== "") warnings.push(`当前模型没有可用的${label}选项，已忽略记住的值。`);
    return "";
  }
  if (savedValue !== "" && options.includes(savedValue)) return savedValue;
  const fallback = options.includes(defaultValue) ? defaultValue : options[0] ?? "";
  if (savedValue !== "") warnings.push(`记住的${label}已不受当前模型支持，已使用当前模型的可用默认值。`);
  return fallback;
}

function resolveSingleMediaPreference(
  mode: SingleMediaPreferenceMode,
  preference: SingleMediaSelectionPreference,
  models: readonly CreatorModel[],
  modePrompts: readonly CreatorPrompt[],
): ResolvedSingleMediaPreference {
  const saved = mode === "image"
    ? preference as ImageSelectionPreference
    : preference as VideoSelectionPreference;
  const warnings: string[] = [];
  const model = saved.modelId === "" ? null : models.find((candidate) => (
    candidate.id === saved.modelId && candidate.category === mode
  )) ?? null;
  let modelId: string | null = saved.modelId;
  let size: string | null = "";
  let duration: string | null = mode === "video" ? "" : null;
  let resolution: string | null = mode === "video" ? "" : null;

  if (saved.modelId !== "" && model === null) {
    modelId = null;
    size = null;
    duration = null;
    resolution = null;
    warnings.push("记住的模型已不在当前图片/视频模型目录中；请重新选择可用模型。");
  } else if (model !== null) {
    const options = modelOptions(model);
    size = resolveOption(saved.size, options.sizes, options.defaultSize, "尺寸", warnings);
    if (mode === "video") {
      const videoPreference = preference as VideoSelectionPreference;
      duration = resolveOption(
        videoPreference.duration,
        options.durations.map(String),
        options.defaultDuration === null ? "" : String(options.defaultDuration),
        "时长",
        warnings,
      );
      resolution = resolveOption(
        videoPreference.resolution,
        options.resolutions,
        options.defaultResolution,
        "分辨率",
        warnings,
      );
    }
  } else {
    if (saved.size !== "" || (mode === "video"
      && ((preference as VideoSelectionPreference).duration !== "" || (preference as VideoSelectionPreference).resolution !== ""))) {
      warnings.push("记住的选项没有关联模型，已清除尺寸、时长和分辨率。");
    }
    if (mode === "video") {
      duration = "";
      resolution = "";
    }
  }

  let promptId: string | null = saved.promptId;
  if (saved.promptId !== "" && !modePrompts.some((prompt) => prompt.id === saved.promptId)) {
    promptId = null;
    warnings.push("记住的提示词不属于当前图片/视频提示词目录；请手动重新选择。");
  }

  return {
    modelId,
    promptId,
    size,
    generationCount: saved.generationCount ?? 1,
    duration,
    resolution,
    warnings,
  };
}

function promptsForSingleMode(mode: SingleMediaPreferenceMode, prompts: readonly CreatorPrompt[]): CreatorPrompt[] {
  return mode === "image"
    ? prompts.filter((prompt) => IMAGE_PROMPT_CATEGORIES.has(prompt.category))
    : prompts.filter((prompt) => prompt.category === "video-design");
}

function singleMediaSelectionIssue(input: {
  readonly mode: SingleMediaPreferenceMode;
  readonly modelId: string;
  readonly promptId: string;
  readonly size: string;
  readonly duration: string;
  readonly resolution: string;
  readonly models: readonly CreatorModel[];
  readonly prompts: readonly CreatorPrompt[];
}): string | null {
  const model = input.modelId === "" ? null : input.models.find((candidate) => (
    candidate.id === input.modelId && candidate.category === input.mode
  )) ?? null;
  if (input.modelId !== "" && model === null) return "所选模型已不在当前目录中，请重新选择。";
  if (input.promptId !== "" && !promptsForSingleMode(input.mode, input.prompts).some((prompt) => prompt.id === input.promptId)) {
    return "所选提示词已不属于当前图片/视频提示词目录，请重新选择。";
  }
  if (model === null) return null;

  const options = modelOptions(model);
  if (input.size !== "" && !options.sizes.includes(input.size)) {
    return input.mode === "image"
      ? "所选图片尺寸已不受当前模型支持，请重新选择。"
      : "所选画面比例已不受当前模型支持，请重新选择。";
  }
  if (input.mode === "video") {
    if (input.duration !== "" && !options.durations.map(String).includes(input.duration)) {
      return "所选时长已不受当前模型支持，请重新选择。";
    }
    if (input.resolution !== "" && !options.resolutions.includes(input.resolution)) {
      return "所选分辨率已不受当前模型支持，请重新选择。";
    }
  }
  return null;
}

function promptText(prompt: CreatorPrompt): string {
  return [prompt.system_prompt, prompt.user_prompt].filter((part): part is string => typeof part === "string" && part.trim() !== "").join("\n\n");
}

function splitReferences(value: string): string[] {
  return value.split(/\r?\n/u).map((reference) => reference.trim()).filter((reference) => reference !== "");
}

function libraryChoices(
  type: MediaGenerationAssetType,
  values: readonly LibraryAsset[],
): AssetChoice[] {
  return values.map((asset) => {
    const name = "name" in asset ? asset.name : asset.title;
    return {
      type,
      id: asset.id,
      name,
      label: "frame_index" in asset ? `第 ${asset.frame_index + 1} 镜 · ${name}` : name,
      description: asset.description ?? "",
    };
  });
}

function isUnauthorized(error: unknown): boolean {
  return (error instanceof MediaReferenceUploadError && error.kind === "unauthorized")
    || (error instanceof MediaGenerationError && error.kind === "unauthorized")
    || (error instanceof CanvasDataError && error.kind === "unauthorized")
    || (error instanceof ApiError && error.status === 401);
}

function isSafeHttpMediaReference(value: string): boolean {
  return /^https?:\/\//iu.test(value) && isValidCanvasImageReference(value);
}

function receiptMessage(receipt: MediaGenerationReceipt): string {
  if (receipt.operation === "image.single") {
    return `图片任务已受理：${receipt.status} · 服务报价 ${receipt.quotedAmount} 积分 · 计费状态 ${receipt.billingStatus}。`;
  }
  if (receipt.operation === "video.single") {
    return `视频任务已受理：${receipt.status} · 服务报价 ${receipt.quotedAmount} 积分 · 计费状态 ${receipt.billingStatus}。`;
  }
  return `批量请求已回读，共 ${receipt.tasks.length} 项；每项仍可在“我的任务”中查看。`;
}

function singleGenerationStatusLabel(status: SingleMediaGenerationStatus): string {
  switch (status) {
    case "waiting": return "等待提交";
    case "submitting": return "提交中";
    case "accepted": return "已受理";
    case "unknown": return "受理待确认";
    case "refused": return "未受理";
    case "blocked": return "因前项停止而未提交";
  }
}

function describeFailure(error: unknown): string {
  if (error instanceof MediaGenerationError) return error.message;
  if (error instanceof MediaReferenceUploadError) return error.message;
  const normalized = normalizeApiError(error);
  if (normalized.status === 503) return normalized.detail ?? "该媒体生成操作当前未启用。";
  return normalized.detail ?? normalized.message;
}

export function MediaGenerationPage({
  services,
  userId,
  sessionGeneration,
  onUnauthorized,
  onOpenTasks,
  workbenchSourceIntent = null,
}: MediaGenerationPageProps) {
  const [mode, setMode] = useState<MediaMode>("video");
  const [batchVideoActive, setBatchVideoActive] = useState(false);
  const [series, setSeries] = useState<Series[]>([]);
  const [selectedSeriesId, setSelectedSeriesId] = useState("");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [selectedChapterId, setSelectedChapterId] = useState("");
  const [storyboardAssets, setStoryboardAssets] = useState<StoryboardAsset[]>([]);
  const [assetChoices, setAssetChoices] = useState<AssetChoice[]>([]);
  const [characterAssets, setCharacterAssets] = useState<Character[]>([]);
  const [sceneAssets, setSceneAssets] = useState<Scene[]>([]);
  const [propAssets, setPropAssets] = useState<Prop[]>([]);
  const [videoModels, setVideoModels] = useState<CreatorModel[]>([]);
  const [imageModels, setImageModels] = useState<CreatorModel[]>([]);
  const [prompts, setPrompts] = useState<CreatorPrompt[]>([]);
  const [selectedVideoModelId, setSelectedVideoModelId] = useState("");
  const [selectedImageModelId, setSelectedImageModelId] = useState("");
  const [selectedImagePromptId, setSelectedImagePromptId] = useState("");
  const [selectedVideoPromptId, setSelectedVideoPromptId] = useState("");
  const [singleGenerationCounts, setSingleGenerationCounts] = useState<Readonly<Record<string, number>>>({});
  const [singleGenerationGroup, setSingleGenerationGroup] = useState<SingleMediaGenerationGroupView | null>(null);
  const singleGenerationGroupRef = useRef<SingleMediaGenerationGroup | null>(null);
  const singleModeGroupsRef = useRef<Record<SingleMediaPreferenceMode, SingleMediaGenerationGroup | null>>({
    image: null,
    video: null,
  });
  const singleModeGroupViewsRef = useRef<Record<SingleMediaPreferenceMode, SingleMediaGenerationGroupView | null>>({
    image: null,
    video: null,
  });
  const singleGenerationGroupOwnerRef = useRef<number | null>(null);
  const singleTaskStatusReadsRef = useRef(new Set<string>());
  const [singleModePreferenceFeedback, setSingleModePreferenceFeedback] = useState<SingleMediaModePreferenceFeedback | null>(null);
  const [singlePreferenceFeedback, setSinglePreferenceFeedback] = useState<SingleMediaPreferenceFeedback | null>(null);
  const [singlePreferenceOffer, setSinglePreferenceOffer] = useState<SingleMediaPreferenceOffer | null>(null);
  const singlePreferenceCatalogRef = useRef<SingleMediaPreferenceCatalogSnapshot | null>(null);
  const editedSinglePreferenceContextsRef = useRef(new Set<string>());
  const singleModePreferenceContextRef = useRef<string | null>(null);
  const [pendingAction, setPendingAction] = useState(false);
  const batchSelectionsRef = useRef<BatchGenerationSelections>(emptyBatchSelections());
  const [batchSelections, setBatchSelections] = useState<BatchGenerationSelections>(batchSelectionsRef.current);
  const batchPreferenceEditsRef = useRef<BatchPreferenceEdits>(emptyBatchEdits());
  const batchChecksRef = useRef<BatchGenerationChecks>(defaultBatchGenerationChecks());
  const [batchChecks, setBatchChecks] = useState<BatchGenerationChecks>(batchChecksRef.current);
  const batchCheckEditsRef = useRef(new Set<BatchCheckField>());
  const [batchPreferenceWarnings, setBatchPreferenceWarnings] = useState<BatchPreferenceWarnings>(emptyBatchWarnings());
  const [batchPreferenceMessage, setBatchPreferenceMessage] = useState<string | null>(null);
  const [batchPreferenceLoading, setBatchPreferenceLoading] = useState(false);
  const [savingBatchPreferences, setSavingBatchPreferences] = useState(false);
  const savingBatchPreferencesRef = useRef(false);
  const batchPreferenceControllerRef = useRef<AbortController | null>(null);
  const batchPreferenceRestoreGenerationRef = useRef(0);
  const quickReferenceSettingsRef = useRef<QuickReferenceSettings>(defaultQuickReferenceSettings());
  const [quickReferenceSettings, setQuickReferenceSettings] = useState(quickReferenceSettingsRef.current);
  const quickReferenceEditsRef = useRef(new Set<QuickReferencePreferenceKey>());
  const [quickReferenceEdits, setQuickReferenceEdits] = useState<ReadonlySet<QuickReferencePreferenceKey>>(new Set());
  const [quickReferencePreferenceMessage, setQuickReferencePreferenceMessage] = useState<string | null>(null);
  const [quickReferencePreferenceLoading, setQuickReferencePreferenceLoading] = useState(false);
  const [quickReferencePreferenceReadFailed, setQuickReferencePreferenceReadFailed] = useState(false);
  const [savingQuickReferencePreferences, setSavingQuickReferencePreferences] = useState(false);
  const savingQuickReferencePreferencesRef = useRef(false);
  const quickReferencePreferenceControllerRef = useRef<AbortController | null>(null);
  const quickReferenceRestoreGenerationRef = useRef(0);
  const quickReferenceActionControllerRef = useRef<AbortController | null>(null);
  const quickReferenceActionRef = useRef(false);
  const lastWorkbenchIntentEpochRef = useRef<number | null>(null);
  const workbenchTargetInteractionVersionRef = useRef(0);
  const [preparingQuickReference, setPreparingQuickReference] = useState(false);
  const [quickReferenceActionMessage, setQuickReferenceActionMessage] = useState<string | null>(null);
  const catalogReadyRef = useRef(false);
  const [imagePrompt, setImagePrompt] = useState("");
  const [videoPrompt, setVideoPrompt] = useState("");
  const [videoSize, setVideoSize] = useState("");
  const [videoDuration, setVideoDuration] = useState("");
  const [videoResolution, setVideoResolution] = useState("");
  const [imageSize, setImageSize] = useState("");
  const [singleTargetMode, setSingleTargetMode] = useState<SingleTargetMode>("standalone");
  const [singleAssetType, setSingleAssetType] = useState<MediaGenerationAssetType>("character");
  const [singleAssetId, setSingleAssetId] = useState("");
  const [singleStoryboardAssetId, setSingleStoryboardAssetId] = useState("");
  const [selectedBatchKeys, setSelectedBatchKeys] = useState<string[]>([]);
  const [imageReferenceImages, setImageReferenceImages] = useState("");
  const [videoReferenceImages, setVideoReferenceImages] = useState("");
  const [videoReferenceAudios, setVideoReferenceAudios] = useState("");
  const [videoReferenceVideos, setVideoReferenceVideos] = useState("");
  const [imageQuickReferenceImages, setImageQuickReferenceImages] = useState<string[]>([]);
  const [videoQuickReferenceImages, setVideoQuickReferenceImages] = useState<string[]>([]);
  const [videoQuickReferenceAudios, setVideoQuickReferenceAudios] = useState<string[]>([]);
  const [batchReferenceImages, setBatchReferenceImages] = useState("");
  const [referenceFiles, setReferenceFiles] = useState<Partial<Record<MediaReferenceKind, File>>>({});
  const [referenceUploadStatus, setReferenceUploadStatus] = useState<ReferenceUploadStatus | null>(null);
  const [uploadingReference, setUploadingReference] = useState(false);
  const uploadInProgressRef = useRef(false);
  const referenceFileInputs = useRef<Partial<Record<MediaReferenceKind, HTMLInputElement | null>>>({});
  const [loading, setLoading] = useState(true);
  const [chapterLoading, setChapterLoading] = useState(false);
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [chapterError, setChapterError] = useState<string | null>(null);
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [requestedWorkbenchSource, setRequestedWorkbenchSource] = useState<RequestedWorkbenchSource | null>(null);
  const [workbenchSourceNotice, setWorkbenchSourceNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingSubmission | null>(null);
  const pendingRef = useRef<PendingSubmission | null>(null);
  const [requesting, setRequesting] = useState(false);
  const requestingRef = useRef(false);
  const requestingOwnerRef = useRef<number | null>(null);
  const pendingActionOwnerRef = useRef<number | null>(null);
  const activeGenerationOperationRef = useRef<number | null>(null);
  const generationOperationSequenceRef = useRef(0);
  const generationControllersRef = useRef(new Set<AbortController>());
  const activeBatchSubmissionRef = useRef<PendingSubmission | null>(null);
  const generationScopeRef = useRef({ services, userId, sessionGeneration, apiBaseUrl: services.apiBaseUrl });
  const [outcome, setOutcome] = useState<SubmissionOutcome | null>(null);
  const acceptedReceiptContextRef = useRef<{
    readonly userId: string;
    readonly services: WorkspaceServices;
    readonly sessionGeneration: number;
    readonly signature: string;
  } | null>(null);
  const acceptedReceiptContextByModeRef = useRef<{
    image: typeof acceptedReceiptContextRef.current;
    video: typeof acceptedReceiptContextRef.current;
  }>({ image: null, video: null });
  const outcomeByModeRef = useRef<Record<SingleMediaPreferenceMode, SubmissionOutcome | null>>({
    image: null,
    video: null,
  });
  const mountedRef = useRef(false);
  const identityRef = useRef({ userId, services, sessionGeneration, signature: "" });
  const verifiedWorkbenchChaptersRef = useRef<{
    readonly key: string;
    readonly services: WorkspaceServices;
    readonly chapters: Chapter[];
  } | null>(null);
  const verifiedWorkbenchDirectoryRef = useRef<{
    readonly key: string;
    readonly services: WorkspaceServices;
    readonly storyboards: StoryboardAsset[];
    readonly characters: Character[];
    readonly scenes: Scene[];
    readonly props: Prop[];
  } | null>(null);
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  function rememberSingleModePresentation(preferenceMode: SingleMediaPreferenceMode) {
    const activeGroup = singleGenerationGroupRef.current;
    if (activeGroup?.mode === preferenceMode) {
      singleModeGroupsRef.current[preferenceMode] = activeGroup;
    }
    const activeView = singleGenerationGroup?.mode === preferenceMode
      ? singleGenerationGroup
      : singleModeGroupViewsRef.current[preferenceMode];
    if (activeView !== null && activeView !== undefined) {
      singleModeGroupViewsRef.current[preferenceMode] = activeView;
    }
    outcomeByModeRef.current[preferenceMode] = outcome;
    acceptedReceiptContextByModeRef.current[preferenceMode] = acceptedReceiptContextRef.current;
  }

  function restoreSingleModePresentation(preferenceMode: SingleMediaPreferenceMode) {
    const group = singleModeGroupsRef.current[preferenceMode];
    singleGenerationGroupRef.current = group;
    singleGenerationGroupOwnerRef.current = group?.operationOwnerId ?? null;
    setSingleGenerationGroup(singleModeGroupViewsRef.current[preferenceMode]);
    acceptedReceiptContextRef.current = acceptedReceiptContextByModeRef.current[preferenceMode];
    setOutcome(outcomeByModeRef.current[preferenceMode]);
  }

  function hideSingleModePresentation() {
    singleGenerationGroupRef.current = null;
    singleGenerationGroupOwnerRef.current = null;
    setSingleGenerationGroup(null);
    acceptedReceiptContextRef.current = null;
    setOutcome(null);
  }

  const selectedVideoModel = videoModels.find((model) => model.id === selectedVideoModelId) ?? null;
  const selectedImageModel = imageModels.find((model) => model.id === selectedImageModelId) ?? null;
  const singlePreferenceMode = mode === "image" || mode === "video" ? mode : null;
  const currentImagePromptOptions = prompts.filter((prompt) => IMAGE_PROMPT_CATEGORIES.has(prompt.category));
  const currentVideoPromptOptions = prompts.filter((prompt) => prompt.category === "video-design");
  const singleSelectionIssue = singlePreferenceMode === null ? null : singleMediaSelectionIssue({
    mode: singlePreferenceMode,
    modelId: singlePreferenceMode === "image" ? selectedImageModelId : selectedVideoModelId,
    promptId: singlePreferenceMode === "image" ? selectedImagePromptId : selectedVideoPromptId,
    size: singlePreferenceMode === "image" ? imageSize : videoSize,
    duration: videoDuration,
    resolution: videoResolution,
    models: singlePreferenceMode === "image" ? imageModels : videoModels,
    prompts,
  });
  const singlePreferenceCategory = singleMediaPreferenceCategory(singleTargetMode, singleAssetType);
  const singlePreferenceContextKey = JSON.stringify([
    userId,
    services.apiBaseUrl,
    sessionGeneration,
    singlePreferenceMode,
    singlePreferenceCategory,
  ]);
  const otherSinglePreferenceContextKey = JSON.stringify([
    userId,
    services.apiBaseUrl,
    sessionGeneration,
    singlePreferenceMode === "image" ? "video" : "image",
    singlePreferenceCategory,
  ]);
  const singleModePreferenceContextKey = JSON.stringify([
    userId,
    services.apiBaseUrl,
    sessionGeneration,
    singlePreferenceCategory,
  ]);
  const singleOwnerContextKey = JSON.stringify([userId, services.apiBaseUrl, sessionGeneration]);
  const singleGenerationCount = singleGenerationCounts[singlePreferenceContextKey] ?? 1;
  const singleModeHasDraft = selectedImageModelId !== "" || selectedVideoModelId !== ""
    || selectedImagePromptId !== "" || selectedVideoPromptId !== ""
    || imagePrompt.trim() !== "" || videoPrompt.trim() !== ""
    || imageSize !== "" || videoSize !== "" || videoDuration !== "" || videoResolution !== ""
    || singleGenerationCount !== 1
    || imageReferenceImages.trim() !== "" || videoReferenceImages.trim() !== ""
    || videoReferenceAudios.trim() !== "" || videoReferenceVideos.trim() !== ""
    || imageQuickReferenceImages.length > 0 || videoQuickReferenceImages.length > 0 || videoQuickReferenceAudios.length > 0
    || referenceFiles.image !== undefined || referenceFiles.audio !== undefined || referenceFiles.video !== undefined
    || (singlePreferenceMode !== null && (editedSinglePreferenceContextsRef.current.has(singlePreferenceContextKey)
      || editedSinglePreferenceContextsRef.current.has(otherSinglePreferenceContextKey)));
  const selectedSeries = series.find((item) => item.id === selectedSeriesId) ?? null;
  const selectedChapter = chapters.find((item) => (
    item.id === selectedChapterId && item.series_id === selectedSeriesId
  )) ?? null;
  const selectedDirectoryKey = JSON.stringify([
    userId,
    services.apiBaseUrl,
    sessionGeneration,
    selectedSeriesId,
    selectedChapterId,
  ]);
  const selectedChaptersKey = JSON.stringify([userId, services.apiBaseUrl, sessionGeneration, selectedSeriesId]);
  const videoOptions = useMemo(() => modelOptions(selectedVideoModel), [selectedVideoModel]);
  const imageOptions = useMemo(() => modelOptions(selectedImageModel), [selectedImageModel]);
  const sortedChapters = useMemo(() => [...chapters].sort((left, right) => left.order - right.order), [chapters]);
  const singleAssetChoices = assetChoices.filter((asset) => asset.type === singleAssetType);
  const selectedBatchAssets = selectedBatchKeys.flatMap((key) => {
    const asset = assetChoices.find((choice) => `${choice.type}:${choice.id}` === key);
    return asset === undefined ? [] : [asset];
  });
  const publicBatchReferences = splitReferences(batchReferenceImages);
  const selectedBatchStoryboardContexts = selectedBatchAssets
    .filter((asset) => asset.type === "storyboard")
    .map((asset) => ({
      asset,
      context: collectBatchStoryboardContext({
        storyboardId: asset.id,
        fallbackDescription: asset.description || asset.name,
        chapterContent: selectedChapter?.content ?? null,
        characters: characterAssets,
        scenes: sceneAssets,
        props: propAssets,
        checks: batchChecks,
        publicReferences: publicBatchReferences,
      }),
    }));
  const batchStoryboardContextByKey = new Map(selectedBatchStoryboardContexts.map(({ asset, context }) => [
    `${asset.type}:${asset.id}`,
    context,
  ]));
  const batchReferenceIssues = [
    ...selectedBatchStoryboardContexts.flatMap(({ asset, context }) => (
      context.missingNames.length === 0
        ? []
        : [`${asset.label}缺少引用图片：${context.missingNames.join("、")}。`]
    )),
    ...selectedBatchAssets.flatMap((asset) => {
      const context = batchStoryboardContextByKey.get(`${asset.type}:${asset.id}`);
      const referenceCount = context?.referenceImages.length ?? mergeBatchReferenceImages(publicBatchReferences).length;
      return referenceCount > 64 ? [`${asset.label}的参考图片超过64项上限，请减少公共或关联图片。`] : [];
    }),
  ];
  const formSignature = JSON.stringify({
    mode,
    selectedSeriesId,
    selectedChapterId,
    selectedVideoModelId,
    selectedImageModelId,
    selectedImagePromptId,
    selectedVideoPromptId,
    singleGenerationCount,
    batchSelections,
    batchChecks,
    batchStoryboardContexts: selectedBatchStoryboardContexts.map(({ asset, context }) => ({
      assetId: asset.id,
      description: context.description,
      referenceImages: context.referenceImages,
      missingNames: context.missingNames,
    })),
    imagePrompt,
    videoPrompt,
    videoSize,
    videoDuration,
    videoResolution,
    imageSize,
    singleTargetMode,
    singleAssetType,
    singleAssetId,
    singleStoryboardAssetId,
    selectedBatchKeys,
    imageReferenceImages,
    videoReferenceImages,
    videoReferenceAudios,
    videoReferenceVideos,
    imageQuickReferenceImages,
    videoQuickReferenceImages,
    videoQuickReferenceAudios,
    batchReferenceImages,
  });
  identityRef.current = { userId, services, sessionGeneration, signature: formSignature };
  const displayedSingleGenerationGroup = singleGenerationGroup?.ownerKey === singleOwnerContextKey
    ? singleGenerationGroup
    : null;
  const showSingleGenerationGroup = displayedSingleGenerationGroup !== null && (
    displayedSingleGenerationGroup.items.length > 1
    || displayedSingleGenerationGroup.signature !== formSignature
    || (requesting && pending === null)
    || pending?.singleGroup?.operationOwnerId === displayedSingleGenerationGroup.operationOwnerId
    || services.getTaskReceipt !== undefined
    || displayedSingleGenerationGroup.items.some((item) => item.taskStatus !== undefined)
  );
  const displayedOutcome = outcome?.signature === formSignature
    && !(displayedSingleGenerationGroup !== null
      && displayedSingleGenerationGroup.items.length > 1 && outcome.receipt === undefined)
    ? outcome
    : null;

  function updatePending(value: PendingSubmission | null) {
    pendingRef.current = value;
    setPending(value);
  }

  function recordWorkbenchTargetInteraction() {
    workbenchTargetInteractionVersionRef.current += 1;
    verifiedWorkbenchChaptersRef.current = null;
    verifiedWorkbenchDirectoryRef.current = null;
    setRequestedWorkbenchSource(null);
  }

  function pendingBelongsToOperation(ownerId: number): boolean {
    const current = pendingRef.current as PendingSubmission | null;
    return current !== null && current.operationOwnerId === ownerId;
  }

  function beginCurrentScopeRun(): MediaGenerationRun | null {
    const port = services.mediaGeneration;
    if (port === undefined) return null;
    const capturedServices = services;
    const capturedUserId = userId;
    const capturedSessionGeneration = sessionGeneration;
    const capturedApiBaseUrl = services.apiBaseUrl;
    return port.beginRun({
      actorId: userId,
      isContextCurrent: () => mountedRef.current
        && identityRef.current.userId === capturedUserId
        && identityRef.current.services === capturedServices
        && identityRef.current.sessionGeneration === capturedSessionGeneration
        && identityRef.current.services.apiBaseUrl === capturedApiBaseUrl,
    });
  }

  function beginGenerationOperation(): number {
    const ownerId = ++generationOperationSequenceRef.current;
    activeGenerationOperationRef.current = ownerId;
    return ownerId;
  }

  function isGenerationOperationCurrent(ownerId: number, run: MediaGenerationRun): boolean {
    return activeGenerationOperationRef.current === ownerId && run.isCurrent();
  }

  function updateRequesting(ownerId: number, value: boolean) {
    if (value) {
      if (activeGenerationOperationRef.current !== ownerId) return;
      requestingOwnerRef.current = ownerId;
      requestingRef.current = true;
      setRequesting(true);
      return;
    }
    if (requestingOwnerRef.current !== ownerId) return;
    requestingOwnerRef.current = null;
    requestingRef.current = false;
    setRequesting(false);
  }

  function beginPendingAction(ownerId: number): boolean {
    if (activeGenerationOperationRef.current !== ownerId || pendingActionOwnerRef.current !== null) return false;
    pendingActionOwnerRef.current = ownerId;
    setPendingAction(true);
    return true;
  }

  function releasePendingAction(ownerId: number) {
    if (pendingActionOwnerRef.current !== ownerId) return;
    pendingActionOwnerRef.current = null;
    setPendingAction(false);
  }

  function finishGenerationOperation(ownerId: number) {
    if (activeGenerationOperationRef.current !== ownerId) return;
    if (pendingRef.current?.operationOwnerId === ownerId) updatePending(null);
    if (activeBatchSubmissionRef.current?.operationOwnerId === ownerId) activeBatchSubmissionRef.current = null;
    releasePendingAction(ownerId);
    updateRequesting(ownerId, false);
    activeGenerationOperationRef.current = null;
  }

  function makeGenerationController(): AbortController {
    const controller = new AbortController();
    generationControllersRef.current.add(controller);
    return controller;
  }

  function releaseGenerationController(controller: AbortController) {
    generationControllersRef.current.delete(controller);
  }

  function markSinglePreferenceEdited(contextKey = singlePreferenceContextKey) {
    editedSinglePreferenceContextsRef.current.add(contextKey);
    setSinglePreferenceOffer(null);
  }

  function setSinglePreferenceMessage(message: string | null, contextKey = singlePreferenceContextKey) {
    setSinglePreferenceFeedback(message === null ? null : { contextKey, message });
  }

  function persistSinglePreference(
    preferenceMode: SingleMediaPreferenceMode,
    preference: ImageSelectionPreference | VideoSelectionPreference,
  ) {
    const scope = { apiBaseUrl: services.apiBaseUrl, userId };
    const preferenceWithCount = {
      ...preference,
      generationCount: preference.generationCount ?? singleGenerationCount,
    };
    const saved = preferenceMode === "image"
      ? writeSingleMediaPreference(scope, "image", singlePreferenceCategory, preferenceWithCount as ImageSelectionPreference)
      : writeSingleMediaPreference(scope, "video", singlePreferenceCategory, preferenceWithCount as VideoSelectionPreference);
    setSinglePreferenceMessage(saved
      ? "已记住当前分类的生成选项。"
      : "浏览器未能保存此分类的生成选项；当前表单仍可继续使用。",
    );
  }

  function applyResolvedSinglePreference(
    offer: SingleMediaPreferenceOffer,
    automatic: boolean,
  ): boolean {
    if (offer.contextKey !== singlePreferenceContextKey || offer.mode !== singlePreferenceMode
      || pendingRef.current !== null || requestingRef.current || uploadInProgressRef.current
      || quickReferenceActionRef.current || savingQuickReferencePreferencesRef.current
      || savingBatchPreferencesRef.current) return false;
    const models = offer.mode === "image" ? imageModels : videoModels;
    const promptsInMode = promptsForSingleMode(offer.mode, prompts);
    const resolved = resolveSingleMediaPreference(offer.mode, offer.preference, models, promptsInMode);
    const selectedPrompt = resolved.promptId === null || resolved.promptId === ""
      ? null
      : promptsInMode.find((prompt) => prompt.id === resolved.promptId) ?? null;
    const nextPromptText = selectedPrompt === null ? null : promptText(selectedPrompt);
    const currentPromptText = offer.mode === "image" ? imagePrompt : videoPrompt;
    if (!automatic && nextPromptText !== null && currentPromptText.trim() !== "" && currentPromptText !== nextPromptText) {
      if (!window.confirm("应用已记住的设置会替换当前提示词。是否继续？")) return false;
    }

    if (offer.mode === "image") {
      if (resolved.modelId !== null) setSelectedImageModelId(resolved.modelId);
      if (resolved.promptId !== null) {
        setSelectedImagePromptId(resolved.promptId);
        if (nextPromptText !== null) setImagePrompt(nextPromptText);
      }
      if (resolved.size !== null) setImageSize(resolved.size);
    } else {
      if (resolved.modelId !== null) setSelectedVideoModelId(resolved.modelId);
      if (resolved.promptId !== null) {
        setSelectedVideoPromptId(resolved.promptId);
        if (nextPromptText !== null) setVideoPrompt(nextPromptText);
      }
      if (resolved.size !== null) setVideoSize(resolved.size);
      if (resolved.duration !== null) setVideoDuration(resolved.duration);
      if (resolved.resolution !== null) setVideoResolution(resolved.resolution);
    }
    setSingleGenerationCounts((current) => ({
      ...current,
      [singlePreferenceContextKey]: resolved.generationCount,
    }));

    editedSinglePreferenceContextsRef.current.add(offer.contextKey);
    setSinglePreferenceOffer(null);
    setSinglePreferenceMessage(resolved.warnings.length > 0
      ? `已应用可用设置；${resolved.warnings.join(" ")}`
      : `已应用此分类记住的${offer.mode === "image" ? "图片" : "视频"}选项。`, offer.contextKey);
    return true;
  }

  function applyRememberedSinglePreference() {
    if (singlePreferenceOffer === null || isFrozen) return;
    applyResolvedSinglePreference(singlePreferenceOffer, false);
  }

  function changeSingleModel(preferenceMode: SingleMediaPreferenceMode, modelId: string) {
    markSinglePreferenceEdited();
    if (preferenceMode === "image") {
      const model = imageModels.find((candidate) => candidate.id === modelId) ?? null;
      const size = model === null ? "" : modelOptions(model).defaultSize;
      setSelectedImageModelId(modelId);
      setImageSize(size);
      setSingleGenerationCounts((current) => ({ ...current, [singlePreferenceContextKey]: 1 }));
      persistSinglePreference("image", { modelId, promptId: selectedImagePromptId, size, generationCount: 1 });
      return;
    }
    const model = videoModels.find((candidate) => candidate.id === modelId) ?? null;
    const options = modelOptions(model);
    const size = model === null ? "" : options.defaultSize;
    const duration = model === null || options.defaultDuration === null ? "" : String(options.defaultDuration);
    const resolution = model === null ? "" : options.defaultResolution;
    setSelectedVideoModelId(modelId);
    setVideoSize(size);
    setVideoDuration(duration);
    setVideoResolution(resolution);
    setSingleGenerationCounts((current) => ({ ...current, [singlePreferenceContextKey]: 1 }));
    persistSinglePreference("video", { modelId, promptId: selectedVideoPromptId, size, duration, resolution, generationCount: 1 });
  }

  function changeSingleGenerationCount(value: string) {
    const next = Number(value);
    if (!Number.isInteger(next) || next < 1 || next > 4 || singlePreferenceMode === null) return;
    markSinglePreferenceEdited();
    setSingleGenerationCounts((current) => ({ ...current, [singlePreferenceContextKey]: next }));
    if (singlePreferenceMode === "image") {
      persistSinglePreference("image", {
        modelId: selectedImageModelId,
        promptId: selectedImagePromptId,
        size: imageSize,
        generationCount: next,
      });
      return;
    }
    persistSinglePreference("video", {
      modelId: selectedVideoModelId,
      promptId: selectedVideoPromptId,
      size: videoSize,
      duration: videoDuration,
      resolution: videoResolution,
      generationCount: next,
    });
  }

  function changeSinglePrompt(preferenceMode: SingleMediaPreferenceMode, promptId: string) {
    markSinglePreferenceEdited();
    if (preferenceMode === "image") {
      selectImagePrompt(promptId);
      persistSinglePreference("image", { modelId: selectedImageModelId, promptId, size: imageSize });
      return;
    }
    selectVideoPrompt(promptId);
    persistSinglePreference("video", {
      modelId: selectedVideoModelId,
      promptId,
      size: videoSize,
      duration: videoDuration,
      resolution: videoResolution,
    });
  }

  function changeSingleSize(preferenceMode: SingleMediaPreferenceMode, size: string) {
    markSinglePreferenceEdited();
    if (preferenceMode === "image") {
      setImageSize(size);
      persistSinglePreference("image", { modelId: selectedImageModelId, promptId: selectedImagePromptId, size });
      return;
    }
    setVideoSize(size);
    persistSinglePreference("video", {
      modelId: selectedVideoModelId,
      promptId: selectedVideoPromptId,
      size,
      duration: videoDuration,
      resolution: videoResolution,
    });
  }

  function changeVideoDuration(duration: string) {
    markSinglePreferenceEdited();
    setVideoDuration(duration);
    persistSinglePreference("video", {
      modelId: selectedVideoModelId,
      promptId: selectedVideoPromptId,
      size: videoSize,
      duration,
      resolution: videoResolution,
    });
  }

  function changeVideoResolution(resolution: string) {
    markSinglePreferenceEdited();
    setVideoResolution(resolution);
    persistSinglePreference("video", {
      modelId: selectedVideoModelId,
      promptId: selectedVideoPromptId,
      size: videoSize,
      duration: videoDuration,
      resolution,
    });
  }

  function updateBatchSelection(
    type: MediaGenerationAssetType,
    field: BatchPreferenceField,
    value: string,
  ) {
    const current = batchSelectionsRef.current;
    const nextSelection = { ...current[type], [field]: value };
    const changedFields = new Set<BatchPreferenceField>([field]);
    if (field === "modelId") {
      const model = imageModels.find((candidate) => candidate.id === value) ?? null;
      nextSelection.size = batchDefaultSize(model);
      changedFields.add("size");
    }
    batchSelectionsRef.current = { ...current, [type]: nextSelection };
    setBatchSelections(batchSelectionsRef.current);
    for (const changedField of changedFields) batchPreferenceEditsRef.current[type].add(changedField);
    setBatchPreferenceWarnings((warnings) => {
      const nextWarnings = { ...warnings, [type]: { ...warnings[type] } };
      for (const changedField of changedFields) delete nextWarnings[type][changedField];
      return nextWarnings;
    });
    setBatchPreferenceMessage(null);
  }

  function updateBatchCheck(field: BatchCheckField, checked: boolean) {
    const next = { ...batchChecksRef.current, [field]: checked };
    batchChecksRef.current = next;
    setBatchChecks(next);
    batchCheckEditsRef.current.add(field);
    setBatchPreferenceMessage(null);
  }

  function updateQuickReferenceSetting(modeName: QuickReferenceMode, field: QuickReferenceField, checked: boolean) {
    if (savingQuickReferencePreferencesRef.current || quickReferenceActionRef.current) return;
    const next = {
      ...quickReferenceSettingsRef.current,
      [modeName]: { ...quickReferenceSettingsRef.current[modeName], [field]: checked },
    };
    const key = `${modeName}:${field}` as QuickReferencePreferenceKey;
    const edits = new Set(quickReferenceEditsRef.current);
    edits.add(key);
    quickReferenceSettingsRef.current = next;
    quickReferenceEditsRef.current = edits;
    setQuickReferenceSettings(next);
    setQuickReferenceEdits(edits);
    setQuickReferencePreferenceMessage(null);
  }

  function preferenceScopeIsCurrent(
    capturedServices: WorkspaceServices,
    capturedUserId: string,
    capturedSessionGeneration: number,
  ): boolean {
    return mountedRef.current
      && identityRef.current.services === capturedServices
      && identityRef.current.userId === capturedUserId
      && identityRef.current.sessionGeneration === capturedSessionGeneration;
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationOperationSequenceRef.current += 1;
      activeGenerationOperationRef.current = null;
      activeBatchSubmissionRef.current = null;
      requestingOwnerRef.current = null;
      pendingActionOwnerRef.current = null;
      for (const controller of generationControllersRef.current) controller.abort();
      generationControllersRef.current.clear();
      singleTaskStatusReadsRef.current.clear();
      const actionController = quickReferenceActionControllerRef.current;
      quickReferenceActionControllerRef.current = null;
      quickReferenceActionRef.current = false;
      actionController?.abort();
    };
  }, []);

  useEffect(() => {
    const previous = generationScopeRef.current;
    const identityChanged = previous.userId !== userId
      || previous.sessionGeneration !== sessionGeneration
      || previous.apiBaseUrl !== services.apiBaseUrl;
    const servicesChanged = previous.services !== services;
    if (!identityChanged && !servicesChanged) return;
    generationScopeRef.current = { services, userId, sessionGeneration, apiBaseUrl: services.apiBaseUrl };
    generationOperationSequenceRef.current += 1;
    activeGenerationOperationRef.current = null;
    for (const controller of generationControllersRef.current) controller.abort();
    generationControllersRef.current.clear();
    requestingOwnerRef.current = null;
    requestingRef.current = false;
    setRequesting(false);
    pendingActionOwnerRef.current = null;
    setPendingAction(false);
    singleTaskStatusReadsRef.current.clear();
    singleModePreferenceContextRef.current = null;
    singlePreferenceCatalogRef.current = null;
    editedSinglePreferenceContextsRef.current.clear();
    if (identityChanged) {
      pendingRef.current = null;
      setPending(null);
      activeBatchSubmissionRef.current = null;
      singleGenerationGroupRef.current = null;
      singleGenerationGroupOwnerRef.current = null;
      singleModeGroupsRef.current = { image: null, video: null };
      singleModeGroupViewsRef.current = { image: null, video: null };
      acceptedReceiptContextRef.current = null;
      acceptedReceiptContextByModeRef.current = { image: null, video: null };
      outcomeByModeRef.current = { image: null, video: null };
      setSingleGenerationGroup(null);
      setOutcome(null);
      return;
    }

    const previousPending = pendingRef.current;
    const group = singleGenerationGroupRef.current ?? previousPending?.singleGroup ?? null;
    for (const preferenceMode of ["image", "video"] as const) {
      const storedGroup = singleModeGroupsRef.current[preferenceMode];
      if (storedGroup !== null && storedGroup !== group) {
        storedGroup.ownerServices = services;
        const storedView = singleModeGroupViewsRef.current[preferenceMode];
        if (storedView !== null && storedView.operationOwnerId === storedGroup.operationOwnerId) {
          singleModeGroupViewsRef.current[preferenceMode] = { ...storedView, ownerServices: services };
        }
      }
      const accepted = acceptedReceiptContextByModeRef.current[preferenceMode];
      if (accepted !== null) {
        acceptedReceiptContextByModeRef.current[preferenceMode] = { ...accepted, services };
      }
    }
    if (acceptedReceiptContextRef.current !== null) {
      acceptedReceiptContextRef.current = { ...acceptedReceiptContextRef.current, services };
    }
    const activeBatchSubmission = activeBatchSubmissionRef.current;
    const needsRecoveryRun = previousPending !== null
      || activeBatchSubmission !== null
      || group?.items.some((item) => item.status === "submitting") === true;
    let nextRun: MediaGenerationRun | null = null;
    let runFailureMessage: string | null = null;
    if (needsRecoveryRun) {
      try {
        nextRun = beginCurrentScopeRun();
        if (nextRun === null) runFailureMessage = "当前服务没有媒体生成恢复入口；原请求仍保留，停止本地跟踪前不会继续提交。";
      } catch (error) {
        runFailureMessage = `当前服务无法建立媒体生成恢复入口：${describeFailure(error)} 原请求仍保留。`;
      }
    }

    if (group !== null && group.ownerKey === singleOwnerContextKey) {
      const operationOwnerId = ++generationOperationSequenceRef.current;
      const priorRun = group.run;
      const submittingIndex = group.items.findIndex((item) => item.status === "submitting");
      group.ownerServices = services;
      group.operationOwnerId = operationOwnerId;
      if (nextRun !== null) group.run = nextRun;
      let groupMessage = runFailureMessage;
      let nextPending: PendingSubmission | null = null;
      const existingGroupPending = previousPending?.singleGroup === group ? previousPending : null;

      if (submittingIndex >= 0) {
        const item = group.items[submittingIndex];
        if (item !== undefined) {
          item.status = "unknown";
          item.message = "服务已更新；原提交结果未知，保留原请求供核对。";
          stopSingleGenerationItems(group, submittingIndex + 1);
          const operation = group.mode === "image" ? "image.single" : "video.single";
          nextPending = {
            operation,
            idempotencyKey: item.input.idempotencyKey,
            expectation: { operation, idempotencyKey: item.input.idempotencyKey },
            run: nextRun ?? priorRun,
            signature: group.signature,
            submissionMessage: item.message,
            operationOwnerId,
            recoveryUnavailable: nextRun === null,
            singleGroup: group,
            singleItemIndex: submittingIndex,
          };
          groupMessage ??= "服务已更新；本项提交仍待确认，后续项目已停止。";
        }
      } else if (existingGroupPending !== null) {
        nextPending = {
          ...existingGroupPending,
          run: nextRun ?? existingGroupPending.run,
          operationOwnerId,
          recoveryUnavailable: nextRun === null,
          singleGroup: group,
        };
      } else if (activeBatchSubmission !== null) {
        nextPending = {
          ...activeBatchSubmission,
          run: nextRun ?? activeBatchSubmission.run,
          operationOwnerId,
          recoveryUnavailable: nextRun === null,
        };
        activeBatchSubmissionRef.current = null;
      }

      if (nextPending !== null) {
        activeGenerationOperationRef.current = operationOwnerId;
        updatePending(nextPending);
        setOutcome({
          signature: group.signature,
          message: runFailureMessage
            ?? "服务已更新；本项仍待确认。请核对原操作，系统不会自动重新提交。",
          pendingLookup: true,
        });
      } else if (previousPending !== null && previousPending.singleGroup === undefined) {
        const pendingOwnerId = ++generationOperationSequenceRef.current;
        const reboundPending = {
          ...previousPending,
          run: nextRun ?? previousPending.run,
          operationOwnerId: pendingOwnerId,
          recoveryUnavailable: nextRun === null,
        };
        activeGenerationOperationRef.current = pendingOwnerId;
        updatePending(reboundPending);
        setOutcome({
          signature: reboundPending.signature,
          message: runFailureMessage
            ?? "服务已更新；本次提交仍待确认。请核对原操作，系统不会自动重新提交。",
          pendingLookup: true,
        });
      } else {
        activeGenerationOperationRef.current = null;
        if (previousPending !== null) updatePending(null);
      }
      publishSingleGenerationGroup(group, groupMessage);
      return;
    }

    if (activeBatchSubmission !== null) {
      const operationOwnerId = ++generationOperationSequenceRef.current;
      const reboundPending = {
        ...activeBatchSubmission,
        run: nextRun ?? activeBatchSubmission.run,
        operationOwnerId,
        recoveryUnavailable: nextRun === null,
      };
      activeBatchSubmissionRef.current = null;
      activeGenerationOperationRef.current = operationOwnerId;
      updatePending(reboundPending);
      setOutcome({
        signature: reboundPending.signature,
        message: runFailureMessage
          ?? "服务已更新；批量请求仍待确认。请核对原操作，系统不会自动重新提交。",
        pendingLookup: true,
      });
    } else if (previousPending !== null) {
      const operationOwnerId = ++generationOperationSequenceRef.current;
      const reboundPending = {
        ...previousPending,
        run: nextRun ?? previousPending.run,
        operationOwnerId,
        recoveryUnavailable: nextRun === null,
      };
      activeGenerationOperationRef.current = operationOwnerId;
      updatePending(reboundPending);
      setOutcome({
        signature: reboundPending.signature,
        message: runFailureMessage
          ?? "服务已更新；本次提交仍待确认。请核对原操作，系统不会自动重新提交。",
        pendingLookup: true,
      });
    } else {
      activeGenerationOperationRef.current = null;
    }
  }, [services, services.apiBaseUrl, sessionGeneration, userId]);

  useEffect(() => {
    batchPreferenceControllerRef.current?.abort();
    batchPreferenceControllerRef.current = null;
    savingBatchPreferencesRef.current = false;
    batchSelectionsRef.current = emptyBatchSelections();
    setBatchSelections(batchSelectionsRef.current);
    batchPreferenceEditsRef.current = emptyBatchEdits();
    batchChecksRef.current = defaultBatchGenerationChecks();
    setBatchChecks(batchChecksRef.current);
    batchCheckEditsRef.current = new Set();
    setBatchPreferenceWarnings(emptyBatchWarnings());
    setBatchPreferenceMessage(null);
    setBatchPreferenceLoading(false);
    setSavingBatchPreferences(false);
  }, [services, sessionGeneration, userId]);

  useEffect(() => {
    quickReferenceRestoreGenerationRef.current += 1;
    quickReferencePreferenceControllerRef.current?.abort();
    quickReferencePreferenceControllerRef.current = null;
    const actionController = quickReferenceActionControllerRef.current;
    quickReferenceActionControllerRef.current = null;
    quickReferenceActionRef.current = false;
    actionController?.abort();
    savingQuickReferencePreferencesRef.current = false;
    quickReferenceSettingsRef.current = defaultQuickReferenceSettings();
    setQuickReferenceSettings(quickReferenceSettingsRef.current);
    quickReferenceEditsRef.current = new Set();
    setQuickReferenceEdits(quickReferenceEditsRef.current);
    setQuickReferencePreferenceMessage(null);
    setQuickReferencePreferenceLoading(false);
    setQuickReferencePreferenceReadFailed(false);
    setSavingQuickReferencePreferences(false);
    setPreparingQuickReference(false);
    setQuickReferenceActionMessage(null);
  }, [services, sessionGeneration, userId]);

  useEffect(() => {
    if (singlePreferenceMode === null || loading || pendingRef.current !== null || requestingRef.current
      || uploadInProgressRef.current || quickReferenceActionRef.current
      || savingQuickReferencePreferencesRef.current || savingBatchPreferencesRef.current) return;

    const contextKey = singleModePreferenceContextKey;
    if (singleModePreferenceContextRef.current === contextKey) return;
    singleModePreferenceContextRef.current = contextKey;

    const read = readSingleMediaModePreference(
      { apiBaseUrl: services.apiBaseUrl, userId },
      singlePreferenceCategory,
    );
    if (read.kind === "unavailable" || read.kind === "invalid") {
      setSingleModePreferenceFeedback({
        contextKey,
        message: read.kind === "unavailable"
          ? "浏览器无法读取当前分类记住的图片/视频模式；当前模式保持不变。"
          : "当前分类记住的图片/视频模式无效；当前模式保持不变。",
      });
      return;
    }
    if (read.kind === "found" && (read.value === "image" || read.value === "video")) {
      if (read.value !== mode && singleModeHasDraft) {
        setSingleModePreferenceFeedback({
          contextKey,
          message: "当前表单已有编辑或参考内容；为保留当前输入，暂不自动切换到此分类记住的模式。可手动选择图片或视频。",
        });
        return;
      }
      if (read.value !== mode) {
        if (mode === "image" || mode === "video") rememberSingleModePresentation(mode);
        restoreSingleModePresentation(read.value);
        setMode(read.value);
      }
      setSingleModePreferenceFeedback({
        contextKey,
        message: `已恢复当前分类的${read.value === "image" ? "图片" : "视频"}模式。`,
      });
      return;
    }
    setSingleModePreferenceFeedback(null);
  }, [
    loading,
    mode,
    pending,
    requesting,
    savingBatchPreferences,
    savingQuickReferencePreferences,
    services.apiBaseUrl,
    sessionGeneration,
    singleModePreferenceContextKey,
    singleModeHasDraft,
    singlePreferenceCategory,
    singlePreferenceMode,
    userId,
    uploadingReference,
    preparingQuickReference,
  ]);

  useEffect(() => {
    if (singlePreferenceMode === null || loading || catalogError !== null || !catalogReadyRef.current
      || pendingRef.current !== null || requestingRef.current || uploadInProgressRef.current
      || quickReferenceActionRef.current || savingQuickReferencePreferencesRef.current
      || savingBatchPreferencesRef.current) return;

    const contextKey = singlePreferenceContextKey;
    const preferenceMode = singlePreferenceMode;
    const models = preferenceMode === "image" ? imageModels : videoModels;
    const acceptedReceipt = acceptedReceiptContextRef.current;
    if (acceptedReceipt?.userId === userId && acceptedReceipt.services === services
      && acceptedReceipt.sessionGeneration === sessionGeneration && acceptedReceipt.signature === formSignature) {
      singlePreferenceCatalogRef.current = { contextKey, models, prompts };
      return;
    }
    const previous = singlePreferenceCatalogRef.current;
    if (previous?.contextKey === contextKey) {
      if (previous.models === models && previous.prompts === prompts) return;
      singlePreferenceCatalogRef.current = { contextKey, models, prompts };

      const warnings: string[] = [];
      const currentModelId = preferenceMode === "image" ? selectedImageModelId : selectedVideoModelId;
      const selectedModel = models.find((candidate) => candidate.id === currentModelId && candidate.category === preferenceMode) ?? null;
      if (currentModelId !== "" && selectedModel === null) {
        warnings.push("所选模型已不在当前目录中，已清除该模型及其选项。");
        if (preferenceMode === "image") {
          setSelectedImageModelId("");
          setImageSize("");
        } else {
          setSelectedVideoModelId("");
          setVideoSize("");
          setVideoDuration("");
          setVideoResolution("");
        }
        setSingleGenerationCounts((current) => ({ ...current, [contextKey]: 1 }));
      } else if (selectedModel !== null) {
        const options = modelOptions(selectedModel);
        if (preferenceMode === "image") {
          if (imageSize !== "" && !options.sizes.includes(imageSize)) {
            setImageSize("");
            warnings.push("所选图片尺寸已不受当前模型支持，已清除，请重新选择。");
          }
        } else {
          if (videoSize !== "" && !options.sizes.includes(videoSize)) {
            setVideoSize("");
            warnings.push("所选画面比例已不受当前模型支持，已清除，请重新选择。");
          }
          if (videoDuration !== "" && !options.durations.map(String).includes(videoDuration)) {
            setVideoDuration("");
            warnings.push("所选时长已不受当前模型支持，已清除，请重新选择。");
          }
          if (videoResolution !== "" && !options.resolutions.includes(videoResolution)) {
            setVideoResolution("");
            warnings.push("所选分辨率已不受当前模型支持，已清除，请重新选择。");
          }
        }
      }

      const currentPromptId = preferenceMode === "image" ? selectedImagePromptId : selectedVideoPromptId;
      const currentPromptOptions = promptsForSingleMode(preferenceMode, prompts);
      if (currentPromptId !== "" && !currentPromptOptions.some((prompt) => prompt.id === currentPromptId)) {
        if (preferenceMode === "image") setSelectedImagePromptId("");
        else setSelectedVideoPromptId("");
        warnings.push("所选提示词已不属于当前图片/视频提示词目录，已切回手动输入；正文保留。");
      }

      if (singlePreferenceOffer?.contextKey === contextKey) {
        const resolved = resolveSingleMediaPreference(
          preferenceMode,
          singlePreferenceOffer.preference,
          models,
          currentPromptOptions,
        );
        setSinglePreferenceOffer({ ...singlePreferenceOffer, resolved });
      }
      if (warnings.length > 0) {
        setSinglePreferenceMessage(warnings.join(" "), contextKey);
      }
      return;
    }

    singlePreferenceCatalogRef.current = { contextKey, models, prompts };
    const read = preferenceMode === "image"
      ? readSingleMediaPreference({ apiBaseUrl: services.apiBaseUrl, userId }, "image", singlePreferenceCategory)
      : readSingleMediaPreference({ apiBaseUrl: services.apiBaseUrl, userId }, "video", singlePreferenceCategory);
    setSinglePreferenceOffer(null);
    if (read.kind === "missing") {
      setSinglePreferenceMessage(null, contextKey);
      return;
    }
    if (read.kind === "unavailable" || read.kind === "invalid") {
      setSinglePreferenceMessage(read.kind === "unavailable"
        ? "浏览器无法读取此分类的本地生成选项；请手动选择。"
        : "此分类的本地生成选项无法读取；请手动选择。", contextKey);
      return;
    }

    const modePrompts = promptsForSingleMode(preferenceMode, prompts);
    const resolved = resolveSingleMediaPreference(preferenceMode, read.value, models, modePrompts);
    const formHasInput = preferenceMode === "image"
      ? selectedImageModelId !== "" || selectedImagePromptId !== "" || imagePrompt.trim() !== "" || imageSize !== ""
        || singleGenerationCount !== 1 || imageReferenceImages.trim() !== ""
        || imageQuickReferenceImages.length > 0 || referenceFiles.image !== undefined
      : selectedVideoModelId !== "" || selectedVideoPromptId !== "" || videoPrompt.trim() !== ""
        || singleGenerationCount !== 1 || videoSize !== "" || videoDuration !== "" || videoResolution !== ""
        || videoReferenceImages.trim() !== "" || videoReferenceAudios.trim() !== "" || videoReferenceVideos.trim() !== ""
        || videoQuickReferenceImages.length > 0 || videoQuickReferenceAudios.length > 0
        || referenceFiles.image !== undefined || referenceFiles.audio !== undefined || referenceFiles.video !== undefined;
    const offer: SingleMediaPreferenceOffer = {
      contextKey,
      mode: preferenceMode,
      preference: read.value,
      resolved,
    };
    if (formHasInput || editedSinglePreferenceContextsRef.current.has(contextKey)) {
      setSinglePreferenceOffer(offer);
      setSinglePreferenceMessage(
        `已找到当前分类记住的${preferenceMode === "image" ? "图片" : "视频"}选项；为保留现有表单，未自动应用。你可以显式应用，替换已有提示词前会再次确认。${resolved.warnings.join(" ")}`,
        contextKey,
      );
      return;
    }
    applyResolvedSinglePreference(offer, true);
  }, [
    catalogError,
    formSignature,
    imageModels,
    imagePrompt,
    imageQuickReferenceImages,
    imageReferenceImages,
    imageSize,
    loading,
    mode,
    prompts,
    pending,
    preparingQuickReference,
    referenceFiles,
    requesting,
    savingBatchPreferences,
    savingQuickReferencePreferences,
    selectedImageModelId,
    selectedImagePromptId,
    selectedVideoModelId,
    selectedVideoPromptId,
    services.apiBaseUrl,
    sessionGeneration,
    singlePreferenceCategory,
    singlePreferenceContextKey,
    singleGenerationCount,
    singlePreferenceMode,
    singlePreferenceOffer,
    userId,
    uploadingReference,
    videoDuration,
    videoModels,
    videoPrompt,
    videoQuickReferenceAudios,
    videoQuickReferenceImages,
    videoReferenceAudios,
    videoReferenceImages,
    videoReferenceVideos,
    videoResolution,
    videoSize,
  ]);

  useEffect(() => {
    const port = services.userPreferences;
    if (port === undefined) {
      setQuickReferencePreferenceMessage("当前服务尚未接入账户快速引用设置。");
      return;
    }
    const controller = new AbortController();
    let current = true;
    const capturedServices = services;
    const capturedUserId = userId;
    const capturedSessionGeneration = sessionGeneration;
    const restoreGeneration = quickReferenceRestoreGenerationRef.current;
    quickReferencePreferenceControllerRef.current = controller;
    setQuickReferencePreferenceLoading(true);
    setQuickReferencePreferenceMessage(null);
    void port.get(controller.signal).then((preferences) => {
      if (!current || controller.signal.aborted
        || !preferenceScopeIsCurrent(capturedServices, capturedUserId, capturedSessionGeneration)
        || restoreGeneration !== quickReferenceRestoreGenerationRef.current) return;
      const restored = restoreQuickReferenceSettings(preferences.quick_ref_settings);
      const next = { ...quickReferenceSettingsRef.current };
      for (const modeName of ["image", "video"] as const) {
        const modeSettings = { ...next[modeName] };
        for (const field of QUICK_REFERENCE_FIELDS[modeName]) {
          if (!quickReferenceEditsRef.current.has(`${modeName}:${field}`)) {
            modeSettings[field] = restored[modeName][field];
          }
        }
        next[modeName] = modeSettings;
      }
      quickReferenceSettingsRef.current = next;
      setQuickReferenceSettings(next);
      setQuickReferencePreferenceReadFailed(false);
      setQuickReferencePreferenceMessage("已读取当前账号的快速引用设置。");
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted
        || !preferenceScopeIsCurrent(capturedServices, capturedUserId, capturedSessionGeneration)
        || restoreGeneration !== quickReferenceRestoreGenerationRef.current) return;
      setQuickReferencePreferenceReadFailed(true);
      if (isUnauthorized(error)) onUnauthorizedRef.current();
      else setQuickReferencePreferenceMessage(`快速引用设置读取失败：${describeFailure(error)}`);
    }).finally(() => {
      if (current && !controller.signal.aborted
        && preferenceScopeIsCurrent(capturedServices, capturedUserId, capturedSessionGeneration)
        && restoreGeneration === quickReferenceRestoreGenerationRef.current) {
        setQuickReferencePreferenceLoading(false);
      }
      if (quickReferencePreferenceControllerRef.current === controller) {
        quickReferencePreferenceControllerRef.current = null;
      }
    });
    return () => {
      current = false;
      controller.abort();
      if (quickReferencePreferenceControllerRef.current === controller) {
        quickReferencePreferenceControllerRef.current = null;
      }
    };
  }, [services, sessionGeneration, userId]);

  useEffect(() => {
    setReferenceFiles({});
    setReferenceUploadStatus(null);
    setImageQuickReferenceImages([]);
    setVideoQuickReferenceImages([]);
    setVideoQuickReferenceAudios([]);
    Object.values(referenceFileInputs.current).forEach((input) => {
      if (input !== null && input !== undefined) input.value = "";
    });
  }, [services, sessionGeneration, userId]);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    catalogReadyRef.current = false;
    setLoading(true);
    setCatalogError(null);
    const catalog = services.creatorSettings;
    const reads = [
      services.listSeries(controller.signal),
      catalog?.listModels("video", controller.signal) ?? Promise.resolve([]),
      catalog?.listModels("image", controller.signal) ?? Promise.resolve([]),
      catalog?.listPrompts(undefined, controller.signal) ?? Promise.resolve([]),
    ] as const;
    void Promise.allSettled(reads).then((results) => {
      if (!current || controller.signal.aborted) return;
      const [seriesResult, videoResult, imageResult, promptResult] = results;
      catalogReadyRef.current = imageResult.status === "fulfilled" && promptResult.status === "fulfilled";
      if (seriesResult.status === "fulfilled") setSeries(seriesResult.value);
      else if (isUnauthorized(seriesResult.reason)) onUnauthorizedRef.current();
      else setCatalogError(describeFailure(seriesResult.reason));
      if (videoResult.status === "fulfilled") setVideoModels(videoResult.value);
      else if (isUnauthorized(videoResult.reason)) onUnauthorizedRef.current();
      else setCatalogError((message) => message ?? describeFailure(videoResult.reason));
      if (imageResult.status === "fulfilled") setImageModels(imageResult.value);
      else if (isUnauthorized(imageResult.reason)) onUnauthorizedRef.current();
      else setCatalogError((message) => message ?? describeFailure(imageResult.reason));
      if (promptResult.status === "fulfilled") setPrompts(promptResult.value);
      else if (isUnauthorized(promptResult.reason)) onUnauthorizedRef.current();
      else setCatalogError((message) => message ?? describeFailure(promptResult.reason));
      if (imageResult.status === "fulfilled" && promptResult.status === "fulfilled") {
        batchSelectionsRef.current = defaultBatchSelections(imageResult.value, promptResult.value);
        setBatchSelections(batchSelectionsRef.current);
      }
      setLoading(false);
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [services, sessionGeneration, userId]);

  useEffect(() => {
    const port = services.userPreferences;
    if (mode !== "batch" || loading || !catalogReadyRef.current) return;
    if (port === undefined) {
      setBatchPreferenceMessage("当前服务尚未接入账户偏好读取与保存。");
      return;
    }
    const controller = new AbortController();
    let current = true;
    const capturedServices = services;
    const capturedUserId = userId;
    const capturedSessionGeneration = sessionGeneration;
    const restoreGeneration = batchPreferenceRestoreGenerationRef.current;
    setBatchPreferenceLoading(true);
    setBatchPreferenceMessage(null);
    void port.get(controller.signal).then((preferences) => {
      if (!current || controller.signal.aborted
        || !preferenceScopeIsCurrent(capturedServices, capturedUserId, capturedSessionGeneration)
        || restoreGeneration !== batchPreferenceRestoreGenerationRef.current) return;
      if (requestingRef.current || pendingRef.current !== null || savingBatchPreferencesRef.current
        || quickReferenceActionRef.current) {
        setBatchPreferenceMessage("账户偏好读取晚于本次操作，已保留当前选项；提交内容不会被更改。");
        return;
      }

      const savedBatch = record(preferences.batch_gen_prefs);
      if (savedBatch === null) {
        setBatchPreferenceMessage("账户中还没有保存批量生成选项。修改后可点击“记住批量选项”。");
        return;
      }
      const savedChecks = restoreBatchGenerationChecks(savedBatch.checks);
      const nextChecks = { ...batchChecksRef.current };
      for (const field of BATCH_CHECK_FIELDS) {
        if (!batchCheckEditsRef.current.has(field)) nextChecks[field] = savedChecks[field];
      }
      batchChecksRef.current = nextChecks;
      setBatchChecks(nextChecks);
      const nextWarnings = emptyBatchWarnings();
      const nextSelections = { ...batchSelectionsRef.current };
      const edits = batchPreferenceEditsRef.current;
      for (const type of BATCH_ASSET_TYPES) {
        const saved = record(savedBatch[type]);
        if (saved === null) continue;
        const dirty = edits[type];
        let selection = { ...nextSelections[type] };
        if (!dirty.has("modelId") && typeof saved.model === "string" && saved.model !== "") {
          const model = imageModels.find((candidate) => candidate.id === saved.model) ?? null;
          if (model === null) {
            nextWarnings[type].modelId = "已保存的模型不在当前模型目录中，请重新选择。";
            selection.modelId = "";
            if (!dirty.has("size")) selection.size = "";
          } else {
            selection.modelId = model.id;
            if (!dirty.has("size")) selection.size = batchDefaultSize(model);
          }
        }
        if (!dirty.has("promptId") && typeof saved.prompt === "string") {
          if (saved.prompt === "") {
            selection.promptId = "";
          } else {
            const validPrompt = prompts.some((prompt) => (
              prompt.id === saved.prompt && prompt.category === BATCH_PROMPT_CATEGORIES[type]
            ));
            if (validPrompt) selection.promptId = saved.prompt;
            else {
              nextWarnings[type].promptId = "已保存的提示词不在当前类型目录中，请重新选择。";
              selection.promptId = "";
            }
          }
        }
        if (!dirty.has("size") && typeof saved.size === "string" && saved.size !== "") {
          const model = imageModels.find((candidate) => candidate.id === selection.modelId) ?? null;
          const sizes = modelOptions(model).sizes;
          if (sizes.includes(saved.size)) selection.size = saved.size;
          else {
            nextWarnings[type].size = sizes.length === 0
              ? "已保存的尺寸不适用于当前模型；当前模型未配置尺寸，提交时会省略尺寸。"
              : "已保存的尺寸不适用于当前模型，请重新选择。";
            if (sizes.length > 0) selection.size = "";
          }
        }
        nextSelections[type] = selection;
      }
      batchSelectionsRef.current = nextSelections;
      setBatchSelections(nextSelections);
      setBatchPreferenceWarnings(nextWarnings);
      const hasWarnings = BATCH_ASSET_TYPES.some((type) => Object.keys(nextWarnings[type]).length > 0);
      setBatchPreferenceMessage(hasWarnings
        ? "已读取账户批量选项；部分保存值在当前目录中不可用，请重新选择后保存。"
        : "已读取账户中保存的批量选项。修改后可点击“记住批量选项”。");
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted
        || !preferenceScopeIsCurrent(capturedServices, capturedUserId, capturedSessionGeneration)
        || restoreGeneration !== batchPreferenceRestoreGenerationRef.current) return;
      if (isUnauthorized(error)) onUnauthorizedRef.current();
      else setBatchPreferenceMessage(`账户批量选项读取失败：${describeFailure(error)}`);
    }).finally(() => {
      if (current && !controller.signal.aborted
        && preferenceScopeIsCurrent(capturedServices, capturedUserId, capturedSessionGeneration)) {
        setBatchPreferenceLoading(false);
      }
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [imageModels, loading, mode, prompts, services, sessionGeneration, userId]);

  useEffect(() => {
    const verifiedChapters = verifiedWorkbenchChaptersRef.current;
    if (selectedSeriesId !== "" && verifiedChapters?.key === selectedChaptersKey
      && verifiedChapters.services === services) {
      setChapterError(null);
      setChapterLoading(false);
      setChapters(verifiedChapters.chapters);
      return;
    }
    if (selectedSeriesId === "") {
      setChapters([]);
      setSelectedChapterId("");
      setStoryboardAssets([]);
      setAssetChoices([]);
      setCharacterAssets([]);
      setSceneAssets([]);
      setPropAssets([]);
      return;
    }
    const controller = new AbortController();
    let current = true;
    setChapterLoading(true);
    setChapterError(null);
    setSelectedChapterId("");
    setStoryboardAssets([]);
    setAssetChoices([]);
    setCharacterAssets([]);
    setSceneAssets([]);
    setPropAssets([]);
    setSelectedBatchKeys([]);
    void services.listChapters(selectedSeriesId, controller.signal).then((result) => {
      if (current && !controller.signal.aborted) {
        setChapters(result);
      }
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (isUnauthorized(error)) onUnauthorizedRef.current();
      else setChapterError(describeFailure(error));
      setChapters([]);
    }).finally(() => {
      if (current && !controller.signal.aborted) setChapterLoading(false);
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [selectedChaptersKey, selectedSeriesId, services]);

  useEffect(() => {
    if (selectedSeriesId === "" || selectedChapterId === "") {
      setStoryboardAssets([]);
      setAssetChoices([]);
      setCharacterAssets([]);
      setSceneAssets([]);
      setPropAssets([]);
      setDirectoryError(null);
      setDirectoryLoading(false);
      return;
    }
    const verifiedDirectory = verifiedWorkbenchDirectoryRef.current;
    if (verifiedDirectory?.key === selectedDirectoryKey && verifiedDirectory.services === services) {
      setDirectoryError(null);
      setDirectoryLoading(false);
      setStoryboardAssets(verifiedDirectory.storyboards);
      setCharacterAssets(verifiedDirectory.characters);
      setSceneAssets(verifiedDirectory.scenes);
      setPropAssets(verifiedDirectory.props);
      setAssetChoices([
        ...libraryChoices("storyboard", verifiedDirectory.storyboards),
        ...libraryChoices("character", verifiedDirectory.characters),
        ...libraryChoices("scene", verifiedDirectory.scenes),
        ...libraryChoices("prop", verifiedDirectory.props),
      ]);
      return;
    }
    const controller = new AbortController();
    let current = true;
    setDirectoryLoading(true);
    setDirectoryError(null);
    setSelectedBatchKeys([]);
    setStoryboardAssets([]);
    setAssetChoices([]);
    setCharacterAssets([]);
    setSceneAssets([]);
    setPropAssets([]);
    void Promise.all([
      services.listStoryboardAssets(selectedSeriesId, selectedChapterId, controller.signal),
      services.listCharacters(selectedSeriesId, controller.signal),
      services.listScenes(selectedSeriesId, controller.signal),
      services.listProps(selectedSeriesId, controller.signal),
    ]).then(([frames, characters, scenes, props]) => {
      if (!current || controller.signal.aborted) return;
      const currentFrames = frames.filter((item) => (
        item.series_id === selectedSeriesId && item.chapter_id === selectedChapterId
      ));
      const currentCharacters = characters.filter((item) => item.series_id === selectedSeriesId);
      const currentScenes = scenes.filter((item) => item.series_id === selectedSeriesId);
      const currentProps = props.filter((item) => item.series_id === selectedSeriesId);
      setStoryboardAssets(currentFrames);
      setCharacterAssets(currentCharacters);
      setSceneAssets(currentScenes);
      setPropAssets(currentProps);
      setAssetChoices([
        ...libraryChoices("storyboard", currentFrames),
        ...libraryChoices("character", currentCharacters),
        ...libraryChoices("scene", currentScenes),
        ...libraryChoices("prop", currentProps),
      ]);
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (isUnauthorized(error)) onUnauthorizedRef.current();
      else setDirectoryError(describeFailure(error));
      setStoryboardAssets([]);
      setAssetChoices([]);
      setCharacterAssets([]);
      setSceneAssets([]);
      setPropAssets([]);
    }).finally(() => {
      if (current && !controller.signal.aborted) setDirectoryLoading(false);
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [selectedChapterId, selectedDirectoryKey, selectedSeriesId, services]);

  useEffect(() => {
    if (singleAssetId !== "" && !singleAssetChoices.some((asset) => asset.id === singleAssetId)) {
      setSingleAssetId("");
    }
    if (singleStoryboardAssetId !== "" && !storyboardAssets.some((asset) => asset.id === singleStoryboardAssetId)) {
      setSingleStoryboardAssetId("");
    }
  }, [singleAssetChoices, singleAssetId, singleStoryboardAssetId, storyboardAssets]);

  useEffect(() => {
    const intent = workbenchSourceIntent;
    if (intent === null || lastWorkbenchIntentEpochRef.current === intent.navigationEpoch) return;
    lastWorkbenchIntentEpochRef.current = intent.navigationEpoch;
    if (intent.actorId !== userId || intent.services !== services || intent.apiBaseUrl !== services.apiBaseUrl
      || intent.sessionGeneration !== sessionGeneration || !intent.isCurrent()) {
      setWorkbenchSourceNotice("左侧来源已变化，生成目标没有更新。请重新选择来源。");
      return;
    }
    setWorkbenchSourceNotice(null);
    setRequestedWorkbenchSource({
      intent,
      interactionVersion: workbenchTargetInteractionVersionRef.current,
      needsConfirmation: pendingRef.current !== null || requestingRef.current || uploadInProgressRef.current,
      confirmed: false,
    });
  }, [services, sessionGeneration, userId, workbenchSourceIntent]);

  useEffect(() => {
    const request = requestedWorkbenchSource;
    if (request === null) return;
    const intent = request.intent;
    if (!intent.isCurrent() || intent.actorId !== userId || intent.services !== services
      || intent.apiBaseUrl !== services.apiBaseUrl || intent.sessionGeneration !== sessionGeneration) {
      setRequestedWorkbenchSource(null);
      setWorkbenchSourceNotice("左侧来源已失效，当前媒体目标保持不变。");
      return;
    }
    if (request.interactionVersion !== workbenchTargetInteractionVersionRef.current) {
      setRequestedWorkbenchSource(null);
      setWorkbenchSourceNotice("你已手动更改媒体目标，保留当前选择。需要时可重新点选左侧来源。");
      return;
    }
    if (pendingRef.current !== null || requestingRef.current || uploadInProgressRef.current) {
      if (!request.needsConfirmation) setRequestedWorkbenchSource({ ...request, needsConfirmation: true });
      return;
    }
    if (request.needsConfirmation && !request.confirmed) return;
    if (intent.target.kind === "asset" && intent.target.category === "fused") {
      setRequestedWorkbenchSource(null);
      setWorkbenchSourceNotice("融合素材不能作为普通图片或视频消息目标；原媒体目标保持不变。");
      return;
    }
    if (loading) return;
    if (intent.target.kind !== "chapter" && mode !== "image" && mode !== "video") return;
    const controller = new AbortController();
    let active = true;
    const isCandidateCurrent = () => active
      && mountedRef.current
      && !controller.signal.aborted
      && identityRef.current.userId === userId
      && identityRef.current.services === services
      && identityRef.current.sessionGeneration === sessionGeneration
      && identityRef.current.services.apiBaseUrl === intent.apiBaseUrl
      && request.interactionVersion === workbenchTargetInteractionVersionRef.current
      && intent.isCurrent();
    const finishFailure = (message: string) => {
      if (!isCandidateCurrent()) return;
      setRequestedWorkbenchSource(null);
      setWorkbenchSourceNotice(message);
    };
    setWorkbenchSourceNotice("正在独立核对左侧媒体来源；核对期间保留当前剧集、章节、目标和提示词。");

    const directoryReads = intent.target.kind === "chapter"
      ? Promise.resolve(null)
      : Promise.all([
        services.listStoryboardAssets(intent.seriesId, intent.chapterId, controller.signal),
        services.listCharacters(intent.seriesId, controller.signal),
        services.listScenes(intent.seriesId, controller.signal),
        services.listProps(intent.seriesId, controller.signal),
      ]);
    void Promise.all([
      services.listSeries(controller.signal),
      services.listChapters(intent.seriesId, controller.signal),
      directoryReads,
    ]).then(([candidateSeries, candidateChapters, candidateDirectories]) => {
      if (!isCandidateCurrent()) return;
      const seriesMatches = candidateSeries.filter((item) => item.id === intent.seriesId && item.can_enter === true);
      if (seriesMatches.length !== 1) {
        finishFailure(seriesMatches.length === 0
          ? "左侧剧集已不在最新可进入目录中；当前媒体目标保持不变。"
          : "左侧剧集编号不唯一，无法安全应用媒体目标。媒体目标保持不变。");
        return;
      }
      const chapterMatches = candidateChapters.filter((chapter) => (
        chapter.id === intent.chapterId && chapter.series_id === intent.seriesId
      ));
      if (chapterMatches.length !== 1) {
        finishFailure(chapterMatches.length === 0
          ? "左侧章节已不在最新剧集目录中；当前剧集、章节、目标和提示词保持不变。"
          : "左侧章节编号不唯一；当前剧集、章节、目标和提示词保持不变。");
        return;
      }
      const chapter = chapterMatches[0]!;
      if (intent.target.kind === "chapter") {
        const candidateChaptersKey = JSON.stringify([
          userId,
          services.apiBaseUrl,
          sessionGeneration,
          intent.seriesId,
        ]);
        verifiedWorkbenchChaptersRef.current = {
          key: candidateChaptersKey,
          services,
          chapters: candidateChapters,
        };
        setSeries(candidateSeries);
        setChapters(candidateChapters);
        setChapterError(null);
        setChapterLoading(false);
        setSelectedSeriesId(intent.seriesId);
        setSelectedChapterId(intent.chapterId);
        setSingleAssetId("");
        setSingleStoryboardAssetId("");
        setRequestedWorkbenchSource(null);
        setWorkbenchSourceNotice("已选择左侧章节。请在媒体页面明确选择分镜或素材目标；系统不会把章节伪装成媒体消息目标。");
        return;
      }
      if (candidateDirectories === null) {
        finishFailure("左侧章节素材目录无法读取；当前剧集、章节、目标和提示词保持不变。");
        return;
      }
      const [candidateStoryboards, candidateCharacters, candidateScenes, candidateProps] = candidateDirectories;
      let selectedTarget:
        | { readonly kind: "frame"; readonly storyboardAssetId: string }
        | { readonly kind: "asset"; readonly assetType: MediaGenerationAssetType; readonly assetId: string }
        | null = null;
      if (intent.target.kind === "frame") {
        const target = intent.target;
        const content = chapter.content;
        const frameMatches = Array.isArray(content) ? content.flatMap((frame, index) => {
          const storyboard = frame.storyboard;
          return Array.isArray(storyboard) && storyboard[0] === target.storyboardAssetId ? [index] : [];
        }) : [];
        const assetMatches = candidateStoryboards.filter((asset) => asset.id === target.storyboardAssetId
          && asset.series_id === intent.seriesId && asset.chapter_id === intent.chapterId);
        if (frameMatches.length !== 1 || assetMatches.length !== 1 || assetMatches[0]?.frame_index !== frameMatches[0]) {
          finishFailure("左侧分镜身份在最新章节和素材目录中无法唯一核对；当前媒体目标保持不变。");
          return;
        }
        selectedTarget = { kind: "frame", storyboardAssetId: target.storyboardAssetId };
      } else {
        const target = intent.target;
        const assetType = target.category === "characters" ? "character"
          : target.category === "scenes" ? "scene"
            : target.category === "props" ? "prop"
              : target.category === "storyboard" ? "storyboard" : null;
        const directory = assetType === "character" ? candidateCharacters
          : assetType === "scene" ? candidateScenes
            : assetType === "prop" ? candidateProps
              : assetType === "storyboard" ? candidateStoryboards : [];
        const assetMatches = directory.filter((asset) => asset.id === target.assetId
          && asset.series_id === intent.seriesId
          && (assetType !== "storyboard" || ("chapter_id" in asset && asset.chapter_id === intent.chapterId)));
        if (assetType === null || assetMatches.length !== 1) {
          finishFailure("左侧素材身份在最新目录中无法唯一核对；当前媒体目标保持不变。");
          return;
        }
        selectedTarget = { kind: "asset", assetType, assetId: target.assetId };
      }
      if (!isCandidateCurrent() || selectedTarget === null) return;
      const scopedStoryboards = candidateStoryboards.filter((asset) => (
        asset.series_id === intent.seriesId && asset.chapter_id === intent.chapterId
      ));
      const scopedCharacters = candidateCharacters.filter((asset) => asset.series_id === intent.seriesId);
      const scopedScenes = candidateScenes.filter((asset) => asset.series_id === intent.seriesId);
      const scopedProps = candidateProps.filter((asset) => asset.series_id === intent.seriesId);
      const candidateDirectoryKey = JSON.stringify([
        userId,
        services.apiBaseUrl,
        sessionGeneration,
        intent.seriesId,
        intent.chapterId,
      ]);
      const candidateChaptersKey = JSON.stringify([
        userId,
        services.apiBaseUrl,
        sessionGeneration,
        intent.seriesId,
      ]);
      verifiedWorkbenchChaptersRef.current = {
        key: candidateChaptersKey,
        services,
        chapters: candidateChapters,
      };
      verifiedWorkbenchDirectoryRef.current = {
        key: candidateDirectoryKey,
        services,
        storyboards: scopedStoryboards,
        characters: scopedCharacters,
        scenes: scopedScenes,
        props: scopedProps,
      };
      setSeries(candidateSeries);
      setChapters(candidateChapters);
      setChapterError(null);
      setChapterLoading(false);
      setStoryboardAssets(scopedStoryboards);
      setCharacterAssets(scopedCharacters);
      setSceneAssets(scopedScenes);
      setPropAssets(scopedProps);
      setAssetChoices([
        ...libraryChoices("storyboard", scopedStoryboards),
        ...libraryChoices("character", scopedCharacters),
        ...libraryChoices("scene", scopedScenes),
        ...libraryChoices("prop", scopedProps),
      ]);
      setDirectoryError(null);
      setDirectoryLoading(false);
      setSelectedBatchKeys([]);
      setSelectedSeriesId(intent.seriesId);
      setSelectedChapterId(intent.chapterId);
      if (selectedTarget.kind === "frame") {
        setSingleTargetMode("frame_message");
        setSingleStoryboardAssetId(selectedTarget.storyboardAssetId);
        setSingleAssetId("");
      } else {
        setSingleTargetMode("asset_message");
        setSingleAssetType(selectedTarget.assetType);
        setSingleAssetId(selectedTarget.assetId);
        setSingleStoryboardAssetId("");
      }
      setRequestedWorkbenchSource(null);
      setWorkbenchSourceNotice("已将左侧选择应用为媒体目标；提交时仍会重新核对当前目录。");
    }).catch((error: unknown) => {
      if (!isCandidateCurrent()) return;
      if (isUnauthorized(error)) onUnauthorizedRef.current();
      const detail = describeFailure(error);
      finishFailure(`左侧目标目录核对失败（${detail}）；当前剧集、章节、目标和提示词保持不变。`);
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, [
    loading, mode, pending, requesting, requestedWorkbenchSource, services, sessionGeneration, series,
    uploadingReference, userId,
  ]);

  const setModeSafely = (value: MediaMode) => {
    if (pendingRef.current !== null || requestingRef.current || uploadInProgressRef.current
      || quickReferenceActionRef.current || savingQuickReferencePreferencesRef.current
      || savingBatchPreferencesRef.current || batchVideoActive) return;
    if (mode === "image" || mode === "video") rememberSingleModePresentation(mode);
    if (value === "image" || value === "video") {
      const saved = writeSingleMediaModePreference(
        { apiBaseUrl: services.apiBaseUrl, userId },
        singlePreferenceCategory,
        value,
      );
      setSingleModePreferenceFeedback({
        contextKey: singleModePreferenceContextKey,
        message: saved
          ? `已记住当前分类的${value === "image" ? "图片" : "视频"}模式。`
          : "浏览器未能保存当前分类的图片/视频模式；当前页面仍可继续使用。",
      });
    }
    setMode(value);
    if (value === "image" || value === "video") restoreSingleModePresentation(value);
    else hideSingleModePresentation();
    setReferenceUploadStatus(null);
  };

  const applyReceipt = (signature: string, receipt: MediaGenerationReceipt) => {
    if (signature !== identityRef.current.signature) return;
    const acceptedContext = { userId, services, sessionGeneration, signature };
    const nextOutcome = { signature, receipt, message: receiptMessage(receipt) };
    acceptedReceiptContextRef.current = acceptedContext;
    const receiptMode = receipt.operation === "image.single" ? "image"
      : receipt.operation === "video.single" ? "video" : null;
    if (receiptMode !== null) {
      acceptedReceiptContextByModeRef.current[receiptMode] = acceptedContext;
      outcomeByModeRef.current[receiptMode] = nextOutcome;
    }
    setOutcome(nextOutcome);
    updatePending(null);
  };

  const pendingSubmissionIsCurrent = (submission: PendingSubmission): boolean => {
    if (!isGenerationOperationCurrent(submission.operationOwnerId, submission.run)) return false;
    if (submission.singleGroup !== undefined) {
      return submission.singleGroup.ownerKey === singleOwnerContextKey
        && submission.singleGroup.ownerServices === services;
    }
    return submission.ownerKey === undefined
      ? identityRef.current.signature === submission.signature
      : submission.ownerKey === singleOwnerContextKey;
  };

  const lookupPending = async (submission: PendingSubmission) => {
    const ownerId = submission.operationOwnerId;
    if (pendingActionOwnerRef.current !== null || !pendingSubmissionIsCurrent(submission)) return;
    if (!beginPendingAction(ownerId)) return;
    const controller = makeGenerationController();
    try {
      const receipt = await submission.run.lookup(submission.expectation, controller.signal);
      if (!pendingSubmissionIsCurrent(submission)) return;
      if (submission.singleGroup !== undefined && submission.singleItemIndex !== undefined) {
        if (receipt.operation !== submission.operation) {
          throw new MediaGenerationError("invalid-response", "核对结果与原生成操作不匹配。");
        }
        const group = submission.singleGroup;
        const item = group.items[submission.singleItemIndex];
        if (item === undefined) throw new MediaGenerationError("invalid-response", "原生成项目已不在当前队列中。");
        item.status = "accepted";
        item.receipt = receipt as SingleMediaGenerationReceipt;
        delete item.message;
        publishSingleGenerationGroup(group);
        updatePending(null);
        releasePendingAction(ownerId);
        if (submission.singleItemIndex + 1 < group.items.length) {
          await submitSingleGenerationGroupFrom(group, submission.singleItemIndex + 1);
        } else {
          completeSingleGenerationGroup(group);
        }
        return;
      }
      if (receipt.operation !== submission.operation) {
        throw new MediaGenerationError("invalid-response", "核对结果与原生成操作不匹配。");
      }
      if (submission.ownerKey !== undefined && submission.ownerKey === singleOwnerContextKey
        && identityRef.current.signature !== submission.signature && receipt.operation === "image.batch") {
        setOutcome({
          signature: identityRef.current.signature,
          receipt,
          message: `已核对原批量请求。${receiptMessage(receipt)}`,
        });
      } else {
        applyReceipt(submission.signature, receipt);
      }
      finishGenerationOperation(ownerId);
    } catch (error) {
      if (!pendingSubmissionIsCurrent(submission)) return;
      if (isUnauthorized(error)) {
        onUnauthorized();
        finishGenerationOperation(ownerId);
        return;
      }
      const isChangedBatchForm = submission.operation === "image.batch"
        && submission.ownerKey !== undefined
        && identityRef.current.signature !== submission.signature;
      setOutcome({
        signature: isChangedBatchForm ? identityRef.current.signature : submission.signature,
        message: `${isChangedBatchForm ? "原批量请求" : submission.submissionMessage} 尚无法核对本次提交。${describeFailure(error)} 请保留本页，稍后再次核对；不会自动重新提交。`,
        pendingLookup: true,
      });
    } finally {
      releaseGenerationController(controller);
      releasePendingAction(ownerId);
    }
  };

  function stopLocalSubmissionTracking(submission: PendingSubmission) {
    if (pendingRef.current?.operationOwnerId !== submission.operationOwnerId) return;
    updatePending(null);
    finishGenerationOperation(submission.operationOwnerId);
    const group = submission.singleGroup;
    if (group !== undefined && group.ownerKey === singleOwnerContextKey) {
      publishSingleGenerationGroup(group, "已停止本地跟踪；原提交仍待确认，已保留项目与原请求信息。可在确认服务状态后再继续操作。");
    }
  }

  function publishSingleGenerationGroup(group: SingleMediaGenerationGroup, message: string | null = null) {
    const view: SingleMediaGenerationGroupView = {
      mode: group.mode,
      signature: group.signature,
      ownerKey: group.ownerKey,
      ownerServices: group.ownerServices,
      operationOwnerId: group.operationOwnerId,
      items: group.items.map(({ index, status, receipt, message: itemMessage, taskStatus, taskStatusMessage, taskStatusReading }) => ({
        index,
        status,
        ...(receipt === undefined ? {} : { receipt }),
        ...(itemMessage === undefined ? {} : { message: itemMessage }),
        ...(taskStatus === undefined ? {} : { taskStatus }),
        ...(taskStatusMessage === undefined ? {} : { taskStatusMessage }),
        ...(taskStatusReading === undefined ? {} : { taskStatusReading }),
      })),
      message,
    };
    singleModeGroupsRef.current[group.mode] = group;
    singleModeGroupViewsRef.current[group.mode] = view;
    singleGenerationGroupRef.current = group;
    singleGenerationGroupOwnerRef.current = group.operationOwnerId;
    setSingleGenerationGroup(view);
  }

  function updateSingleGenerationGroupItem(
    group: SingleMediaGenerationGroupView,
    index: number,
    update: Partial<SingleMediaGenerationGroupView["items"][number]>,
  ) {
    const retainedGroup = singleModeGroupsRef.current[group.mode];
    if (retainedGroup !== null && retainedGroup.operationOwnerId === group.operationOwnerId) {
      const retainedItem = retainedGroup.items.find((candidate) => candidate.index === index);
      if (retainedItem !== undefined) Object.assign(retainedItem, update);
      const currentView = singleModeGroupViewsRef.current[group.mode];
      if (currentView !== null && currentView.operationOwnerId === retainedGroup.operationOwnerId) {
        const nextView = {
          ...currentView,
          items: currentView.items.map((item) => item.index === index ? { ...item, ...update } : item),
        };
        singleModeGroupViewsRef.current[group.mode] = nextView;
      }
    }
    const activeView = singleModeGroupViewsRef.current[group.mode];
    if (mode === group.mode && singleGenerationGroupRef.current?.operationOwnerId === group.operationOwnerId
      && activeView !== null && activeView.operationOwnerId === group.operationOwnerId) {
      setSingleGenerationGroup(activeView);
    }
  }

  async function refreshSingleGenerationTaskStatus(
    group: SingleMediaGenerationGroupView,
    item: SingleMediaGenerationGroupView["items"][number],
  ) {
    const receipt = item.receipt;
    if (receipt === undefined || item.status !== "accepted" || item.taskStatusReading) return;
    const getTaskReceipt = group.ownerServices.getTaskReceipt;
    if (getTaskReceipt === undefined) {
      updateSingleGenerationGroupItem(group, item.index, { taskStatusMessage: "当前服务未接入任务状态读取。" });
      return;
    }

    const readKey = `${group.operationOwnerId}:${item.index}`;
    if (singleTaskStatusReadsRef.current.has(readKey)) return;
    singleTaskStatusReadsRef.current.add(readKey);
    const capturedServices = services;
    const capturedUserId = userId;
    const capturedSessionGeneration = sessionGeneration;
    const isCurrentGroup = () => preferenceScopeIsCurrent(
      capturedServices,
      capturedUserId,
      capturedSessionGeneration,
    ) && singleModeGroupsRef.current[group.mode]?.operationOwnerId === group.operationOwnerId;
    const controller = makeGenerationController();
    updateSingleGenerationGroupItem(group, item.index, { taskStatusReading: true, taskStatusMessage: null });
    try {
      const task = await getTaskReceipt(receipt.taskId, controller.signal);
      if (!isCurrentGroup()) return;
      const expectedType = receipt.operation === "image.single" ? "image-single" : "video-single";
      if (task.id !== receipt.taskId || task.type !== expectedType || task.messageId !== receipt.messageId) {
        updateSingleGenerationGroupItem(group, item.index, {
          taskStatusMessage: "任务状态与原受理回执不匹配，未显示本次状态。",
        });
        return;
      }
      updateSingleGenerationGroupItem(group, item.index, {
        taskStatus: {
          status: task.status,
          progress: task.progress !== null && task.progress <= 100 ? task.progress : null,
          progressMessage: task.progressMessage,
        },
        taskStatusMessage: null,
      });
    } catch (error) {
      if (!isCurrentGroup()) return;
      if (isUnauthorized(error)) onUnauthorized();
      else updateSingleGenerationGroupItem(group, item.index, {
        taskStatusMessage: `暂时无法读取本项任务状态。${describeFailure(error)}`,
      });
    } finally {
      releaseGenerationController(controller);
      singleTaskStatusReadsRef.current.delete(readKey);
      if (isCurrentGroup()) updateSingleGenerationGroupItem(group, item.index, { taskStatusReading: false });
    }
  }

  function stopSingleGenerationItems(group: SingleMediaGenerationGroup, startIndex: number) {
    for (let index = startIndex; index < group.items.length; index += 1) {
      const item = group.items[index];
      if (item !== undefined && item.status === "waiting") item.status = "blocked";
    }
  }

  async function submitSingleGenerationItem(
    group: SingleMediaGenerationGroup,
    item: SingleMediaGenerationItem,
    run: MediaGenerationRun = group.run,
  ): Promise<SingleMediaGenerationReceipt> {
    const controller = makeGenerationController();
    try {
      return await (group.mode === "image"
        ? run.submitImage(item.input as MediaGenerationImageInput, controller.signal)
        : run.submitVideo(item.input as MediaGenerationVideoInput, controller.signal));
    } finally {
      releaseGenerationController(controller);
    }
  }

  function completeSingleGenerationGroup(group: SingleMediaGenerationGroup) {
    if (!isGenerationOperationCurrent(group.operationOwnerId, group.run)
      || group.ownerKey !== singleOwnerContextKey || group.ownerServices !== services) return;
    const receipts = group.items.flatMap((item) => item.receipt === undefined ? [] : [item.receipt]);
    const message = `${receipts.length} 项${group.mode === "image" ? "图片" : "视频"}任务已按顺序受理；每项回执对应自己的原任务。`;
    publishSingleGenerationGroup(group, message);
    if (receipts.length === 1 && identityRef.current.signature === group.signature) {
      applyReceipt(group.signature, receipts[0]!);
      finishGenerationOperation(group.operationOwnerId);
      return;
    }
    if (identityRef.current.signature === group.signature) {
      const acceptedContext = { userId, services, sessionGeneration, signature: group.signature };
      const nextOutcome = { signature: group.signature, message };
      acceptedReceiptContextRef.current = acceptedContext;
      acceptedReceiptContextByModeRef.current[group.mode] = acceptedContext;
      outcomeByModeRef.current[group.mode] = nextOutcome;
      setOutcome(nextOutcome);
    }
    finishGenerationOperation(group.operationOwnerId);
  }

  async function submitSingleGenerationGroupFrom(group: SingleMediaGenerationGroup, startIndex: number) {
    const operationOwnerId = group.operationOwnerId;
    const run = group.run;
    if (!isGenerationOperationCurrent(operationOwnerId, run)
      || group.ownerKey !== singleOwnerContextKey || group.ownerServices !== services
      || requestingRef.current || pendingRef.current !== null) return;
    updateRequesting(operationOwnerId, true);
    let pendingToLookup: PendingSubmission | null = null;
    let completed = false;
    try {
      for (let index = startIndex; index < group.items.length; index += 1) {
        if (!isGenerationOperationCurrent(operationOwnerId, run)
          || group.ownerKey !== singleOwnerContextKey || group.ownerServices !== services) return;
        const item = group.items[index];
        if (item === undefined) continue;
        item.status = "submitting";
        delete item.message;
        publishSingleGenerationGroup(group);
        try {
          const receipt = await submitSingleGenerationItem(group, item, run);
          if (!isGenerationOperationCurrent(operationOwnerId, run)
            || group.ownerKey !== singleOwnerContextKey || group.ownerServices !== services) return;
          if (receipt.operation !== (group.mode === "image" ? "image.single" : "video.single")) {
            throw new MediaGenerationError("invalid-response", "生成回执与本次图片/视频操作不匹配。");
          }
          item.status = "accepted";
          item.receipt = receipt;
          publishSingleGenerationGroup(group);
        } catch (error) {
          if (!isGenerationOperationCurrent(operationOwnerId, run)
            || group.ownerKey !== singleOwnerContextKey || group.ownerServices !== services) return;
          if (isUnauthorized(error)) {
            stopSingleGenerationItems(group, index + 1);
            item.status = "refused";
            item.message = describeFailure(error);
            publishSingleGenerationGroup(group, `第 ${index + 1} 项未能继续；后续项目已停止。`);
            onUnauthorized();
            return;
          }
          stopSingleGenerationItems(group, index + 1);
          if (error instanceof MediaGenerationError && error.kind === "unknown-outcome") {
            item.status = "unknown";
            item.message = describeFailure(error);
            const operation = group.mode === "image" ? "image.single" : "video.single";
            pendingToLookup = {
              operation,
              idempotencyKey: item.input.idempotencyKey,
              expectation: { operation, idempotencyKey: item.input.idempotencyKey },
              run,
              signature: group.signature,
              submissionMessage: describeFailure(error),
              operationOwnerId,
              singleGroup: group,
              singleItemIndex: index,
            };
            updatePending(pendingToLookup);
            setOutcome({
              signature: group.signature,
              message: `第 ${index + 1} 项受理状态待确认；后续项目已停止。系统只核对原操作，不会自动重新提交。`,
              pendingLookup: true,
            });
            publishSingleGenerationGroup(group);
            break;
          }
          item.status = "refused";
          item.message = describeFailure(error);
          publishSingleGenerationGroup(group, `第 ${index + 1} 项未受理；后续项目已停止。${describeFailure(error)}`);
          setOutcome({ signature: group.signature, message: `第 ${index + 1} 项未受理；后续项目已停止。${describeFailure(error)}` });
          return;
        }
      }
      completed = pendingToLookup === null && group.items.every((item) => item.status === "accepted");
    } finally {
      updateRequesting(operationOwnerId, false);
      if (!completed && pendingToLookup === null
        && !pendingBelongsToOperation(operationOwnerId)) {
        finishGenerationOperation(operationOwnerId);
      }
    }
    if (completed) completeSingleGenerationGroup(group);
    if (pendingToLookup !== null) await lookupPending(pendingToLookup);
  }

  const retryPendingSingleGenerationItem = async (submission: PendingSubmission) => {
    const group = submission.singleGroup;
    const index = submission.singleItemIndex;
    const ownerId = submission.operationOwnerId;
    const run = submission.run;
    if (group === undefined || index === undefined || pendingActionOwnerRef.current !== null
      || !isGenerationOperationCurrent(ownerId, run) || group.ownerKey !== singleOwnerContextKey
      || group.ownerServices !== services) return;
    const item = group.items[index];
    if (item === undefined || item.status !== "unknown") return;
    if (!beginPendingAction(ownerId)) return;
    updateRequesting(ownerId, true);
    item.status = "submitting";
    item.message = "正按原请求与原幂等键重试本项。";
    publishSingleGenerationGroup(group);
    try {
      const receipt = await submitSingleGenerationItem(group, item, run);
      if (!isGenerationOperationCurrent(ownerId, run)
        || group.ownerKey !== singleOwnerContextKey || group.ownerServices !== services) return;
      if (receipt.operation !== submission.operation) {
        throw new MediaGenerationError("invalid-response", "生成回执与原操作不匹配。");
      }
      item.status = "accepted";
      item.receipt = receipt;
      delete item.message;
      publishSingleGenerationGroup(group);
      updatePending(null);
      releasePendingAction(ownerId);
      updateRequesting(ownerId, false);
      if (index + 1 < group.items.length) await submitSingleGenerationGroupFrom(group, index + 1);
      else completeSingleGenerationGroup(group);
    } catch (error) {
      if (!isGenerationOperationCurrent(ownerId, run)
        || group.ownerKey !== singleOwnerContextKey || group.ownerServices !== services) return;
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      if (error instanceof MediaGenerationError && error.kind === "unknown-outcome") {
        item.status = "unknown";
        item.message = describeFailure(error);
        setOutcome({
          signature: group.signature,
          message: `第 ${index + 1} 项仍待确认；仍保留原幂等键，可重新核对或在明确操作后再次按原请求重试。`,
          pendingLookup: true,
        });
      } else {
        item.status = "refused";
        item.message = describeFailure(error);
        stopSingleGenerationItems(group, index + 1);
        updatePending(null);
        setOutcome({ signature: group.signature, message: `第 ${index + 1} 项未受理；后续项目已停止。${describeFailure(error)}` });
      }
      publishSingleGenerationGroup(group);
    } finally {
      updateRequesting(ownerId, false);
      releasePendingAction(ownerId);
      if (!pendingBelongsToOperation(ownerId)) finishGenerationOperation(ownerId);
    }
  };

  const makeRun = (allowFrozenFormChanges = false) => {
    const capturedSignature = formSignature;
    const capturedUserId = userId;
    const capturedServices = services;
    const capturedSessionGeneration = sessionGeneration;
    const capturedApiBaseUrl = services.apiBaseUrl;
    const port = services.mediaGeneration;
    if (port === undefined) throw new MediaGenerationError("rejected", "媒体生成服务尚未接入。", 503);
    return port.beginRun({
      actorId: userId,
      isContextCurrent: () => mountedRef.current
        && identityRef.current.userId === capturedUserId
        && identityRef.current.services === capturedServices
        && identityRef.current.sessionGeneration === capturedSessionGeneration
        && identityRef.current.services.apiBaseUrl === capturedApiBaseUrl
        && (allowFrozenFormChanges || identityRef.current.signature === capturedSignature),
    });
  };

  const makeReferenceUploadRun = () => {
    const capturedSignature = formSignature;
    const capturedUserId = userId;
    const capturedServices = services;
    const capturedSessionGeneration = sessionGeneration;
    const port = services.mediaReferenceUpload;
    if (port === undefined) throw new MediaReferenceUploadError("not-sent", "参考素材上传服务尚未接入。");
    return port.beginRun({
      actorId: userId,
      isContextCurrent: () => mountedRef.current
        && identityRef.current.userId === capturedUserId
        && identityRef.current.services === capturedServices
        && identityRef.current.sessionGeneration === capturedSessionGeneration
        && identityRef.current.signature === capturedSignature,
    });
  };

  const setReferenceFile = (kind: MediaReferenceKind, file: File | undefined) => {
    if ((mode === "image" && kind === "image") || mode === "video") {
      markSinglePreferenceEdited();
    }
    setReferenceFiles((current) => {
      const next = { ...current };
      if (file === undefined) delete next[kind];
      else next[kind] = file;
      return next;
    });
    setReferenceUploadStatus((current) => current?.kind === kind ? null : current);
  };

  const appendReferenceUrl = (kind: MediaReferenceKind, url: string) => {
    const append = (current: string) => `${current}${current === "" || current.endsWith("\n") ? "" : "\n"}${url}`;
    if (kind === "image" && mode === "image") {
      markSinglePreferenceEdited();
      setImageReferenceImages(append);
    } else if (kind === "image" && mode === "video") {
      markSinglePreferenceEdited();
      setVideoReferenceImages(append);
    }
    else if (kind === "image") setBatchReferenceImages(append);
    else if (kind === "audio") {
      if (mode === "video") markSinglePreferenceEdited();
      setVideoReferenceAudios(append);
    } else {
      if (mode === "video") markSinglePreferenceEdited();
      setVideoReferenceVideos(append);
    }
  };

  const referenceCount = (kind: MediaReferenceKind) => {
    if (kind === "image" && mode === "image") {
      return new Set([...imageQuickReferenceImages, ...splitReferences(imageReferenceImages)]).size;
    }
    if (kind === "image" && mode === "video") {
      return new Set([...videoQuickReferenceImages, ...splitReferences(videoReferenceImages)]).size;
    }
    if (kind === "audio" && mode === "video") {
      return new Set([...videoQuickReferenceAudios, ...splitReferences(videoReferenceAudios)]).size;
    }
    const value = kind === "image" ? batchReferenceImages : videoReferenceVideos;
    return splitReferences(value).length;
  };

  const uploadReference = async (kind: MediaReferenceKind) => {
    if (uploadInProgressRef.current || pendingRef.current !== null || requestingRef.current
      || quickReferenceActionRef.current || savingQuickReferencePreferencesRef.current
      || savingBatchPreferencesRef.current) return;
    const file = referenceFiles[kind];
    if (file === undefined) return;
    if (referenceCount(kind) >= REFERENCE_COUNT_LIMIT[kind]) {
      setReferenceUploadStatus({
        kind,
        status: "failed",
        message: `参考${REFERENCE_KIND_LABELS[kind]}已达到 ${REFERENCE_COUNT_LIMIT[kind]} 项上限，请先调整列表。`,
      });
      return;
    }
    const signature = formSignature;
    let run: ReturnType<typeof makeReferenceUploadRun>;
    try {
      run = makeReferenceUploadRun();
    } catch (error) {
      setReferenceUploadStatus({ kind, status: "failed", message: describeFailure(error) });
      return;
    }

    uploadInProgressRef.current = true;
    setUploadingReference(true);
    setReferenceUploadStatus({ kind, status: "uploading", message: `正在上传${REFERENCE_KIND_LABELS[kind]}…` });
    try {
      const url = await run.upload(kind, file, new AbortController().signal);
      if (!run.isCurrent() || identityRef.current.signature !== signature) return;
      appendReferenceUrl(kind, url);
      setReferenceUploadStatus({ kind, status: "uploaded", message: `已上传并添加到当前参考${REFERENCE_KIND_LABELS[kind]}列表。` });
      setReferenceFiles((current) => {
        const next = { ...current };
        delete next[kind];
        return next;
      });
      const input = referenceFileInputs.current[kind];
      if (input !== null && input !== undefined) input.value = "";
    } catch (error) {
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      if (!run.isCurrent() || identityRef.current.signature !== signature) return;
      const unknown = error instanceof MediaReferenceUploadError && error.kind === "unknown";
      setReferenceUploadStatus({
        kind,
        status: unknown ? "unknown" : "failed",
        message: unknown
          ? `${describeFailure(error)}系统不会自动重传；如需重试，请再次明确点击上传。`
          : describeFailure(error),
      });
    } finally {
      uploadInProgressRef.current = false;
      setUploadingReference(false);
      if (!run.isCurrent() || identityRef.current.signature !== signature) {
        setReferenceUploadStatus((current) => current?.status === "uploading" ? null : current);
      }
    }
  };

  const singleTarget = (): MediaGenerationSingleTarget => {
    if (singleTargetMode === "standalone") return Object.freeze({ kind: "standalone" });
    if (singleTargetMode === "frame_message") {
      return Object.freeze({ kind: "frame_message", chapter_id: selectedChapterId, storyboard_asset_id: singleStoryboardAssetId });
    }
    return Object.freeze({ kind: "asset_message", chapter_id: selectedChapterId, asset_type: singleAssetType, asset_id: singleAssetId });
  };

  const submitSingleGenerationGroup = async (generationMode: SingleMediaPreferenceMode) => {
    if (pendingRef.current !== null || requestingRef.current || uploadInProgressRef.current
      || quickReferenceActionRef.current || savingQuickReferencePreferencesRef.current
      || savingBatchPreferencesRef.current || singlePreferenceMode !== generationMode
      || singleSelectionIssue !== null
      || (generationMode === "image" && (selectedImageModel === null || imagePrompt.trim() === ""))
      || (generationMode === "video" && (selectedVideoModel === null || videoPrompt.trim() === ""))) return;
    const signature = formSignature;
    let run: MediaGenerationRun;
    try {
      run = makeRun(true);
    } catch (error) {
      setOutcome({ signature, message: describeFailure(error) });
      return;
    }

    const target = singleTarget();
    const items: SingleMediaGenerationItem[] = generationMode === "image"
      ? Array.from({ length: singleGenerationCount }, (_, index) => ({
        index,
        status: "waiting" as const,
        input: Object.freeze({
          modelId: selectedImageModel!.id,
          prompt: imagePrompt,
          target,
          ...(imageSize === "" ? {} : { size: imageSize }),
          referenceImages: Object.freeze([...new Set([...imageQuickReferenceImages, ...splitReferences(imageReferenceImages)])]),
          idempotencyKey: crypto.randomUUID(),
        }),
      }))
      : Array.from({ length: singleGenerationCount }, (_, index) => ({
        index,
        status: "waiting" as const,
        input: Object.freeze({
          modelId: selectedVideoModel!.id,
          prompt: videoPrompt,
          target,
          ...(videoSize === "" ? {} : { size: videoSize }),
          ...(videoDuration === "" ? {} : { duration: Number(videoDuration) }),
          ...(videoResolution === "" ? {} : { resolution: videoResolution }),
          referenceImages: Object.freeze([...new Set([...videoQuickReferenceImages, ...splitReferences(videoReferenceImages)])]),
          referenceAudios: Object.freeze([...new Set([...videoQuickReferenceAudios, ...splitReferences(videoReferenceAudios)])]),
          referenceVideos: Object.freeze(splitReferences(videoReferenceVideos)),
          idempotencyKey: crypto.randomUUID(),
        }),
      }));
    const operationOwnerId = beginGenerationOperation();
    const group: SingleMediaGenerationGroup = {
      mode: generationMode,
      signature,
      ownerKey: singleOwnerContextKey,
      ownerServices: services,
      operationOwnerId,
      run,
      items,
    };
    publishSingleGenerationGroup(group);
    acceptedReceiptContextRef.current = null;
    setOutcome(null);
    await submitSingleGenerationGroupFrom(group, 0);
  };

  const submitImage = () => submitSingleGenerationGroup("image");
  const submitVideo = () => submitSingleGenerationGroup("video");

  const submitBatch = async () => {
    if (pendingRef.current !== null || requestingRef.current || savingBatchPreferencesRef.current
      || savingQuickReferencePreferencesRef.current || quickReferenceActionRef.current
      || selectedSeries === null || selectedChapter === null || selectedChapterId === ""
      || directoryLoading || directoryError !== null || batchReferenceIssues.length > 0
      || selectedBatchAssets.length < 1 || selectedBatchAssets.length > 64
      || selectedBatchAssets.some((asset) => batchSelectionError(
        asset.type,
        batchSelectionsRef.current[asset.type],
        imageModels,
        prompts,
      ) !== null || batchPreferenceWarningBlocksSubmission(
        asset.type,
        batchSelectionsRef.current[asset.type],
        batchPreferenceWarnings,
        imageModels,
      ))) return;
    batchPreferenceRestoreGenerationRef.current += 1;
    const rawKey = crypto.randomUUID();
    const signature = formSignature;
    let run: MediaGenerationRun;
    try {
      run = makeRun();
    } catch (error) {
      setOutcome({ signature, message: describeFailure(error) });
      return;
    }
    const frozenSelections = Object.freeze(Object.fromEntries(BATCH_ASSET_TYPES.map((type) => (
      [type, Object.freeze({ ...batchSelectionsRef.current[type] })]
    ))) as BatchGenerationSelections);
    const assets: readonly MediaGenerationBatchAssetInput[] = Object.freeze(selectedBatchAssets.map((asset) => {
      const context = batchStoryboardContextByKey.get(`${asset.type}:${asset.id}`);
      const references = context?.referenceImages ?? mergeBatchReferenceImages(publicBatchReferences);
      return Object.freeze({
        type: asset.type,
        id: asset.id,
        name: asset.name,
        description: context?.description ?? asset.description,
        modelId: frozenSelections[asset.type].modelId,
        promptId: frozenSelections[asset.type].promptId,
        ...(frozenSelections[asset.type].size === "" ? {} : { size: frozenSelections[asset.type].size }),
        referenceImages: Object.freeze([...references]),
      });
    }));
    const input = Object.freeze({ chapterId: selectedChapterId, assets, idempotencyKey: rawKey });
    const expectation: MediaGenerationLookupExpectation = {
      operation: "image.batch",
      idempotencyKey: rawKey,
      batchAssets: Object.freeze(assets.map((asset) => Object.freeze({ type: asset.type, id: asset.id }))),
    };
    const operationOwnerId = beginGenerationOperation();
    const submission: PendingSubmission = {
      operation: "image.batch",
      idempotencyKey: rawKey,
      expectation,
      run,
      signature,
      submissionMessage: "批量素材图片请求",
      operationOwnerId,
      ownerKey: singleOwnerContextKey,
    };
    activeBatchSubmissionRef.current = submission;
    const controller = makeGenerationController();
    setOutcome(null);
    updateRequesting(operationOwnerId, true);
    try {
      const receipt = await run.submitBatch(input, controller.signal);
      if (isGenerationOperationCurrent(operationOwnerId, run)) {
        if (activeBatchSubmissionRef.current?.operationOwnerId === operationOwnerId) {
          activeBatchSubmissionRef.current = null;
        }
        applyReceipt(signature, receipt);
        finishGenerationOperation(operationOwnerId);
      }
    } catch (error) {
      if (!isGenerationOperationCurrent(operationOwnerId, run)) return;
      if (activeBatchSubmissionRef.current?.operationOwnerId === operationOwnerId) {
        activeBatchSubmissionRef.current = null;
      }
      if (isUnauthorized(error)) {
        onUnauthorized();
        finishGenerationOperation(operationOwnerId);
        return;
      }
      if (error instanceof MediaGenerationError && error.kind === "unknown-outcome") {
        const unknownSubmission = { ...submission, submissionMessage: describeFailure(error) };
        updatePending(unknownSubmission);
        setOutcome({ signature, message: `${describeFailure(error)} 提交状态待确认，正在核对本次提交；不会自动重新提交。`, pendingLookup: true });
        await lookupPending(unknownSubmission);
        return;
      }
      setOutcome({ signature, message: describeFailure(error) });
      finishGenerationOperation(operationOwnerId);
    } finally {
      releaseGenerationController(controller);
      updateRequesting(operationOwnerId, false);
      if (activeBatchSubmissionRef.current?.operationOwnerId === operationOwnerId) {
        activeBatchSubmissionRef.current = null;
      }
      if (!pendingBelongsToOperation(operationOwnerId)) finishGenerationOperation(operationOwnerId);
    }
  };

  const saveBatchPreferences = async () => {
    const port: UserPreferencesPort | undefined = services.userPreferences;
    if (port === undefined || savingBatchPreferencesRef.current || savingQuickReferencePreferencesRef.current
      || quickReferenceActionRef.current || requestingRef.current || pendingRef.current !== null) return;
    const edits = emptyBatchEdits();
    for (const type of BATCH_ASSET_TYPES) {
      edits[type] = new Set(batchPreferenceEditsRef.current[type]);
    }
    const checkEdits = new Set(batchCheckEditsRef.current);
    if (!BATCH_ASSET_TYPES.some((type) => edits[type].size > 0) && checkEdits.size === 0) {
      setBatchPreferenceMessage("没有需要保存的批量选项修改。");
      return;
    }
    batchPreferenceRestoreGenerationRef.current += 1;
    const selections = Object.freeze(Object.fromEntries(BATCH_ASSET_TYPES.map((type) => (
      [type, Object.freeze({ ...batchSelectionsRef.current[type] })]
    ))) as BatchGenerationSelections);
    const checks = Object.freeze({ ...batchChecksRef.current });
    const capturedServices = services;
    const capturedUserId = userId;
    const capturedSessionGeneration = sessionGeneration;
    const capturedSignature = identityRef.current.signature;
    const controller = new AbortController();
    batchPreferenceControllerRef.current = controller;
    savingBatchPreferencesRef.current = true;
    setSavingBatchPreferences(true);
    setBatchPreferenceMessage("正在读取当前账户偏好并合并本次修改…");

    const isCurrentSave = () => preferenceScopeIsCurrent(
      capturedServices,
      capturedUserId,
      capturedSessionGeneration,
    ) && identityRef.current.signature === capturedSignature
      && batchPreferenceControllerRef.current === controller;
    const clearSavedEdits = () => {
      for (const type of BATCH_ASSET_TYPES) {
        for (const field of edits[type]) {
          if (batchSelectionsRef.current[type][field] === selections[type][field]) {
            batchPreferenceEditsRef.current[type].delete(field);
          }
        }
      }
      for (const field of checkEdits) {
        if (batchChecksRef.current[field] === checks[field]) batchCheckEditsRef.current.delete(field);
      }
    };

    try {
      let currentPreferences: UserPreferences;
      try {
        currentPreferences = await port.get(controller.signal);
      } catch (error) {
        if (!isCurrentSave()) return;
        if (isUnauthorized(error)) onUnauthorized();
        else setBatchPreferenceMessage(`读取账户偏好失败，未保存任何内容：${describeFailure(error)}`);
        return;
      }
      if (!isCurrentSave()) return;

      const update = mergeBatchPreferenceEdits(currentPreferences, selections, edits, checks, checkEdits);
      try {
        const receipt = await port.update(update, controller.signal);
        if (!isCurrentSave()) return;
        if (!receipt.updatedKeys.includes("batch_gen_prefs")) {
          setBatchPreferenceMessage("服务器没有确认批量选项已保存；本页保留了修改，可再次明确点击保存。");
          return;
        }
        clearSavedEdits();
        setBatchPreferenceMessage("批量生成选项已保存到当前账号。");
      } catch (error) {
        if (!isCurrentSave()) return;
        if (isUnauthorized(error)) {
          onUnauthorized();
          return;
        }
        const definiteClientRejection = error instanceof ApiError
          && error.kind === "http"
          && error.status !== null
          && error.status >= 400
          && error.status < 500
          && error.status !== 408;
        if (definiteClientRejection) {
          setBatchPreferenceMessage(`批量选项未保存：${describeFailure(error)}`);
          return;
        }
        setBatchPreferenceMessage("保存结果尚未确认，正在只读核对账户偏好；不会自动重复保存。");
        try {
          const latestPreferences = await port.get(controller.signal);
          if (!isCurrentSave()) return;
          if (batchPreferenceEditsMatch(latestPreferences, selections, edits, checks, checkEdits)) {
            clearSavedEdits();
            setBatchPreferenceMessage("保存请求没有直接回执；只读核对发现账户偏好已包含本次修改。");
          } else {
            setBatchPreferenceMessage("保存结果仍未确认；已只读核对账户偏好，本页保留修改且不会自动重试。");
          }
        } catch (readError) {
          if (!isCurrentSave()) return;
          if (isUnauthorized(readError)) onUnauthorized();
          else setBatchPreferenceMessage(`保存结果仍未确认；只读核对也失败：${describeFailure(readError)}`);
        }
      }
    } finally {
      if (batchPreferenceControllerRef.current === controller) {
        batchPreferenceControllerRef.current = null;
        savingBatchPreferencesRef.current = false;
        if (preferenceScopeIsCurrent(capturedServices, capturedUserId, capturedSessionGeneration)) {
          setSavingBatchPreferences(false);
        }
      }
    }
  };

  const saveQuickReferencePreferences = async () => {
    const port: UserPreferencesPort | undefined = services.userPreferences;
    if (port === undefined || savingQuickReferencePreferencesRef.current || savingBatchPreferencesRef.current
      || requestingRef.current || pendingRef.current !== null || quickReferenceActionRef.current) return;
    const edits = new Set(quickReferenceEditsRef.current);
    if (edits.size === 0) {
      setQuickReferencePreferenceMessage("没有需要保存的快速引用设置修改。");
      return;
    }

    quickReferenceRestoreGenerationRef.current += 1;
    quickReferencePreferenceControllerRef.current?.abort();
    const settings = quickReferenceSettingsRef.current;
    const capturedServices = services;
    const capturedUserId = userId;
    const capturedSessionGeneration = sessionGeneration;
    const controller = new AbortController();
    quickReferencePreferenceControllerRef.current = controller;
    savingQuickReferencePreferencesRef.current = true;
    setSavingQuickReferencePreferences(true);
    setQuickReferencePreferenceLoading(false);
    setQuickReferencePreferenceMessage("正在读取当前账户偏好并合并本次修改…");

    const isCurrentSave = () => preferenceScopeIsCurrent(
      capturedServices,
      capturedUserId,
      capturedSessionGeneration,
    ) && quickReferencePreferenceControllerRef.current === controller;
    const clearSavedEdits = () => {
      const current = quickReferenceEditsRef.current;
      const remaining = new Set(current);
      for (const key of edits) {
        const separator = key.indexOf(":");
        const modeName = key.slice(0, separator) as QuickReferenceMode;
        const field = key.slice(separator + 1) as QuickReferenceField;
        if (quickReferenceSettingsRef.current[modeName][field] === settings[modeName][field]) remaining.delete(key);
      }
      quickReferenceEditsRef.current = remaining;
      setQuickReferenceEdits(remaining);
    };

    const verifyUnknown = async (message: string) => {
      setQuickReferencePreferenceMessage(`${message} 正在只读核对账户偏好；不会自动重复保存。`);
      try {
        const latest = await port.get(controller.signal);
        if (!isCurrentSave()) return;
        if (quickReferenceEditsMatch(latest, settings, edits)) {
          clearSavedEdits();
          setQuickReferencePreferenceMessage("保存请求没有直接回执；只读核对发现账户偏好已包含本次修改。");
        } else {
          setQuickReferencePreferenceMessage("保存结果仍未确认；已只读核对账户偏好，本页保留修改且不会自动重试。");
        }
      } catch (error) {
        if (!isCurrentSave()) return;
        if (isUnauthorized(error)) onUnauthorized();
        else setQuickReferencePreferenceMessage(`保存结果仍未确认；只读核对也失败：${describeFailure(error)}`);
      }
    };

    try {
      let latestPreferences: UserPreferences;
      try {
        latestPreferences = await port.get(controller.signal);
      } catch (error) {
        if (!isCurrentSave()) return;
        if (isUnauthorized(error)) onUnauthorized();
        else setQuickReferencePreferenceMessage(`读取账户偏好失败，未保存任何内容：${describeFailure(error)}`);
        return;
      }
      if (!isCurrentSave()) return;

      const update = mergeQuickReferencePreferences(latestPreferences, settings, edits);
      try {
        const receipt = await port.update(update, controller.signal);
        if (!isCurrentSave()) return;
        if (!receipt.updatedKeys.includes("quick_ref_settings")) {
          await verifyUnknown("服务器没有确认快速引用设置已保存。");
          return;
        }
        clearSavedEdits();
        setQuickReferencePreferenceMessage("快速引用设置已保存到当前账号。");
      } catch (error) {
        if (!isCurrentSave()) return;
        if (isUnauthorized(error)) {
          onUnauthorized();
          return;
        }
        const definiteClientRejection = error instanceof ApiError
          && error.kind === "http"
          && error.status !== null
          && error.status >= 400
          && error.status < 500
          && error.status !== 408;
        if (definiteClientRejection) {
          setQuickReferencePreferenceMessage(`快速引用设置未保存：${describeFailure(error)}`);
          return;
        }
        await verifyUnknown("保存结果尚未确认。");
      }
    } finally {
      if (quickReferencePreferenceControllerRef.current === controller) {
        quickReferencePreferenceControllerRef.current = null;
        savingQuickReferencePreferencesRef.current = false;
        if (preferenceScopeIsCurrent(capturedServices, capturedUserId, capturedSessionGeneration)) {
          setSavingQuickReferencePreferences(false);
        }
      }
    }
  };

  const applyQuickReference = async () => {
    if (quickReferenceActionRef.current || isFrozen || quickReferencePreferenceLoading
      || savingQuickReferencePreferencesRef.current || savingBatchPreferencesRef.current
      || services.userPreferences === undefined || mode === "batch"
      || singleTargetMode === "standalone" || selectedSeries === null || selectedChapter === null
      || selectedChapterId === "" || selectedChapter.series_id !== selectedSeriesId
      || directoryLoading || directoryError !== null) return;

    if (quickReferencePreferenceReadFailed
      && !window.confirm("无法确认账号保存的快速引用设置。本页开关可能仍是默认值；仍按当前开关填入吗？")) return;

    let target: QuickReferenceTarget;
    if (singleTargetMode === "frame_message") {
      if (singleStoryboardAssetId === "") return;
      target = { kind: "frame_message", storyboardAssetId: singleStoryboardAssetId };
    } else {
      if (singleAssetId === "") return;
      target = { kind: "asset_message", assetType: singleAssetType, assetId: singleAssetId };
    }

    const isImage = mode === "image";
    const currentPrompt = isImage ? imagePrompt : videoPrompt;
    const hasCurrentPrompt = currentPrompt.trim() !== "";
    const hasCurrentReferences = (isImage
      ? imageQuickReferenceImages.length + splitReferences(imageReferenceImages).length
      : videoQuickReferenceImages.length + splitReferences(videoReferenceImages).length) > 0
      || (!isImage && (videoQuickReferenceAudios.length + splitReferences(videoReferenceAudios).length > 0
        || splitReferences(videoReferenceVideos).length > 0));
    if ((hasCurrentPrompt || hasCurrentReferences)
      && !window.confirm("快速引用会替换当前提示词和参考素材列表。要继续吗？")) return;

    const capturedServices = services;
    const capturedUserId = userId;
    const capturedSessionGeneration = sessionGeneration;
    const capturedSignature = identityRef.current.signature;
    const capturedMode: QuickReferenceMode = isImage ? "image" : "video";
    const capturedPreferenceContextKey = singlePreferenceContextKey;
    const capturedSettings = Object.freeze({ ...quickReferenceSettingsRef.current[capturedMode] });
    const controller = new AbortController();
    quickReferenceActionControllerRef.current = controller;
    quickReferenceActionRef.current = true;
    setPreparingQuickReference(true);
    setQuickReferenceActionMessage("正在准备当前章节的快速引用…");

    const isCurrentAction = () => mountedRef.current
      && !controller.signal.aborted
      && quickReferenceActionControllerRef.current === controller
      && identityRef.current.services === capturedServices
      && identityRef.current.userId === capturedUserId
      && identityRef.current.sessionGeneration === capturedSessionGeneration
      && identityRef.current.signature === capturedSignature;

    try {
      const frameStoryboardId = target.kind === "frame_message"
        ? target.storyboardAssetId
        : target.assetType === "storyboard" ? target.assetId : null;
      const frame: StoryboardFrame | null = frameStoryboardId === null
        ? null
        : findQuickReferenceFrame(selectedChapter.content, frameStoryboardId);
      if (target.kind === "frame_message" && frame === null) {
        setQuickReferenceActionMessage("当前章节没有找到该分镜的原始引用关系，未更改提示词或参考素材。");
        return;
      }

      let fusedAssets = [] as Awaited<ReturnType<NonNullable<WorkspaceServices["listFusedAssets"]>>>;
      if (requiresQuickReferenceFusedRead(frame, capturedSettings)) {
        const listFusedAssets = capturedServices.listFusedAssets;
        if (listFusedAssets === undefined) {
          setQuickReferenceActionMessage("当前服务尚未接入融合素材目录，无法确认快速引用；当前输入未更改。");
          return;
        }
        try {
          fusedAssets = (await listFusedAssets(selectedSeriesId, controller.signal))
            .filter((asset) => asset.series_id === selectedSeriesId);
        } catch (error) {
          if (!isCurrentAction()) return;
          if (isUnauthorized(error)) onUnauthorizedRef.current();
          else setQuickReferenceActionMessage("读取融合素材目录失败，快速引用未应用；请稍后重试。");
          return;
        }
        if (!isCurrentAction()) return;
      }

      const plan = buildQuickReferencePlan({
        mode: capturedMode,
        target,
        settings: capturedSettings,
        chapterContent: selectedChapter.content,
        storyboardAssets,
        characters: characterAssets,
        scenes: sceneAssets,
        props: propAssets,
        fusedAssets,
      });
      if (plan === null) {
        setQuickReferenceActionMessage("当前目标没有可用的章节上下文，快速引用未应用。");
        return;
      }
      const styleCategory = capturedMode === "video"
        ? "video"
        : target.kind === "frame_message" ? "storyboard" : target.assetType;
      const style = resolveQuickReferenceStyle({
        series: selectedSeries,
        prompts,
        category: styleCategory,
        ...(capturedMode === "video" ? { fallbackCategories: ["storyboard"] as const } : {}),
      });
      const imageReferences = [...plan.imageReferences];
      if (style.imageUrl !== null && !imageReferences.includes(style.imageUrl)) imageReferences.push(style.imageUrl);
      if (plan.baseDescription.trim() === "" && imageReferences.length === 0
        && plan.audioReferences.length === 0 && plan.voiceNotes.length === 0 && style.promptText === "") {
        setQuickReferenceActionMessage("当前上下文没有可引用的描述或素材，输入未更改。");
        return;
      }
      if (imageReferences.length > REFERENCE_COUNT_LIMIT.image
        || plan.audioReferences.length > REFERENCE_COUNT_LIMIT.audio) {
        setQuickReferenceActionMessage("当前上下文的参考素材超过可提交上限；输入未更改，请先缩小引用范围。");
        return;
      }
      if (!isImage && (!imageReferences.every(isSafeHttpMediaReference)
        || !plan.audioReferences.every(isSafeHttpMediaReference))) {
        setQuickReferenceActionMessage("当前视频服务只接受安全的公开 HTTP(S) 参考素材；受保护上传地址或内嵌图片暂未接通，输入未更改。");
        return;
      }

      const preparedImages: string[] = [];
      const finalImageIndexes = new Map<string, number>();
      const indexByPreparedImage = new Map<string, number>();
      let totalImageBytes = 0;
      for (const source of imageReferences) {
        let prepared = source;
        if (capturedMode === "image" && isSafeCanvasUploadPath(source)) {
          if (capturedServices.canvasData === undefined) {
            setQuickReferenceActionMessage("当前服务未接入受保护图片读取，快速引用未应用；当前输入未更改。");
            return;
          }
          try {
            const blob = await capturedServices.canvasData.readImage(source, controller.signal);
            prepared = await readCanvasImageBlobAsDataUrl(blob, controller.signal);
          } catch (error) {
            if (!isCurrentAction()) return;
            if (isUnauthorized(error)) onUnauthorizedRef.current();
            else setQuickReferenceActionMessage("读取章节关联图片失败，快速引用未应用；当前输入未更改。");
            return;
          }
          if (!isCurrentAction()) return;
        } else if (capturedMode === "video" ? !isSafeHttpMediaReference(source) : !isValidCanvasImageReference(source)) {
          setQuickReferenceActionMessage("章节包含无法安全提交的图片地址，快速引用未应用；当前输入未更改。");
          return;
        }
        if (!isValidCanvasImageReference(prepared)) {
          setQuickReferenceActionMessage("关联图片未能转换为可提交的参考图，快速引用未应用；当前输入未更改。");
          return;
        }
        let index = indexByPreparedImage.get(prepared);
        if (index === undefined) {
          const bytes = new TextEncoder().encode(prepared).byteLength;
          totalImageBytes += bytes;
          if (bytes > 8 * 1024 * 1024 || totalImageBytes > 32 * 1024 * 1024) {
            setQuickReferenceActionMessage("准备后的参考图片超过当前服务的单图或总量限制，快速引用未应用。");
            return;
          }
          preparedImages.push(prepared);
          index = preparedImages.length;
          indexByPreparedImage.set(prepared, index);
        }
        finalImageIndexes.set(source, index);
      }
      if (!isCurrentAction()) return;

      const finalAudioIndexes = new Map(plan.audioReferences.map((url, index) => [url, index + 1] as const));
      const description = formatQuickReferenceDescription(plan, finalImageIndexes, finalAudioIndexes, style);
      let matchedDuration: ReturnType<typeof matchQuickReferenceDuration> | null = null;
      if (!isImage && capturedSettings.matchDuration) {
        matchedDuration = matchQuickReferenceDuration(description, videoOptions.durations);
      }
      if (!isCurrentAction()) return;

      editedSinglePreferenceContextsRef.current.add(capturedPreferenceContextKey);
      setSinglePreferenceOffer(null);
      if (isImage) {
        setSelectedImagePromptId("");
        setImagePrompt(description);
        setImageReferenceImages("");
        setImageQuickReferenceImages(preparedImages);
      } else {
        setSelectedVideoPromptId("");
        setVideoPrompt(description);
        setVideoReferenceImages("");
        setVideoQuickReferenceImages(preparedImages);
        setVideoReferenceAudios("");
        setVideoQuickReferenceAudios([...plan.audioReferences]);
        setVideoReferenceVideos("");
        if (matchedDuration?.duration !== null && matchedDuration?.duration !== undefined) {
          setVideoDuration(String(matchedDuration.duration));
        }
      }
      const durationMessage = matchedDuration?.message ?? null;
      setQuickReferenceActionMessage(durationMessage === null
        ? "已填入当前章节快速引用；尚未提交生成。"
        : `已填入当前章节快速引用；尚未提交生成。${durationMessage}`);
    } catch (error) {
      if (!isCurrentAction()) return;
      if (isUnauthorized(error)) onUnauthorizedRef.current();
      else setQuickReferenceActionMessage("快速引用准备失败，当前提示词和参考列表未更改。");
    } finally {
      if (quickReferenceActionControllerRef.current === controller) {
        quickReferenceActionControllerRef.current = null;
        quickReferenceActionRef.current = false;
        if (preferenceScopeIsCurrent(capturedServices, capturedUserId, capturedSessionGeneration)) {
          setPreparingQuickReference(false);
        }
      }
    }
  };

  const cancelQuickReferencePreparation = () => {
    const controller = quickReferenceActionControllerRef.current;
    if (controller === null) return;
    quickReferenceActionControllerRef.current = null;
    quickReferenceActionRef.current = false;
    setPreparingQuickReference(false);
    setQuickReferenceActionMessage("准备已取消；当前提示词和参考列表未更改。");
    controller.abort();
  };

  const selectImagePrompt = (id: string) => {
    setSelectedImagePromptId(id);
    if (id === "") setImagePrompt("");
    else {
      const selectedPrompt = promptsForSingleMode("image", prompts).find((prompt) => prompt.id === id);
      if (selectedPrompt !== undefined) setImagePrompt(promptText(selectedPrompt));
    }
  };

  const selectVideoPrompt = (id: string) => {
    setSelectedVideoPromptId(id);
    if (id === "") setVideoPrompt("");
    else {
      const selectedPrompt = promptsForSingleMode("video", prompts).find((prompt) => prompt.id === id);
      if (selectedPrompt !== undefined) setVideoPrompt(promptText(selectedPrompt));
    }
  };

  const toggleBatchAsset = (key: string, checked: boolean) => {
    setSelectedBatchKeys((current) => {
      if (checked) return current.length >= 64 || current.includes(key) ? current : [...current, key];
      return current.filter((item) => item !== key);
    });
  };

  const selectedAssetForSingle = singleAssetChoices.find((asset) => asset.id === singleAssetId);
  const batchPromptOptions = (type: MediaGenerationAssetType) => prompts.filter((prompt) => (
    prompt.category === BATCH_PROMPT_CATEGORIES[type]
  ));
  const selectedBatchTypes = new Set(selectedBatchAssets.map((asset) => asset.type));
  const batchSubmissionBlocked = directoryLoading || directoryError !== null || batchReferenceIssues.length > 0
    || [...selectedBatchTypes].some((type) => batchSelectionError(
    type,
    batchSelections[type],
    imageModels,
    prompts,
  ) !== null || batchPreferenceWarningBlocksSubmission(type, batchSelections[type], batchPreferenceWarnings, imageModels));
  const hasBatchPreferenceEdits = batchCheckEditsRef.current.size > 0
    || BATCH_ASSET_TYPES.some((type) => batchPreferenceEditsRef.current[type].size > 0);
  const hasQuickReferenceEdits = quickReferenceEdits.size > 0;
  const isImageMode = mode === "image";
  const singleModel = isImageMode ? selectedImageModel : selectedVideoModel;
  const singleOptions = isImageMode ? imageOptions : videoOptions;
  const singlePrompt = isImageMode ? imagePrompt : videoPrompt;
  const singlePromptId = isImageMode ? selectedImagePromptId : selectedVideoPromptId;
  const singlePromptOptions = isImageMode ? currentImagePromptOptions : currentVideoPromptOptions;
  const singleReferences = isImageMode ? imageReferenceImages : videoReferenceImages;
  const singleReferenceSetter = isImageMode ? setImageReferenceImages : setVideoReferenceImages;
  const singleImageReferences = [...new Set([
    ...(isImageMode ? imageQuickReferenceImages : videoQuickReferenceImages),
    ...splitReferences(singleReferences),
  ])];
  const singleAudioReferences = [...new Set([...videoQuickReferenceAudios, ...splitReferences(videoReferenceAudios)])];
  const singleVideoReferences = splitReferences(videoReferenceVideos);
  const singleReferenceLimitMessage = singleImageReferences.length > REFERENCE_COUNT_LIMIT.image
    ? "参考图片超过64项上限，请先减少引用。"
    : !isImageMode && singleAudioReferences.length > REFERENCE_COUNT_LIMIT.audio
      ? "参考音频超过5项上限，请先减少引用。"
      : !isImageMode && singleVideoReferences.length > REFERENCE_COUNT_LIMIT.video
        ? "参考视频超过5项上限，请先减少引用。"
        : null;
  const isFrozen = pending !== null || pendingAction || requesting || uploadingReference || savingBatchPreferences || batchVideoActive
    || savingQuickReferencePreferences || preparingQuickReference;

  const renderReferenceUploader = (kind: MediaReferenceKind) => {
    const label = REFERENCE_KIND_LABELS[kind];
    const status = referenceUploadStatus?.kind === kind ? referenceUploadStatus : null;
    return (
      <>
        <label className="media-generation-wide">
          <span>选择参考{label}文件</span>
          <input
            accept={REFERENCE_FILE_ACCEPT[kind]}
            aria-label={`选择${label}文件`}
            disabled={isFrozen}
            onChange={(event) => setReferenceFile(kind, event.currentTarget.files?.[0])}
            ref={(element) => { referenceFileInputs.current[kind] = element; }}
            type="file"
          />
        </label>
        <button
          className="secondary-button media-generation-wide"
          disabled={isFrozen || referenceFiles[kind] === undefined || services.mediaReferenceUpload === undefined
            || referenceCount(kind) >= REFERENCE_COUNT_LIMIT[kind]}
          onClick={() => void uploadReference(kind)}
          type="button"
        >
          {uploadingReference && status?.status === "uploading" ? "上传中…" : `上传并添加${label}到参考列表`}
        </button>
        {status !== null && (
          <p className={`media-generation-note media-generation-wide${status.status === "failed" ? " media-generation-alert" : ""}`}
            role={status.status === "failed" ? "alert" : "status"}>{status.message}</p>
        )}
        {referenceCount(kind) >= REFERENCE_COUNT_LIMIT[kind] && (
          <p className="media-generation-note media-generation-wide" role="status">
            参考{label}最多 {REFERENCE_COUNT_LIMIT[kind]} 项，请先调整列表后再上传。
          </p>
        )}
        {services.mediaReferenceUpload === undefined && (
          <p className="media-generation-note media-generation-wide" role="status">参考素材上传服务尚未接入。</p>
        )}
      </>
    );
  };

  return (
    <section className="media-generation-page" aria-labelledby="media-generation-title">
      <header className="media-generation-heading">
        <div>
          <p className="eyebrow">图片与视频生成</p>
          <h1 id="media-generation-title">AI 生成</h1>
          <p>按所选模型提交图片、视频或一次性批量生成章节素材。任务受理后可到“我的任务”查看。</p>
        </div>
        <button className="secondary-button" onClick={onOpenTasks} type="button">我的任务</button>
      </header>

      <nav className="media-generation-tabs" aria-label="生成类型">
        <button aria-pressed={mode === "image"} disabled={isFrozen} onClick={() => setModeSafely("image")} type="button">普通图片</button>
        <button aria-pressed={mode === "video"} disabled={isFrozen} onClick={() => setModeSafely("video")} type="button">普通视频</button>
        <button aria-pressed={mode === "batch"} disabled={isFrozen} onClick={() => setModeSafely("batch")} type="button">批量素材生图</button>
        <button aria-pressed={mode === "batch-video"} disabled={isFrozen} onClick={() => setModeSafely("batch-video")} type="button">批量分镜视频</button>
      </nav>

      <QuickReferenceSettingsPanel
        disabled={isFrozen}
        hasEdits={hasQuickReferenceEdits}
        loading={quickReferencePreferenceLoading}
        message={quickReferencePreferenceMessage}
        onChange={updateQuickReferenceSetting}
        onSave={() => void saveQuickReferencePreferences()}
        saving={savingQuickReferencePreferences}
        serviceAvailable={services.userPreferences !== undefined}
        settings={quickReferenceSettings}
      />
      {quickReferencePreferenceReadFailed && (
        <p className="media-generation-alert" role="alert">
          无法读取账号快速引用设置。填入前会请你确认是否按本页当前开关继续。
        </p>
      )}

      {catalogError !== null && <p className="media-generation-alert" role="alert">配置目录读取失败：{catalogError}</p>}
      {workbenchSourceNotice !== null && <p className="media-generation-note" role="status">{workbenchSourceNotice}</p>}
      {loading && <p role="status">正在读取剧集、模型和提示词目录…</p>}
      {requestedWorkbenchSource !== null && (
        <section className="media-generation-note" aria-label="待应用的左侧来源">
          <p role="status">{pending !== null || requesting || uploadingReference
            ? "媒体操作仍在进行。原目标、请求和回执保持不变；左侧新来源已暂存。"
            : requestedWorkbenchSource.needsConfirmation && !requestedWorkbenchSource.confirmed
              ? "媒体操作已结束。请确认后再应用刚才选择的左侧来源。"
              : mode !== "image" && mode !== "video"
                ? "来源已确认。请先选择普通图片或普通视频，再应用为对应目标。"
                : "正在核对左侧来源与最新章节目录…"}</p>
          {requestedWorkbenchSource.needsConfirmation && !requestedWorkbenchSource.confirmed
            && pending === null && !requesting && !uploadingReference && (
            <button className="secondary-button" onClick={() => setRequestedWorkbenchSource((current) => (
              current === null ? null : { ...current, confirmed: true }
            ))} type="button">应用左侧来源</button>
          )}
          <button className="text-button" onClick={() => setRequestedWorkbenchSource(null)} type="button">保留当前媒体目标</button>
        </section>
      )}
      {!loading && services.mediaGeneration === undefined && (
        <p className="media-generation-note" role="status">当前服务没有接入媒体生成提交接口，页面不会模拟生成结果。</p>
      )}

      {mode === "image" || mode === "video" ? (
        <div className="media-generation-card">
          <div className="media-generation-section-heading">
            <div><p className="eyebrow">{isImageMode ? "图片生成" : "视频生成"}</p><h2>{isImageMode ? "普通图片" : "普通视频"}</h2></div>
            <span>提交前会固定模型、目标与参考素材。</span>
          </div>
          {singleModePreferenceFeedback?.contextKey === singleModePreferenceContextKey && (
            <p className="media-generation-note media-generation-wide" role="status">{singleModePreferenceFeedback.message}</p>
          )}
          {singlePreferenceFeedback?.contextKey === singlePreferenceContextKey && (
            <p className="media-generation-note media-generation-wide" role="status">{singlePreferenceFeedback.message}</p>
          )}
          {singlePreferenceOffer?.contextKey === singlePreferenceContextKey && (
            <div className="media-generation-note media-generation-wide" role="group" aria-label="已记住的生成选项">
              <span>当前表单保留中。应用记住的选项只调整模型、提示词选择和支持的参数；若更换为记住的模板，会先确认替换正文。</span>
              <button className="secondary-button" disabled={isFrozen} onClick={applyRememberedSinglePreference} type="button">
                应用已记住的{isImageMode ? "图片" : "视频"}选项
              </button>
            </div>
          )}
          <div className="media-generation-fields">
            <label>
              <span>{isImageMode ? "图片模型" : "视频模型"}</span>
              <select disabled={loading || isFrozen} onChange={(event) => changeSingleModel(isImageMode ? "image" : "video", event.target.value)} value={isImageMode ? selectedImageModelId : selectedVideoModelId}>
                <option value="">{isImageMode ? "选择图片模型" : "选择视频模型"}</option>
                {(isImageMode ? imageModels : videoModels).map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
              </select>
            </label>
            <label>
              <span>提示词目录</span>
              <select disabled={isFrozen} onChange={(event) => changeSinglePrompt(isImageMode ? "image" : "video", event.target.value)} value={singlePromptId}>
                <option value="">手动输入</option>
                {singlePromptOptions.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}</option>)}
              </select>
            </label>
            <label className="media-generation-wide">
              <span>{isImageMode ? "图片提示词" : "视频提示词"}</span>
              <textarea disabled={isFrozen} onChange={(event) => {
                markSinglePreferenceEdited();
                if (isImageMode) setImagePrompt(event.target.value);
                else setVideoPrompt(event.target.value);
              }} rows={5} value={singlePrompt} />
            </label>

            {singleOptions.sizes.length > 0 && (
              <label><span>{isImageMode ? "图片尺寸" : "画面比例"}</span><select disabled={isFrozen} onChange={(event) => isImageMode
                ? changeSingleSize("image", event.target.value)
                : changeSingleSize("video", event.target.value)} value={isImageMode ? imageSize : videoSize}>
                {singleOptions.sizes.map((size) => <option key={size} value={size}>{size}</option>)}
              </select></label>
            )}
            {!isImageMode && videoOptions.durations.length > 0 && (
              <label><span>时长（秒）</span><select disabled={isFrozen} onChange={(event) => changeVideoDuration(event.target.value)} value={videoDuration}>
                {videoOptions.durations.map((duration) => <option key={duration} value={String(duration)}>{duration}</option>)}
              </select></label>
            )}
            {!isImageMode && videoOptions.resolutions.length > 0 && (
              <label><span>分辨率</span><select disabled={isFrozen} onChange={(event) => changeVideoResolution(event.target.value)} value={videoResolution}>
                {videoOptions.resolutions.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}
              </select></label>
            )}

            <label>
              <span>生成数量</span>
              <select disabled={isFrozen} onChange={(event) => changeSingleGenerationCount(event.target.value)} value={String(singleGenerationCount)}>
                {[1, 2, 3, 4].map((count) => (
                  <option key={count} value={String(count)}>{isImageMode ? `生成 ${count} 张` : `生成 ${count} 个视频`}</option>
                ))}
              </select>
            </label>

            <label>
              <span>{isImageMode ? "图片目标" : "视频目标"}</span>
              <select disabled={isFrozen} onChange={(event) => {
                recordWorkbenchTargetInteraction();
                setSingleTargetMode(event.target.value as SingleTargetMode);
              }} value={singleTargetMode}>
                <option value="standalone">{isImageMode ? "独立图片" : "独立视频"}</option>
                <option value="frame_message">章节分镜消息</option>
                <option value="asset_message">章节素材消息</option>
              </select>
            </label>
            {singleTargetMode !== "standalone" && (
              <>
                <label><span>剧集</span><select disabled={isFrozen} onChange={(event) => {
                  recordWorkbenchTargetInteraction();
                  setSelectedSeriesId(event.target.value);
                }} value={selectedSeriesId}>
                  <option value="">选择剧集</option>
                  {series.filter((item) => item.can_enter).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select></label>
                <label><span>章节</span><select disabled={isFrozen || chapterLoading || selectedSeriesId === ""} onChange={(event) => {
                  recordWorkbenchTargetInteraction();
                  setSelectedChapterId(event.target.value);
                  setSingleAssetId("");
                  setSingleStoryboardAssetId("");
                }} value={selectedChapterId}>
                  <option value="">选择章节</option>
                  {sortedChapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title}</option>)}
                </select></label>
              </>
            )}
            {singleTargetMode === "frame_message" && (
              <label className="media-generation-wide"><span>分镜目标</span><select disabled={isFrozen || directoryLoading || selectedChapterId === ""} onChange={(event) => {
                recordWorkbenchTargetInteraction();
                setSingleStoryboardAssetId(event.target.value);
              }} value={singleStoryboardAssetId}>
                <option value="">选择章节分镜</option>
                {storyboardAssets.map((asset) => <option key={asset.id} value={asset.id}>第 {asset.frame_index + 1} 镜 · {asset.name}</option>)}
              </select></label>
            )}
            {singleTargetMode === "asset_message" && (
              <>
                <label><span>素材类型</span><select disabled={isFrozen} onChange={(event) => {
                  recordWorkbenchTargetInteraction();
                  setSingleAssetType(event.target.value as MediaGenerationAssetType);
                }} value={singleAssetType}>
                  <option value="character">角色</option><option value="scene">场景</option><option value="prop">道具</option>
                  <option value="storyboard">分镜</option>
                </select></label>
                <label><span>目标素材</span><select disabled={isFrozen || directoryLoading || selectedChapterId === ""} onChange={(event) => {
                  recordWorkbenchTargetInteraction();
                  setSingleAssetId(event.target.value);
                }} value={singleAssetId}>
                  <option value="">选择素材</option>
                  {singleAssetChoices.map((asset) => <option key={asset.id} value={asset.id}>{asset.label}</option>)}
                </select></label>
                {selectedAssetForSingle !== undefined && <p className="media-generation-wide media-generation-target-detail">{selectedAssetForSingle.description || "该素材没有描述。"}</p>}
              </>
            )}
            {singleTargetMode !== "standalone" && (
              <div className="media-generation-actions media-generation-wide">
                <button className="secondary-button" disabled={isFrozen || loading || directoryLoading
                  || quickReferencePreferenceLoading
                  || services.userPreferences === undefined
                  || (singleTargetMode === "frame_message"
                    ? selectedChapterId === "" || singleStoryboardAssetId === ""
                    : selectedChapterId === "" || singleAssetId === "")}
                  onClick={() => void applyQuickReference()} type="button">
                  {preparingQuickReference ? "正在准备快速引用…" : "快速引用当前章节"}
                </button>
                {preparingQuickReference && <button className="secondary-button" onClick={cancelQuickReferencePreparation} type="button">取消准备</button>}
              </div>
            )}
            {quickReferenceActionMessage !== null && <p className="media-generation-note media-generation-wide" role="status">{quickReferenceActionMessage}</p>}
            <label><span>参考图片 URL（每行一项）</span><textarea disabled={isFrozen} onChange={(event) => {
              markSinglePreferenceEdited();
              singleReferenceSetter(event.target.value);
              setReferenceUploadStatus((current) => current?.kind === "image" ? null : current);
            }} rows={3} value={singleReferences} /></label>
            {(isImageMode ? imageQuickReferenceImages : videoQuickReferenceImages).length > 0 && (
              <p className="media-generation-note media-generation-wide" role="status">
                已准备 {(isImageMode ? imageQuickReferenceImages : videoQuickReferenceImages).length} 张章节参考图；图片地址不会显示在页面，提交时会与下面的手动列表一起发送。
              </p>
            )}
            {renderReferenceUploader("image")}
            {!isImageMode && <>
              <label><span>参考音频 URL（每行一项）</span><textarea disabled={isFrozen} onChange={(event) => {
                markSinglePreferenceEdited();
                setVideoReferenceAudios(event.target.value);
                setReferenceUploadStatus((current) => current?.kind === "audio" ? null : current);
              }} rows={3} value={videoReferenceAudios} /></label>
              {videoQuickReferenceAudios.length > 0 && (
                <p className="media-generation-note media-generation-wide" role="status">
                  已准备 {videoQuickReferenceAudios.length} 项章节角色音频参考；地址不会显示在页面。
                </p>
              )}
              {renderReferenceUploader("audio")}
              <label className="media-generation-wide"><span>参考视频 URL（每行一项）</span><textarea disabled={isFrozen} onChange={(event) => {
                markSinglePreferenceEdited();
                setVideoReferenceVideos(event.target.value);
                setReferenceUploadStatus((current) => current?.kind === "video" ? null : current);
              }} rows={3} value={videoReferenceVideos} /></label>
              {renderReferenceUploader("video")}
            </>}
          </div>
          {chapterError !== null && <p className="media-generation-alert" role="alert">章节读取失败：{chapterError}</p>}
          {directoryError !== null && <p className="media-generation-alert" role="alert">章节素材读取失败：{directoryError}</p>}
          {singleTargetMode !== "standalone" && directoryLoading && <p role="status">正在读取章节分镜和素材目录…</p>}
          {singleSelectionIssue !== null && <p className="media-generation-alert" role="alert">{singleSelectionIssue}</p>}
          {singleReferenceLimitMessage !== null && <p className="media-generation-alert" role="alert">{singleReferenceLimitMessage}</p>}
          <div className="media-generation-actions">
            <button className="primary-button" disabled={loading || isFrozen || singleModel === null || singlePrompt.trim() === ""
              || singleSelectionIssue !== null || singleReferenceLimitMessage !== null
              || (singleTargetMode === "frame_message" && (selectedChapterId === "" || singleStoryboardAssetId === ""))
              || (singleTargetMode === "asset_message" && (selectedChapterId === "" || singleAssetId === ""))}
              onClick={() => void (isImageMode ? submitImage() : submitVideo())} type="button">
              {pending?.operation === (isImageMode ? "image.single" : "video.single")
                ? "等待回执…" : isImageMode ? "提交图片任务" : "提交视频任务"}
            </button>
          </div>
        </div>
      ) : mode === "batch" ? (
        <div className="media-generation-card">
          <div className="media-generation-section-heading">
            <div><p className="eyebrow">批量生成</p><h2>批量素材生图</h2></div>
            <span>一次请求按选择顺序提交 1–64 项，不会拆成多次单图。</span>
          </div>
          <div className="media-generation-fields">
            <label><span>剧集</span><select disabled={isFrozen || loading} onChange={(event) => setSelectedSeriesId(event.target.value)} value={selectedSeriesId}>
              <option value="">选择剧集</option>
              {series.filter((item) => item.can_enter).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
            <label><span>章节</span><select disabled={isFrozen || chapterLoading || selectedSeriesId === ""} onChange={(event) => setSelectedChapterId(event.target.value)} value={selectedChapterId}>
              <option value="">选择章节</option>
              {sortedChapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title}</option>)}
            </select></label>
            <fieldset className="media-generation-batch-type media-generation-wide" disabled={isFrozen}>
              <legend>分镜引用检查</legend>
              <p className="media-generation-note">仅控制已选分镜所引用资产的缺图检查与自动参考图；不会移除已选角色、场景或道具任务。</p>
              {BATCH_CHECK_FIELDS.map((field) => (
                <label key={field}>
                  <input checked={batchChecks[field]} onChange={(event) => updateBatchCheck(field, event.target.checked)} type="checkbox" />
                  <span>检查{ASSET_TYPE_LABELS[field]}图片</span>
                </label>
              ))}
            </fieldset>
            {BATCH_ASSET_TYPES.map((type) => {
              const selection = batchSelections[type];
              const model = imageModels.find((candidate) => candidate.id === selection.modelId) ?? null;
              const sizes = modelOptions(model).sizes;
              const error = batchSelectionError(type, selection, imageModels, prompts);
              return (
                <fieldset className="media-generation-batch-type" key={type} disabled={isFrozen}>
                  <legend>{ASSET_TYPE_LABELS[type]}选项</legend>
                  <label><span>{ASSET_TYPE_LABELS[type]}模型</span><select disabled={loading || isFrozen}
                    onChange={(event) => updateBatchSelection(type, "modelId", event.target.value)} value={selection.modelId}>
                    <option value="">选择图片模型</option>
                    {imageModels.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}
                  </select></label>
                  <label><span>{ASSET_TYPE_LABELS[type]}提示词</span><select
                    onChange={(event) => updateBatchSelection(type, "promptId", event.target.value)} value={selection.promptId}>
                    <option value="">不使用提示词</option>
                    {batchPromptOptions(type).map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}</option>)}
                  </select></label>
                  <label><span>{ASSET_TYPE_LABELS[type]}图片尺寸</span><select
                    disabled={model === null || sizes.length === 0}
                    onChange={(event) => updateBatchSelection(type, "size", event.target.value)} value={selection.size}>
                    <option value="">{model === null ? "请先选择模型" : sizes.length === 0 ? "该模型未配置尺寸" : "选择图片尺寸"}</option>
                    {sizes.map((size) => <option key={size} value={size}>{size}</option>)}
                  </select></label>
                  {error !== null && <p className="media-generation-note media-generation-wide" role="status">{error}</p>}
                  {Object.values(batchPreferenceWarnings[type]).map((warning) => (
                    <p className="media-generation-alert media-generation-wide" key={warning} role="alert">{warning}</p>
                  ))}
                </fieldset>
              );
            })}
            <label className="media-generation-wide"><span>公共参考图片 URL（每行一项）</span><textarea disabled={isFrozen} onChange={(event) => {
              setBatchReferenceImages(event.target.value);
              setReferenceUploadStatus((current) => current?.kind === "image" ? null : current);
            }} rows={3} value={batchReferenceImages} /></label>
            {renderReferenceUploader("image")}
          </div>
          {chapterError !== null && <p className="media-generation-alert" role="alert">章节读取失败：{chapterError}</p>}
          {directoryError !== null && <p className="media-generation-alert" role="alert">章节素材读取失败：{directoryError}</p>}
          {selectedChapterId !== "" && directoryLoading && <p role="status">正在读取当前章节素材…</p>}
          {batchReferenceIssues.map((issue) => <p className="media-generation-alert" key={issue} role="alert">{issue}</p>)}
          {selectedChapterId !== "" && !directoryLoading && directoryError === null && (
            <fieldset className="media-generation-assets" disabled={isFrozen}>
              <legend>选择生成素材 · {selectedBatchAssets.length}/64</legend>
              {assetChoices.length === 0 ? <p>当前章节没有可选的分镜或素材。</p> : (
                <ol>
                  {assetChoices.map((asset) => {
                    const key = `${asset.type}:${asset.id}`;
                    const checked = selectedBatchKeys.includes(key);
                    return (
                      <li key={key}>
                        <label>
                          <input checked={checked} disabled={!checked && selectedBatchKeys.length >= 64}
                            onChange={(event) => toggleBatchAsset(key, event.target.checked)} type="checkbox" />
                          <span><strong>{asset.label}</strong><small>{ASSET_TYPE_LABELS[asset.type]}{asset.description ? ` · ${asset.description}` : ""}</small></span>
                        </label>
                        <span className="media-generation-order">{checked ? `第 ${selectedBatchKeys.indexOf(key) + 1} 项` : ""}</span>
                      </li>
                    );
                  })}
                </ol>
              )}
            </fieldset>
          )}
          <div className="media-generation-actions">
            <button className="secondary-button" disabled={isFrozen || batchPreferenceLoading || !hasBatchPreferenceEdits
              || services.userPreferences === undefined} onClick={() => void saveBatchPreferences()} type="button">
              {savingBatchPreferences ? "保存中…" : "记住批量选项"}
            </button>
            <button className="primary-button" disabled={loading || isFrozen || selectedSeries === null || selectedChapterId === ""
              || selectedBatchAssets.length === 0 || selectedBatchAssets.length > 64 || batchSubmissionBlocked}
              onClick={() => void submitBatch()} type="button">{pending?.operation === "image.batch" ? "等待回执…" : `提交 ${selectedBatchAssets.length} 项批量任务`}</button>
          </div>
          {batchPreferenceLoading && <p className="media-generation-note" role="status">正在读取当前账号的批量选项…</p>}
          {batchPreferenceMessage !== null && <p className="media-generation-note" role="status">{batchPreferenceMessage}</p>}
          <p className="media-generation-note">服务暂不可用时会显示对应状态；提交状态待确认时，可以重新核对本次提交。已受理的任务可在“我的任务”中查看。</p>
        </div>
      ) : (
        <BatchVideoGenerationPanel
          services={services}
          userId={userId}
          sessionGeneration={sessionGeneration}
          series={series}
          selectedSeriesId={selectedSeriesId}
          onSeriesChange={setSelectedSeriesId}
          chapters={chapters}
          selectedChapterId={selectedChapterId}
          onChapterChange={setSelectedChapterId}
          selectedSeries={selectedSeries}
          selectedChapter={selectedChapter}
          storyboards={storyboardAssets}
          characters={characterAssets}
          scenes={sceneAssets}
          props={propAssets}
          models={videoModels}
          prompts={prompts}
          quickReferenceSettings={quickReferenceSettings}
          loading={loading}
          chapterLoading={chapterLoading}
          directoryLoading={directoryLoading}
          chapterError={chapterError}
          directoryError={directoryError}
          onUnauthorized={onUnauthorized}
          onOpenTasks={onOpenTasks}
          onActivityChange={setBatchVideoActive}
        />
      )}

      {pending !== null && (
        <div className="media-generation-alert" role="status">
          <p>提交状态尚未确认。页面保留了本次请求，请先核对状态再继续。</p>
          {pending.singleGroup !== undefined && pending.singleItemIndex !== undefined
            && pending.singleItemIndex + 1 < pending.singleGroup.items.length && (
            <p>核对若确认本项已受理，后续未提交项目会按原请求继续；已受理项目不会重复提交。</p>
          )}
          <button className="secondary-button" disabled={pending.recoveryUnavailable || !pending.run.isCurrent() || pendingAction} onClick={() => void lookupPending(pending)} type="button">
            {pendingAction ? "正在核对…" : "重新核对提交"}
          </button>
          {pending.singleGroup !== undefined && (
            <button className="secondary-button" disabled={pending.recoveryUnavailable || !pending.run.isCurrent() || pendingAction || requesting}
              onClick={() => void retryPendingSingleGenerationItem(pending)} type="button">
              按原请求重试本项（保留原操作和幂等键）
            </button>
          )}
          {(pending.recoveryUnavailable || !pending.run.isCurrent()) && (
            <>
              <p>当前没有可用的原请求核对入口；停止本地跟踪不会撤销或重新发送服务端请求。</p>
              <button className="secondary-button" disabled={pendingAction || requesting}
                onClick={() => stopLocalSubmissionTracking(pending)} type="button">
                停止本地跟踪
              </button>
            </>
          )}
        </div>
      )}
      {showSingleGenerationGroup && displayedSingleGenerationGroup !== null && (
        <div className="media-generation-result" role="status" aria-label={`${displayedSingleGenerationGroup.mode === "image" ? "图片" : "视频"}任务结果`}>
          {(requesting || pending?.singleGroup?.signature === displayedSingleGenerationGroup.signature)
            && <p>本组继续使用提交时冻结的请求；已受理项目和待确认项目会保留在这里。</p>}
          {displayedSingleGenerationGroup.message !== null && <p>{displayedSingleGenerationGroup.message}</p>}
          <ol>
            {displayedSingleGenerationGroup.items.map((item) => (
              <li key={item.index}>
                <strong>第 {item.index + 1} 项 · {singleGenerationStatusLabel(item.status)}</strong>
                {item.receipt !== undefined && (
                  <>
                    <p>
                      {item.receipt.operation === "image.single" ? "图片" : "视频"}任务 {item.receipt.taskId}
                      （提交回执状态：{item.receipt.status}；消息编号：{item.receipt.messageId ?? "—"}）
                    </p>
                    <p>
                      {item.taskStatus === undefined
                        ? "任务状态尚未核对。"
                        : `任务状态：${item.taskStatus.status}${item.taskStatus.progress === null ? "" : ` · ${item.taskStatus.progress}%`}`}
                      {item.taskStatus?.progressMessage ? ` · ${item.taskStatus.progressMessage}` : ""}
                    </p>
                    {item.taskStatusMessage && <p role="status">{item.taskStatusMessage}</p>}
                    {services.getTaskReceipt === undefined
                      ? <p>当前服务未接入任务状态读取。</p>
                      : <button className="secondary-button" disabled={requesting || item.taskStatusReading
                        || pending?.singleGroup?.operationOwnerId === displayedSingleGenerationGroup.operationOwnerId}
                        onClick={() => void refreshSingleGenerationTaskStatus(displayedSingleGenerationGroup, item)} type="button">
                        {item.taskStatusReading ? "正在读取状态…" : `刷新第 ${item.index + 1} 项任务状态`}
                      </button>}
                  </>
                )}
                {item.message !== undefined && <p>{item.message}</p>}
              </li>
            ))}
          </ol>
          {displayedSingleGenerationGroup.items.some((item) => item.receipt !== undefined)
            && displayedOutcome?.receipt === undefined && (
            <button className="secondary-button" onClick={onOpenTasks} type="button">查看我的任务</button>
          )}
        </div>
      )}
      {displayedOutcome !== null && (
        <div className={displayedOutcome.pendingLookup ? "media-generation-alert" : "media-generation-result"} role={displayedOutcome.pendingLookup ? "status" : "status"}>
          <p>{displayedOutcome.message}</p>
          {displayedOutcome.receipt !== undefined && (
            <>
              {displayedOutcome.receipt.operation === "image.single" || displayedOutcome.receipt.operation === "video.single" ? (
                <dl><div><dt>任务编号</dt><dd>{displayedOutcome.receipt.taskId}</dd></div><div><dt>消息编号</dt><dd>{displayedOutcome.receipt.messageId ?? "—"}</dd></div></dl>
              ) : (
                <ol>{displayedOutcome.receipt.tasks.map((task) => <li key={`${task.type}:${task.assetId}`}>{task.assetName} · {task.taskId}</li>)}</ol>
              )}
              <button className="secondary-button" onClick={onOpenTasks} type="button">查看我的任务</button>
            </>
          )}
        </div>
      )}
    </section>
  );
}
