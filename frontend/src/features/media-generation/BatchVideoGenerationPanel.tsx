import { useEffect, useMemo, useRef, useState } from "react";
import type { Chapter, Character, FusedAsset, Prop, Scene, Series, StoryboardAsset } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import {
  MediaGenerationError,
  type MediaGenerationRun,
  type MediaGenerationVideoInput,
  type VideoGenerationReceipt,
} from "../../shared/api/mediaGeneration";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import type { WorkspaceServices } from "../../shared/api/services";
import type { TaskReceipt } from "../../shared/api/taskDetails";
import type { QuickReferenceSettings } from "./quickReference";
import {
  batchVideoModelOptions,
  buildBatchVideoFrameContext,
  estimateBatchVideoCost,
  promptCharacterLimit,
  renderBatchVideoPrompt,
  suggestedBatchVideoDuration,
} from "./batchVideoContext";
import {
  batchVideoPreferenceKey,
  defaultBatchVideoChecks,
  readBatchVideoPreference,
  writeBatchVideoPreference,
  type BatchVideoChecks,
  type BatchVideoPreference,
} from "./batchVideoPreferences";

type ItemStatus = "waiting" | "submitting" | "accepted" | "unknown" | "failed" | "skipped";

interface QueueItem {
  readonly storyboardId: string;
  readonly label: string;
  readonly duration: number | null;
  readonly promptLength: number;
  readonly fullPromptLength: number;
  readonly input: MediaGenerationVideoInput;
  readonly status: ItemStatus;
  readonly detail: string;
  readonly taskId?: string;
  readonly messageId?: string;
  readonly taskStatus?: string;
  readonly taskProgress?: number | null;
  readonly taskReads?: number;
}

interface QueueRun {
  readonly userId: string;
  readonly sessionGeneration: number;
  readonly services: WorkspaceServices;
  readonly seriesId: string;
  readonly chapterId: string;
  readonly controller: AbortController;
  items: QueueItem[];
  nextIndex: number;
  run?: MediaGenerationRun;
  busy: boolean;
  stoppedUnknown: boolean;
  statusAuthorizationFailed: boolean;
  readonly statusReads: Set<number>;
}

interface QueuePreflight {
  readonly scope: QueueScope;
  readonly items: readonly QueueItem[];
  readonly compactItems: readonly QueueItem[];
  readonly skipped: readonly string[];
  readonly maxPromptChars: number;
  readonly overLimitLabels: readonly string[];
  readonly compactFailureLabels: readonly string[];
}

interface QueueScope {
  readonly userId: string;
  readonly sessionGeneration: number;
  readonly services: WorkspaceServices;
  readonly seriesId: string;
  readonly chapterId: string;
}

interface Props {
  readonly services: WorkspaceServices;
  readonly userId: string;
  readonly sessionGeneration: number;
  readonly series: readonly Series[];
  readonly selectedSeriesId: string;
  readonly onSeriesChange: (value: string) => void;
  readonly chapters: readonly Chapter[];
  readonly selectedChapterId: string;
  readonly onChapterChange: (value: string) => void;
  readonly selectedSeries: Series | null;
  readonly selectedChapter: Chapter | null;
  readonly storyboards: readonly StoryboardAsset[];
  readonly characters: readonly Character[];
  readonly scenes: readonly Scene[];
  readonly props: readonly Prop[];
  readonly models: readonly CreatorModel[];
  readonly prompts: readonly CreatorPrompt[];
  readonly quickReferenceSettings: QuickReferenceSettings;
  readonly loading: boolean;
  readonly chapterLoading: boolean;
  readonly directoryLoading: boolean;
  readonly chapterError: string | null;
  readonly directoryError: string | null;
  readonly onUnauthorized: () => void;
  readonly onOpenTasks: () => void;
  readonly onActivityChange: (active: boolean) => void;
}

interface RowContext {
  readonly asset: StoryboardAsset;
  readonly result: ReturnType<typeof buildBatchVideoFrameContext>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function ids(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => typeof item === "string" && item !== ""
    ? [item]
    : typeof item === "number" && Number.isSafeInteger(item) ? [String(item)] : []);
}

function defaultPreference(models: readonly CreatorModel[], prompts: readonly CreatorPrompt[]): BatchVideoPreference {
  const model = models.find((candidate) => candidate.category === "video") ?? null;
  const options = batchVideoModelOptions(model);
  return {
    modelId: model?.id ?? "",
    promptId: prompts.find((candidate) => candidate.category === "video-design")?.id ?? "",
    size: options.defaultSize || options.sizes[0] || "",
    resolution: options.defaultResolution || options.resolutions[0] || "",
    checks: defaultBatchVideoChecks(),
  };
}

function resolvePreference(
  saved: BatchVideoPreference,
  fallback: BatchVideoPreference,
  models: readonly CreatorModel[],
  prompts: readonly CreatorPrompt[],
): { readonly preference: BatchVideoPreference; readonly warning: string | null } {
  const model = models.find((candidate) => candidate.category === "video" && candidate.id === saved.modelId)
    ?? models.find((candidate) => candidate.category === "video" && candidate.id === fallback.modelId)
    ?? null;
  const options = batchVideoModelOptions(model);
  const promptValid = saved.promptId === "" || prompts.some((candidate) => (
    candidate.id === saved.promptId && candidate.category === "video-design"
  ));
  const size = saved.size === "" || options.sizes.includes(saved.size)
    ? saved.size : options.defaultSize || options.sizes[0] || "";
  const resolution = saved.resolution === "" || options.resolutions.includes(saved.resolution)
    ? saved.resolution : options.defaultResolution || options.resolutions[0] || "";
  const warnings: string[] = [];
  if (saved.modelId !== "" && model?.id !== saved.modelId) warnings.push("已记住的模型不可用，已选当前默认模型。");
  if (!promptValid) warnings.push("已记住的提示词不可用，已切回不使用模板。");
  if (size !== saved.size && saved.size !== "") warnings.push("画面比例已按当前模型可用选项调整。");
  if (resolution !== saved.resolution && saved.resolution !== "") warnings.push("分辨率已按当前模型可用选项调整。");
  return {
    preference: {
      modelId: model?.id ?? "",
      promptId: promptValid ? saved.promptId : "",
      size: options.sizes.length === 0 ? "" : size,
      resolution: options.resolutions.length === 0 ? "" : resolution,
      checks: { ...saved.checks },
    },
    warning: warnings.length === 0 ? null : warnings.join(" "),
  };
}

