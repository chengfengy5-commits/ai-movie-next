import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import type { AdminCreditsApi, AdminUserCredit, AdminUserCreditSortField } from "./adminCreditsTypes";
import "./Credits.css";

const PAGE_SIZE = 10;
type SortOrder = "asc" | "desc";

const creditColumns: { field: AdminUserCreditSortField; label: string }[] = [
  { field: "user_id", label: "用户 ID" },
  { field: "credits", label: "剩余积分" },
  { field: "updated_at", label: "更新时间" },
];

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

function isExplicitRejection(error: unknown): error is ApiError {
  return error instanceof ApiError
    && error.kind === "http"
    && error.status !== null
    && error.status >= 400
    && error.status < 500;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

export interface UserCreditsListProps {
  api: AdminCreditsApi;
  reloadVersion: number;
  onEdit(creditId: string): void;
  onAuthenticationError(): void;
}

export function UserCreditsList({ api, reloadVersion, onEdit, onAuthenticationError }: UserCreditsListProps) {
  const [page, setPage] = useState(1);
  const [sortField, setSortField] = useState<AdminUserCreditSortField>("updated_at");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [rows, setRows] = useState<AdminUserCredit[]>([]);
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

    void api.listUserCredits({
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
      setLoadError(errorText(cause));
    }).finally(() => {
      if (current) setLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, onAuthenticationError, page, reloadVersion, retry, sortField, sortOrder]);

  function changeSort(field: AdminUserCreditSortField) {
    if (field === sortField) setSortOrder((value) => value === "asc" ? "desc" : "asc");
    else {
      setSortField(field);
      setSortOrder("asc");
    }
    setPage(1);
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="admin-credits-page" aria-label="用户积分">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">用户管理</p>
          <h1>用户积分</h1>
          <p>查看并编辑用户当前积分余额。</p>
        </div>
      </header>

      {loading ? <section className="admin-state-card" role="status">正在读取用户积分…</section> : null}
      {!loading && loadError ? (
        <section className="admin-state-card" role="alert">
          <p>{loadError}</p>
          <button className="admin-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新读取</button>
        </section>
      ) : null}
      {!loading && !loadError && rows.length === 0 ? (
        <section className="admin-state-card" aria-label="空记录">
          <strong>暂无积分记录</strong>
          <p>当前没有可显示的用户积分。</p>
        </section>
      ) : null}
      {!loading && !loadError && rows.length > 0 ? (
        <div className="admin-table-wrap">
          <table className="admin-model-table" aria-label="用户积分列表">
            <thead>
              <tr>
                {creditColumns.map(({ field, label }) => (
                  <th key={field} scope="col" aria-sort={sortField === field ? (sortOrder === "asc" ? "ascending" : "descending") : "none"}>
                    <button className="admin-credit-sort-button" type="button" onClick={() => changeSort(field)}>
                      {label}<span aria-hidden="true">{sortField === field ? (sortOrder === "asc" ? " ↑" : " ↓") : ""}</span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <button
                      className="admin-table-link"
                      type="button"
                      aria-label={`编辑积分记录 ${row.id}`}
                      onClick={() => onEdit(row.id)}
                    >
                      {row.user_id}
                    </button>
                  </td>
                  <td>{row.credits.toLocaleString("zh-CN")}</td>
                  <td>{formatDate(row.updated_at)}</td>
                </tr>
              ))}
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

export interface UserCreditEditorProps {
  api: AdminCreditsApi;
  creditId: string;
  onCancel(): void;
  onSaved(): void;
  onAuthenticationError(): void;
}

export function UserCreditEditor({ api, creditId, onCancel, onSaved, onAuthenticationError }: UserCreditEditorProps) {
  const [credit, setCredit] = useState<AdminUserCredit | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [checkingCurrent, setCheckingCurrent] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [recheckError, setRecheckError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reloadVersion, setReloadVersion] = useState(0);
  const saveInFlight = useRef(false);
  const saveController = useRef<AbortController | null>(null);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      saveController.current?.abort();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    const initialRead = reloadVersion === 0;
    if (initialRead) setLoading(true);
    else setCheckingCurrent(true);
    setLoadError(null);
    if (!initialRead) setRecheckError(null);

    void api.getUserCredit(creditId, controller.signal).then((result) => {
      if (!current) return;
      setCredit(result);
      setDraft(String(result.credits));
      setFormError(null);
      setOutcomeUnknown(false);
      if (!initialRead) {
        setSaveError(null);
        setSaveNotice("已重新读取当前积分，请核对后再决定是否保存。");
      }
    }).catch((cause: unknown) => {
      if (!current || controller.signal.aborted) return;
      if (isAuthenticationError(cause)) {
        onAuthenticationError();
      } else if (initialRead || !credit) {
        setLoadError(errorText(cause));
      } else {
        setRecheckError(errorText(cause));
      }
    }).finally(() => {
      if (!current) return;
      if (initialRead) setLoading(false);
      else setCheckingCurrent(false);
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, creditId, onAuthenticationError, reloadVersion]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saveInFlight.current || !credit) return;
    const integerText = draft.trim();
    const value = /^[+-]?\d+$/.test(integerText) ? Number(integerText) : Number.NaN;
    if (!Number.isSafeInteger(value)) {
      setFormError("请输入有效的整数积分。空值和小数无法保存。");
      setSaveNotice(null);
      return;
    }

    saveInFlight.current = true;
    setSaving(true);
    setFormError(null);
    setSaveError(null);
    setSaveNotice(null);
    setOutcomeUnknown(false);
    const controller = new AbortController();
    saveController.current = controller;
    try {
      await api.updateUserCredit(creditId, value, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      onSaved();
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted) return;
      if (isAuthenticationError(cause)) {
        onAuthenticationError();
      } else if (isExplicitRejection(cause)) {
        setSaveError(`保存请求已被拒绝：${errorText(cause)}`);
      } else {
        setOutcomeUnknown(true);
        setSaveError(`${errorText(cause)} 保存状态尚未确认，已保留你输入的积分。请先重新读取当前积分，再决定是否再次保存。系统不会自动重复提交。`);
      }
    } finally {
      saveInFlight.current = false;
      if (saveController.current === controller) saveController.current = null;
      if (mounted.current) setSaving(false);
    }
  }

  if (loading || (checkingCurrent && !credit)) return <section className="admin-state-card" role="status">正在读取积分记录…</section>;
  if (loadError || !credit) {
    return (
      <section className="admin-credits-page" aria-label="编辑用户积分">
        <header className="admin-page-heading"><div><p className="admin-eyebrow">用户积分</p><h1>编辑积分</h1></div></header>
        <section className="admin-state-card" role="alert">
          <p>{loadError ?? "积分记录不存在。"}</p>
          <div className="admin-credit-actions">
            <button className="admin-button" type="button" onClick={() => setReloadVersion((value) => value + 1)}>重新读取</button>
            <button className="admin-button admin-button-secondary" type="button" onClick={onCancel}>返回列表</button>
          </div>
        </section>
      </section>
    );
  }

  return (
    <section className="admin-credits-page" aria-label="编辑用户积分">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">用户积分</p>
          <h1>编辑积分</h1>
          <p>为用户 {credit.user_id} 设置当前积分余额。</p>
        </div>
      </header>
      {saveNotice ? <p className="admin-credit-notice" role="status">{saveNotice}</p> : null}
      {saveError ? (
        <section className={outcomeUnknown ? "admin-credit-error is-uncertain" : "admin-credit-error"} role="alert">
          <p>{saveError}</p>
          {outcomeUnknown ? (
            <div className="admin-credit-actions">
              <button className="admin-button" type="button" disabled={checkingCurrent} onClick={() => setReloadVersion((value) => value + 1)}>
                {checkingCurrent ? "正在读取…" : "重新读取当前积分"}
              </button>
              {recheckError ? <span>{recheckError}</span> : null}
            </div>
          ) : null}
        </section>
      ) : null}
      <form className="admin-credit-form" onSubmit={(event) => { void save(event); }}>
        <label className="admin-field" htmlFor="admin-credit-user-id">
          <span>用户 ID</span>
          <input id="admin-credit-user-id" value={credit.user_id} readOnly />
        </label>
        <label className="admin-field" htmlFor="admin-credit-amount">
          <span>积分数量</span>
          <input
            id="admin-credit-amount"
            type="text"
            inputMode="numeric"
            value={draft}
            aria-describedby="admin-credit-amount-hint"
            disabled={saving || checkingCurrent}
            onChange={(event) => {
              setDraft(event.target.value);
              setFormError(null);
            }}
          />
        </label>
        <small id="admin-credit-amount-hint">可设置为 0 或负数。</small>
        {formError ? <p className="admin-form-error" role="alert">{formError}</p> : null}
        <div className="admin-credit-actions">
          <button className="admin-button admin-button-primary" type="submit" disabled={saving || checkingCurrent || outcomeUnknown}>
            {saving ? "正在保存…" : "保存"}
          </button>
          <button className="admin-button admin-button-secondary" type="button" onClick={onCancel}>返回列表</button>
        </div>
      </form>
    </section>
  );
}
