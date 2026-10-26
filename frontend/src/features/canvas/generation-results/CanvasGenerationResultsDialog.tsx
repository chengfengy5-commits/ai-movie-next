import { useCallback, useEffect, useRef, useState } from "react";
import { CanvasOwnedImageError, type CanvasOwnedImageRun } from "../../../shared/api/canvasOwnedImage";
import type { CanvasOwnedResultItem } from "../../../shared/api/canvasOwnedResults";
import type { CanvasResultApplicationLookup } from "../../../shared/api/canvasResultApplications";
import type {
  CanvasGenerationApplicationCandidate,
  CanvasGenerationApplicationDestination,
} from "../../../shared/api/canvasGenerationApplications";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import "./CanvasGenerationResultsDialog.css";

export interface PendingCanvasGenerationIntent {
  readonly destination: CanvasGenerationApplicationDestination;
  readonly candidate: CanvasGenerationApplicationCandidate;
  readonly sourceVersion: string;
  readonly state: "unknown" | "not_applied";
  readonly lookup: CanvasResultApplicationLookup | null;
}

export interface CanvasGenerationResultsDialogProps {
  readonly intentOnly?: boolean;
  readonly taskIdFilter: string | null;
  readonly targetLabel: string;
  readonly items: readonly CanvasOwnedResultItem[];
  readonly nextCursor: string | null;
  readonly loading: boolean;
  readonly busy: boolean;
  readonly error: string | null;
  readonly message: string | null;
  readonly pendingIntent: PendingCanvasGenerationIntent | null;
  readonly historyLookups: ReadonlyMap<string, CanvasResultApplicationLookup>;
  readonly lockRequired: boolean;
  readonly lockUnavailable: boolean;
  readonly lockPending: boolean;
  readonly lockMessage: string | null;
  readonly confirmedProof: {
    readonly resultRef: string;
    readonly status: "applied" | "already_applied";
    readonly applicationRef: string;
    readonly targetRevision: string;
    readonly committedAt: string;
  } | null;
  readonly imagePreviewAvailable: boolean;
  readonly imagePreviewScopeKey: string;
  readonly imagePreviewServiceIdentity: object;
  onClose(): void;
  onRefresh(): void;
  onLoadMore(): void;
  onApply(item: CanvasOwnedResultItem): void;
  onVerify(item: CanvasOwnedResultItem): void;
  onLookupPendingIntent(): void;
  onRetryPendingIntent(): void;
  onAcquireLock(): void;
  onBeginImagePreview(item: CanvasOwnedResultItem): CanvasOwnedImageRun | null;
}

type ImagePreviewState =
  | { readonly kind: "loading"; readonly itemKey: string; readonly requestId: number }
  | { readonly kind: "ready"; readonly itemKey: string; readonly requestId: number; readonly objectUrl: string }
  | { readonly kind: "error"; readonly itemKey: string; readonly requestId: number; readonly message: string };

interface ImagePreviewRequest {
  readonly id: number;
  readonly itemKey: string;
  readonly controller: AbortController;
  readonly scopeKey: string;
  readonly serviceIdentity: object;
  run: CanvasOwnedImageRun | null;
  objectUrl: string | null;
}

function imagePreviewItemKey(item: Pick<CanvasOwnedResultItem, "task_id" | "result_ref">): string {
  return JSON.stringify([item.task_id, item.result_ref]);
}

function imagePreviewFailureMessage(cause: unknown): string {
  if (cause instanceof CanvasOwnedImageError) {
    switch (cause.kind) {
      case "not-sent": return "登录或画布状态已变化，图片未显示。请重新打开记录后重试。";
      case "unauthorized": return "登录状态已失效，请重新登录后查看图片。";
      case "forbidden": return "当前账号无权读取这张图片。";
      case "not-found": return "原图片记录暂不可用，请点击“重试查看图片”。";
      case "unready": return "图片记录尚未就绪，请稍后重试。";
      case "unavailable": return "图片读取服务暂不可用，请稍后重试。";
      case "read-error":
      case "invalid-response": return "图片读取失败，请点击“重试查看图片”。";
    }
  }
  return "图片无法读取或显示，请点击“重试查看图片”。";
}

