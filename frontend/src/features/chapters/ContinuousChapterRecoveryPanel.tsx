import type { ContinuousChapterOperationEnvelope } from "./continuousChapterOperationJournal";
import "./continuousChapterRecoveryPanel.css";

export type ContinuousChapterRecoveryStatus = "checking" | "empty" | "ready" | "error";

interface ContinuousChapterRecoveryPanelProps {
  readonly chapterTitle: string;
  readonly status: ContinuousChapterRecoveryStatus;
  readonly journal: ContinuousChapterOperationEnvelope | null;
  readonly message: string | null;
  readonly busy: boolean;
  onRetryRead(): void;
  onVerify(): void;
  onResolve(): void;
  onContinueDraft(): void;
  onDiscardUndispatched(): void;
}

function phaseLabel(phase: ContinuousChapterOperationEnvelope["phase"]): string {
  switch (phase) {
    case "captured_not_dispatched": return "请求尚未发出";
    case "dispatch_possible": return "请求可能已发出，等待核对";
    case "unknown": return "结果尚未确认";
    case "acknowledged_pending_receipt": return "等待原操作回执";
    case "receipt_committed_pending_target_read": return "已收到回执，等待核对当前章节";
    case "receipt_rejected": return "原操作被拒绝，等待核对当前章节";
    case "resolved_reviewed": return "已完成确认";
  }
}

export function ContinuousChapterRecoveryPanel({
  chapterTitle,
  status,
  journal,
  message,
  busy,
  onRetryRead,
  onVerify,
  onResolve,
  onContinueDraft,
  onDiscardUndispatched,
}: ContinuousChapterRecoveryPanelProps) {
  if (status === "empty") return null;

  const mayResolveSavedChapter = journal !== null
    && journal.observation.receipt !== null
    && journal.observation.target_status === "matches"
    && journal.operation.kind === "save-chapter"
    && journal.observation.receipt.outcome === "committed"
    && journal.phase !== "resolved_reviewed";
  const mayContinueDraft = journal !== null
    && journal.observation.receipt !== null
    && journal.observation.target_status === "matches"
    && (!(journal.operation.kind === "save-chapter" && journal.observation.receipt.outcome === "committed")
      || journal.phase === "resolved_reviewed");
  const mayDiscardUndispatched = journal !== null
    && journal.phase === "captured_not_dispatched"
    && journal.observation.receipt === null;

  return (
    <section className="continuous-chapter-recovery" aria-label="连续编辑操作恢复">
      <header>
        <p className="eyebrow">整章编辑恢复 · {chapterTitle}</p>
        <h2>恢复原操作</h2>
      </header>
      {status === "checking" && <p role="status">正在读取本章的原操作记录…</p>}
      {status === "error" && (
        <div className="continuous-chapter-recovery-alert" role="alert">
          <p>{message ?? "本地原操作记录无法安全读取；本章写入入口已锁定。"}</p>
          <button className="secondary-button" disabled={busy} onClick={onRetryRead} type="button">重试读取记录</button>
        </div>
      )}
      {status === "ready" && journal !== null && (
        <>
          <p role="status">{phaseLabel(journal.phase)}</p>
          {message !== null && message !== phaseLabel(journal.phase) && (
            <p className="continuous-chapter-recovery-message">{message}</p>
          )}
          <p className="continuous-chapter-recovery-help">
            这里只查询原操作编号，不会重新提交。草稿、来源映射和原请求已保留；需要重新核对后才能解除本章写入冻结。
          </p>
          <ol className="continuous-chapter-recovery-draft" aria-label="原连续编辑草稿">
            {journal.snapshot.frames.map((frame, index) => (
              <li key={frame.draftId}>
                <strong>第 {index + 1} 段 · {frame.sourceIndex === null ? "新增" : `原镜头 ${frame.sourceIndex + 1}`}</strong>
                <pre>{frame.text}</pre>
                <small>
                  分镜：{frame.storyboardId ?? "无"}；角色：{frame.characterIds.join("、") || "无"}；
                  场景：{frame.sceneIds.join("、") || "无"}；道具：{frame.propIds.join("、") || "无"}
                </small>
              </li>
            ))}
          </ol>
          {journal.observation.receipt !== null && (
            <p className="continuous-chapter-recovery-receipt" role="status">
              原操作回执：{journal.observation.receipt.command_kind === "save-chapter"
                ? "整章保存"
                : journal.observation.receipt.command_kind === "create-character"
                  ? "创建角色素材"
                  : journal.observation.receipt.command_kind === "create-scene"
                    ? "创建场景素材"
                    : "创建道具素材"}，
              {journal.observation.receipt.outcome === "committed" ? "已确认完成" : "已拒绝"}；
              {journal.observation.receipt.outcome === "committed" && journal.operation.kind !== "save-chapter"
                && typeof journal.observation.receipt.result_identity.asset_id === "string"
                ? `素材编号 ${journal.observation.receipt.result_identity.asset_id}；`
                : ""}
              当前章节核对：{journal.observation.target_status === "matches"
                ? "与回执一致"
                : journal.observation.target_status === "changed"
                  ? "与原目标有变化"
                  : "尚未完成"}。
            </p>
          )}
          <div className="continuous-chapter-recovery-actions">
            {journal.phase !== "captured_not_dispatched" && (
              <button className="secondary-button" disabled={busy} onClick={onVerify} type="button">
                {busy ? "正在读取原操作…" : "重新读取原操作"}
              </button>
            )}
            {mayDiscardUndispatched && (
              <button className="text-button" disabled={busy} onClick={onDiscardUndispatched} type="button">
                放弃明确尚未发送的记录
              </button>
            )}
            {mayResolveSavedChapter && (
              <button className="primary-button" disabled={busy} onClick={onResolve} type="button">
                我已审阅已保存章节，解除本章冻结
              </button>
            )}
            {mayContinueDraft && (
              <button className="primary-button" disabled={busy} onClick={onContinueDraft} type="button">
                继续编辑已核对草稿
              </button>
            )}
            {journal.phase === "resolved_reviewed"
              && journal.operation.kind === "save-chapter"
              && journal.observation.receipt?.outcome === "committed" && (
              <button className="primary-button" disabled={busy} onClick={onResolve} type="button">
                清理已审阅的原操作记录
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
