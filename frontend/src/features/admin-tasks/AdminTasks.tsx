import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import {
  ADMIN_TASK_PAGE_SIZE,
  type AdminTaskListState,
  type AdminTaskRecord,
  type AdminTaskSortField,
  type AdminTasksApi,
  type AdminTaskStatusFilter,
  type AdminTaskTypeFilter,
} from "./adminTasksTypes";
import { downloadAdminTasksCsv, serializeAdminTasksCsv } from "./adminTasksExport";
import "./adminTasks.css";

const TYPE_OPTIONS: ReadonlyArray<{ value: Exclude<AdminTaskTypeFilter, "">; label: string }> = [
  { value: "chat", label: "对话" },
  { value: "image", label: "图片" },
  { value: "video", label: "视频" },
  { value: "optimize-frame", label: "优化分镜" },
];

const STATUS_OPTIONS: ReadonlyArray<{ value: Exclude<AdminTaskStatusFilter, "">; label: string }> = [
  { value: "processing", label: "处理中" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "失败" },
];

const COLUMNS: ReadonlyArray<{ field: AdminTaskSortField; label: string }> = [
  { field: "type", label: "类型" },
  { field: "model_name", label: "模型" },
  { field: "status", label: "状态" },
  { field: "credit_cost", label: "消耗积分" },
  { field: "user_id", label: "用户 ID" },
  { field: "created_at", label: "创建时间" },
];

interface TaskFilters {
  userId: string;
  type: AdminTaskTypeFilter;
  status: AdminTaskStatusFilter;
  modelName: string;
}

interface TaskOwner {
  api: AdminTasksApi;
  ownerScope: string;
  viewKey: string;
  queryKey: string;
}

function sameOwner(a: TaskOwner, b: TaskOwner): boolean {
  return a.api === b.api && a.ownerScope === b.ownerScope && a.viewKey === b.viewKey && a.queryKey === b.queryKey;
}

function isAuthenticationError(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.kind === "network") return "无法连接管理后台服务，请检查服务后重试。";
  if (error instanceof ApiError && error.kind === "timeout") return "读取任务列表超时，请稍后重试。";
  return "读取任务列表失败，请重试。";
}

function dateValue(value: string | null): string {
  return value ?? "—";
}

export interface AdminTasksProps {
  api: AdminTasksApi;
  ownerScope: string;
  viewKey: string;
  listState: AdminTaskListState;
  onListStateChange(state: AdminTaskListState): void;
  onAuthenticationError(): void;
}

