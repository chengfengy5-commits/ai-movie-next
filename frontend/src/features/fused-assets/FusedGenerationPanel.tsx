import { useCallback, useEffect, useRef, useState } from "react";
import type { Character, Prop } from "../../shared/api/contracts";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import { ApiError } from "../../shared/api/errors";
import type { FusedGenerationInput, FusedGenerationMemberType } from "../../shared/api/fusedGeneration";
import { MediaReferenceUploadError } from "../../shared/api/mediaReferenceUpload";
import type { TaskReceipt } from "../../shared/api/taskDetails";
import type { WorkspaceServices } from "../../shared/api/services";
import {
  fusedGenerationPreferenceKey,
  fusedImageSizeOptions,
  readFusedGenerationPreferenceResult,
  resolveFusedPrompt,
  safeFusedImageUrl,
  writeFusedGenerationPreference,
} from "./fusedGenerationPreferences";

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

interface ActiveOperation {
  readonly scope: PanelScope;
  readonly controller: AbortController;
  readonly kind: "upload" | "generate";
  phase: "uploading" | "submitting" | "tracking" | "unknown";
  taskId?: string;
  messageId?: string | null;
}

interface Props {
  readonly services: WorkspaceServices;
  readonly seriesId: string;
  readonly userId: string;
  readonly sessionGeneration: number;
  readonly blocked: boolean;
  onUnauthorized(): void;
  onActivityChange(active: boolean): void;
  onRefreshAssets(): Promise<void>;
}

function sameScope(left: PanelScope, right: PanelScope): boolean {
  return left.services === right.services && left.apiBaseUrl === right.apiBaseUrl
    && left.seriesId === right.seriesId && left.userId === right.userId
    && left.sessionGeneration === right.sessionGeneration;
}

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
    return "融合生成服务暂不可用（HTTP 503）。本次没有可核对的任务回执；系统不会自动重发。";
  }
  if (error instanceof ApiError && error.status !== null && error.status >= 400 && error.status < 500
    && error.status !== 408) {
    return `融合生成请求被服务拒绝（HTTP ${error.status}）。请核对成员图片、模型和提示词后再决定是否重新提交。`;
  }
  return "融合生成请求结果未知，服务器可能已受理且费用状态尚未确认。系统不会自动重发；请只读核对任务或明确停止本地跟踪。";
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

function isMatchingTask(receipt: TaskReceipt, operation: ActiveOperation): boolean {
  return receipt.id === operation.taskId && receipt.type === "fused"
    && receipt.messageId === (operation.messageId ?? null);
}

