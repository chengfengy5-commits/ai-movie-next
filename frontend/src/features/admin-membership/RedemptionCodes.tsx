import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import { downloadMembershipCsv, serializeRedemptionCodesCsv } from "./redemptionExport";
import type {
  AdminMembershipApi,
  AdminMembershipExitSnapshot,
  AdminRedemptionCode,
  PendingMembershipExit,
  RedemptionCodeQuery,
  RedemptionSortField,
} from "./adminMembershipTypes";
import "./adminMembership.css";

const PAGE_SIZE = 25;
const API_OWNER_IDS = new WeakMap<AdminMembershipApi, number>();
let nextApiOwnerId = 0;

function apiOwnerId(api: AdminMembershipApi): number {
  const existing = API_OWNER_IDS.get(api);
  if (existing !== undefined) return existing;
  const created = ++nextApiOwnerId;
  API_OWNER_IDS.set(api, created);
  return created;
}
const SORT_FIELDS: Array<[RedemptionSortField, string]> = [
  ["id", "ID"], ["code", "兑换码"], ["type", "类型"], ["value", "面值"],
  ["is_used", "使用状态"], ["used_by", "使用者"], ["used_at", "使用时间"],
  ["created_by", "创建管理员"], ["expires_at", "过期时间"], ["created_at", "创建时间"],
];

export type RedemptionCodesMode = "list" | "create";

export interface RedemptionCodesProps {
  api: AdminMembershipApi;
  mode: RedemptionCodesMode;
  ownerScope: string;
  viewKey: string;
  currentAdministratorId: string;
  listVersion?: number;
  onAuthenticationError(): void;
  onCreate(): void;
  onCancel(pending?: PendingMembershipExit): void;
  onSaved(): void;
  onExitStateChange?(snapshot: AdminMembershipExitSnapshot): void;
}

interface Owner {
  api: AdminMembershipApi;
  scope: string;
  view: string;
  mode: RedemptionCodesMode;
  administratorId: string;
}

function sameOwner(left: Owner, right: Owner): boolean {
  return left.api === right.api && left.scope === right.scope && left.view === right.view
    && left.mode === right.mode && left.administratorId === right.administratorId;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "network") return "无法连接管理后台服务，请检查服务后重试。";
    if (error.kind === "timeout") return "请求超时，请确认服务状态后重试。";
    if (error.kind === "invalid-response") return error.message;
    return error.detail ?? error.message;
  }
  return "请求失败，请重试。";
}

function definiteRejection(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "http"
    && [400, 401, 403, 404, 422].includes(error.status ?? -1);
}

