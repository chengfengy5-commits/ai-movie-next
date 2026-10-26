import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset } from "../../shared/api/contracts";
import type { CreatorPrompt, CreatorSettingsServicePort } from "../../shared/api/creatorSettings";
import {
  encodeChatContent,
  parseChatContent,
  type ChatAssistantAssetType,
  type ChatAssistantMessage,
  type ChatAssistantRun,
  type ChatAssistantStreamMessage,
  type ChatAssistantTarget,
} from "../../shared/api/chatAssistant";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import type { StoryboardModelChoice } from "../storyboard-generation/contracts";
import "./chatAssistant.css";

const PENDING_LOCATOR_PREFIX = "haoai.chat-assistant.pending.v1";
const PLACEHOLDER_CONTENT = "...";
const MAX_CONTEXT_MESSAGES = 40;
const MAX_RECOVERY_ATTEMPTS = 20;
const RECOVERY_INTERVAL_MS = 3_000;

type MessageTaskState = "unknown" | "processing" | "completed" | "failed";

interface PendingLocator {
  readonly actorId: string;
  readonly apiBaseUrl: string;
  readonly seriesId: string;
  readonly target: ChatAssistantTarget;
  readonly assistantMessageId: string;
  readonly taskId: string | null;
}

interface PendingOperation extends PendingLocator {
  readonly phase: "receiving" | "recovering" | "unknown";
  readonly detail: string;
  readonly attempt: number;
}

interface HistorySnapshot {
  readonly scopeKey: string;
  readonly messages: ChatAssistantMessage[];
  readonly taskStates: ReadonlyMap<string, MessageTaskState>;
}

interface ChatAssistantPageProps {
  readonly services: WorkspaceServices;
  readonly series: Series;
  readonly userId: string;
  readonly sessionGeneration: number;
  onUnauthorized(): void;
  onBack(): void;
  onNavigateSource(target: ChatAssistantSourceIntent): void;
}

export type ChatAssistantSourceIntent =
  | {
      readonly kind: "chapter";
      readonly seriesId: string;
      readonly chapterId: string;
      isCurrent(): boolean;
    }
  | {
      readonly kind: "asset";
      readonly seriesId: string;
      readonly category: "characters" | "scenes" | "props";
      readonly assetId: string;
      isCurrent(): boolean;
    };

interface AssetChoice {
  readonly type: ChatAssistantAssetType;
  readonly id: string;
  readonly name: string;
}

function chatSettings(services: WorkspaceServices): CreatorSettingsServicePort | undefined {
  return services.creatorSettings;
}

function makeTargetKey(target: ChatAssistantTarget): string {
  if (target.kind === "chapter") return "chapter";
  if (target.kind === "frame") return `frame:${target.frameIndex}`;
  return `asset:${target.assetType}:${target.assetId}`;
}

function parseTargetKey(value: string, chapterId: string): ChatAssistantTarget | null {
  if (chapterId.trim() === "") return null;
  if (value === "chapter") return { kind: "chapter", chapterId };
  const frame = value.match(/^frame:(0|[1-9]\d*)$/u);
  if (frame !== null) {
    const frameIndex = Number(frame[1]);
    return Number.isSafeInteger(frameIndex) ? { kind: "frame", chapterId, frameIndex } : null;
  }
  const asset = value.match(/^asset:(character|scene|prop|storyboard):(.+)$/u);
  if (asset !== null) {
    return {
      kind: "asset",
      chapterId,
      assetType: asset[1] as ChatAssistantAssetType,
      assetId: asset[2]!,
    };
  }
  return null;
}

function targetScopeKey(target: ChatAssistantTarget | null): string {
  return target === null ? "" : `${target.chapterId}:${makeTargetKey(target)}`;
}

function locatorKey(userId: string, seriesId: string): string {
  return `${PENDING_LOCATOR_PREFIX}:${encodeURIComponent(userId)}:${encodeURIComponent(seriesId)}`;
}

function readPendingLocator(userId: string, apiBaseUrl: string | null, seriesId: string): PendingLocator | null {
  if (apiBaseUrl === null || typeof window === "undefined") return null;
  try {
    const serialized = window.sessionStorage.getItem(locatorKey(userId, seriesId));
    if (serialized === null) return null;
    const value: unknown = JSON.parse(serialized);
    if (!isRecord(value)
      || value.actorId !== userId
      || value.apiBaseUrl !== apiBaseUrl
      || value.seriesId !== seriesId
      || typeof value.assistantMessageId !== "string"
      || value.assistantMessageId.trim() === ""
      || (value.taskId !== null && typeof value.taskId !== "string")
      || !isChatTarget(value.target)
      || value.target.chapterId.trim() === "") {
      return null;
    }
    return value as unknown as PendingLocator;
  } catch {
    return null;
  }
}

function writePendingLocator(locator: PendingLocator): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(locatorKey(locator.actorId, locator.seriesId), JSON.stringify(locator));
  } catch {
    // The in-memory recovery path remains available when session storage is blocked.
  }
}

function clearPendingLocator(locator: PendingLocator): void {
  if (typeof window === "undefined") return;
  try {
    const key = locatorKey(locator.actorId, locator.seriesId);
    const serialized = window.sessionStorage.getItem(key);
    if (serialized === null) return;
    const value: unknown = JSON.parse(serialized);
    if (isRecord(value) && value.assistantMessageId === locator.assistantMessageId) {
      window.sessionStorage.removeItem(key);
    }
  } catch {
    // A stale locator is harmless and can be replaced by the next confirmed assistant message.
  }
}

