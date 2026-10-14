import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import type { AdminCreditLog, AdminCreditLogSortField, AdminCreditsApi } from "./adminCreditsTypes";
import "./Credits.css";

const PAGE_SIZE = 10;
type SortOrder = "asc" | "desc";

const logColumns: { field: AdminCreditLogSortField; label: string }[] = [
  { field: "type", label: "类型" },
  { field: "amount", label: "变动" },
  { field: "balance_after", label: "变动后余额" },
  { field: "description", label: "说明" },
  { field: "created_at", label: "时间" },
];

const knownTypeLabels = new Map<string, string>([
  ["charge", "充值"],
  ["usage", "消耗"],
  ["refund", "退款"],
]);

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "network") return "无法连接管理后台服务，请稍后重试。";
    if (error.kind === "timeout") return "请求超时，请稍后重试。";
    return error.detail ?? error.message;
  }
  return "请求失败，请稍后重试。";
}

function isAuthenticationError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

function displayType(type: string): string {
  if (!type) return "—";
  return knownTypeLabels.get(type) ?? type;
}

function displayDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function amountText(amount: number): string {
  return amount > 0 ? `+${amount.toLocaleString("zh-CN")}` : amount.toLocaleString("zh-CN");
}

export interface CreditLogsProps {
  api: AdminCreditsApi;
  onAuthenticationError(): void;
}

export function CreditLogs({ api, onAuthenticationError }: CreditLogsProps) {
  const [page, setPage] = useState(1);
  const [sortField, setSortField] = useState<AdminCreditLogSortField>("created_at");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [draftUserId, setDraftUserId] = useState("");
  const [userId, setUserId] = useState("");
  const [rows, setRows] = useState<AdminCreditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setLoadError(null);
    setRows([]);

    void api.listCreditLogs({
      skip: (page - 1) * PAGE_SIZE,
      limit: PAGE_SIZE,
      sort_field: sortField,
      sort_order: sortOrder,
      ...(userId ? { user_id: userId } : {}),
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
      setLoadError(errorText(cause));
    }).finally(() => {
      if (current) setLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, onAuthenticationError, page, retry, sortField, sortOrder, userId]);

  function applyFilter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setUserId(draftUserId.trim());
    setPage(1);
  }

  function changeSort(field: AdminCreditLogSortField) {
    if (field === sortField) setSortOrder((value) => value === "asc" ? "desc" : "asc");
    else {
      setSortField(field);
      setSortOrder("asc");
    }
    setPage(1);
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="admin-credits-page" aria-label="积分流水">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">用户管理</p>
          <h1>积分流水</h1>
          <p>查看积分变动记录，可按用户 ID 筛选。</p>
        </div>
      </header>

      <form className="admin-credit-filter" onSubmit={applyFilter}>
        <label htmlFor="admin-credit-log-user-id">用户 ID</label>
        <input
          id="admin-credit-log-user-id"
          value={draftUserId}
          onChange={(event) => setDraftUserId(event.target.value)}
          placeholder="输入用户 ID"
        />
        <button className="admin-button" type="submit">查询</button>
        <button
          className="admin-button admin-button-secondary"
          type="button"
          onClick={() => {
            setDraftUserId("");
            setUserId("");
            setPage(1);
          }}
        >
          清除筛选
        </button>
      </form>

      {loading ? <section className="admin-state-card" role="status">正在读取积分流水…</section> : null}
      {!loading && loadError ? (
        <section className="admin-state-card" role="alert">
          <p>{loadError}</p>
          <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新读取</button>
        </section>
      ) : null}
      {!loading && !loadError && rows.length === 0 ? (
        <section className="admin-state-card" aria-label="空记录">
          <strong>暂无积分流水</strong>
          <p>{userId ? "该用户当前没有流水记录。" : "当前没有可显示的积分流水。"}</p>
        </section>
      ) : null}
      {!loading && !loadError && rows.length > 0 ? (
        <div className="admin-table-wrap">
          <table className="admin-model-table" aria-label="积分流水列表">
            <thead>
              <tr>
                {logColumns.map(({ field, label }) => (
                  <th key={field} scope="col" aria-sort={sortField === field ? (sortOrder === "asc" ? "ascending" : "descending") : "none"}>
                    <button className="admin-credit-sort-button" type="button" onClick={() => changeSort(field)}>
                      {label}<span aria-hidden="true">{sortField === field ? (sortOrder === "asc" ? " ↑" : " ↓") : ""}</span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => <CreditLogRow key={row.id} row={row} />)}
            </tbody>
          </table>
          <footer className="admin-table-footer">
            <span>共 {total.toLocaleString("zh-CN")} 条记录</span>
            <div className="admin-pagination">
              <button className="admin-button admin-button-secondary" type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
              <span>第 {page} / {pageCount} 页</span>
              <button className="admin-button admin-button-secondary" type="button" disabled={page >= pageCount || loading} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>下一页</button>
            </div>
          </footer>
        </div>
      ) : null}
    </section>
  );
}

function CreditLogRow({ row }: { row: AdminCreditLog }) {
  return (
    <tr>
      <td>{displayType(row.type)}</td>
      <td className={row.amount > 0 ? "admin-credit-amount is-positive" : row.amount < 0 ? "admin-credit-amount is-negative" : "admin-credit-amount"}>
        {amountText(row.amount)}
      </td>
      <td>{row.balance_after.toLocaleString("zh-CN")}</td>
      <td>{row.description ?? "—"}</td>
      <td>{displayDate(row.created_at)}</td>
    </tr>
  );
}
