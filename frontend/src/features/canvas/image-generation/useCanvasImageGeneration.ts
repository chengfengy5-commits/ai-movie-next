import { useCallback, useEffect, useRef, useState } from "react";
import {
  CanvasImageGenerationError,
  isValidCanvasImageGenerationSize,
  type CanvasImageGenerationPort,
  type CanvasImageGenerationReceipt,
  type CanvasImageGenerationSubmitInput,
} from "../../../shared/api/canvasImageGeneration";
import type { CanvasOwnedResultsTarget } from "../../../shared/api/canvasOwnedResults";
import type { CreatorPrompt, CreatorSettingsServicePort } from "../../../shared/api/creatorSettings";
import type {
  CanvasImageGenerationBatchReceiptView,
  CanvasImageGenerationBatchView,
  CanvasImageGenerationIntentView,
  CanvasImageGenerationModelOption,
  CanvasImageGenerationPromptTemplateOption,
} from "./CanvasImageGenerationDialog";

export interface CanvasImageGenerationWorkspaceContext {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  isCurrent(): boolean;
}

export interface CanvasImageGenerationTarget {
  readonly selectedRef: string;
  readonly targetKey: string;
  readonly destination: CanvasOwnedResultsTarget;
  readonly targetLabel: string;
  readonly sourceVersion: string;
  readonly requiresLock: boolean;
  readonly promptSeed: string;
  readonly context: CanvasImageGenerationWorkspaceContext;
}

export interface CanvasImageGenerationLockResult {
  readonly status: "ready" | "needed" | "unavailable";
  readonly message: string;
}

export interface CanvasImageGenerationWorkspaceIntent extends CanvasImageGenerationIntentView {
  readonly input: CanvasImageGenerationSubmitInput;
  readonly target: CanvasImageGenerationTarget;
}

export interface CanvasImageGenerationWorkspaceReceipt extends CanvasImageGenerationBatchReceiptView {
  readonly target: CanvasImageGenerationTarget;
}

export interface CanvasImageGenerationControllerState {
  readonly mode: "form" | "intent" | "receipt" | null;
  readonly target: CanvasImageGenerationTarget | null;
  readonly models: readonly CanvasImageGenerationModelOption[];
  readonly selectedModelId: string;
  readonly selectedCount: number;
  readonly countSelectionInvalid: boolean;
  readonly selectedSize: string;
  readonly sizeSelectionInvalid: boolean;
  readonly promptTemplates: readonly CanvasImageGenerationPromptTemplateOption[];
  readonly selectedPromptTemplateId: string;
  readonly promptTemplateSelectionInvalid: boolean;
  readonly prompt: string;
  readonly modelsLoading: boolean;
  readonly promptTemplatesLoading: boolean;
  readonly promptTemplateError: string | null;
  readonly busy: boolean;
  readonly error: string | null;
  readonly message: string | null;
  readonly lockMessage: string | null;
  readonly lockReady: boolean;
  readonly lockPending: boolean;
  readonly intent: CanvasImageGenerationWorkspaceIntent | null;
  readonly receipt: CanvasImageGenerationWorkspaceReceipt | null;
  readonly batch: CanvasImageGenerationBatchView | null;
}

export interface CanvasImageGenerationControllerOptions {
  readonly contextKey: string;
  readonly serviceIdentity: object;
  readonly selectedTargetKey: string | null;
  readonly creatorSettings: CreatorSettingsServicePort | undefined;
  readonly port: CanvasImageGenerationPort | undefined;
  validateTarget(target: CanvasImageGenerationTarget, signal: AbortSignal, requireSelection: boolean): Promise<boolean>;
  checkEditLock(target: CanvasImageGenerationTarget, signal: AbortSignal): Promise<CanvasImageGenerationLockResult>;
  acquireEditLock(target: CanvasImageGenerationTarget, signal: AbortSignal): Promise<CanvasImageGenerationLockResult>;
  onGuardChange(blocked: boolean, hasUnresolvedIntent: boolean, busy: boolean): void;
}

type ActionKind = "submit" | "retry" | "lookup" | "lock";

interface ActionHandle {
  readonly controller: AbortController;
  readonly kind: ActionKind;
}

interface FrozenImageGenerationRequest {
  readonly ordinal: number;
  readonly input: CanvasImageGenerationSubmitInput;
}

interface FrozenImageGenerationBatch {
  readonly target: CanvasImageGenerationTarget;
  readonly modelName: string;
  readonly serviceIdentity: object;
  readonly port: CanvasImageGenerationPort;
  readonly requests: readonly FrozenImageGenerationRequest[];
  readonly acceptedReceipts: readonly CanvasImageGenerationWorkspaceReceipt[];
  readonly notSentCount: number;
  readonly activeOrdinal: number | null;
  readonly stoppedOrdinal: number | null;
  readonly outcome: CanvasImageGenerationBatchView["outcome"];
}

const EMPTY_STATE: CanvasImageGenerationControllerState = {
  mode: null,
  target: null,
  models: [],
  selectedModelId: "",
  selectedCount: 1,
  countSelectionInvalid: false,
  selectedSize: "",
  sizeSelectionInvalid: false,
  promptTemplates: [],
  selectedPromptTemplateId: "",
  promptTemplateSelectionInvalid: false,
  prompt: "",
  modelsLoading: false,
  promptTemplatesLoading: false,
  promptTemplateError: null,
  busy: false,
  error: null,
  message: null,
  lockMessage: null,
  lockReady: false,
  lockPending: false,
  intent: null,
  receipt: null,
  batch: null,
};

