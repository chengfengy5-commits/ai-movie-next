import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import { downloadMembershipCsv, serializePurchasePackagesCsv } from "./redemptionExport";
import type {
  AdminMembershipApi,
  AdminMembershipExitSnapshot,
  AdminPurchasePackage,
  PackageQuery,
  PackageSortField,
  PendingMembershipExit,
  PurchasePackageInput,
  PurchasePackageUpdate,
} from "./adminMembershipTypes";
import "./adminMembership.css";

const PAGE_SIZE = 10;
const API_OWNER_IDS = new WeakMap<AdminMembershipApi, number>();
let nextApiOwnerId = 0;

function apiOwnerId(api: AdminMembershipApi): number {
  const existing = API_OWNER_IDS.get(api);
  if (existing !== undefined) return existing;
  const created = ++nextApiOwnerId;
  API_OWNER_IDS.set(api, created);
  return created;
}
const SORT_FIELDS: Array<[PackageSortField, string]> = [
  ["id", "ID"], ["category", "类别"], ["name", "名称"], ["price", "价格（元）"],
  ["credits", "积分"], ["days", "会员天数"], ["sort_order", "排序"],
  ["is_active", "启用"], ["created_at", "创建时间"], ["updated_at", "更新时间"],
];

export type PurchasePackagesMode = "list" | "create" | "edit";

export interface PurchasePackagesProps {
  api: AdminMembershipApi;
  mode: PurchasePackagesMode;
  recordId?: string;
  ownerScope: string;
  viewKey: string;
  currentAdministratorId: string;
  listVersion?: number;
  onAuthenticationError(): void;
  onCreate(): void;
  onEdit(id: string): void;
  onCancel(pending?: PendingMembershipExit): void;
  onSaved(): void;
  onExitStateChange?(snapshot: AdminMembershipExitSnapshot): void;
}

interface Owner {
  api: AdminMembershipApi;
  scope: string;
  view: string;
  mode: PurchasePackagesMode;
  id: string;
  administratorId: string;
}

function sameOwner(left: Owner, right: Owner): boolean {
  return left.api === right.api && left.scope === right.scope && left.view === right.view
    && left.mode === right.mode && left.id === right.id && left.administratorId === right.administratorId;
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

function formatDate(value: string): string {
  if (value === "") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
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
  field: PackageSortField;
  label: string;
  activeField: PackageSortField;
  order: "asc" | "desc";
  onSort(field: PackageSortField): void;
}) {
  const active = field === activeField;
  return <th scope="col" aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}>
    <button type="button" className="admin-users-sort-button" onClick={() => onSort(field)}
      aria-label={"按" + label + "排序"}>{label}<span aria-hidden="true">{active ? (order === "asc" ? " ↑" : " ↓") : ""}</span></button>
  </th>;
}

