import { useEffect, useRef, useState } from "react";
import { ApiError, isAbortError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import type { MyTaskRecord } from "../../shared/api/myTasks";
import {
  safeTaskText,
  type TaskHistoryTruth,
  type TaskReceipt,
  type TaskRequestRecord,
} from "../../shared/api/taskDetails";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";
import { TaskControlPanel } from "./TaskControlPanel";
import "./taskDetailDialog.css";

interface TaskDetailDialogProps {
  task: MyTaskRecord;
  userId: string;
  services: WorkspaceServices;
  sessionGeneration?: number;
  openGeneration: number;
  onClose(): void;
  onUnauthorized(): void;
  onCancelAttemptMayHaveReachedServer(): void;
  onAutomaticObservationChange?: ((openGeneration: number, active: boolean) => void) | undefined;
}

interface ReadState<T> {
  snapshot: T | null;
  loading: boolean;
  refreshing: boolean;
  error: ApiError | null;
}

function initialReadState<T>(): ReadState<T> {
  return { snapshot: null, loading: true, refreshing: false, error: null };
}

function displayValue(value: string | number | null): string {
  return value === null || value === "" ? "暂无" : String(value);
}

function presentError(error: ApiError, section: "receipt" | "request"): string {
  if (error.status === 403) {
    return section === "request"
      ? "当前账号暂不可查看任务请求记录；结果与账务快照仍可单独读取。"
      : "当前账号暂不可查看这条任务记录。";
  }
  if (error.status === 404) {
    return "任务记录不存在或已不可读取。";
  }
  if (error.status !== null && error.status >= 500) {
    return "本地服务暂时无法读取记录，请稍后重试。";
  }
  if (error.kind === "timeout") {
    return "读取超时，可单独重试这一部分。";
  }
  if (error.kind === "network") {
    return "无法连接本地服务，请确认服务正在运行后重试。";
  }
  if (error.kind === "invalid-response") {
    return "本地服务返回的数据无法识别。";
  }
  return error.detail ?? error.message;
}

function billingStatusLabel(status: string | null): string {
  if (status === null) return "未提供";
  const labels: Record<string, string> = {
    legacy_unreconciled: "历史状态未核对",
    unbilled: "未计费记录",
    charged: "已记账记录",
    no_charge: "未收费记录",
    refunded: "已退款记录",
    partially_refunded: "部分退款记录",
  };
  return labels[status] ?? status;
}

function historySourceLabel(
  status: TaskHistoryTruth["source"]["status"],
  source: string,
): string {
  if (status === "registered" && source === "task_submissions.operation") {
    return "已登记的任务提交意图（尚无计费单元图）";
  }
  const labels: Record<TaskHistoryTruth["source"]["status"], string> = {
    legacy: "历史未核对记录（没有注册任务图）",
    registered: "已登记的任务图",
    unsupported: "当前版本无法读取任务来源",
    unknown: "任务来源未知",
  };
  return labels[status];
}

function historyFactSourceLabel(source: string): string {
  const labels: Record<string, string> = {
    "billing_units.operation_and_step_graph_version": "计费单元中的操作与任务图版本",
    "task_submissions.operation": "已接受提交记录中的操作",
    "ai_tasks.worker_lease_and_recovery_status": "Task 记录中的 Worker 租约与恢复字段",
    "external_submissions_and_linked_result_evidence": "关联的外部提交与结果证据",
    "ai_tasks.user_cancelled_at": "Task 记录中的取消时间",
    "billing_units.user_cancelled_at": "计费单元中的取消时间",
    "billing_units.deadline_at_and_result_status": "计费单元中的期限与本地结果状态",
    policy_stop_events: "本地策略停止事件",
    "local_continuation_work.phase": "本地后续处理阶段",
    unavailable: "当前服务未提供来源数据",
  };
  return labels[source] ?? "未知来源";
}

function historySourceConflictLabel(
  conflict: TaskHistoryTruth["source"]["conflicts"][number],
): string {
  const labels: Record<TaskHistoryTruth["source"]["conflicts"][number], string> = {
    intent_graph_mismatch: "已接受提交意图与计费单元任务图不一致",
    multiple_task_submission_intents: "同一任务关联了多个不同提交操作",
  };
  return labels[conflict];
}

function localSourceCoverageLabel(source: string): string {
  const labels: Record<string, string> = {
    ai_tasks: "Task 取消记录",
    billing_units: "计费单元期限与取消记录",
    policy_stop_events: "本地策略停止事件",
    local_continuation_work: "本地后续处理记录",
  };
  return labels[source] ?? "未知本地来源";
}

function workerObservationLabel(status: TaskHistoryTruth["worker"]["status"]): string {
  const labels: Record<TaskHistoryTruth["worker"]["status"], string> = {
    active_lease: "有未过期的 Worker 租约记录",
    expired_lease: "Worker 租约已过期",
    unclaimed: "没有活动 Worker 租约记录",
    unknown: "Worker 状态未知",
    unsupported: "当前版本不支持 Worker 观察",
  };
  return labels[status];
}

function providerObservationLabel(status: TaskHistoryTruth["provider"]["status"]): string {
  const labels: Record<TaskHistoryTruth["provider"]["status"], string> = {
    verified_success: "已有已验证的 Provider 结果证据",
    known_failure: "已有明确的 Provider 失败证据",
    partial: "只有部分计费单元有 Provider 结果证据",
    unknown: "Provider 结果未知",
    unsupported: "当前版本不支持 Provider 结果观察",
  };
  return labels[status];
}

function providerTimingLabel(unit: TaskHistoryTruth["provider"]["units"][number]): string | null {
  const { timelyEvidenceCount, lateEvidenceCount, unknownTimingCount } = unit;
  const evidenceCount = timelyEvidenceCount + lateEvidenceCount + unknownTimingCount;
  if (evidenceCount === 0) return null;
  if (unit.timing === "timely") return `Provider 结果期限内（${timelyEvidenceCount} 条）`;
  if (unit.timing === "late") return `Provider 结果期限后（${lateEvidenceCount} 条）`;
  if (unit.timing === "mixed") {
    return `Provider 时效明细：期限内 ${timelyEvidenceCount} 条、期限后 ${lateEvidenceCount} 条、时间未知 ${unknownTimingCount} 条`;
  }
  return `Provider 结果时间未知（${unknownTimingCount} 条）`;
}

function localDispositionLabel(status: TaskHistoryTruth["localDisposition"]["status"]): string {
  const labels: Record<TaskHistoryTruth["localDisposition"]["status"], string> = {
    observed: "已记录本地处置事实",
    none_recorded: "已读取的来源中没有本地处置记录",
    unknown: "本地处置来源不完整，无法确认",
    unsupported: "当前版本不支持读取本地处置事实",
  };
  return labels[status];
}

function localObservationLabel(status: TaskHistoryTruth["localDisposition"]["observations"][number]["status"]): string {
  const labels: Record<TaskHistoryTruth["localDisposition"]["observations"][number]["status"], string> = {
    cancel_requested: "本地已记录取消请求",
    deadline_elapsed_unsettled: "执行期限已过，单元仍记录为待处理或未知",
    policy_stopped: "本地策略已停止后续处理",
    local_continuation: "存在本地后续处理记录",
  };
  return labels[status];
}

function ResultPanel({
  state,
  onRefresh,
}: {
  state: ReadState<TaskReceipt>;
  onRefresh(): void;
}) {
  const [copyMessage, setCopyMessage] = useState("");
  const resultText = state.snapshot === null ? "" : safeTaskText(state.snapshot.result);

  async function copyResult(): Promise<void> {
    if (state.snapshot === null) return;
    try {
      if (typeof navigator.clipboard?.writeText !== "function") {
        setCopyMessage("当前浏览器不支持复制。");
        return;
      }
      await navigator.clipboard.writeText(resultText);
      setCopyMessage("已复制当前可见的安全文本。");
    } catch {
      setCopyMessage("复制失败，请检查浏览器权限。");
    }
  }

  return (
    <section aria-labelledby="task-detail-result-heading" className="task-detail-section">
      <header className="task-detail-section-heading">
        <div>
          <h3 id="task-detail-result-heading">结果与费用记录</h3>
          <p>详情在本次打开后读取。费用字段是服务器任务快照，不能据此认定实际扣费。</p>
        </div>
        <button
          className="secondary-button"
          disabled={state.loading || state.refreshing}
          onClick={onRefresh}
          type="button"
        >
          {state.refreshing ? "正在刷新…" : "刷新结果记录"}
        </button>
      </header>

      {state.loading && state.snapshot === null && (
        <p aria-live="polite" className="task-detail-state">正在读取结果与费用记录…</p>
      )}
      {state.error !== null && (
        <div className="task-detail-error" role="alert">
          <p>{safeTaskText(presentError(state.error, "receipt"))}</p>
          {state.snapshot !== null && <p>下方仍保留上次读取的旧快照。</p>}
          <button className="secondary-button" disabled={state.loading || state.refreshing} onClick={onRefresh} type="button">
            重试读取结果记录
          </button>
        </div>
      )}
      {state.snapshot !== null && (
        <>
          {state.refreshing && <p aria-live="polite" className="task-detail-state">正在刷新；仍显示上次快照。</p>}
          {state.snapshot.historyTruth.billingScope.legacyQuoteScope === "first_unit" && (
            <p className="task-detail-readonly-note">
              以下报价与账务状态来自首个计费单元；此任务共有 {state.snapshot.historyTruth.billingScope.unitCount} 个计费单元，未汇总为全任务金额。
            </p>
          )}
          {state.snapshot.historyTruth.billingScope.legacyQuoteScope === "task_record" && (
            <p className="task-detail-readonly-note">没有读取到计费单元，以下报价与账务状态来自原 Task 记录。</p>
          )}
          <dl className="task-detail-facts">
            <div><dt>任务状态</dt><dd>{safeTaskText(state.snapshot.status)}</dd></div>
            <div><dt>进度记录</dt><dd>{displayValue(state.snapshot.progress)}</dd></div>
            <div><dt>账务状态记录</dt><dd>{safeTaskText(billingStatusLabel(state.snapshot.billingStatus))}</dd></div>
            <div><dt>服务器报价记录</dt><dd>{state.snapshot.quotedAmount === null ? "未提供" : state.snapshot.quotedAmount + " 积分"}</dd></div>
            <div><dt>任务类型</dt><dd>{safeTaskText(state.snapshot.type)}</dd></div>
            <div><dt>创建时间</dt><dd>{safeTaskText(displayValue(state.snapshot.createdAt))}</dd></div>
            <div><dt>更新时间</dt><dd>{safeTaskText(displayValue(state.snapshot.updatedAt))}</dd></div>
          </dl>
          {state.snapshot.progressMessage !== null && (
            <p className="task-detail-progress-message">{safeTaskText(state.snapshot.progressMessage)}</p>
          )}
          <div className="task-detail-payload">
            <div className="task-detail-payload-heading">
              <h4>完整结果文本</h4>
              <button className="secondary-button" onClick={() => void copyResult()} type="button">
                复制安全结果文本
              </button>
            </div>
            <pre aria-label="安全结果文本">{resultText}</pre>
            {copyMessage !== "" && <p aria-live="polite">{copyMessage}</p>}
          </div>
        </>
      )}
    </section>
  );
}

function HistoryTruthPanel({ truth }: { truth: TaskHistoryTruth }) {
  return (
    <section aria-labelledby="task-detail-history-truth-heading" className="task-detail-section">
      <header className="task-detail-section-heading">
        <div>
          <h3 id="task-detail-history-truth-heading">来源与执行观察</h3>
          <p>只读展示本地保存的来源、Worker 租约和 Provider 证据；不会查询上游 Provider。</p>
        </div>
      </header>
      <dl className="task-detail-facts">
        <div>
          <dt>历史来源</dt>
          <dd>
            {safeTaskText(historySourceLabel(truth.source.status, truth.source.source))}
            {truth.source.unitCount !== null && ` · ${truth.source.unitCount} 个计费单元`}
          </dd>
        </div>
        <div><dt>历史来源依据</dt><dd>{safeTaskText(historyFactSourceLabel(truth.source.source))}</dd></div>
        {truth.source.conflicts.length > 0 && (
          <div>
            <dt>来源冲突</dt>
            <dd>{safeTaskText(truth.source.conflicts.map(historySourceConflictLabel).join("；"))}</dd>
          </div>
        )}
        <div>
          <dt>Worker 执行观察</dt>
          <dd>{safeTaskText(workerObservationLabel(truth.worker.status))}</dd>
        </div>
        <div><dt>Worker 观察来源</dt><dd>{safeTaskText(historyFactSourceLabel(truth.worker.source))}</dd></div>
        <div><dt>恢复观察</dt><dd>{safeTaskText(truth.worker.recoveryStatus)}</dd></div>
        <div><dt>Worker 租约截止</dt><dd>{safeTaskText(displayValue(truth.worker.leaseUntil))}</dd></div>
        <div><dt>观察评估时间</dt><dd>{safeTaskText(displayValue(truth.worker.evaluatedAt))}</dd></div>
        <div>
          <dt>Provider 原始证据</dt>
          <dd>
            {safeTaskText(providerObservationLabel(truth.provider.status))}
            {truth.provider.unitCount !== null && ` · 覆盖 ${truth.provider.verifiedUnitCount}/${truth.provider.unitCount} 个单元`}
          </dd>
        </div>
        <div><dt>Provider 观察来源</dt><dd>{safeTaskText(historyFactSourceLabel(truth.provider.source))}</dd></div>
        <div>
          <dt>本地处置观察</dt>
          <dd>{safeTaskText(localDispositionLabel(truth.localDisposition.status))}</dd>
        </div>
        {truth.localDisposition.sourceCoverage.length > 0 && (
          <div>
            <dt>本地来源覆盖</dt>
            <dd>{safeTaskText(truth.localDisposition.sourceCoverage.map(localSourceCoverageLabel).join("、"))}</dd>
          </div>
        )}
      </dl>

      {truth.provider.units.length > 0 && (
        <ul className="task-detail-history-units">
          {truth.provider.units.map((unit, index) => {
            const timingLabel = providerTimingLabel(unit);
            return (
              <li key={`${unit.operation ?? "unit"}-${index}`}>
                <strong>{safeTaskText(unit.operation ?? `计费单元 ${index + 1}`)}</strong>
                {unit.graphVersion !== null && <span> · {safeTaskText(unit.graphVersion)}</span>}
                <span> · {safeTaskText(providerObservationLabel(unit.status))}</span>
                <span> · 有效 Provider 证据 {unit.providerEvidenceCount} 条</span>
                {unit.unitResultStatus !== "unknown" && <span> · 本地结果记录：{safeTaskText(unit.unitResultStatus)}</span>}
                {timingLabel !== null && <span> · {safeTaskText(timingLabel)}</span>}
                {unit.submissionStatuses.includes("failed") && unit.hasUnverifiedFailedSubmission && (
                  <span> · 提交记录为失败，但缺少独立有效的 Provider 失败证据，因此上游结果仍未知</span>
                )}
                {unit.providers.length > 0 && <span> · Provider：{safeTaskText(unit.providers.join("、"))}</span>}
                {unit.observedAt !== null && <span> · 证据记录时间：{safeTaskText(unit.observedAt)}</span>}
              </li>
            );
          })}
        </ul>
      )}

      {truth.localDisposition.observations.length > 0 && (
        <ul className="task-detail-history-dispositions">
          {truth.localDisposition.observations.map((observation, index) => (
            <li key={`${observation.status}-${index}`}>
              {safeTaskText(localObservationLabel(observation.status))}
              {observation.reason !== null && ` · 原因：${safeTaskText(observation.reason)}`}
              {observation.phase !== null && ` · 阶段：${safeTaskText(observation.phase)}`}
              {observation.recordedAt !== null && ` · ${safeTaskText(observation.recordedAt)}`}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RequestPanel({
  state,
  onRefresh,
}: {
  state: ReadState<TaskRequestRecord>;
  onRefresh(): void;
}) {
  const [copyMessage, setCopyMessage] = useState("");
  const requestText = state.snapshot === null ? "" : safeTaskText(state.snapshot.request);

  async function copyRequest(): Promise<void> {
    if (state.snapshot === null) return;
    try {
      if (typeof navigator.clipboard?.writeText !== "function") {
        setCopyMessage("当前浏览器不支持复制。");
        return;
      }
      await navigator.clipboard.writeText(requestText);
      setCopyMessage("已复制当前可见的安全文本。");
    } catch {
      setCopyMessage("复制失败，请检查浏览器权限。");
    }
  }

  return (
    <section aria-labelledby="task-detail-request-heading" className="task-detail-section">
      <header className="task-detail-section-heading">
        <div>
          <h3 id="task-detail-request-heading">任务请求记录</h3>
          <p>这是已保存的请求业务记录，不代表第三方服务的实际最终载荷。</p>
        </div>
        <button
          className="secondary-button"
          disabled={state.loading || state.refreshing}
          onClick={onRefresh}
          type="button"
        >
          {state.refreshing ? "正在刷新…" : "刷新请求记录"}
        </button>
      </header>

      {state.loading && state.snapshot === null && (
        <p aria-live="polite" className="task-detail-state">正在读取任务请求记录…</p>
      )}
      {state.error !== null && (
        <div className="task-detail-error" role="alert">
          <p>{safeTaskText(presentError(state.error, "request"))}</p>
          {state.snapshot !== null && <p>下方仍保留上次读取的旧快照。</p>}
          <button className="secondary-button" disabled={state.loading || state.refreshing} onClick={onRefresh} type="button">
            重试读取请求记录
          </button>
        </div>
      )}
      {state.snapshot !== null && (
        <>
          {state.refreshing && <p aria-live="polite" className="task-detail-state">正在刷新；仍显示上次快照。</p>}
          <dl className="task-detail-facts">
            <div><dt>任务类型</dt><dd>{safeTaskText(state.snapshot.type)}</dd></div>
            <div><dt>任务状态</dt><dd>{safeTaskText(state.snapshot.status)}</dd></div>
            <div><dt>记录模型</dt><dd>{safeTaskText(displayValue(state.snapshot.modelName))}</dd></div>
            <div><dt>请求积分记录</dt><dd>{state.snapshot.creditCost === null ? "未提供" : state.snapshot.creditCost + " 积分"}</dd></div>
            <div><dt>外部服务</dt><dd>{safeTaskText(displayValue(state.snapshot.externalProvider))}</dd></div>
            <div><dt>外部任务编号</dt><dd>{safeTaskText(displayValue(state.snapshot.externalTaskId))}</dd></div>
            <div><dt>创建时间</dt><dd>{safeTaskText(displayValue(state.snapshot.createdAt))}</dd></div>
          </dl>
          <div className="task-detail-payload">
            <div className="task-detail-payload-heading">
              <h4>完整请求文本</h4>
              <button className="secondary-button" onClick={() => void copyRequest()} type="button">
                复制安全请求文本
              </button>
            </div>
            <pre aria-label="安全请求文本">{requestText}</pre>
            {copyMessage !== "" && <p aria-live="polite">{copyMessage}</p>}
          </div>
        </>
      )}
    </section>
  );
}

export function TaskDetailDialog({
  task,
  userId,
  services,
  sessionGeneration = 0,
  openGeneration,
  onClose,
  onUnauthorized,
  onCancelAttemptMayHaveReachedServer,
  onAutomaticObservationChange,
}: TaskDetailDialogProps) {
  const [receipt, setReceipt] = useState<ReadState<TaskReceipt>>(initialReadState);
  const [request, setRequest] = useState<ReadState<TaskRequestRecord>>(initialReadState);
  const receiptControllerRef = useRef<AbortController | null>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: onClose });

  async function loadReceipt(controller: AbortController): Promise<void> {
    const isCurrent = () => receiptControllerRef.current === controller && !controller.signal.aborted;
    try {
      if (services.getTaskReceipt === undefined) {
        throw new ApiError("invalid-response", "当前服务模式不支持读取任务结果记录。");
      }
      const snapshot = await services.getTaskReceipt(task.id, controller.signal);
      if (isCurrent()) {
        setReceipt({ snapshot, loading: false, refreshing: false, error: null });
      }
    } catch (cause) {
      if (!isCurrent() || isAbortError(cause)) return;
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        onUnauthorizedRef.current();
        return;
      }
      setReceipt((previous) => ({ ...previous, loading: false, refreshing: false, error }));
    }
  }

  async function loadRequest(controller: AbortController): Promise<void> {
    const isCurrent = () => requestControllerRef.current === controller && !controller.signal.aborted;
    try {
      if (services.getTaskRequest === undefined) {
        throw new ApiError("invalid-response", "当前服务模式不支持读取任务请求记录。");
      }
      const snapshot = await services.getTaskRequest(task.id, controller.signal);
      if (isCurrent()) {
        setRequest({ snapshot, loading: false, refreshing: false, error: null });
      }
    } catch (cause) {
      if (!isCurrent() || isAbortError(cause)) return;
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        onUnauthorizedRef.current();
        return;
      }
      setRequest((previous) => ({ ...previous, loading: false, refreshing: false, error }));
    }
  }

  useEffect(() => {
    const receiptController = new AbortController();
    const requestController = new AbortController();
    receiptControllerRef.current = receiptController;
    requestControllerRef.current = requestController;
    setReceipt(initialReadState());
    setRequest(initialReadState());
    void loadReceipt(receiptController);
    void loadRequest(requestController);

    return () => {
      receiptController.abort();
      requestController.abort();
      receiptControllerRef.current?.abort();
      requestControllerRef.current?.abort();
      receiptControllerRef.current = null;
      requestControllerRef.current = null;
    };
  }, [openGeneration, services, sessionGeneration, task.id, userId]);

  async function refreshReceipt(): Promise<void> {
    receiptControllerRef.current?.abort();
    const controller = new AbortController();
    receiptControllerRef.current = controller;
    setReceipt((previous) => ({
      ...previous,
      loading: previous.snapshot === null,
      refreshing: previous.snapshot !== null,
      error: null,
    }));
    await loadReceipt(controller);
  }

  function refreshRequest(): void {
    requestControllerRef.current?.abort();
    const controller = new AbortController();
    requestControllerRef.current = controller;
    setRequest((previous) => ({
      ...previous,
      loading: previous.snapshot === null,
      refreshing: previous.snapshot !== null,
      error: null,
    }));
    void loadRequest(controller);
  }

  return (
    <div className="task-detail-backdrop" role="presentation">
      <section
        aria-labelledby="task-detail-title"
        aria-modal="true"
        className="task-detail-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="task-detail-dialog-header">
          <div>
            <p className="eyebrow">只读任务记录 · {safeTaskText(task.type)}</p>
            <h2 id="task-detail-title">任务详情</h2>
          </div>
          <button
            aria-label="关闭任务详情"
            className="icon-button"
            onClick={focus.dismiss}
            type="button"
          >
            ×
          </button>
        </header>
        <p className="task-detail-readonly-note">
          这里只读取本人任务的服务器记录。关闭窗口会停止本地读取；已发出的取消请求可能仍被服务端接收，重开后只核对，不会自动重发。
        </p>
        <div className="task-detail-sections">
          <TaskControlPanel
            onAutomaticObservationChange={onAutomaticObservationChange}
            onCancelAttemptMayHaveReachedServer={onCancelAttemptMayHaveReachedServer}
            openGeneration={openGeneration}
            onRefreshReceipt={refreshReceipt}
            onUnauthorized={onUnauthorized}
            services={services}
            sessionGeneration={sessionGeneration}
            task={task}
            userId={userId}
          />
          <ResultPanel onRefresh={refreshReceipt} state={receipt} />
          {receipt.snapshot !== null && <HistoryTruthPanel truth={receipt.snapshot.historyTruth} />}
          <RequestPanel onRefresh={refreshRequest} state={request} />
        </div>
      </section>
    </div>
  );
}