function errorMessage(cause: unknown): string {
  if (cause instanceof CanvasImageGenerationError) {
    switch (cause.kind) {
      case "not-sent": return "请求未发送。请检查当前章节、目标和图片模型后重试。";
      case "rejected": return "服务未接受这次图片任务。请调整内容后再提交。";
      case "unknown-outcome": return "提交结果暂时无法确认。请核对原操作，系统不会自动重试。";
      case "read-error": return "暂时无法读取原图片任务的受理记录。";
      case "unauthorized": return "登录身份未通过图片任务校验。";
      case "forbidden": return "当前身份无权提交或核对这条图片任务。";
      case "not-found": return "暂未查到这条图片任务，之前的提交仍可能稍后完成。";
      case "unavailable": return "图片任务服务暂时不可用。";
      case "invalid-response": return "图片任务服务返回了无法确认的记录。";
    }
  }
  return "无法确认图片任务操作。请检查画布来源、编辑锁与服务状态后重试。";
}

function receiptView(
  target: CanvasImageGenerationTarget,
  input: CanvasImageGenerationSubmitInput,
  modelName: string,
  receipt: CanvasImageGenerationReceipt,
  ordinal: number,
): CanvasImageGenerationWorkspaceReceipt {
  return {
    target,
    destination: input.destination,
    modelId: input.modelId,
    modelName,
    prompt: input.prompt,
    size: input.size ?? null,
    receipt,
    targetLabel: target.targetLabel,
    ordinal,
    idempotencyKey: input.idempotencyKey,
  };
}

function intentView(
  target: CanvasImageGenerationTarget,
  input: CanvasImageGenerationSubmitInput,
  modelName: string,
  state: CanvasImageGenerationWorkspaceIntent["state"],
  batch: CanvasImageGenerationBatchView,
  ordinal: number,
): CanvasImageGenerationWorkspaceIntent {
  return {
    target,
    destination: input.destination,
    idempotencyKey: input.idempotencyKey,
    modelId: input.modelId,
    modelName,
    prompt: input.prompt,
    size: input.size ?? null,
    targetLabel: target.targetLabel,
    ordinal,
    batch,
    input,
    state,
  };
}

function batchView(batch: FrozenImageGenerationBatch): CanvasImageGenerationBatchView {
  return {
    requestedCount: batch.requests.length,
    acceptedReceipts: batch.acceptedReceipts,
    notSentCount: batch.notSentCount,
    activeOrdinal: batch.activeOrdinal,
    stoppedOrdinal: batch.stoppedOrdinal,
    outcome: batch.outcome,
  };
}

function receiptRunContext(
  intent: CanvasImageGenerationWorkspaceIntent,
): {
  actorId: string;
  seriesId: string;
  chapterId: string;
  isContextCurrent(): boolean;
} {
  return {
    actorId: intent.target.context.actorId,
    seriesId: intent.target.context.seriesId,
    chapterId: intent.target.context.chapterId,
    isContextCurrent: () => intent.target.context.isCurrent(),
  };
}

function errorKind(cause: unknown): CanvasImageGenerationError["kind"] | null {
  return cause instanceof CanvasImageGenerationError ? cause.kind : null;
}