function formatDate(value: string | null): string {
  if (value === null || value === "") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

function codeState(record: AdminRedemptionCode): string {
  if (record.is_used) return "已使用";
  if (record.expires_at !== null) {
    const expiry = Date.parse(record.expires_at);
    if (!Number.isNaN(expiry) && expiry < Date.now()) return "已过期";
  }
  return "未使用";
}

interface CommonProps {
  api: AdminMembershipApi;
  ownerScope: string;
  viewKey: string;
  currentAdministratorId: string;
  listVersion: number;
  onAuthenticationError(): void;
  onCancel(pending?: PendingMembershipExit): void;
  onSaved(): void;
  onExitStateChange?(snapshot: AdminMembershipExitSnapshot): void;
}

function SortHeader({
  field, label, activeField, order, onSort,
}: {
  field: RedemptionSortField;
  label: string;
  activeField: RedemptionSortField;
  order: "asc" | "desc";
  onSort(field: RedemptionSortField): void;
}) {
  const active = field === activeField;
  return (
    <th scope="col" aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className="admin-users-sort-button" onClick={() => onSort(field)}
        aria-label={"按" + label + "排序"}>
        {label}<span aria-hidden="true">{active ? (order === "asc" ? " ↑" : " ↓") : ""}</span>
      </button>
    </th>
  );
}

function RedemptionCodeList(props: CommonProps & { onCreate(): void }) {
  const { api, ownerScope, viewKey, currentAdministratorId, listVersion, onAuthenticationError, onCreate, onSaved, onExitStateChange } = props;
  const owner: Owner = { api, scope: ownerScope, view: viewKey, mode: "list", administratorId: currentAdministratorId };
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const savedCallback = useRef(onSaved);
  savedCallback.current = onSaved;
  const exitCallback = useRef(onExitStateChange);
  exitCallback.current = onExitStateChange;

  const [filters, setFilters] = useState({ code: "", type: "", is_used: "", value: "" });
  const [appliedFilters, setAppliedFilters] = useState({ code: "", type: "", is_used: "", value: "" });
  const [page, setPage] = useState(1);
  const [sortField, setSortField] = useState<RedemptionSortField>("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [rows, setRows] = useState<AdminRedemptionCode[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [writeNotice, setWriteNotice] = useState<string | null>(null);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [unknown, setUnknown] = useState<{ id: string; message: string } | null>(null);
  const [checkNotice, setCheckNotice] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const exportSequence = useRef(0);
  const exportController = useRef<AbortController | null>(null);
  const checkController = useRef<AbortController | null>(null);
  const writeController = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const writeAttempt = useRef<{ owner: Owner; id: number } | null>(null);
  const attemptSequence = useRef(0);

  const numericValue = appliedFilters.value === "" ? undefined : Number(appliedFilters.value);
  const query = useMemo<RedemptionCodeQuery>(() => ({
    skip: (page - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
    sort_field: sortField,
    sort_order: sortOrder,
    ...(appliedFilters.code !== "" ? { code: appliedFilters.code } : {}),
    ...(appliedFilters.type !== "" ? { type: appliedFilters.type } : {}),
    ...(appliedFilters.is_used !== "" ? { is_used: appliedFilters.is_used === "true" } : {}),
    ...(numericValue !== undefined ? { value: numericValue } : {}),
  }), [page, sortField, sortOrder, appliedFilters, numericValue]);
  const currentQuery = useRef(query);
  currentQuery.current = query;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestSequence.current += 1;
      exportSequence.current += 1;
      exportController.current?.abort();
      checkController.current?.abort();
      writeController.current?.abort();
      exportController.current = null;
    };
  }, []);

  useEffect(() => {
    exportSequence.current += 1;
    exportController.current?.abort();
    exportController.current = null;
    setExportBusy(false);
    setExportError(null);
    setExportNotice(null);
  }, [query]);

  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++requestSequence.current;
    const requestOwner = ownerRef.current;
    let current = true;
    setLoading(true);
    setLoadError(null);
    void api.listRedemptionCodes(query, controller.signal).then((result) => {
      if (!current || controller.signal.aborted || requestSequence.current !== sequence
        || !sameOwner(requestOwner, ownerRef.current) || currentQuery.current !== query) return;
      setRows(result.data);
      setTotal(result.total);
      setLoading(false);
    }).catch((error: unknown) => {
      if (!current || controller.signal.aborted || requestSequence.current !== sequence
        || !sameOwner(requestOwner, ownerRef.current) || currentQuery.current !== query) return;
      if (error instanceof ApiError && [401, 403].includes(error.status ?? -1)) {
        authCallback.current();
        return;
      }
      setLoadError(errorMessage(error));
      setLoading(false);
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [api, ownerScope, viewKey, currentAdministratorId, query, listVersion, reload]);

  async function exportCodes() {
    if (exportBusy || exportController.current) return;
    const capturedQuery = query;
    const capturedOwner = ownerRef.current;
    const controller = new AbortController();
    const sequence = ++exportSequence.current;
    exportController.current = controller;
    setExportBusy(true);
    setExportError(null);
    setExportNotice(null);
    try {
      const result = await api.listRedemptionCodes({ ...capturedQuery, skip: 0, limit: 1000 }, controller.signal);
      if (controller.signal.aborted || exportSequence.current !== sequence
        || !sameOwner(capturedOwner, ownerRef.current) || currentQuery.current !== capturedQuery) return;
      const records = result.data.slice(0, 1000);
      downloadMembershipCsv(serializeRedemptionCodesCsv(records),
        "haoai-redemption-codes-" + new Date().toISOString().slice(0, 10) + ".csv");
      setExportNotice(result.total > records.length
        ? "已导出前 " + records.length + " 条，共 " + result.total + " 条符合当前筛选。"
        : "已导出 " + records.length + " 条兑换码记录。");
    } catch (error) {
      if (controller.signal.aborted || exportSequence.current !== sequence
        || !sameOwner(capturedOwner, ownerRef.current) || currentQuery.current !== capturedQuery) return;
      if (error instanceof ApiError && [401, 403].includes(error.status ?? -1)) authCallback.current();
      else setExportError(errorMessage(error));
    } finally {
      if (mounted.current && exportSequence.current === sequence && sameOwner(capturedOwner, ownerRef.current)) {
        exportController.current = null;
        setExportBusy(false);
      }
    }
  }

  async function deleteCode(record: AdminRedemptionCode) {
    if (unknown || (writeAttempt.current && sameOwner(writeAttempt.current.owner, ownerRef.current))) return;
    const state = codeState(record);
    if (!window.confirm("确定删除兑换码“" + record.code + "”（" + state + "）吗？删除不会撤销已经兑换的权益。")) return;
    const attempt = { owner: ownerRef.current, id: ++attemptSequence.current };
    writeAttempt.current = attempt;
    const pendingIntent: PendingMembershipExit = {
      kind: "write-in-flight", resource: "redemption-codes", operation: "delete",
      recordId: record.id, intentId: attempt.id, draft: {},
      message: "兑换码删除请求仍在处理中。离开会停止本地跟踪，但不会撤销已发送请求。",
    };
    exitCallback.current?.({ ownerScope, viewKey, administratorId: currentAdministratorId, pending: pendingIntent });
    const capturedQuery = query;
    const capturedRows = rows;
    const capturedPage = page;
    const controller = new AbortController();
    writeController.current = controller;
    setDeletingId(record.id);
    setWriteNotice(null);
    setWriteError(null);
    setCheckNotice(null);
    setUnknown(null);
    try {
      const result = await api.deleteRedemptionCode(record.id, controller.signal);
      if (!mounted.current || !sameOwner(attempt.owner, ownerRef.current)) return;
      setWriteNotice(result.message);
      exitCallback.current?.({ ownerScope, viewKey, administratorId: currentAdministratorId, pending: null });
      if (currentQuery.current === capturedQuery && capturedRows.length === 1 && capturedPage > 1) {
        setPage((value) => Math.max(1, value - 1));
      } else {
        setReload((value) => value + 1);
      }
      savedCallback.current();
    } catch (error) {
      if (!mounted.current || !sameOwner(attempt.owner, ownerRef.current)) return;
      if (error instanceof ApiError && [401, 403].includes(error.status ?? -1)) {
        authCallback.current();
      } else if (definiteRejection(error)) {
        exitCallback.current?.({ ownerScope, viewKey, administratorId: currentAdministratorId, pending: null });
        setWriteError("删除请求已被服务器明确拒绝：" + errorMessage(error));
      } else {
        const message = "删除结果未确认。记录事实与本次请求无法归因；系统不会自动重发删除。";
        setUnknown({ id: record.id, message });
        exitCallback.current?.({ ownerScope, viewKey, administratorId: currentAdministratorId, pending: {
          ...pendingIntent, kind: "unknown-write", message,
        } });
        setWriteError(errorMessage(error) + " " + message);
      }
    } finally {
      if (writeAttempt.current === attempt) writeAttempt.current = null;
      if (writeController.current === controller) writeController.current = null;
      if (mounted.current && sameOwner(attempt.owner, ownerRef.current)) setDeletingId(null);
    }
  }

  async function checkUnknownDelete() {
    if (!unknown || checking || checkController.current) return;
    const pending = unknown;
    const capturedOwner = ownerRef.current;
    const controller = new AbortController();
    checkController.current = controller;
    setChecking(true);
    setCheckNotice(null);
    try {
      const current = await api.getRedemptionCode(pending.id, controller.signal);
      if (!mounted.current || !sameOwner(capturedOwner, ownerRef.current) || unknown !== pending) return;
      setCheckNotice("只读核对：记录当前仍存在（" + current.id + "）。这不能证明原删除请求之后不会完成。");
    } catch (error) {
      if (!mounted.current || !sameOwner(capturedOwner, ownerRef.current) || unknown !== pending) return;
      if (error instanceof ApiError && error.status === 404) {
        setCheckNotice("只读核对：记录当前不存在。这不能证明原删除请求产生了该结果。");
      } else if (error instanceof ApiError && [401, 403].includes(error.status ?? -1)) {
        authCallback.current();
      } else {
        setCheckNotice("只读核对失败：" + errorMessage(error));
      }
    } finally {
      if (checkController.current === controller) checkController.current = null;
      if (mounted.current && sameOwner(capturedOwner, ownerRef.current) && unknown === pending) setChecking(false);
    }
  }

  function changeSort(field: RedemptionSortField) {
    if (field === sortField) setSortOrder((order) => order === "asc" ? "desc" : "asc");
    else {
      setSortField(field);
      setSortOrder("asc");
    }
    setPage(1);
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    if (filters.value !== "" && (!/^-?\d+$/.test(filters.value) || !Number.isSafeInteger(Number(filters.value)))) {
      setWriteError("面值筛选必须是整数。");
      return;
    }
    setWriteError(null);
    setAppliedFilters(filters);
    setPage(1);
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="admin-membership-page">
      <header className="admin-page-heading">
        <div><p className="admin-eyebrow">会员运营</p><h1>兑换码</h1><p>按服务端筛选、排序和分页管理兑换码。</p></div>
        <div className="admin-membership-actions">
          <button type="button" className="admin-button admin-button-secondary" onClick={() => { void exportCodes(); }} disabled={exportBusy}>
            {exportBusy ? "导出中…" : "导出 CSV（最多 1000 条）"}
          </button>
          <button type="button" className="admin-button admin-button-primary" onClick={onCreate}>生成兑换码</button>
        </div>
      </header>
      <form className="admin-filter-panel" aria-label="兑换码筛选" onSubmit={applyFilters}>
        <label><span>兑换码包含</span><input value={filters.code} onChange={(event) => setFilters((current) => ({ ...current, code: event.target.value }))} /></label>
        <label><span>类型</span><select value={filters.type} onChange={(event) => setFilters((current) => ({ ...current, type: event.target.value }))}>
          <option value="">全部</option><option value="membership">会员</option><option value="credits">积分</option>
        </select></label>
        <label><span>使用状态</span><select value={filters.is_used} onChange={(event) => setFilters((current) => ({ ...current, is_used: event.target.value }))}>
          <option value="">全部</option><option value="true">已使用</option><option value="false">未使用</option>
        </select></label>
        <label><span>面值</span><input inputMode="numeric" value={filters.value} onChange={(event) => setFilters((current) => ({ ...current, value: event.target.value }))} /></label>
        <button type="submit" className="admin-button admin-button-secondary">应用筛选</button>
        <button type="button" className="admin-button admin-button-secondary" onClick={() => {
          const empty = { code: "", type: "", is_used: "", value: "" };
          setFilters(empty); setAppliedFilters(empty); setPage(1); setWriteError(null);
        }}>清除</button>
      </form>
      {exportNotice && <p className="admin-membership-notice" role="status">{exportNotice}</p>}
      {exportError && <p className="admin-membership-error" role="alert">{exportError}</p>}
      {writeNotice && <p className="admin-membership-notice" role="status">{writeNotice}</p>}
      {writeError && <p className="admin-membership-error" role="alert">{writeError}</p>}
      {unknown && <section className="admin-membership-unknown" aria-label="删除结果待核对">
        <strong>删除结果待核对</strong><p>{unknown.message}</p>
        <div className="admin-membership-actions">
          <button type="button" className="admin-button admin-button-secondary" onClick={() => { void checkUnknownDelete(); }} disabled={checking}>
            {checking ? "核对中…" : "只读查询记录"}
          </button>
          <button type="button" className="admin-button admin-button-secondary" onClick={() => setReload((value) => value + 1)}>只读刷新目录</button>
        </div>
        {checkNotice && <p role="status">{checkNotice}</p>}
      </section>}
      {loadError ? <section className="admin-state-card"><strong>兑换码列表加载失败</strong><p>{loadError}</p>
        <button type="button" className="admin-button admin-button-secondary" onClick={() => setReload((value) => value + 1)}>重试读取</button></section>
        : <div className="admin-table-wrap">
          <table className="admin-model-table" aria-label="兑换码列表">
            <thead><tr>{SORT_FIELDS.map(([field, label]) => <SortHeader key={field} field={field} label={label}
              activeField={sortField} order={sortOrder} onSort={changeSort} />)}<th>操作</th></tr></thead>
            <tbody>
              {loading ? <tr><td colSpan={11}>加载中…</td></tr> : rows.length === 0
                ? <tr><td colSpan={11}>{page > 1 ? <><span>本页没有记录。</span> <button type="button" className="admin-text-button" onClick={() => setPage(1)}>回第一页并重读</button></> : "没有符合条件的兑换码"}</td></tr>
                : rows.map((record) => <tr key={record.id}>
                  <td>{record.id}</td><td>{record.code}</td><td>{record.type}</td><td>{record.value}</td>
                  <td><span className={"admin-status-pill " + (record.is_used ? "" : "is-on")}>{codeState(record)}</span></td>
                  <td>{record.used_by ?? "—"}</td><td>{formatDate(record.used_at)}</td><td>{record.created_by}</td>
                  <td>{formatDate(record.expires_at)}</td><td>{formatDate(record.created_at)}</td>
                  <td><button type="button" className="admin-text-button is-danger" disabled={deletingId === record.id || unknown !== null}
                    onClick={() => { void deleteCode(record); }}>{deletingId === record.id ? "处理中…" : "删除"}</button></td>
                </tr>)}
            </tbody>
          </table>
          <footer className="admin-table-footer"><span>共 {total} 条 · 每页 {PAGE_SIZE} 条</span>
            <div className="admin-pagination"><button type="button" className="admin-button admin-button-secondary" disabled={page <= 1 || loading}
              onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button><span>第 {page} / {pageCount} 页</span>
              <button type="button" className="admin-button admin-button-secondary" disabled={page >= pageCount || loading}
                onClick={() => setPage((value) => value + 1)}>下一页</button></div>
          </footer>
        </div>}
    </section>
  );
}

interface GenerateDraft {
  type: string;
  value: string;
  count: string;
  expires_at: string;
}

function GenerateRedemptionCodes(props: CommonProps) {
  const { api, ownerScope, viewKey, currentAdministratorId, onAuthenticationError, onCancel, onSaved, onExitStateChange } = props;
  const owner: Owner = { api, scope: ownerScope, view: viewKey, mode: "create", administratorId: currentAdministratorId };
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const savedCallback = useRef(onSaved);
  savedCallback.current = onSaved;
  const [draft, setDraft] = useState<GenerateDraft>({ type: "membership", value: "", count: "1", expires_at: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unknown, setUnknown] = useState<PendingMembershipExit | null>(null);
  const exitCallback = useRef(onExitStateChange);
  exitCallback.current = onExitStateChange;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const dirtyIntent = useRef(0);
  const writeIntent = useRef<PendingMembershipExit | null>(null);
  const [receipt, setReceipt] = useState<{ code: string; count: number } | null>(null);
  const [directory, setDirectory] = useState<AdminRedemptionCode[] | null>(null);
  const [directoryNotice, setDirectoryNotice] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const attempt = useRef<{ owner: Owner; id: number } | null>(null);
  const checkController = useRef<AbortController | null>(null);
  const writeController = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      sequence.current += 1;
      checkController.current?.abort();
      writeController.current?.abort();
    };
  }, []);

  function dirtyExit(value: GenerateDraft): PendingMembershipExit | null {
    const dirty = value.value !== "" || value.count !== "1" || value.expires_at !== "" || value.type !== "membership";
    return dirty ? {
      kind: "dirty-draft", resource: "redemption-codes", operation: "create",
      intentId: dirtyIntent.current || (dirtyIntent.current = ++sequence.current),
      draft: { ...value }, message: "兑换码生成表单尚未提交。",
    } : null;
  }

  function pendingExit(): PendingMembershipExit | undefined {
    if (unknown) return unknown;
    if (writeIntent.current) return writeIntent.current;
    if (receipt) return undefined;
    return dirtyExit(draftRef.current) ?? undefined;
  }

  function publishExit(pending: PendingMembershipExit | null): void {
    exitCallback.current?.({ ownerScope, viewKey, administratorId: currentAdministratorId, pending });
  }

  function changeDraft(next: GenerateDraft): void {
    draftRef.current = next;
    const pending = dirtyExit(next);
    if (!pending) dirtyIntent.current = 0;
    setDraft(next);
    publishExit(pending);
  }

  useEffect(() => {
    publishExit(pendingExit() ?? null);
  }, [draft, unknown, busy, receipt, ownerScope, viewKey, currentAdministratorId]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (unknown || (attempt.current && sameOwner(attempt.current.owner, ownerRef.current))) return;
    const value = Number(draft.value);
    const count = Number(draft.count);
    if (!/^\d+$/.test(draft.value) || !Number.isSafeInteger(value) || value <= 0) { setError("面值必须是正整数。"); return; }
    if (!/^\d+$/.test(draft.count) || !Number.isSafeInteger(count) || count < 1 || count > 1000) { setError("生成数量必须在 1 到 1000 之间。"); return; }
    let expiresAt: string | null = null;
    if (draft.expires_at !== "") {
      const parsedDate = new Date(draft.expires_at);
      if (Number.isNaN(parsedDate.getTime())) { setError("过期时间无效。"); return; }
      expiresAt = parsedDate.toISOString();
    }
    const input = { type: draft.type as "membership" | "credits", value, count, expires_at: expiresAt };
    const attemptToken = { owner: ownerRef.current, id: ++sequence.current };
    attempt.current = attemptToken;
    const pendingIntent: PendingMembershipExit = {
      kind: "write-in-flight", resource: "redemption-codes", operation: "create",
      intentId: attemptToken.id, draft: { ...draft },
      message: "兑换码生成请求仍在处理中。离开会停止本地跟踪，但不会撤销已发送请求。",
    };
    writeIntent.current = pendingIntent;
    publishExit(pendingIntent);
    let confirmed = false;
    let uncertain: PendingMembershipExit | null = null;
    const controller = new AbortController();
    writeController.current = controller;
    setBusy(true);
    setError(null);
    try {
      const result = await api.generateRedemptionCodes(input, currentAdministratorId, controller.signal);
      if (!mounted.current || !sameOwner(attemptToken.owner, ownerRef.current)) return;
      setReceipt({ code: result.record.code, count: result.generated_count });
      dirtyIntent.current = 0;
      confirmed = true;
      publishExit(null);
      savedCallback.current();
    } catch (cause) {
      if (!mounted.current || !sameOwner(attemptToken.owner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else if (definiteRejection(cause)) {
        setError("生成请求已被服务器明确拒绝：" + errorMessage(cause));
      } else {
        uncertain = { ...pendingIntent, kind: "unknown-write", message: "生成结果待核对；服务端仅返回首条记录和数量。保留原提交内容，系统不会自动再次生成。" };
        setUnknown(uncertain);
        publishExit(uncertain);
        setError(errorMessage(cause) + " " + uncertain.message);
      }
    } finally {
      if (attempt.current === attemptToken) attempt.current = null;
      if (writeController.current === controller) writeController.current = null;
      if (writeIntent.current === pendingIntent) writeIntent.current = null;
      if (mounted.current && sameOwner(attemptToken.owner, ownerRef.current)) {
        setBusy(false);
        if (confirmed) publishExit(null);
        else if (uncertain) publishExit(uncertain);
        else publishExit(dirtyExit(draftRef.current));
      }
    }
  }

  async function checkDirectory() {
    if (checking || checkController.current) return;
    const capturedOwner = ownerRef.current;
    const controller = new AbortController();
    checkController.current = controller;
    setChecking(true);
    setDirectoryNotice(null);
    try {
      const result = await api.listRedemptionCodes({
        skip: 0, limit: 25, sort_field: "created_at", sort_order: "desc",
        ...(draft.type ? { type: draft.type } : {}),
        ...(draft.value !== "" && Number.isSafeInteger(Number(draft.value)) ? { value: Number(draft.value) } : {}),
      }, controller.signal);
      if (!mounted.current || !sameOwner(capturedOwner, ownerRef.current) || !unknown) return;
      setDirectory(result.data);
      setDirectoryNotice("只读目录结果，仅呈现当前记录；没有批次 ID，无法据此确认本次生成。");
    } catch (cause) {
      if (!mounted.current || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else setDirectoryNotice("目录查询失败：" + errorMessage(cause));
    } finally {
      if (checkController.current === controller) checkController.current = null;
      if (mounted.current && sameOwner(capturedOwner, ownerRef.current)) setChecking(false);
    }
  }

  return (
    <section className="admin-membership-page">
      <header className="admin-page-heading"><div><p className="admin-eyebrow">会员运营</p><h1>生成兑换码</h1><p>单次最多生成 1000 个。服务仅返回本批首条回执和数量。</p></div></header>
      <form className="admin-model-form admin-membership-form" onSubmit={(event) => { void submit(event); }}>
        <div className="admin-form-grid">
          <label className="admin-field"><span>类型</span><select value={draft.type} disabled={busy || Boolean(unknown)}
            onChange={(event) => changeDraft({ ...draft, type: event.target.value })}>
            <option value="membership">会员</option><option value="credits">积分</option>
          </select></label>
          <label className="admin-field"><span>面值</span><input inputMode="numeric" value={draft.value} disabled={busy || Boolean(unknown)}
            onChange={(event) => changeDraft({ ...draft, value: event.target.value })} /></label>
          <label className="admin-field"><span>数量</span><input inputMode="numeric" value={draft.count} disabled={busy || Boolean(unknown)}
            onChange={(event) => changeDraft({ ...draft, count: event.target.value })} /></label>
          <label className="admin-field"><span>过期时间（可留空）</span><input type="datetime-local" value={draft.expires_at} disabled={busy || Boolean(unknown)}
            onChange={(event) => changeDraft({ ...draft, expires_at: event.target.value })} />
            <small>提交时会转换为带 UTC 偏移的 ISO 时间。</small></label>
        </div>
        {error && <p className="admin-membership-error" role="alert">{error}</p>}
        {receipt && <div className="admin-membership-receipt" role="status">
          <strong>服务端已确认生成 {receipt.count} 个兑换码。</strong>
          <p>回执只包含首条记录：<code>{receipt.code}</code></p>
          <p>此回执不是整批码列表，不能据此导出全部兑换码。</p>
        </div>}
        {unknown && <div className="admin-membership-unknown" aria-label="生成结果待核对">
          <strong>生成结果待核对</strong>
          <p>目录中的相似记录或数量变化不能归因到本次请求。只读查询不会重发生成请求。</p>
          <button type="button" className="admin-button admin-button-secondary" onClick={() => { void checkDirectory(); }} disabled={checking}>
            {checking ? "查询中…" : "只读查询兑换码目录"}
          </button>
          {directoryNotice && <p role="status">{directoryNotice}</p>}
          {directory && <div className="admin-table-wrap"><table className="admin-model-table" aria-label="只读兑换码目录核对">
            <thead><tr><th>ID</th><th>兑换码</th><th>类型</th><th>面值</th><th>创建时间</th></tr></thead>
            <tbody>{directory.length === 0 ? <tr><td colSpan={5}>没有记录</td></tr> : directory.map((record) =>
              <tr key={record.id}><td>{record.id}</td><td>{record.code}</td><td>{record.type}</td><td>{record.value}</td><td>{formatDate(record.created_at)}</td></tr>)}</tbody>
          </table></div>}
        </div>}
        <div className="admin-membership-actions">
          <button type="submit" className="admin-button admin-button-primary" disabled={busy || Boolean(unknown) || receipt !== null}>{busy ? "提交中…" : "生成"}</button>
          <button type="button" className="admin-button admin-button-secondary" onClick={() => onCancel(pendingExit())}>返回目录</button>
        </div>
      </form>
    </section>
  );
}

export function RedemptionCodes(props: RedemptionCodesProps) {
  const common: CommonProps = {
    api: props.api,
    ownerScope: props.ownerScope,
    viewKey: props.viewKey,
    currentAdministratorId: props.currentAdministratorId,
    listVersion: props.listVersion ?? 0,
    onAuthenticationError: props.onAuthenticationError,
    onCancel: props.onCancel,
    onSaved: props.onSaved,
    ...(props.onExitStateChange ? { onExitStateChange: props.onExitStateChange } : {}),
  };
  const componentKey = JSON.stringify([apiOwnerId(props.api), props.ownerScope, props.viewKey, props.currentAdministratorId, props.mode]);
  return props.mode === "list"
    ? <RedemptionCodeList key={componentKey} {...common} onCreate={props.onCreate} />
    : <GenerateRedemptionCodes key={componentKey} {...common} />;
}