function requiresFusedRead(chapter: Chapter | null, checks: BatchVideoChecks): boolean {
  return chapter?.content?.some((frame) => isRecord(frame) && ids(frame.fused).length > 0) === true
    && (checks.character || checks.prop);
}

function isUnauthorized(error: unknown): boolean {
  return (error instanceof MediaGenerationError && error.kind === "unauthorized")
    || (error instanceof ApiError && error.status === 401);
}

function isDefiniteFailure(error: unknown): boolean {
  return error instanceof MediaGenerationError && (error.kind === "not-sent" || error.kind === "rejected");
}

function makeId(): string {
  return crypto.randomUUID();
}

function frameLabel(asset: StoryboardAsset): string {
  return "第 " + (asset.frame_index + 1) + " 镜 · " + (asset.name || "未命名分镜");
}

const MAX_AUTOMATIC_TASK_READS = 3;
const TASK_READ_BATCH_SIZE = 4;

function isTerminalTaskStatus(status: string | undefined): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

function taskStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: "排队中",
    processing: "处理中",
    cancelling: "正在取消",
    reconciling: "正在核对",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] ?? "状态：" + status;
}

export function BatchVideoGenerationPanel(props: Props) {
  const [preference, setPreference] = useState(() => defaultPreference(props.models, props.prompts));
  const [preferenceMessage, setPreferenceMessage] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<readonly string[]>([]);
  const selectedIdsRef = useRef<readonly string[]>([]);
  const [durations, setDurations] = useState<Record<string, string>>({});
  const durationTouchedRef = useRef(new Set<string>());
  const [fusedAssets, setFusedAssets] = useState<readonly FusedAsset[]>([]);
  const [fusedState, setFusedState] = useState<"idle" | "loading" | "ready" | "unavailable" | "failed">("idle");
  const [fusedMessage, setFusedMessage] = useState<string | null>(null);
  const [items, setItems] = useState<readonly QueueItem[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [paused, setPaused] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const [preflight, setPreflight] = useState<QueuePreflight | null>(null);
  const preflightRef = useRef<QueuePreflight | null>(null);
  const [statusReadingIds, setStatusReadingIds] = useState<ReadonlySet<string>>(() => new Set());
  const queueRef = useRef<QueueRun | null>(null);
  const mountedRef = useRef(false);
  const activeRef = useRef(false);
  const scopeIdentityRef = useRef({
    services: props.services,
    userId: props.userId,
    sessionGeneration: props.sessionGeneration,
    seriesId: props.selectedSeriesId,
    chapterId: props.selectedChapterId,
  });
  const preferenceEditedRef = useRef(false);
  const restoredPreferenceScopeRef = useRef<string | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const activityCallbackRef = useRef(props.onActivityChange);
  activityCallbackRef.current = props.onActivityChange;
  const scopeKey = batchVideoPreferenceKey(props.userId, props.services.apiBaseUrl);
  const model = props.models.find((candidate) => candidate.category === "video" && candidate.id === preference.modelId) ?? null;
  const options = useMemo(() => batchVideoModelOptions(model), [model]);
  const needsFused = requiresFusedRead(props.selectedChapter, preference.checks);
  const storyboards = useMemo(() => [...props.storyboards]
    .filter((asset) => asset.series_id === props.selectedSeriesId && asset.chapter_id === props.selectedChapterId)
    .sort((left, right) => left.frame_index - right.frame_index || left.id.localeCompare(right.id)),
  [props.storyboards, props.selectedChapterId, props.selectedSeriesId]);
  const contexts = useMemo<readonly RowContext[]>(() => {
    if (props.selectedSeries === null || props.selectedChapter === null) return [];
    return storyboards.map((asset) => ({
      asset,
      result: buildBatchVideoFrameContext({
        storyboardId: asset.id,
        chapterContent: props.selectedChapter?.content ?? null,
        storyboardAssets: storyboards,
        characters: props.characters,
        scenes: props.scenes,
        props: props.props,
        fusedAssets,
        checks: preference.checks,
        quickSettings: props.quickReferenceSettings.video,
        series: props.selectedSeries!,
        prompts: props.prompts,
      }),
    }));
  }, [fusedAssets, preference.checks, props.characters, props.prompts, props.props,
    props.quickReferenceSettings.video, props.scenes, props.selectedChapter, props.selectedSeries, storyboards]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const queue = queueRef.current;
      queueRef.current = null;
      queue?.controller.abort();
      if (activeRef.current) {
        activeRef.current = false;
        activityCallbackRef.current(false);
      }
    };
  }, []);

  useEffect(() => {
    const nextScope = {
      services: props.services,
      userId: props.userId,
      sessionGeneration: props.sessionGeneration,
      seriesId: props.selectedSeriesId,
      chapterId: props.selectedChapterId,
    };
    const previous = scopeIdentityRef.current;
    if (previous.services === nextScope.services && previous.userId === nextScope.userId
      && previous.sessionGeneration === nextScope.sessionGeneration
      && previous.seriesId === nextScope.seriesId && previous.chapterId === nextScope.chapterId) return;

    scopeIdentityRef.current = nextScope;
    const queue = queueRef.current;
    queueRef.current = null;
    queue?.controller.abort();
    preflightRef.current = null;
    setPreflight(null);
    setItems([]);
    setBusy(false);
    setPaused(false);
    setUnknown(false);
    setErrorMessage(null);
    setMessage(null);
    setStatusReadingIds(new Set());
    if (activeRef.current) {
      activeRef.current = false;
      activityCallbackRef.current(false);
    }
    if (previous.userId !== nextScope.userId || previous.services.apiBaseUrl !== nextScope.services.apiBaseUrl) {
      preferenceEditedRef.current = false;
      restoredPreferenceScopeRef.current = null;
      setPreference(defaultPreference(props.models, props.prompts));
      setPreferenceMessage(null);
    }
  }, [props.models, props.prompts, props.selectedChapterId, props.selectedSeriesId,
    props.services, props.sessionGeneration, props.userId]);

  useEffect(() => {
    if (props.loading || (props.models.length === 0 && props.prompts.length === 0)
      || restoredPreferenceScopeRef.current === scopeKey || preferenceEditedRef.current) return;
    restoredPreferenceScopeRef.current = scopeKey;
    const fallback = defaultPreference(props.models, props.prompts);
    const stored = readBatchVideoPreference(props.userId, props.services.apiBaseUrl);
    if (stored.kind === "found") {
      const resolved = resolvePreference(stored.value, fallback, props.models, props.prompts);
      setPreference(resolved.preference);
      setPreferenceMessage(resolved.warning
        ? "已读取当前账号批量视频选项；" + resolved.warning
        : "已读取当前账号批量视频选项。逐镜头时长单独设置，不会写入偏好。");
      return;
    }
    setPreference(fallback);
    setPreferenceMessage(stored.kind === "invalid"
      ? "本地批量视频选项无效，已使用当前目录默认值。"
      : stored.kind === "unavailable" ? "浏览器无法读取账号偏好；本页仍可手动选择。" : null);
  // Restore once per owner/API scope; later catalog refreshes only validate active choices at submit time.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey, props.loading, props.models, props.prompts]);

  useEffect(() => {
    const validIds = new Set(storyboards.map((asset) => asset.id));
    const next = selectedIds.filter((id) => validIds.has(id));
    selectedIdsRef.current = next;
    if (next.length !== selectedIds.length) setSelectedIds(next);
    durationTouchedRef.current = new Set([...durationTouchedRef.current].filter((id) => validIds.has(id)));
  }, [selectedIds, storyboards]);

  useEffect(() => {
    const controller = new AbortController();
    const snapshot = {
      services: props.services,
      userId: props.userId,
      sessionGeneration: props.sessionGeneration,
      seriesId: props.selectedSeriesId,
      chapterId: props.selectedChapterId,
    };
    if (!needsFused || snapshot.seriesId === "" || snapshot.chapterId === "") {
      setFusedAssets([]);
      setFusedState("ready");
      setFusedMessage(null);
      return () => controller.abort();
    }
    const listFused = snapshot.services.listFusedAssets;
    if (listFused === undefined) {
      setFusedAssets([]);
      setFusedState("unavailable");
      setFusedMessage("融合素材目录未接入，当前引用无法确认；关闭角色/道具检查后才能继续。");
      return () => controller.abort();
    }
    let current = true;
    setFusedState("loading");
    setFusedMessage(null);
    void listFused(snapshot.seriesId, controller.signal).then((result) => {
      const latest = propsRef.current;
      if (!current || controller.signal.aborted || latest.services !== snapshot.services
        || latest.userId !== snapshot.userId || latest.sessionGeneration !== snapshot.sessionGeneration
        || latest.selectedSeriesId !== snapshot.seriesId || latest.selectedChapterId !== snapshot.chapterId) return;
      setFusedAssets(result.filter((asset) => asset.series_id === snapshot.seriesId));
      setFusedState("ready");
    }).catch((error: unknown) => {
      const latest = propsRef.current;
      if (!current || controller.signal.aborted || latest.services !== snapshot.services
        || latest.userId !== snapshot.userId || latest.sessionGeneration !== snapshot.sessionGeneration
        || latest.selectedSeriesId !== snapshot.seriesId || latest.selectedChapterId !== snapshot.chapterId) return;
      if (isUnauthorized(error)) propsRef.current.onUnauthorized();
      else setFusedMessage("融合素材目录读取失败；为避免遗漏引用，当前队列已阻止。");
      setFusedAssets([]);
      setFusedState("failed");
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [needsFused, props.selectedChapterId, props.selectedSeriesId, props.services, props.sessionGeneration, props.userId]);

  useEffect(() => {
    if (model === null) {
      setDurations({});
      durationTouchedRef.current.clear();
      return;
    }
    setDurations((current) => {
      const next: Record<string, string> = {};
      for (const asset of storyboards) {
        const oldValue = current[asset.id] ?? "";
        const oldDuration = oldValue === "" ? null : Number(oldValue);
        if (durationTouchedRef.current.has(asset.id) && oldDuration !== null && options.durations.includes(oldDuration)) {
          next[asset.id] = oldValue;
          continue;
        }
        durationTouchedRef.current.delete(asset.id);
        const frame = props.selectedChapter?.content?.find((candidate) => (
          isRecord(candidate) && ids(candidate.storyboard).includes(asset.id)
        ));
        const text = isRecord(frame)
          ? [frame.text, frame.original_text].find((part) => typeof part === "string" && part.trim() !== "") as string | undefined
          : undefined;
        const suggested = props.quickReferenceSettings.video.matchDuration && text !== undefined
          ? suggestedBatchVideoDuration(text, options.durations) : null;
        next[asset.id] = suggested === null ? "" : String(suggested);
      }
      return next;
    });
  }, [model, options.durations, props.quickReferenceSettings.video.matchDuration, props.selectedChapter, storyboards]);

  function setActive(active: boolean) {
    setBusy(active);
    if (activeRef.current === active) return;
    activeRef.current = active;
    activityCallbackRef.current(active);
  }

  function isCurrent(queue: QueueRun): boolean {
    const current = propsRef.current;
    return mountedRef.current && queueRef.current === queue && !queue.controller.signal.aborted
      && current.userId === queue.userId && current.sessionGeneration === queue.sessionGeneration
      && current.services === queue.services && current.selectedSeriesId === queue.seriesId
      && current.selectedChapterId === queue.chapterId;
  }

  function isScopeCurrent(scope: QueueScope): boolean {
    const current = propsRef.current;
    return current.userId === scope.userId && current.sessionGeneration === scope.sessionGeneration
      && current.services === scope.services && current.selectedSeriesId === scope.seriesId
      && current.selectedChapterId === scope.chapterId;
  }

  function canStartQueue(): boolean {
    const queue = queueRef.current;
    return queue === null || (!queue.busy && !queue.stoppedUnknown && queue.nextIndex >= queue.items.length);
  }

  function updatePreference(next: BatchVideoPreference) {
    preferenceEditedRef.current = true;
    setPreference(next);
    const saved = writeBatchVideoPreference(props.userId, props.services.apiBaseUrl, next);
    setPreferenceMessage(saved
      ? "批量视频选项已按当前账号和 API 地址记住。"
      : "浏览器未能保存偏好；当前表单仍可继续使用。");
  }

  function publishItems(queue: QueueRun, index: number, patch: Partial<QueueItem>) {
    if (!isCurrent(queue)) return;
    const next = [...queue.items];
    next[index] = { ...next[index]!, ...patch };
    queue.items = next;
    setItems(next);
  }

  function acceptReceipt(queue: QueueRun, index: number, value: unknown): boolean {
    if (!isCurrent(queue) || !isRecord(value) || value.operation !== "video.single"
      || typeof value.taskId !== "string" || value.taskId === ""
      || typeof value.messageId !== "string" || value.messageId === "") return false;
    const receipt = value as unknown as VideoGenerationReceipt;
    publishItems(queue, index, {
      status: "accepted",
      taskId: receipt.taskId,
      messageId: value.messageId,
      taskStatus: receipt.status,
      taskProgress: null,
      taskReads: 0,
      detail: "已受理：" + taskStatusLabel(receipt.status) + " · 服务报价 " + receipt.quotedAmount + " 积分 · " + receipt.billingStatus,
    });
    return true;
  }

  async function refreshTaskStatus(queue: QueueRun, index: number): Promise<boolean> {
    if (!isCurrent(queue) || queue.statusReads.has(index)) return false;
    const item = queue.items[index];
    const getTaskReceipt = queue.services.getTaskReceipt;
    if (item?.status !== "accepted" || item.taskId === undefined || item.messageId === undefined) return false;
    if (getTaskReceipt === undefined) {
      publishItems(queue, index, { detail: "已受理；当前没有任务状态读取接口，请到“我的任务”核对。" });
      return false;
    }
    queue.statusReads.add(index);
    setStatusReadingIds((current) => new Set(current).add(item.storyboardId));
    try {
      const receipt: TaskReceipt = await getTaskReceipt(item.taskId, queue.controller.signal);
      if (!isCurrent(queue)) return false;
      if (receipt.id !== item.taskId || receipt.type !== "video-single" || receipt.messageId !== item.messageId) {
        publishItems(queue, index, { detail: "任务状态与当前分镜受理回执不匹配，保持待核对。" });
        return false;
      }
      const progress = receipt.progress !== null && receipt.progress <= 100 ? receipt.progress : null;
      const progressText = progress === null ? "" : " · " + progress + "%";
      publishItems(queue, index, {
        taskStatus: receipt.status,
        taskProgress: progress,
        taskReads: (item.taskReads ?? 0) + 1,
        detail: "任务状态：" + taskStatusLabel(receipt.status) + progressText,
      });
      return !isTerminalTaskStatus(receipt.status);
    } catch (error) {
      if (!isCurrent(queue)) return false;
      if (isUnauthorized(error)) {
        queue.statusAuthorizationFailed = true;
        propsRef.current.onUnauthorized();
      } else publishItems(queue, index, { detail: "已受理；暂时无法读取任务状态，可稍后手动刷新或到“我的任务”核对。" });
      return false;
    } finally {
      queue.statusReads.delete(index);
      if (mountedRef.current && isCurrent(queue)) setStatusReadingIds((current) => {
        const next = new Set(current);
        next.delete(item.storyboardId);
        return next;
      });
    }
  }

  async function pollAcceptedTasks(queue: QueueRun) {
    if (queue.services.getTaskReceipt === undefined || queue.statusAuthorizationFailed) return;
    for (let attempt = 0; attempt < MAX_AUTOMATIC_TASK_READS && isCurrent(queue) && !queue.statusAuthorizationFailed; attempt += 1) {
      if (attempt > 0) {
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, 2000);
          queue.controller.signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
        });
      }
      if (!isCurrent(queue)) return;
      const pending = queue.items.flatMap((item, index) => item.status === "accepted"
        && (item.taskReads ?? 0) < MAX_AUTOMATIC_TASK_READS && !isTerminalTaskStatus(item.taskStatus)
        ? [index] : []);
      if (pending.length === 0) return;
      for (let offset = 0; offset < pending.length; offset += TASK_READ_BATCH_SIZE) {
        await Promise.all(pending.slice(offset, offset + TASK_READ_BATCH_SIZE)
          .map((index) => refreshTaskStatus(queue, index)));
        if (!isCurrent(queue)) return;
      }
    }
  }

  function markUnsent(queue: QueueRun, from: number) {
    if (!isCurrent(queue)) return;
    const next = queue.items.map((item, index) => index >= from && item.status === "waiting"
      ? { ...item, status: "skipped" as const, detail: "前一项未能确认，已停止后续提交。" } : item);
    queue.items = next;
    setItems(next);
  }

  function stopQueue(queue: QueueRun, feedback: string) {
    if (!isCurrent(queue)) return;
    queue.busy = false;
    queueRef.current = null;
    setActive(false);
    setPaused(false);
    setUnknown(false);
    setMessage(feedback);
  }

  async function runQueue(queue: QueueRun, start: number) {
    if (!isCurrent(queue) || queue.run === undefined) return;
    queue.busy = true;
    queue.nextIndex = start;
    setActive(true);
    setPaused(false);
    setUnknown(false);
    setErrorMessage(null);
    for (let index = start; index < queue.items.length; index += 1) {
      if (!isCurrent(queue)) return;
      const item = queue.items[index]!;
      if (item.status !== "waiting" && item.status !== "skipped") continue;
      publishItems(queue, index, { status: "submitting", detail: "正在提交…" });
      queue.nextIndex = index + 1;
      try {
        const receipt = await queue.run.submitVideo(item.input, queue.controller.signal);
        if (!isCurrent(queue)) return;
        if (!acceptReceipt(queue, index, receipt)) {
          publishItems(queue, index, { status: "unknown", detail: "回执没有可核对的分镜消息 ID，状态待确认。" });
          markUnsent(queue, index + 1);
          queue.busy = false;
          queue.stoppedUnknown = true;
          setBusy(false);
          setPaused(true);
          setUnknown(true);
          setMessage("当前分镜任务尚不能确认；不会自动重发或继续提交。可核对原任务或按同一操作重试。");
          void pollAcceptedTasks(queue);
          return;
        }
      } catch (error) {
        if (!isCurrent(queue)) return;
        if (isUnauthorized(error)) {
          propsRef.current.onUnauthorized();
          publishItems(queue, index, { status: "failed", detail: "当前登录已失效，队列已停止。" });
          markUnsent(queue, index + 1);
          stopQueue(queue, "当前登录已失效，批量视频队列已停止。");
          return;
        }
        if (!(error instanceof MediaGenerationError) || !isDefiniteFailure(error)) {
          publishItems(queue, index, { status: "unknown", detail: "受理状态未知；没有自动重发。" });
          markUnsent(queue, index + 1);
          queue.busy = false;
          queue.stoppedUnknown = true;
          setBusy(false);
          setPaused(true);
          setUnknown(true);
          setMessage("当前分镜受理状态未知；不会自动重发，也不会继续后续分镜。");
          void pollAcceptedTasks(queue);
          return;
        }
        publishItems(queue, index, { status: "failed", detail: "服务拒绝了此项，队列已停止。" });
        markUnsent(queue, index + 1);
        queue.busy = false;
        queue.stoppedUnknown = true;
        setBusy(false);
        setPaused(true);
        setUnknown(false);
        setMessage("服务拒绝了当前分镜；未发送的后续项已停止。可按同一操作核对或显式重试后再决定是否继续。");
        void pollAcceptedTasks(queue);
        return;
      }
      if (index < queue.items.length - 1) {
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, 1000);
          queue.controller.signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
        });
      }
    }
    if (!isCurrent(queue)) return;
    queue.busy = false;
    setActive(false);
    setMessage("本批逐镜头请求已提交完成。后台处理状态请到“我的任务”核对；此页不模拟视频完成。");
    void pollAcceptedTasks(queue);
  }

  async function reconcile(index: number, action: "lookup" | "retry") {
    const queue = queueRef.current;
    const item = queue?.items[index];
    if (!queue || !isCurrent(queue) || (item?.status !== "unknown" && item?.status !== "failed")
      || queue.busy || queue.run === undefined) return;
    queue.busy = true;
    setBusy(true);
    setErrorMessage(null);
    try {
      const receipt = action === "lookup"
        ? await queue.run.lookup({ operation: "video.single", idempotencyKey: item.input.idempotencyKey }, queue.controller.signal)
        : await queue.run.submitVideo(item.input, queue.controller.signal);
      if (!isCurrent(queue)) return;
      if (!acceptReceipt(queue, index, receipt)) {
        setErrorMessage("仍未取得可核对的分镜消息 ID；状态保持未知，不能继续后续项。");
        return;
      }
      queue.stoppedUnknown = false;
      setUnknown(false);
      setPaused(true);
      setMessage("原分镜任务已核对。后续分镜未发送，可明确继续原队列。");
    } catch (error) {
      if (!isCurrent(queue)) return;
      if (isUnauthorized(error)) propsRef.current.onUnauthorized();
      else if (action === "retry" && isDefiniteFailure(error)) {
        publishItems(queue, index, { status: "failed", detail: "服务再次拒绝了同一操作；请求键和冻结内容保持不变。" });
        setErrorMessage("服务拒绝了当前分镜，未发送后续项。可检查输入后结束本次跟踪，再显式建立新队列。");
      } else {
        if (action === "retry") publishItems(queue, index, { status: "unknown", detail: "同一操作仍没有可确认回执；冻结内容与原请求键未改变。" });
        setErrorMessage(action === "lookup"
          ? "暂时无法核对原任务；没有重发，可以稍后手动核对。"
          : "同一操作仍没有可确认回执；冻结的请求内容和原请求键未改变。");
      }
    } finally {
      if (isCurrent(queue)) {
        queue.busy = false;
        setBusy(false);
      }
    }
  }

  function continueQueue() {
    const queue = queueRef.current;
    if (queue === null || !isCurrent(queue) || queue.busy || queue.stoppedUnknown || queue.nextIndex >= queue.items.length) return;
    queue.items = queue.items.map((item) => item.status === "skipped"
      ? { ...item, status: "waiting" as const, detail: "等待继续提交。" } : item);
    setItems(queue.items);
    void runQueue(queue, queue.nextIndex);
  }

  function stopTracking() {
    const queue = queueRef.current;
    if (queue === null || !isCurrent(queue) || queue.busy || (!paused && queue.nextIndex < queue.items.length)) return;
    if (!window.confirm("只停止本地跟踪，不会取消服务端任务或确认费用；未发送分镜不会提交。是否继续？")) return;
    queue.controller.abort();
    queueRef.current = null;
    setActive(false);
    setPaused(false);
    setUnknown(false);
    setMessage("已停止本地跟踪。原请求可能仍在服务端运行，费用尚未确认；后续分镜未提交。");
  }

  function beginQueue(queued: readonly QueueItem[], skipped: readonly string[], scope: QueueScope): boolean {
    if (!isScopeCurrent(scope) || !canStartQueue() || scope.services.mediaGeneration === undefined) return false;
    const previousQueue = queueRef.current;
    if (previousQueue !== null) {
      queueRef.current = null;
      previousQueue.controller.abort();
    }
    preflightRef.current = null;
    setPreflight(null);
    setStatusReadingIds(new Set());
    const queue: QueueRun = {
      userId: scope.userId,
      sessionGeneration: scope.sessionGeneration,
      services: scope.services,
      seriesId: scope.seriesId,
      chapterId: scope.chapterId,
      controller: new AbortController(),
      items: [...queued],
      nextIndex: 0,
      busy: true,
      stoppedUnknown: false,
      statusAuthorizationFailed: false,
      statusReads: new Set(),
    };
    queueRef.current = queue;
    setItems(queue.items);
    setMessage(skipped.length > 0 ? "已跳过无可生成内容的分镜：" + skipped.join("、") + "。" : null);
    setErrorMessage(null);
    queue.run = scope.services.mediaGeneration.beginRun({
      actorId: queue.userId,
      isContextCurrent: () => isCurrent(queue),
    });
    void runQueue(queue, 0);
    return true;
  }

  async function startQueue() {
    if (!canStartQueue() || preflightRef.current !== null || paused || props.services.mediaGeneration === undefined
      || props.selectedChapter === null || props.selectedSeries === null || model === null
      || selectedIds.length === 0 || needsFused && fusedState !== "ready") return;
    const selected = storyboards.filter((asset) => selectedIds.includes(asset.id));
    const chosenPrompt = props.prompts.find((candidate) => candidate.id === preference.promptId && candidate.category === "video-design") ?? null;
    if (preference.promptId !== "" && chosenPrompt === null) {
      setErrorMessage("所选模板不属于当前视频提示词目录，请重新选择.");
      return;
    }
    if (options.sizes.length > 0 && !options.sizes.includes(preference.size)) {
      setErrorMessage("请选择当前模型支持的画面比例。");
      return;
    }
    if (options.resolutions.length > 0 && !options.resolutions.includes(preference.resolution)) {
      setErrorMessage("请选择当前模型支持的分辨率。");
      return;
    }
    const scope: QueueScope = {
      userId: props.userId,
      sessionGeneration: props.sessionGeneration,
      services: props.services,
      seriesId: props.selectedSeries.id,
      chapterId: props.selectedChapter.id,
    };
    const maxPromptChars = promptCharacterLimit(model);
    const queued: QueueItem[] = [];
    const compactQueued: QueueItem[] = [];
    const skipped: string[] = [];
    const overLimitLabels: string[] = [];
    const compactFailureLabels: string[] = [];
    for (const asset of selected) {
      const context = contexts.find((entry) => entry.asset.id === asset.id)?.result;
      if (context === undefined || context.kind !== "ready") {
        setErrorMessage(context?.kind === "missing-assets"
          ? frameLabel(asset) + "缺少引用图片：" + context.names.join("、")
          : context?.kind === "too-many-references"
            ? frameLabel(asset) + "的参考素材超过64张图片或5个音频上限。"
            : context?.kind === "unsafe-reference"
              ? frameLabel(asset) + "含当前视频服务不能提交的私有或不安全参考素材。"
              : frameLabel(asset) + "没有可确认的当前章节引用关系。");
        return;
      }
      const durationValue = durations[asset.id] ?? "";
      const duration = durationValue === "" ? null : Number(durationValue);
      if (options.durations.length > 0 && (duration === null || !options.durations.includes(duration))) {
        setErrorMessage(frameLabel(asset) + "尚未设置当前模型支持的独立时长。");
        return;
      }
      const full = renderBatchVideoPrompt(chosenPrompt, context.value, 0);
      if (full.prompt.trim() === "") {
        skipped.push(frameLabel(asset));
        continue;
      }
      const input: MediaGenerationVideoInput = Object.freeze({
        modelId: model.id,
        prompt: full.prompt,
        target: Object.freeze({ kind: "frame_message", chapter_id: props.selectedChapter.id, storyboard_asset_id: asset.id }),
        ...(options.sizes.length > 0 && preference.size !== "" ? { size: preference.size } : {}),
        ...(duration !== null && options.durations.length > 0 ? { duration } : {}),
        ...(options.resolutions.length > 0 && preference.resolution !== "" ? { resolution: preference.resolution } : {}),
        referenceImages: Object.freeze([...full.referenceImages]),
        referenceAudios: Object.freeze([...full.referenceAudios]),
        referenceVideos: Object.freeze([]),
        idempotencyKey: makeId(),
      });
      const fullItem: QueueItem = {
        storyboardId: asset.id,
        label: frameLabel(asset),
        duration,
        promptLength: full.prompt.length,
        fullPromptLength: full.prompt.length,
        input,
        status: "waiting",
        detail: "完整提示词等待提交。",
      };
      queued.push(fullItem);
      if (full.prompt.length > maxPromptChars) {
        overLimitLabels.push(fullItem.label + "（" + full.prompt.length + " 字符）");
        let compact = renderBatchVideoPrompt(chosenPrompt, context.value, 1);
        let trimLevel: 1 | 2 = 1;
        if (compact.prompt.length > maxPromptChars) {
          compact = renderBatchVideoPrompt(chosenPrompt, context.value, 2);
          trimLevel = 2;
        }
        if (compact.prompt.length > maxPromptChars) {
          compactFailureLabels.push(fullItem.label + "（精简后 " + compact.prompt.length + " 字符）");
        }
        compactQueued.push({
          ...fullItem,
          promptLength: compact.prompt.length,
          input: Object.freeze({ ...input, prompt: compact.prompt,
            referenceImages: Object.freeze([...compact.referenceImages]),
            referenceAudios: Object.freeze([...compact.referenceAudios]) }),
          detail: "按第 " + trimLevel + " 档精简，等待提交。",
        });
      } else {
        compactQueued.push(fullItem);
      }
    }
    if (queued.length === 0) {
      setErrorMessage(skipped.length > 0 ? "所选分镜没有正文或可用模板，未提交：" + skipped.join("、") : "没有可提交的分镜。");
      return;
    }
    const estimate = estimateBatchVideoCost(model, preference.resolution, queued.map((item) => item.duration));
    const estimateText = estimate.amount === null ? "平台费用本地估算未知（" + estimate.description + "）。"
      : "本地参考估算 " + estimate.amount + " 平台积分（" + estimate.description + "），最终以服务端受理报价/扣费为准。";
    const summary = [
      "将按镜头顺序逐项提交 " + queued.length + " 个普通视频任务，每项使用独立请求键。",
      skipped.length > 0 ? "无可生成内容的分镜将跳过：" + skipped.join("、") + "。" : "",
      estimateText,
      !model.is_system ? "个人模型的平台积分为 0；上游费用由用户自行承担。" : "",
    ].filter(Boolean).join("\n\n");
    if (overLimitLabels.length > 0) {
      const plan: QueuePreflight = { scope, items: queued, compactItems: compactQueued, skipped, maxPromptChars,
        overLimitLabels, compactFailureLabels };
      preflightRef.current = plan;
      setPreflight(plan);
      setErrorMessage(null);
      setActive(true);
      return;
    }
    if (!window.confirm(summary)) return;
    if (!beginQueue(queued, skipped, scope)) setErrorMessage("当前任务或页面范围已变化，请重新确认后再提交。");
  }

  function beginPreflightQueue(compact: boolean, expectedPlan: QueuePreflight) {
    const current = preflightRef.current;
    if (current !== expectedPlan) return;
    if (!isScopeCurrent(current.scope)) {
      preflightRef.current = null;
      setPreflight(null);
      setActive(false);
      setErrorMessage("页面范围已变化，请重新选择分镜后提交。");
      return;
    }
    if (!canStartQueue()) {
      setErrorMessage("当前任务仍在处理或等待核对，不能覆盖其请求内容。");
      return;
    }
    const selected = compact ? current.compactItems : current.items;
    preflightRef.current = null;
    setPreflight(null);
    if (!beginQueue(selected, current.skipped, current.scope)) {
      setActive(false);
      setErrorMessage("当前任务或页面范围已变化，请重新确认后再提交。");
    }
  }

  const selectedDurations = selectedIds.map((id) => {
    const value = durations[id] ?? "";
    return value === "" ? null : Number(value);
  });
  const estimate = model === null
    ? { amount: null, description: "请选择视频模型" }
    : estimateBatchVideoCost(model, preference.resolution, selectedDurations);
  const blockedSelection = selectedIds.some((id) => contexts.find((entry) => entry.asset.id === id)?.result.kind !== "ready");
  const preflightEstimate = preflight === null || model === null ? null
    : estimateBatchVideoCost(model, preference.resolution, preflight.items.map((item) => item.duration));

  return (
    <section className="media-generation-card" aria-labelledby="batch-video-title">
      <div className="media-generation-section-heading">
        <div><p className="eyebrow">批量生成</p><h2 id="batch-video-title">批量分镜视频</h2></div>
        <span>每个镜头分别创建普通视频任务，并按镜头顺序提交。</span>
      </div>
      <div className="media-generation-fields">
        <label><span>剧集</span><select disabled={busy || paused || props.loading} onChange={(event) => props.onSeriesChange(event.target.value)} value={props.selectedSeriesId}>
          <option value="">选择剧集</option>{props.series.filter((series) => series.can_enter).map((series) => <option key={series.id} value={series.id}>{series.name}</option>)}
        </select></label>
        <label><span>章节</span><select disabled={busy || paused || props.chapterLoading || props.selectedSeriesId === ""} onChange={(event) => props.onChapterChange(event.target.value)} value={props.selectedChapterId}>
          <option value="">选择章节</option>{[...props.chapters].sort((left, right) => left.order - right.order).map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title}</option>)}
        </select></label>
        <label><span>视频模型</span><select disabled={props.loading || busy || paused} onChange={(event) => {
          const nextModel = props.models.find((candidate) => candidate.id === event.target.value) ?? null;
          const nextOptions = batchVideoModelOptions(nextModel);
          updatePreference({ ...preference, modelId: event.target.value,
            size: nextOptions.defaultSize || nextOptions.sizes[0] || "",
            resolution: nextOptions.defaultResolution || nextOptions.resolutions[0] || "" });
        }} value={preference.modelId}>
          <option value="">选择视频模型</option>{props.models.filter((candidate) => candidate.category === "video").map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}
        </select></label>
        <label><span>视频提示词</span><select disabled={busy || paused} onChange={(event) => updatePreference({ ...preference, promptId: event.target.value })} value={preference.promptId}>
          <option value="">不使用模板</option>{props.prompts.filter((candidate) => candidate.category === "video-design").map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}
        </select></label>
        <label><span>画面比例</span><select disabled={busy || paused || options.sizes.length === 0} onChange={(event) => updatePreference({ ...preference, size: event.target.value })} value={preference.size}>
          {options.sizes.length === 0 ? <option value="">模型未配置画面比例</option> : options.sizes.map((size) => <option key={size} value={size}>{size}</option>)}
        </select></label>
        <label><span>分辨率</span><select disabled={busy || paused || options.resolutions.length === 0} onChange={(event) => updatePreference({ ...preference, resolution: event.target.value })} value={preference.resolution}>
          {options.resolutions.length === 0 ? <option value="">模型未配置分辨率</option> : options.resolutions.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}
        </select></label>
        <fieldset className="media-generation-batch-type media-generation-wide" disabled={busy || paused}>
          <legend>分镜资产检查范围</legend>
          <p className="media-generation-note">角色、场景、道具控制缺图检查与自动参考；分镜只控制分镜图片是否加入参考。</p>
          {(["character", "scene", "prop", "storyboard"] as const).map((field) => (
            <label key={field}><input checked={preference.checks[field]} onChange={(event) => updatePreference({
              ...preference, checks: { ...preference.checks, [field]: event.target.checked },
            })} type="checkbox" />
              <span>{field === "character" ? "角色" : field === "scene" ? "场景" : field === "prop" ? "道具" : "分镜"}</span></label>
          ))}
        </fieldset>
      </div>
      {props.chapterError !== null && <p className="media-generation-alert" role="alert">章节读取失败：{props.chapterError}</p>}
      {props.directoryError !== null && <p className="media-generation-alert" role="alert">素材目录读取失败：{props.directoryError}</p>}
      {props.directoryLoading && <p className="media-generation-note" role="status">正在读取当前章节目录…</p>}
      {needsFused && fusedState === "loading" && <p className="media-generation-note" role="status">正在核对当前剧集融合素材…</p>}
      {fusedMessage !== null && <p className="media-generation-alert" role="alert">{fusedMessage}</p>}
      {preferenceMessage !== null && <p className="media-generation-note" role="status">{preferenceMessage}</p>}
      {props.selectedChapterId !== "" && !props.directoryLoading && props.directoryError === null && (
        <fieldset className="media-generation-assets" disabled={busy || paused}>
          <legend>选择分镜 · {selectedIds.length}/{storyboards.length}</legend>
          {storyboards.length === 0 ? <p>当前章节没有可用分镜。</p> : <ol>
            {contexts.map(({ asset, result }) => {
              const selected = selectedIds.includes(asset.id);
              const issue = result.kind === "missing-assets" ? "缺少引用图片：" + result.names.join("、")
                : result.kind === "too-many-references" ? "参考素材超过64张图片或5个音频上限。"
                  : result.kind === "unsafe-reference" ? "含视频服务不能提交的私有或不安全素材。"
                    : result.kind === "missing-frame" ? "无法确认稳定分镜引用关系。" : "";
              return <li className="batch-video-row" key={asset.id}>
                <label><input checked={selected} disabled={issue !== "" || needsFused && fusedState !== "ready"}
                  onChange={(event) => {
                    const next = event.target.checked ? [...selectedIdsRef.current, asset.id] : selectedIdsRef.current.filter((id) => id !== asset.id);
                    selectedIdsRef.current = next;
                    setSelectedIds(next);
                  }} type="checkbox" />
                  <span><strong>{frameLabel(asset)}</strong><small>{issue || asset.description || "使用当前分镜正文和已启用的资产引用。"}</small></span></label>
                {options.durations.length > 0 && <label className="batch-video-duration"><span>时长</span><select aria-label={frameLabel(asset) + "时长"}
                  disabled={busy || paused} onChange={(event) => {
                    durationTouchedRef.current.add(asset.id);
                    setDurations((current) => ({ ...current, [asset.id]: event.target.value }));
                  }} value={durations[asset.id] ?? ""}>
                  <option value="">选择时长</option>{options.durations.map((duration) => <option key={duration} value={duration}>{duration} 秒</option>)}
                </select></label>}
              </li>;
            })}
          </ol>}
        </fieldset>
      )}
      {selectedIds.length > 0 && <p className="media-generation-note" role="status">
        {estimate.amount === null ? "本地费用估算未知（" + estimate.description + "）。"
          : "本地参考估算 " + estimate.amount + " 平台积分（" + estimate.description + "）；最终以服务端报价与扣费为准。"}
        {model !== null && !model.is_system ? " 个人模型的平台积分为 0；上游费用由用户自行承担。" : ""}
      </p>}
      {errorMessage !== null && <p className="media-generation-alert" role="alert">{errorMessage}</p>}
      {message !== null && <p className="media-generation-note" role={unknown ? "alert" : "status"}>{message}</p>}
      {items.length > 0 && <ol className="batch-video-queue" aria-label="逐镜头任务状态">
        {items.map((item, index) => <li key={item.storyboardId}>
          <strong>{item.label}</strong><span>{(item.duration === null ? "" : " · " + item.duration + " 秒")
            + " · " + item.promptLength + " 字符" + (item.promptLength !== item.fullPromptLength
              ? "（完整 " + item.fullPromptLength + " 字符）" : "")}</span>
          <p role={item.status === "unknown" || item.status === "failed" ? "alert" : "status"}>{item.detail}</p>
          {item.status === "accepted" && item.taskStatus !== undefined && <p role="status">
            {"任务状态：" + taskStatusLabel(item.taskStatus) + (item.taskProgress === null || item.taskProgress === undefined
              ? "" : " · " + item.taskProgress + "%")}
          </p>}
          {item.status === "accepted" && <button className="secondary-button" onClick={props.onOpenTasks} type="button">在我的任务查看</button>}
          {item.status === "accepted" && <button className="secondary-button" disabled={statusReadingIds.has(item.storyboardId)}
            onClick={() => { const queue = queueRef.current; if (queue !== null) void refreshTaskStatus(queue, index); }} type="button">
            {statusReadingIds.has(item.storyboardId) ? "正在刷新状态…" : "刷新任务状态"}
          </button>}
          {(item.status === "unknown" || item.status === "failed") && <div className="media-generation-actions">
            <button className="secondary-button" disabled={busy} onClick={() => void reconcile(index, "lookup")} type="button">核对原任务</button>
            <button className="secondary-button" disabled={busy} onClick={() => void reconcile(index, "retry")} type="button">按同一操作重试</button>
          </div>}
        </li>)}
      </ol>}
      {paused && queueRef.current !== null && <div className="media-generation-actions">
        {!queueRef.current.stoppedUnknown && <button className="secondary-button" disabled={busy || queueRef.current.nextIndex >= queueRef.current.items.length}
          onClick={continueQueue} type="button">明确继续未发送分镜</button>}
        <button className="secondary-button" disabled={busy} onClick={stopTracking} type="button">停止本地跟踪</button>
      </div>}
      {!paused && queueRef.current !== null && !queueRef.current.busy && queueRef.current.nextIndex >= queueRef.current.items.length
        && <div className="media-generation-actions"><button className="secondary-button" onClick={stopTracking} type="button">结束本地任务跟踪</button></div>}
      {preflight !== null && <div className="batch-video-preflight-backdrop">
        <section aria-labelledby="batch-video-preflight-title" aria-modal="true" className="batch-video-preflight" role="dialog">
          <h3 id="batch-video-preflight-title">确认超长提示词处理</h3>
          <p>这些分镜的完整提示词超过模型参考上限 {preflight.maxPromptChars} 字符：</p>
          <ul>{preflight.overLimitLabels.map((label) => <li key={label}>{label}</li>)}</ul>
          {preflight.skipped.length > 0 && <p>以下无正文且未选模板的分镜将跳过：{preflight.skipped.join("、")}。</p>}
          <p>“精简后生成”按旧规则先移除风格提示词和风格图，仍超限时再移除音色及资产对照文字；参考素材本身保留。</p>
          {preflight.compactFailureLabels.length > 0 && <p role="alert">精简后仍超过模型参考上限：{preflight.compactFailureLabels.join("、")}。上游可能拒绝；你仍可明确选择按精简内容尝试提交。</p>}
          <p>{preflightEstimate?.amount === null || preflightEstimate === null
            ? "本地参考估算未知。"
            : "本地参考估算 " + preflightEstimate.amount + " 平台积分；最终以服务端受理报价/扣费为准。"}
            {model !== null && !model.is_system ? " 个人模型的平台积分为 0；上游费用由用户自行承担。" : ""}</p>
          <div className="media-generation-actions">
            <button className="secondary-button" onClick={() => beginPreflightQueue(false, preflight)} type="button">完整提示词提交</button>
            <button className="primary-button"
              onClick={() => beginPreflightQueue(true, preflight)} type="button">
              {preflight.compactFailureLabels.length > 0 ? "精简后仍超限，仍然尝试" : "精简后生成"}
            </button>
            <button className="secondary-button" onClick={() => {
              if (preflightRef.current !== preflight) return;
              preflightRef.current = null;
              setPreflight(null);
              setActive(false);
            }} type="button">取消并返回修改</button>
          </div>
        </section>
      </div>}
      <div className="media-generation-actions">
        <button className="primary-button" disabled={props.loading || props.directoryLoading || props.selectedChapter === null
          || model === null || selectedIds.length === 0 || busy || paused || blockedSelection
          || props.services.mediaGeneration === undefined || needsFused && fusedState !== "ready"}
          onClick={() => void startQueue()} type="button">{busy ? "正在逐镜头提交…" : "提交 " + selectedIds.length + " 个视频任务"}</button>
        <button className="secondary-button" onClick={props.onOpenTasks} type="button">我的任务</button>
      </div>
      {props.services.mediaGeneration === undefined && <p className="media-generation-note" role="status">当前服务未接入视频任务提交，页面不会模拟受理结果。</p>}
      <p className="media-generation-note">任务逐项走普通视频接口。未知状态不会自动重发；停止本地跟踪不取消服务端任务，也不代表费用已确认。</p>
    </section>
  );
}
