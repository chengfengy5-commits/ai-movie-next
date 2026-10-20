import { useEffect, useRef, useState } from "react";
import type { FrameReviewCapabilities, FrameReviewResult, FrameReviewTask } from "../../shared/api/frameReview";
import type { CreatorModel, CreatorPrompt } from "../../shared/api/creatorSettings";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import { parseFrameReviewResult } from "../../shared/api/frameReviewHttp";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";
import "./frameReview.css";

export type FrameReviewSourceCheck = { status: "applied" } | { status: "unconfirmed"; message: string };
type ReviewPhase = "idle" | "submitting" | "observing" | "unknown-submit" | "unknown-task" | "finished";
type TerminalState = "completed" | "failed" | "cancelled" | "unknown";

interface FrameReviewDialogProps {
  services: WorkspaceServices;
  userId: string;
  seriesId: string;
  chapterId: string;
  chapterTitle: string;
  frameIndex: number;
  storyboardAssetId: string;
  sourceText: string;
  sourceVersion: string;
  isCurrent(): boolean;
  onClose(): void;
  onUnauthorized(): void;
  onSourceApplied(result: FrameReviewResult): Promise<FrameReviewSourceCheck>;
}

const pollIntervalMs = 1200;
const activeTaskStatuses = new Set(["queued", "pending", "processing", "running", "cancelling", "reconciling"]);

function capabilityMessage(capabilities: FrameReviewCapabilities): string | null {
  if (capabilities.enabled) return null;
  switch (capabilities.reason) {
    case "not_configured":
      return "服务端尚未接入分镜复核。";
    case "not_ready":
      return "服务端的凭据登记或运行配置尚未就绪。";
    case "worker_unavailable":
      return "分镜复核 worker 当前未运行，请稍后重试。";
    case null:
      return "服务端暂时无法确认分镜复核状态。";
  }
}

function isOwnedByActor(item: { is_system?: boolean; user_id: string }, userId: string): boolean {
  return item.is_system === true || item.user_id === userId;
}