const IMAGE_PROMPT_TEMPLATE_CATEGORIES = new Set([
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
const LEGACY_IMAGE_SIZE_OPTIONS = Object.freeze([
  "1024x1024",
  "1792x1024",
  "1024x1792",
  "1216x832",
  "832x1216",
]);
const INVALID_IMAGE_SIZE_CONFIGURATION = "该图片模型的尺寸配置无效，无法安全提交。";

interface ImageSizeConfiguration {
  readonly sizeOptions: readonly string[];
  readonly defaultSize: string | null;
  readonly sizeConfigurationError: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function imageSizeConfiguration(params: string | null): ImageSizeConfiguration {
  const noSize = Object.freeze({
    sizeOptions: Object.freeze([]) as readonly string[],
    defaultSize: null,
    sizeConfigurationError: null,
  });
  if (params === null || params === "") return noSize;

  let parsed: unknown;
  try {
    parsed = JSON.parse(params) as unknown;
  } catch {
    return Object.freeze({ ...noSize, sizeConfigurationError: INVALID_IMAGE_SIZE_CONFIGURATION });
  }
  if (!isRecord(parsed)) {
    return Object.freeze({ ...noSize, sizeConfigurationError: INVALID_IMAGE_SIZE_CONFIGURATION });
  }
  if (!Object.prototype.hasOwnProperty.call(parsed, "image_size")) return noSize;

  const config = parsed.image_size;
  if (config === null) return noSize;
  if (!isRecord(config)) {
    return Object.freeze({ ...noSize, sizeConfigurationError: INVALID_IMAGE_SIZE_CONFIGURATION });
  }
  if (config.enabled === false || config.enabled === null || config.enabled === undefined) return noSize;
  if (config.enabled !== true) {
    return Object.freeze({ ...noSize, sizeConfigurationError: INVALID_IMAGE_SIZE_CONFIGURATION });
  }

  const hasOptions = config.options !== null && config.options !== undefined;
  let configuredOptions: readonly string[];
  if (hasOptions) {
    if (!Array.isArray(config.options) || config.options.length === 0
      || !config.options.every(isValidCanvasImageGenerationSize)) {
      return Object.freeze({ ...noSize, sizeConfigurationError: INVALID_IMAGE_SIZE_CONFIGURATION });
    }
    configuredOptions = Object.freeze([...new Set(config.options)]);
  } else {
    configuredOptions = LEGACY_IMAGE_SIZE_OPTIONS;
  }

  const hasDefault = config.default !== null && config.default !== undefined;
  let configuredDefault: string | null = null;
  if (hasDefault) {
    const value = config.default;
    if (!isValidCanvasImageGenerationSize(value)) {
      return Object.freeze({ ...noSize, sizeConfigurationError: INVALID_IMAGE_SIZE_CONFIGURATION });
    }
    configuredDefault = value;
  }
  if (hasOptions && configuredDefault !== null && !configuredOptions.includes(configuredDefault)) {
    return Object.freeze({ ...noSize, sizeConfigurationError: INVALID_IMAGE_SIZE_CONFIGURATION });
  }

  const defaultSize = configuredDefault !== null && configuredOptions.includes(configuredDefault)
    ? configuredDefault
    : configuredOptions[0] ?? null;
  return Object.freeze({
    sizeOptions: configuredOptions,
    defaultSize,
    sizeConfigurationError: null,
  });
}

function isPromptText(value: unknown): value is string | null | undefined {
  return value === undefined || value === null || typeof value === "string";
}

function projectImagePromptTemplates(rows: readonly CreatorPrompt[]): readonly CanvasImageGenerationPromptTemplateOption[] {
  const idCounts = new Map<string, number>();
  for (const row of rows) {
    if (isRecord(row) && typeof row.id === "string") {
      idCounts.set(row.id, (idCounts.get(row.id) ?? 0) + 1);
    }
  }

  const templates: CanvasImageGenerationPromptTemplateOption[] = [];
  for (const row of rows) {
    if (!isRecord(row)
      || typeof row.id !== "string"
      || row.id.trim() === ""
      || idCounts.get(row.id) !== 1
      || typeof row.name !== "string"
      || row.name.trim() === ""
      || typeof row.category !== "string"
      || !IMAGE_PROMPT_TEMPLATE_CATEGORIES.has(row.category)
      || !isPromptText(row.system_prompt)
      || !isPromptText(row.user_prompt)) continue;

    templates.push(Object.freeze({
      id: row.id,
      name: row.name,
      category: row.category,
      systemPrompt: row.system_prompt ?? null,
      userPrompt: row.user_prompt ?? null,
    }));
  }
  return Object.freeze(templates);
}

function composeImagePrompt(
  userInput: string,
  promptSeed: string,
  template: CanvasImageGenerationPromptTemplateOption | null,
): string {
  const basePrompt = (userInput || promptSeed || "").trim();
  if (template !== null) {
    const composed = [template.systemPrompt, template.userPrompt, userInput]
      .filter((part): part is string => typeof part === "string" && part !== "")
      .join("\n")
      .trim();
    if (composed !== "") return composed;
  }
  return basePrompt || "生成一张高质量图片";
}

export function useCanvasImageGenerationController(options: CanvasImageGenerationControllerOptions) {
  const [state, setState] = useState(EMPTY_STATE);
  const stateRef = useRef(state);
  stateRef.current = state;
  const intentRef = useRef<CanvasImageGenerationWorkspaceIntent | null>(state.intent);
  intentRef.current = state.intent;
  const batchRef = useRef<FrozenImageGenerationBatch | null>(null);
  const stopBatchAfterCurrentRef = useRef(false);
  const busyRef = useRef(state.busy);
  busyRef.current = state.busy;
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const modelControllerRef = useRef<AbortController | null>(null);
  const promptTemplateControllerRef = useRef<AbortController | null>(null);
  const actionRef = useRef<ActionHandle | null>(null);
  const requestDispatchedRef = useRef(false);
  const modelRunIdRef = useRef(0);
  const promptTemplateRunIdRef = useRef(0);

  const update = useCallback((change: (current: CanvasImageGenerationControllerState) => CanvasImageGenerationControllerState) => {
    const next = change(stateRef.current);
    stateRef.current = next;
    setState(next);
    return next;
  }, []);

  const readCurrentIntent = useCallback((): CanvasImageGenerationWorkspaceIntent | null => intentRef.current, []);

  const notifyGuard = useCallback(() => {
    const hasIntent = intentRef.current !== null;
    const busy = busyRef.current;
    const modalOpen = stateRef.current.mode !== null;
    optionsRef.current.onGuardChange(busy || hasIntent || modalOpen, hasIntent, busy);
  }, []);

  const setIntent = useCallback((intent: CanvasImageGenerationWorkspaceIntent | null) => {
    intentRef.current = intent;
    update((current) => ({ ...current, intent }));
    notifyGuard();
  }, [notifyGuard, update]);

  const setBusy = useCallback((busy: boolean) => {
    busyRef.current = busy;
    update((current) => ({ ...current, busy }));
    notifyGuard();
  }, [notifyGuard, update]);

  const finishAction = useCallback((action: ActionHandle, isContextCurrent: () => boolean) => {
    if (actionRef.current !== action || !isContextCurrent()) return;
    actionRef.current = null;
    requestDispatchedRef.current = false;
    setBusy(false);
  }, [setBusy]);

  const beginAction = useCallback((kind: ActionKind): ActionHandle | null => {
    if (busyRef.current) return null;
    const action = { controller: new AbortController(), kind };
    actionRef.current = action;
    requestDispatchedRef.current = false;
    setBusy(true);
    return action;
  }, [setBusy]);

  const close = useCallback(() => {
    modelRunIdRef.current += 1;
    modelControllerRef.current?.abort();
    modelControllerRef.current = null;
    promptTemplateRunIdRef.current += 1;
    promptTemplateControllerRef.current?.abort();
    promptTemplateControllerRef.current = null;

    const action = actionRef.current;
    if (action?.kind === "submit" && batchRef.current !== null) {
      stopBatchAfterCurrentRef.current = true;
    }
    if (action !== null && (action.kind !== "submit" && action.kind !== "retry"
      || !requestDispatchedRef.current)) {
      action.controller.abort();
    }
    update((current) => ({
      ...current,
      mode: null,
      modelsLoading: false,
      promptTemplatesLoading: false,
    }));
    notifyGuard();
  }, [notifyGuard, update]);

  const updateIntent = useCallback((intent: CanvasImageGenerationWorkspaceIntent) => {
    setIntent(intent);
    update((current) => ({ ...current, target: intent.target }));
  }, [setIntent, update]);

  const publishBatch = useCallback((batch: FrozenImageGenerationBatch) => {
    batchRef.current = batch;
    update((current) => ({ ...current, batch: batchView(batch) }));
  }, [update]);

  const stopBatch = useCallback((
    batch: FrozenImageGenerationBatch,
    outcome: "unknown" | "stopped",
    stoppedOrdinal: number,
    notSentCount: number,
    error: string | null,
    message: string | null,
    pendingIntent: CanvasImageGenerationWorkspaceIntent | null,
    keepForm = false,
  ) => {
    const nextBatch: FrozenImageGenerationBatch = {
      ...batch,
      outcome,
      activeOrdinal: null,
      stoppedOrdinal,
      notSentCount,
    };
    batchRef.current = nextBatch;
    intentRef.current = pendingIntent;
    update((current) => ({
      ...current,
      mode: current.mode === null
        ? null
        : pendingIntent !== null
          ? "intent"
          : keepForm && nextBatch.acceptedReceipts.length === 0 && current.mode === "form"
            ? "form"
            : "receipt",
      target: batch.target,
      intent: pendingIntent,
      receipt: nextBatch.acceptedReceipts[nextBatch.acceptedReceipts.length - 1] ?? null,
      batch: batchView(nextBatch),
      error,
      message,
    }));
    notifyGuard();
  }, [notifyGuard, update]);

  const recordBatchReceipt = useCallback((
    batch: FrozenImageGenerationBatch,
    intent: CanvasImageGenerationWorkspaceIntent,
    receipt: CanvasImageGenerationReceipt,
    complete: boolean,
  ) => {
    if (!intent.target.context.isCurrent()
      || intentRef.current?.input.idempotencyKey !== intent.input.idempotencyKey) return;
    const confirmed = receiptView(intent.target, intent.input, intent.modelName, receipt, intent.ordinal);
    const remainingCount = batch.requests.length - batch.acceptedReceipts.length - 1;
    const allComplete = complete && remainingCount === 0;
    const nextBatch: FrozenImageGenerationBatch = {
      ...batch,
      acceptedReceipts: Object.freeze([...batch.acceptedReceipts, confirmed]),
      notSentCount: allComplete ? 0 : remainingCount,
      activeOrdinal: null,
      stoppedOrdinal: allComplete ? null : intent.ordinal,
      outcome: allComplete ? "complete" : complete ? "stopped" : "submitting",
    };
    batchRef.current = nextBatch;
    intentRef.current = null;
    update((current) => ({
      ...current,
      mode: current.mode === null ? null : complete ? "receipt" : "form",
      target: intent.target,
      intent: null,
      receipt: confirmed,
      batch: batchView(nextBatch),
      error: null,
      message: complete
        ? allComplete
          ? "本轮图片任务受理记录已确认。画布仍未自动应用图片。"
          : "原图片任务已确认；本轮后续请求保持未发送。"
        : null,
      lockMessage: complete ? null : current.lockMessage,
      lockReady: complete ? false : current.lockReady,
      lockPending: complete ? false : current.lockPending,
    }));
    notifyGuard();
  }, [notifyGuard, update]);

  const loadModels = useCallback((target: CanvasImageGenerationTarget) => {
    modelRunIdRef.current += 1;
    const runId = modelRunIdRef.current;
    modelControllerRef.current?.abort();
    const controller = new AbortController();
    modelControllerRef.current = controller;
    update((current) => ({
      ...current,
      modelsLoading: true,
      models: [],
      selectedModelId: "",
      selectedSize: "",
      sizeSelectionInvalid: false,
      error: null,
    }));

    const settings = optionsRef.current.creatorSettings;
    if (settings === undefined) {
      update((current) => ({
        ...current,
        modelsLoading: false,
        error: "当前服务没有接入图片模型列表。",
      }));
      modelControllerRef.current = null;
      return;
    }

    void Promise.resolve().then(() => settings.listModels("image", controller.signal)).then((rows) => {
      if (controller.signal.aborted || runId !== modelRunIdRef.current || !target.context.isCurrent()) return;
      const seen = new Set<string>();
      const models = rows.filter((model) => {
        if (model.category !== "image" || model.id.trim() === "" || model.name.trim() === "" || seen.has(model.id)) return false;
        seen.add(model.id);
        return true;
      }).map((model) => ({
        id: model.id,
        name: model.name,
        ...imageSizeConfiguration(model.params),
      }));
      update((current) => current.target?.targetKey !== target.targetKey ? current : {
        ...current,
        models,
        selectedModelId: models[0]?.id ?? "",
        selectedSize: models[0]?.defaultSize ?? "",
        sizeSelectionInvalid: false,
        error: null,
      });
    }).catch(() => {
      if (controller.signal.aborted || runId !== modelRunIdRef.current || !target.context.isCurrent()) return;
      update((current) => current.target?.targetKey !== target.targetKey ? current : {
        ...current,
        models: [],
        selectedModelId: "",
        selectedSize: "",
        sizeSelectionInvalid: false,
        error: "图片模型暂时无法读取，请稍后重试。",
      });
    }).finally(() => {
      if (modelControllerRef.current === controller) modelControllerRef.current = null;
      if (!controller.signal.aborted && runId === modelRunIdRef.current && target.context.isCurrent()) {
        update((current) => current.target?.targetKey !== target.targetKey ? current : { ...current, modelsLoading: false });
      }
    });
  }, [update]);

  const loadPromptTemplates = useCallback((target: CanvasImageGenerationTarget) => {
    promptTemplateRunIdRef.current += 1;
    const runId = promptTemplateRunIdRef.current;
    promptTemplateControllerRef.current?.abort();
    const controller = new AbortController();
    promptTemplateControllerRef.current = controller;
    update((current) => ({
      ...current,
      promptTemplates: [],
      selectedPromptTemplateId: "",
      promptTemplateSelectionInvalid: false,
      promptTemplatesLoading: true,
      promptTemplateError: null,
    }));

    const settings = optionsRef.current.creatorSettings;
    if (settings === undefined) {
      update((current) => current.target?.targetKey !== target.targetKey || current.mode !== "form" ? current : {
        ...current,
        promptTemplatesLoading: false,
        promptTemplateError: "提示词模板暂时无法读取；仍可填写描述生成。",
      });
      promptTemplateControllerRef.current = null;
      return;
    }

    const serviceIdentity = optionsRef.current.serviceIdentity;
    const isCurrent = () => {
      try {
        const current = stateRef.current;
        return !controller.signal.aborted
          && runId === promptTemplateRunIdRef.current
          && target.context.isCurrent()
          && optionsRef.current.serviceIdentity === serviceIdentity
          && optionsRef.current.creatorSettings === settings
          && optionsRef.current.selectedTargetKey === target.targetKey
          && current.mode === "form"
          && current.target?.targetKey === target.targetKey;
      } catch {
        return false;
      }
    };

    void Promise.resolve().then(() => {
      if (!isCurrent()) return null;
      return settings.listPrompts(undefined, controller.signal);
    }).then((rows) => {
      if (rows === null || !isCurrent()) return;
      const promptTemplates = projectImagePromptTemplates(rows);
      update((current) => current.target?.targetKey !== target.targetKey || current.mode !== "form" ? current : {
        ...current,
        promptTemplates,
        selectedPromptTemplateId: "",
        promptTemplateSelectionInvalid: false,
        promptTemplateError: null,
      });
    }).catch(() => {
      if (!isCurrent()) return;
      update((current) => current.target?.targetKey !== target.targetKey || current.mode !== "form" ? current : {
        ...current,
        promptTemplates: [],
        selectedPromptTemplateId: "",
        promptTemplateSelectionInvalid: false,
        promptTemplateError: "提示词模板暂时无法读取；仍可填写描述生成。",
      });
    }).finally(() => {
      if (promptTemplateControllerRef.current === controller) promptTemplateControllerRef.current = null;
      if (isCurrent()) {
        update((current) => current.target?.targetKey !== target.targetKey || current.mode !== "form" ? current : {
          ...current,
          promptTemplatesLoading: false,
        });
      }
    });
  }, [update]);

  const openNew = useCallback((target: CanvasImageGenerationTarget): boolean => {
    if (busyRef.current || intentRef.current !== null
      || target.targetKey !== optionsRef.current.selectedTargetKey
      || !target.context.isCurrent()) return false;
    update((current) => ({
      ...current,
      mode: "form",
      target,
      models: [],
      selectedModelId: "",
      selectedCount: 1,
      countSelectionInvalid: false,
      selectedSize: "",
      sizeSelectionInvalid: false,
      promptTemplates: [],
      selectedPromptTemplateId: "",
      promptTemplateSelectionInvalid: false,
      prompt: target.promptSeed,
      modelsLoading: true,
      promptTemplatesLoading: true,
      promptTemplateError: null,
      error: null,
      message: null,
      lockMessage: null,
      lockReady: false,
      lockPending: false,
      intent: null,
      receipt: null,
      batch: null,
    }));
    batchRef.current = null;
    stopBatchAfterCurrentRef.current = false;
    notifyGuard();
    loadModels(target);
    loadPromptTemplates(target);
    return true;
  }, [loadModels, loadPromptTemplates, notifyGuard, update]);

  const openPendingIntent = useCallback((): boolean => {
    const intent = intentRef.current;
    if (intent === null || busyRef.current || !intent.target.context.isCurrent()) return false;
    update((current) => ({
      ...current,
      mode: "intent",
      target: intent.target,
      error: null,
      message: null,
      lockMessage: null,
      lockReady: false,
      lockPending: false,
    }));
    return true;
  }, [update]);

  const openReceipt = useCallback((): boolean => {
    const receipt = stateRef.current.receipt;
    const batch = batchRef.current;
    const target = receipt?.target ?? batch?.target ?? null;
    if (intentRef.current !== null || target === null || busyRef.current || !target.context.isCurrent()) return false;
    update((current) => ({ ...current, mode: "receipt", target, error: null }));
    notifyGuard();
    return true;
  }, [notifyGuard, update]);

  const setSelectedModelId = useCallback((modelId: string) => {
    const current = stateRef.current;
    const model = current.models.find((option) => option.id === modelId);
    if (current.busy || model === undefined) return;
    update((latest) => ({
      ...latest,
      selectedModelId: modelId,
      selectedSize: model.defaultSize ?? "",
      sizeSelectionInvalid: false,
      error: null,
    }));
  }, [update]);

  const setSelectedCount = useCallback((value: string) => {
    const count = Number(value);
    const valid = Number.isInteger(count) && count >= 1 && count <= 4;
    if (stateRef.current.mode !== "form" || busyRef.current) return;
    update((current) => ({
      ...current,
      selectedCount: count,
      countSelectionInvalid: !valid,
      error: null,
    }));
  }, [update]);

  const setSelectedSize = useCallback((size: string) => {
    const current = stateRef.current;
    const model = current.models.find((option) => option.id === current.selectedModelId);
    if (current.mode !== "form" || current.busy || model === undefined || model.sizeOptions.length === 0) return;
    if (!model.sizeOptions.includes(size)) {
      update((latest) => ({ ...latest, selectedSize: size, sizeSelectionInvalid: true }));
      return;
    }
    update((latest) => ({ ...latest, selectedSize: size, sizeSelectionInvalid: false, error: null }));
  }, [update]);

  const setSelectedPromptTemplateId = useCallback((promptTemplateId: string) => {
    const current = stateRef.current;
    if (current.mode !== "form" || current.busy) return;
    if (promptTemplateId === "") {
      update((latest) => ({
        ...latest,
        selectedPromptTemplateId: "",
        promptTemplateSelectionInvalid: false,
        promptTemplateError: null,
      }));
      return;
    }
    const matches = current.promptTemplates.filter((template) => template.id === promptTemplateId);
    if (matches.length !== 1) {
      update((latest) => ({
        ...latest,
        selectedPromptTemplateId: promptTemplateId,
        promptTemplateSelectionInvalid: true,
        promptTemplateError: "所选提示词模板无法唯一确认，请重新选择模板或选择“无（纯画面描述）”。",
      }));
      return;
    }
    update((latest) => ({
      ...latest,
      selectedPromptTemplateId: promptTemplateId,
      promptTemplateSelectionInvalid: false,
      promptTemplateError: null,
    }));
  }, [update]);

  const setPrompt = useCallback((prompt: string) => {
    if (busyRef.current) return;
    update((current) => ({ ...current, prompt, error: null }));
  }, [update]);

  const createRun = useCallback((
    intent: CanvasImageGenerationWorkspaceIntent,
    batch: FrozenImageGenerationBatch | null = batchRef.current,
  ) => {
    const currentOptions = optionsRef.current;
    if (batch !== null && (currentOptions.serviceIdentity !== batch.serviceIdentity
      || currentOptions.port !== batch.port)) {
      throw new CanvasImageGenerationError("not-sent", "图片服务已变化，没有发送新的任务。", null);
    }
    const port = batch?.port ?? currentOptions.port;
    if (port === undefined) throw new CanvasImageGenerationError("not-sent", "当前服务没有接入图片任务提交。", null);
    const context = receiptRunContext(intent);
    const run = port.beginRun(context);
    if (!run.isCurrent() || !context.isContextCurrent()) {
      throw new CanvasImageGenerationError("not-sent", "登录、章节或任务上下文已变化，没有发送请求。", null);
    }
    return run;
  }, []);

  const isBatchContextCurrent = useCallback((batch: FrozenImageGenerationBatch, action: ActionHandle): boolean => {
    const currentOptions = optionsRef.current;
    return !action.controller.signal.aborted
      && batch.target.context.isCurrent()
      && currentOptions.serviceIdentity === batch.serviceIdentity
      && currentOptions.port === batch.port;
  }, []);

  const isBatchSnapshotCurrent = useCallback((batch: FrozenImageGenerationBatch): boolean => {
    const currentOptions = optionsRef.current;
    return batchRef.current?.requests === batch.requests
      && batch.target.context.isCurrent()
      && currentOptions.serviceIdentity === batch.serviceIdentity
      && currentOptions.port === batch.port;
  }, []);

  const submitNew = useCallback(async () => {
    const current = stateRef.current;
    const target = current.target;
    const model = current.models.find((option) => option.id === current.selectedModelId);
    if (current.mode !== "form" || target === null || model === undefined
      || current.modelsLoading || busyRef.current || intentRef.current !== null) return;
    if (!Number.isInteger(current.selectedCount) || current.selectedCount < 1 || current.selectedCount > 4
      || current.countSelectionInvalid) {
      update((latest) => ({ ...latest, countSelectionInvalid: true, error: "请选择1至4张，未发送图片任务。" }));
      return;
    }
    if (model.sizeConfigurationError !== null) return;
    const size = model.sizeOptions.length > 0 ? current.selectedSize : undefined;
    if (model.sizeOptions.length > 0
      && (current.sizeSelectionInvalid || !model.sizeOptions.includes(current.selectedSize)
        || !isValidCanvasImageGenerationSize(current.selectedSize))) {
      update((latest) => ({ ...latest, sizeSelectionInvalid: true }));
      return;
    }
    if (current.promptTemplateSelectionInvalid) {
      update((latest) => ({
        ...latest,
        promptTemplateError: "请重新选择有效提示词模板或选择“无（纯画面描述）”，未发送图片任务。",
      }));
      return;
    }

    let selectedTemplate: CanvasImageGenerationPromptTemplateOption | null = null;
    if (current.selectedPromptTemplateId !== "") {
      const matches = current.promptTemplates.filter((template) => template.id === current.selectedPromptTemplateId);
      if (matches.length !== 1) {
        update((latest) => ({
          ...latest,
          selectedPromptTemplateId: current.selectedPromptTemplateId,
          promptTemplateSelectionInvalid: true,
          promptTemplateError: "所选提示词模板无法唯一确认，请重新选择模板或选择“无（纯画面描述）”，未发送图片任务。",
        }));
        return;
      }
      const match = matches[0];
      if (match === undefined) return;
      selectedTemplate = Object.freeze({ ...match });
    }
    const prompt = composeImagePrompt(current.prompt, target.promptSeed, selectedTemplate);
    const modelId = model.id;
    const modelName = model.name;
    const serviceIdentity = optionsRef.current.serviceIdentity;
    const port = optionsRef.current.port;
    if (port === undefined) {
      update((latest) => ({ ...latest, error: "当前服务没有接入图片任务提交。" }));
      return;
    }
    const destination = Object.freeze({ ...target.destination });
    const idempotencyKeys = new Set<string>();
    const requests: FrozenImageGenerationRequest[] = [];
    for (let ordinal = 1; ordinal <= current.selectedCount; ordinal += 1) {
      const idempotencyKey = globalThis.crypto?.randomUUID?.();
      if (idempotencyKey === undefined || idempotencyKeys.has(idempotencyKey)) {
        update((latest) => ({ ...latest, error: "当前环境无法为每张图片生成独立安全编号，未发送任务。" }));
        return;
      }
      idempotencyKeys.add(idempotencyKey);
      const input: CanvasImageGenerationSubmitInput = Object.freeze({
        modelId,
        prompt,
        destination,
        ...(size === undefined ? {} : { size }),
        idempotencyKey,
      });
      requests.push(Object.freeze({ ordinal, input }));
    }

    const action = beginAction("submit");
    if (action === null) return;
    stopBatchAfterCurrentRef.current = false;
    let batch: FrozenImageGenerationBatch = {
      target,
      modelName,
      serviceIdentity,
      port,
      requests: Object.freeze(requests),
      acceptedReceipts: Object.freeze([]),
      notSentCount: requests.length,
      activeOrdinal: null,
      stoppedOrdinal: null,
      outcome: "submitting",
    };
    publishBatch(batch);
    update((latest) => ({ ...latest, receipt: null, message: null, error: null }));
    try {
      for (const request of batch.requests) {
        if (stopBatchAfterCurrentRef.current) {
          stopBatch(batch, "stopped", request.ordinal, batch.requests.length - batch.acceptedReceipts.length,
            null, "本轮已停止派发后续请求。客户端停止派发不代表服务端取消或退款。", null);
          return;
        }
        requestDispatchedRef.current = false;
        batch = {
          ...batch,
          activeOrdinal: request.ordinal,
          notSentCount: batch.requests.length - batch.acceptedReceipts.length,
          stoppedOrdinal: null,
          outcome: "submitting",
        };
        publishBatch(batch);
        const optionsBeforeValidation = optionsRef.current;
        const targetIsValid = isBatchContextCurrent(batch, action)
          && optionsBeforeValidation.selectedTargetKey === target.targetKey
          && await optionsBeforeValidation.validateTarget(target, action.controller.signal, true);
        if (!targetIsValid || !isBatchContextCurrent(batch, action)
          || optionsRef.current.selectedTargetKey !== target.targetKey) {
          if (!isBatchSnapshotCurrent(batch)) return;
          stopBatch(batch, "stopped", request.ordinal, batch.requests.length - batch.acceptedReceipts.length,
            "当前分镜或素材版本已变化，未发送本张图片任务。", "本轮已停止，未发送的后续请求不会自动补发。", null);
          return;
        }
        const lock = await optionsRef.current.checkEditLock(target, action.controller.signal);
        if (!isBatchContextCurrent(batch, action)) {
          if (isBatchSnapshotCurrent(batch)) {
            stopBatch(batch, "stopped", request.ordinal, batch.requests.length - batch.acceptedReceipts.length,
              null, "表单已关闭或状态已变化，本轮剩余请求未发送。", null);
          }
          return;
        }
        update((latest) => ({ ...latest, lockReady: lock.status === "ready", lockMessage: lock.message }));
        if (lock.status !== "ready") {
          stopBatch(batch, "stopped", request.ordinal, batch.requests.length - batch.acceptedReceipts.length,
            null, lock.message || "当前编辑锁未通过，本轮后续请求未发送。", null, true);
          return;
        }
        if (stopBatchAfterCurrentRef.current
          || optionsRef.current.selectedTargetKey !== target.targetKey
          || !isBatchContextCurrent(batch, action)) {
          if (!isBatchSnapshotCurrent(batch)) return;
          stopBatch(batch, "stopped", request.ordinal, batch.requests.length - batch.acceptedReceipts.length,
            null, "目标或表单状态已变化，本轮后续请求未发送。", null);
          return;
        }

        const activeBatch: FrozenImageGenerationBatch = {
          ...batch,
          notSentCount: batch.requests.length - batch.acceptedReceipts.length - 1,
          outcome: "submitting",
        };
        batch = activeBatch;
        publishBatch(batch);
        const intent = intentView(target, request.input, modelName, "unknown", batchView(batch), request.ordinal);
        updateIntent(intent);
        const run = createRun(intent, batch);
        if (!isBatchContextCurrent(batch, action)) return;
        requestDispatchedRef.current = true;
        const receipt = await run.submit(request.input, action.controller.signal);
        if (!isBatchContextCurrent(batch, action)) return;

        const isLastRequest = request.ordinal === batch.requests.length;
        recordBatchReceipt(batch, intent, receipt, isLastRequest);
        const updatedBatch = batchRef.current;
        if (updatedBatch === null) return;
        batch = updatedBatch;
        requestDispatchedRef.current = false;
        if (isLastRequest) return;
        if (stopBatchAfterCurrentRef.current || optionsRef.current.selectedTargetKey !== target.targetKey) {
          stopBatch(batch, "stopped", request.ordinal + 1, batch.requests.length - batch.acceptedReceipts.length,
            null, "本轮已停止派发后续请求。客户端停止派发不代表服务端取消或退款。", null);
          return;
        }
      }
    } catch (cause) {
      const activeBatch = batchRef.current;
      if (activeBatch === null || activeBatch.requests !== batch.requests || !isBatchSnapshotCurrent(activeBatch)) return;
      const kind = errorKind(cause);
      const sent = requestDispatchedRef.current;
      const activeRequest = activeBatch.requests.find((request) => request.ordinal === activeBatch.activeOrdinal);
      if (activeRequest === undefined) return;
      if (!sent || kind === "not-sent" || kind === "rejected") {
        const wasSentAndRejected = sent && kind === "rejected";
        stopBatch(activeBatch, "stopped", activeRequest.ordinal,
          activeBatch.requests.length - activeBatch.acceptedReceipts.length - (wasSentAndRejected ? 1 : 0),
          errorMessage(cause), wasSentAndRejected
            ? `第 ${activeRequest.ordinal} 张未被服务接受；未发送的后续请求不会自动补发。`
            : `第 ${activeRequest.ordinal} 张未发送；本轮后续请求未发送。`, null,
          activeBatch.acceptedReceipts.length === 0);
      } else {
        const existing = readCurrentIntent();
        if (existing === null || existing.input.idempotencyKey !== activeRequest.input.idempotencyKey) return;
        const unknownBatch: FrozenImageGenerationBatch = {
          ...activeBatch,
          activeOrdinal: null,
          stoppedOrdinal: activeRequest.ordinal,
          notSentCount: activeBatch.requests.length - activeBatch.acceptedReceipts.length - 1,
          outcome: "unknown",
        };
        const unknownIntent = { ...existing, batch: batchView(unknownBatch), state: "unknown" as const };
        stopBatch(unknownBatch, "unknown", activeRequest.ordinal, unknownBatch.notSentCount,
          null, `第 ${activeRequest.ordinal} 张结果暂时无法确认；后续请求未发送。请先核对这张原任务。`, unknownIntent);
      }
    } finally {
      finishAction(action, target.context.isCurrent);
    }
  }, [beginAction, createRun, finishAction, isBatchContextCurrent, isBatchSnapshotCurrent, publishBatch, readCurrentIntent, recordBatchReceipt, stopBatch, update, updateIntent]);

  const lookupPendingIntent = useCallback(async () => {
    const intent = intentRef.current;
    if (intent === null || busyRef.current || !intent.target.context.isCurrent()) return;
    const batch = batchRef.current;
    if (batch === null) return;
    const port = optionsRef.current.port;
    if (port === undefined) {
      update((current) => ({ ...current, error: "当前服务没有接入原任务核对。" }));
      return;
    }
    const action = beginAction("lookup");
    if (action === null) return;
    try {
      const run = createRun(intent, batch);
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()) return;
      const receipt = await run.lookup(intent.input.idempotencyKey, action.controller.signal);
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()
        || intentRef.current?.input.idempotencyKey !== intent.input.idempotencyKey) return;
      recordBatchReceipt(batch, intent, receipt, true);
    } catch (cause) {
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()
        || intentRef.current?.input.idempotencyKey !== intent.input.idempotencyKey) return;
      if (errorKind(cause) === "not-found") {
        updateIntent({ ...intent, state: "not-found" });
        update((current) => ({
          ...current,
          mode: "intent",
          error: null,
          message: null,
        }));
      } else {
        update((current) => ({ ...current, mode: "intent", error: errorMessage(cause), message: null }));
      }
    } finally {
      finishAction(action, intent.target.context.isCurrent);
    }
  }, [beginAction, createRun, finishAction, recordBatchReceipt, update, updateIntent]);

  const retryOriginalIntent = useCallback(async () => {
    const intent = intentRef.current;
    if (intent === null || intent.state !== "not-found" || busyRef.current
      || !intent.target.context.isCurrent()) return;
    const batch = batchRef.current;
    if (batch === null) return;
    const action = beginAction("retry");
    if (action === null) return;
    try {
      if (!await optionsRef.current.validateTarget(intent.target, action.controller.signal, false)) {
        if (!action.controller.signal.aborted && intent.target.context.isCurrent()) {
          update((current) => ({
            ...current,
            error: "原分镜或素材版本已变化。为避免把原任务改绑到新内容，暂不重新提交。",
          }));
        }
        return;
      }
      const lock = await optionsRef.current.checkEditLock(intent.target, action.controller.signal);
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()) return;
      update((current) => ({ ...current, lockReady: lock.status === "ready", lockMessage: lock.message }));
      if (lock.status !== "ready") return;

      const run = createRun(intent, batch);
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()) return;
      updateIntent({ ...intent, state: "unknown" });
      requestDispatchedRef.current = true;
      const receipt = await run.submit(intent.input, action.controller.signal);
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()) return;
      recordBatchReceipt(batch, intent, receipt, true);
    } catch (cause) {
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()
        || intentRef.current?.input.idempotencyKey !== intent.input.idempotencyKey) return;
      const kind = errorKind(cause);
      if (!requestDispatchedRef.current && kind === "not-sent") {
        updateIntent({ ...intent, state: "not-found" });
        update((current) => ({
          ...current,
          mode: current.mode === null ? null : "intent",
          error: errorMessage(cause),
          message: null,
        }));
      } else {
        updateIntent({ ...intent, state: "unknown" });
        update((current) => ({
          ...current,
          mode: current.mode === null ? null : "intent",
          error: kind === "rejected" ? errorMessage(cause) : null,
          message: kind === "rejected"
            ? "本次重试已被拒绝；此前请求仍可能稍后完成，请继续核对原请求编号。"
            : "重新提交结果暂时无法确认。原请求编号和内容仍保留，请核对后再决定下一步。",
        }));
      }
    } finally {
      finishAction(action, intent.target.context.isCurrent);
    }
  }, [beginAction, createRun, finishAction, recordBatchReceipt, setIntent, update, updateIntent]);

  const acquireLock = useCallback(async () => {
    const current = stateRef.current;
    const target = current.mode === "intent" ? intentRef.current?.target ?? null : current.target;
    if (target === null || current.busy || !target.requiresLock || !target.context.isCurrent()) return;
    const action = beginAction("lock");
    if (action === null) return;
    update((latest) => ({ ...latest, lockPending: true, lockMessage: null, error: null }));
    try {
      const result = await optionsRef.current.acquireEditLock(target, action.controller.signal);
      if (action.controller.signal.aborted || !target.context.isCurrent()) return;
      update((latest) => ({ ...latest, lockReady: result.status === "ready", lockMessage: result.message }));
    } catch (cause) {
      if (action.controller.signal.aborted || !target.context.isCurrent()) return;
      update((latest) => ({ ...latest, lockReady: false, error: errorMessage(cause) }));
    } finally {
      if (actionRef.current === action && target.context.isCurrent()) {
        update((latest) => ({ ...latest, lockPending: false }));
        finishAction(action, target.context.isCurrent);
      }
    }
  }, [beginAction, finishAction, update]);

  const startNewFromReceipt = useCallback((target: CanvasImageGenerationTarget): boolean => openNew(target), [openNew]);

  useEffect(() => {
    modelRunIdRef.current += 1;
    modelControllerRef.current?.abort();
    modelControllerRef.current = null;
    promptTemplateRunIdRef.current += 1;
    promptTemplateControllerRef.current?.abort();
    promptTemplateControllerRef.current = null;
    actionRef.current?.controller.abort();
    actionRef.current = null;
    requestDispatchedRef.current = false;
    batchRef.current = null;
    stopBatchAfterCurrentRef.current = false;
    intentRef.current = null;
    busyRef.current = false;
    stateRef.current = EMPTY_STATE;
    setState(EMPTY_STATE);
    optionsRef.current.onGuardChange(false, false, false);
    return () => {
      modelRunIdRef.current += 1;
      modelControllerRef.current?.abort();
      promptTemplateRunIdRef.current += 1;
      promptTemplateControllerRef.current?.abort();
      actionRef.current?.controller.abort();
    };
  }, [options.contextKey, options.serviceIdentity]);

  useEffect(() => {
    const current = stateRef.current;
    if ((current.mode === "form" || current.mode === "receipt")
      && current.target !== null && current.target.targetKey !== options.selectedTargetKey) {
      close();
    }
  }, [close, options.selectedTargetKey]);

  return {
    state,
    intent: state.intent,
    receipt: state.receipt,
    busy: state.busy,
    blocked: state.busy || state.intent !== null || state.mode !== null,
    openNew,
    openPendingIntent,
    openReceipt,
    close,
    setSelectedModelId,
    setSelectedCount,
    setSelectedSize,
    setSelectedPromptTemplateId,
    setPrompt,
    submitNew,
    lookupPendingIntent,
    retryOriginalIntent,
    acquireLock,
    startNewFromReceipt,
  };
}
