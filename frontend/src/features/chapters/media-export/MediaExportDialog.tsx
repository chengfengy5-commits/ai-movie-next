import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Chapter } from "../../../shared/api/contracts";
import type { PersonalRoughCutSnapshot } from "../../../shared/api/personalRoughCut";
import {
  MediaExportSinkError,
  type MediaExportEligibility,
  type MediaExportQueue,
  type MediaExportQueueItem,
  type MediaExportSink,
} from "../../../shared/api/mediaExport";
import type { MediaExportReaderError } from "../../../shared/api/mediaExportReader";
import type { MediaExportSessionTransport } from "../../../shared/api/mediaExportSession";
import { useAuth } from "../../auth/AuthProvider";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import {
  buildChapterMediaExportQueue,
  buildSavedRoughCutMediaExportQueue,
  createFailedMediaExportRetryQueue,
  startMediaExport,
  type MediaExportRun,
} from "./queue";
import {
  createBrowserMediaExportSink,
  createDirectoryMediaExportSink,
  type DirectoryHandleLike,
} from "./sinks";
import "./mediaExport.css";

type ExportSourceMode = "chapter" | "rough-cut";
type ExportPhase = "review" | "confirmed" | "preparing" | "running" | "complete";

interface MediaExportDialogProps {
  readonly chapter: Chapter;
  readonly seriesName: string;
  readonly sourceMode: ExportSourceMode;
  readonly sourceRevision?: string | null;
  readonly roughCutSnapshot?: PersonalRoughCutSnapshot | null;
  readonly isSourceCurrent?: () => boolean;
  readonly onClose: () => void;
  readonly onUnauthorized?: (error: MediaExportReaderError) => void;
}

interface DirectoryPickerWindow {
  showDirectoryPicker?: (options?: { readonly mode?: "readwrite" }) => Promise<DirectoryHandleLike>;
}

interface CapturedPageScope {
  readonly userId: string;
  readonly sessionGeneration: number;
  readonly services: ReturnType<typeof useAuth>["services"];
  readonly chapterId: string;
  readonly seriesId: string;
  readonly seriesName: string;
  readonly chapterTitle: string;
  readonly chapterUpdatedAt: string;
  readonly chapterContent: Chapter["content"];
  readonly sourceRevision: string | null;
  readonly sourceMode: ExportSourceMode;
  readonly roughCutSnapshot: PersonalRoughCutSnapshot | null | undefined;
}

let fallbackRunSequence = 0;

function createRunId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  const identity = uuid ?? `${Date.now()}-${++fallbackRunSequence}`;
  return `media-export-${identity}`;
}

function currentSourceRevision(props: MediaExportDialogProps): string | null {
  return props.sourceRevision === undefined ? props.chapter.updated_at : props.sourceRevision;
}

function allFrameIndexes(chapter: Chapter): number[] {
  return Array.from({ length: chapter.content?.length ?? 0 }, (_, index) => index);
}

function defaultChapterSelection(chapter: Chapter, seriesName: string, sourceRevision: string | null): Set<number> {
  const queue = buildChapterMediaExportQueue({
    runId: "media-export-selection-preview",
    chapter,
    seriesName,
    selectedFrameIndexes: allFrameIndexes(chapter),
    sourceRevision,
  });
  return new Set(queue.items
    .filter((item) => item.eligibility === "ready")
    .map((item) => item.frameIndex));
}

function statusForEligibility(eligibility: MediaExportEligibility, selected: boolean): string {
  switch (eligibility) {
    case "ready":
      return selected ? "已选，可导出" : "已排除";
    case "excluded":
      return "已排除";
    case "pending":
      return "粗剪仍在等待处理";
    case "missing":
      return "缺少视频";
    case "duplicate":
      return "素材身份重复，无法确认";
    case "invalid":
      return "素材身份无效";
  }
}

