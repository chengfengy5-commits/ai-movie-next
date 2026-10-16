import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import type {
  AdminSeriesApi,
  AdminSeriesExitSnapshot,
  AdminSeriesListState,
  AdminSeriesMode,
  AdminSeriesPage,
  AdminSeriesRecord,
  AdminSeriesUpdatePatch,
  PendingAdminSeriesExit,
} from "./adminSeriesTypes";
import { downloadAdminSeriesCsv, serializeAdminSeriesCsv } from "./adminSeriesExport";
import "./adminSeries.css";

const PAGE_SIZE = 10;

interface Owner {
  api: AdminSeriesApi;
  scope: string;
  view: string;
  administratorId: string;
  mode: AdminSeriesMode;
  recordId: string;
}

function sameOwner(a: Owner, b: Owner): boolean {
  return a.api === b.api && a.scope === b.scope && a.view === b.view
    && a.administratorId === b.administratorId && a.mode === b.mode && a.recordId === b.recordId;
}

function readError(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return "这条剧集记录已不存在。";
  if (error instanceof ApiError && error.kind === "network") return "无法连接管理后台服务，请检查服务后重试。";
  if (error instanceof ApiError && error.kind === "timeout") return "读取剧集信息超时，请稍后重试。";
  return "读取剧集信息失败，请重试。";
}

function isDefinedRejection(error: unknown): error is ApiError {
  return error instanceof ApiError && [401, 403, 404, 422].includes(error.status ?? -1);
}

function createPending(
  owner: Owner,
  kind: PendingAdminSeriesExit["kind"],
  operation: PendingAdminSeriesExit["operation"],
  intentId: number,
  message: string,
  draft: Readonly<{ name: string; description: string; imageUrl: string }>,
): PendingAdminSeriesExit {
  return {
    kind,
    operation,
    mode: owner.mode === "list" ? "create" : owner.mode,
    recordId: owner.mode === "edit" ? owner.recordId : null,
    intentId,
    draft,
    message,
  };
}

export interface AdminSeriesProps {
  api: AdminSeriesApi;
  mode: AdminSeriesMode;
  recordId?: string;
  ownerScope: string;
  viewKey: string;
  administratorId: string;
  listVersion: number;
  listState: AdminSeriesListState;
  notice?: string | null;
  onListStateChange(state: AdminSeriesListState): void;
  onAuthenticationError(): void;
  onCreate(): void;
  onEdit(id: string): void;
  onCancel(pending?: PendingAdminSeriesExit): void;
  onSaved(record: AdminSeriesRecord, operation: "create" | "update"): void;
  onExitStateChange?(snapshot: AdminSeriesExitSnapshot): void;
}

export function AdminSeries(props: AdminSeriesProps) {
  if (props.mode === "list") return <SeriesList {...props} />;
  return <SeriesForm {...props} />;
}

