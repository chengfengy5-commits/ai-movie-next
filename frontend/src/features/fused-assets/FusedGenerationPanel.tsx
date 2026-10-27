import { useCallback, useEffect, useRef, useState } from "react";
import type { Character, Prop } from "../../shared/api/contracts";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import { ApiError } from "../../shared/api/errors";
import type {
  FusedGenerationInput,
  FusedGenerationMemberType,
  FusedSubmissionReceipt,
} from "../../shared/api/fusedGeneration";
import { MediaReferenceUploadError } from "../../shared/api/mediaReferenceUpload";
import type { WorkspaceServices } from "../../shared/api/services";
import type { FusedGenerationOpenIntent, FusedGenerationTargetProof } from "./fusedGenerationEntryIntent";
import {
  fusedGenerationPreferenceKey,
  fusedImageSizeOptions,
  readFusedGenerationPreferenceResult,
  resolveFusedPrompt,
  safeFusedImageUrl,
  writeFusedGenerationPreference,
} from "./fusedGenerationPreferences";
import {
  newFusedSubmissionKey,
  readFusedGenerationSubmissionRegistry,
  writeFusedGenerationSubmissionRecord,
  type FusedGenerationSubmissionRecord,
} from "./fusedGenerationSubmissions";

type FusedMemberAsset = Character | Prop;
type FeedbackKind = "success" | "failure" | "unknown" | "info";

interface Feedback {
  readonly kind: FeedbackKind;
  readonly message: string;
}

interface PanelScope {
  readonly services: WorkspaceServices;
  readonly apiBaseUrl: string | null;
  readonly seriesId: string;
  readonly userId: string;
  readonly sessionGeneration: number;
}

interface CustomImage {
  readonly url: string;
  readonly name: string;
}

interface ComposerDraft {
  readonly selectedAssetIds: string[];
  readonly customImages: CustomImage[];
  readonly imageUrlDraft: string;
  readonly modelId: string;
  readonly promptId: string;
  readonly size: string;
  readonly target: FusedGenerationTargetProof | null;
}

function createComposerDraft(): ComposerDraft {
  return {
    selectedAssetIds: [],
    customImages: [],
    imageUrlDraft: "",
    modelId: "",
    promptId: "",
    size: "1024x1024",
    target: null,
  };
}

interface ActiveOperation {
  readonly scope: PanelScope;
  readonly controller: AbortController;
  readonly kind: "upload" | "generate";
  phase: "uploading" | "submitting" | "tracking" | "unknown";
  memberType?: FusedGenerationMemberType;
  submissionKey?: string;
  taskId?: string;
  messageId?: string | null;
}

interface Props {
  readonly services: WorkspaceServices;
  readonly seriesId: string;
  readonly userId: string;
  readonly sessionGeneration: number;
  readonly blocked: boolean;
  readonly openIntent?: FusedGenerationOpenIntent | null;
  onUnauthorized(): void;
  onActivityChange(active: boolean): void;
  onRefreshAssets(): Promise<void>;
  onIntentConsumed?(intentId: string): void;
}

type SubmissionEntryPhase = "reserved" | "posting" | "unknown" | "accepted" | "completed" | "failed" | "conflict";
const MAX_CONCURRENT_SUBMISSION_READS = 3;

interface SubmissionEntryView {
  readonly record: FusedGenerationSubmissionRecord;
  readonly identityConflict: boolean;
  readonly phase: SubmissionEntryPhase;
  readonly message: string;
  readonly observationState: "idle" | "active" | "stopped" | "read_error";
  readonly receipt: FusedSubmissionReceipt | null;
  readonly taskStatus: string | null;
}

interface SubmissionReadOwner {
  readonly scope: PanelScope;
  readonly controller: AbortController;
  readonly epoch: number;
}

function sameScope(left: PanelScope, right: PanelScope): boolean {
  return left.services === right.services && left.apiBaseUrl === right.apiBaseUrl
    && left.seriesId === right.seriesId && left.userId === right.userId
    && left.sessionGeneration === right.sessionGeneration;
}

function sameSubmissionIdentity(
  left: FusedGenerationSubmissionRecord,
  right: FusedGenerationSubmissionRecord,
): boolean {
  return left.version === right.version && left.userId === right.userId
    && left.apiBaseUrl === right.apiBaseUrl && left.seriesId === right.seriesId
    && left.memberType === right.memberType && left.operation === right.operation
    && left.idempotencyKey === right.idempotencyKey && left.taskId === right.taskId
    && left.target.kind === right.target.kind && left.target.chapterId === right.target.chapterId
    && left.target.storyboardAssetId === right.target.storyboardAssetId
    && left.target.frameIndex === right.target.frameIndex;
}

const submissionIdentityConflictMessage = "同一原任务标识对应的本地记录不一致；原记录仍保留，可只读核对，不会重新提交或覆盖。";

function assetName(asset: FusedMemberAsset): string {
  return asset.name.trim() || "未命名素材";
}

function hasImage(asset: FusedMemberAsset): boolean {
  return typeof asset.image_url === "string" && asset.image_url.trim() !== "";
}

function defaultPromptId(prompts: readonly CreatorPrompt[]): string {
  return prompts[0]?.id ?? "";
}

function isTerminalTaskStatus(status: string): boolean {
  return ["completed", "succeeded", "success", "failed", "error"].includes(status.toLowerCase());
}

function readableStatus(status: string): string {
  switch (status.toLowerCase()) {
    case "completed":
    case "succeeded":
    case "success":
      return "已完成";
    case "failed":
    case "error":
      return "生成失败";
    case "queued":
    case "pending":
      return "排队中";
    case "processing":
    case "running":
      return "生成中";
    default:
      return "状态待确认";
  }
}

function safeFailureMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) {
    return "融合生成服务暂不可用。当前没有可核对的任务回执；不会自动重新提交生成。";
  }
  if (error instanceof ApiError && error.status !== null && error.status >= 400 && error.status < 500
    && error.status !== 408) {
    return "融合生成请求未被接受。请检查成员图片、模型和提示词；如需再次尝试，请明确发起新任务。";
  }
  return "融合生成结果暂时无法确认，服务器可能已受理且费用状态尚未确认。系统不会自动重新提交；你可以只读核对任务，或停止本地观察。";
}

function feeSummary(amount: number | null): string {
  return amount === null ? "" : `（记录费用 ${amount} 积分）`;
}

function preferenceFromStorage(
  scope: PanelScope,
  memberType: FusedGenerationMemberType,
): { readonly value: ReturnType<typeof readFusedGenerationPreferenceResult>["value"]; readonly available: boolean } {
  if (typeof window === "undefined") return { value: null, available: false };
  try {
    const storage = window.localStorage;
    return readFusedGenerationPreferenceResult(
      storage,
      fusedGenerationPreferenceKey(scope.userId, scope.apiBaseUrl ?? "", memberType),
    );
  } catch {
    return { value: null, available: false };
  }
}

