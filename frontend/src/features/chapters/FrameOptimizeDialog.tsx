import { useEffect, useRef, useState } from "react";
import type { Chapter, Series, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import type {
  FrameOptimizeCapabilities,
  FrameOptimizeSubmitInput,
  FrameOptimizeTaskStatus,
} from "../../shared/api/frameOptimize";
import type { TaskRequestRecord } from "../../shared/api/taskDetails";
import { buildFrameOptimizeRequest, frameOptimizeSourceText, parseFrameOptimizePartialResult } from "./frameOptimizeContent";
import type { FrameOptimizeAssetNames as ContentAssetNames } from "./frameOptimizeContent";
import {
  beginFrameOptimizeSubmit,
  clearFrameOptimizeSubmitPreflight,
  clearFrameOptimizeTask,
  clearRejectedFrameOptimizeSubmit,
  frameOptimizeChapterAttempt,
  frameOptimizeRestoreIsBusy,
  markFrameOptimizeSubmitSent,
  recordFrameOptimizeTask,
  recordFrameOptimizeTaskForLease,
} from "./frameOptimizeSafety";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";
import "./frameOptimize.css";

export type FrameOptimizeAssetNames = ContentAssetNames;

export interface FrameOptimizeSourceSnapshot {
  chapter: Chapter;
  assets: StoryboardAsset[];
  names: FrameOptimizeAssetNames;
}

export interface FrameOptimizeSourceCheck {
  status: "verified" | "conflict" | "unconfirmed";
  message: string;
}

interface FrameOptimizeDialogProps {
  services: WorkspaceServices;
  userId: string;
  seriesId: string;
  seriesName: string;
  series: Pick<Series, "style_prompt_id" | "style_prompt">;
  chapter: Chapter;
  assets: StoryboardAsset[];
  isCurrent(): boolean;
  prepareSubmit(signal: AbortSignal): Promise<FrameOptimizeSourceSnapshot>;
  verifyTaskSource(
    status: FrameOptimizeTaskStatus,
    request: FrameOptimizeSubmitInput | null,
    signal: AbortSignal,
  ): Promise<FrameOptimizeSourceCheck>;
  restoreOriginals(indices: number[], signal: AbortSignal): Promise<FrameOptimizeSourceCheck>;
  onClose(): void;
  onUnauthorized(): void;
}

type DialogPhase = "loading" | "ready" | "submitting" | "observing" | "unknown-submit" | "unverified-task" | "finished";
type TerminalKind = "completed" | "failed" | "cancelled" | "unknown";
type RunningTaskCheck = "checking" | "clear" | "active" | "unknown";

const pollIntervalMs = 1200;
const activeStatuses = new Set(["queued", "pending", "processing", "running", "cancelling", "reconciling"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseRegisteredOptimizeRequest(value: unknown, chapterId: string): FrameOptimizeSubmitInput | null {
  if (!isRecord(value) || value.chapter_id !== chapterId
    || typeof value.model_id !== "string" || value.model_id.trim() === ""
    || (value.prompt_id !== null && value.prompt_id !== undefined && typeof value.prompt_id !== "string")
    || typeof value.enable_thinking !== "boolean"
    || !Number.isSafeInteger(value.concurrency) || (value.concurrency as number) < 3 || (value.concurrency as number) > 10
    || !Array.isArray(value.chapter_texts) || value.chapter_texts.length === 0 || !Array.isArray(value.frames)
    || value.frames.length === 0) return null;
  const indices = new Set<number>();
  let previousIndex = -1;
  const validFrames = value.frames.every((frame: unknown) => {
    if (!isRecord(frame) || !Number.isSafeInteger(frame.index) || (frame.index as number) < 0
      || (frame.index as number) <= previousIndex || indices.has(frame.index as number) || typeof frame.text !== "string"
      || typeof frame.asset_context !== "string") return false;
    previousIndex = frame.index as number;
    indices.add(frame.index as number);
    return true;
  });
  if (!validFrames || !value.chapter_texts.every((text: unknown) => typeof text === "string")) return null;
  return { ...value, prompt_id: typeof value.prompt_id === "string" ? value.prompt_id : "" } as FrameOptimizeSubmitInput;
}

function isLegacyBatchOptimizeRequestForChapter(value: unknown, chapterId: string): boolean {
  return isRecord(value) && value.chapter_id === chapterId;
}

function isOwnedByActor(item: { is_system?: boolean; user_id: string }, userId: string): boolean {
  return item.is_system === true || item.user_id === userId;
}

function capabilityMessage(capabilities: FrameOptimizeCapabilities): string | null {
  if (capabilities.enabled) return null;
  switch (capabilities.reason) {
    case "not_configured": return "服务端尚未启用分镜优化。";
    case "not_ready": return "服务端凭据登记或运行配置尚未就绪。";
    case "worker_unavailable": return "服务端分镜优化 worker 当前不可用。";
    case null: return "服务端暂时无法确认分镜优化能力。";
  }
}

function frameTargetId(frame: StoryboardFrame): string | null {
  const references = frame.storyboard;
  return Array.isArray(references) && typeof references[0] === "string" && references[0].trim() !== ""
    ? references[0]
    : null;
}

export function FrameOptimizeDialog({
  services,
  userId,
  seriesId,
  seriesName,
  series,
  chapter,
  assets,
  isCurrent,
  prepareSubmit,
  verifyTaskSource,
  restoreOriginals,
  onClose,
  onUnauthorized,
}: FrameOptimizeDialogProps) {
  const frameOptimize = services.frameOptimize;
  const creatorSettings = services.creatorSettings;
  const [capabilities, setCapabilities] = useState<FrameOptimizeCapabilities | null>(null);
  const [models, setModels] = useState<CreatorModel[]>([]);
  const [prompts, setPrompts] = useState<CreatorPrompt[]>([]);
  const [modelId, setModelId] = useState("");
  const [promptId, setPromptId] = useState("");
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(() => new Set());
  const [enableThinking, setEnableThinking] = useState(false);
  const [concurrency, setConcurrency] = useState(5);
  const [phase, setPhase] = useState<DialogPhase>("loading");
  const [runningTaskCheck, setRunningTaskCheck] = useState<RunningTaskCheck>("checking");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<FrameOptimizeTaskStatus | null>(null);
  const [taskRequest, setTaskRequest] = useState<FrameOptimizeSubmitInput | null>(null);
  const [readOnlyLegacyTask, setReadOnlyLegacyTask] = useState(false);
  const [terminal, setTerminal] = useState<TerminalKind | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [capabilityNotice, setCapabilityNotice] = useState<string | null>(null);
  const [cancelPending, setCancelPending] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [restorePending, setRestorePending] = useState(false);
  const [recheck, setRecheck] = useState(0);
  const mounted = useRef(false);
  const taskIdRef = useRef<string | null>(null);
  const taskRequestRef = useRef<FrameOptimizeSubmitInput | null>(null);
  const terminalRequestRef = useRef(0);
  const terminalDoneRef = useRef(false);
  const isCurrentRef = useRef(isCurrent);
  const unauthorizedRef = useRef(onUnauthorized);
  const prepareSubmitRef = useRef(prepareSubmit);
  const verifySourceRef = useRef(verifyTaskSource);
  const restoreRef = useRef(restoreOriginals);
  isCurrentRef.current = isCurrent;
  unauthorizedRef.current = onUnauthorized;
  prepareSubmitRef.current = prepareSubmit;
  verifySourceRef.current = verifyTaskSource;
  restoreRef.current = restoreOriginals;
  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: onClose });
  const frames = Array.isArray(chapter.content) ? chapter.content : [];
  const stableIndices = frames.flatMap((frame, index) => {
    if (!isRecord(frame)) return [];
    const id = frameTargetId(frame as StoryboardFrame);
    const matches = assets.filter((asset) => asset.id === id && asset.series_id === seriesId
      && asset.chapter_id === chapter.id && asset.frame_index === index);
    return id !== null && matches.length === 1 ? [index] : [];
  });
  const restorableIndices = stableIndices.filter((index) => {
    const frame = frames[index];
    return isRecord(frame) && typeof frame.original_text === "string" && frame.original_text !== "";
  });
  const parsedTaskResult = task === null ? null : parseFrameOptimizePartialResult(task.result);
  const restoreBlocked = frameOptimizeRestoreIsBusy(userId, seriesId, chapter.id);
  const current = () => mounted.current && isCurrentRef.current();

  function setVerifiedTask(nextTaskId: string, request: FrameOptimizeSubmitInput): void {
    taskIdRef.current = nextTaskId;
    taskRequestRef.current = request;
    setTaskId(nextTaskId);
    setTaskRequest(request);
    terminalDoneRef.current = false;
    recordFrameOptimizeTask(userId, seriesId, chapter.id, nextTaskId);
    setReadOnlyLegacyTask(false);
  }

  function setReadOnlyTask(nextTaskId: string): void {
    taskIdRef.current = nextTaskId;
    taskRequestRef.current = null;
    setTaskId(nextTaskId);
    setTaskRequest(null);
    terminalDoneRef.current = false;
    setReadOnlyLegacyTask(true);
    recordFrameOptimizeTask(userId, seriesId, chapter.id, nextTaskId);
  }

  async function readTaskRequest(taskIdToVerify: string, signal: AbortSignal): Promise<TaskRequestRecord | null> {
    if (services.getTaskRequest === undefined) return null;
    const record = await services.getTaskRequest(taskIdToVerify, signal);
    return record.id === taskIdToVerify && record.type === "batch-optimize" ? record : null;
  }

  function verifyTaskRequestRecord(record: TaskRequestRecord): FrameOptimizeSubmitInput | null {
    const request = parseRegisteredOptimizeRequest(record.request, chapter.id);
    if (request === null) return null;
    const currentFrames = Array.isArray(chapter.content) ? chapter.content : [];
    const sourceMatches = request.frames.every((requested) => {
      const currentFrame = currentFrames[requested.index];
      if (!isRecord(currentFrame) || frameOptimizeSourceText(currentFrame as StoryboardFrame) !== requested.text) return false;
      const id = frameTargetId(currentFrame as StoryboardFrame);
      return id !== null && assets.filter((asset) => asset.id === id && asset.series_id === seriesId
        && asset.chapter_id === chapter.id && asset.frame_index === requested.index).length === 1;
    });
    return sourceMatches ? request : null;
  }

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const currentRequest = () => current() && !controller.signal.aborted;
    if (frameOptimize === undefined) {
      setRunningTaskCheck("unknown");
      setPhase("ready");
      setCapabilityNotice("当前运行模式没有接入真实分镜优化服务；提交与原文恢复均已禁用，因为无法确认本章节没有运行中的任务。");
      return () => { mounted.current = false; controller.abort(); };
    }

    void frameOptimize.getCapabilities(controller.signal).then(async (value) => {
      if (!currentRequest()) return;
      setCapabilities(value);
      if (!value.enabled) return;
      if (creatorSettings === undefined) {
        setCapabilityNotice("模型与提示词目录尚未接入；新的分镜优化提交已禁用。");
        return;
      }
      const [modelRows, promptRows] = await Promise.all([
        creatorSettings.listModels("text", controller.signal),
        creatorSettings.listPrompts("frame-optimize", controller.signal),
      ]);
      if (!currentRequest()) return;
      const allowedProviders = new Set(value.supported_providers);
      const availableModels = modelRows.filter((model) => model.category === "text"
        && allowedProviders.has(model.provider as (typeof value.supported_providers)[number])
        && isOwnedByActor(model, userId));
      const availablePrompts = promptRows.filter((prompt) => prompt.category === "frame-optimize" && isOwnedByActor(prompt, userId));
      setModels(availableModels);
      setPrompts(availablePrompts);
      setModelId(availableModels[0]?.id ?? "");
      setPromptId("");
      if (availableModels.length === 0) setCapabilityNotice("没有符合服务端支持范围的文本模型；请检查模型目录。");
    }).catch((cause: unknown) => {
      if (!currentRequest()) return;
      const error = normalizeApiError(cause);
      if (error.status === 401) unauthorizedRef.current();
      setCapabilityNotice(error.detail ?? error.message);
    });

    void (async () => {
      const attempt = frameOptimizeChapterAttempt(userId, seriesId, chapter.id);
      const knownTaskId = attempt?.kind === "task" ? attempt.taskId ?? null : null;
      try {
        const running = await frameOptimize.getRunning(chapter.id, controller.signal);
        if (!currentRequest()) return;
        setRunningTaskCheck(running === null ? "clear" : "active");
        const candidateId = knownTaskId ?? running?.task_id ?? null;
        if (candidateId === null) {
          if (attempt?.kind === "submit-unknown" || attempt?.kind === "restore-unknown") {
            setPhase("unknown-submit");
            setMessage(attempt.kind === "restore-unknown"
              ? "原文恢复请求结果未确认，已冻结再次写入。请重新读取章节并人工核对。"
              : "此前提交结果未确认。服务器当前未报告运行中任务，但这不能排除迟到落库；本章节不会自动重提。请刷新页面并在任务列表核对。");
          } else {
            setPhase("ready");
          }
          return;
        }
        if (knownTaskId !== null && running !== null && running.task_id !== knownTaskId) {
          setPhase("unverified-task");
          setMessage("本章节记录的任务编号与服务器运行任务不一致；未显示目标或结果，也未允许再次提交。");
          return;
        }
        const record = await readTaskRequest(candidateId, controller.signal);
        if (!currentRequest()) return;
        const registeredRequest = record === null ? null : parseRegisteredOptimizeRequest(record.request, chapter.id);
        if (record === null || !isLegacyBatchOptimizeRequestForChapter(record.request, chapter.id)
          && registeredRequest === null) {
          setPhase("unverified-task");
          setMessage("发现运行中批量任务，但无法核对其类型、章节与 frame.optimize 登记请求；未显示任务目标或结果，也未允许重提。");
          return;
        }
        const request = registeredRequest !== null
          ? verifyTaskRequestRecord(record)
          : null;
        if (!currentRequest()) return;
        if (running !== null && running.task_id === candidateId) {
          if (request === null) {
            setReadOnlyTask(candidateId);
            setPhase("observing");
            setMessage("已核对当前账号的旧版批量优化任务及所属章节；可只读观察或请求取消，不使用其请求或输出写入章节。");
            return;
          }
          setVerifiedTask(candidateId, request);
          setPhase("observing");
          setMessage("已核对任务类型、章节和登记请求，正在读取权威任务状态。");
        } else if (knownTaskId !== null) {
          if (request === null) {
            setReadOnlyTask(candidateId);
            setPhase("observing");
            setMessage("已核对当前账号的旧版批量优化任务及所属章节；可只读观察或请求取消，不使用其请求或输出写入章节。");
            return;
          }
          setVerifiedTask(candidateId, request);
          setPhase("observing");
          setMessage("已核对先前回执对应的任务请求，正在读取权威任务状态。");
        } else {
          setPhase("ready");
        }
      } catch (cause) {
        if (!currentRequest()) return;
        const error = normalizeApiError(cause);
        if (error.status === 401) unauthorizedRef.current();
        setRunningTaskCheck("unknown");
        if (attempt !== null || knownTaskId !== null) {
          setPhase("unknown-submit");
          setMessage("无法确认本章节已有请求的落库状态；不会重发提交。" + (error.detail ?? error.message));
        } else {
          setPhase("ready");
          setMessage("暂时无法读取运行任务；在确认本章节没有活任务前，提交与原文恢复均不可用。" + (error.detail ?? error.message));
        }
      }
    })();

    return () => {
      mounted.current = false;
      controller.abort();
    };
  }, [chapter.id, creatorSettings, frameOptimize, recheck, seriesId, userId]);

  useEffect(() => {
    if (phase !== "observing" || taskId === null || frameOptimize === undefined) return undefined;
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const pollIsCurrent = () => active && current() && !controller.signal.aborted && taskIdRef.current === taskId;

    const poll = async (): Promise<void> => {
      try {
        const next = await frameOptimize.getTaskStatus(taskId, controller.signal);
        if (!pollIsCurrent()) return;
        setTask(next);
        if (activeStatuses.has(next.status)) {
          timer = setTimeout(() => void poll(), pollIntervalMs);
          return;
        }
        if (!["completed", "failed", "cancelled", "canceled"].includes(next.status)) {
          setTerminal("unknown");
          setPhase("unverified-task");
          setMessage(`服务器返回未识别的任务状态“${next.status}”；不会将它视为完成或允许重提。`);
          return;
        }
        terminalDoneRef.current = true;
        setCancelPending(false);
        const sequence = ++terminalRequestRef.current;
        const sourceCheck = await verifySourceRef.current(next, taskRequestRef.current, controller.signal);
        if (!pollIsCurrent() || sequence !== terminalRequestRef.current) return;
        let terminalRunningCheck: RunningTaskCheck = "unknown";
        let stillRunning = true;
        let runningCheckMessage = "";
        try {
          const running = await frameOptimize.getRunning(chapter.id, controller.signal);
          if (!pollIsCurrent() || sequence !== terminalRequestRef.current) return;
          stillRunning = running !== null;
          terminalRunningCheck = stillRunning ? "active" : "clear";
          if (stillRunning) runningCheckMessage = "服务器仍报告本章节有运行任务；在其消失前不能恢复原文或重新提交。";
        } catch (cause) {
          if (!pollIsCurrent() || sequence !== terminalRequestRef.current) return;
          const error = normalizeApiError(cause);
          if (error.status === 401) unauthorizedRef.current();
          runningCheckMessage = "终态后无法确认本章节已无运行任务；原文恢复仍不可用。";
        }
        setRunningTaskCheck(terminalRunningCheck);
        setTerminal(next.status === "completed" ? "completed"
          : next.status === "failed" ? "failed" : "cancelled");
        setPhase("finished");
        setMessage([sourceCheck.message, runningCheckMessage].filter((part) => part !== "").join(" "));
        if (sourceCheck.status !== "unconfirmed" && !stillRunning) {
          clearFrameOptimizeTask(userId, seriesId, chapter.id, taskId);
        }
      } catch (cause) {
        if (!pollIsCurrent()) return;
        const error = normalizeApiError(cause);
        if (error.status === 401) unauthorizedRef.current();
        setPhase("unverified-task");
        setTerminal("unknown");
        setMessage("无法读取权威任务状态；未自动重提。" + (error.detail ?? error.message));
      }
    };
    void poll();
    return () => {
      active = false;
      controller.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, [chapter.id, frameOptimize, phase, recheck, seriesId, taskId, userId]);

  async function submit(): Promise<void> {
    if (phase !== "ready" || capabilities?.enabled !== true || frameOptimize === undefined
      || modelId === "" || selectedIndices.size === 0 || restoreBlocked || runningTaskCheck !== "clear"
      || frameOptimizeChapterAttempt(userId, seriesId, chapter.id) !== null) return;
    const submitLease = beginFrameOptimizeSubmit(userId, seriesId, chapter.id);
    if (submitLease === null) return;
    setPhase("submitting");
    setMessage("提交前重新读取章节和素材，核对原始内容、顺序与稳定素材 ID…");
    const controller = new AbortController();
    let submitAttempted = false;
    let runningLookupStarted = false;
    let runningLookupConfirmed = false;
    try {
      const source = await prepareSubmitRef.current(controller.signal);
      if (!current()) return;
      const request = buildFrameOptimizeRequest(
        source.chapter,
        [...selectedIndices],
        series,
        source.names,
        { modelId, promptId, enableThinking, concurrency },
      );
      if (request === null) throw new Error("当前选择无法构造安全的分镜优化请求。");
      runningLookupStarted = true;
      const running = await frameOptimize.getRunning(chapter.id, controller.signal);
      runningLookupConfirmed = true;
      if (!current()) return;
      setRunningTaskCheck(running === null ? "clear" : "active");
      if (running !== null) {
        const taskRecord = await readTaskRequest(running.task_id, controller.signal);
        if (!current()) return;
        const registeredRequest = taskRecord === null
          ? null
          : parseRegisteredOptimizeRequest(taskRecord.request, chapter.id);
        if (taskRecord === null || !isLegacyBatchOptimizeRequestForChapter(taskRecord.request, chapter.id)
          && registeredRequest === null) {
          clearFrameOptimizeSubmitPreflight(submitLease);
          setPhase("unverified-task");
          setMessage("提交前发现未能核对的运行任务，已停止提交；不会使用任务输出或重发请求。" );
          return;
        }
        const verifiedRequest = registeredRequest !== null
          ? verifyTaskRequestRecord(taskRecord)
          : null;
        if (!current()) return;
        clearFrameOptimizeSubmitPreflight(submitLease);
        if (verifiedRequest === null) {
          setReadOnlyTask(running.task_id);
          setPhase("observing");
          setMessage("提交前发现当前账号所属章节的旧版批量优化任务；已切换到只读观察，不会重复提交。" );
          return;
        }
        setVerifiedTask(running.task_id, verifiedRequest);
        setPhase("observing");
        setMessage("提交前发现已登记的本章节任务；已核对请求并改为观察，不会重复提交。" );
        return;
      }
      if (!markFrameOptimizeSubmitSent(submitLease)) {
        setPhase("unknown-submit");
        setMessage("本章节提交占用已变化；未继续发送请求，请核对任务状态。" );
        return;
      }
      submitAttempted = true;
      const receipt = await frameOptimize.submit(request, controller.signal);
      if (!recordFrameOptimizeTaskForLease(submitLease, receipt.task_id)) {
        recordFrameOptimizeTask(userId, seriesId, chapter.id, receipt.task_id);
      }
      if (!current()) return;
      taskIdRef.current = receipt.task_id;
      taskRequestRef.current = request;
      terminalDoneRef.current = false;
      setTaskId(receipt.task_id);
      setTaskRequest(request);
      setPhase("observing");
      setMessage(`服务器已登记 ${receipt.frame_count} 个镜头，正在读取任务状态。`);
    } catch (cause) {
      if (!current()) return;
      const error = normalizeApiError(cause);
      if (runningLookupStarted && !runningLookupConfirmed) setRunningTaskCheck("unknown");
      if (error.status === 401) unauthorizedRef.current();
      if (!submitAttempted) {
        setPhase("ready");
        setMessage(error.detail ?? error.message);
        return;
      }
      if (error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response"
        || (error.status !== null && error.status >= 500)) {
        setPhase("unknown-submit");
        setMessage("提交结果暂时无法确认，未自动重发；请在任务列表核对，不要关闭后重复提交。" + (error.detail ?? error.message));
      } else {
        clearRejectedFrameOptimizeSubmit(submitLease);
        setPhase("ready");
        setMessage(error.detail ?? error.message);
      }
    } finally {
      if (!submitAttempted) clearFrameOptimizeSubmitPreflight(submitLease);
    }
  }

  async function cancel(): Promise<void> {
    if (phase !== "observing" || taskId === null || frameOptimize === undefined || cancelPending || cancelRequested
      || terminalDoneRef.current) return;
    const requestedTaskId = taskId;
    const terminalSequence = terminalRequestRef.current;
    const cancelIsCurrent = () => current() && taskIdRef.current === requestedTaskId
      && terminalRequestRef.current === terminalSequence && !terminalDoneRef.current;
    setCancelPending(true);
    setMessage("正在请求取消；回执不表示任务已经停止，继续读取服务器状态…");
    try {
      await frameOptimize.cancel(taskId, new AbortController().signal);
      if (!cancelIsCurrent()) return;
      setCancelRequested(true);
      setMessage("服务器已收到取消请求，继续读取权威状态；不代表任务已停止或已退款。");
    } catch (cause) {
      if (!cancelIsCurrent()) return;
      const error = normalizeApiError(cause);
      if (error.status === 401) unauthorizedRef.current();
      setMessage("取消请求未能确认，未自动重发；仍继续读取任务状态。" + (error.detail ?? error.message));
      setCancelRequested(true);
    } finally {
      if (cancelIsCurrent()) setCancelPending(false);
    }
  }

  async function restoreSelectedOriginals(): Promise<void> {
    if (restorePending || restoreBlocked || phase === "submitting" || phase === "observing"
      || phase === "unknown-submit" || phase === "unverified-task" || phase === "loading"
      || runningTaskCheck !== "clear" || services.frameOptimize === undefined) return;
    const targets = [...selectedIndices].filter((index) => restorableIndices.includes(index)).sort((a, b) => a - b);
    if (targets.length === 0) return;
    setRestorePending(true);
    try {
      const result = await restoreRef.current(targets, new AbortController().signal);
      if (!current()) return;
      setMessage(result.message);
    } catch (cause) {
      if (!current()) return;
      const error = normalizeApiError(cause);
      if (error.status === 401) unauthorizedRef.current();
      setMessage(error.detail ?? error.message);
    } finally {
      if (current()) setRestorePending(false);
    }
  }

  function setSelection(indices: number[]): void {
    const allowed = new Set(stableIndices);
    setSelectedIndices(new Set(indices.filter((index) => allowed.has(index)).sort((a, b) => a - b)));
  }

  const canSubmit = phase === "ready" && runningTaskCheck === "clear" && capabilities?.enabled === true && modelId !== ""
    && selectedIndices.size > 0 && models.some((model) => model.id === modelId) && !restoreBlocked;
  const canRestore = selectedIndices.size > 0 && selectedIndices.size === [...selectedIndices].filter((index) => restorableIndices.includes(index)).length
    && !restoreBlocked && runningTaskCheck === "clear" && services.frameOptimize !== undefined
    && phase !== "loading" && phase !== "submitting" && phase !== "observing"
    && phase !== "unknown-submit" && phase !== "unverified-task";

  return (
    <div className="series-dialog-backdrop chapter-editing-backdrop frame-optimize-backdrop" role="presentation">
      <section aria-labelledby="frame-optimize-title" aria-modal="true" className="series-dialog chapter-editing-dialog frame-optimize-dialog" ref={focus.dialogRef} role="dialog" tabIndex={-1}>
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">{seriesName} · {chapter.title}</p>
            <h2 id="frame-optimize-title">批量优化分镜</h2>
          </div>
          <button aria-label="关闭批量分镜优化" className="icon-button" onClick={onClose} type="button">×</button>
        </div>

        {message !== null && <p className={phase === "unknown-submit" || phase === "unverified-task" ? "frame-optimize-warning" : ""} role={phase === "unknown-submit" || phase === "unverified-task" ? "alert" : "status"}>{message}</p>}
        {capabilityNotice !== null && <p className="frame-optimize-muted" role="status">{capabilityNotice}</p>}
        {capabilities !== null && !capabilities.enabled && <p className="frame-optimize-muted">{capabilityMessage(capabilities)}</p>}

        <section aria-label="批量选择分镜" className="frame-optimize-selection">
          <div className="frame-optimize-selection-actions">
            <button className="secondary-button" onClick={() => setSelection(stableIndices)} type="button">全选可定位分镜</button>
            <button className="secondary-button" onClick={() => setSelection(stableIndices.filter((index) => !restorableIndices.includes(index)))} type="button">选择未优化</button>
            <button className="secondary-button" onClick={() => setSelection([])} type="button">清空选择</button>
          </div>
          <ul>
            {frames.map((frame, index) => {
              if (!isRecord(frame)) return <li key={index}>镜头 {index + 1}：结构无法识别，不能安全操作。</li>;
              const selected = selectedIndices.has(index);
              const hasTarget = frameTargetId(frame as StoryboardFrame) !== null;
              const text = typeof frame.text === "string" ? frame.text : "";
              const original = typeof frame.original_text === "string" ? frame.original_text : "";
              return (
                <li key={`${frameTargetId(frame as StoryboardFrame) ?? "missing"}:${index}`}>
                  <label>
                    <input checked={selected} disabled={!hasTarget || phase === "submitting" || phase === "observing"} onChange={(event) => {
                      const next = new Set(selectedIndices);
                      if (event.currentTarget.checked) next.add(index); else next.delete(index);
                      setSelection([...next]);
                    }} type="checkbox" />
                    <span>镜头 {index + 1}{original ? " · 已有原文" : " · 未保存原文"}</span>
                  </label>
                  <p>{text || "（空文字）"}</p>
                  {!hasTarget && <small>缺少可唯一核对的分镜素材 ID，已禁用。</small>}
                </li>
              );
            })}
          </ul>
        </section>

        {capabilities?.enabled === true && phase === "ready" && (
          <>
            <label className="frame-optimize-label">
              文本模型
              <select value={modelId} onChange={(event) => setModelId(event.currentTarget.value)}>
                {models.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.provider}{model.is_system ? " · 系统" : " · 我的"}</option>)}
              </select>
            </label>
            <label className="frame-optimize-label">
              分镜优化提示词（可选）
              <select value={promptId} onChange={(event) => setPromptId(event.currentTarget.value)}>
                <option value="">不指定提示词</option>
                {prompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}{prompt.is_system ? " · 系统" : " · 我的"}</option>)}
              </select>
            </label>
            <label className="frame-optimize-thinking">
              <input checked={enableThinking} onChange={(event) => setEnableThinking(event.currentTarget.checked)} type="checkbox" />
              启用模型思考
            </label>
            <label className="frame-optimize-label">
              并发设置（3–10）
              <input max={10} min={3} onChange={(event) => setConcurrency(Number(event.currentTarget.value))} type="number" value={concurrency} />
            </label>
            <p className="frame-optimize-muted">并发值作为服务器请求配置；当前 worker 的实际执行仍为串行，不代表已验证并行处理。</p>
          </>
        )}

        {(phase === "observing" || phase === "finished" || phase === "unverified-task") && taskId !== null && (
          <section aria-label="批量分镜优化任务状态" className="frame-optimize-task" role="status">
            <p>任务编号：{taskId}</p>
            {task !== null && <><p>任务状态：{task.status}</p><progress max={100} value={task.progress} />{task.progress_message && <p>{task.progress_message}</p>}</>}
            {parsedTaskResult !== null && parsedTaskResult.valid && parsedTaskResult.partial.length > 0 && (
              <section aria-label="任务返回的部分分镜结果">
                <p>任务返回的部分结果（只读展示，未据此写入章节）：</p>
                <ol>
                  {parsedTaskResult.partial.map((item) => (
                    <li key={item.index}>镜头 {item.index + 1}：{item.optimized_text}</li>
                  ))}
                </ol>
              </section>
            )}
            {parsedTaskResult?.error !== null && parsedTaskResult?.error !== undefined && (
              <p>任务错误：{parsedTaskResult.error.slice(0, 500)}</p>
            )}
            {parsedTaskResult !== null && !parsedTaskResult.valid && (
              <p>任务结果格式无法识别；不会将其用于章节写入。</p>
            )}
            {phase === "finished" && terminal !== null && <p>终态：{terminal === "completed" ? "完成" : terminal === "failed" ? "失败（可能含部分结果）" : terminal === "cancelled" ? "已取消" : "未知"}</p>}
            {taskRequest !== null
              ? <p>已核对章节 {taskRequest.chapter_id} 的 frame.optimize 登记请求；任务输出只用于对照，章节仍以重新读取的服务端真源为准。</p>
              : readOnlyLegacyTask && <p>已核对当前账号、任务类型与章节的旧版任务；仅提供状态读取和取消请求，旧请求与输出不作为章节写入依据。</p>}
          </section>
        )}

        <div className="series-dialog-actions frame-optimize-actions">
          <button className="secondary-button" onClick={onClose} type="button">关闭</button>
          {phase === "ready" && capabilities?.enabled === true && <button className="primary-button" disabled={!canSubmit} onClick={() => void submit()} type="button">提交优化</button>}
          {phase === "observing" && taskId !== null && task?.status !== "cancelling" && !cancelRequested && <button className="secondary-button" disabled={cancelPending} onClick={() => void cancel()} type="button">请求取消</button>}
          {(phase === "unknown-submit" || phase === "unverified-task") && <button className="secondary-button" onClick={() => setRecheck((value) => value + 1)} type="button">重新核对任务</button>}
          {canRestore && restorableIndices.some((index) => selectedIndices.has(index)) && <button className="secondary-button" disabled={restorePending} onClick={() => void restoreSelectedOriginals()} type="button">恢复所选镜头原文</button>}
        </div>
      </section>
    </div>
  );
}