function PurchasePackageList(props: CommonProps & { onCreate(): void; onEdit(id: string): void }) {
  const { api, ownerScope, viewKey, currentAdministratorId, listVersion, onAuthenticationError, onCreate, onEdit, onSaved, onExitStateChange } = props;
  const owner: Owner = { api, scope: ownerScope, view: viewKey, mode: "list", id: "", administratorId: currentAdministratorId };
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const savedCallback = useRef(onSaved);
  savedCallback.current = onSaved;
  const exitCallback = useRef(onExitStateChange);
  exitCallback.current = onExitStateChange;

  const [page, setPage] = useState(1);
  const [sortField, setSortField] = useState<PackageSortField>("sort_order");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");
  const [rows, setRows] = useState<AdminPurchasePackage[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
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
  const checkAttemptSequence = useRef(0);

  const query = useMemo<PackageQuery>(() => ({
    skip: (page - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
    sort_field: sortField,
    sort_order: sortOrder,
  }), [page, sortField, sortOrder]);
  const currentQuery = useRef(query);
  currentQuery.current = query;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestSequence.current += 1;
      exportSequence.current += 1;
      checkAttemptSequence.current += 1;
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
    void api.listPurchasePackages(query, controller.signal).then((result) => {
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

  async function exportPackages() {
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
      const result = await api.listPurchasePackages({ ...capturedQuery, skip: 0, limit: 1000 }, controller.signal);
      if (controller.signal.aborted || exportSequence.current !== sequence
        || !sameOwner(capturedOwner, ownerRef.current) || currentQuery.current !== capturedQuery) return;
      const records = result.data.slice(0, 1000);
      downloadMembershipCsv(serializePurchasePackagesCsv(records),
        "haoai-purchase-packages-" + new Date().toISOString().slice(0, 10) + ".csv");
      setExportNotice(result.total > records.length
        ? "已导出前 " + records.length + " 条，共 " + result.total + " 条套餐。"
        : "已导出 " + records.length + " 条套餐记录。");
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

  async function deletePackage(record: AdminPurchasePackage) {
    if (unknown || (writeAttempt.current && sameOwner(writeAttempt.current.owner, ownerRef.current))) return;
    if (!window.confirm("确定删除套餐“" + record.name + "”（" + record.id + "）吗？这不会撤销已经购买的权益。")) return;
    const attempt = { owner: ownerRef.current, id: ++attemptSequence.current };
    writeAttempt.current = attempt;
    const pendingIntent: PendingMembershipExit = {
      kind: "write-in-flight", resource: "purchase-packages", operation: "delete",
      recordId: record.id, intentId: attempt.id, draft: {},
      message: "套餐删除请求仍在处理中。离开会停止本地跟踪，但不会撤销已发送请求。",
    };
    exitCallback.current?.({ ownerScope, viewKey, administratorId: currentAdministratorId, pending: pendingIntent });
    const capturedQuery = query;
    const capturedRows = rows;
    const capturedPage = page;
    const controller = new AbortController();
    writeController.current = controller;
    setDeletingId(record.id);
    setNotice(null);
    setWriteError(null);
    setCheckNotice(null);
    setUnknown(null);
    try {
      const result = await api.deletePurchasePackage(record.id, controller.signal);
      if (!sameOwner(attempt.owner, ownerRef.current) || !mounted.current) return;
      setNotice(result.message);
      exitCallback.current?.({ ownerScope, viewKey, administratorId: currentAdministratorId, pending: null });
      if (currentQuery.current === capturedQuery && capturedRows.length === 1 && capturedPage > 1) {
        setPage((value) => Math.max(1, value - 1));
      } else {
        setReload((value) => value + 1);
      }
      savedCallback.current();
    } catch (error) {
      if (!sameOwner(attempt.owner, ownerRef.current) || !mounted.current) return;
      if (error instanceof ApiError && [401, 403].includes(error.status ?? -1)) authCallback.current();
      else if (definiteRejection(error)) {
        exitCallback.current?.({ ownerScope, viewKey, administratorId: currentAdministratorId, pending: null });
        setWriteError("删除请求已被服务器明确拒绝：" + errorMessage(error));
      }
      else {
        const message = "删除结果未确认。只读查询显示当前事实，无法归因请求；系统不会自动重发。";
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
    const checkAttempt = ++checkAttemptSequence.current;
    const controller = new AbortController();
    checkController.current = controller;
    setChecking(true);
    setCheckNotice(null);
    try {
      const current = await api.getPurchasePackage(pending.id, controller.signal);
      if (!mounted.current || controller.signal.aborted || checkAttemptSequence.current !== checkAttempt
        || !sameOwner(capturedOwner, ownerRef.current) || unknown !== pending) return;
      setCheckNotice("只读核对：套餐当前" + (current.is_active ? "已启用" : "已停用") + "，名称为“" + current.name + "”。当前事实不能证明原删除请求之后不会完成。");
    } catch (error) {
      if (!mounted.current || controller.signal.aborted || checkAttemptSequence.current !== checkAttempt
        || !sameOwner(capturedOwner, ownerRef.current) || unknown !== pending) return;
      if (error instanceof ApiError && error.status === 404) setCheckNotice("只读核对：套餐当前不存在；这不能证明原删除请求产生了该结果。");
      else if (error instanceof ApiError && [401, 403].includes(error.status ?? -1)) authCallback.current();
      else setCheckNotice("只读核对失败：" + errorMessage(error));
    } finally {
      if (checkAttemptSequence.current === checkAttempt && checkController.current === controller) {
        checkController.current = null;
      }
      if (mounted.current && !controller.signal.aborted && checkAttemptSequence.current === checkAttempt
        && sameOwner(capturedOwner, ownerRef.current) && unknown === pending) setChecking(false);
    }
  }

  function changeSort(field: PackageSortField) {
    if (field === sortField) setSortOrder((order) => order === "asc" ? "desc" : "asc");
    else { setSortField(field); setSortOrder("asc"); }
    setPage(1);
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return <section className="admin-membership-page">
    <header className="admin-page-heading">
      <div><p className="admin-eyebrow">会员运营</p><h1>购买套餐</h1><p>展示全部状态的套餐；套餐目录暂不支持按类别或启用状态筛选。</p></div>
      <div className="admin-membership-actions">
        <button type="button" className="admin-button admin-button-secondary" onClick={() => { void exportPackages(); }} disabled={exportBusy}>
          {exportBusy ? "导出中…" : "导出 CSV（最多 1000 条）"}
        </button>
        <button type="button" className="admin-button admin-button-primary" onClick={onCreate}>新建套餐</button>
      </div>
    </header>
    {exportNotice && <p className="admin-membership-notice" role="status">{exportNotice}</p>}
    {exportError && <p className="admin-membership-error" role="alert">{exportError}</p>}
    {notice && <p className="admin-membership-notice" role="status">{notice}</p>}
    {writeError && <p className="admin-membership-error" role="alert">{writeError}</p>}
    {unknown && <section className="admin-membership-unknown" aria-label="删除结果待核对">
      <strong>删除结果待核对</strong><p>{unknown.message}</p>
      <div className="admin-membership-actions">
        <button type="button" className="admin-button admin-button-secondary" onClick={() => { void checkUnknownDelete(); }} disabled={checking}>
          {checking ? "核对中…" : "只读查询套餐"}
        </button>
        <button type="button" className="admin-button admin-button-secondary" onClick={() => setReload((value) => value + 1)}>只读刷新目录</button>
      </div>
      {checkNotice && <p role="status">{checkNotice}</p>}
    </section>}
    {loadError ? <section className="admin-state-card"><strong>套餐列表加载失败</strong><p>{loadError}</p>
      <button type="button" className="admin-button admin-button-secondary" onClick={() => setReload((value) => value + 1)}>重试读取</button></section>
      : <div className="admin-table-wrap"><table className="admin-model-table" aria-label="购买套餐列表">
        <thead><tr>{SORT_FIELDS.map(([field, label]) => <SortHeader key={field} field={field} label={label} activeField={sortField} order={sortOrder} onSort={changeSort} />)}<th>操作</th></tr></thead>
        <tbody>{loading ? <tr><td colSpan={11}>加载中…</td></tr> : rows.length === 0
          ? <tr><td colSpan={11}>没有套餐记录</td></tr>
          : rows.map((record) => <tr key={record.id}>
            <td>{record.id}</td><td>{record.category}</td><td>{record.name}</td><td>{record.price}</td>
            <td>{record.credits === null ? "—" : record.credits}</td><td>{record.days === null ? "—" : record.days}</td>
            <td>{record.sort_order}</td><td><span className={"admin-status-pill " + (record.is_active ? "is-on" : "")}>{record.is_active ? "启用" : "停用"}</span></td>
            <td>{formatDate(record.created_at)}</td><td>{formatDate(record.updated_at)}</td>
            <td><div className="admin-table-actions"><button type="button" className="admin-text-button" onClick={() => onEdit(record.id)}>编辑</button>
              <button type="button" className="admin-text-button is-danger" disabled={deletingId === record.id || unknown !== null}
                onClick={() => { void deletePackage(record); }}>{deletingId === record.id ? "处理中…" : "删除"}</button></div></td>
          </tr>)}</tbody>
      </table><footer className="admin-table-footer"><span>共 {total} 条 · 每页 {PAGE_SIZE} 条</span><div className="admin-pagination">
        <button type="button" className="admin-button admin-button-secondary" disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
        <span>第 {page} / {pageCount} 页</span>
        <button type="button" className="admin-button admin-button-secondary" disabled={page >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>下一页</button>
      </div></footer></div>}
  </section>;
}

interface PackageDraft {
  category: string;
  name: string;
  price: string;
  credits: string;
  days: string;
  sort_order: string;
  is_active: boolean;
}

function emptyDraft(): PackageDraft {
  return { category: "credits", name: "", price: "0", credits: "", days: "", sort_order: "0", is_active: true };
}

function draftFromRecord(record: AdminPurchasePackage): PackageDraft {
  return {
    category: record.category,
    name: record.name,
    price: String(record.price),
    credits: record.credits === null ? "" : String(record.credits),
    days: record.days === null ? "" : String(record.days),
    sort_order: String(record.sort_order),
    is_active: record.is_active,
  };
}

function parseIntegerDraft(value: string): number | null {
  if (!/^-?\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function updateFromDraft(baseline: AdminPurchasePackage, draft: PackageDraft): { patch: PurchasePackageUpdate; error: string | null } {
  const patch: PurchasePackageUpdate = {};
  if (draft.category !== baseline.category) patch.category = draft.category;
  if (draft.name !== baseline.name) patch.name = draft.name;
  if (draft.price !== String(baseline.price)) {
    const value = parseIntegerDraft(draft.price);
    if (value === null) return { patch: {}, error: "价格必须填写整数元，且不能超出安全整数范围。" };
    patch.price = value;
  }
  if (draft.sort_order !== String(baseline.sort_order)) {
    const value = parseIntegerDraft(draft.sort_order);
    if (value === null) return { patch: {}, error: "排序必须填写整数，且不能超出安全整数范围。" };
    patch.sort_order = value;
  }
  if (draft.credits !== (baseline.credits === null ? "" : String(baseline.credits))) {
    if (draft.credits === "") patch.credits = null;
    else {
      const value = parseIntegerDraft(draft.credits);
      if (value === null) return { patch: {}, error: "积分必须填写整数或留空。" };
      patch.credits = value;
    }
  }
  if (draft.days !== (baseline.days === null ? "" : String(baseline.days))) {
    if (draft.days === "") patch.days = null;
    else {
      const value = parseIntegerDraft(draft.days);
      if (value === null) return { patch: {}, error: "会员天数必须填写整数或留空。" };
      patch.days = value;
    }
  }
  if (draft.is_active !== baseline.is_active) patch.is_active = draft.is_active;
  return { patch, error: null };
}

function createInput(draft: PackageDraft): { input: PurchasePackageInput | null; error: string | null } {
  const price = parseIntegerDraft(draft.price);
  const sortOrder = parseIntegerDraft(draft.sort_order);
  if (price === null) return { input: null, error: "价格必须填写整数元，且不能超出安全整数范围。" };
  if (sortOrder === null) return { input: null, error: "排序必须填写整数，且不能超出安全整数范围。" };
  let credits: number | null = null;
  let days: number | null = null;
  if (draft.credits !== "") {
    credits = parseIntegerDraft(draft.credits);
    if (credits === null) return { input: null, error: "积分必须填写整数或留空。" };
  }
  if (draft.days !== "") {
    days = parseIntegerDraft(draft.days);
    if (days === null) return { input: null, error: "会员天数必须填写整数或留空。" };
  }
  return {
    input: {
      category: draft.category,
      name: draft.name,
      price,
      credits,
      days,
      sort_order: sortOrder,
      is_active: draft.is_active,
    },
    error: null,
  };
}

function PurchasePackageForm(props: CommonProps & { mode: "create" | "edit"; recordId?: string }) {
  const { api, ownerScope, viewKey, currentAdministratorId, onAuthenticationError, onCancel, onSaved, onExitStateChange, mode, recordId } = props;
  const owner: Owner = { api, scope: ownerScope, view: viewKey, mode, id: recordId ?? "", administratorId: currentAdministratorId };
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const savedCallback = useRef(onSaved);
  savedCallback.current = onSaved;
  const exitCallback = useRef(onExitStateChange);
  exitCallback.current = onExitStateChange;

  const [draft, setDraft] = useState<PackageDraft>(emptyDraft);
  const [baseline, setBaseline] = useState<AdminPurchasePackage | null>(null);
  const [loadedId, setLoadedId] = useState<string | null>(mode === "create" ? "" : null);
  const [loading, setLoading] = useState(mode === "edit");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [unknown, setUnknown] = useState<{ operation: string; id?: string; intentId: number; message: string; draft: PackageDraft } | null>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const writeIntent = useRef<PendingMembershipExit | null>(null);
  const dirtyIntent = useRef(0);
  const [receipt, setReceipt] = useState<AdminPurchasePackage | null>(null);
  const [deleteConfirmed, setDeleteConfirmed] = useState(false);
  const [checkNotice, setCheckNotice] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [directory, setDirectory] = useState<AdminPurchasePackage[] | null>(null);
  const [reload, setReload] = useState(0);
  const requestSequence = useRef(0);
  const attemptSequence = useRef(0);
  const attempt = useRef<{ owner: Owner; id: number } | null>(null);
  const mounted = useRef(false);
  const unknownRef = useRef(unknown);
  const checkController = useRef<AbortController | null>(null);
  const writeController = useRef<AbortController | null>(null);
  unknownRef.current = unknown;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestSequence.current += 1;
      checkController.current?.abort();
      writeController.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (mode === "create") {
      setBaseline(null);
      setDraft(emptyDraft());
      setLoadedId("");
      setLoading(false);
      setLoadError(null);
      return;
    }
    const controller = new AbortController();
    const sequence = ++requestSequence.current;
    const capturedOwner = ownerRef.current;
    setBaseline(null);
    setDraft(emptyDraft());
    setLoadedId(null);
    setLoading(true);
    setLoadError(null);
    void api.getPurchasePackage(recordId ?? "", controller.signal).then((record) => {
      if (!mounted.current || controller.signal.aborted || sequence !== requestSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      setBaseline(record);
      setDraft(draftFromRecord(record));
      setLoadedId(record.id);
      setLoading(false);
    }).catch((cause: unknown) => {
      if (!mounted.current || controller.signal.aborted || sequence !== requestSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else setLoadError(errorMessage(cause));
      setLoading(false);
    });
    return () => { controller.abort(); };
  }, [api, ownerScope, viewKey, currentAdministratorId, mode, recordId, reload]);

  const changes = useMemo(() => baseline ? updateFromDraft(baseline, draft) : { patch: {}, error: null },
    [baseline, draft]);
  function dirtyExit(value: PackageDraft, baselineValue = baseline): PendingMembershipExit | null {
    const isDirty = mode === "create"
      ? value.category !== "credits" || value.name !== "" || value.price !== "0" || value.credits !== ""
        || value.days !== "" || value.sort_order !== "0" || !value.is_active
      : baselineValue !== null && (() => {
        const current = updateFromDraft(baselineValue, value);
        return Object.keys(current.patch).length > 0 || current.error !== null;
      })();
    if (!isDirty) return null;
    if (!dirtyIntent.current) dirtyIntent.current = ++attemptSequence.current;
    return {
      kind: "dirty-draft", resource: "purchase-packages", operation: mode === "create" ? "create" : "update",
      ...(recordId ? { recordId } : {}), intentId: dirtyIntent.current,
      draft: { ...value }, message: "套餐表单尚未提交。",
    };
  }

  function pendingExit(): PendingMembershipExit | undefined {
    if (unknown) return {
      kind: "unknown-write", resource: "purchase-packages", operation: unknown.operation as "create" | "update" | "delete",
      ...(unknown.id ? { recordId: unknown.id } : {}), intentId: unknown.intentId,
      draft: { ...unknown.draft }, message: unknown.message,
    };
    if (writeIntent.current) return writeIntent.current;
    if (receipt || deleteConfirmed) return undefined;
    return dirtyExit(draftRef.current) ?? undefined;
  }

  function publishExit(pending: PendingMembershipExit | null): void {
    exitCallback.current?.({ ownerScope, viewKey, administratorId: currentAdministratorId, pending });
  }

  function changeDraft(next: PackageDraft): void {
    draftRef.current = next;
    const pending = dirtyExit(next);
    if (!pending) dirtyIntent.current = 0;
    setDraft(next);
    publishExit(pending);
  }

  useEffect(() => {
    publishExit(pendingExit() ?? null);
  }, [draft, baseline, unknown, busy, receipt, deleteConfirmed, ownerScope, viewKey, currentAdministratorId]);

  async function checkUnknown() {
    if (!unknown || checking || checkController.current) return;
    const pending = unknown;
    const capturedOwner = ownerRef.current;
    const controller = new AbortController();
    checkController.current = controller;
    setChecking(true);
    setCheckNotice(null);
    setDirectory(null);
    try {
      if (pending.id) {
        const current = await api.getPurchasePackage(pending.id, controller.signal);
        if (!mounted.current || !sameOwner(capturedOwner, ownerRef.current) || unknownRef.current !== pending) return;
        setCheckNotice("只读快照：名称“" + current.name + "”，类别“" + current.category + "”，价格 " + current.price
          + " 元，状态" + (current.is_active ? "启用" : "停用") + "。这只能说明当前值，不能归因原写请求。");
      } else {
        const page = await api.listPurchasePackages({ skip: 0, limit: 10, sort_field: "sort_order", sort_order: "asc" }, controller.signal);
        if (!mounted.current || !sameOwner(capturedOwner, ownerRef.current) || unknownRef.current !== pending) return;
        setDirectory(page.data);
        setCheckNotice("只读目录结果仅呈现当前套餐。没有写入回执 ID，无法据此确认本次创建。");
      }
    } catch (cause) {
      if (!mounted.current || !sameOwner(capturedOwner, ownerRef.current) || unknownRef.current !== pending) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else if (cause instanceof ApiError && cause.status === 404) setCheckNotice("只读快照：该 ID 当前不存在；这不能证明本次请求产生了该结果。");
      else setCheckNotice("只读核对失败：" + errorMessage(cause));
    } finally {
      if (checkController.current === controller) checkController.current = null;
      if (mounted.current && sameOwner(capturedOwner, ownerRef.current) && unknownRef.current === pending) setChecking(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || unknown || receipt || (attempt.current && sameOwner(attempt.current.owner, ownerRef.current))) return;
    let input: PurchasePackageInput | null = null;
    let patch: PurchasePackageUpdate | null = null;
    if (mode === "create") {
      const parsed = createInput(draft);
      if (parsed.error || !parsed.input) { setError(parsed.error ?? "表单数据无效。"); return; }
      input = parsed.input;
    } else {
      if (!baseline || loadedId !== recordId) return;
      const parsed = updateFromDraft(baseline, draft);
      if (parsed.error) { setError(parsed.error); return; }
      if (Object.keys(parsed.patch).length === 0) { setNotice("没有字段变化，无需发送更新。"); return; }
      patch = parsed.patch;
    }

    const attemptToken = { owner: ownerRef.current, id: ++attemptSequence.current };
    attempt.current = attemptToken;
    const pendingIntent: PendingMembershipExit = {
      kind: "write-in-flight", resource: "purchase-packages", operation: mode === "create" ? "create" : "update",
      ...(recordId ? { recordId } : {}), intentId: attemptToken.id, draft: { ...draft },
      message: "套餐写入请求仍在处理中。离开会停止本地跟踪，但不会撤销已发送请求。",
    };
    writeIntent.current = pendingIntent;
    publishExit(pendingIntent);
    let confirmed = false;
    let uncertain: PendingMembershipExit | null = null;
    const controller = new AbortController();
    writeController.current = controller;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = mode === "create"
        ? await api.createPurchasePackage(input as PurchasePackageInput, controller.signal)
        : await api.updatePurchasePackage(recordId ?? "", patch as PurchasePackageUpdate, controller.signal);
      if (!mounted.current || !sameOwner(attemptToken.owner, ownerRef.current)) return;
      setBaseline(saved);
      setDraft(draftFromRecord(saved));
      setReceipt(saved);
      setNotice(mode === "create" ? "套餐已由服务端确认创建。" : "套餐更新已由服务端确认。");
      confirmed = true;
      publishExit(null);
      savedCallback.current();
    } catch (cause) {
      if (!mounted.current || !sameOwner(attemptToken.owner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else if (definiteRejection(cause)) {
        setError("保存请求已被服务器明确拒绝：" + errorMessage(cause));
      }
      else {
        const message = "保存结果待核对。保留原草稿并冻结写入；系统不会自动重发。";
        uncertain = { ...pendingIntent, kind: "unknown-write", message };
        setUnknown({ operation: mode === "create" ? "create" : "update", ...(recordId ? { id: recordId } : {}), intentId: attemptToken.id, message, draft });
        publishExit(uncertain);
        setError(errorMessage(cause) + " " + message);
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

  async function deleteCurrentPackage() {
    if (!baseline || busy || unknown || (attempt.current && sameOwner(attempt.current.owner, ownerRef.current))) return;
    if (!window.confirm("确定删除套餐“" + baseline.name + "”（" + baseline.id + "）吗？这不会撤销已经购买的权益。")) return;
    const attemptToken = { owner: ownerRef.current, id: ++attemptSequence.current };
    attempt.current = attemptToken;
    const pendingIntent: PendingMembershipExit = {
      kind: "write-in-flight", resource: "purchase-packages", operation: "delete",
      recordId: baseline.id, intentId: attemptToken.id, draft: { ...draft },
      message: "套餐删除请求仍在处理中。离开会停止本地跟踪，但不会撤销已发送请求。",
    };
    writeIntent.current = pendingIntent;
    publishExit(pendingIntent);
    let confirmed = false;
    let uncertain: PendingMembershipExit | null = null;
    const controller = new AbortController();
    writeController.current = controller;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.deletePurchasePackage(baseline.id, controller.signal);
      if (!mounted.current || !sameOwner(attemptToken.owner, ownerRef.current)) return;
      setNotice(result.message);
      setDeleteConfirmed(true);
      confirmed = true;
      publishExit(null);
      savedCallback.current();
    } catch (cause) {
      if (!mounted.current || !sameOwner(attemptToken.owner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else if (definiteRejection(cause)) {
        setError("删除请求已被服务器明确拒绝：" + errorMessage(cause));
      }
      else {
        const message = "删除结果待核对；系统不会自动重发。";
        uncertain = { ...pendingIntent, kind: "unknown-write", message };
        setUnknown({ operation: "delete", id: baseline.id, intentId: attemptToken.id, message, draft });
        publishExit(uncertain);
        setError(errorMessage(cause) + " " + message);
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

  const ready = mode === "create" || loadedId === recordId;

  return <section className="admin-membership-page">
    <header className="admin-page-heading"><div><p className="admin-eyebrow">会员运营</p>
      <h1>{mode === "create" ? "新建购买套餐" : "编辑购买套餐"}</h1>
      <p>价格按整数元保存；积分、会员天数可为空，0 和负数会原样保留。</p></div></header>
    {loading && <div className="admin-state-card"><strong>正在读取套餐</strong><p>读取完整记录后再编辑。</p></div>}
    {loadError && <div className="admin-state-card"><strong>套餐读取失败</strong><p>{loadError}</p>
      <button type="button" className="admin-button admin-button-secondary" onClick={() => { setLoadError(null); setReload((value) => value + 1); }}>重试读取</button></div>}
    {ready && !loading && !loadError && <form className="admin-model-form admin-membership-form" onSubmit={(event) => { void submit(event); }}>
      <div className="admin-form-grid">
        <label className="admin-field"><span>类别</span><select value={draft.category} disabled={busy || Boolean(unknown) || Boolean(receipt) || deleteConfirmed}
          onChange={(event) => changeDraft({ ...draft, category: event.target.value })}>
          {draft.category !== "credits" && draft.category !== "membership" && <option value={draft.category}>{draft.category}（旧类别，保留）</option>}
          <option value="credits">积分</option><option value="membership">会员</option>
        </select><small>切换类别不会清除积分或会员天数。</small></label>
        <label className="admin-field"><span>名称</span><input value={draft.name} disabled={busy || Boolean(unknown) || Boolean(receipt) || deleteConfirmed}
          onChange={(event) => changeDraft({ ...draft, name: event.target.value })} /></label>
        <label className="admin-field"><span>价格（整数元）</span><input inputMode="numeric" value={draft.price} disabled={busy || Boolean(unknown) || Boolean(receipt) || deleteConfirmed}
          onChange={(event) => changeDraft({ ...draft, price: event.target.value })} /></label>
        <label className="admin-field"><span>积分（空值表示 null）</span><input inputMode="numeric" value={draft.credits} disabled={busy || Boolean(unknown) || Boolean(receipt) || deleteConfirmed}
          onChange={(event) => changeDraft({ ...draft, credits: event.target.value })} /></label>
        <label className="admin-field"><span>会员天数（空值表示 null）</span><input inputMode="numeric" value={draft.days} disabled={busy || Boolean(unknown) || Boolean(receipt) || deleteConfirmed}
          onChange={(event) => changeDraft({ ...draft, days: event.target.value })} /></label>
        <label className="admin-field"><span>排序</span><input inputMode="numeric" value={draft.sort_order} disabled={busy || Boolean(unknown) || Boolean(receipt) || deleteConfirmed}
          onChange={(event) => changeDraft({ ...draft, sort_order: event.target.value })} /></label>
        <label className="admin-field admin-membership-checkbox"><span>启用</span><input type="checkbox" checked={draft.is_active} disabled={busy || Boolean(unknown) || Boolean(receipt) || deleteConfirmed}
          onChange={(event) => changeDraft({ ...draft, is_active: event.target.checked })} /></label>
      </div>
      {error && <p className="admin-membership-error" role="alert">{error}</p>}
      {notice && <p className="admin-membership-notice" role="status">{notice}</p>}
      {mode === "edit" && baseline && <p className="admin-field-help">ID：{baseline.id} · 原始时间：{baseline.created_at} / {baseline.updated_at}</p>}
      {unknown && <section className="admin-membership-unknown" aria-label="套餐写入结果待核对">
        <strong>套餐写入结果待核对</strong><p>{unknown.message}当前快照只能展示事实，不能归因该请求。</p>
        <button type="button" className="admin-button admin-button-secondary" onClick={() => { void checkUnknown(); }} disabled={checking}>
          {checking ? "查询中…" : unknown.id ? "只读读取该套餐" : "只读查询套餐目录"}
        </button>
        {checkNotice && <p role="status">{checkNotice}</p>}
        {directory && <div className="admin-table-wrap"><table className="admin-model-table" aria-label="只读套餐目录核对">
          <thead><tr><th>ID</th><th>类别</th><th>名称</th><th>价格</th><th>状态</th></tr></thead>
          <tbody>{directory.length === 0 ? <tr><td colSpan={5}>没有记录</td></tr> : directory.map((record) =>
            <tr key={record.id}><td>{record.id}</td><td>{record.category}</td><td>{record.name}</td><td>{record.price}</td><td>{record.is_active ? "启用" : "停用"}</td></tr>)}</tbody>
        </table></div>}
      </section>}
      {deleteConfirmed && <p className="admin-membership-receipt" role="status">服务端已确认删除套餐。返回目录前保留此确认。</p>}
      {receipt && <section className="admin-membership-receipt" role="status"><strong>服务端已确认套餐记录</strong>
        <p>{receipt.name} · {receipt.category} · {receipt.price} 元 · {receipt.is_active ? "启用" : "停用"}</p>
        <p>积分：{receipt.credits === null ? "null" : receipt.credits}；会员天数：{receipt.days === null ? "null" : receipt.days}</p>
      </section>}
      <div className="admin-membership-actions">
        <button type="submit" className="admin-button admin-button-primary" disabled={busy || Boolean(unknown) || Boolean(receipt) || deleteConfirmed || loading
          || (mode === "edit" && (!baseline || Object.keys(changes.patch).length === 0 && !changes.error))}>
          {busy ? "提交中…" : mode === "create" ? "创建套餐" : "保存更改"}
        </button>
        {mode === "edit" && baseline && <button type="button" className="admin-button admin-button-secondary" onClick={() => { void deleteCurrentPackage(); }} disabled={busy || Boolean(unknown) || Boolean(receipt) || deleteConfirmed}>删除套餐</button>}
        <button type="button" className="admin-button admin-button-secondary" onClick={() => onCancel(pendingExit())}>返回目录</button>
      </div>
    </form>}
  </section>;
}

export function PurchasePackages(props: PurchasePackagesProps) {
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
  const componentKey = JSON.stringify([apiOwnerId(props.api), props.ownerScope, props.viewKey, props.currentAdministratorId, props.mode, props.recordId ?? ""]);
  return props.mode === "list"
    ? <PurchasePackageList key={componentKey} {...common} onCreate={props.onCreate} onEdit={props.onEdit} />
    : <PurchasePackageForm key={componentKey} {...common} mode={props.mode} {...(props.recordId ? { recordId: props.recordId } : {})} />;
}