function revokeImagePreviewUrl(request: ImagePreviewRequest): void {
  if (request.objectUrl === null) return;
  const objectUrl = request.objectUrl;
  request.objectUrl = null;
  try {
    URL.revokeObjectURL(objectUrl);
  } catch {
    // Releasing one preview must not prevent the dialog from continuing to work.
  }
}

function eligibilityLabel(item: CanvasOwnedResultItem): string {
  switch (item.eligibility) {
    case "ready": return "可应用到当前目标";
    case "already_applied": return "此前已应用";
    case "stale": return "原始目标版本已变化";
    case "target_deleted": return "原始目标已删除";
    case "unready": return "记录尚未就绪";
  }
}

function outcomeLabel(status: "applied" | "already_applied"): string {
  return status === "applied" ? "应用成功" : "此前已应用";
}

function proofDetails(application: {
  application_ref: string;
  target_revision: string;
  committed_at: string;
}) {
  return (
    <details className="canvas-generation-details">
      <summary>查看记录详情</summary>
      <p>应用记录编号：{application.application_ref}</p>
      <p>提交时目标版本：{application.target_revision}</p>
      <p>确认时间：{application.committed_at}</p>
    </details>
  );
}

function lookupMessage(lookup: CanvasResultApplicationLookup): string {
  if (lookup.status === "not_applied") {
    return "暂未查到应用记录，之前请求仍可能稍后完成。原操作已保留。";
  }
  return "此前已应用，已找到对应记录。它不代表当前图像仍与当时相同。";
}