function isChatTarget(value: unknown): value is ChatAssistantTarget {
  if (!isRecord(value) || typeof value.chapterId !== "string" || value.chapterId.trim() === "") return false;
  if (value.kind === "chapter") return true;
  if (value.kind === "frame") return Number.isSafeInteger(value.frameIndex) && (value.frameIndex as number) >= 0;
  return value.kind === "asset"
    && ["character", "scene", "prop", "storyboard"].includes(String(value.assetType))
    && typeof value.assetId === "string"
    && value.assetId.trim() !== "";
}

function assetChoices(
  storyboards: readonly StoryboardAsset[],
  characters: readonly Character[],
  scenes: readonly Scene[],
  props: readonly Prop[],
): AssetChoice[] {
  return [
    ...storyboards.map((asset) => ({ type: "storyboard" as const, id: asset.id, name: asset.name })),
    ...characters.map((asset) => ({ type: "character" as const, id: asset.id, name: asset.name })),
    ...scenes.map((asset) => ({ type: "scene" as const, id: asset.id, name: asset.title })),
    ...props.map((asset) => ({ type: "prop" as const, id: asset.id, name: asset.name })),
  ];
}

function assetLabel(type: ChatAssistantAssetType): string {
  switch (type) {
    case "character": return "角色";
    case "scene": return "场景";
    case "prop": return "道具";
    case "storyboard": return "分镜素材";
  }
}

function sourceText(message: ChatAssistantMessage, assets: readonly AssetChoice[]): string {
  if (message.frameIndex !== null) return `打开原章节（历史第 ${message.frameIndex + 1} 镜）`;
  if (message.assetType !== null && message.assetId !== null) {
    const choice = assets.find((item) => item.type === message.assetType && item.id === message.assetId);
    const label = `${assetLabel(message.assetType)}：${choice?.name ?? "原目标"}`;
    return message.assetType === "storyboard" ? `打开原章节（${label}）` : `打开来源素材（${label}）`;
  }
  return "打开原章节";
}

function terminalStatus(status: string): MessageTaskState | null {
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  return null;
}

