import { useEffect, useMemo, useRef, useState } from "react";
import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import {
  MediaGenerationError,
  type MediaGenerationImageInput,
  type MediaGenerationAssetType,
  type MediaGenerationBatchAssetInput,
  type MediaGenerationLookupExpectation,
  type MediaGenerationOperation,
  type MediaGenerationReceipt,
  type MediaGenerationRun,
  type MediaGenerationSingleTarget,
} from "../../shared/api/mediaGeneration";
import {
  type UserPreferences,
  type UserPreferencesPort,
} from "../../shared/api/userPreferences";
import {
  MediaReferenceUploadError,
  type MediaReferenceKind,
} from "../../shared/api/mediaReferenceUpload";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import "./mediaGeneration.css";

type MediaMode = "image" | "video" | "batch";
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
): Record<string, unknown> {
  const savedBatch = record(preferences.batch_gen_prefs) ?? {};
  const nextBatch: Record<string, unknown> = { ...savedBatch };
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
): boolean {
  const savedBatch = record(preferences.batch_gen_prefs);
  if (savedBatch === null) return false;
  return BATCH_ASSET_TYPES.every((type) => {
    const savedType = record(savedBatch[type]);
    return [...edits[type]].every((field) => (
      savedType?.[BATCH_PREFERENCE_KEYS[field]] === selections[type][field]
    ));
  });
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
}

interface SubmissionOutcome {
  readonly signature: string;
  readonly receipt?: MediaGenerationReceipt;
  readonly message?: string;
  readonly pendingLookup?: boolean;
}