function statusForItem(item: MediaExportQueueItem, selected: boolean): string {
  switch (item.state) {
    case "resolving":
      return "正在解析下载地址";
    case "reading":
      return "正在读取视频字节";
    case "writing":
      return "正在写入目标文件";
    case "saved":
      return "已保存";
    case "download-started":
      return "已发起浏览器下载，磁盘结果未确认";
    case "failed":
      return item.message ?? "失败，可确认新目标后手动重试";
    case "unknown":
      return item.message ?? "保存结果未知，不能自动覆盖或重试";
    case "stopped":
      return item.message ?? "已停止";
    case "not-started":
    case "queued":
      return statusForEligibility(item.eligibility, selected);
  }
}

function frameDescription(
  item: MediaExportQueueItem,
  chapter: Chapter,
  roughCutSnapshot: PersonalRoughCutSnapshot | null | undefined,
  mode: ExportSourceMode,
): string {
  const rawText = mode === "chapter"
    ? chapter.content?.[item.frameIndex]?.text
    : roughCutSnapshot?.frames[item.sourceIndex]?.text;
  const text = typeof rawText === "string" ? rawText.trim() : "";
  return text.length > 0 ? text.slice(0, 120) : `分镜 ${item.frameIndex + 1}`;
}

function hasReadyItems(queue: MediaExportQueue): boolean {
  return queue.items.some((item) => item.eligibility === "ready" && item.state === "queued");
}

function canUseDirectoryPicker(): boolean {
  return typeof (window as unknown as DirectoryPickerWindow).showDirectoryPicker === "function";
}