export function CanvasGenerationResultsDialog({
  intentOnly = false,
  taskIdFilter,
  targetLabel,
  items,
  nextCursor,
  loading,
  busy,
  error,
  message,
  pendingIntent,
  historyLookups,
  lockRequired,
  lockUnavailable,
  lockPending,
  lockMessage,
  confirmedProof,
  onClose,
  onRefresh,
  onLoadMore,
  onApply,
  onVerify,
  onLookupPendingIntent,
  onRetryPendingIntent,
  onAcquireLock,
  imagePreviewAvailable,
  imagePreviewScopeKey,
  imagePreviewServiceIdentity,
  onBeginImagePreview,
}: CanvasGenerationResultsDialogProps) {
  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: onClose });
  const [imagePreview, setImagePreview] = useState<ImagePreviewState | null>(null);
  const activeImagePreviewRef = useRef<ImagePreviewRequest | null>(null);
  const imagePreviewSequenceRef = useRef(0);
  const visibleItems = taskIdFilter === null ? items : items.filter((item) => item.task_id === taskIdFilter);
  const itemsRef = useRef(visibleItems);
  itemsRef.current = visibleItems;
  const imagePreviewScopeKeyRef = useRef(imagePreviewScopeKey);
  imagePreviewScopeKeyRef.current = imagePreviewScopeKey;
  const imagePreviewServiceIdentityRef = useRef(imagePreviewServiceIdentity);
  imagePreviewServiceIdentityRef.current = imagePreviewServiceIdentity;

  const clearImagePreview = useCallback((updateState = true) => {
    const active = activeImagePreviewRef.current;
    activeImagePreviewRef.current = null;
    imagePreviewSequenceRef.current += 1;
    if (active !== null) {
      active.controller.abort();
      revokeImagePreviewUrl(active);
    }
    if (updateState) setImagePreview(null);
  }, []);

  const itemIsReadable = useCallback((itemKey: string) => itemsRef.current.some((item) =>
    imagePreviewItemKey(item) === itemKey && item.eligibility !== "unready"), []);

  const previewRequestBelongsToDialog = useCallback((request: ImagePreviewRequest) => (
    activeImagePreviewRef.current === request
      && !request.controller.signal.aborted
      && imagePreviewScopeKeyRef.current === request.scopeKey
      && imagePreviewServiceIdentityRef.current === request.serviceIdentity
      && itemIsReadable(request.itemKey)
  ), [itemIsReadable]);

  const previewRequestIsCurrent = useCallback((request: ImagePreviewRequest) => {
    if (activeImagePreviewRef.current !== request
      || request.controller.signal.aborted
      || imagePreviewScopeKeyRef.current !== request.scopeKey
      || imagePreviewServiceIdentityRef.current !== request.serviceIdentity
      || !itemIsReadable(request.itemKey)) return false;
    try {
      return request.run?.isCurrent() ?? false;
    } catch {
      return false;
    }
  }, [itemIsReadable]);

  const beginImagePreview = useCallback((item: CanvasOwnedResultItem) => {
    if (item.eligibility === "unready" || !itemsRef.current.includes(item)) return;

    clearImagePreview();
    const request: ImagePreviewRequest = {
      id: ++imagePreviewSequenceRef.current,
      itemKey: imagePreviewItemKey(item),
      controller: new AbortController(),
      scopeKey: imagePreviewScopeKey,
      serviceIdentity: imagePreviewServiceIdentity,
      run: null,
      objectUrl: null,
    };
    activeImagePreviewRef.current = request;
    setImagePreview({ kind: "loading", itemKey: request.itemKey, requestId: request.id });

    const readImage = async () => {
      let run: CanvasOwnedImageRun | null;
      try {
        run = onBeginImagePreview(item);
      } catch (cause) {
        if (!previewRequestBelongsToDialog(request)) return;
        request.controller.abort();
        setImagePreview({
          kind: "error",
          itemKey: request.itemKey,
          requestId: request.id,
          message: imagePreviewFailureMessage(cause),
        });
        return;
      }

      request.run = run;
      if (run === null) {
        if (!previewRequestBelongsToDialog(request)) return;
        request.controller.abort();
        setImagePreview({
          kind: "error",
          itemKey: request.itemKey,
          requestId: request.id,
          message: imagePreviewFailureMessage(new CanvasOwnedImageError("not-sent", "图片读取上下文已变化。", null)),
        });
        return;
      }
      if (!previewRequestIsCurrent(request)) {
        if (activeImagePreviewRef.current === request) clearImagePreview();
        return;
      }

      try {
        const blob = await run.read({ taskId: item.task_id, resultRef: item.result_ref }, request.controller.signal);
        if (!previewRequestIsCurrent(request)) {
          if (activeImagePreviewRef.current === request) clearImagePreview();
          return;
        }

        const objectUrl = URL.createObjectURL(blob);
        request.objectUrl = objectUrl;
        if (!previewRequestIsCurrent(request)) {
          revokeImagePreviewUrl(request);
          if (activeImagePreviewRef.current === request) clearImagePreview();
          return;
        }
        setImagePreview({
          kind: "ready",
          itemKey: request.itemKey,
          requestId: request.id,
          objectUrl,
        });
      } catch (cause) {
        if (activeImagePreviewRef.current !== request || request.controller.signal.aborted) return;
        if (!previewRequestIsCurrent(request)) {
          clearImagePreview();
          return;
        }
        request.controller.abort();
        setImagePreview({
          kind: "error",
          itemKey: request.itemKey,
          requestId: request.id,
          message: imagePreviewFailureMessage(cause),
        });
      }
    };

    void readImage();
  }, [clearImagePreview, imagePreviewScopeKey, imagePreviewServiceIdentity, onBeginImagePreview, previewRequestBelongsToDialog, previewRequestIsCurrent]);

  const failImagePreview = useCallback((requestId: number, message: string) => {
    const request = activeImagePreviewRef.current;
    if (request === null || request.id !== requestId) return;
    revokeImagePreviewUrl(request);
    request.controller.abort();
    activeImagePreviewRef.current = null;
    setImagePreview({ kind: "error", itemKey: request.itemKey, requestId, message });
  }, []);

  useEffect(() => {
    clearImagePreview();
    return () => clearImagePreview(false);
  }, [clearImagePreview, imagePreviewScopeKey, imagePreviewServiceIdentity]);

  useEffect(() => {
    const request = activeImagePreviewRef.current;
    if (request !== null && !itemIsReadable(request.itemKey)) clearImagePreview();
  }, [clearImagePreview, items, itemIsReadable]);

  const activeImagePreview = activeImagePreviewRef.current;
  const currentImagePreview = imagePreview?.kind === "ready"
    && activeImagePreview !== null
    && activeImagePreview.id === imagePreview.requestId
    && previewRequestIsCurrent(activeImagePreview)
    ? imagePreview
    : null;

  return (
    <div className="canvas-generation-backdrop" role="presentation">
      <section
        aria-labelledby="canvas-generation-title"
        aria-modal="true"
        className="canvas-generation-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="canvas-generation-heading">
          <div>
            <p className="eyebrow">{targetLabel}</p>
            <h2 id="canvas-generation-title">生成图片记录</h2>
            <p>只显示当前目标的图片生成记录。应用前会再次确认目标版本和必要的编辑锁。</p>
          </div>
          <button
            aria-label="关闭生成图片记录"
            className="canvas-generation-close"
            onClick={() => { focus.markDismissed(); onClose(); }}
            type="button"
          >×</button>
        </header>

        {pendingIntent !== null && (
          <div className="canvas-generation-alert" role="alert">
            <p>
              {pendingIntent.state === "unknown"
                ? "上次提交结果暂时无法确认。原操作已保留；请先核对应用记录，确认前不会重复提交。"
                : "暂未查到应用记录，之前请求仍可能稍后完成。只有你明确选择重新提交原操作时，系统才会再次提交。"}
            </p>
            <details className="canvas-generation-details">
              <summary>查看记录详情</summary>
              <p>任务编号：{pendingIntent.candidate.task_id}</p>
              <p>结果编号：{pendingIntent.candidate.result_ref}</p>
            </details>
            {pendingIntent.state === "unknown" ? (
              <button disabled={busy || loading} onClick={onLookupPendingIntent} type="button">继续核对</button>
            ) : (
              <div className="canvas-generation-actions">
                <button disabled={busy || loading || lockPending} onClick={onRetryPendingIntent} type="button">
                  重新提交原操作
                </button>
                <button disabled={busy || loading} onClick={onLookupPendingIntent} type="button">
                  再次核对
                </button>
              </div>
            )}
            {pendingIntent.lookup !== null && <p>{lookupMessage(pendingIntent.lookup)}</p>}
          </div>
        )}

        {confirmedProof !== null && (
          <div className="canvas-generation-confirmed" role="status">
            <p>
              {outcomeLabel(confirmedProof.status)}。已找到对应记录；这只证明当时的应用，不代表当前图像仍未被后续手工修改。
            </p>
            {proofDetails({
              application_ref: confirmedProof.applicationRef,
              target_revision: confirmedProof.targetRevision,
              committed_at: confirmedProof.committedAt,
            })}
          </div>
        )}

        {lockMessage !== null && <p className="canvas-generation-message" role="status">{lockMessage}</p>}
        {lockUnavailable && (
          <p className="canvas-generation-alert" role="alert">当前无法确认编辑锁，暂时不能应用此结果。</p>
        )}
        {lockRequired && !lockUnavailable && (
          <div className="canvas-generation-alert" role="alert">
            <p>应用前需要由当前账号持有有效的章节编辑锁。读取记录不会自动取得或续期锁。</p>
            <button disabled={busy || lockPending} onClick={onAcquireLock} type="button">取得编辑锁</button>
          </div>
        )}

        {message !== null && <p className="canvas-generation-message" role="status">{message}</p>}
        {error !== null && <p className="canvas-generation-error" role="alert">{error}</p>}

        <div aria-label="生成记录列表" aria-busy={loading} className="canvas-generation-list">
          {loading && <p role="status">正在读取生成记录…</p>}
          {!loading && error === null && visibleItems.length === 0 && (
            <p>{intentOnly
              ? "此处只保留了原操作；请先核对应用记录。"
              : taskIdFilter !== null
                ? "当前已加载范围内暂无该任务的图片结果。任务显示已完成不代表结果已就绪；可重新读取当前页或按需加载下一页。"
                : "当前目标没有可显示的图片生成记录。"}</p>
          )}
          {!loading && error === null && visibleItems.length === 0 && taskIdFilter !== null && !intentOnly && (
            <details className="canvas-generation-details">
              <summary>查看对应任务编号</summary>
              <p>任务编号：{taskIdFilter}</p>
            </details>
          )}
          {visibleItems.map((item) => {
            const lookup = historyLookups.get(item.result_ref);
            const applyDisabled = busy || loading || pendingIntent !== null
              || lockPending || lockRequired || lockUnavailable;
            return (
              <article className="canvas-generation-item" key={item.result_ref}>
                <div className="canvas-generation-item-heading">
                  <strong>{eligibilityLabel(item)}</strong>
                  <span>{item.operation === "image.single" ? "单张图片" : "批量图片"}</span>
                </div>
                <p>完成时间：{item.completed_at}</p>
                <details className="canvas-generation-details">
                  <summary>查看记录详情</summary>
                  <p>任务编号：{item.task_id}</p>
                  <p>结果编号：{item.result_ref}</p>
                </details>
                {item.application !== null && proofDetails(item.application)}
                {lookup !== undefined && (
                  <div className="canvas-generation-message" role="status">
                    <p>{lookupMessage(lookup)}</p>
                    {lookup.application !== null && proofDetails(lookup.application)}
                  </div>
                )}
                <div className="canvas-generation-actions">
                  <button
                    disabled={!imagePreviewAvailable || item.eligibility === "unready"
                      || (imagePreview?.kind === "loading" && imagePreview.itemKey === imagePreviewItemKey(item))}
                    onClick={() => beginImagePreview(item)}
                    type="button"
                  >
                    {imagePreview?.kind === "loading" && imagePreview.itemKey === imagePreviewItemKey(item)
                      ? "正在读取图片…"
                      : imagePreview?.kind === "error" && imagePreview.itemKey === imagePreviewItemKey(item)
                        ? "重试查看图片"
                        : "查看图片"}
                  </button>
                  {item.eligibility === "ready" && item.expected_target !== null && item.application_key !== null && (
                    <button disabled={applyDisabled} onClick={() => onApply(item)} type="button">
                      应用到当前目标
                    </button>
                  )}
                  {item.eligibility === "already_applied" && (
                    <button disabled={busy || loading} onClick={() => onVerify(item)} type="button">
                      核对应用记录
                    </button>
                  )}
                </div>
                {item.eligibility === "unready" && (
                  <p className="canvas-generation-image-preview-hint">记录尚未就绪，暂时不能读取图片。</p>
                )}
                {!imagePreviewAvailable && item.eligibility !== "unready" && (
                  <p className="canvas-generation-image-preview-hint">当前暂不支持图片预览。</p>
                )}
                {imagePreview?.itemKey === imagePreviewItemKey(item) && imagePreview.kind === "loading" && (
                  <p className="canvas-generation-image-preview-hint" role="status">正在读取历史图片…</p>
                )}
                {imagePreview?.itemKey === imagePreviewItemKey(item) && imagePreview.kind === "error" && (
                  <p className="canvas-generation-error" role="alert">{imagePreview.message}</p>
                )}
                {currentImagePreview?.itemKey === imagePreviewItemKey(item) && (
                  <figure aria-label="历史图片预览" className="canvas-generation-image-preview">
                    <img
                      alt="生成结果图片预览"
                      onError={() => failImagePreview(currentImagePreview.requestId, "图片无法解码或显示，请点击“重试查看图片”。")}
                      onLoad={(event) => {
                        const request = activeImagePreviewRef.current;
                        if (request === null || request.id !== currentImagePreview.requestId) return;
                        if (event.currentTarget.naturalWidth <= 0 || event.currentTarget.naturalHeight <= 0) {
                          failImagePreview(currentImagePreview.requestId, "图片没有可显示的像素，请点击“重试查看图片”。");
                        } else if (!previewRequestIsCurrent(request)) {
                          failImagePreview(currentImagePreview.requestId, "画布状态已变化，图片已关闭。请重新打开记录后重试。");
                        }
                      }}
                      src={currentImagePreview.objectUrl}
                    />
                    <figcaption>历史图片预览，不会应用到当前目标。</figcaption>
                    <button onClick={() => clearImagePreview()} type="button">关闭图片预览</button>
                  </figure>
                )}
              </article>
            );
          })}
        </div>

        <footer className="canvas-generation-footer">
          {!intentOnly && <button disabled={loading || busy} onClick={onRefresh} type="button">重新读取当前页</button>}
          {nextCursor !== null && (
            <button disabled={loading || busy} onClick={onLoadMore} type="button">加载下一页</button>
          )}
          <button onClick={() => { focus.markDismissed(); onClose(); }} type="button">关闭</button>
        </footer>
      </section>
    </div>
  );
}