interface MediaGenerationPageProps {
  readonly services: WorkspaceServices;
  readonly userId: string;
  readonly sessionGeneration: number;
  onUnauthorized(): void;
  onOpenTasks(): void;
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
    || (error instanceof ApiError && error.status === 401);
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
}: MediaGenerationPageProps) {
  const [mode, setMode] = useState<MediaMode>("video");
  const [series, setSeries] = useState<Series[]>([]);
  const [selectedSeriesId, setSelectedSeriesId] = useState("");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [selectedChapterId, setSelectedChapterId] = useState("");
  const [storyboardAssets, setStoryboardAssets] = useState<StoryboardAsset[]>([]);
  const [assetChoices, setAssetChoices] = useState<AssetChoice[]>([]);
  const [videoModels, setVideoModels] = useState<CreatorModel[]>([]);
  const [imageModels, setImageModels] = useState<CreatorModel[]>([]);
  const [prompts, setPrompts] = useState<CreatorPrompt[]>([]);
  const [selectedVideoModelId, setSelectedVideoModelId] = useState("");
  const [selectedImageModelId, setSelectedImageModelId] = useState("");
  const [selectedImagePromptId, setSelectedImagePromptId] = useState("");
  const [selectedVideoPromptId, setSelectedVideoPromptId] = useState("");
  const batchSelectionsRef = useRef<BatchGenerationSelections>(emptyBatchSelections());
  const [batchSelections, setBatchSelections] = useState<BatchGenerationSelections>(batchSelectionsRef.current);
  const batchPreferenceEditsRef = useRef<BatchPreferenceEdits>(emptyBatchEdits());
  const [batchPreferenceWarnings, setBatchPreferenceWarnings] = useState<BatchPreferenceWarnings>(emptyBatchWarnings());
  const [batchPreferenceMessage, setBatchPreferenceMessage] = useState<string | null>(null);
  const [batchPreferenceLoading, setBatchPreferenceLoading] = useState(false);
  const [savingBatchPreferences, setSavingBatchPreferences] = useState(false);
  const savingBatchPreferencesRef = useRef(false);
  const batchPreferenceControllerRef = useRef<AbortController | null>(null);
  const batchPreferenceRestoreGenerationRef = useRef(0);
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
  const [pending, setPending] = useState<PendingSubmission | null>(null);
  const pendingRef = useRef<PendingSubmission | null>(null);
  const [requesting, setRequesting] = useState(false);
  const requestingRef = useRef(false);
  const [outcome, setOutcome] = useState<SubmissionOutcome | null>(null);
  const mountedRef = useRef(false);
  const identityRef = useRef({ userId, services, sessionGeneration, signature: "" });
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  const selectedVideoModel = videoModels.find((model) => model.id === selectedVideoModelId) ?? null;
  const selectedImageModel = imageModels.find((model) => model.id === selectedImageModelId) ?? null;
  const selectedSeries = series.find((item) => item.id === selectedSeriesId) ?? null;
  const videoOptions = useMemo(() => modelOptions(selectedVideoModel), [selectedVideoModel]);
  const imageOptions = useMemo(() => modelOptions(selectedImageModel), [selectedImageModel]);
  const sortedChapters = useMemo(() => [...chapters].sort((left, right) => left.order - right.order), [chapters]);
  const singleAssetChoices = assetChoices.filter((asset) => asset.type === singleAssetType);
  const selectedBatchAssets = selectedBatchKeys.flatMap((key) => {
    const asset = assetChoices.find((choice) => `${choice.type}:${choice.id}` === key);
    return asset === undefined ? [] : [asset];
  });
  const formSignature = JSON.stringify({
    mode,
    selectedSeriesId,
    selectedChapterId,
    selectedVideoModelId,
    selectedImageModelId,
    selectedImagePromptId,
    selectedVideoPromptId,
    batchSelections,
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
    batchReferenceImages,
  });
  identityRef.current = { userId, services, sessionGeneration, signature: formSignature };
  const displayedOutcome = outcome?.signature === formSignature ? outcome : null;

  function updatePending(value: PendingSubmission | null) {
    pendingRef.current = value;
    setPending(value);
  }

  function updateRequesting(value: boolean) {
    requestingRef.current = value;
    setRequesting(value);
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
    };
  }, []);

  useEffect(() => {
    batchPreferenceControllerRef.current?.abort();
    batchPreferenceControllerRef.current = null;
    savingBatchPreferencesRef.current = false;
    batchSelectionsRef.current = emptyBatchSelections();
    setBatchSelections(batchSelectionsRef.current);
    batchPreferenceEditsRef.current = emptyBatchEdits();
    setBatchPreferenceWarnings(emptyBatchWarnings());
    setBatchPreferenceMessage(null);
    setBatchPreferenceLoading(false);
    setSavingBatchPreferences(false);
  }, [services, sessionGeneration, userId]);

  useEffect(() => {
    setReferenceFiles({});
    setReferenceUploadStatus(null);
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
      if (requestingRef.current || pendingRef.current !== null || savingBatchPreferencesRef.current) {
        setBatchPreferenceMessage("账户偏好读取晚于本次操作，已保留当前选项；提交内容不会被更改。");
        return;
      }

      const savedBatch = record(preferences.batch_gen_prefs);
      if (savedBatch === null) {
        setBatchPreferenceMessage("账户中还没有保存批量生成选项。修改后可点击“记住批量选项”。");
        return;
      }
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
    if (selectedVideoModel === null) {
      setVideoSize("");
      setVideoDuration("");
      setVideoResolution("");
      return;
    }
    setVideoSize(videoOptions.defaultSize);
    setVideoDuration(videoOptions.defaultDuration === null ? "" : String(videoOptions.defaultDuration));
    setVideoResolution(videoOptions.defaultResolution);
  }, [selectedVideoModelId, selectedVideoModel, videoOptions.defaultDuration, videoOptions.defaultResolution, videoOptions.defaultSize]);

  useEffect(() => {
    if (selectedImageModel === null) {
      setImageSize("");
      return;
    }
    setImageSize(imageOptions.defaultSize);
  }, [selectedImageModelId, selectedImageModel, imageOptions.defaultSize]);

  useEffect(() => {
    if (selectedSeriesId === "") {
      setChapters([]);
      setSelectedChapterId("");
      return;
    }
    const controller = new AbortController();
    let current = true;
    setChapterLoading(true);
    setChapterError(null);
    setSelectedChapterId("");
    setStoryboardAssets([]);
    setAssetChoices([]);
    setSelectedBatchKeys([]);
    void services.listChapters(selectedSeriesId, controller.signal).then((result) => {
      if (current && !controller.signal.aborted) setChapters(result);
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
  }, [selectedSeriesId, services]);

  useEffect(() => {
    if (selectedSeriesId === "" || selectedChapterId === "") {
      setStoryboardAssets([]);
      setAssetChoices([]);
      setDirectoryError(null);
      return;
    }
    const controller = new AbortController();
    let current = true;
    setDirectoryLoading(true);
    setDirectoryError(null);
    setSelectedBatchKeys([]);
    void Promise.all([
      services.listStoryboardAssets(selectedSeriesId, selectedChapterId, controller.signal),
      services.listCharacters(selectedSeriesId, controller.signal),
      services.listScenes(selectedSeriesId, controller.signal),
      services.listProps(selectedSeriesId, controller.signal),
    ]).then(([frames, characters, scenes, props]) => {
      if (!current || controller.signal.aborted) return;
      setStoryboardAssets(frames.filter((item) => item.chapter_id === selectedChapterId));
      setAssetChoices([
        ...libraryChoices("storyboard", frames.filter((item) => item.chapter_id === selectedChapterId)),
        ...libraryChoices("character", characters),
        ...libraryChoices("scene", scenes),
        ...libraryChoices("prop", props),
      ]);
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (isUnauthorized(error)) onUnauthorizedRef.current();
      else setDirectoryError(describeFailure(error));
      setStoryboardAssets([]);
      setAssetChoices([]);
    }).finally(() => {
      if (current && !controller.signal.aborted) setDirectoryLoading(false);
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [selectedChapterId, selectedSeriesId, services]);

  useEffect(() => {
    if (selectedImagePromptId === "") return;
    const prompt = prompts.find((candidate) => candidate.id === selectedImagePromptId);
    if (prompt !== undefined) setImagePrompt(promptText(prompt));
  }, [prompts, selectedImagePromptId]);

  useEffect(() => {
    if (selectedVideoPromptId === "") return;
    const prompt = prompts.find((candidate) => candidate.id === selectedVideoPromptId);
    if (prompt !== undefined) setVideoPrompt(promptText(prompt));
  }, [prompts, selectedVideoPromptId]);

  useEffect(() => {
    if (singleAssetId !== "" && !singleAssetChoices.some((asset) => asset.id === singleAssetId)) {
      setSingleAssetId("");
    }
    if (singleStoryboardAssetId !== "" && !storyboardAssets.some((asset) => asset.id === singleStoryboardAssetId)) {
      setSingleStoryboardAssetId("");
    }
  }, [singleAssetChoices, singleAssetId, singleStoryboardAssetId, storyboardAssets]);

  const setModeSafely = (value: MediaMode) => {
    if (pendingRef.current !== null || requestingRef.current || uploadInProgressRef.current) return;
    setMode(value);
    setOutcome(null);
    setReferenceUploadStatus(null);
  };

  const applyReceipt = (signature: string, receipt: MediaGenerationReceipt) => {
    if (signature !== identityRef.current.signature) return;
    setOutcome({ signature, receipt, message: receiptMessage(receipt) });
    updatePending(null);
  };

  const lookupPending = async (submission: PendingSubmission) => {
    if (submission.run.isCurrent() !== true) return;
    const controller = new AbortController();
    try {
      const receipt = await submission.run.lookup(submission.expectation, controller.signal);
      if (submission.run.isCurrent()) applyReceipt(submission.signature, receipt);
    } catch (error) {
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      if (!submission.run.isCurrent()) return;
      setOutcome({
        signature: submission.signature,
        message: `${submission.submissionMessage} 尚无法核对本次提交。${describeFailure(error)} 请保留本页，稍后再次核对；不会自动重新提交。`,
        pendingLookup: true,
      });
    }
  };

  const makeRun = () => {
    const capturedSignature = formSignature;
    const capturedUserId = userId;
    const capturedServices = services;
    const capturedSessionGeneration = sessionGeneration;
    const port = services.mediaGeneration;
    if (port === undefined) throw new MediaGenerationError("rejected", "媒体生成服务尚未接入。", 503);
    return port.beginRun({
      actorId: userId,
      isContextCurrent: () => mountedRef.current
        && identityRef.current.userId === capturedUserId
        && identityRef.current.services === capturedServices
        && identityRef.current.sessionGeneration === capturedSessionGeneration
        && identityRef.current.signature === capturedSignature,
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
    if (kind === "image" && mode === "image") setImageReferenceImages(append);
    else if (kind === "image" && mode === "video") setVideoReferenceImages(append);
    else if (kind === "image") setBatchReferenceImages(append);
    else if (kind === "audio") setVideoReferenceAudios(append);
    else setVideoReferenceVideos(append);
  };

  const referenceCount = (kind: MediaReferenceKind) => {
    const value = kind === "image"
      ? mode === "image" ? imageReferenceImages : mode === "video" ? videoReferenceImages : batchReferenceImages
      : kind === "audio" ? videoReferenceAudios : videoReferenceVideos;
    return splitReferences(value).length;
  };

  const uploadReference = async (kind: MediaReferenceKind) => {
    if (uploadInProgressRef.current || pendingRef.current !== null || requestingRef.current) return;
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

  const submitImage = async () => {
    if (pendingRef.current !== null || requestingRef.current || selectedImageModel === null || imagePrompt.trim() === "") return;
    const rawKey = crypto.randomUUID();
    const signature = formSignature;
    let run: MediaGenerationRun;
    try {
      run = makeRun();
    } catch (error) {
      setOutcome({ signature, message: describeFailure(error) });
      return;
    }
    const input: MediaGenerationImageInput = Object.freeze({
      modelId: selectedImageModel.id,
      prompt: imagePrompt,
      target: singleTarget(),
      ...(imageSize === "" ? {} : { size: imageSize }),
      referenceImages: Object.freeze(splitReferences(imageReferenceImages)),
      idempotencyKey: rawKey,
    });
    const controller = new AbortController();
    setOutcome(null);
    updateRequesting(true);
    try {
      const receipt = await run.submitImage(input, controller.signal);
      if (run.isCurrent()) applyReceipt(signature, receipt);
    } catch (error) {
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      if (!run.isCurrent()) return;
      if (error instanceof MediaGenerationError && error.kind === "unknown-outcome") {
        const submission: PendingSubmission = {
          operation: "image.single",
          idempotencyKey: rawKey,
          expectation: { operation: "image.single", idempotencyKey: rawKey },
          run,
          signature,
          submissionMessage: describeFailure(error),
        };
        updatePending(submission);
        setOutcome({ signature, message: `${describeFailure(error)} 提交状态待确认，正在核对本次提交；不会自动重新提交。`, pendingLookup: true });
        await lookupPending(submission);
        return;
      }
      setOutcome({ signature, message: describeFailure(error) });
    } finally {
      updateRequesting(false);
    }
  };

  const submitVideo = async () => {
    if (pendingRef.current !== null || requestingRef.current || selectedVideoModel === null || videoPrompt.trim() === "") return;
    const target = singleTarget();
    const rawKey = crypto.randomUUID();
    const signature = formSignature;
    let run: MediaGenerationRun;
    try {
      run = makeRun();
    } catch (error) {
      setOutcome({ signature, message: describeFailure(error) });
      return;
    }
    const input = Object.freeze({
      modelId: selectedVideoModel.id,
      prompt: videoPrompt,
      target,
      ...(videoSize === "" ? {} : { size: videoSize }),
      ...(videoDuration === "" ? {} : { duration: Number(videoDuration) }),
      ...(videoResolution === "" ? {} : { resolution: videoResolution }),
      referenceImages: Object.freeze(splitReferences(videoReferenceImages)),
      referenceAudios: Object.freeze(splitReferences(videoReferenceAudios)),
      referenceVideos: Object.freeze(splitReferences(videoReferenceVideos)),
      idempotencyKey: rawKey,
    });
    const controller = new AbortController();
    setOutcome(null);
    updateRequesting(true);
    try {
      const receipt = await run.submitVideo(input, controller.signal);
      if (run.isCurrent()) applyReceipt(signature, receipt);
    } catch (error) {
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      if (!run.isCurrent()) return;
      if (error instanceof MediaGenerationError && error.kind === "unknown-outcome") {
        const submission: PendingSubmission = {
          operation: "video.single",
          idempotencyKey: rawKey,
          expectation: { operation: "video.single", idempotencyKey: rawKey },
          run,
          signature,
          submissionMessage: describeFailure(error),
        };
        updatePending(submission);
        setOutcome({ signature, message: `${describeFailure(error)} 提交状态待确认，正在核对本次提交；不会自动重新提交。`, pendingLookup: true });
        await lookupPending(submission);
        return;
      }
      setOutcome({ signature, message: describeFailure(error) });
    } finally {
      updateRequesting(false);
    }
  };

  const submitBatch = async () => {
    if (pendingRef.current !== null || requestingRef.current || savingBatchPreferencesRef.current
      || selectedSeries === null || selectedChapterId === ""
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
    const references = Object.freeze(splitReferences(batchReferenceImages));
    const frozenSelections = Object.freeze(Object.fromEntries(BATCH_ASSET_TYPES.map((type) => (
      [type, Object.freeze({ ...batchSelectionsRef.current[type] })]
    ))) as BatchGenerationSelections);
    const assets: readonly MediaGenerationBatchAssetInput[] = Object.freeze(selectedBatchAssets.map((asset) => Object.freeze({
      type: asset.type,
      id: asset.id,
      name: asset.name,
      description: asset.description,
      modelId: frozenSelections[asset.type].modelId,
      promptId: frozenSelections[asset.type].promptId,
      ...(frozenSelections[asset.type].size === "" ? {} : { size: frozenSelections[asset.type].size }),
      referenceImages: references,
    })));
    const input = Object.freeze({ chapterId: selectedChapterId, assets, idempotencyKey: rawKey });
    const expectation: MediaGenerationLookupExpectation = {
      operation: "image.batch",
      idempotencyKey: rawKey,
      batchAssets: Object.freeze(assets.map((asset) => Object.freeze({ type: asset.type, id: asset.id }))),
    };
    const controller = new AbortController();
    setOutcome(null);
    updateRequesting(true);
    try {
      const receipt = await run.submitBatch(input, controller.signal);
      if (run.isCurrent()) applyReceipt(signature, receipt);
    } catch (error) {
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      if (!run.isCurrent()) return;
      if (error instanceof MediaGenerationError && error.kind === "unknown-outcome") {
        const submission: PendingSubmission = {
          operation: "image.batch",
          idempotencyKey: rawKey,
          expectation,
          run,
          signature,
          submissionMessage: describeFailure(error),
        };
        updatePending(submission);
        setOutcome({ signature, message: `${describeFailure(error)} 提交状态待确认，正在核对本次提交；不会自动重新提交。`, pendingLookup: true });
        await lookupPending(submission);
        return;
      }
      setOutcome({ signature, message: describeFailure(error) });
    } finally {
      updateRequesting(false);
    }
  };

  const saveBatchPreferences = async () => {
    const port: UserPreferencesPort | undefined = services.userPreferences;
    if (port === undefined || savingBatchPreferencesRef.current || requestingRef.current || pendingRef.current !== null) return;
    const edits = emptyBatchEdits();
    for (const type of BATCH_ASSET_TYPES) {
      edits[type] = new Set(batchPreferenceEditsRef.current[type]);
    }
    if (!BATCH_ASSET_TYPES.some((type) => edits[type].size > 0)) {
      setBatchPreferenceMessage("没有需要保存的批量选项修改。");
      return;
    }
    batchPreferenceRestoreGenerationRef.current += 1;
    const selections = Object.freeze(Object.fromEntries(BATCH_ASSET_TYPES.map((type) => (
      [type, Object.freeze({ ...batchSelectionsRef.current[type] })]
    ))) as BatchGenerationSelections);
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

      const update = mergeBatchPreferenceEdits(currentPreferences, selections, edits);
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
          if (batchPreferenceEditsMatch(latestPreferences, selections, edits)) {
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

  const selectImagePrompt = (id: string) => {
    setSelectedImagePromptId(id);
    if (id === "") setImagePrompt("");
  };

  const selectVideoPrompt = (id: string) => {
    setSelectedVideoPromptId(id);
    if (id === "") setVideoPrompt("");
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
  const batchSubmissionBlocked = [...selectedBatchTypes].some((type) => batchSelectionError(
    type,
    batchSelections[type],
    imageModels,
    prompts,
  ) !== null || batchPreferenceWarningBlocksSubmission(type, batchSelections[type], batchPreferenceWarnings, imageModels));
  const hasBatchPreferenceEdits = BATCH_ASSET_TYPES.some((type) => batchPreferenceEditsRef.current[type].size > 0);
  const currentImagePromptOptions = prompts.filter((prompt) => IMAGE_PROMPT_CATEGORIES.has(prompt.category));
  const currentVideoPromptOptions = prompts.filter((prompt) => prompt.category === "video-design");
  const isImageMode = mode === "image";
  const singleModel = isImageMode ? selectedImageModel : selectedVideoModel;
  const singleOptions = isImageMode ? imageOptions : videoOptions;
  const singlePrompt = isImageMode ? imagePrompt : videoPrompt;
  const singlePromptId = isImageMode ? selectedImagePromptId : selectedVideoPromptId;
  const singlePromptOptions = isImageMode ? currentImagePromptOptions : currentVideoPromptOptions;
  const singleReferences = isImageMode ? imageReferenceImages : videoReferenceImages;
  const singleReferenceSetter = isImageMode ? setImageReferenceImages : setVideoReferenceImages;
  const isFrozen = pending !== null || requesting || uploadingReference || savingBatchPreferences;

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
      </nav>

      {catalogError !== null && <p className="media-generation-alert" role="alert">配置目录读取失败：{catalogError}</p>}
      {loading && <p role="status">正在读取剧集、模型和提示词目录…</p>}
      {!loading && services.mediaGeneration === undefined && (
        <p className="media-generation-note" role="status">当前服务没有接入媒体生成提交接口，页面不会模拟生成结果。</p>
      )}

      {mode !== "batch" ? (
        <div className="media-generation-card">
          <div className="media-generation-section-heading">
            <div><p className="eyebrow">{isImageMode ? "图片生成" : "视频生成"}</p><h2>{isImageMode ? "普通图片" : "普通视频"}</h2></div>
            <span>提交前会固定模型、目标与参考素材。</span>
          </div>
          <div className="media-generation-fields">
            <label>
              <span>{isImageMode ? "图片模型" : "视频模型"}</span>
              <select disabled={loading || isFrozen} onChange={(event) => isImageMode
                ? setSelectedImageModelId(event.target.value)
                : setSelectedVideoModelId(event.target.value)} value={isImageMode ? selectedImageModelId : selectedVideoModelId}>
                <option value="">{isImageMode ? "选择图片模型" : "选择视频模型"}</option>
                {(isImageMode ? imageModels : videoModels).map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
              </select>
            </label>
            <label>
              <span>提示词目录</span>
              <select disabled={isFrozen} onChange={(event) => isImageMode
                ? selectImagePrompt(event.target.value)
                : selectVideoPrompt(event.target.value)} value={singlePromptId}>
                <option value="">手动输入</option>
                {singlePromptOptions.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}</option>)}
              </select>
            </label>
            <label className="media-generation-wide">
              <span>{isImageMode ? "图片提示词" : "视频提示词"}</span>
              <textarea disabled={isFrozen} onChange={(event) => isImageMode
                ? setImagePrompt(event.target.value)
                : setVideoPrompt(event.target.value)} rows={5} value={singlePrompt} />
            </label>

            {singleOptions.sizes.length > 0 && (
              <label><span>{isImageMode ? "图片尺寸" : "画面比例"}</span><select disabled={isFrozen} onChange={(event) => isImageMode
                ? setImageSize(event.target.value)
                : setVideoSize(event.target.value)} value={isImageMode ? imageSize : videoSize}>
                {singleOptions.sizes.map((size) => <option key={size} value={size}>{size}</option>)}
              </select></label>
            )}
            {!isImageMode && videoOptions.durations.length > 0 && (
              <label><span>时长（秒）</span><select disabled={isFrozen} onChange={(event) => setVideoDuration(event.target.value)} value={videoDuration}>
                {videoOptions.durations.map((duration) => <option key={duration} value={String(duration)}>{duration}</option>)}
              </select></label>
            )}
            {!isImageMode && videoOptions.resolutions.length > 0 && (
              <label><span>分辨率</span><select disabled={isFrozen} onChange={(event) => setVideoResolution(event.target.value)} value={videoResolution}>
                {videoOptions.resolutions.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}
              </select></label>
            )}

            <label>
              <span>{isImageMode ? "图片目标" : "视频目标"}</span>
              <select disabled={isFrozen} onChange={(event) => setSingleTargetMode(event.target.value as SingleTargetMode)} value={singleTargetMode}>
                <option value="standalone">{isImageMode ? "独立图片" : "独立视频"}</option>
                <option value="frame_message">章节分镜消息</option>
                <option value="asset_message">章节素材消息</option>
              </select>
            </label>
            {singleTargetMode !== "standalone" && (
              <>
                <label><span>剧集</span><select disabled={isFrozen} onChange={(event) => setSelectedSeriesId(event.target.value)} value={selectedSeriesId}>
                  <option value="">选择剧集</option>
                  {series.filter((item) => item.can_enter).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select></label>
                <label><span>章节</span><select disabled={isFrozen || chapterLoading || selectedSeriesId === ""} onChange={(event) => {
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
              <label className="media-generation-wide"><span>分镜目标</span><select disabled={isFrozen || directoryLoading || selectedChapterId === ""} onChange={(event) => setSingleStoryboardAssetId(event.target.value)} value={singleStoryboardAssetId}>
                <option value="">选择章节分镜</option>
                {storyboardAssets.map((asset) => <option key={asset.id} value={asset.id}>第 {asset.frame_index + 1} 镜 · {asset.name}</option>)}
              </select></label>
            )}
            {singleTargetMode === "asset_message" && (
              <>
                <label><span>素材类型</span><select disabled={isFrozen} onChange={(event) => setSingleAssetType(event.target.value as MediaGenerationAssetType)} value={singleAssetType}>
                  <option value="character">角色</option><option value="scene">场景</option><option value="prop">道具</option>
                  <option value="storyboard">分镜</option>
                </select></label>
                <label><span>目标素材</span><select disabled={isFrozen || directoryLoading || selectedChapterId === ""} onChange={(event) => setSingleAssetId(event.target.value)} value={singleAssetId}>
                  <option value="">选择素材</option>
                  {singleAssetChoices.map((asset) => <option key={asset.id} value={asset.id}>{asset.label}</option>)}
                </select></label>
                {selectedAssetForSingle !== undefined && <p className="media-generation-wide media-generation-target-detail">{selectedAssetForSingle.description || "该素材没有描述。"}</p>}
              </>
            )}
            <label><span>参考图片 URL（每行一项）</span><textarea disabled={isFrozen} onChange={(event) => {
              singleReferenceSetter(event.target.value);
              setReferenceUploadStatus((current) => current?.kind === "image" ? null : current);
            }} rows={3} value={singleReferences} /></label>
            {renderReferenceUploader("image")}
            {!isImageMode && <>
              <label><span>参考音频 URL（每行一项）</span><textarea disabled={isFrozen} onChange={(event) => {
                setVideoReferenceAudios(event.target.value);
                setReferenceUploadStatus((current) => current?.kind === "audio" ? null : current);
              }} rows={3} value={videoReferenceAudios} /></label>
              {renderReferenceUploader("audio")}
              <label className="media-generation-wide"><span>参考视频 URL（每行一项）</span><textarea disabled={isFrozen} onChange={(event) => {
                setVideoReferenceVideos(event.target.value);
                setReferenceUploadStatus((current) => current?.kind === "video" ? null : current);
              }} rows={3} value={videoReferenceVideos} /></label>
              {renderReferenceUploader("video")}
            </>}
          </div>
          {chapterError !== null && <p className="media-generation-alert" role="alert">章节读取失败：{chapterError}</p>}
          {directoryError !== null && <p className="media-generation-alert" role="alert">章节素材读取失败：{directoryError}</p>}
          {singleTargetMode !== "standalone" && directoryLoading && <p role="status">正在读取章节分镜和素材目录…</p>}
          <div className="media-generation-actions">
            <button className="primary-button" disabled={loading || isFrozen || singleModel === null || singlePrompt.trim() === ""
              || (singleTargetMode === "frame_message" && (selectedChapterId === "" || singleStoryboardAssetId === ""))
              || (singleTargetMode === "asset_message" && (selectedChapterId === "" || singleAssetId === ""))}
              onClick={() => void (isImageMode ? submitImage() : submitVideo())} type="button">
              {pending?.operation === (isImageMode ? "image.single" : "video.single")
                ? "等待回执…" : isImageMode ? "提交图片任务" : "提交视频任务"}
            </button>
          </div>
        </div>
      ) : (
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
      )}

      {pending !== null && (
        <div className="media-generation-alert" role="status">
          <p>提交状态尚未确认。页面保留了本次请求，请先核对状态再继续。</p>
          <button className="secondary-button" disabled={!pending.run.isCurrent()} onClick={() => void lookupPending(pending)} type="button">重新核对提交</button>
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