function SeriesList({
  api, ownerScope, viewKey, administratorId, listVersion, listState, onListStateChange,
  onAuthenticationError, onCreate, onEdit, onExitStateChange, notice,
}: AdminSeriesProps) {
  const owner: Owner = { api, scope: ownerScope, view: viewKey, administratorId, mode: "list", recordId: "" };
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const editCallback = useRef(onEdit);
  editCallback.current = onEdit;
  const exitCallback = useRef(onExitStateChange);
  exitCallback.current = onExitStateChange;
  const [records, setRecords] = useState<AdminSeriesRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [draftFilters, setDraftFilters] = useState({ name: listState.name, userId: listState.userId });
  const [exportStatus, setExportStatus] = useState(" ");
  const [exporting, setExporting] = useState(false);
  const [retry, setRetry] = useState(0);
  const listSequence = useRef(0);
  const exportSequence = useRef(0);
  const exportController = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const appliedState = useRef(listState);
  appliedState.current = listState;

  const query = useMemo(() => ({
    skip: (listState.page - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
    sort_field: listState.sortField,
    sort_order: listState.sortOrder,
    ...(listState.name !== "" ? { name: listState.name } : {}),
    ...(listState.userId !== "" ? { user_id: listState.userId } : {}),
  }), [listState]);
  const queryKey = JSON.stringify(query);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      listSequence.current += 1;
      exportSequence.current += 1;
      exportController.current?.abort();
    };
  }, []);

  useEffect(() => {
    exportSequence.current += 1;
    exportController.current?.abort();
    exportController.current = null;
    setExporting(false);
  }, [api, ownerScope, viewKey, administratorId, queryKey, listVersion]);

  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++listSequence.current;
    const capturedOwner = ownerRef.current;
    let current = true;
    setStatus("loading");
    setError(null);
    void api.listSeries(query, controller.signal).then((page) => {
      if (!current || !mounted.current || controller.signal.aborted || sequence !== listSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      const maxPage = Math.max(1, Math.ceil(page.total / PAGE_SIZE));
      if (listState.page > maxPage) {
        onListStateChange({ ...listState, page: maxPage });
        return;
      }
      setRecords(page.data);
      setTotal(page.total);
      setStatus("ready");
    }).catch((cause: unknown) => {
      if (!current || !mounted.current || controller.signal.aborted || sequence !== listSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) {
        authCallback.current();
        return;
      }
      setError(readError(cause));
      setStatus("error");
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [api, queryKey, listState.page, listVersion, retry, onListStateChange]);

  useEffect(() => {
    exitCallback.current?.({ ownerScope, viewKey, administratorId, mode: "list", recordId: "", pending: null });
  }, [ownerScope, viewKey, administratorId]);

  const submitFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onListStateChange({ ...listState, name: draftFilters.name, userId: draftFilters.userId, page: 1 });
  };

  function changeSort(field: AdminSeriesListState["sortField"]) {
    const sortOrder = listState.sortField === field && listState.sortOrder === "asc" ? "desc" : "asc";
    onListStateChange({ ...listState, sortField: field, sortOrder, page: 1 });
  }

  async function exportCsv() {
    if (exporting) return;
    const capturedOwner = ownerRef.current;
    const capturedState = appliedState.current;
    const capturedListVersion = listVersion;
    const query = {
      skip: 0,
      limit: 1000,
      sort_field: capturedState.sortField,
      sort_order: capturedState.sortOrder,
      ...(capturedState.name !== "" ? { name: capturedState.name } : {}),
      ...(capturedState.userId !== "" ? { user_id: capturedState.userId } : {}),
    };
    const sequence = ++exportSequence.current;
    const controller = new AbortController();
    exportController.current = controller;
    const isCurrent = () => mounted.current && sequence === exportSequence.current
      && sameOwner(capturedOwner, ownerRef.current) && capturedListVersion === listVersion
      && JSON.stringify(appliedState.current) === JSON.stringify(capturedState);
    setExporting(true);
    setExportStatus("正在准备 CSV…");
    try {
      const page = await api.listSeries(query, controller.signal);
      if (!isCurrent() || controller.signal.aborted) return;
      downloadAdminSeriesCsv(serializeAdminSeriesCsv(page.data), "admin-series.csv");
      const truncation = page.total > page.data.length ? `（已导出前 ${page.data.length} 条，共 ${page.total} 条）` : "";
      setExportStatus(`已下载 ${page.data.length} 条剧集${truncation}`);
    } catch (cause) {
      if (!isCurrent() || controller.signal.aborted) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else setExportStatus("CSV 导出失败，请稍后重试。");
    } finally {
      if (exportController.current === controller) {
        exportController.current = null;
        if (isCurrent()) setExporting(false);
      }
    }
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <section className="admin-series-page">
      <header className="admin-page-heading admin-series-heading">
        <div><p className="admin-eyebrow">内容管理</p><h1>剧集管理</h1><p>管理后台全局剧集记录；新建记录归当前管理员。</p></div>
        <div className="admin-series-actions">
          <button className="admin-button admin-button-secondary" type="button" onClick={() => void exportCsv()} disabled={exporting}>导出 CSV（最多1000条）</button>
          <button className="admin-button admin-button-primary" type="button" onClick={onCreate}>新建剧集</button>
        </div>
      </header>
      {notice ? <p className="admin-series-confirmation" role="status">{notice}</p> : null}
      {exportStatus.trim() ? <p className="admin-series-export-status" role="status">{exportStatus}</p> : null}
      <form className="admin-series-filters" onSubmit={submitFilters}>
        <label>名称筛选<input aria-label="按名称筛选" value={draftFilters.name} onChange={(event) => setDraftFilters((value) => ({ ...value, name: event.target.value }))} /></label>
        <label>用户 ID 筛选<input aria-label="按用户 ID 筛选" value={draftFilters.userId} onChange={(event) => setDraftFilters((value) => ({ ...value, userId: event.target.value }))} /></label>
        <button className="admin-button admin-button-secondary" type="submit">查询</button>
        <button className="admin-button admin-button-secondary" type="button" onClick={() => { setDraftFilters({ name: "", userId: "" }); onListStateChange({ ...listState, name: "", userId: "", page: 1 }); }}>重置</button>
      </form>
      {status === "loading" ? <section className="admin-state-card" role="status">正在读取剧集…</section> : null}
      {status === "error" ? <section className="admin-state-card" role="alert"><strong>剧集列表加载失败</strong><p>{error}</p><button className="admin-button admin-button-secondary" type="button" onClick={() => setRetry((value) => value + 1)}>重试读取</button></section> : null}
      {status === "ready" && records.length === 0 ? <section className="admin-state-card" role="status">没有符合条件的剧集。</section> : null}
      {status === "ready" && records.length > 0 ? (
        <div className="admin-series-table-wrap"><table className="admin-series-table">
          <thead><tr>
            <th><button type="button" onClick={() => changeSort("id")}>剧集 ID {listState.sortField === "id" ? listState.sortOrder : ""}</button></th>
            <th><button type="button" onClick={() => changeSort("user_id")}>用户 ID {listState.sortField === "user_id" ? listState.sortOrder : ""}</button></th>
            <th><button type="button" onClick={() => changeSort("name")}>名称 {listState.sortField === "name" ? listState.sortOrder : ""}</button></th>
            <th><button type="button" onClick={() => changeSort("created_at")}>创建时间 {listState.sortField === "created_at" ? listState.sortOrder : ""}</button></th>
            <th>操作</th>
          </tr></thead>
          <tbody>{records.map((record) => <tr key={record.id}>
            <td>{record.id}</td><td>{record.user_id}</td><td>{record.name}</td><td>{record.created_at}</td>
            <td><button className="admin-button admin-button-secondary" type="button" onClick={() => editCallback.current(record.id)}>编辑</button></td>
          </tr>)}</tbody>
        </table></div>
      ) : null}
      <footer className="admin-series-pagination">
        <span>共 {total} 条 · 第 {listState.page} / {pageCount} 页</span>
        <button className="admin-button admin-button-secondary" type="button" disabled={listState.page <= 1 || status !== "ready"} onClick={() => onListStateChange({ ...listState, page: Math.max(1, listState.page - 1) })}>上一页</button>
        <button className="admin-button admin-button-secondary" type="button" disabled={listState.page >= pageCount || status !== "ready"} onClick={() => onListStateChange({ ...listState, page: listState.page + 1 })}>下一页</button>
      </footer>
    </section>
  );
}