export function FrameReviewDialog({
  services,
  userId,
  seriesId,
  chapterId,
  chapterTitle,
  frameIndex,
  storyboardAssetId,
  sourceText,
  sourceVersion,
  isCurrent,
  onClose,
  onUnauthorized,
  onSourceApplied,
}: FrameReviewDialogProps) {
  const frameReview = services.frameReview;
  const creatorSettings = services.creatorSettings;
  const [capabilities, setCapabilities] = useState<FrameReviewCapabilities | null>(null);
  const [models, setModels] = useState<CreatorModel[]>([]);
  const [prompts, setPrompts] = useState<CreatorPrompt[]>([]);
  const [modelId, setModelId] = useState("");
  const [promptId, setPromptId] = useState("");
  const [errorReason, setErrorReason] = useState("");
  const [assetContext, setAssetContext] = useState("");
  const [previousContext, setPreviousContext] = useState("");
  const [enableThinking, setEnableThinking] = useState(false);
  const [phase, setPhase] = useState<ReviewPhase>("idle");
  const [terminal, setTerminal] = useState<TerminalState | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<FrameReviewTask | null>(null);
  const [receipt, setReceipt] = useState<{ modelName: string; isSystem: boolean; creditCost: number } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [cancelAcknowledged, setCancelAcknowledged] = useState(false);
  const [cancelUncertain, setCancelUncertain] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  const [recheckSequence, setRecheckSequence] = useState(0);
  const mounted = useRef(false);
  const phaseRef = useRef<ReviewPhase>("idle");
  const taskIdRef = useRef<string | null>(null);
  const taskGenerationRef = useRef(0);
  const taskFinalObservedRef = useRef(false);
  const cancelRequestSequenceRef = useRef(0);
  const cancelPendingRef = useRef(false);
  const isCurrentRef = useRef(isCurrent);
  const unauthorizedRef = useRef(onUnauthorized);
  const sourceAppliedRef = useRef(onSourceApplied);
  isCurrentRef.current = isCurrent;
  unauthorizedRef.current = onUnauthorized;
  sourceAppliedRef.current = onSourceApplied;
  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: onClose });

  function transitionPhase(nextPhase: ReviewPhase): void {
    if (phaseRef.current !== nextPhase) taskGenerationRef.current += 1;
    phaseRef.current = nextPhase;
    setPhase(nextPhase);
  }

  function assignTaskId(nextTaskId: string): void {
    if (taskIdRef.current !== nextTaskId) taskGenerationRef.current += 1;
    taskIdRef.current = nextTaskId;
    setTaskId(nextTaskId);
  }

  function invalidateCancelResponse(): void {
    taskGenerationRef.current += 1;
    taskFinalObservedRef.current = true;
    cancelPendingRef.current = false;
    setCancelPending(false);
  }

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const current = () => mounted.current && !controller.signal.aborted && isCurrentRef.current();
    if (frameReview === undefined) {
      setMessage("当前运行模式没有接入分镜复核服务。");
      return () => {
        mounted.current = false;
        controller.abort();
      };
    }
    if (creatorSettings === undefined) {
      setMessage("模型与提示词目录尚未接入。");
      return () => {
        mounted.current = false;
        controller.abort();
      };
    }

    void frameReview.getCapabilities(controller.signal).then(async (value) => {
      if (!current()) return;
      setCapabilities(value);
      const disabledMessage = capabilityMessage(value);
      if (disabledMessage !== null) {
        setMessage(disabledMessage);
        return;
      }
      const [modelRows, promptRows] = await Promise.all([
        creatorSettings.listModels("text", controller.signal),
        creatorSettings.listPrompts("ai-review", controller.signal),
      ]);
      if (!current()) return;
      const allowedProviders = new Set(value.supported_providers);
      const availableModels = modelRows.filter((model) => (
        model.category === "text"
        && allowedProviders.has(model.provider as (typeof value.supported_providers)[number])
        && isOwnedByActor(model, userId)
      ));
      const availablePrompts = promptRows.filter((prompt) => (
        prompt.category === "ai-review" && isOwnedByActor(prompt, userId)
      ));
      setModels(availableModels);
      setPrompts(availablePrompts);
      setModelId(availableModels[0]?.id ?? "");
      setPromptId(availablePrompts[0]?.id ?? "");
      if (availableModels.length === 0) setMessage("没有可用于分镜复核的已支持文本模型，请检查模型配置。");
      else if (availablePrompts.length === 0) setMessage("没有可用于分镜复核的提示词，请检查 AI 复核提示词配置。");
      else setMessage(null);
    }).catch((cause: unknown) => {
      if (!current()) return;
      const error = normalizeApiError(cause);
      if (error.status === 401) unauthorizedRef.current();
      setMessage(error.status === 404
        ? "当前服务尚未接入分镜复核接口。"
        : error.detail ?? error.message);
    });

    return () => {
      mounted.current = false;
      controller.abort();
    };
  }, [creatorSettings, frameReview, userId]);

  useEffect(() => {
    if (phase !== "observing" || taskId === null || frameReview === undefined) return undefined;
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const current = () => active && !controller.signal.aborted && mounted.current && isCurrentRef.current();

    const poll = async (): Promise<void> => {
      try {
        const nextTask = await frameReview.getTask(taskId, controller.signal);
        if (!current()) return;
        setTask(nextTask);
        if (nextTask.status === "completed") {
          invalidateCancelResponse();
          const result = parseFrameReviewResult(nextTask, {
            chapterId,
            frameIndex,
            promptId,
          });
          if (result.original_text !== sourceText) {
            setTerminal("unknown");
            transitionPhase("finished");
            setMessage("任务已完成，但服务器读取到的原文与打开复核时不同；请刷新章节并核对镜头。");
            return;
          }
          const sourceCheck = await sourceAppliedRef.current(result);
          if (!current()) return;
          setTerminal(sourceCheck.status === "applied" ? "completed" : "unknown");
          transitionPhase("finished");
          setMessage(sourceCheck.status === "applied"
            ? "服务器已回写镜头；章节源数据已重新读取并核对。"
            : sourceCheck.message);
          return;
        }
        if (nextTask.status === "failed") {
          invalidateCancelResponse();
          setTerminal("failed");
          transitionPhase("finished");
          setMessage(nextTask.progress_message ?? "服务器报告此任务失败。");
          return;
        }
        if (nextTask.status === "cancelled" || nextTask.status === "canceled") {
          invalidateCancelResponse();
          setTerminal("cancelled");
          transitionPhase("finished");
          setMessage("服务器已确认任务取消；此状态不说明是否退款。");
          return;
        }
        if (!activeTaskStatuses.has(nextTask.status)) {
          invalidateCancelResponse();
          setTerminal("unknown");
          transitionPhase("unknown-task");
          setMessage("服务器返回了无法识别的任务状态。任务没有被当作完成，请按任务编号核对。");
          return;
        }
        timer = setTimeout(() => void poll(), pollIntervalMs);
      } catch (cause) {
        if (!current()) return;
        const error = normalizeApiError(cause);
        invalidateCancelResponse();
        if (error.status === 401) unauthorizedRef.current();
        transitionPhase("unknown-task");
        setTerminal("unknown");
        setMessage(error.status === 404
          ? "任务状态读取接口暂不可用。请按任务编号到我的任务中核对。"
          : `任务状态暂时无法确认。请按任务编号核对；未自动重发提交。${error.detail ?? error.message}`);
      }
    };

    void poll();
    return () => {
      active = false;
      controller.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, [chapterId, frameIndex, frameReview, phase, promptId, recheckSequence, sourceText, taskId]);

  async function submit(): Promise<void> {
    if (phase !== "idle" || frameReview === undefined || modelId === "" || promptId === "" || errorReason.trim() === "") return;
    transitionPhase("submitting");
    setMessage(null);
    try {
      const submitted = await frameReview.submit({
        chapter_id: chapterId,
        frame_index: frameIndex,
        prompt_id: promptId,
        model_id: modelId,
        message_id: "",
        error_reason: errorReason,
        asset_context: assetContext,
        prev_context: previousContext,
        enable_thinking: enableThinking,
      }, new AbortController().signal);
      if (!mounted.current || !isCurrent()) return;
      assignTaskId(submitted.task_id);
      setReceipt({ modelName: submitted.model_name, isSystem: submitted.is_system, creditCost: submitted.credit_cost });
      transitionPhase("observing");
    } catch (cause) {
      if (!mounted.current || !isCurrent()) return;
      const error = normalizeApiError(cause);
      if (error.status === 401) unauthorizedRef.current();
      if (error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response"
        || (error.status !== null && error.status >= 500)) {
        transitionPhase("unknown-submit");
        setMessage("提交结果暂时无法确认，未自动重发。请在我的任务中核查；不要根据相似记录猜任务编号。");
      } else {
        transitionPhase("idle");
        setMessage(error.detail ?? error.message);
      }
    }
  }

  async function cancel(): Promise<void> {
    if (phase !== "observing" || taskId === null || frameReview === undefined
      || cancelAcknowledged || cancelUncertain || cancelPendingRef.current
      || taskFinalObservedRef.current) return;
    const requestTaskId = taskId;
    const taskGeneration = taskGenerationRef.current;
    const requestSequence = ++cancelRequestSequenceRef.current;
    const isCurrentCancelRequest = () => mounted.current
      && isCurrentRef.current()
      && cancelRequestSequenceRef.current === requestSequence
      && taskIdRef.current === requestTaskId
      && phaseRef.current === "observing"
      && taskGenerationRef.current === taskGeneration;
    cancelPendingRef.current = true;
    setCancelPending(true);
    setMessage("正在发送取消请求…");
    try {
      await frameReview.cancel(requestTaskId, new AbortController().signal);
      if (!isCurrentCancelRequest()) return;
      cancelPendingRef.current = false;
      setCancelPending(false);
      setCancelAcknowledged(true);
      setMessage("服务器已收到取消请求，继续观察实际任务状态；这不代表任务已停止或已退款。");
    } catch (cause) {
      if (!isCurrentCancelRequest()) return;
      cancelPendingRef.current = false;
      setCancelPending(false);
      const error = normalizeApiError(cause);
      if (error.status === 401) unauthorizedRef.current();
      if (error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response") {
        setCancelUncertain(true);
        setMessage("取消请求结果暂时无法确认，未自动重发；继续观察任务状态。");
      } else {
        setMessage(error.detail ?? error.message);
      }
    }
  }

  const selectedModel = models.find((model) => model.id === modelId) ?? null;
  const selectedPrompt = prompts.find((prompt) => prompt.id === promptId) ?? null;
  const capabilityError = capabilities === null ? message : capabilityMessage(capabilities);
  const busy = phase === "submitting" || phase === "observing";
  const taskStatusText = task === null ? "等待服务器任务" : `任务状态：${task.status}`;

  return (
    <div className="series-dialog-backdrop chapter-editing-backdrop frame-review-backdrop" role="presentation">
      <section
        aria-labelledby="frame-review-title"
        aria-modal="true"
        className="series-dialog chapter-editing-dialog frame-review-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">{chapterTitle || "未命名章节"} · 镜头 {frameIndex + 1}</p>
            <h2 id="frame-review-title">AI 分镜复核</h2>
          </div>
          <button aria-label="关闭分镜复核" className="icon-button" onClick={onClose} type="button">×</button>
        </div>

        <p className="frame-review-source-label">当前镜头文字</p>
        <div
          className="frame-review-source"
          data-series-id={seriesId}
          data-storyboard-id={storyboardAssetId}
          data-source-version={sourceVersion}
        >
          {sourceText || "（空文字）"}
        </div>
        <p className="chapter-editing-help">
          提交后由服务端应用结果，并按现有报价与应用后计费规则处理。此窗口只观察服务器结果，不会再次保存镜头正文。
        </p>

        {capabilityError !== null && <p className="series-dialog-error" role="status">{capabilityError}</p>}

        {capabilities?.enabled === true && phase === "idle" && (
          <>
            <label className="frame-review-label">
              文本模型
              <select value={modelId} onChange={(event) => setModelId(event.currentTarget.value)}>
                {models.map((model) => (
                  <option key={model.id} value={model.id}>{model.name} · {model.provider}{model.is_system ? " · 系统" : " · 我的"}</option>
                ))}
              </select>
            </label>
            <label className="frame-review-label">
              AI 复核提示词
              <select value={promptId} onChange={(event) => setPromptId(event.currentTarget.value)}>
                {prompts.map((prompt) => (
                  <option key={prompt.id} value={prompt.id}>{prompt.name}{prompt.is_system ? " · 系统" : " · 我的"}</option>
                ))}
              </select>
            </label>
            <label className="frame-review-label">
              希望核对的问题
              <textarea onChange={(event) => setErrorReason(event.currentTarget.value)} rows={3} value={errorReason} />
            </label>
            <label className="frame-review-label">
              素材上下文（可选）
              <textarea onChange={(event) => setAssetContext(event.currentTarget.value)} rows={2} value={assetContext} />
            </label>
            <label className="frame-review-label">
              前文上下文（可选）
              <textarea onChange={(event) => setPreviousContext(event.currentTarget.value)} rows={2} value={previousContext} />
            </label>
            <label className="frame-review-thinking">
              <input checked={enableThinking} onChange={(event) => setEnableThinking(event.currentTarget.checked)} type="checkbox" />
              启用模型思考
            </label>
          </>
        )}

        {phase === "submitting" && <p role="status">正在向服务端提交一次复核请求…</p>}
        {phase === "observing" && (
          <section aria-label="分镜复核任务状态" className="frame-review-task" role="status">
            <p>{taskStatusText}</p>
            {task?.progress !== null && task?.progress !== undefined && <progress max={100} value={task.progress} />}
            {task?.progress_message && <p>{task.progress_message}</p>}
            {message !== null && <p className="frame-review-request-state">{message}</p>}
            {receipt !== null && (
              <p>模型：{receipt.modelName}（{receipt.isSystem ? "系统" : "我的"}）；服务器报价：{receipt.creditCost}</p>
            )}
            {taskId !== null && <p className="frame-review-task-id">任务编号：{taskId}。可在“我的任务”中按此编号核对。</p>}
          </section>
        )}
        {phase === "unknown-submit" && <p className="frame-review-unknown" role="alert">{message}</p>}
        {phase === "unknown-task" && taskId !== null && (
          <p className="frame-review-unknown" role="alert">任务编号：{taskId}。{message}</p>
        )}
        {phase === "finished" && terminal === "completed" && task !== null && (
          <section className="frame-review-result" aria-label="复核完成结果">
            <p role="status">{message}</p>
            <p>服务器报价：{receipt?.creditCost ?? task.credit_cost}</p>
            <h3>服务器回写后的镜头文字</h3>
            <div>{task.result === null ? "无可显示结果" : (() => {
              try {
                const value = JSON.parse(task.result) as { optimized_text?: unknown };
                return typeof value.optimized_text === "string" ? value.optimized_text : "结果无法显示";
              } catch {
                return "结果无法显示";
              }
            })()}</div>
          </section>
        )}
        {phase === "finished" && terminal !== "completed" && (
          <p className={terminal === "failed" ? "series-dialog-error" : "frame-review-unknown"} role="status">{message}</p>
        )}
        {phase === "unknown-task" && taskId !== null && (
          <button className="secondary-button" onClick={() => {
            taskFinalObservedRef.current = false;
            transitionPhase("observing");
            setRecheckSequence((value) => value + 1);
          }} type="button">重新读取任务状态</button>
        )}
        {message !== null && phase === "idle" && capabilities?.enabled === true && (
          <p className="series-dialog-error" role="alert">{message}</p>
        )}
        {phase === "finished" && taskId !== null && <p className="frame-review-task-id">任务编号：{taskId}。可在“我的任务”中按此编号核对。</p>}

        <div className="series-dialog-actions frame-review-actions">
          <button className="secondary-button" onClick={onClose} type="button">关闭</button>
          {phase === "idle" && capabilities?.enabled === true && (
            <button
              className="primary-button"
              disabled={models.length === 0 || prompts.length === 0 || modelId === "" || promptId === "" || errorReason.trim() === "" || selectedModel === null || selectedPrompt === null}
              onClick={() => void submit()}
              type="button"
            >提交复核</button>
          )}
          {busy && taskId !== null && !cancelAcknowledged && !cancelUncertain && (
            <button className="secondary-button" disabled={cancelPending} onClick={() => void cancel()} type="button">请求取消</button>
          )}
        </div>
      </section>
    </div>
  );
}