export function MediaExportDialog({
  chapter,
  seriesName,
  sourceMode: initialSourceMode,
  sourceRevision,
  roughCutSnapshot,
  isSourceCurrent,
  onClose,
  onUnauthorized,
}: MediaExportDialogProps) {
  const { services, state, sessionGeneration } = useAuth();
  const userId = state.status === "authenticated" ? state.user.id : null;
  const dialogProps: MediaExportDialogProps = {
    chapter,
    seriesName,
    sourceMode: initialSourceMode,
    ...(sourceRevision === undefined ? {} : { sourceRevision }),
    ...(roughCutSnapshot === undefined ? {} : { roughCutSnapshot }),
    ...(isSourceCurrent === undefined ? {} : { isSourceCurrent }),
    onClose,
    ...(onUnauthorized === undefined ? {} : { onUnauthorized }),
  };
  const propsRef = useRef<MediaExportDialogProps>(dialogProps);
  propsRef.current = dialogProps;
  const authRef = useRef({ services, state, sessionGeneration });
  authRef.current = { services, state, sessionGeneration };
  const mountedRef = useRef(false);
  const identityInvalidatedRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const runRef = useRef<MediaExportRun | null>(null);
  const sessionTransportRef = useRef<MediaExportSessionTransport | null>(null);
  const previousScopeRef = useRef<CapturedPageScope | null>(null);
  const sourceModeRef = useRef<ExportSourceMode>("chapter");
  const [sourceMode, setSourceMode] = useState<ExportSourceMode>(initialSourceMode);
  sourceModeRef.current = sourceMode;
  const [phase, setPhase] = useState<ExportPhase>("review");
  const [draftRunId, setDraftRunId] = useState(createRunId);
  const [selectedFrameIndexes, setSelectedFrameIndexes] = useState<Set<number>>(
    () => defaultChapterSelection(chapter, seriesName, currentSourceRevision(propsRef.current)),
  );
  const [frozenQueue, setFrozenQueue] = useState<MediaExportQueue | null>(null);
  const [resultQueue, setResultQueue] = useState<MediaExportQueue | null>(null);
  const [confirmationChecked, setConfirmationChecked] = useState(false);
  const [retrySelectedIds, setRetrySelectedIds] = useState<Set<string>>(new Set());
  const [retryConfirmed, setRetryConfirmed] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [runNotice, setRunNotice] = useState<string | null>(null);

  const focus = useSeriesDialogFocus({
    active: true,
    canDismiss: true,
    onDismiss: () => {
      controllerRef.current?.abort();
      runRef.current?.abort();
      propsRef.current.onClose();
    },
  });

  const invalidateIfSessionChanged = useCallback((): boolean => {
    const transport = sessionTransportRef.current;
    if (transport === null || transport.isCurrent()) {
      return false;
    }
    controllerRef.current?.abort();
    runRef.current?.abort();
    sessionTransportRef.current = null;
    controllerRef.current = null;
    runRef.current = null;
    identityInvalidatedRef.current = true;
    setFrozenQueue(null);
    setResultQueue(null);
    setPhase("review");
    setErrorMessage("登录凭据已变化，当前媒体导出已停止。请核对登录状态后重新开始。");
    propsRef.current.onClose();
    return true;
  }, []);

  useEffect(() => {
    const onStorage = () => {
      invalidateIfSessionChanged();
    };
    window.addEventListener("storage", onStorage);
    try {
      createTransport(sourceModeRef.current);
    } catch {
      // The dialog can still explain the session error when the user starts an export.
    }
    return () => {
      window.removeEventListener("storage", onStorage);
    };
  }, [invalidateIfSessionChanged]);

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
      runRef.current?.abort();
      sessionTransportRef.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    const nextScope: CapturedPageScope | null = userId === null
      ? null
      : {
        userId,
        sessionGeneration,
        services,
        chapterId: chapter.id,
        seriesId: chapter.series_id,
        seriesName,
        chapterTitle: chapter.title,
        chapterUpdatedAt: chapter.updated_at,
        chapterContent: chapter.content,
        sourceRevision: currentSourceRevision(propsRef.current),
        sourceMode: sourceModeRef.current,
        roughCutSnapshot,
      };
    const previous = previousScopeRef.current;
    const changed = previous !== null && (
      nextScope === null
      || previous.userId !== nextScope.userId
      || previous.sessionGeneration !== nextScope.sessionGeneration
      || previous.services !== nextScope.services
      || previous.chapterId !== nextScope.chapterId
      || previous.seriesId !== nextScope.seriesId
      || previous.seriesName !== nextScope.seriesName
      || previous.chapterTitle !== nextScope.chapterTitle
      || previous.chapterUpdatedAt !== nextScope.chapterUpdatedAt
      || previous.chapterContent !== nextScope.chapterContent
      || previous.sourceRevision !== nextScope.sourceRevision
      || previous.roughCutSnapshot !== nextScope.roughCutSnapshot
    );
    previousScopeRef.current = nextScope;
    if (!changed) return;

    controllerRef.current?.abort();
    runRef.current?.abort();
    controllerRef.current = null;
    runRef.current = null;
    setSourceMode(propsRef.current.sourceMode);
    setSelectedFrameIndexes(defaultChapterSelection(chapter, seriesName, currentSourceRevision(propsRef.current)));
    setDraftRunId(createRunId());
    setFrozenQueue(null);
    setResultQueue(null);
    setPhase("review");
    setConfirmationChecked(false);
    setRetrySelectedIds(new Set());
    setRetryConfirmed(false);
    setErrorMessage(null);
    setRunNotice(null);
  }, [
    chapter.content,
    chapter.id,
    chapter.series_id,
    chapter.title,
    chapter.updated_at,
    roughCutSnapshot,
    seriesName,
    services,
    sessionGeneration,
    sourceRevision,
    userId,
  ]);

  const selectedIndexes = useMemo(
    () => Array.from(selectedFrameIndexes).sort((left, right) => left - right),
    [selectedFrameIndexes],
  );
  const chapterAllQueue = buildChapterMediaExportQueue({
    runId: draftRunId,
    chapter,
    seriesName,
    selectedFrameIndexes: allFrameIndexes(chapter),
    sourceRevision: currentSourceRevision(propsRef.current),
  });
  const chapterProjection = buildChapterMediaExportQueue({
    runId: draftRunId,
    chapter,
    seriesName,
    selectedFrameIndexes: selectedIndexes,
    sourceRevision: currentSourceRevision(propsRef.current),
  });
  let roughCutProjection: MediaExportQueue | null = null;
  let roughCutMessage: string | null = null;
  if (roughCutSnapshot !== undefined && roughCutSnapshot !== null) {
    try {
      roughCutProjection = buildSavedRoughCutMediaExportQueue({
        runId: draftRunId,
        chapter,
        seriesName,
        snapshot: roughCutSnapshot,
      });
    } catch {
      roughCutMessage = "当前粗剪未保存或不属于本章，不能创建导出队列。";
    }
  }
  const activeProjection = sourceMode === "chapter" ? chapterProjection : roughCutProjection;
  const queueForDisplay = resultQueue ?? frozenQueue ?? (
    sourceMode === "chapter" ? chapterAllQueue : roughCutProjection
  );
  const failedItems = (resultQueue?.items ?? []).filter((item) => (
    item.eligibility === "ready" && item.state === "failed"
  ));
  const chapterReadyCount = chapterAllQueue.items.filter((item) => item.eligibility === "ready").length;
  const chapterMissingCount = chapterAllQueue.items.filter((item) => item.eligibility === "missing").length;
  const chapterDuplicateCount = chapterAllQueue.items.filter((item) => item.eligibility === "duplicate").length;
  const chapterExcludedCount = chapterAllQueue.items.filter((item) => (
    item.eligibility === "ready" && !selectedFrameIndexes.has(item.frameIndex)
  )).length;

  function createTransport(capturedMode: ExportSourceMode): MediaExportSessionTransport {
    const auth = authRef.current;
    const authState = auth.state;
    const apiBaseUrl = auth.services.apiBaseUrl;
    const props = propsRef.current;
    if (identityInvalidatedRef.current
      || authState.status !== "authenticated"
      || apiBaseUrl === null
      || auth.services.createMediaExportSession === undefined) {
      throw new Error("媒体导出需要已登录的 API 服务会话。");
    }
    const captured: CapturedPageScope = {
      userId: authState.user.id,
      sessionGeneration: auth.sessionGeneration,
      services: auth.services,
      chapterId: props.chapter.id,
      seriesId: props.chapter.series_id,
      seriesName: props.seriesName,
      chapterTitle: props.chapter.title,
      chapterUpdatedAt: props.chapter.updated_at,
      chapterContent: props.chapter.content,
      sourceRevision: currentSourceRevision(props),
      sourceMode: capturedMode,
      roughCutSnapshot: props.roughCutSnapshot,
    };
    const isCurrent = (): boolean => {
      const currentAuth = authRef.current;
      const currentProps = propsRef.current;
      if (
        !mountedRef.current
        || identityInvalidatedRef.current
        || currentAuth.state.status !== "authenticated"
        || currentAuth.state.user.id !== captured.userId
        || currentAuth.sessionGeneration !== captured.sessionGeneration
        || currentAuth.services !== captured.services
        || currentProps.chapter.id !== captured.chapterId
        || currentProps.chapter.series_id !== captured.seriesId
        || currentProps.seriesName !== captured.seriesName
        || currentProps.chapter.title !== captured.chapterTitle
        || currentProps.chapter.updated_at !== captured.chapterUpdatedAt
        || currentProps.chapter.content !== captured.chapterContent
        || currentSourceRevision(currentProps) !== captured.sourceRevision
        || sourceModeRef.current !== captured.sourceMode
      ) {
        return false;
      }
      if (captured.sourceMode === "rough-cut" && currentProps.roughCutSnapshot !== captured.roughCutSnapshot) {
        return false;
      }
      try {
        return currentProps.isSourceCurrent?.() !== false;
      } catch {
        return false;
      }
    };
    const transport = auth.services.createMediaExportSession(isCurrent);
    sessionTransportRef.current = transport;
    return transport;
  }

  function updateQueueItem(item: MediaExportQueueItem, transport: MediaExportSessionTransport): void {
    if (!transport.isCurrent()) return;
    setResultQueue((current) => {
      if (current === null || !transport.isCurrent()) return current;
      return {
        ...current,
        items: current.items.map((candidate) => candidate.id === item.id ? item : candidate),
      };
    });
  }

  function executeRun(
    queue: MediaExportQueue,
    sink: MediaExportSink,
    transport: MediaExportSessionTransport,
    controller: AbortController,
  ): void {
    setFrozenQueue(queue);
    setResultQueue(queue);
    setPhase("running");
    setRunNotice(null);
    const run = startMediaExport(queue, {
      transport,
      sink,
      signal: controller.signal,
      onItem: (item) => updateQueueItem(item, transport),
      onUnauthorized: (error) => {
        if (!transport.isCurrent()) return;
        setErrorMessage("登录状态已失效，媒体导出已停止，请重新登录。");
        propsRef.current.onUnauthorized?.(error);
      },
    });
    runRef.current = run;
    void run.done.then((result) => {
      if (!transport.isCurrent()) return;
      setResultQueue(result);
      setPhase("complete");
    }).catch(() => {
      if (transport.isCurrent()) {
        setErrorMessage("媒体导出未能完成，请核对逐项状态后再处理。");
        setPhase("complete");
      }
    }).finally(() => {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (runRef.current === run) runRef.current = null;
    });
  }

  async function startWithDirectory(queue: MediaExportQueue): Promise<void> {
    if (invalidateIfSessionChanged()) return;
    setErrorMessage(null);
    setPhase("preparing");
    let transport: MediaExportSessionTransport;
    try {
      transport = createTransport(queue.sourceKind === "rough-cut" ? "rough-cut" : "chapter");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法建立当前登录会话。");
      setPhase("confirmed");
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    const pickerWindow = window as unknown as DirectoryPickerWindow;
    try {
      const sink = await createDirectoryMediaExportSink({
        runId: queue.outputDirectoryId ?? queue.runId,
        context: { signal: controller.signal, isCurrent: transport.isCurrent },
        pickDirectory: async () => {
          const picker = pickerWindow.showDirectoryPicker;
          if (picker === undefined) {
            throw new DOMException("Directory picker is unavailable.", "NotSupportedError");
          }
          const directory = await picker.call(window, { mode: "readwrite" });
          if (controller.signal.aborted || !transport.isCurrent()) {
            throw new DOMException("The export scope changed.", "AbortError");
          }
          return directory;
        },
      });
      if (controller.signal.aborted || !transport.isCurrent()) return;
      executeRun(queue, sink, transport, controller);
    } catch (error) {
      if (!controller.signal.aborted && transport.isCurrent()) {
        setErrorMessage(error instanceof MediaExportSinkError ? error.message : "无法准备目标目录。");
        setPhase("confirmed");
      }
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }

  function startWithBrowser(queue: MediaExportQueue): void {
    if (invalidateIfSessionChanged()) return;
    setErrorMessage(null);
    let transport: MediaExportSessionTransport;
    try {
      transport = createTransport(queue.sourceKind === "rough-cut" ? "rough-cut" : "chapter");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法建立当前登录会话。");
      setPhase("confirmed");
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    executeRun(queue, createBrowserMediaExportSink({
      appendAnchor(anchor) {
        const dialog = focus.dialogRef.current;
        if (dialog === null) {
          throw new Error("The media export dialog is no longer mounted.");
        }
        dialog.appendChild(anchor as HTMLAnchorElement);
      },
    }), transport, controller);
  }

  function confirmProjection(): void {
    if (invalidateIfSessionChanged()) return;
    if (activeProjection === null || !hasReadyItems(activeProjection)) return;
    setFrozenQueue(activeProjection);
    setResultQueue(null);
    setConfirmationChecked(false);
    setPhase("confirmed");
    setErrorMessage(null);
  }

  function selectFrame(frameIndex: number, checked: boolean): void {
    setSelectedFrameIndexes((current) => {
      const next = new Set(current);
      if (checked) next.add(frameIndex);
      else next.delete(frameIndex);
      return next;
    });
    setErrorMessage(null);
  }

  function makeRetryQueue(): MediaExportQueue | null {
    if (resultQueue === null || retrySelectedIds.size === 0 || !retryConfirmed) return null;
    const outputDirectoryId = createRunId();
    try {
      const queue = createFailedMediaExportRetryQueue(resultQueue, {
        itemIds: Array.from(retrySelectedIds),
        outputDirectoryId,
        confirmedNewTarget: true,
      });
      setFrozenQueue(queue);
      setResultQueue(queue);
      setConfirmationChecked(true);
      setErrorMessage(null);
      return queue;
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法创建失败项重试队列。");
      return null;
    }
  }

  async function retryWithDirectory(): Promise<void> {
    const queue = makeRetryQueue();
    if (queue === null) return;
    await startWithDirectory(queue);
    setRetrySelectedIds(new Set());
    setRetryConfirmed(false);
  }

  function retryWithBrowser(): void {
    const queue = makeRetryQueue();
    if (queue === null) return;
    startWithBrowser(queue);
    setRetrySelectedIds(new Set());
    setRetryConfirmed(false);
  }

  const displayedItems = queueForDisplay?.items ?? [];
  const isReviewing = phase === "review";
  const isActive = phase === "preparing" || phase === "running";
  const confirmationText = sourceMode === "rough-cut"
    ? "我已核对当前已保存粗剪的导出清单与顺序。"
    : "我已核对本次选择的分镜与导出清单。";

  return (
    <div className="media-export-backdrop">
      <section
        aria-labelledby="media-export-title"
        aria-modal="true"
        className="media-export-dialog"
        onKeyDown={(event) => event.stopPropagation()}
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="media-export-header">
          <div>
            <p className="media-export-eyebrow">逐项读取并保存视频文件</p>
            <h2 id="media-export-title">导出媒体素材</h2>
            <p>{chapter.title} · {seriesName}</p>
          </div>
          <button
            aria-label={isActive ? "停止当前导出并关闭" : "关闭媒体导出"}
            className="media-export-close"
            onClick={() => focus.dismiss()}
            type="button"
          >
            ×
          </button>
        </header>

        {state.status === "restoring" && (
          <p role="status">正在验证登录状态…</p>
        )}
        {state.status === "restore-error" && (
          <p role="alert">当前登录状态无法确认，请稍后重新打开导出。</p>
        )}
        {state.status === "anonymous" && (
          <p role="alert">请先登录，再导出媒体素材。</p>
        )}
        {state.status === "authenticated" && services.apiBaseUrl === null && (
          <p role="alert">当前运行模式没有连接媒体导出 API。</p>
        )}
        {errorMessage !== null && <p className="media-export-error" role="alert">{errorMessage}</p>}
        {runNotice !== null && <p className="media-export-notice" role="status">{runNotice}</p>}

        {isReviewing && state.status === "authenticated" && services.apiBaseUrl !== null && (
          <>
            <div className="media-export-source-choice" aria-label="导出来源">
              <strong>{sourceMode === "chapter" ? "导出来源：本章逐镜" : "导出来源：已保存粗剪"}</strong>
              {sourceMode === "rough-cut" && roughCutMessage !== null && <p role="status">{roughCutMessage}</p>}
              {sourceMode === "rough-cut" && (roughCutSnapshot === null || roughCutSnapshot === undefined)
                ? <p>当前没有已保存的粗剪快照。</p>
                : null}
            </div>

            {sourceMode === "chapter" && (
              <div className="media-export-summary" aria-live="polite">
                <span>可导出 {selectedIndexes.filter((index) => (
                  chapterAllQueue.items[index]?.eligibility === "ready"
                )).length}/{chapterReadyCount}</span>
                <span>缺少视频 {chapterMissingCount}</span>
                <span>身份重复 {chapterDuplicateCount}</span>
                <span>已排除 {chapterExcludedCount}</span>
              </div>
            )}
            {sourceMode === "rough-cut" && roughCutProjection !== null && (
              <div className="media-export-summary" aria-live="polite">
                <span>可导出 {roughCutProjection.items.filter((item) => item.eligibility === "ready").length}</span>
                <span>缺少视频 {roughCutProjection.items.filter((item) => item.eligibility === "missing").length}</span>
                <span>仍在等待 {roughCutProjection.items.filter((item) => item.eligibility === "pending").length}</span>
                <span>身份重复 {roughCutProjection.items.filter((item) => item.eligibility === "duplicate").length}</span>
                <span>已排除 {roughCutProjection.items.filter((item) => item.eligibility === "excluded").length}</span>
              </div>
            )}

            {sourceMode === "chapter" && chapterAllQueue.items.length === 0
              ? <p>本章没有可检查的分镜。</p>
              : null}
            {sourceMode === "rough-cut" && roughCutProjection === null
              ? <p>请先保存粗剪，再按本次保存的顺序确认导出清单。</p>
              : null}
            <ol aria-label="媒体导出预览" className="media-export-items">
              {(sourceMode === "chapter" ? chapterAllQueue.items : roughCutProjection?.items ?? []).map((item) => {
                const selected = sourceMode === "chapter" && selectedFrameIndexes.has(item.frameIndex);
                return (
                  <li className="media-export-item" data-export-item-id={item.id} key={item.id}>
                    {sourceMode === "chapter" && item.eligibility === "ready" && (
                      <label className="media-export-item-select">
                        <input
                          aria-label={`选择分镜 ${item.frameIndex + 1}`}
                          checked={selected}
                          onChange={(event) => selectFrame(item.frameIndex, event.currentTarget.checked)}
                          type="checkbox"
                        />
                      </label>
                    )}
                    <div className="media-export-item-main">
                      <strong>{item.filename}</strong>
                      <span>{frameDescription(item, chapter, roughCutSnapshot, sourceMode)}</span>
                    </div>
                    <span className={`media-export-status media-export-status-${item.eligibility}`}>
                      {statusForEligibility(item.eligibility, selected || sourceMode === "rough-cut")}
                    </span>
                  </li>
                );
              })}
            </ol>
            <div className="media-export-actions">
              <button
                className="primary-button"
                disabled={activeProjection === null || !hasReadyItems(activeProjection)}
                onClick={confirmProjection}
                type="button"
              >
                确认导出清单
              </button>
            </div>
          </>
        )}

        {phase === "confirmed" && frozenQueue !== null && (
          <>
            <p className="media-export-confirmation-title">
              已确认 {frozenQueue.items.filter((item) => item.eligibility === "ready").length} 个可导出视频。开始后会按此顺序逐项读取。
            </p>
            <label className="media-export-confirmation">
              <input
                checked={confirmationChecked}
                onChange={(event) => setConfirmationChecked(event.currentTarget.checked)}
                type="checkbox"
              />
              {confirmationText}
            </label>
            <ol aria-label="已确认的导出清单" className="media-export-items">
              {frozenQueue.items.map((item) => (
                <li className="media-export-item" data-export-item-id={item.id} key={item.id}>
                  <div className="media-export-item-main">
                    <strong>{item.filename}</strong>
                    <span>{frameDescription(item, chapter, roughCutSnapshot, sourceMode)}</span>
                  </div>
                  <span className={`media-export-status media-export-status-${item.eligibility}`}>
                    {statusForEligibility(item.eligibility, item.eligibility === "ready")}
                  </span>
                </li>
              ))}
            </ol>
            <div className="media-export-actions">
              {canUseDirectoryPicker() && (
                <button
                  className="primary-button"
                  disabled={!confirmationChecked}
                  onClick={() => { void startWithDirectory(frozenQueue); }}
                  type="button"
                >
                  选择目录并开始
                </button>
              )}
              <button
                className="secondary-button"
                disabled={!confirmationChecked}
                onClick={() => startWithBrowser(frozenQueue)}
                type="button"
              >
                使用浏览器下载
              </button>
              <button className="secondary-button" onClick={() => setPhase("review")} type="button">
                返回修改选择
              </button>
            </div>
            {!canUseDirectoryPicker() && (
              <p className="media-export-notice" role="status">
                当前浏览器不支持目录授权；浏览器下载只表示已发起，不代表文件已落盘。
              </p>
            )}
          </>
        )}

        {phase === "preparing" && <p role="status">正在准备目标目录，请完成浏览器的目录选择。</p>}
        {phase === "running" && (
          <div className="media-export-run-controls">
            <p role="status">正在按已确认的顺序导出。</p>
            <button
              className="secondary-button"
              onClick={() => {
                runRef.current?.stopAfterCurrent();
                setRunNotice("当前文件结束后将停止剩余队列；关闭对话框会立即中止当前文件。");
              }}
              type="button"
            >
              停止剩余文件
            </button>
          </div>
        )}
        {isActive && queueForDisplay !== null && (
          <ol aria-label="媒体导出逐项状态" className="media-export-items">
            {displayedItems.map((item) => (
              <li className="media-export-item" data-export-item-id={item.id} key={item.id}>
                <div className="media-export-item-main">
                  <strong>{item.filename}</strong>
                  <span>{frameDescription(item, chapter, roughCutSnapshot, sourceMode)}</span>
                </div>
                <span className={`media-export-status media-export-status-${item.state}`}>
                  {statusForItem(item, item.eligibility === "ready")}
                </span>
              </li>
            ))}
          </ol>
        )}
        {phase === "complete" && resultQueue !== null && (
          <>
            <p role="status">本次队列已结束。浏览器下载项目只确认已发起，目录项目仅在写入及关闭成功后标记已保存。</p>
            <ol aria-label="媒体导出逐项状态" className="media-export-items">
              {resultQueue.items.map((item) => (
                <li className="media-export-item" data-export-item-id={item.id} key={item.id}>
                  <div className="media-export-item-main">
                    <strong>{item.filename}</strong>
                    <span>{frameDescription(item, chapter, roughCutSnapshot, sourceMode)}</span>
                  </div>
                  <span className={`media-export-status media-export-status-${item.state}`}>
                    {statusForItem(item, item.eligibility === "ready")}
                  </span>
                </li>
              ))}
            </ol>
            {failedItems.length > 0 && (
              <section aria-label="手动重试已知失败项" className="media-export-retry">
                <h3>只重试确认失败的项目</h3>
                <p>成功、已发起下载、结果未知和已停止项目不会重放。重试需要确认新的目标。</p>
                {failedItems.map((item) => (
                  <label className="media-export-retry-item" key={item.id}>
                    <input
                      aria-label={`选择失败项 ${item.filename}`}
                      checked={retrySelectedIds.has(item.id)}
                      onChange={(event) => {
                        const checked = event.currentTarget.checked;
                        setRetrySelectedIds((current) => {
                          const next = new Set(current);
                          if (checked) next.add(item.id);
                          else next.delete(item.id);
                          return next;
                        });
                        setRetryConfirmed(false);
                      }}
                      type="checkbox"
                    />
                    <span>{item.filename} · {item.message}</span>
                  </label>
                ))}
                <label className="media-export-confirmation">
                  <input
                    checked={retryConfirmed}
                    disabled={retrySelectedIds.size === 0}
                    onChange={(event) => setRetryConfirmed(event.currentTarget.checked)}
                    type="checkbox"
                  />
                  我确认只重试所选失败项，并使用一个新的目标目录或下载批次。
                </label>
                <div className="media-export-actions">
                  {canUseDirectoryPicker() && (
                    <button
                      className="primary-button"
                      disabled={!retryConfirmed || retrySelectedIds.size === 0}
                      onClick={() => { void retryWithDirectory(); }}
                      type="button"
                    >
                      选择新目录并重试
                    </button>
                  )}
                  <button
                    className="secondary-button"
                    disabled={!retryConfirmed || retrySelectedIds.size === 0}
                    onClick={retryWithBrowser}
                    type="button"
                  >
                    以新下载批次重试
                  </button>
                </div>
              </section>
            )}
          </>
        )}

      </section>
    </div>
  );
}
