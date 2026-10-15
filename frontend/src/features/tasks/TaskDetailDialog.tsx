import { useEffect, useRef, useState } from "react";
import { ApiError, isAbortError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import type { MyTaskRecord } from "../../shared/api/myTasks";
import { safeTaskText, type TaskReceipt, type TaskRequestRecord } from "../../shared/api/taskDetails";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";
import "./taskDetailDialog.css";

interface TaskDetailDialogProps {
  task: MyTaskRecord;
  userId: string;
  services: WorkspaceServices;
  openGeneration: number;
  onClose(): void;
  onUnauthorized(): void;
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
  openGeneration,
  onClose,
  onUnauthorized,
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
  }, [openGeneration, services, task.id, userId]);

  function refreshReceipt(): void {
    receiptControllerRef.current?.abort();
    const controller = new AbortController();
    receiptControllerRef.current = controller;
    setReceipt((previous) => ({
      ...previous,
      loading: previous.snapshot === null,
      refreshing: previous.snapshot !== null,
      error: null,
    }));
    void loadReceipt(controller);
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
          这里仅查看本人任务的服务器记录。关闭窗口或切换任务会停止未完成读取。
        </p>
        <div className="task-detail-sections">
          <ResultPanel onRefresh={refreshReceipt} state={receipt} />
          <RequestPanel onRefresh={refreshRequest} state={request} />
        </div>
      </section>
    </div>
  );
}