export function AdminTasks({ api, ownerScope, viewKey, listState, onListStateChange, onAuthenticationError }: AdminTasksProps) {
  const [filters, setFilters] = useState<TaskFilters>(() => ({
    userId: listState.userId,
    type: listState.type,
    status: listState.status,
    modelName: listState.modelName,
  }));
  const [rows, setRows] = useState<AdminTaskRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportNotice, setExportNotice] = useState<string | null>(null);

  const appliedQuery = useMemo(() => ({
    skip: (listState.page - 1) * ADMIN_TASK_PAGE_SIZE,
    limit: ADMIN_TASK_PAGE_SIZE,
    sort_field: listState.sortField,
    sort_order: listState.sortOrder,
    ...(listState.userId !== "" ? { user_id: listState.userId } : {}),
    ...(listState.type !== "" ? { type: listState.type } : {}),
    ...(listState.status !== "" ? { status: listState.status } : {}),
    ...(listState.modelName !== "" ? { model_name: listState.modelName } : {}),
  }), [listState]);
  const queryKey = JSON.stringify(appliedQuery);
  const owner: TaskOwner = { api, ownerScope, viewKey, queryKey };
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const listCallback = useRef(onListStateChange);
  listCallback.current = onListStateChange;
  const requestSequence = useRef(0);
  const exportSequence = useRef(0);
  const exportController = useRef<AbortController | null>(null);

  useEffect(() => {
    setFilters({
      userId: listState.userId,
      type: listState.type,
      status: listState.status,
      modelName: listState.modelName,
    });
  }, [listState.userId, listState.type, listState.status, listState.modelName]);

  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++requestSequence.current;
    const capturedOwner = ownerRef.current;
    let current = true;
    setLoading(true);
    setError(null);
    setRows([]);
    void api.listTasks(appliedQuery, controller.signal).then((result) => {
      if (!current || controller.signal.aborted || sequence !== requestSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      const lastPage = Math.max(1, Math.ceil(result.total / ADMIN_TASK_PAGE_SIZE));
      if (listState.page > lastPage) {
        setTotal(result.total);
        listCallback.current({ ...listState, page: lastPage });
        return;
      }
      setRows(result.data);
      setTotal(result.total);
    }).catch((cause: unknown) => {
      if (!current || controller.signal.aborted || sequence !== requestSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (isAuthenticationError(cause)) authCallback.current();
      else setError(errorMessage(cause));
    }).finally(() => {
      if (current && sequence === requestSequence.current && sameOwner(capturedOwner, ownerRef.current)) setLoading(false);
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [api, appliedQuery, listState, ownerScope, queryKey, retry, viewKey]);

  useEffect(() => {
    exportSequence.current += 1;
    exportController.current?.abort();
    exportController.current = null;
    setExporting(false);
    setExportError(null);
    setExportNotice(null);
    return () => {
      exportSequence.current += 1;
      exportController.current?.abort();
      exportController.current = null;
    };
  }, [api, ownerScope, queryKey, viewKey]);

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    listCallback.current({ ...listState, ...filters, page: 1 });
  }

  function resetFilters() {
    const reset: TaskFilters = { userId: "", type: "", status: "", modelName: "" };
    setFilters(reset);
    listCallback.current({ ...listState, ...reset, page: 1 });
  }

  function changeSort(field: AdminTaskSortField) {
    const sortOrder = listState.sortField === field
      ? listState.sortOrder === "asc" ? "desc" : "asc"
      : "asc";
    listCallback.current({ ...listState, sortField: field, sortOrder, page: 1 });
  }

  async function exportTasks() {
    if (exportController.current) return;
    const capturedOwner = ownerRef.current;
    const sequence = ++exportSequence.current;
    const controller = new AbortController();
    exportController.current = controller;
    setExporting(true);
    setExportError(null);
    setExportNotice(null);
    try {
      const result = await api.listTasks({ ...appliedQuery, skip: 0, limit: 1000 }, controller.signal);
      if (controller.signal.aborted || sequence !== exportSequence.current || !sameOwner(capturedOwner, ownerRef.current)) return;
      const records = result.data.slice(0, 1000);
      downloadAdminTasksCsv(serializeAdminTasksCsv(records), "admin-tasks.csv");
      setExportNotice(result.total > records.length
        ? `已导出 ${records.length} 条，符合条件共 ${result.total} 条。`
        : `已导出 ${records.length} 条任务。`);
    } catch (cause) {
      if (controller.signal.aborted || sequence !== exportSequence.current || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (isAuthenticationError(cause)) authCallback.current();
      else setExportError(errorMessage(cause));
    } finally {
      if (sequence === exportSequence.current && sameOwner(capturedOwner, ownerRef.current)) {
        exportController.current = null;
        setExporting(false);
      }
    }
  }

  const pageCount = Math.max(1, Math.ceil(total / ADMIN_TASK_PAGE_SIZE));

  return (
    <section className="admin-tasks-page" aria-label="全局任务列表">
      <header className="admin-page-heading admin-tasks-heading">
        <div>
          <p className="admin-eyebrow">运营数据</p>
          <h1>全局任务</h1>
          <p>只读查看全站任务状态与消耗信息，不提供恢复、取消或删除操作。</p>
        </div>
        <button className="admin-button admin-button-secondary" type="button" onClick={() => void exportTasks()} disabled={exporting || loading}>
          {exporting ? "导出中…" : "导出 CSV（最多1000条）"}
        </button>
      </header>

      <form className="admin-tasks-filters" onSubmit={applyFilters}>
        <label>
          用户 ID
          <input
            aria-label="按用户 ID 筛选"
            value={filters.userId}
            onChange={(event) => setFilters((current) => ({ ...current, userId: event.target.value }))}
          />
        </label>
        <label>
          类型
          <select
            aria-label="按类型筛选"
            value={filters.type}
            onChange={(event) => setFilters((current) => ({ ...current, type: event.target.value as AdminTaskTypeFilter }))}
          >
            <option value="">全部类型</option>
            {TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label>
          状态
          <select
            aria-label="按状态筛选"
            value={filters.status}
            onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value as AdminTaskStatusFilter }))}
          >
            <option value="">全部状态</option>
            {STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label>
          模型名称
          <input
            aria-label="按模型名称筛选"
            value={filters.modelName}
            onChange={(event) => setFilters((current) => ({ ...current, modelName: event.target.value }))}
          />
        </label>
        <div className="admin-tasks-filter-actions">
          <button className="admin-button admin-button-primary" type="submit">查询</button>
          <button className="admin-button admin-button-secondary" type="button" onClick={resetFilters}>重置</button>
        </div>
      </form>

      {exportError ? <p className="admin-tasks-error" role="alert">{exportError}</p> : null}
      {exportNotice ? <p className="admin-tasks-notice" role="status">{exportNotice}</p> : null}
      {loading ? <section className="admin-state-card" role="status">正在读取全局任务…</section> : null}
      {!loading && error ? <section className="admin-state-card" role="alert">
        <p>{error}</p><button className="admin-button admin-button-secondary" type="button" onClick={() => setRetry((value) => value + 1)}>重试读取</button>
      </section> : null}
      {!loading && !error && rows.length === 0 ? (
        <section className="admin-state-card" aria-label="空记录">
          <strong>暂无任务</strong>
          <p>当前筛选条件下没有任务记录。</p>
        </section>
      ) : null}
      {!loading && !error && rows.length > 0 ? <div className="admin-tasks-table-wrap">
        <table className="admin-tasks-table">
          <thead>
            <tr>{COLUMNS.map((column) => (
              <th
                key={column.field}
                aria-sort={listState.sortField === column.field
                  ? listState.sortOrder === "asc" ? "ascending" : "descending"
                  : "none"}
              >
                <button type="button" aria-label={`按${column.label}排序`} onClick={() => changeSort(column.field)}>
                  {column.label}{listState.sortField === column.field ? ` ${listState.sortOrder === "asc" ? "↑" : "↓"}` : ""}
                </button>
              </th>
            ))}</tr>
          </thead>
          <tbody>{rows.map((task) => <tr key={task.id}>
            <td>{task.type}</td><td>{task.model_name || "—"}</td><td>{task.status}</td>
            <td>{task.credit_cost}</td><td>{task.user_id}</td><td>{dateValue(task.created_at)}</td>
          </tr>)}</tbody>
        </table>
      </div> : null}

      <footer className="admin-tasks-pagination">
        <span>共 {total} 条 · 第 {listState.page} / {pageCount} 页</span>
        <button
          className="admin-button admin-button-secondary"
          type="button"
          disabled={loading || listState.page <= 1}
          onClick={() => listCallback.current({ ...listState, page: Math.max(1, listState.page - 1) })}
        >上一页</button>
        <button
          className="admin-button admin-button-secondary"
          type="button"
          disabled={loading || listState.page >= pageCount}
          onClick={() => listCallback.current({ ...listState, page: listState.page + 1 })}
        >下一页</button>
      </footer>
    </section>
  );
}
