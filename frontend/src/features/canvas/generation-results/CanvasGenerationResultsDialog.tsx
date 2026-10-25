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
  onClose(): void;
  onRefresh(): void;
  onLoadMore(): void;
  onApply(item: CanvasOwnedResultItem): void;
  onVerify(item: CanvasOwnedResultItem): void;
  onLookupPendingIntent(): void;
  onRetryPendingIntent(): void;
  onAcquireLock(): void;
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
}: CanvasGenerationResultsDialogProps) {
  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: onClose });

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
          {!loading && error === null && items.length === 0 && (
            <p>{intentOnly ? "此处只保留了原操作；请先核对应用记录。" : "当前目标没有可显示的图片生成记录。"}</p>
          )}
          {items.map((item) => {
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
