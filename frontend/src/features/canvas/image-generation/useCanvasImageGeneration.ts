import { useCallback, useEffect, useRef, useState } from "react";
import {
  CanvasImageGenerationError,
  type CanvasImageGenerationPort,
  type CanvasImageGenerationReceipt,
  type CanvasImageGenerationSubmitInput,
} from "../../../shared/api/canvasImageGeneration";
import type { CanvasOwnedResultsTarget } from "../../../shared/api/canvasOwnedResults";
import type { CreatorPrompt, CreatorSettingsServicePort } from "../../../shared/api/creatorSettings";
import type {
  CanvasImageGenerationIntentView,
  CanvasImageGenerationModelOption,
  CanvasImageGenerationPromptTemplateOption,
  CanvasImageGenerationReceiptView,
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

export interface CanvasImageGenerationWorkspaceReceipt extends CanvasImageGenerationReceiptView {
  readonly target: CanvasImageGenerationTarget;
}

export interface CanvasImageGenerationControllerState {
  readonly mode: "form" | "intent" | "receipt" | null;
  readonly target: CanvasImageGenerationTarget | null;
  readonly models: readonly CanvasImageGenerationModelOption[];
  readonly selectedModelId: string;
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

const EMPTY_STATE: CanvasImageGenerationControllerState = {
  mode: null,
  target: null,
  models: [],
  selectedModelId: "",
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
): CanvasImageGenerationWorkspaceReceipt {
  return {
    target,
    destination: input.destination,
    modelId: input.modelId,
    modelName,
    prompt: input.prompt,
    receipt,
    targetLabel: target.targetLabel,
  };
}

function intentView(
  target: CanvasImageGenerationTarget,
  input: CanvasImageGenerationSubmitInput,
  modelName: string,
  state: CanvasImageGenerationWorkspaceIntent["state"],
): CanvasImageGenerationWorkspaceIntent {
  return {
    target,
    destination: input.destination,
    idempotencyKey: input.idempotencyKey,
    modelId: input.modelId,
    modelName,
    prompt: input.prompt,
    targetLabel: target.targetLabel,
    input,
    state,
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
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

  const completeWithReceipt = useCallback((intent: CanvasImageGenerationWorkspaceIntent, receipt: CanvasImageGenerationReceipt) => {
    if (!intent.target.context.isCurrent()
      || intentRef.current?.input.idempotencyKey !== intent.input.idempotencyKey) return;
    const confirmed = receiptView(intent.target, intent.input, intent.modelName, receipt);
    intentRef.current = null;
    update((current) => ({
      ...current,
      mode: current.mode === null ? null : "receipt",
      target: intent.target,
      intent: null,
      receipt: confirmed,
      error: null,
      message: "任务受理记录已确认。画布仍未自动应用图片。",
      lockMessage: null,
      lockReady: false,
      lockPending: false,
    }));
    notifyGuard();
  }, [notifyGuard, update]);

  const loadModels = useCallback((target: CanvasImageGenerationTarget) => {
    modelRunIdRef.current += 1;
    const runId = modelRunIdRef.current;
    modelControllerRef.current?.abort();
    const controller = new AbortController();
    modelControllerRef.current = controller;
    update((current) => ({ ...current, modelsLoading: true, models: [], selectedModelId: "", error: null }));

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
      }).map((model) => ({ id: model.id, name: model.name }));
      update((current) => current.target?.targetKey !== target.targetKey ? current : {
        ...current,
        models,
        selectedModelId: models[0]?.id ?? "",
        error: null,
      });
    }).catch(() => {
      if (controller.signal.aborted || runId !== modelRunIdRef.current || !target.context.isCurrent()) return;
      update((current) => current.target?.targetKey !== target.targetKey ? current : {
        ...current,
        models: [],
        selectedModelId: "",
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
    }));
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
    if (receipt === null || busyRef.current || !receipt.target.context.isCurrent()) return false;
    update((current) => ({ ...current, mode: "receipt", target: receipt.target, error: null }));
    notifyGuard();
    return true;
  }, [notifyGuard, update]);

  const setSelectedModelId = useCallback((modelId: string) => {
    const current = stateRef.current;
    if (current.busy || !current.models.some((model) => model.id === modelId)) return;
    update((latest) => ({ ...latest, selectedModelId: modelId, error: null }));
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

  const createRun = useCallback((intent: CanvasImageGenerationWorkspaceIntent) => {
    const port = optionsRef.current.port;
    if (port === undefined) throw new CanvasImageGenerationError("not-sent", "当前服务没有接入图片任务提交。", null);
    const context = receiptRunContext(intent);
    const run = port.beginRun(context);
    if (!run.isCurrent() || !context.isContextCurrent()) {
      throw new CanvasImageGenerationError("not-sent", "登录、章节或任务上下文已变化，没有发送请求。", null);
    }
    return run;
  }, []);

  const finishSubmission = useCallback(async (
    intent: CanvasImageGenerationWorkspaceIntent,
    action: ActionHandle,
  ) => {
    const run = createRun(intent);
    if (action.controller.signal.aborted || !intent.target.context.isCurrent()) return;
    requestDispatchedRef.current = true;
    const receipt = await run.submit(intent.input, action.controller.signal);
    if (action.controller.signal.aborted || !intent.target.context.isCurrent()) return;
    completeWithReceipt(intent, receipt);
  }, [completeWithReceipt, createRun]);

  const submitNew = useCallback(async () => {
    const current = stateRef.current;
    const target = current.target;
    const model = current.models.find((option) => option.id === current.selectedModelId);
    if (current.mode !== "form" || target === null || model === undefined
      || current.modelsLoading || busyRef.current || intentRef.current !== null) return;
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

    const action = beginAction("submit");
    if (action === null) return;
    try {
      if (!target.context.isCurrent() || !await optionsRef.current.validateTarget(target, action.controller.signal, true)) {
        if (!action.controller.signal.aborted && target.context.isCurrent()) {
          update((latest) => ({ ...latest, error: "当前分镜或素材版本已变化，未提交图片任务。请重新选择目标。" }));
        }
        return;
      }
      const lock = await optionsRef.current.checkEditLock(target, action.controller.signal);
      if (action.controller.signal.aborted || !target.context.isCurrent()) return;
      update((latest) => ({ ...latest, lockReady: lock.status === "ready", lockMessage: lock.message }));
      if (lock.status !== "ready") return;

      const idempotencyKey = globalThis.crypto?.randomUUID?.();
      if (idempotencyKey === undefined) {
        update((latest) => ({ ...latest, error: "当前环境无法生成安全的请求编号，未发送任务。" }));
        return;
      }
      const input: CanvasImageGenerationSubmitInput = Object.freeze({
        modelId,
        prompt,
        destination: target.destination,
        idempotencyKey,
      });
      const intent = intentView(target, input, modelName, "unknown");
      updateIntent(intent);
      update((latest) => ({ ...latest, error: null, message: null, mode: "form" }));
      await finishSubmission(intent, action);
    } catch (cause) {
      if (!target.context.isCurrent()) return;
      const kind = errorKind(cause);
      const sent = requestDispatchedRef.current;
      if (!sent || kind === "not-sent") {
        if (readCurrentIntent()?.target === target) setIntent(null);
        update((latest) => ({ ...latest, error: errorMessage(cause), message: null }));
      } else if (kind === "rejected") {
        setIntent(null);
        update((latest) => ({
          ...latest,
          mode: latest.mode === null ? null : "form",
          error: errorMessage(cause),
          message: null,
        }));
      } else {
        const existing = readCurrentIntent();
        if (existing !== null) updateIntent({ ...existing, state: "unknown" });
        update((latest) => ({
          ...latest,
          mode: latest.mode === null ? null : "intent",
          error: null,
          message: "上次提交结果暂时无法确认。原请求已保留，请明确核对后再决定下一步。",
        }));
      }
    } finally {
      finishAction(action, target.context.isCurrent);
    }
  }, [beginAction, finishAction, finishSubmission, readCurrentIntent, setIntent, update, updateIntent]);

  const lookupPendingIntent = useCallback(async () => {
    const intent = intentRef.current;
    if (intent === null || busyRef.current || !intent.target.context.isCurrent()) return;
    const port = optionsRef.current.port;
    if (port === undefined) {
      update((current) => ({ ...current, error: "当前服务没有接入原任务核对。" }));
      return;
    }
    const action = beginAction("lookup");
    if (action === null) return;
    try {
      const run = createRun(intent);
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()) return;
      const receipt = await run.lookup(intent.input.idempotencyKey, action.controller.signal);
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()
        || intentRef.current?.input.idempotencyKey !== intent.input.idempotencyKey) return;
      completeWithReceipt(intent, receipt);
    } catch (cause) {
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()
        || intentRef.current?.input.idempotencyKey !== intent.input.idempotencyKey) return;
      if (errorKind(cause) === "not-found") {
        updateIntent({ ...intent, state: "not-found" });
        update((current) => ({
          ...current,
          mode: "intent",
          error: null,
          message: "暂未查到任务记录。之前的请求仍可能稍后完成；原提交内容已保留。",
        }));
      } else {
        update((current) => ({ ...current, mode: "intent", error: errorMessage(cause), message: null }));
      }
    } finally {
      finishAction(action, intent.target.context.isCurrent);
    }
  }, [beginAction, completeWithReceipt, createRun, finishAction, update, updateIntent]);

  const retryOriginalIntent = useCallback(async () => {
    const intent = intentRef.current;
    if (intent === null || intent.state !== "not-found" || busyRef.current
      || !intent.target.context.isCurrent()) return;
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

      const run = createRun(intent);
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()) return;
      updateIntent({ ...intent, state: "unknown" });
      requestDispatchedRef.current = true;
      const receipt = await run.submit(intent.input, action.controller.signal);
      if (action.controller.signal.aborted || !intent.target.context.isCurrent()) return;
      completeWithReceipt(intent, receipt);
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
  }, [beginAction, completeWithReceipt, createRun, finishAction, setIntent, update, updateIntent]);

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
    setSelectedPromptTemplateId,
    setPrompt,
    submitNew,
    lookupPendingIntent,
    retryOriginalIntent,
    acquireLock,
    startNewFromReceipt,
  };
}
