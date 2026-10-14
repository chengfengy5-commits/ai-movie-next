import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type {
  AdminUserRelatedRecord,
  AdminUserRelatedResource,
  AdminUsersApi,
} from "./usersTypes";

const PAGE_SIZE = 10;

type SortOrder = "asc" | "desc";

type Column = { field: string; label: string };

const columnsByResource: Record<AdminUserRelatedResource, Column[]> = {
  "credit-logs": [
    { field: "type", label: "类型" },
    { field: "amount", label: "变动积分" },
    { field: "balance_after", label: "变动后余额" },
    { field: "description", label: "说明" },
    { field: "task_id", label: "任务 ID" },
    { field: "created_at", label: "时间" },
  ],
  "model-configs": [
    { field: "category", label: "分类" },
    { field: "name", label: "名称" },
    { field: "provider", label: "服务商" },
    { field: "model", label: "模型" },
    { field: "sort_order", label: "排序" },
    { field: "created_at", label: "创建时间" },
  ],
  "prompt-configs": [
    { field: "category", label: "分类" },
    { field: "name", label: "名称" },
    { field: "sort_order", label: "排序" },
    { field: "created_at", label: "创建时间" },
  ],
  tasks: [
    { field: "type", label: "类型" },
    { field: "status", label: "状态" },
    { field: "model_name", label: "模型" },
    { field: "credit_cost", label: "消耗积分" },
    { field: "created_at", label: "创建时间" },
  ],
  series: [
    { field: "name", label: "名称" },
    { field: "description", label: "简介" },
    { field: "created_at", label: "创建时间" },
  ],
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "network") return "无法连接管理后台服务，请检查服务后重试。";
    if (error.kind === "timeout") return "请求超时，请确认服务状态后重试。";
    return error.detail ?? error.message;
  }
  return "请求失败，请重试。";
}

function isAuthenticationError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString("zh-CN");
  }
  return String(value);
}

function readCell(record: AdminUserRelatedRecord, field: string): unknown {
  return (record as unknown as Record<string, unknown>)[field];
}

export interface UserRelatedRecordsProps {
  api: AdminUsersApi;
  resource: AdminUserRelatedResource;
  userId: string;
  onAuthenticationError(): void;
}

export function UserRelatedRecords({ api, resource, userId, onAuthenticationError }: UserRelatedRecordsProps) {
  const [page, setPage] = useState(1);
  const [sortField, setSortField] = useState("created_at");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [rows, setRows] = useState<AdminUserRelatedRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const columns = useMemo(() => columnsByResource[resource], [resource]);

  useEffect(() => {
    if (!userId.trim()) {
      setRows([]);
      setTotal(0);
      setError("缺少用户编号，无法读取关联数据。");
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setError(null);
    setRows([]);

    void api.listRelatedRecords(resource, userId, {
      skip: (page - 1) * PAGE_SIZE,
      limit: PAGE_SIZE,
      sort_field: sortField,
      sort_order: sortOrder,
    }, controller.signal).then((result) => {
      if (!current) return;
      const lastPage = Math.max(1, Math.ceil(result.total / PAGE_SIZE));
      if (page > lastPage) {
        setTotal(result.total);
        setPage(lastPage);
        return;
      }
      setRows(result.data);
      setTotal(result.total);
    }).catch((cause: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (isAuthenticationError(cause)) {
        onAuthenticationError();
        return;
      }
      setError(errorMessage(cause));
    }).finally(() => {
      if (current) setLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, onAuthenticationError, page, resource, retry, sortField, sortOrder, userId]);

  function changeSort(field: string) {
    if (field === sortField) setSortOrder((value) => value === "asc" ? "desc" : "asc");
    else {
      setSortField(field);
      setSortOrder("asc");
    }
    setPage(1);
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="admin-user-related" aria-label="用户关联数据">
      <header className="admin-user-related-heading">
        <div>
          <h2>{resourceLabel(resource)}</h2>
          <p>仅显示当前编辑用户的记录。</p>
        </div>
        <button className="admin-button admin-button-secondary" type="button" onClick={() => setRetry((value) => value + 1)} disabled={loading}>
          重新读取
        </button>
      </header>

      {loading ? <section className="admin-state-card" role="status">正在读取关联数据…</section> : null}
      {!loading && error ? (
        <section className="admin-state-card" role="alert">
          <p>{error}</p>
          <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>重试读取</button>
        </section>
      ) : null}
      {!loading && !error && rows.length === 0 ? (
        <section className="admin-state-card admin-users-empty" aria-label="空记录">
          <strong>暂无记录</strong>
          <p>该用户当前没有此类数据。</p>
        </section>
      ) : null}
      {!loading && !error && rows.length > 0 ? (
        <div className="admin-table-wrap admin-user-related-table-wrap">
            <table className="admin-users-table" aria-label={resourceLabel(resource)}>
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column.field} scope="col" aria-sort={sortField === column.field ? (sortOrder === "asc" ? "ascending" : "descending") : "none"}>
                      <button className="admin-users-sort-button" type="button" onClick={() => changeSort(column.field)}>
                        {column.label}
                        <span aria-hidden="true">{sortField === column.field ? (sortOrder === "asc" ? " ↑" : " ↓") : ""}</span>
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((record, index) => (
                  <tr key={String(record.id) + ":" + index}>
                    {columns.map((column) => <td key={column.field}>{displayValue(readCell(record, column.field))}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
      ) : null}
      {!loading && !error ? (
        <footer className="admin-table-footer">
            <span>共 {total.toLocaleString("zh-CN")} 条记录</span>
            <div className="admin-pagination">
              <button className="admin-button admin-button-secondary" type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
              <span>第 {page} / {pageCount} 页</span>
              <button className="admin-button admin-button-secondary" type="button" disabled={page >= pageCount || loading} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>下一页</button>
            </div>
        </footer>
      ) : null}
    </section>
  );
}

function resourceLabel(resource: AdminUserRelatedResource): string {
  switch (resource) {
    case "credit-logs": return "积分流水";
    case "model-configs": return "模型配置";
    case "prompt-configs": return "提示词配置";
    case "tasks": return "AI 任务";
    case "series": return "剧集";
  }
}