interface SeriesDraft { name: string; description: string; imageUrl: string }
interface WriteIntent {
  id: number;
  operation: "create" | "update";
  recordId: string | null;
  draft: Readonly<SeriesDraft>;
  patch: Readonly<AdminSeriesUpdatePatch>;
  createInput: Readonly<{ name: string; description: string | null }>;
  pending: PendingAdminSeriesExit;
}

function draftFromRecord(record: AdminSeriesRecord): SeriesDraft {
  return { name: record.name, description: record.description ?? "", imageUrl: record.image_url ?? "" };
}

function patchFromDraft(baseline: AdminSeriesRecord, draft: SeriesDraft): AdminSeriesUpdatePatch {
  const patch: AdminSeriesUpdatePatch = {};
  if (draft.name !== baseline.name) patch.name = draft.name;
  if (draft.description !== (baseline.description ?? "")) {
    patch.description = draft.description === "" ? null : draft.description;
  }
  if (draft.imageUrl !== (baseline.image_url ?? "")) {
    patch.image_url = draft.imageUrl === "" ? null : draft.imageUrl;
  }
  return patch;
}

function draftAfterAbandoningUnknownUpdate(
  snapshot: AdminSeriesRecord,
  intent: WriteIntent,
): SeriesDraft {
  const nextDraft = draftFromRecord(snapshot);
  if (intent.patch.name !== undefined) nextDraft.name = intent.draft.name;
  if (intent.patch.description !== undefined) nextDraft.description = intent.draft.description;
  if (intent.patch.image_url !== undefined) nextDraft.imageUrl = intent.draft.imageUrl;
  return nextDraft;
}