function buildContextMessages(
  messages: readonly ChatAssistantMessage[],
  statuses: ReadonlyMap<string, MessageTaskState>,
  prompt: CreatorPrompt | null,
  userText: string,
): ChatAssistantStreamMessage[] {
  const safeTurns: ChatAssistantStreamMessage[] = [];
  for (let index = 0; index < messages.length - 1; index += 1) {
    const user = messages[index]!;
    const assistant = messages[index + 1];
    if (user.role !== "user" || assistant?.role !== "assistant" || statuses.get(assistant.id) !== "completed") continue;
    const userText = parseChatContent(user.content).text;
    const assistantText = parseChatContent(assistant.content).text;
    if (userText.trim() === "" || assistantText.trim() === "" || assistantText === PLACEHOLDER_CONTENT) continue;
    safeTurns.push({ role: "user", content: userText }, { role: "assistant", content: assistantText });
  }
  const boundedTurns = safeTurns.slice(-MAX_CONTEXT_MESSAGES);
  const result: ChatAssistantStreamMessage[] = [];
  if (prompt?.system_prompt?.trim()) result.push({ role: "system", content: prompt.system_prompt });
  result.push(...boundedTurns);
  const userPrompt = prompt?.user_prompt?.trim();
  result.push({ role: "user", content: userPrompt ? `${userPrompt}\n\n${userText}` : userText });
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function ChatAssistantPage({
  services,
  series,
  userId,
  sessionGeneration,
  onUnauthorized,
  onBack,
  onNavigateSource,
}: ChatAssistantPageProps) {
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [chapterId, setChapterId] = useState("");
  const [targetKey, setTargetKey] = useState("chapter");
  const [storyboards, setStoryboards] = useState<StoryboardAsset[]>([]);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [props, setProps] = useState<Prop[]>([]);
  const [models, setModels] = useState<StoryboardModelChoice[]>([]);
  const [prompts, setPrompts] = useState<CreatorPrompt[]>([]);
  const [modelId, setModelId] = useState("");
  const [promptId, setPromptId] = useState("");
  const [enableThinking, setEnableThinking] = useState(false);
  const [draft, setDraft] = useState("");
  const [snapshot, setSnapshot] = useState<HistorySnapshot | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState<PendingOperation | null>(null);
  const [streamPreview, setStreamPreview] = useState("");
  const [streamNotice, setStreamNotice] = useState<string | null>(null);
  const [messageNotice, setMessageNotice] = useState<string | null>(null);
  const [deletingMessageId, setDeletingMessageId] = useState<string | null>(null);
  const streamControllerRef = useRef<AbortController | null>(null);
  const recoveryWaitControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
  const operationBusyRef = useRef(false);
  const restoredLocatorRef = useRef<string | null>(null);
  const renderIdentityRef = useRef<object>({});
  const unauthorizedHandlerRef = useRef(onUnauthorized);
  unauthorizedHandlerRef.current = onUnauthorized;
  const target = useMemo(() => parseTargetKey(targetKey, chapterId), [chapterId, targetKey]);
  const targetSignature = targetScopeKey(target);
  const identitySignature = useMemo(() => ({
    userId,
    apiBaseUrl: services.apiBaseUrl,
    sessionGeneration,
    services,
    seriesId: series.id,
    targetSignature,
  }), [services, services.apiBaseUrl, sessionGeneration, series.id, targetSignature, userId]);
  renderIdentityRef.current = identitySignature;
  const currentLocator = readPendingLocator(userId, services.apiBaseUrl, series.id);
  const assets = useMemo(() => assetChoices(storyboards, characters, scenes, props), [storyboards, characters, scenes, props]);
  const selectedModel = models.find((model) => model.id === modelId) ?? null;
  const selectedPrompt = promptId === "" ? null : prompts.find((prompt) => prompt.id === promptId) ?? null;
  const historyScopeKey = targetSignature;
  const visibleMessages = snapshot?.scopeKey === historyScopeKey ? snapshot.messages : [];
  const taskStates = snapshot?.scopeKey === historyScopeKey ? snapshot.taskStates : new Map<string, MessageTaskState>();
  const canSend = series.can_enter === true
    && services.chatAssistant !== undefined
    && selectedModel !== null
    && target !== null
    && draft.trim() !== ""
    && !catalogLoading
    && !historyLoading
    && !directoryLoading
    && !sending
    && pending === null;

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      streamControllerRef.current?.abort();
      recoveryWaitControllerRef.current?.abort();
      recoveryWaitControllerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    const settings = chatSettings(services);
    const catalog = services.storyboardGeneration;
    setCatalogLoading(true);
    setCatalogError(null);
    setPageError(null);
    if (services.chatAssistant === undefined || catalog === undefined || settings === undefined) {
      setCatalogLoading(false);
      setModels([]);
      setPrompts([]);
      setCatalogError("聊天服务或模型/提示词目录尚未接入。");
      return () => { current = false; controller.abort(); };
    }

    Promise.all([
      services.listChapters(series.id, controller.signal),
      catalog.listModels(controller.signal),
      settings.listPrompts("chat-assistant", controller.signal),
    ]).then(([nextChapters, nextModels, nextPrompts]) => {
      if (!current || controller.signal.aborted) return;
      const textModels = nextModels.filter((model) => model.category === "text" || model.category === "reasoning");
      const assistantPrompts = nextPrompts.filter((prompt) => prompt.category === "chat-assistant");
      setChapters(nextChapters);
      setModels(textModels);
      setPrompts(assistantPrompts);
      const locator = readPendingLocator(userId, services.apiBaseUrl, series.id);
      if (locator !== null && nextChapters.some((chapter) => chapter.id === locator.target.chapterId)) {
        setChapterId(locator.target.chapterId);
        setTargetKey(makeTargetKey(locator.target));
        setPending({ ...locator, phase: "unknown", detail: "发现上次尚未确认的聊天操作，可刷新原任务。", attempt: 0 });
      } else {
        setChapterId((currentId) => currentId !== "" && nextChapters.some((chapter) => chapter.id === currentId)
          ? currentId
          : nextChapters[0]?.id ?? "");
      }
      setModelId((currentId) => textModels.some((model) => model.id === currentId) ? currentId : "");
      setCatalogLoading(false);
      setCatalogError(null);
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted) return;
      const normalized = normalizeApiError(error);
      if (normalized.status === 401) unauthorizedHandlerRef.current();
      else setCatalogError(normalized.detail ?? normalized.message);
      setCatalogLoading(false);
    });
    return () => { current = false; controller.abort(); };
  }, [series.id, services, sessionGeneration, userId]);

  useEffect(() => {
    if (chapterId === "" || !chapters.some((chapter) => chapter.id === chapterId)) {
      setStoryboards([]);
      setCharacters([]);
      setScenes([]);
      setProps([]);
      setDirectoryError(null);
      return;
    }
    const controller = new AbortController();
    let current = true;
    setDirectoryLoading(true);
    setDirectoryError(null);
    Promise.all([
      services.listStoryboardAssets(series.id, chapterId, controller.signal),
      services.listCharacters(series.id, controller.signal),
      services.listScenes(series.id, controller.signal),
      services.listProps(series.id, controller.signal),
    ]).then(([nextStoryboards, nextCharacters, nextScenes, nextProps]) => {
      if (!current || controller.signal.aborted) return;
      setStoryboards(nextStoryboards);
      setCharacters(nextCharacters);
      setScenes(nextScenes);
      setProps(nextProps);
      setDirectoryLoading(false);
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted) return;
      const normalized = normalizeApiError(error);
      if (normalized.status === 401) unauthorizedHandlerRef.current();
      else setDirectoryError(normalized.detail ?? normalized.message);
      setDirectoryLoading(false);
    });
    return () => { current = false; controller.abort(); };
  }, [chapterId, series.id, services]);

  useEffect(() => {
    if (target === null || services.chatAssistant === undefined || series.can_enter !== true) {
      setSnapshot(null);
      setHistoryLoading(false);
      return;
    }
    const scopeKey = targetScopeKey(target);
    const identity = identitySignature;
    const controller = new AbortController();
    let current = true;
    setHistoryLoading(true);
    setHistoryError(null);
    setSnapshot(null);
    const run = createRun(identity);
    if (run === null) {
      setHistoryLoading(false);
      setHistoryError("聊天服务或当前登录会话无法确认。");
      return () => { current = false; controller.abort(); };
    }
    void readHistorySnapshot(run, target, controller.signal).then((next) => {
      if (!current || controller.signal.aborted || !run.isCurrent()) return;
      setSnapshot({ ...next, scopeKey });
      setHistoryLoading(false);
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted) return;
      const normalized = normalizeApiError(error);
      if (normalized.status === 401) unauthorizedHandlerRef.current();
      else setHistoryError(normalized.detail ?? normalized.message);
      setHistoryLoading(false);
    });
    return () => { current = false; controller.abort(); };
  }, [identitySignature, services, series.can_enter, target, targetSignature, userId]);

  useEffect(() => {
    if (currentLocator === null || !chapters.some((chapter) => chapter.id === currentLocator.target.chapterId)) return;
    setPending((existing) => existing?.assistantMessageId === currentLocator.assistantMessageId
      ? existing
      : { ...currentLocator, phase: "unknown", detail: "发现上次尚未确认的聊天操作，可刷新原任务。", attempt: 0 });
  }, [currentLocator?.assistantMessageId, chapters]);

  useEffect(() => {
    if (pending === null || pending.phase !== "unknown" || currentLocator === null
      || pending.assistantMessageId !== currentLocator.assistantMessageId
      || targetScopeKey(pending.target) !== targetSignature
      || restoredLocatorRef.current === currentLocator.assistantMessageId) return;
    restoredLocatorRef.current = currentLocator.assistantMessageId;
    void manualRefreshPending();
  }, [currentLocator?.assistantMessageId, pending?.assistantMessageId, pending?.phase, targetSignature]);

  function createRun(scopeSignature: object): ChatAssistantRun | null {
    const port = services.chatAssistant;
    if (port === undefined) return null;
    try {
      return port.beginRun({
        actorId: userId,
        isContextCurrent: () => mountedRef.current && renderIdentityRef.current === scopeSignature,
      });
    } catch (error) {
      if (normalizeApiError(error).status === 401) unauthorizedHandlerRef.current();
      return null;
    }
  }

  async function inspectAssistant(
    run: ChatAssistantRun,
    message: ChatAssistantMessage,
    signal: AbortSignal,
  ): Promise<{ state: MessageTaskState; taskId: string | null }> {
    if (message.role !== "assistant" || message.content === PLACEHOLDER_CONTENT) return { state: "unknown", taskId: null };
    const lookup = await run.getTaskByMessage(message.id, signal);
    if (!run.isCurrent()) throw new ApiError("aborted", "聊天会话已变化。");
    if (lookup.kind === "missing") return { state: "unknown", taskId: null };
    const first = lookup.receipt;
    if (first.messageId !== message.id || first.type !== "chat") return { state: "unknown", taskId: first.id };
    let receipt = first;
    if (services.getTaskReceipt !== undefined) {
      receipt = await services.getTaskReceipt(first.id, signal);
      if (!run.isCurrent() || receipt.messageId !== message.id || receipt.type !== "chat") {
        return { state: "unknown", taskId: first.id };
      }
    } else {
      return { state: "unknown", taskId: first.id };
    }
    const terminal = terminalStatus(receipt.status);
    if (terminal === "completed") {
      return { state: typeof receipt.result === "string" && receipt.result === message.content ? "completed" : "unknown", taskId: first.id };
    }
    if (terminal === "failed") return { state: "failed", taskId: first.id };
    return { state: "processing", taskId: first.id };
  }

  async function readHistorySnapshot(
    run: ChatAssistantRun,
    selectedTarget: ChatAssistantTarget,
    signal: AbortSignal,
  ): Promise<HistorySnapshot> {
    const messages = await run.listMessages(selectedTarget, signal);
    if (!run.isCurrent()) throw new ApiError("aborted", "聊天会话已变化。");
    const taskStates = new Map<string, MessageTaskState>();
    const assistants = messages.filter((message) => message.role === "assistant").slice(-80);
    await Promise.all(assistants.map(async (message) => {
      try {
        const result = await inspectAssistant(run, message, signal);
        taskStates.set(message.id, result.state);
      } catch (error) {
        if (normalizeApiError(error).status === 401) throw error;
        taskStates.set(message.id, "unknown");
      }
    }));
    if (!run.isCurrent() || signal.aborted) throw new ApiError("aborted", "聊天会话已变化。");
    return { scopeKey: targetScopeKey(selectedTarget), messages, taskStates };
  }

  function onChapterChange(nextChapterId: string) {
    if (pending !== null || sending) return;
    setChapterId(nextChapterId);
    setTargetKey("chapter");
    setMessageNotice(null);
  }

  function onTargetChange(nextTargetKey: string) {
    if (pending !== null || sending) return;
    setTargetKey(nextTargetKey);
    setMessageNotice(null);
  }

  async function createConfirmedMessage(
    run: ChatAssistantRun,
    input: Parameters<ChatAssistantRun["createMessage"]>[0],
    signal: AbortSignal,
  ): Promise<ChatAssistantMessage | null> {
    try {
      return await run.createMessage(input, signal);
    } catch (error) {
      const apiError = normalizeApiError(error);
      if (apiError.status === 401) {
        if (run.isCurrent()) unauthorizedHandlerRef.current();
        throw error;
      }
      if (apiError.status !== null && apiError.status < 500 && apiError.kind === "http") throw error;
      try {
        const messages = await run.listMessages(input.target, signal);
        if (run.isCurrent()) {
          const scopeKey = targetScopeKey(input.target);
          setSnapshot((current) => current?.scopeKey === scopeKey
            ? { ...current, messages }
            : current);
        }
      } catch (refreshError) {
        if (normalizeApiError(refreshError).status === 401 && run.isCurrent()) unauthorizedHandlerRef.current();
      }
      return null;
    }
  }

  async function submitMessage() {
    if (!canSend || target === null || selectedModel === null || services.chatAssistant === undefined) return;
    if (operationBusyRef.current) return;
    operationBusyRef.current = true;
    setSending(true);
    setMessageNotice(null);
    setStreamNotice(null);
    setStreamPreview("");
    const capturedTarget = target;
    const capturedDraft = draft;
    const capturedModel = selectedModel;
    const capturedPrompt = selectedPrompt;
    const scopeSignature = identitySignature;
    const run = createRun(scopeSignature);
    const requestController = new AbortController();
    if (run === null) {
      setSending(false);
      operationBusyRef.current = false;
      setMessageNotice("当前登录会话无法确认，未创建聊天消息。");
      return;
    }
    try {
      const before = await run.listMessages(capturedTarget, requestController.signal);
      if (!run.isCurrent()) return;
      const beforeSnapshot = await readHistorySnapshot(run, capturedTarget, requestController.signal);
      if (!run.isCurrent()) return;
      setSnapshot(beforeSnapshot);
      const metadata = {
        modelId: capturedModel.id,
        modelName: capturedModel.name,
        promptId: capturedPrompt?.id ?? null,
      };
      const userContent = encodeChatContent(metadata, capturedDraft);
      const userMessage = await createConfirmedMessage(run, {
        target: capturedTarget,
        role: "user",
        content: userContent,
        modelName: null,
      }, requestController.signal);
      if (userMessage === null) {
        if (run.isCurrent()) setMessageNotice("用户消息创建结果尚未确认。系统只读刷新了当前会话，不会自动重试；请刷新核对。");
        return;
      }
      if (!run.isCurrent()) return;
      setSnapshot((current) => current?.scopeKey === targetScopeKey(capturedTarget)
        ? { ...current, messages: [...current.messages.filter((message) => message.id !== userMessage.id), userMessage] }
        : current);

      const assistantMessage = await createConfirmedMessage(run, {
        target: capturedTarget,
        role: "assistant",
        content: PLACEHOLDER_CONTENT,
        modelName: capturedModel.name,
      }, requestController.signal);
      if (assistantMessage === null) {
        if (run.isCurrent()) {
          setMessageNotice("助手占位消息创建结果尚未确认；系统只读刷新了会话，不会猜测消息编号或启动聊天流。");
          setHistoryError("请核对当前会话后再决定下一步；本次不会自动重试。");
        }
        return;
      }
      if (!run.isCurrent()) return;
      const locator: PendingLocator = {
        actorId: userId,
        apiBaseUrl: services.apiBaseUrl ?? "",
        seriesId: series.id,
        target: capturedTarget,
        assistantMessageId: assistantMessage.id,
        taskId: null,
      };
      restoredLocatorRef.current = assistantMessage.id;
      writePendingLocator(locator);
      setSnapshot((current) => current?.scopeKey === targetScopeKey(capturedTarget)
        ? { ...current, messages: [...current.messages.filter((message) => message.id !== assistantMessage.id), assistantMessage] }
        : current);
      setPending({ ...locator, phase: "receiving", detail: "正在接收聊天回复。停止接收不会取消服务器任务。", attempt: 0 });

      const streamController = new AbortController();
      streamControllerRef.current = streamController;
      let completeReceived = false;
      let streamErrorMessage: string | null = null;
      try {
        const streamMessages = buildContextMessages(before, beforeSnapshot.taskStates, capturedPrompt, capturedDraft);
        const input = {
          modelId: capturedModel.id,
          messages: streamMessages,
          messageId: assistantMessage.id,
          promptId: capturedPrompt?.id ?? null,
          enableThinking,
        } as const;
        for await (const event of run.stream(input, streamController.signal)) {
          if (!run.isCurrent()) return;
          if (event.type === "content") setStreamPreview(event.fullText);
          else if (event.type === "status" || event.type === "warning") setStreamNotice(event.message);
          else if (event.type === "error") streamErrorMessage = event.message;
          else if (event.type === "complete") completeReceived = true;
        }
      } catch (error) {
        const normalized = normalizeApiError(error);
        if (normalized.status === 401 && run.isCurrent()) unauthorizedHandlerRef.current();
        streamErrorMessage = normalized.detail ?? normalized.message;
      } finally {
        if (streamControllerRef.current === streamController) streamControllerRef.current = null;
      }
      if (!run.isCurrent()) return;
      await recoverPending({
        ...locator,
        phase: completeReceived && streamErrorMessage === null ? "recovering" : "unknown",
        detail: streamErrorMessage ?? (completeReceived ? "聊天流已完成，正在核对原任务与消息。" : "聊天流未确认完成，正在读取原任务；不会重新发送。"),
        attempt: 0,
      }, run, completeReceived, streamErrorMessage);
    } catch (error) {
      if (run.isCurrent()) {
        const normalized = normalizeApiError(error);
        if (normalized.status === 401) unauthorizedHandlerRef.current();
        else setMessageNotice(normalized.detail ?? normalized.message);
      }
    } finally {
      setSending(false);
      operationBusyRef.current = false;
      streamControllerRef.current = null;
    }
  }

  async function recoverPending(
    initial: PendingOperation,
    run: ChatAssistantRun,
    streamComplete: boolean,
    streamErrorMessage: string | null,
  ): Promise<void> {
    let current = initial;
    setPending(current);
    for (let attempt = 0; attempt < MAX_RECOVERY_ATTEMPTS; attempt += 1) {
      if (!run.isCurrent()) return;
      setPending({ ...current, phase: attempt === 0 ? "recovering" : "recovering", attempt: attempt + 1 });
      const controller = new AbortController();
      const outcome = await inspectPending(current, run, controller.signal);
      if (!run.isCurrent()) return;
      if (outcome.kind === "completed" || outcome.kind === "failed") {
        clearPendingLocator(current);
        setPending(null);
        setStreamPreview("");
        setStreamNotice(null);
        if (outcome.kind === "completed") {
          setMessageNotice("聊天已完成，并已核对原任务回执与服务器消息。");
          setDraft((currentDraft) => currentDraft === draft ? "" : currentDraft);
        } else {
          setMessageNotice(outcome.detail ?? "聊天任务已失败。可重新编辑后明确发送新的请求。");
        }
        await refreshHistory(current.target, run);
        return;
      }
      if (outcome.kind === "conflict") {
        setPending({ ...current, phase: "unknown", detail: "该服务器消息关联多个任务，无法安全选择；请稍后手动刷新。" });
        return;
      }
      current = {
        ...current,
        phase: "recovering",
        taskId: outcome.taskId ?? current.taskId,
        detail: streamErrorMessage ?? (streamComplete
          ? "服务器尚未返回可核对的终态，正在查询原消息任务。"
          : "结果尚未确认，正在查询原消息任务；系统不会重发。"),
        attempt: attempt + 1,
      };
      setPending(current);
      if (attempt + 1 < MAX_RECOVERY_ATTEMPTS) {
        const waitController = new AbortController();
        recoveryWaitControllerRef.current = waitController;
        await delay(RECOVERY_INTERVAL_MS, waitController.signal);
        if (recoveryWaitControllerRef.current === waitController) recoveryWaitControllerRef.current = null;
      }
    }
    if (run.isCurrent()) setPending({ ...current, phase: "unknown", detail: "暂未取得终态回执。可手动刷新原任务；系统不会重新发送。" });
  }

  async function inspectPending(
    locator: PendingLocator,
    run: ChatAssistantRun,
    signal: AbortSignal,
  ): Promise<{ kind: "processing" | "missing"; taskId: string | null } | { kind: "completed" } | { kind: "failed"; detail: string | null } | { kind: "conflict" }> {
    try {
      const lookup = await run.getTaskByMessage(locator.assistantMessageId, signal);
      if (!run.isCurrent()) return { kind: "missing", taskId: null };
      if (lookup.kind === "missing") return { kind: "missing", taskId: null };
      const first = lookup.receipt;
      if (first.type !== "chat" || first.messageId !== locator.assistantMessageId) {
        return { kind: "conflict" };
      }
      if (services.getTaskReceipt === undefined) return { kind: "processing", taskId: first.id };
      const receipt = await services.getTaskReceipt(first.id, signal);
      if (!run.isCurrent() || receipt.type !== "chat" || receipt.messageId !== locator.assistantMessageId) {
        return { kind: "conflict" };
      }
      const final = terminalStatus(receipt.status);
      if (final === "failed") return { kind: "failed", detail: typeof receipt.result === "string" ? receipt.result : null };
      if (final === "completed") {
        if (typeof receipt.result !== "string") return { kind: "processing", taskId: receipt.id };
        const history = await run.listMessages(locator.target, signal);
        if (!run.isCurrent()) return { kind: "missing", taskId: receipt.id };
        const assistant = history.find((message) => message.id === locator.assistantMessageId);
        if (assistant === undefined || assistant.role !== "assistant" || assistant.content !== receipt.result) {
          return { kind: "processing", taskId: receipt.id };
        }
        return { kind: "completed" };
      }
      return { kind: "processing", taskId: receipt.id };
    } catch (error) {
      const normalized = normalizeApiError(error);
      if (normalized.status === 409) return { kind: "conflict" };
      if (normalized.status === 401 && run.isCurrent()) unauthorizedHandlerRef.current();
      return { kind: "missing", taskId: locator.taskId };
    }
  }

  async function refreshHistory(selectedTarget: ChatAssistantTarget, existingRun?: ChatAssistantRun) {
    const scope = identitySignature;
    const run = existingRun ?? createRun(scope);
    if (run === null) return;
    const controller = new AbortController();
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const next = await readHistorySnapshot(run, selectedTarget, controller.signal);
      if (run.isCurrent() && renderIdentityRef.current === scope) setSnapshot(next);
    } catch (error) {
      if (run.isCurrent()) {
        const normalized = normalizeApiError(error);
        if (normalized.status === 401) unauthorizedHandlerRef.current();
        else setHistoryError(normalized.detail ?? normalized.message);
      }
    } finally {
      if (run.isCurrent()) setHistoryLoading(false);
    }
  }

  async function manualRefreshPending() {
    if (pending === null || operationBusyRef.current) return;
    operationBusyRef.current = true;
    setPending({ ...pending, phase: "recovering", detail: "正在刷新原消息任务，不会重新发送。", attempt: 1 });
    const run = createRun(identitySignature);
    if (run === null) {
      setPending({ ...pending, phase: "unknown", detail: "当前登录会话无法确认。" });
      operationBusyRef.current = false;
      return;
    }
    try {
      const outcome = await inspectPending(pending, run, new AbortController().signal);
      if (!run.isCurrent()) return;
      if (outcome.kind === "completed" || outcome.kind === "failed") {
        clearPendingLocator(pending);
        setPending(null);
        setStreamPreview("");
        setMessageNotice(outcome.kind === "completed" ? "聊天已完成并核对原消息。" : outcome.detail ?? "原聊天任务已失败。");
        await refreshHistory(pending.target, run);
      } else if (outcome.kind === "conflict") {
        setPending({ ...pending, phase: "unknown", detail: "原消息关联多个任务，无法安全选择。" });
      } else {
        setPending({ ...pending, taskId: outcome.taskId ?? pending.taskId, phase: "unknown", detail: "仍未取得终态回执；可稍后手动刷新，系统不会重发。" });
      }
    } finally {
      operationBusyRef.current = false;
    }
  }

  function stopTrackingPending() {
    if (pending === null || pending.phase !== "unknown" || operationBusyRef.current) return;
    clearPendingLocator(pending);
    setPending(null);
    setStreamPreview("");
    setStreamNotice(null);
    setMessageNotice("已停止本地跟踪。原任务仍可能在服务器运行，费用状态未确认；此操作不会取消任务或自动重发。之后主动发送会创建新的助手消息。");
  }

  async function copyMessage(text: string) {
    const scope = identitySignature;
    const updateNotice = (notice: string) => {
      if (mountedRef.current && renderIdentityRef.current === scope) setMessageNotice(notice);
    };
    try {
      if (typeof navigator.clipboard?.writeText !== "function") {
        updateNotice("当前浏览器不支持复制消息。");
        return;
      }
      await navigator.clipboard.writeText(text);
      updateNotice("已复制当前可见消息文本。");
    } catch {
      updateNotice("复制失败，请检查浏览器权限或手动选择消息文本。");
    }
  }

  async function deleteTerminalMessage(message: ChatAssistantMessage) {
    if (target === null || services.chatAssistant === undefined || deletingMessageId !== null || pending !== null) return;
    const state = taskStates.get(message.id);
    if (state !== "completed" && state !== "failed") return;
    const identity = identitySignature;
    const run = createRun(identity);
    if (run === null) return;
    setDeletingMessageId(message.id);
    try {
      await run.deleteMessage(target, message.id, new AbortController().signal);
      if (!run.isCurrent()) return;
      setSnapshot((current) => current?.scopeKey === targetSignature
        ? { ...current, messages: current.messages.filter((item) => item.id !== message.id) }
        : current);
      setMessageNotice("已删除这条终态消息。");
    } catch (error) {
      const normalized = normalizeApiError(error);
      if (normalized.status === 401 && run.isCurrent()) unauthorizedHandlerRef.current();
      else if (run.isCurrent()) setMessageNotice(normalized.detail ?? normalized.message);
    } finally {
      if (run.isCurrent()) setDeletingMessageId(null);
    }
  }

  function navigateToMessageSource(message: ChatAssistantMessage) {
    if (!chapters.some((chapter) => chapter.id === message.chapterId)) return;
    const identity = identitySignature;
    const isCurrent = () => mountedRef.current && renderIdentityRef.current === identity
      && snapshot?.scopeKey === targetSignature
      && snapshot.messages.some((candidate) => candidate.id === message.id);
    if (message.assetType !== null && message.assetId !== null && message.assetType !== "storyboard") {
      const category = message.assetType === "character" ? "characters" : message.assetType === "scene" ? "scenes" : "props";
      const list = category === "characters" ? characters : category === "scenes" ? scenes : props;
      if (list.filter((asset) => asset.id === message.assetId).length !== 1) return;
      onNavigateSource({ kind: "asset", seriesId: series.id, category, assetId: message.assetId, isCurrent });
      return;
    }
    onNavigateSource({ kind: "chapter", seriesId: series.id, chapterId: message.chapterId, isCurrent });
  }

  const currentChapter = chapters.find((chapter) => chapter.id === chapterId) ?? null;
  const targetOptionRows = useMemo(() => {
    const options: Array<{ key: string; label: string }> = [{ key: "chapter", label: "整章会话" }];
    if (currentChapter !== null && Array.isArray(currentChapter.content)) {
      currentChapter.content.forEach((_frame, index) => options.push({ key: `frame:${index}`, label: `第 ${index + 1} 镜` }));
    }
    for (const asset of assets) {
      options.push({ key: `asset:${asset.type}:${asset.id}`, label: `${assetLabel(asset.type)}：${asset.name}` });
    }
    return options;
  }, [assets, currentChapter]);

  useEffect(() => {
    if (target === null) setTargetKey("chapter");
    else if (target.chapterId !== chapterId) setTargetKey("chapter");
  }, [chapterId, target]);

  return (
    <section className="chat-assistant-page" aria-labelledby="chat-assistant-title">
      <header className="chat-assistant-header">
        <div>
          <p className="eyebrow">剧集助手</p>
          <h1 id="chat-assistant-title">AI 聊天助手</h1>
          <p className="chat-assistant-series">{series.name}</p>
        </div>
        <button className="secondary-button" onClick={onBack} type="button">返回章节</button>
      </header>

      {series.can_enter !== true && <p className="chat-assistant-alert" role="alert">当前账号没有进入该剧集的权限。</p>}
      {catalogError !== null && <p className="chat-assistant-alert" role="alert">模型/提示词目录：{catalogError}</p>}
      {directoryError !== null && <p className="chat-assistant-alert" role="alert">会话目标读取失败：{directoryError}</p>}
      {pageError !== null && <p className="chat-assistant-alert" role="alert">{pageError}</p>}

      <div className="chat-assistant-controls">
        <label>
          <span>章节</span>
          <select disabled={chapters.length === 0 || pending !== null || sending} onChange={(event) => onChapterChange(event.target.value)} value={chapterId}>
            <option value="">选择章节</option>
            {chapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title}</option>)}
          </select>
        </label>
        <label>
          <span>会话目标</span>
          <select disabled={currentChapter === null || pending !== null || sending || directoryLoading} onChange={(event) => onTargetChange(event.target.value)} value={targetKey}>
            {targetOptionRows.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
        </label>
        <label>
          <span>文本模型</span>
          <select disabled={catalogLoading || models.length === 0 || pending !== null || sending} onChange={(event) => setModelId(event.target.value)} value={modelId}>
            <option value="">选择模型</option>
            {models.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.provider}</option>)}
          </select>
        </label>
        <label>
          <span>提示词模板</span>
          <select disabled={catalogLoading || pending !== null || sending} onChange={(event) => setPromptId(event.target.value)} value={promptId}>
            <option value="">无需提示词</option>
            {prompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}</option>)}
          </select>
        </label>
        <label className="chat-assistant-thinking">
          <input checked={enableThinking} disabled={pending !== null || sending} onChange={(event) => setEnableThinking(event.target.checked)} type="checkbox" />
          <span>启用深度思考</span>
        </label>
      </div>

      <div className="chat-assistant-history" aria-label="聊天记录" aria-live="polite">
        <div className="chat-assistant-history-heading">
          <h2>当前会话</h2>
          <button className="text-button" disabled={historyLoading || target === null} onClick={() => target !== null && void refreshHistory(target)} type="button">
            刷新会话
          </button>
        </div>
        {historyLoading && <p role="status">正在读取当前会话…</p>}
        {historyError !== null && <p className="chat-assistant-alert" role="alert">{historyError}</p>}
        {!historyLoading && visibleMessages.length === 0 && <p className="chat-assistant-empty">选择章节和会话目标后开始聊天。</p>}
        <ol className="chat-assistant-message-list">
          {visibleMessages.map((message) => {
            const parsed = message.role === "user"
              ? parseChatContent(message.content)
              : { metadata: null, text: message.content };
            const displayText = message.id === pending?.assistantMessageId && streamPreview !== "" ? streamPreview : parsed.text;
            const state = taskStates.get(message.id) ?? (message.id === pending?.assistantMessageId ? "processing" : "unknown");
            const canDelete = state === "completed" || state === "failed";
            const promptName = parsed.metadata?.promptId === null || parsed.metadata?.promptId === undefined
              ? null
              : prompts.find((prompt) => prompt.id === parsed.metadata!.promptId)?.name ?? null;
            return (
              <li className={`chat-assistant-message ${message.role}`} key={message.id}>
                <div className="chat-assistant-message-meta">
                  <strong>{message.role === "user" ? "我" : message.modelName ?? parsed.metadata?.modelName ?? "AI 助手"}</strong>
                  <time dateTime={message.createdAt}>{formatMessageTime(message.createdAt)}</time>
                </div>
                {(parsed.metadata !== null || promptName !== null) && (
                  <p className="chat-assistant-context-chip">
                    {parsed.metadata?.modelName ?? message.modelName ?? "模型"}
                    {promptName !== null && ` · ${promptName}`}
                  </p>
                )}
                <p className="chat-assistant-message-body">{displayText}</p>
                {state === "processing" && message.id !== pending?.assistantMessageId && <p className="chat-assistant-inline-state">任务仍在运行或等待确认</p>}
                {message.id === pending?.assistantMessageId && <p className="chat-assistant-inline-state">{pending.phase === "receiving" ? "正在接收" : "结果未确认"}</p>}
                <div className="chat-assistant-message-actions">
                  <button className="text-button" onClick={() => navigateToMessageSource(message)} type="button">{sourceText(message, assets)}</button>
                  <button className="text-button" onClick={() => void copyMessage(displayText)} type="button">复制消息</button>
                  {message.role === "user" && (
                    <button className="text-button" onClick={() => {
                      const context = parseChatContent(message.content);
                      setDraft(context.text);
                      if (context.metadata !== null) {
                        if (models.some((model) => model.id === context.metadata!.modelId)) setModelId(context.metadata.modelId);
                        setPromptId(context.metadata.promptId !== null && prompts.some((prompt) => prompt.id === context.metadata!.promptId)
                          ? context.metadata.promptId
                          : "");
                      }
                      setMessageNotice("已将原消息载入输入框；不会自动发送。");
                    }} type="button">重新编辑</button>
                  )}
                  {canDelete && pending === null && (
                    <button className="text-button danger-text" disabled={deletingMessageId === message.id}
                      onClick={() => void deleteTerminalMessage(message)} type="button">删除此消息</button>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </div>

      {pending !== null && (
        <section className="chat-assistant-pending" aria-label="待核对的聊天任务">
          <p role="status">{pending.detail}</p>
          {pending.phase === "receiving" && (
            <button className="secondary-button" onClick={() => streamControllerRef.current?.abort()} type="button">停止接收</button>
          )}
          {pending.phase !== "receiving" && (
            <button className="secondary-button" disabled={operationBusyRef.current} onClick={() => void manualRefreshPending()} type="button">刷新原任务状态</button>
          )}
          {pending.phase === "unknown" && (
            <button className="secondary-button" disabled={operationBusyRef.current} onClick={stopTrackingPending} type="button">停止跟踪本次任务</button>
          )}
          {pending.phase === "receiving" && <span>停止接收只停止本页读取，不会取消服务器任务。</span>}
          {pending.phase === "recovering" && <span>正在只读核对原任务；本页不会重新发送。</span>}
          {pending.phase === "unknown" && (
            <span>原任务仍可能在服务器运行，费用状态未确认。停止跟踪只清除本页等待，不会取消任务或自动重发。</span>
          )}
        </section>
      )}

      {streamNotice !== null && <p className="chat-assistant-note" role="status">{streamNotice}</p>}
      {messageNotice !== null && <p className="chat-assistant-note" role="status">{messageNotice}</p>}

      <form className="chat-assistant-composer" onSubmit={(event) => { event.preventDefault(); void submitMessage(); }}>
        <label>
          <span>发送给 AI 助手</span>
          <textarea disabled={catalogError !== null || series.can_enter !== true} onChange={(event) => setDraft(event.target.value)} rows={5} value={draft} />
        </label>
        <div className="chat-assistant-composer-actions">
          <span>{selectedModel === null ? "请先选择实际可用的文本模型。" : `当前模型：${selectedModel.name}`}</span>
          <button className="primary-button" disabled={!canSend} type="submit">发送消息</button>
        </div>
      </form>
    </section>
  );
}

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      globalThis.clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      resolve();
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function formatMessageTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