function promptSelection(prompts: readonly CreatorPrompt[], id: string): CreatorPrompt | null {
  return prompts.find((prompt) => prompt.id === id && prompt.category === "fuse") ?? null;
}

export function FusedGenerationPanel({
  services,
  seriesId,
  userId,
  sessionGeneration,
  blocked,
  openIntent = null,
  onUnauthorized,
  onActivityChange,
  onRefreshAssets,
  onIntentConsumed,
}: Props) {
  const scopeRef = useRef<PanelScope>({ services, apiBaseUrl: services.apiBaseUrl, seriesId, userId, sessionGeneration });
  scopeRef.current = { services, apiBaseUrl: services.apiBaseUrl, seriesId, userId, sessionGeneration };
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized;
  const activityRef = useRef(onActivityChange);
  activityRef.current = onActivityChange;
  const refreshAssetsRef = useRef(onRefreshAssets);
  refreshAssetsRef.current = onRefreshAssets;
  const mountedRef = useRef(false);
  const operationRef = useRef<ActiveOperation | null>(null);
  const submissionReadOwnersRef = useRef(new Map<string, SubmissionReadOwner>());
  const submissionReadEpochsRef = useRef(new Map<string, number>());
  const ingestRegistryRef = useRef<() => void>(() => undefined);
  const catalogControllerRef = useRef<AbortController | null>(null);
  const assetControllerRef = useRef<AbortController | null>(null);
  const selectedTypeRef = useRef<FusedGenerationMemberType>("character");
  const restoredPreferenceContextsRef = useRef(new Set<string>());

  const [memberType, setMemberType] = useState<FusedGenerationMemberType>("character");
  selectedTypeRef.current = memberType;
  const [models, setModels] = useState<CreatorModel[]>([]);
  const [prompts, setPrompts] = useState<CreatorPrompt[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [assets, setAssets] = useState<FusedMemberAsset[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(true);
  const [assetsError, setAssetsError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<FusedGenerationMemberType, ComposerDraft>>({
    character: createComposerDraft(),
    prop: createComposerDraft(),
  });
  const draftInteractionRef = useRef<Record<FusedGenerationMemberType, number>>({ character: 0, prop: 0 });
  const draft = drafts[memberType];
  const selectedAssetIds = draft.selectedAssetIds;
  const customImages = draft.customImages;
  const imageUrlDraft = draft.imageUrlDraft;
  const modelId = draft.modelId;
  const promptId = draft.promptId;
  const size = draft.size;
  const setDraftField = useCallback(<K extends keyof ComposerDraft>(
    type: FusedGenerationMemberType,
    field: K,
    update: ComposerDraft[K] | ((current: ComposerDraft[K]) => ComposerDraft[K]),
  ) => {
    draftInteractionRef.current[type] += 1;
    setDrafts((current) => {
      const previous = current[type];
      const previousValue = previous[field];
      const nextValue = typeof update === "function"
        ? (update as (value: ComposerDraft[K]) => ComposerDraft[K])(previousValue)
        : update;
      if (Object.is(previousValue, nextValue)) return current;
      return { ...current, [type]: { ...previous, [field]: nextValue } };
    });
  }, []);
  const setSelectedAssetIds = useCallback((value: string[] | ((current: string[]) => string[])) => {
    setDraftField(memberType, "selectedAssetIds", value);
  }, [memberType, setDraftField]);
  const setCustomImages = useCallback((value: CustomImage[] | ((current: CustomImage[]) => CustomImage[])) => {
    setDraftField(memberType, "customImages", value);
  }, [memberType, setDraftField]);
  const setImageUrlDraft = useCallback((value: string) => setDraftField(memberType, "imageUrlDraft", value), [memberType, setDraftField]);
  const setModelId = useCallback((value: string) => setDraftField(memberType, "modelId", value), [memberType, setDraftField]);
  const setPromptId = useCallback((value: string) => setDraftField(memberType, "promptId", value), [memberType, setDraftField]);
  const setSize = useCallback((value: string) => setDraftField(memberType, "size", value), [memberType, setDraftField]);
  const [readyPreferenceContext, setReadyPreferenceContext] = useState<string | null>(null);
  const [operation, setOperation] = useState<ActiveOperation | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [submissionEntries, setSubmissionEntries] = useState<Record<string, SubmissionEntryView>>({});
  const submissionEntriesRef = useRef(submissionEntries);
  submissionEntriesRef.current = submissionEntries;
  const [selectedSubmissionKey, setSelectedSubmissionKey] = useState<string | null>(null);
  const [registryWarning, setRegistryWarning] = useState<string | null>(null);
  const [registryAvailable, setRegistryAvailable] = useState(false);

  const isCurrent = useCallback((scope: PanelScope, signal?: AbortSignal): boolean => (
    mountedRef.current && signal?.aborted !== true && sameScope(scopeRef.current, scope)
  ), []);

  function ownsOperation(candidate: ActiveOperation): boolean {
    return operationRef.current === candidate;
  }

  const updateOperation = useCallback((next: ActiveOperation | null) => {
    operationRef.current = next;
    setOperation(next);
    activityRef.current(next !== null);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    restoredPreferenceContextsRef.current.clear();
    setMemberType("character");
    setModels([]);
    setPrompts([]);
    setCatalogLoading(true);
    setCatalogError(null);
    setAssets([]);
    setAssetsLoading(true);
    setAssetsError(null);
    setDrafts({ character: createComposerDraft(), prop: createComposerDraft() });
    draftInteractionRef.current = { character: 0, prop: 0 };
    setReadyPreferenceContext(null);
    const previousOperation = operationRef.current;
    if (previousOperation !== null) {
      previousOperation.controller.abort();
      operationRef.current = null;
    }
    setOperation(null);
    setSubmissionEntries({});
    setSelectedSubmissionKey(null);
    setRegistryWarning(null);
    setRegistryAvailable(false);
    for (const owner of submissionReadOwnersRef.current.values()) owner.controller.abort();
    submissionReadOwnersRef.current.clear();
    submissionReadEpochsRef.current.clear();
    setFeedback(null);

    catalogControllerRef.current?.abort();
    assetControllerRef.current?.abort();
    const scope = scopeRef.current;
    const controller = new AbortController();
    catalogControllerRef.current = controller;
    const creatorSettings = scope.services.creatorSettings;
    if (creatorSettings === undefined) {
      setCatalogLoading(false);
      setCatalogError("当前服务未接入生图模型和融合提示词目录。");
    } else {
      void Promise.all([
        creatorSettings.listModels("image", controller.signal),
        creatorSettings.listPrompts("fuse", controller.signal),
      ]).then(([nextModels, nextPrompts]) => {
        if (!isCurrent(scope, controller.signal)) return;
        setModels(nextModels.filter((model) => model.category === "image"));
        setPrompts(nextPrompts.filter((prompt) => prompt.category === "fuse"));
        setCatalogError(null);
      }).catch((error: unknown) => {
        if (!isCurrent(scope, controller.signal)) return;
        if (error instanceof ApiError && error.status === 401) unauthorizedRef.current();
        else if (!(error instanceof ApiError && error.kind === "aborted")) {
          setCatalogError("生图模型或融合提示词目录读取失败，请稍后重试。");
        }
      }).finally(() => {
        if (isCurrent(scope, controller.signal)) setCatalogLoading(false);
      });
    }
    return () => {
      mountedRef.current = false;
      controller.abort();
      if (catalogControllerRef.current === controller) catalogControllerRef.current = null;
      assetControllerRef.current?.abort();
      assetControllerRef.current = null;
      const active = operationRef.current;
      if (active !== null) {
        active.controller.abort();
        operationRef.current = null;
        activityRef.current(false);
      }
      for (const owner of submissionReadOwnersRef.current.values()) owner.controller.abort();
      submissionReadOwnersRef.current.clear();
      submissionReadEpochsRef.current.clear();
    };
  }, [services, seriesId, userId, sessionGeneration, isCurrent, updateOperation]);

  useEffect(() => {
    const scope = scopeRef.current;
    const type = memberType;
    const controller = new AbortController();
    assetControllerRef.current?.abort();
    assetControllerRef.current = controller;
    setAssets([]);
    setAssetsLoading(true);
    setAssetsError(null);
    const request = type === "character"
      ? scope.services.listCharacters(scope.seriesId, controller.signal)
      : scope.services.listProps(scope.seriesId, controller.signal);
    void request.then((rows) => {
      if (!isCurrent(scope, controller.signal) || selectedTypeRef.current !== type) return;
      setAssets(rows.filter((asset) => asset.series_id === scope.seriesId));
      setAssetsError(null);
    }).catch((error: unknown) => {
      if (!isCurrent(scope, controller.signal) || selectedTypeRef.current !== type) return;
      if (error instanceof ApiError && error.status === 401) unauthorizedRef.current();
      else if (!(error instanceof ApiError && error.kind === "aborted")) {
        setAssetsError("当前剧集成员目录读取失败，暂时不能生成。");
      }
    }).finally(() => {
      if (isCurrent(scope, controller.signal) && selectedTypeRef.current === type) setAssetsLoading(false);
    });
    return () => {
      controller.abort();
      if (assetControllerRef.current === controller) assetControllerRef.current = null;
    };
  }, [memberType, services, seriesId, userId, sessionGeneration, isCurrent]);

  const preferenceContext = `${userId}\u0000${services.apiBaseUrl ?? ""}\u0000${memberType}`;
  useEffect(() => {
    if (catalogLoading || catalogError !== null) return;
    if (restoredPreferenceContextsRef.current.has(preferenceContext)) {
      setReadyPreferenceContext(preferenceContext);
      return;
    }
    const storageRead = preferenceFromStorage(scopeRef.current, memberType);
    const saved = storageRead.value;
    const nextModel = models.find((model) => model.id === saved?.modelId) ?? models[0] ?? null;
    const choices = fusedImageSizeOptions(nextModel);
    const nextPrompt = prompts.find((prompt) => prompt.id === saved?.promptId && prompt.category === "fuse");
    restoredPreferenceContextsRef.current.add(preferenceContext);
    setDrafts((current) => ({
      ...current,
      [memberType]: {
        ...current[memberType],
        modelId: nextModel?.id ?? "",
        size: saved !== null && choices.values.includes(saved.size) ? saved.size : choices.defaultValue,
        promptId: saved !== null && saved.promptId === "" ? "" : nextPrompt?.id ?? defaultPromptId(prompts),
      },
    }));
    setReadyPreferenceContext(preferenceContext);
    if (!storageRead.available) {
      setFeedback({ kind: "info", message: "浏览器本地偏好不可读，当前使用本页默认选项；你仍可手动选择并生成。" });
    }
  }, [catalogLoading, catalogError, models, prompts, preferenceContext, memberType]);

  useEffect(() => {
    const scope = scopeRef.current;
    if (typeof window === "undefined") {
      setRegistryAvailable(false);
      setRegistryWarning("当前环境不能枚举本地任务定位记录，暂停新融合提交。");
      return;
    }
    const syncRegistry = (): void => {
      let registry: ReturnType<typeof readFusedGenerationSubmissionRegistry>;
      try {
        const storage = window.localStorage;
        registry = readFusedGenerationSubmissionRegistry(storage, {
          userId: scope.userId,
          apiBaseUrl: scope.apiBaseUrl ?? "",
          seriesId: scope.seriesId,
        });
      } catch {
        registry = {
          status: "unavailable",
          records: [],
          invalidKeys: [],
          conflictingKeys: [],
          unresolvedLegacyKeys: [],
        };
      }
      setRegistryAvailable(registry.status === "available");
      if (registry.status === "unavailable") {
        setRegistryWarning("浏览器本地融合任务记录不可枚举或读取，暂停新融合提交。");
        return;
      }
      const warnings: string[] = [];
      if (registry.invalidKeys.length > 0) warnings.push("存在格式损坏但仍保留的本地任务记录");
      if (registry.conflictingKeys.length > 0) warnings.push("存在原任务标识相同但内容不一致的记录");
      if (registry.unresolvedLegacyKeys.length > 0) warnings.push("旧任务记录仍保留，部分未能安全迁入任务列表");
      setRegistryWarning(warnings.length === 0 ? null : `${warnings.join("；")}。记录会保留，可单条只读核验或创建独立新任务。`);
      for (const record of registry.records) {
        const key = record.idempotencyKey;
        const current = submissionEntriesRef.current[key];
        const hasRegistryConflict = registry.conflictingKeys.includes(key);
        if (current !== undefined) {
          if (hasRegistryConflict || !sameSubmissionIdentity(current.record, record)) {
            const owner = submissionReadOwnersRef.current.get(key);
            if (owner !== undefined) {
              owner.controller.abort();
              submissionReadOwnersRef.current.delete(key);
              submissionReadEpochsRef.current.set(key, owner.epoch + 1);
            }
            setSubmissionEntries((entries) => {
              const preserved = entries[key] ?? current;
              return {
                ...entries,
                [key]: {
                  ...preserved,
                  identityConflict: true,
                  phase: "conflict",
                  observationState: "idle",
                  message: submissionIdentityConflictMessage,
                },
              };
            });
          }
          continue;
        }
        setSubmissionEntries((entries) => entries[record.idempotencyKey] === undefined
          ? {
            ...entries,
            [record.idempotencyKey]: {
              record,
              identityConflict: hasRegistryConflict,
              phase: hasRegistryConflict ? "conflict" : "unknown",
              message: hasRegistryConflict
                ? submissionIdentityConflictMessage
                : "发现一条旧任务记录；选择后可只读核对原任务。",
              observationState: "idle",
              receipt: null,
              taskStatus: null,
            },
          }
          : entries);
        setSelectedSubmissionKey((selected) => selected ?? record.idempotencyKey);
      }
    };
    ingestRegistryRef.current = syncRegistry;
    syncRegistry();
    const onStorage = (event: StorageEvent): void => {
      if (event.key === null || event.key.startsWith("haoai:fused-submission:v1:")
        || event.key.startsWith("haoai:fused-submission:v2:")) syncRegistry();
    };
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("storage", onStorage);
      if (ingestRegistryRef.current === syncRegistry) ingestRegistryRef.current = () => undefined;
    };
  }, [services, seriesId, userId, sessionGeneration]);

  function savePreference(nextModelId: string, nextPromptId: string, nextSize: string): void {
    const key = fusedGenerationPreferenceKey(userId, services.apiBaseUrl ?? "", memberType);
    let saved = false;
    try {
      saved = writeFusedGenerationPreference(window.localStorage, key, {
        modelId: nextModelId,
        promptId: nextPromptId,
        size: nextSize,
      });
    } catch {
      saved = false;
    }
    if (!saved) {
      setFeedback({ kind: "info", message: "浏览器没有保存本类型的本地偏好；当前页面选择仍可用于本次请求。" });
    } else {
      setFeedback(null);
    }
  }

  function changeMemberType(nextType: FusedGenerationMemberType): void {
    if (operationRef.current !== null || blocked || nextType === memberType) return;
    setMemberType(nextType);
    setFeedback(null);
  }

  function changeModel(nextModelId: string): void {
    if (operationRef.current !== null || blocked) return;
    const nextModel = models.find((model) => model.id === nextModelId) ?? null;
    const nextSize = fusedImageSizeOptions(nextModel).defaultValue;
    setModelId(nextModelId);
    setSize(nextSize);
    savePreference(nextModelId, promptId, nextSize);
  }

  function changePrompt(nextPromptId: string): void {
    if (operationRef.current !== null || blocked) return;
    setPromptId(nextPromptId);
    savePreference(modelId, nextPromptId, size);
  }

  function changeSize(nextSize: string): void {
    if (operationRef.current !== null || blocked) return;
    setSize(nextSize);
    savePreference(modelId, promptId, nextSize);
  }

  function toggleAsset(assetId: string): void {
    if (operationRef.current !== null || blocked) return;
    setSelectedAssetIds((current) => current.includes(assetId)
      ? current.filter((id) => id !== assetId)
      : [...current, assetId]);
    setFeedback(null);
  }

  function addImageUrl(): void {
    if (operationRef.current !== null || blocked) return;
    const url = safeFusedImageUrl(imageUrlDraft, services.apiBaseUrl);
    if (url === null) {
      setFeedback({ kind: "failure", message: "图片地址必须是安全的 HTTP(S) 地址；仅当前 API 同源可使用 HTTP。" });
      return;
    }
    setCustomImages((current) => [...current, { url, name: "自定义图片" }]);
    setImageUrlDraft("");
    setFeedback({ kind: "success", message: "图片地址已加入本次融合成员。" });
  }

  async function uploadImage(file: File): Promise<void> {
    if (operationRef.current !== null || blocked) return;
    const uploadPort = services.mediaReferenceUpload;
    if (uploadPort === undefined) {
      setFeedback({ kind: "failure", message: "当前服务未接入图片上传。" });
      return;
    }
    const scope = scopeRef.current;
    const controller = new AbortController();
    const active: ActiveOperation = { scope, controller, kind: "upload", phase: "uploading" };
    updateOperation(active);
    setFeedback({ kind: "info", message: "正在上传图片…" });
    try {
      const run = uploadPort.beginRun({
        actorId: scope.userId,
        isContextCurrent: () => isCurrent(scope, controller.signal),
      });
      const uploadedUrl = await run.upload("image", file, controller.signal);
      if (!isCurrent(scope, controller.signal) || !ownsOperation(active) || !run.isCurrent()) return;
      const url = safeFusedImageUrl(uploadedUrl, scope.apiBaseUrl);
      if (url === null) {
        setFeedback({ kind: "failure", message: "上传已返回，但地址未通过安全校验，未加入成员。" });
        return;
      }
      setCustomImages((current) => [...current, {
        url,
        name: file.name.replace(/\.[^.]+$/u, "").replace(/[\u0000-\u001f\u007f-\u009f]/gu, " ").trim().slice(0, 100)
          || "自定义图片",
      }]);
      setFeedback({ kind: "success", message: "图片已上传并加入本次融合成员；尚未提交生成。" });
    } catch (error) {
      if (!isCurrent(scope, controller.signal) || !ownsOperation(active)) return;
      if (error instanceof MediaReferenceUploadError && error.kind === "unauthorized") {
        unauthorizedRef.current();
        return;
      }
      setFeedback({
        kind: error instanceof MediaReferenceUploadError && error.kind === "unknown" ? "unknown" : "failure",
        message: error instanceof MediaReferenceUploadError && error.kind === "unknown"
          ? "图片上传结果未知；没有自动重传。请确认后再显式选择上传。"
          : "图片上传失败，文件没有加入本次成员。",
      });
    } finally {
      if (operationRef.current === active) updateOperation(null);
    }
  }

  function updateSubmissionEntry(
    key: string,
    update: (current: SubmissionEntryView) => SubmissionEntryView,
  ): void {
    setSubmissionEntries((current) => {
      const entry = current[key];
      if (entry === undefined) return current;
      return { ...current, [key]: update(entry) };
    });
  }

  function isCurrentSubmissionRead(
    record: FusedGenerationSubmissionRecord,
    scope: PanelScope,
    epoch: number,
    signal: AbortSignal,
  ): boolean {
    return isCurrent(scope, signal)
      && submissionReadEpochsRef.current.get(record.idempotencyKey) === epoch
      && submissionReadOwnersRef.current.get(record.idempotencyKey)?.epoch === epoch;
  }

  function stopSubmissionObservation(key: string): void {
    const owner = submissionReadOwnersRef.current.get(key);
    if (owner === undefined) return;
    owner.controller.abort();
    const nextEpoch = owner.epoch + 1;
    submissionReadEpochsRef.current.set(key, nextEpoch);
    submissionReadOwnersRef.current.delete(key);
    updateSubmissionEntry(key, (entry) => ({
      ...entry,
      observationState: "stopped",
      phase: entry.identityConflict ? "conflict" : entry.phase,
      message: entry.identityConflict
        ? submissionIdentityConflictMessage
        : "已停止本地观察；服务端任务仍在，原任务记录保留。你可以随时只读核对。",
    }));
  }

  async function observeSubmission(
    record: FusedGenerationSubmissionRecord,
  ): Promise<void> {
    const scope = scopeRef.current;
    if (record.userId !== scope.userId || record.apiBaseUrl !== (scope.apiBaseUrl ?? "")
      || record.seriesId !== scope.seriesId) return;
    const key = record.idempotencyKey;
    if (!submissionReadOwnersRef.current.has(key)
      && submissionReadOwnersRef.current.size >= MAX_CONCURRENT_SUBMISSION_READS) {
      updateSubmissionEntry(key, (entry) => ({
        ...entry,
        phase: entry.identityConflict ? "conflict" : "unknown",
        observationState: "read_error",
        message: entry.identityConflict
          ? submissionIdentityConflictMessage
          : "已有三条任务正在只读核对；稍后可单独核对本条。",
      }));
      return;
    }
    submissionReadOwnersRef.current.get(key)?.controller.abort();
    const epoch = (submissionReadEpochsRef.current.get(key) ?? 0) + 1;
    submissionReadEpochsRef.current.set(key, epoch);
    const controller = new AbortController();
    const owner: SubmissionReadOwner = { scope, controller, epoch };
    submissionReadOwnersRef.current.set(key, owner);
    updateSubmissionEntry(key, (entry) => ({
      ...entry,
      observationState: "active",
      phase: entry.identityConflict ? "conflict" : entry.phase,
      message: entry.identityConflict
        ? `${submissionIdentityConflictMessage} 正在核对原任务标识。`
        : "正在只读核对原融合任务；不会重新提交生成。",
    }));
    const lookup = scope.services.fusedGeneration?.lookupSubmission;
    if (lookup === undefined) {
      updateSubmissionEntry(key, (entry) => ({
        ...entry,
        phase: entry.identityConflict ? "conflict" : "unknown",
        observationState: "read_error",
        message: entry.identityConflict
          ? submissionIdentityConflictMessage
          : "当前服务暂不支持只读核对；原任务记录保留，不会重新提交。",
      }));
      submissionReadOwnersRef.current.delete(key);
      return;
    }
    let readError = false;
    try {
      const receipt = await lookup(key, controller.signal);
      if (!isCurrentSubmissionRead(record, scope, epoch, controller.signal)) return;
      updateSubmissionEntry(key, (entry) => ({
        ...entry,
        receipt,
        phase: entry.identityConflict ? "conflict"
          : receipt.status === "completed" ? "completed"
            : receipt.status === "failed" || receipt.status === "rejected_after_charge" ? "failed"
              : receipt.status === "accepted" || receipt.status === "result_known" ? "accepted" : "unknown",
        message: entry.identityConflict
          ? submissionIdentityConflictMessage
          : submissionReceiptMessage(receipt),
      }));
      const canReadTask = receipt.taskId !== null
        && ["accepted", "result_known", "completed", "failed"].includes(receipt.status);
      if (!canReadTask || scope.services.getTaskReceipt === undefined) return;
      const task = await scope.services.getTaskReceipt(receipt.taskId, controller.signal);
      if (!isCurrentSubmissionRead(record, scope, epoch, controller.signal)) return;
      if (task.id !== receipt.taskId || task.type !== "fused" || task.messageId !== receipt.messageId) {
        updateSubmissionEntry(key, (entry) => ({
          ...entry,
          phase: entry.identityConflict ? "conflict" : "unknown",
          taskStatus: null,
          message: entry.identityConflict
            ? submissionIdentityConflictMessage
            : "任务回执身份不匹配；原任务记录保留，结果仍未知，不会重新提交。",
        }));
        return;
      }
      const terminal = isTerminalTaskStatus(task.status);
      updateSubmissionEntry(key, (entry) => ({
        ...entry,
        phase: entry.identityConflict ? "conflict"
          : terminal
            ? (["completed", "success", "succeeded"].includes(task.status.toLowerCase()) ? "completed" : "failed")
            : "accepted",
        taskStatus: task.status,
        message: entry.identityConflict
          ? submissionIdentityConflictMessage
          : terminal
            ? `原融合任务${readableStatus(task.status)}；本地恢复记录仍保留。`
            : `原融合任务状态：${readableStatus(task.status)}。`,
      }));
      if (terminal && isCurrent(scope, controller.signal)) await refreshAssetsRef.current();
    } catch (error) {
      if (!isCurrentSubmissionRead(record, scope, epoch, controller.signal)) return;
      if (error instanceof ApiError && error.status === 401) {
        unauthorizedRef.current();
      } else {
        readError = true;
        const notFound = error instanceof ApiError && error.status === 404;
        updateSubmissionEntry(key, (entry) => ({
          ...entry,
          phase: entry.identityConflict ? "conflict" : "unknown",
          observationState: "read_error",
          message: entry.identityConflict ? submissionIdentityConflictMessage : notFound
            ? "服务端暂时没有这条原任务记录；记录仍保留，你可以稍后再核对，不会重新提交。"
            : "原任务只读核对失败；记录仍保留，你可以稍后重试核对。",
        }));
      }
    } finally {
      if (submissionReadOwnersRef.current.get(key) === owner) {
        submissionReadOwnersRef.current.delete(key);
        if (!readError && isCurrent(scope)) {
          updateSubmissionEntry(key, (entry) => entry.observationState === "active"
            ? { ...entry, observationState: "idle" }
            : entry);
        }
      }
    }
  }

  function submissionReceiptMessage(receipt: FusedSubmissionReceipt): string {
    const amount = feeSummary(receipt.feeAmount);
    switch (receipt.status) {
      case "accepted":
      case "result_known":
        return "服务端已受理原融合任务，正在只读核对任务详情。";
      case "completed":
        return `原融合任务已完成${amount}；恢复记录仍保留。`;
      case "failed":
        return `原融合任务已失败${amount}；恢复记录仍保留。`;
      case "provider_result_unknown":
        return `原任务的生成结果仍在确认中${amount}。不会再次提交生成。`;
      case "media_result_unknown":
        return `原任务的图片保存或送达情况仍待确认${amount}。不会重新生成。`;
      case "charged_incomplete":
        return `原任务仍未完成${amount}。费用状态以服务端回执为准，不会重新提交生成。`;
      case "rejected_after_charge":
        return `服务端保存了原拒绝回执${amount}。如需继续，请显式创建新任务。`;
    }
  }

  async function submitGeneration(): Promise<void> {
    if (operationRef.current !== null || blocked) return;
    const submit = services.fusedGeneration;
    if (submit === undefined) {
      setFeedback({ kind: "failure", message: "当前服务未接入融合生成。" });
      return;
    }
    if (!registryAvailable) {
      setFeedback({ kind: "failure", message: "本地任务定位记录不可安全枚举，未发送请求。" });
      return;
    }
    if (catalogLoading || catalogError !== null || assetsLoading || assetsError !== null
      || readyPreferenceContext !== preferenceContext) {
      setFeedback({ kind: "failure", message: "模型、提示词或当前剧集成员目录尚未完成核对。" });
      return;
    }
    const scope = scopeRef.current;
    const selectedModel = models.find((model) => model.id === modelId && model.category === "image");
    const sizeOptions = fusedImageSizeOptions(selectedModel ?? null);
    if (selectedModel === undefined || !sizeOptions.values.includes(size)) {
      setFeedback({ kind: "failure", message: "请选择当前生图模型支持的尺寸。" });
      return;
    }
    const selectedAssets = selectedAssetIds.map((id) => {
      const matches = assets.filter((asset) => asset.id === id && asset.series_id === scope.seriesId);
      return matches.length === 1 && hasImage(matches[0]!)
        ? { kind: "asset" as const, id, name: assetName(matches[0]!) }
        : null;
    });
    if (selectedAssets.some((asset) => asset === null)) {
      setFeedback({ kind: "failure", message: "所选资产缺少可用图片或身份重复；请刷新素材目录核对。" });
      return;
    }
    const members = [
      ...selectedAssets.filter((asset): asset is NonNullable<typeof asset> => asset !== null),
      ...customImages.map((image) => ({ kind: "image" as const, url: image.url, name: image.name })),
    ];
    if (members.length === 0) {
      setFeedback({ kind: "failure", message: "请至少选择一项带图片的角色/道具，或添加自定义图片。" });
      return;
    }
    const selectedPrompt = promptId === "" ? null : promptSelection(prompts, promptId);
    if (promptId !== "" && selectedPrompt === null) {
      setFeedback({ kind: "failure", message: "所选融合提示词不在当前目录中，请重新选择。" });
      return;
    }
    const prompt = resolveFusedPrompt(selectedPrompt, memberType);
    if (selectedPrompt !== null && prompt.system === null && prompt.user === null) {
      setFeedback({
        kind: "failure",
        message: "所选模板没有可用于当前融合类型的提示词内容。请改选模板或选择“不使用模板”。",
      });
      return;
    }

    const target = draft.target;
    const input: FusedGenerationInput = Object.freeze({
      series_id: scope.seriesId,
      member_type: memberType,
      members: Object.freeze(members),
      model_id: selectedModel.id,
      prompt_id: promptId,
      prompt_system: prompt.system,
      prompt_user: prompt.user,
      size,
      chapter_id: target?.chapterId ?? null,
      frame_index: target?.frameIndex ?? null,
      name: null,
      ...(target?.storyboardAssetId === undefined ? {} : { storyboard_asset_id: target.storyboardAssetId }),
      ...(target === null ? {} : { expected_frame: target.expectedFrame }),
      ...(target?.expectedContent === undefined ? {} : { expected_content: target.expectedContent }),
    });
    let idempotencyKey: string;
    try {
      idempotencyKey = newFusedSubmissionKey();
    } catch {
      setFeedback({ kind: "failure", message: "无法生成安全的融合提交核对键，未发送请求。" });
      return;
    }
    const record: FusedGenerationSubmissionRecord = Object.freeze({
      version: 2,
      userId: scope.userId,
      apiBaseUrl: scope.apiBaseUrl ?? "",
      seriesId: scope.seriesId,
      memberType,
      operation: "fused.image",
      idempotencyKey,
      target: target === null
        ? Object.freeze({ kind: "standalone" })
        : Object.freeze({
          kind: "frame",
          chapterId: target.chapterId,
          ...(target.storyboardAssetId === undefined ? {} : { storyboardAssetId: target.storyboardAssetId }),
          frameIndex: target.frameIndex,
        }),
    });
    let recordSaved = false;
    try {
      if (typeof window !== "undefined") {
        const storage = window.localStorage;
        recordSaved = writeFusedGenerationSubmissionRecord(storage, record);
      }
    } catch {
      recordSaved = false;
    }
    if (!recordSaved) {
      setFeedback({ kind: "failure", message: "浏览器未能保存并确认本次原任务标识，请求未发送。" });
      return;
    }

    setSubmissionEntries((entries) => ({
      ...entries,
      [idempotencyKey]: {
        record,
        identityConflict: false,
        phase: "posting",
        message: "正在发送本次新融合任务；恢复定位已先写入并回读。",
        observationState: "idle",
        receipt: null,
        taskStatus: null,
      },
    }));
    setSelectedSubmissionKey(idempotencyKey);
    const controller = new AbortController();
    const active: ActiveOperation = {
      scope,
      controller,
      kind: "generate",
      phase: "submitting",
      memberType,
      submissionKey: idempotencyKey,
    };
    updateOperation(active);
    setFeedback({ kind: "info", message: "正在提交融合生成任务…" });
    try {
      const receipt = await submit.submit(input, controller.signal, { idempotencyKey });
      if (!isCurrent(scope, controller.signal) || !ownsOperation(active)) return;
      updateSubmissionEntry(idempotencyKey, (entry) => ({
        ...entry,
        phase: entry.identityConflict ? "conflict" : "accepted",
        message: entry.identityConflict
          ? submissionIdentityConflictMessage
            : `服务器已受理任务 ${receipt.taskId}；需要时可只读核对原任务。`,
      }));
      active.phase = "tracking";
      active.taskId = receipt.taskId;
      active.messageId = receipt.messageId;
      updateOperation(null);
      setFeedback({ kind: "success", message: "新任务已受理；原任务记录和本次定位记录均保留。" });
    } catch (error) {
      if (!isCurrent(scope, controller.signal) || !ownsOperation(active)) return;
      updateOperation(null);
      if (error instanceof ApiError && error.status === 401) {
        unauthorizedRef.current();
        updateSubmissionEntry(idempotencyKey, (entry) => ({
          ...entry,
          phase: entry.identityConflict ? "conflict" : "unknown",
          message: entry.identityConflict
            ? submissionIdentityConflictMessage
            : "本次提交遇到登录状态变化；原任务记录已保留，可在当前账号恢复后只读核对。",
        }));
        return;
      }
      if (error instanceof ApiError && error.kind === "http" && error.status !== null
        && error.status >= 400 && error.status < 500 && error.status !== 408) {
        updateSubmissionEntry(idempotencyKey, (entry) => ({
          ...entry,
          phase: entry.identityConflict ? "conflict" : "failed",
          message: entry.identityConflict ? submissionIdentityConflictMessage : safeFailureMessage(error),
        }));
        setFeedback({ kind: "failure", message: safeFailureMessage(error) });
        return;
      }
      updateSubmissionEntry(idempotencyKey, (entry) => ({
        ...entry,
        phase: entry.identityConflict ? "conflict" : "unknown",
        message: safeFailureMessage(error),
      }));
      setFeedback({ kind: "unknown", message: safeFailureMessage(error) });
    }
  }

  function stopLocalTracking(): void {
    const active = operationRef.current;
    if (active === null) return;
    active.controller.abort();
    if (active.kind === "generate" && active.submissionKey !== undefined) {
      updateSubmissionEntry(active.submissionKey, (entry) => ({
        ...entry,
        phase: entry.identityConflict ? "conflict" : "unknown",
        message: entry.identityConflict
          ? submissionIdentityConflictMessage
          : "已停止本地等待；服务端任务仍可能受理，原任务记录保留。",
      }));
    }
    updateOperation(null);
    setFeedback({
      kind: "unknown",
      message: active.kind === "generate"
        ? "已停止本地等待；服务器上的任务仍可能完成或产生费用。你可以对该卡片只读核对原任务。"
        : "已停止本地等待；上传请求仍可能在服务器完成，未知图片不会加入本次成员列表。",
    });
  }

  function startNewDraft(): void {
    if (operationRef.current !== null || blocked) return;
    draftInteractionRef.current[memberType] += 1;
    setDrafts((current) => ({
      ...current,
      [memberType]: {
        ...current[memberType],
        selectedAssetIds: [],
        customImages: [],
        imageUrlDraft: "",
        target: null,
      },
    }));
    setFeedback({ kind: "info", message: "已清空当前类型的成员与分镜目标；提示词、模型和尺寸保留。下一次提交会建立新任务。" });
  }

  function applyOpenIntent(): void {
    if (openIntent === null || operationRef.current !== null || blocked) return;
    const scope = scopeRef.current;
    if (openIntent.userId !== scope.userId || openIntent.apiBaseUrl !== scope.apiBaseUrl
      || openIntent.seriesId !== scope.seriesId || openIntent.sessionGeneration !== scope.sessionGeneration) {
      setFeedback({ kind: "failure", message: "入口来源已不属于当前会话，未应用。请从来源重新打开。" });
      return;
    }
    const type = openIntent.memberType;
    const selectedIds = [...new Set(openIntent.memberAssetIds)];
    if (memberType !== type) {
      setMemberType(type);
      setFeedback({
        kind: "info",
        message: `已切换到${type === "character" ? "角色" : "道具"}类型；当前目录回读完成后，请再次确认应用来源。`,
      });
      return;
    }
    if (assetsLoading || assetsError !== null) {
      setFeedback({ kind: "failure", message: "当前角色/道具目录尚未完成核对，入口未应用。" });
      return;
    }
    if ((openIntent.target === null && selectedIds.length === 0)
      || selectedIds.length !== openIntent.memberAssetIds.length
      || selectedIds.some((id) => {
        const matches = assets.filter((asset) => asset.id === id && asset.series_id === scope.seriesId);
        return matches.length !== 1 || !hasImage(matches[0]!);
      })) {
      setFeedback({ kind: "failure", message: "入口中的角色/道具素材已变化、缺图或身份重复，请返回来源重新选择。" });
      return;
    }
    draftInteractionRef.current[type] += 1;
    setMemberType(type);
    setDrafts((current) => ({
      ...current,
      [type]: {
        ...current[type],
        selectedAssetIds: selectedIds,
        customImages: [],
        imageUrlDraft: "",
        target: openIntent.target,
      },
    }));
    setFeedback(openIntent.target === null
      ? {
        kind: "info",
        message: "角色/道具素材已应用到当前草稿；目标保持为独立生成，提示词、模型和尺寸保留。",
      }
      : {
        kind: "info",
        message: "来源已显式应用到当前草稿；分镜证明只保存在内存，并会随本次请求发送以供费用前核验。",
      });
    onIntentConsumed?.(openIntent.id);
  }

  const currentModel = models.find((model) => model.id === modelId && model.category === "image") ?? null;
  const sizes = fusedImageSizeOptions(currentModel);
  const preferenceContextReady = readyPreferenceContext === preferenceContext;
  const locked = operation !== null;
  const disabled = locked || blocked || catalogLoading || assetsLoading || !preferenceContextReady
    || !registryAvailable;
  const validAssets = assets.filter((asset) => asset.series_id === seriesId);
  const visibleSubmissionEntries = Object.values(submissionEntries)
    .filter((entry) => entry.record.userId === userId && entry.record.seriesId === seriesId
      && entry.record.apiBaseUrl === (services.apiBaseUrl ?? ""))
    .sort((left, right) => left.record.idempotencyKey.localeCompare(right.record.idempotencyKey));
  const activeSubmissionReadCount = visibleSubmissionEntries
    .filter((entry) => entry.observationState === "active").length;

  return (
    <section className="fused-generation-panel" aria-label="融合图片生成">
      <div className="fused-generation-heading">
        <div>
          <h2>生成融合图片</h2>
          <p>仅使用当前剧集角色/道具和你明确添加的图片。提交后由服务器按现有规则处理费用。</p>
        </div>
        {locked && (
          <button className="text-button" onClick={stopLocalTracking} type="button">停止本地等待</button>
        )}
      </div>

      {registryWarning !== null && <p className="fused-generation-alert" role="status">{registryWarning}</p>}
      {openIntent !== null && (
        <div className="fused-generation-entry-intent" aria-label="融合生成入口">
          <p>
            {openIntent.target === null
              ? `角色/道具素材入口已准备；会保持独立生成，并在应用前核对当前目录。`
              : `已回读来源 ${openIntent.target.chapterId} 的${openIntent.target.storyboardAssetId === undefined
                ? `第 ${openIntent.target.frameIndex + 1} 个分镜`
                : `分镜 ${openIntent.target.storyboardAssetId}`}。`}
            应用来源会替换当前类型的成员和目标；提示词、模型、尺寸保留。
          </p>
          <button className="secondary-button" disabled={locked || blocked || assetsLoading || assetsError !== null} onClick={applyOpenIntent} type="button">
            应用来源到当前草稿
          </button>
        </div>
      )}
      {draft.target !== null && (
        <div className="fused-generation-target" aria-label="当前分镜目标">
          <span>
            目标分镜：{draft.target.storyboardAssetId ?? `第 ${draft.target.frameIndex + 1} 个分镜`}（{draft.target.chapterId}）
          </span>
          <button className="text-button" disabled={locked || blocked} onClick={() => setDraftField(memberType, "target", null)} type="button">
            改为独立生成
          </button>
        </div>
      )}

      <div className="fused-generation-fields">
        <label>
          <span>融合类型</span>
          <select disabled={locked || blocked} onChange={(event) => changeMemberType(event.currentTarget.value as FusedGenerationMemberType)} value={memberType}>
            <option value="character">角色</option>
            <option value="prop">道具</option>
          </select>
        </label>
        <label>
          <span>生图模型</span>
          <select disabled={disabled || catalogError !== null} onChange={(event) => changeModel(event.currentTarget.value)} value={modelId}>
            <option value="">请选择模型</option>
            {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
          </select>
        </label>
        <label>
          <span>融合提示词</span>
          <select disabled={disabled || catalogError !== null} onChange={(event) => changePrompt(event.currentTarget.value)} value={promptId}>
            <option value="">不使用模板</option>
            {prompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}</option>)}
          </select>
        </label>
        <label>
          <span>图片尺寸</span>
          <select disabled={disabled || modelId === ""} onChange={(event) => changeSize(event.currentTarget.value)} value={size}>
            {sizes.values.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
      </div>

      {catalogLoading && <p className="fused-generation-state" role="status">正在读取模型和融合提示词…</p>}
      {catalogError !== null && <p className="fused-generation-alert" role="alert">{catalogError}</p>}
      {assetsLoading && <p className="fused-generation-state" role="status">正在读取当前剧集{memberType === "character" ? "角色" : "道具"}…</p>}
      {assetsError !== null && <p className="fused-generation-alert" role="alert">{assetsError}</p>}
      {catalogError === null && !catalogLoading && models.length === 0 && (
        <p className="fused-generation-alert" role="alert">当前没有可用的生图模型。</p>
      )}

      <fieldset className="fused-generation-members" disabled={disabled || assetsError !== null}>
        <legend>选择融合成员</legend>
        {validAssets.length === 0 && !assetsLoading && assetsError === null && (
          <p className="fused-generation-state">当前剧集没有{memberType === "character" ? "角色" : "道具"}。</p>
        )}
        {validAssets.map((asset) => {
          const label = assetName(asset);
          const imageAvailable = hasImage(asset);
          return (
            <label className="fused-generation-member" key={asset.id}>
              <input
                aria-label={label}
                checked={selectedAssetIds.includes(asset.id)}
                disabled={!imageAvailable}
                onChange={() => toggleAsset(asset.id)}
                type="checkbox"
              />
              <span>{label}</span>
              {!imageAvailable && <span className="fused-generation-missing">缺少图片</span>}
            </label>
          );
        })}
      </fieldset>

      <div className="fused-generation-custom-image">
        <label>
          <span>自定义图片地址</span>
          <input
            disabled={disabled}
            onChange={(event) => setImageUrlDraft(event.currentTarget.value)}
            placeholder="https://… 或当前服务的 /uploads/… 地址"
            value={imageUrlDraft}
          />
        </label>
        <button className="secondary-button" disabled={disabled || imageUrlDraft.trim() === ""} onClick={addImageUrl} type="button">
          添加图片地址
        </button>
        <label className="fused-generation-file-label">
          <span>或上传图片</span>
          <input
            accept="image/jpeg,image/png,image/gif,image/webp,image/bmp"
            disabled={disabled || services.mediaReferenceUpload === undefined}
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              if (file !== undefined) void uploadImage(file);
            }}
            type="file"
          />
        </label>
      </div>

      {customImages.length > 0 && (
        <ul className="fused-generation-custom-list" aria-label="已添加的自定义图片">
          {customImages.map((image, index) => (
            <li key={`${image.url}:${index}`}>
              <span>{image.name}</span>
              <button
                aria-label={`移除自定义图片 ${index + 1}`}
                disabled={disabled}
                onClick={() => setCustomImages((current) => current.filter((_, currentIndex) => currentIndex !== index))}
                type="button"
              >移除</button>
            </li>
          ))}
        </ul>
      )}

      {feedback !== null && <p className={`fused-generation-feedback fused-generation-${feedback.kind}`} role={feedback.kind === "failure" ? "alert" : "status"}>{feedback.message}</p>}

      <section className="fused-generation-submissions" aria-label="可恢复融合任务">
        <div className="fused-generation-submissions-heading">
          <h3>融合任务记录</h3>
          {visibleSubmissionEntries.length > 0 && (
            <button className="text-button" disabled={!registryAvailable} onClick={() => ingestRegistryRef.current()} type="button">
              刷新本地任务记录
            </button>
          )}
        </div>
        {visibleSubmissionEntries.length === 0
          ? <p className="fused-generation-state">当前账号和剧集还没有本地恢复记录。</p>
          : <ul className="fused-generation-submission-list">
            {visibleSubmissionEntries.map((entry) => {
              const key = entry.record.idempotencyKey;
              const target = entry.record.target;
              const targetLabel = target.kind === "standalone"
                ? "独立生成"
                : target.storyboardAssetId ?? `章节 ${target.chapterId} · 第 ${(target.frameIndex ?? 0) + 1} 个分镜`;
              return (
                <li className={selectedSubmissionKey === key ? "is-selected" : ""} key={key}>
                  <button className="text-button" onClick={() => setSelectedSubmissionKey(key)} type="button">
                    {entry.record.memberType === "character" ? "角色" : "道具"} · {targetLabel} · {key.slice(0, 8)}
                  </button>
                  <span className={`fused-generation-submission-status fused-generation-submission-${entry.phase}`}>
                    {entry.taskStatus === null ? entry.phase === "posting" ? "提交中" : entry.phase === "accepted" ? "已受理" : entry.phase === "completed" ? "已完成" : entry.phase === "failed" ? "已结束" : entry.phase === "conflict" ? "记录冲突" : "待核对" : readableStatus(entry.taskStatus)}
                  </span>
                  <p role="status">{entry.message}</p>
                  <div>
                    {entry.observationState === "active" ? (
                      <button className="text-button" onClick={() => stopSubmissionObservation(key)} type="button">停止观察</button>
                    ) : (
                      <button
                        className="text-button"
                        disabled={!registryAvailable || activeSubmissionReadCount >= MAX_CONCURRENT_SUBMISSION_READS}
                        onClick={() => void observeSubmission(entry.record)}
                        type="button"
                      >只读核对</button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>}
      </section>

      <div className="fused-generation-actions">
        {visibleSubmissionEntries.length > 0 && (
          <button className="secondary-button" disabled={locked || blocked} onClick={startNewDraft} type="button">
            清空当前草稿，开始另一条任务
          </button>
        )}
        <button className="primary-button" disabled={disabled || modelId === "" || models.length === 0} onClick={() => void submitGeneration()} type="button">
          {operation?.phase === "submitting" ? "正在提交…" : visibleSubmissionEntries.length > 0 ? "提交新的独立融合任务" : "提交融合生成"}
        </button>
      </div>
    </section>
  );
}