function SeriesForm(props: AdminSeriesProps) {
  const { api, mode, recordId = "", ownerScope, viewKey, administratorId } = props;
  const owner: Owner = { api, scope: ownerScope, view: viewKey, administratorId, mode, recordId };
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const authCallback = useRef(props.onAuthenticationError);
  authCallback.current = props.onAuthenticationError;
  const exitCallback = useRef(props.onExitStateChange);
  exitCallback.current = props.onExitStateChange;
  const savedCallback = useRef(props.onSaved);
  savedCallback.current = props.onSaved;
  const cancelCallback = useRef(props.onCancel);
  cancelCallback.current = props.onCancel;
  const [baseline, setBaseline] = useState<AdminSeriesRecord | null>(null);
  const [draft, setDraft] = useState<SeriesDraft>({ name: "", description: "", imageUrl: "" });
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(mode === "edit" ? "loading" : "ready");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unknown, setUnknown] = useState<WriteIntent | null>(null);
  const [snapshot, setSnapshot] = useState<AdminSeriesRecord | null>(null);
  const [candidatePage, setCandidatePage] = useState<AdminSeriesPage | null>(null);
  const [snapshotState, setSnapshotState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const writeIntentRef = useRef<WriteIntent | null>(null);
  const currentUnknownRef = useRef<WriteIntent | null>(null);
  const intentSequence = useRef(0);
  const loadSequence = useRef(0);
  const mounted = useRef(true);
  currentUnknownRef.current = unknown;

  const patch = useMemo(() => baseline ? patchFromDraft(baseline, draft) : {}, [baseline, draft]);
  const isDirty = mode === "create"
    ? draft.name !== "" || draft.description !== ""
    : Object.keys(patch).length > 0;

  const publishPending = useCallback((pending: PendingAdminSeriesExit | null) => {
    const current = ownerRef.current;
    exitCallback.current?.({ ownerScope: current.scope, viewKey: current.view, administratorId: current.administratorId,
      mode: current.mode, recordId: current.recordId, pending });
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      loadSequence.current += 1;
      // Unmounting ends local tracking; it does not claim to cancel a server write.
    };
  }, []);

  useEffect(() => {
    if (mode !== "edit") {
      setLoadState("ready");
      return;
    }
    const controller = new AbortController();
    const sequence = ++loadSequence.current;
    const capturedOwner = ownerRef.current;
    let current = true;
    setLoadState("loading");
    setLoadError(null);
    void api.getSeries(recordId, controller.signal).then((record) => {
      if (!current || !mounted.current || controller.signal.aborted || sequence !== loadSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      setBaseline(record);
      setDraft(draftFromRecord(record));
      setLoadState("ready");
    }).catch((cause: unknown) => {
      if (!current || !mounted.current || controller.signal.aborted || sequence !== loadSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else { setLoadError(readError(cause)); setLoadState("error"); }
    });
    return () => { current = false; controller.abort(); };
  }, [api, mode, recordId, version]);

  useEffect(() => {
    if (writeIntentRef.current) return;
    const intent = currentUnknownRef.current;
    const pending = intent?.pending ?? (isDirty
      ? createPending(ownerRef.current, "dirty-draft", mode === "create" ? "create" : "update", intentSequence.current, "当前表单有未保存的修改。", draft)
      : null);
    publishPending(pending);
  }, [isDirty, mode, publishPending, unknown, draft]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || writeIntentRef.current || unknown || loadState !== "ready") return;
    const capturedOwner = ownerRef.current;
    const operation = mode === "create" ? "create" : "update";
    const submittedDraft = Object.freeze({ ...draft });
    const submittedPatch = Object.freeze({ ...patch }) as Readonly<AdminSeriesUpdatePatch>;
    if (operation === "update" && Object.keys(submittedPatch).length === 0) return;
    const createInput = Object.freeze({ name: submittedDraft.name, description: submittedDraft.description === "" ? null : submittedDraft.description });
    const id = ++intentSequence.current;
    const pending = createPending(capturedOwner, "write-in-flight", operation, id, "剧集保存请求已发出。离开会停止本地跟踪，但不能撤销服务端操作。", submittedDraft);
    const intent: WriteIntent = { id, operation, recordId: operation === "update" ? recordId : null, draft: submittedDraft, patch: submittedPatch, createInput, pending };
    writeIntentRef.current = intent;
    setBusy(true);
    setError(null);
    publishPending(pending);
    try {
      const saved = operation === "create"
        ? await api.createSeries(createInput)
        : await api.updateSeries(recordId, submittedPatch);
      if (!mounted.current || writeIntentRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      writeIntentRef.current = null;
      setBusy(false);
      publishPending(null);
      savedCallback.current(saved, operation);
    } catch (cause) {
      if (!mounted.current || writeIntentRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      writeIntentRef.current = null;
      setBusy(false);
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) {
        publishPending(null);
        authCallback.current();
        return;
      }
      if (isDefinedRejection(cause)) {
        publishPending(isDirty ? createPending(capturedOwner, "dirty-draft", operation, id, "当前表单有未保存的修改。", submittedDraft) : null);
        setError(cause.status === 404 ? "剧集记录已不存在。" : "服务拒绝了这次保存，请检查内容后重试。");
        return;
      }
      const frozen: WriteIntent = {
        ...intent,
        pending: createPending(capturedOwner, "unknown-write", operation, id,
          "服务未能确认这次保存是否完成。原提交内容已冻结；只读核对不会证明该请求的结果。", intent.draft),
      };
      setUnknown(frozen);
      publishPending(frozen.pending);
      setError("保存结果未知。请先只读核对当前记录；不要重复提交原请求。");
    }
  }

  async function checkCurrent() {
    const intent = currentUnknownRef.current;
    if (!intent || snapshotState === "loading") return;
    const capturedOwner = ownerRef.current;
    const capturedIntent = intent;
    const sequence = ++loadSequence.current;
    const controller = new AbortController();
    setSnapshot(null);
    setCandidatePage(null);
    setSnapshotState("loading");
    try {
      if (intent.operation === "update" && intent.recordId) {
        const current = await api.getSeries(intent.recordId, controller.signal);
        if (!mounted.current || controller.signal.aborted || sequence !== loadSequence.current
          || currentUnknownRef.current !== capturedIntent || !sameOwner(capturedOwner, ownerRef.current)) return;
        setSnapshot(current);
      } else {
        const state = props.listState;
        const page = await api.listSeries({
          skip: 0,
          limit: 1000,
          sort_field: state.sortField,
          sort_order: state.sortOrder,
          ...(state.name !== "" ? { name: state.name } : {}),
          ...(state.userId !== "" ? { user_id: state.userId } : {}),
        }, controller.signal);
        if (!mounted.current || controller.signal.aborted || sequence !== loadSequence.current
          || currentUnknownRef.current !== capturedIntent || !sameOwner(capturedOwner, ownerRef.current)) return;
        setCandidatePage(page);
      }
      setSnapshotState("ready");
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted || sequence !== loadSequence.current
        || currentUnknownRef.current !== capturedIntent || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else setSnapshotState("error");
    }
  }

  function abandonUnknown() {
    const intent = currentUnknownRef.current;
    if (!intent) return;
    if (!window.confirm("放弃原请求的本地核对后，后续保存会成为一项新操作；原请求可能仍已完成。继续吗？")) return;
    const rebasingSnapshot = mode === "edit" ? snapshot : null;
    const nextDraft = rebasingSnapshot ? draftAfterAbandoningUnknownUpdate(rebasingSnapshot, intent) : draft;
    if (rebasingSnapshot) {
      setBaseline(rebasingSnapshot);
      setDraft(nextDraft);
    }
    currentUnknownRef.current = null;
    setUnknown(null);
    setSnapshot(null);
    setCandidatePage(null);
    setSnapshotState("idle");
    setError(null);
    const dirtyAfterAbandon = rebasingSnapshot
      ? Object.keys(patchFromDraft(rebasingSnapshot, nextDraft)).length > 0
      : isDirty;
    publishPending(dirtyAfterAbandon
      ? createPending(ownerRef.current, "dirty-draft", intent.operation, intent.id, "当前表单有未保存的修改。", nextDraft)
      : null);
  }

  const cancel = () => cancelCallback.current(unknown?.pending ?? writeIntentRef.current?.pending);

  return (
    <section className="admin-series-page">
      <header className="admin-page-heading"><div><p className="admin-eyebrow">内容管理</p><h1>{mode === "create" ? "新建剧集" : "编辑剧集"}</h1><p>{mode === "create" ? "新建记录将归当前管理员。" : "仅提交本次修改的剧集元数据。"}</p></div></header>
      <form className="admin-series-form" onSubmit={(event) => { void submit(event); }}>
        {mode === "edit" && loadState === "loading" ? <section role="status" className="admin-state-card">正在读取剧集记录…</section> : null}
        {mode === "edit" && loadState === "error" ? <section role="alert" className="admin-state-card"><p>{loadError}</p><button className="admin-button admin-button-secondary" type="button" onClick={() => setVersion((value) => value + 1)}>重试读取</button><button className="admin-button admin-button-secondary" type="button" onClick={cancel}>返回列表</button></section> : null}
        {loadState === "ready" ? <>
          <label className="admin-series-field">名称<input aria-label="剧集名称" value={draft.name} disabled={busy || Boolean(unknown)} onChange={(event) => setDraft((value) => ({ ...value, name: event.target.value }))} /></label>
          <label className="admin-series-field">简介<textarea aria-label="剧集简介" value={draft.description} disabled={busy || Boolean(unknown)} onChange={(event) => setDraft((value) => ({ ...value, description: event.target.value }))} /></label>
          {mode === "edit" ? <label className="admin-series-field">封面 URL<input aria-label="封面 URL" value={draft.imageUrl} disabled={busy || Boolean(unknown)} onChange={(event) => setDraft((value) => ({ ...value, imageUrl: event.target.value }))} /></label> : null}
          {error ? <p className="admin-series-error" role="alert">{error}</p> : null}
          {unknown ? <section className="admin-series-unknown" role="region" aria-label="剧集写入结果待核对">
            <strong>保存结果待核对</strong><p>{unknown.pending.message}</p>
            <button className="admin-button admin-button-secondary" type="button" onClick={() => void checkCurrent()} disabled={snapshotState === "loading"}>{mode === "edit" ? "只读核对当前记录" : "只读核对候选列表"}</button>
            {snapshotState === "loading" ? <p role="status">正在读取当前记录…</p> : null}
            {snapshotState === "ready" && snapshot ? <div className="admin-series-snapshot"><p>当前记录快照（不代表原请求的提交回执）：</p><dl><div><dt>名称</dt><dd>{snapshot.name}</dd></div><div><dt>简介</dt><dd>{snapshot.description ?? "（空）"}</dd></div><div><dt>封面 URL</dt><dd>{snapshot.image_url ?? "（空）"}</dd></div></dl></div> : null}
            {snapshotState === "ready" && candidatePage ? <div className="admin-series-snapshot"><p>当前筛选下的只读候选（至多1000条；同名或数量变化都不能确认原请求）：</p><ul>{candidatePage.data.map((record) => <li key={record.id}>{record.id} · {record.name}</li>)}</ul><p>当前总数：{candidatePage.total}</p></div> : null}
            {snapshotState === "error" ? <p role="alert">只读核对失败，原保存结果仍未知。</p> : null}
            <button className="admin-button admin-button-secondary" type="button" onClick={abandonUnknown}>明确放弃原意图</button>
          </section> : null}
          <div className="admin-series-form-actions">
            <button className="admin-button admin-button-secondary" type="button" onClick={cancel}>返回列表</button>
            <button className="admin-button admin-button-primary" type="submit" disabled={busy || Boolean(unknown) || (mode === "edit" && Object.keys(patch).length === 0)}>{busy ? "保存中…" : "保存剧集"}</button>
          </div>
        </> : null}
      </form>
    </section>
  );
}
