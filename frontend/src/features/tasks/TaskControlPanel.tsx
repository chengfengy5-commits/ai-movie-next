import { useEffect } from "react";
import type { WorkspaceServices } from "../../shared/api/services";
import type { MyTaskRecord } from "../../shared/api/myTasks";
import { safeTaskText } from "../../shared/api/taskDetails";
import { useTaskControls } from "./useTaskControls";

const cancelReasonLabels = {
  available: "服务端当前允许请求取消。",
  already_requested: "服务端已记录取消请求；这里只能核对当前任务状态。",
  terminal: "服务端任务记录当前为终态；请查看结果与账务快照。",
  unsupported_operation: "此任务类型没有已确认的取消处理。",
  runtime_unavailable: "当前服务没有可用的取消处理器。",
  unsafe_task_graph: "任务来源无法安全核对，不能请求取消。",
} as const;

interface TaskControlPanelProps {
  task: MyTaskRecord;
  userId: string;
  sessionGeneration: number;
  services: WorkspaceServices;
  openGeneration: number;
  onUnauthorized(): void;
  onRefreshReceipt(): void | Promise<void>;
  onCancelAttemptMayHaveReachedServer(): void;
  onAutomaticObservationChange?: ((openGeneration: number, active: boolean) => void) | undefined;
}

export function TaskControlPanel(props: TaskControlPanelProps) {
  const state = useTaskControls(props);
  const { controls, pendingMarker, phase, confirming, message } = state;
  const readAvailable = props.services.mode === "api" && props.services.getTaskControls !== undefined;
  const busy = phase === "checking" || phase === "sending" || phase === "verifying";

  useEffect(() => {
    props.onAutomaticObservationChange?.(props.openGeneration, state.automaticallyObserving);
  }, [props.onAutomaticObservationChange, props.openGeneration, state.automaticallyObserving]);

  return (
    <section aria-labelledby="task-detail-controls-heading" className="task-detail-section">
      <header className="task-detail-section-heading">
        <div>
          <h3 id="task-detail-controls-heading">任务操作与状态核对</h3>
          <p>取消请求只代表向本地服务提出请求；任务结果、Provider 状态和账务记录以独立读取为准。</p>
        </div>
        {readAvailable && (
          <button
            className="secondary-button"
            disabled={busy || controls.loading || controls.refreshing}
            onClick={() => void state.verifyNow()}
            type="button"
          >
            {controls.refreshing ? "正在核对…" : "核对任务与取消状态"}
          </button>
        )}
      </header>

      {!state.isApiService && (
        <p className="task-detail-readonly-note">演示环境只提供本地只读记录，不支持真实取消操作。</p>
      )}
      {state.isApiService && props.services.getTaskControls === undefined && (
        <p className="task-detail-readonly-note">当前服务尚未提供任务操作能力读取；取消操作保持关闭。</p>
      )}
      {controls.loading && controls.snapshot === null && state.isApiService && readAvailable && (
        <p aria-live="polite" className="task-detail-state">正在读取服务端任务操作能力…</p>
      )}
      {controls.error !== null && (
        <div className="task-detail-error" role="alert">
          <p>{safeTaskText(controls.error)}</p>
          <p>取消操作保持关闭；可以重读服务端状态。</p>
        </div>
      )}
      {controls.snapshot !== null && (
        <dl className="task-detail-facts">
          <div><dt>服务端当前状态</dt><dd>{safeTaskText(controls.snapshot.status)}</dd></div>
          <div><dt>取消能力来源</dt><dd>{safeTaskText(cancelReasonLabels[controls.snapshot.cancel.reason])}</dd></div>
        </dl>
      )}
      {controls.snapshot?.status === "cancelling" && (
        <p className="task-detail-readonly-note">
          “取消处理中”是本地任务记录，不证明上游已经停止，也不代表费用已退还。
        </p>
      )}

      {pendingMarker.error !== null && (
        <p className="task-detail-error" role="alert">
          {safeTaskText(pendingMarker.error)} 取消提交将保持关闭；仍可读取服务端状态。
        </p>
      )}
      {pendingMarker.pending && (
        <p className="task-detail-readonly-note" role="status">
          这条任务有尚未解决的取消请求记录。恢复时只读取服务端，不会再次提交取消请求。
        </p>
      )}
      {message !== null && <p aria-live="polite" className="task-detail-state" role="status">{safeTaskText(message)}</p>}
      {phase === "paused" && pendingMarker.pending && (
        <p className="task-detail-readonly-note">自动核对已暂停；需要时可手动继续核对。</p>
      )}

      {state.canSend && !confirming && (
        <button className="secondary-button" onClick={state.beginConfirmation} type="button">
          请求取消
        </button>
      )}
      {confirming && state.canSend && (
        <div className="task-detail-readonly-note" role="group" aria-label="确认取消请求">
          <p>将向服务端发送一次取消请求。任务结果可能仍在处理，取消请求不代表上游已停止或会退款。</p>
          <button className="primary-button" disabled={busy} onClick={() => void state.confirmCancellation()} type="button">
            确认并请求取消
          </button>
          <button className="secondary-button" disabled={busy} onClick={state.cancelConfirmation} type="button">
            返回
          </button>
        </div>
      )}
    </section>
  );
}