export function FusedGenerationPanel({
  services,
  seriesId,
  userId,
  sessionGeneration,
  blocked,
  onUnauthorized,
  onActivityChange,
  onRefreshAssets,
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
  const taskReadOwnerRef = useRef<object | null>(null);
  const catalogControllerRef = useRef<AbortController | null>(null);
  const assetControllerRef = useRef<AbortController | null>(null);
  const selectedTypeRef = useRef<FusedGenerationMemberType>("character");
  const restoredPreferenceContextRef = useRef<string | null>(null);

  const [memberType, setMemberType] = useState<FusedGenerationMemberType>("character");
  selectedTypeRef.current = memberType;
  const [models, setModels] = useState<CreatorModel[]>([]);
  const [prompts, setPrompts] = useState<CreatorPrompt[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [assets, setAssets] = useState<FusedMemberAsset[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(true);
  const [assetsError, setAssetsError] = useState<string | null>(null);
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([]);
  const [customImages, setCustomImages] = useState<CustomImage[]>([]);
  const [imageUrlDraft, setImageUrlDraft] = useState("");
  const [modelId, setModelId] = useState("");
  const [promptId, setPromptId] = useState("");
  const [size, setSize] = useState("1024x1024");
  const [readyPreferenceContext, setReadyPreferenceContext] = useState<string | null>(null);
  const [operation, setOperation] = useState<ActiveOperation | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);
  const [taskReadBusy, setTaskReadBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

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
    restoredPreferenceContextRef.current = null;
    setMemberType("character");
    setModels([]);
    setPrompts([]);
    setCatalogLoading(true);
    setCatalogError(null);
    setAssets([]);
    setAssetsLoading(true);
    setAssetsError(null);
    setSelectedAssetIds([]);
    setCustomImages([]);
    setImageUrlDraft("");
    setModelId("");
    setPromptId("");
    setSize("1024x1024");
    setReadyPreferenceContext(null);
    const previousOperation = operationRef.current;
    if (previousOperation !== null) {
      previousOperation.controller.abort();
      operationRef.current = null;
    }
    setOperation(null);
    setTaskStatus(null);
    setTaskReadBusy(false);
    taskReadOwnerRef.current = null;
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
    setSelectedAssetIds([]);
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
    if (catalogLoading || catalogError !== null || restoredPreferenceContextRef.current === preferenceContext) return;
    const storageRead = preferenceFromStorage(scopeRef.current, memberType);
    const saved = storageRead.value;
    const nextModel = models.find((model) => model.id === saved?.modelId) ?? models[0] ?? null;
    const choices = fusedImageSizeOptions(nextModel);
    setModelId(nextModel?.id ?? "");
    setSize(saved !== null && choices.values.includes(saved.size) ? saved.size : choices.defaultValue);
    const nextPrompt = prompts.find((prompt) => prompt.id === saved?.promptId && prompt.category === "fuse");
    setPromptId(saved !== null && saved.promptId === "" ? "" : nextPrompt?.id ?? defaultPromptId(prompts));
    restoredPreferenceContextRef.current = preferenceContext;
    setReadyPreferenceContext(preferenceContext);
    if (!storageRead.available) {
      setFeedback({ kind: "info", message: "浏览器本地偏好不可读，当前使用本页默认选项；你仍可手动选择并生成。" });
    }
  }, [catalogLoading, catalogError, models, prompts, preferenceContext, memberType]);

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
    setTaskStatus(null);
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

  async function reconcileTask(active: ActiveOperation): Promise<void> {
    const getTaskReceipt = active.scope.services.getTaskReceipt;
    if (getTaskReceipt === undefined || active.taskId === undefined
      || operationRef.current !== active || taskReadOwnerRef.current !== null
      || !isCurrent(active.scope, active.controller.signal)) return;
    const readOwner = {};
    taskReadOwnerRef.current = readOwner;
    setTaskReadBusy(true);
    setFeedback({ kind: "info", message: "正在只读核对融合任务…" });
    try {
      const receipt = await getTaskReceipt(active.taskId, active.controller.signal);
      if (operationRef.current !== active || !isCurrent(active.scope, active.controller.signal)) return;
      if (!isMatchingTask(receipt, active)) {
        active.phase = "unknown";
        setFeedback({ kind: "unknown", message: "任务回执身份不匹配，结果仍未知。不会重复提交；可停止本地跟踪。" });
        return;
      }
      setTaskStatus(receipt.status);
      if (isTerminalTaskStatus(receipt.status)) {
        updateOperation(null);
        setFeedback({
          kind: receipt.status.toLowerCase() === "completed" || receipt.status.toLowerCase() === "success"
            || receipt.status.toLowerCase() === "succeeded" ? "success" : "failure",
          message: `融合任务${readableStatus(receipt.status)}；正在刷新当前剧集目录。`,
        });
        await refreshAssetsRef.current();
      } else {
        active.phase = "tracking";
        setFeedback({ kind: "info", message: `任务已受理，当前状态：${readableStatus(receipt.status)}。可手动核对状态。` });
      }
    } catch (error) {
      if (operationRef.current !== active || !isCurrent(active.scope, active.controller.signal)) return;
      if (error instanceof ApiError && error.status === 401) {
        unauthorizedRef.current();
        updateOperation(null);
      } else {
        setFeedback({ kind: "unknown", message: "任务已受理，但只读状态暂时无法核对。可重试只读核对，或停止本地跟踪；不会重复提交。" });
      }
    } finally {
      if (taskReadOwnerRef.current === readOwner) {
        taskReadOwnerRef.current = null;
        setTaskReadBusy(false);
      }
    }
  }

  async function submitGeneration(): Promise<void> {
    if (operationRef.current !== null || blocked) return;
    const submit = services.fusedGeneration;
    if (submit === undefined) {
      setFeedback({ kind: "failure", message: "当前服务未接入融合生成。" });
      return;
    }
    const scope = scopeRef.current;
    if (catalogLoading || catalogError !== null || assetsLoading || assetsError !== null
      || readyPreferenceContext !== preferenceContext) {
      setFeedback({ kind: "failure", message: "模型、提示词或当前剧集成员目录尚未完成核对。" });
      return;
    }
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
    const input: FusedGenerationInput = Object.freeze({
      series_id: scope.seriesId,
      member_type: memberType,
      members: Object.freeze(members),
      model_id: selectedModel.id,
      prompt_id: promptId,
      prompt_system: prompt.system,
      prompt_user: prompt.user,
      size,
      chapter_id: null,
      frame_index: null,
      name: null,
    });
    const controller = new AbortController();
    const active: ActiveOperation = { scope, controller, kind: "generate", phase: "submitting" };
    updateOperation(active);
    setTaskStatus(null);
    setFeedback({ kind: "info", message: "正在提交融合生成任务…" });
    try {
      const receipt = await submit.submit(input, controller.signal);
      if (!isCurrent(scope, controller.signal) || !ownsOperation(active)) return;
      active.phase = "tracking";
      active.taskId = receipt.taskId;
      active.messageId = receipt.messageId;
      setFeedback({ kind: "info", message: "任务已受理，正在只读核对任务状态。" });
      if (scope.services.getTaskReceipt === undefined) {
        setFeedback({ kind: "unknown", message: "任务已受理，但当前服务没有任务详情读取能力。不会重复提交；可停止本地跟踪。" });
      } else {
        await reconcileTask(active);
      }
    } catch (error) {
      if (!isCurrent(scope, controller.signal) || !ownsOperation(active)) return;
      if (error instanceof ApiError && error.status === 401) {
        unauthorizedRef.current();
        updateOperation(null);
        return;
      }
      if (error instanceof ApiError && error.kind === "http" && error.status !== null
        && error.status >= 400 && error.status < 500 && error.status !== 408) {
        updateOperation(null);
        setFeedback({ kind: "failure", message: safeFailureMessage(error) });
        return;
      }
      active.phase = "unknown";
      setFeedback({ kind: "unknown", message: safeFailureMessage(error) });
    }
  }

  function stopLocalTracking(): void {
    const active = operationRef.current;
    if (active === null) return;
    const kind = active.kind;
    active.controller.abort();
    taskReadOwnerRef.current = null;
    updateOperation(null);
    setTaskReadBusy(false);
    setFeedback({
      kind: "unknown",
      message: kind === "generate"
        ? "已停止本地跟踪；服务器上的融合任务仍可能运行并产生费用，未收到取消确认。请到任务页或刷新融合列表只读核对。"
        : "已停止本地跟踪；上传请求仍可能在服务器完成。未知图片不会加入本次成员列表。",
    });
  }

  const currentModel = models.find((model) => model.id === modelId && model.category === "image") ?? null;
  const sizes = fusedImageSizeOptions(currentModel);
  const preferenceContextReady = readyPreferenceContext === preferenceContext;
  const locked = operation !== null;
  const disabled = locked || blocked || catalogLoading || assetsLoading || !preferenceContextReady;
  const validAssets = assets.filter((asset) => asset.series_id === seriesId);

  return (
    <section className="fused-generation-panel" aria-label="融合图片生成">
      <div className="fused-generation-heading">
        <div>
          <h2>生成融合图片</h2>
          <p>仅使用当前剧集角色/道具和你明确添加的图片。提交后由服务器按现有规则处理费用。</p>
        </div>
        {locked && (
          <button className="text-button" onClick={stopLocalTracking} type="button">停止本地跟踪</button>
        )}
      </div>

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
      {taskStatus !== null && <p className="fused-generation-state" role="status">最近任务状态：{readableStatus(taskStatus)}</p>}

      <div className="fused-generation-actions">
        {operation?.kind === "generate" && operation.taskId !== undefined && (
          <button className="secondary-button" disabled={taskReadBusy} onClick={() => void reconcileTask(operation)} type="button">
            {taskReadBusy ? "正在核对…" : "核对任务状态"}
          </button>
        )}
        <button className="primary-button" disabled={disabled || modelId === "" || models.length === 0} onClick={() => void submitGeneration()} type="button">
          {operation?.phase === "submitting" ? "正在提交…" : "提交融合生成"}
        </button>
      </div>
    </section>
  );
}
