import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import type {
  AdminSmtpAccountCreateInput,
  AdminSmtpAccountExitSnapshot,
  AdminSmtpAccountMode,
  AdminSmtpAccountRecord,
  AdminSmtpAccountUpdatePatch,
  AdminSmtpAccountsApi,
  PendingAdminSmtpAccountExit,
} from "./adminSmtpAccountsTypes";
import { downloadAdminSmtpAccountsCsv, serializeAdminSmtpAccountsCsv } from "./adminSmtpAccountsExport";
import "./adminSmtpAccounts.css";

const PAGE_SIZE = 10;

interface Owner {
  api: AdminSmtpAccountsApi;
  scope: string;
  view: string;
  administratorId: string;
  mode: AdminSmtpAccountMode;
  recordId: string;
}

interface FormDraft {
  host: string;
  port: string;
  user: string;
  password: string;
  isActive: boolean;
}

interface WriteIntent {
  id: number;
  operation: "create" | "update" | "delete";
  recordId: string | null;
  draft: FormDraft;
  createInput?: AdminSmtpAccountCreateInput;
  patch?: AdminSmtpAccountUpdatePatch;
  pending: PendingAdminSmtpAccountExit;
}

function sameOwner(a: Owner, b: Owner): boolean {
  return a.api === b.api && a.scope === b.scope && a.view === b.view
    && a.administratorId === b.administratorId && a.mode === b.mode && a.recordId === b.recordId;
}

function isAuthenticationError(error: unknown): error is ApiError {
  return error instanceof ApiError && [401, 403].includes(error.status ?? -1);
}

function isKnownRejection(error: unknown): error is ApiError {
  return error instanceof ApiError && [400, 401, 403, 404, 422].includes(error.status ?? -1);
}

function safeReadError(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return "这条 SMTP 账号记录已不存在。";
  if (error instanceof ApiError && error.kind === "network") return "无法连接管理后台服务，请检查服务后重试。";
  if (error instanceof ApiError && error.kind === "timeout") return "读取 SMTP 账号超时，请稍后重试。";
  return "读取 SMTP 账号失败，请重试。";
}

function pendingFor(
  owner: Owner,
  kind: PendingAdminSmtpAccountExit["kind"],
  operation: PendingAdminSmtpAccountExit["operation"],
  intentId: number,
  message: string,
  secretChanged: boolean,
  recordId?: string | null,
): PendingAdminSmtpAccountExit {
  return {
    kind,
    operation,
    mode: owner.mode,
    recordId: operation === "delete" ? (recordId ?? owner.recordId) || null : owner.mode === "edit" ? owner.recordId : null,
    intentId,
    secretChanged,
    message,
  };
}

function defaultDraft(): FormDraft {
  return { host: "smtp.qq.com", port: "465", user: "", password: "", isActive: true };
}

function draftFromRecord(record: AdminSmtpAccountRecord): FormDraft {
  return {
    host: record.host,
    port: String(record.port),
    user: record.user,
    password: "",
    isActive: record.is_active,
  };
}

function parsePort(value: string): number | null {
  if (!/^[+-]?\d+$/.test(value)) return null;
  const port = Number(value);
  return Number.isSafeInteger(port) ? port : null;
}

function patchFromDraft(baseline: AdminSmtpAccountRecord, draft: FormDraft): AdminSmtpAccountUpdatePatch {
  const patch: AdminSmtpAccountUpdatePatch = {};
  if (draft.user !== baseline.user) patch.user = draft.user;
  if (draft.host !== baseline.host) patch.host = draft.host;
  const port = parsePort(draft.port);
  if (port !== null && port !== baseline.port) patch.port = port;
  if (draft.isActive !== baseline.is_active) patch.is_active = draft.isActive;
  if (draft.password !== "") patch.password = draft.password;
  return patch;
}

export interface AdminSmtpAccountsProps {
  api: AdminSmtpAccountsApi;
  mode: AdminSmtpAccountMode;
  recordId?: string;
  ownerScope: string;
  viewKey: string;
  administratorId: string;
  listVersion: number;
  onAuthenticationError(): void;
  onCreate(): void;
  onEdit(id: string): void;
  onCancel(pending?: PendingAdminSmtpAccountExit): void;
  onSaved(): void;
  onExitStateChange?(snapshot: AdminSmtpAccountExitSnapshot): void;
}

export function AdminSmtpAccounts(props: AdminSmtpAccountsProps) {
  if (props.mode === "list") return <AccountList {...props} />;
  return <AccountForm {...props} />;
}

function AccountList({
  api, ownerScope, viewKey, administratorId, listVersion, onAuthenticationError, onCreate, onEdit,
  onCancel, onSaved, onExitStateChange,
}: AdminSmtpAccountsProps) {
  const owner = useMemo<Owner>(() => ({ api, scope: ownerScope, view: viewKey, administratorId, mode: "list", recordId: "" }),
    [administratorId, api, ownerScope, viewKey]);
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const previousOwnerRef = useRef(owner);
  const ownerIsCurrent = sameOwner(owner, previousOwnerRef.current);
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const cancelCallback = useRef(onCancel);
  cancelCallback.current = onCancel;
  const exitCallback = useRef(onExitStateChange);
  exitCallback.current = onExitStateChange;
  const savedCallback = useRef(onSaved);
  savedCallback.current = onSaved;
  const [records, setRecords] = useState<AdminSmtpAccountRecord[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [writeBusy, setWriteBusy] = useState(false);
  const [unknownDelete, setUnknownDelete] = useState<WriteIntent | null>(null);
  const [readback, setReadback] = useState<AdminSmtpAccountRecord | null>(null);
  const [readbackState, setReadbackState] = useState<"idle" | "loading" | "ready" | "missing" | "error">("idle");
  const listSequence = useRef(0);
  const readbackSequence = useRef(0);
  const intentSequence = useRef(0);
  const writeIntentRef = useRef<WriteIntent | null>(null);
  const unknownRef = useRef<WriteIntent | null>(null);
  const mounted = useRef(true);
  const listController = useRef<AbortController | null>(null);
  const readbackController = useRef<AbortController | null>(null);
  unknownRef.current = unknownDelete;

  function publishPending(pending: PendingAdminSmtpAccountExit | null): void {
    const current = ownerRef.current;
    exitCallback.current?.({ ownerScope: current.scope, viewKey: current.view, administratorId: current.administratorId,
      mode: current.mode, recordId: current.recordId, pending });
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      listSequence.current += 1;
      readbackSequence.current += 1;
      listController.current?.abort();
      readbackController.current?.abort();
    };
  }, []);

  useLayoutEffect(() => {
    if (ownerIsCurrent) return;
    previousOwnerRef.current = owner;
    listSequence.current += 1;
    readbackSequence.current += 1;
    listController.current?.abort();
    readbackController.current?.abort();
    writeIntentRef.current = null;
    unknownRef.current = null;
    setRecords([]);
    setStatus("loading");
    setError(null);
    setNotice(null);
    setPage(1);
    setWriteBusy(false);
    setUnknownDelete(null);
    setReadback(null);
    setReadbackState("idle");
  }, [owner, ownerIsCurrent]);

  useEffect(() => {
    const controller = new AbortController();
    listController.current?.abort();
    listController.current = controller;
    const sequence = ++listSequence.current;
    const capturedOwner = ownerRef.current;
    let current = true;
    setStatus("loading");
    setError(null);
    void api.listAccounts(controller.signal).then((pageResult) => {
      if (!current || !mounted.current || controller.signal.aborted || sequence !== listSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      setRecords(pageResult.data);
      setPage((value) => Math.min(value, Math.max(1, Math.ceil(pageResult.total / PAGE_SIZE))));
      setStatus("ready");
    }).catch((cause: unknown) => {
      if (!current || !mounted.current || controller.signal.aborted || sequence !== listSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (isAuthenticationError(cause)) authCallback.current();
      else { setError(safeReadError(cause)); setStatus("error"); }
    });
    return () => { current = false; controller.abort(); };
  }, [api, listVersion, retry, ownerScope, viewKey, administratorId]);

  useEffect(() => {
    if (!ownerIsCurrent) {
      publishPending(null);
      return;
    }
    const pending = unknownRef.current?.pending ?? writeIntentRef.current?.pending ?? null;
    publishPending(pending);
  }, [ownerIsCurrent, unknownDelete, writeBusy]);

  const total = records.length;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visibleRecords = records.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  async function remove(record: AdminSmtpAccountRecord) {
    if (writeIntentRef.current || unknownRef.current) return;
    if (!window.confirm(`确定删除 SMTP 账号 ${record.user}？此操作会删除账号配置。`)) return;
    const capturedOwner = ownerRef.current;
    const intentId = ++intentSequence.current;
    const pending = pendingFor(capturedOwner, "write-in-flight", "delete", intentId,
      "SMTP 删除请求已发出。离开会停止本地跟踪，但不能撤销服务端操作。", false, record.id);
    const intent: WriteIntent = { id: intentId, operation: "delete", recordId: record.id,
      draft: defaultDraft(), pending };
    writeIntentRef.current = intent;
    setWriteBusy(true);
    setError(null);
    setNotice(null);
    publishPending(pending);
    try {
      await api.deleteAccount(record.id);
      if (!mounted.current || writeIntentRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      writeIntentRef.current = null;
      setWriteBusy(false);
      setRecords((current) => current.filter((item) => item.id !== record.id));
      setNotice("服务端已确认删除 SMTP 账号。");
      publishPending(null);
      savedCallback.current();
    } catch (cause) {
      if (!mounted.current || writeIntentRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      writeIntentRef.current = null;
      setWriteBusy(false);
      if (isAuthenticationError(cause)) {
        publishPending(null);
        authCallback.current();
        return;
      }
      if (isKnownRejection(cause)) {
        setError(cause.status === 404 ? "该 SMTP 账号已不存在，删除未获确认。" : "服务拒绝了删除请求，请刷新后重试。");
        publishPending(null);
        return;
      }
      const frozen: WriteIntent = {
        ...intent,
        pending: pendingFor(capturedOwner, "unknown-write", "delete", intentId,
          "SMTP 删除结果待核对。只读核对不能证明原请求是否完成。", false, record.id),
      };
      unknownRef.current = frozen;
      setUnknownDelete(frozen);
      setReadback(null);
      setReadbackState("idle");
      setError("删除结果待核对；请勿重试原请求。可读取当前记录并在明确放弃后重新操作。");
      publishPending(frozen.pending);
    }
  }

  async function checkDeletedRecord() {
    const intent = unknownRef.current;
    if (!intent?.recordId || readbackState === "loading") return;
    const capturedOwner = ownerRef.current;
    const sequence = ++readbackSequence.current;
    const controller = new AbortController();
    readbackController.current?.abort();
    readbackController.current = controller;
    setReadback(null);
    setReadbackState("loading");
    try {
      const record = await api.getAccount(intent.recordId, controller.signal);
      if (!mounted.current || controller.signal.aborted || sequence !== readbackSequence.current
        || unknownRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      setReadback(record);
      setReadbackState("ready");
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted || sequence !== readbackSequence.current
        || unknownRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (isAuthenticationError(cause)) authCallback.current();
      else if (cause instanceof ApiError && cause.status === 404) setReadbackState("missing");
      else setReadbackState("error");
    }
  }

  function abandonDelete() {
    if (!unknownRef.current) return;
    if (!window.confirm("放弃原删除请求的本地核对后，后续删除会成为一项新操作；原请求可能已完成。继续吗？")) return;
    unknownRef.current = null;
    setUnknownDelete(null);
    setReadback(null);
    setReadbackState("idle");
    setError(null);
    setRetry((value) => value + 1);
    publishPending(null);
  }

  function exportAccounts() {
    if (status !== "ready") return;
    const exported = records.slice(0, 1000);
    downloadAdminSmtpAccountsCsv(serializeAdminSmtpAccountsCsv(exported), "admin-smtp-accounts.csv");
    setNotice(records.length > exported.length
      ? `已导出前 ${exported.length} 条账号元数据，完整列表共 ${records.length} 条。`
      : `已导出 ${exported.length} 条账号元数据。`);
  }

  const cancel = () => cancelCallback.current(unknownRef.current?.pending ?? writeIntentRef.current?.pending);

  return (
    <section className="admin-smtp-page" aria-label="SMTP 账号列表">
      <header className="admin-page-heading admin-smtp-heading">
        <div>
          <p className="admin-eyebrow">系统运营</p>
          <h1>SMTP 账号</h1>
          <p>查看和维护邮件服务账号配置。列表一次读取全部账号，不会自动轮询。</p>
        </div>
        <div className="admin-smtp-heading-actions">
          <button className="admin-button admin-button-secondary" type="button" onClick={exportAccounts} disabled={status !== "ready"}>
            导出 CSV（最多1000条）
          </button>
          <button className="admin-button admin-button-primary" type="button" onClick={onCreate}>新建 SMTP 账号</button>
        </div>
      </header>
      {notice ? <p className="admin-smtp-notice" role="status">{notice}</p> : null}
      {error ? <p className="admin-smtp-error" role="alert">{error}</p> : null}
      {status === "loading" ? <section className="admin-state-card" role="status">正在读取 SMTP 账号…</section> : null}
      {status === "error" ? <section className="admin-state-card"><button className="admin-button admin-button-secondary" type="button" onClick={() => setRetry((value) => value + 1)}>重试读取</button></section> : null}
      {status === "ready" ? (
        <>
          <p className="admin-smtp-list-note">共 {total} 个账号。分页仅在本地切换；列表读取可能触发服务端每日计数归零逻辑。</p>
          <div className="admin-smtp-table-wrap">
            <table className="admin-smtp-table">
              <thead><tr><th>邮箱</th><th>SMTP 主机</th><th>端口</th><th>今日发送量</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
              <tbody>
                {visibleRecords.map((record) => (
                  <tr key={record.id}>
                    <td>{record.user}</td><td>{record.host}</td><td>{record.port}</td><td>{record.daily_sent}</td>
                    <td>{record.is_active ? "已启用" : "已停用"}</td><td>{record.created_at ?? "—"}</td>
                    <td className="admin-smtp-row-actions">
                      <button className="admin-button admin-button-secondary" type="button" onClick={() => onEdit(record.id)} disabled={writeBusy || Boolean(unknownDelete)}>编辑</button>
                      <button className="admin-button admin-button-danger" type="button" onClick={() => void remove(record)} disabled={writeBusy || Boolean(unknownDelete)}>删除</button>
                    </td>
                  </tr>
                ))}
                {visibleRecords.length === 0 ? <tr><td colSpan={7} className="admin-smtp-empty">暂无 SMTP 账号</td></tr> : null}
              </tbody>
            </table>
          </div>
          {unknownDelete ? (
            <section className="admin-smtp-unknown" aria-label="SMTP 删除结果待核对">
              <strong>删除结果待核对</strong>
              <p>{unknownDelete.pending.message}</p>
              <button className="admin-button admin-button-secondary" type="button" onClick={() => void checkDeletedRecord()} disabled={readbackState === "loading"}>只读核对当前账号</button>
              {readbackState === "loading" ? <p role="status">正在读取账号…</p> : null}
              {readbackState === "ready" && readback ? <p>当前账号仍可读取：{readback.user}（该快照不代表删除请求回执）。</p> : null}
              {readbackState === "missing" ? <p>当前读取返回账号不存在；这不能确认原删除请求的结果。</p> : null}
              {readbackState === "error" ? <p role="alert">只读核对失败，原删除结果仍未知。</p> : null}
              <button className="admin-button admin-button-secondary" type="button" onClick={abandonDelete}>明确放弃原意图</button>
            </section>
          ) : null}
          <div className="admin-smtp-pagination">
            <button className="admin-button admin-button-secondary" type="button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</button>
            <span>第 {currentPage} / {pageCount} 页</span>
            <button className="admin-button admin-button-secondary" type="button" disabled={currentPage >= pageCount} onClick={() => setPage(currentPage + 1)}>下一页</button>
          </div>
        </>
      ) : null}
      <button className="admin-button admin-button-secondary admin-smtp-back" type="button" onClick={cancel}>返回概览</button>
    </section>
  );
}

function AccountForm(props: AdminSmtpAccountsProps) {
  const { api, mode, recordId = "", ownerScope, viewKey, administratorId,
    onAuthenticationError, onCancel, onSaved, onExitStateChange } = props;
  const owner = useMemo<Owner>(() => ({ api, scope: ownerScope, view: viewKey, administratorId, mode, recordId }),
    [administratorId, api, mode, ownerScope, recordId, viewKey]);
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const previousOwnerRef = useRef(owner);
  const ownerIsCurrent = sameOwner(owner, previousOwnerRef.current);
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const cancelCallback = useRef(onCancel);
  cancelCallback.current = onCancel;
  const savedCallback = useRef(onSaved);
  savedCallback.current = onSaved;
  const exitCallback = useRef(onExitStateChange);
  exitCallback.current = onExitStateChange;
  const [baseline, setBaseline] = useState<AdminSmtpAccountRecord | null>(null);
  const [draft, setDraft] = useState<FormDraft>(defaultDraft);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(mode === "edit" ? "loading" : "ready");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unknown, setUnknown] = useState<WriteIntent | null>(null);
  const [snapshot, setSnapshot] = useState<AdminSmtpAccountRecord | null>(null);
  const [snapshotState, setSnapshotState] = useState<"idle" | "loading" | "ready" | "missing" | "error">("idle");
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [loadVersion, setLoadVersion] = useState(0);
  const writeIntentRef = useRef<WriteIntent | null>(null);
  const unknownRef = useRef<WriteIntent | null>(null);
  const intentSequence = useRef(0);
  const loadSequence = useRef(0);
  const snapshotSequence = useRef(0);
  const loadController = useRef<AbortController | null>(null);
  const snapshotController = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  unknownRef.current = unknown;

  const patch = useMemo(() => baseline ? patchFromDraft(baseline, draft) : {}, [baseline, draft]);
  const parsedPort = parsePort(draft.port);
  const validPort = parsedPort !== null;
  const isDirty = mode === "create"
    ? draft.host !== "smtp.qq.com" || draft.port !== "465" || draft.user !== "" || draft.password !== "" || !draft.isActive
    : baseline !== null && (Object.keys(patch).length > 0 || !validPort || parsedPort !== baseline.port);
  const forbiddenReplacement = mode === "edit" && draft.password.includes("****");

  function publishPending(pending: PendingAdminSmtpAccountExit | null): void {
    const current = ownerRef.current;
    exitCallback.current?.({ ownerScope: current.scope, viewKey: current.view, administratorId: current.administratorId,
      mode: current.mode, recordId: current.recordId, pending });
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      loadSequence.current += 1;
      snapshotSequence.current += 1;
      loadController.current?.abort();
      snapshotController.current?.abort();
    };
  }, []);

  useLayoutEffect(() => {
    if (ownerIsCurrent) return;
    previousOwnerRef.current = owner;
    loadSequence.current += 1;
    snapshotSequence.current += 1;
    loadController.current?.abort();
    snapshotController.current?.abort();
    writeIntentRef.current = null;
    unknownRef.current = null;
    setBaseline(null);
    setDraft(defaultDraft());
    setLoadState(mode === "edit" ? "loading" : "ready");
    setLoadError(null);
    setError(null);
    setUnknown(null);
    setSnapshot(null);
    setSnapshotState("idle");
    setBusy(false);
    setReceipt(null);
  }, [mode, owner, ownerIsCurrent]);

  useEffect(() => {
    if (mode === "create") {
      setLoadState("ready");
      return;
    }
    const controller = new AbortController();
    loadController.current?.abort();
    loadController.current = controller;
    const sequence = ++loadSequence.current;
    const capturedOwner = ownerRef.current;
    let current = true;
    setLoadState("loading");
    setLoadError(null);
    void api.getAccount(recordId, controller.signal).then((record) => {
      if (!current || !mounted.current || controller.signal.aborted || sequence !== loadSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      setBaseline(record);
      setDraft(draftFromRecord(record));
      setLoadState("ready");
    }).catch((cause: unknown) => {
      if (!current || !mounted.current || controller.signal.aborted || sequence !== loadSequence.current
        || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (isAuthenticationError(cause)) authCallback.current();
      else { setLoadError(safeReadError(cause)); setLoadState("error"); }
    });
    return () => { current = false; controller.abort(); };
  }, [api, mode, recordId, loadVersion, ownerScope, viewKey, administratorId]);

  useEffect(() => {
    if (!ownerIsCurrent) {
      publishPending(null);
      return;
    }
    if (writeIntentRef.current) return;
    const pending = unknownRef.current?.pending ?? (isDirty
      ? pendingFor(ownerRef.current, "dirty-draft", mode === "create" ? "create" : "update", intentSequence.current,
        "当前 SMTP 表单有未保存的修改。", draft.password !== "")
      : null);
    publishPending(pending);
  }, [baseline, draft, isDirty, mode, ownerIsCurrent, unknown]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || writeIntentRef.current || unknownRef.current || loadState !== "ready") return;
    if (!validPort) { setError("端口必须是安全整数。"); return; }
    if (mode === "create" && (!draft.user || !draft.password)) { setError("邮箱和授权码不能为空。"); return; }
    if (forbiddenReplacement) { setError("替换授权码不能包含连续四个星号；当前接口不支持清空授权码。"); return; }

    const capturedOwner = ownerRef.current;
    const operation = mode === "create" ? "create" : "update";
    const submittedDraft: FormDraft = { ...draft };
    const submittedPatch: AdminSmtpAccountUpdatePatch = { ...patch };
    if (operation === "update" && Object.keys(submittedPatch).length === 0) return;
    const createInput: AdminSmtpAccountCreateInput = {
      user: submittedDraft.user,
      password: submittedDraft.password,
      host: submittedDraft.host,
      port: parsePort(submittedDraft.port) as number,
      is_active: submittedDraft.isActive,
    };
    const intentId = ++intentSequence.current;
    const pending = pendingFor(capturedOwner, "write-in-flight", operation, intentId,
      "SMTP 保存请求已发出。离开会停止本地跟踪，但不能撤销服务端操作。", operation === "create" || submittedPatch.password !== undefined);
    const intent: WriteIntent = {
      id: intentId, operation, recordId: operation === "update" ? recordId : null, draft: submittedDraft,
      ...(operation === "create" ? { createInput } : { patch: submittedPatch }), pending,
    };
    writeIntentRef.current = intent;
    setBusy(true);
    setError(null);
    setReceipt(null);
    publishPending(pending);
    try {
      let createdId: string | null = null;
      let createdUser: string | null = null;
      if (operation === "create") {
        const ack = await api.createAccount(createInput);
        createdId = ack.id;
        createdUser = ack.user;
      } else {
        await api.updateAccount(recordId, submittedPatch);
      }
      if (!mounted.current || writeIntentRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      writeIntentRef.current = null;
      setBusy(false);
      publishPending(null);
      if (operation === "create") {
        setDraft(defaultDraft());
        setReceipt(`服务端已确认创建 SMTP 账号 ${createdId}（${createdUser}）。`);
      } else if (baseline) {
        const nextBaseline: AdminSmtpAccountRecord = {
          ...baseline,
          ...(submittedPatch.user !== undefined ? { user: submittedPatch.user } : {}),
          ...(submittedPatch.host !== undefined ? { host: submittedPatch.host } : {}),
          ...(submittedPatch.port !== undefined ? { port: submittedPatch.port } : {}),
          ...(submittedPatch.is_active !== undefined ? { is_active: submittedPatch.is_active } : {}),
        };
        setBaseline(nextBaseline);
        setDraft({ ...draftFromRecord(nextBaseline), password: "" });
        setReceipt("服务端已确认更新操作。该接口不返回字段回显。");
      }
      savedCallback.current();
    } catch (cause) {
      if (!mounted.current || writeIntentRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      writeIntentRef.current = null;
      setBusy(false);
      if (isAuthenticationError(cause)) {
        publishPending(null);
        authCallback.current();
        return;
      }
      if (isKnownRejection(cause)) {
        setError(cause.status === 404 ? "该 SMTP 账号已不存在，保存未获确认。" : "服务拒绝了保存请求，请检查内容后重试。");
        publishPending(isDirty ? pendingFor(capturedOwner, "dirty-draft", operation, intentId,
          "当前 SMTP 表单有未保存的修改。", submittedDraft.password !== "") : null);
        return;
      }
      const frozen: WriteIntent = {
        ...intent,
        pending: pendingFor(capturedOwner, "unknown-write", operation, intentId,
          "SMTP 保存结果待核对。原提交内容已冻结；只读核对不会证明原请求结果。", operation === "create" || submittedPatch.password !== undefined),
      };
      unknownRef.current = frozen;
      setUnknown(frozen);
      setSnapshot(null);
      setSnapshotState("idle");
      setError("保存结果未知。请先只读核对当前账号；不要重复提交原请求。");
      publishPending(frozen.pending);
    }
  }

  async function checkCurrent() {
    const intent = unknownRef.current;
    if (!intent || !intent.recordId || snapshotState === "loading") return;
    const capturedOwner = ownerRef.current;
    const sequence = ++snapshotSequence.current;
    const controller = new AbortController();
    snapshotController.current?.abort();
    snapshotController.current = controller;
    setSnapshot(null);
    setSnapshotState("loading");
    try {
      const current = await api.getAccount(intent.recordId, controller.signal);
      if (!mounted.current || controller.signal.aborted || sequence !== snapshotSequence.current
        || unknownRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      setSnapshot(current);
      setSnapshotState("ready");
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted || sequence !== snapshotSequence.current
        || unknownRef.current !== intent || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (isAuthenticationError(cause)) authCallback.current();
      else if (cause instanceof ApiError && cause.status === 404) setSnapshotState("missing");
      else setSnapshotState("error");
    }
  }

  function abandonUnknown() {
    const intent = unknownRef.current;
    if (!intent) return;
    if (mode === "edit" && intent.operation === "update" && snapshotState !== "ready") return;
    if (!window.confirm("放弃原保存请求的本地核对后，后续保存会成为一项新操作；原请求可能已完成。继续吗？")) return;
    if (mode === "edit" && intent.operation === "update" && snapshot) {
      const nextDraft = draftFromRecord(snapshot);
      const originalPatch = intent.patch ?? {};
      if (originalPatch.user !== undefined) nextDraft.user = intent.draft.user;
      if (originalPatch.host !== undefined) nextDraft.host = intent.draft.host;
      if (originalPatch.port !== undefined) nextDraft.port = intent.draft.port;
      if (originalPatch.is_active !== undefined) nextDraft.isActive = intent.draft.isActive;
      setBaseline(snapshot);
      setDraft(nextDraft);
    } else if (intent.operation === "create") {
      setDraft((current) => ({ ...current, password: "" }));
    }
    intent.draft.password = "";
    if (intent.createInput) intent.createInput.password = "";
    if (intent.patch) delete intent.patch.password;
    unknownRef.current = null;
    setUnknown(null);
    setSnapshot(null);
    setSnapshotState("idle");
    setError(null);
  }

  const cancel = () => cancelCallback.current(unknownRef.current?.pending ?? writeIntentRef.current?.pending);
  const submitDisabled = busy || Boolean(unknown) || loadState !== "ready" || !validPort
    || (mode === "create" && (!draft.user || !draft.password)) || forbiddenReplacement
    || (mode === "edit" && Object.keys(patch).length === 0);

  return (
    <section className="admin-smtp-page" aria-label={mode === "create" ? "新建 SMTP 账号" : "编辑 SMTP 账号"}>
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">系统运营</p>
          <h1>{mode === "create" ? "新建 SMTP 账号" : "编辑 SMTP 账号"}</h1>
          <p>{mode === "create" ? "保存账号配置；此操作不会发送测试邮件。" : "仅提交本次修改的账号字段。授权码留空会保留现有值。"}</p>
        </div>
      </header>
      {loadState === "loading" ? <section role="status" className="admin-state-card">正在读取 SMTP 账号…</section> : null}
      {loadState === "error" ? <section role="alert" className="admin-state-card"><p>{loadError}</p><button className="admin-button admin-button-secondary" type="button" onClick={() => setLoadVersion((value) => value + 1)}>重试读取</button><button className="admin-button admin-button-secondary" type="button" onClick={cancel}>返回列表</button></section> : null}
      {loadState === "ready" ? (
        <form className="admin-smtp-form" onSubmit={(event) => { void submit(event); }}>
          <label className="admin-smtp-field">邮箱
            <input aria-label="SMTP 邮箱" autoComplete="off" required value={draft.user} disabled={busy || Boolean(unknown)} onChange={(event) => { setDraft((value) => ({ ...value, user: event.target.value })); setReceipt(null); }} />
          </label>
          <label className="admin-smtp-field">SMTP 主机
            <input aria-label="SMTP 主机" autoComplete="off" value={draft.host} disabled={busy || Boolean(unknown)} onChange={(event) => { setDraft((value) => ({ ...value, host: event.target.value })); setReceipt(null); }} />
          </label>
          <label className="admin-smtp-field">端口
            <input aria-label="SMTP 端口" inputMode="numeric" value={draft.port} disabled={busy || Boolean(unknown)} onChange={(event) => { setDraft((value) => ({ ...value, port: event.target.value })); setReceipt(null); }} />
          </label>
          <label className="admin-smtp-field">新授权码{mode === "edit" ? "（留空保留）" : ""}
            <input aria-label="SMTP 授权码" type="password" autoComplete="new-password" required={mode === "create"} value={draft.password} disabled={busy || Boolean(unknown)} onChange={(event) => { setDraft((value) => ({ ...value, password: event.target.value })); setReceipt(null); setError(null); }} />
          </label>
          {mode === "edit" && baseline ? <p className="admin-smtp-secret-note">读取到的授权码不会回填到表单。当前接口不支持清空授权码。</p> : null}
          {forbiddenReplacement ? <p className="admin-smtp-error" role="alert">替换授权码不能包含连续四个星号；当前接口会忽略这类替换。</p> : null}
          <label className="admin-smtp-toggle"><input aria-label="SMTP 已启用" type="checkbox" checked={draft.isActive} disabled={busy || Boolean(unknown)} onChange={(event) => { setDraft((value) => ({ ...value, isActive: event.target.checked })); setReceipt(null); }} />启用账号</label>
          {error ? <p className="admin-smtp-error" role="alert">{error}</p> : null}
          {receipt ? <p className="admin-smtp-receipt" role="status">{receipt}</p> : null}
          {unknown ? (
            <section className="admin-smtp-unknown" aria-label="SMTP 保存结果待核对">
              <strong>保存结果待核对</strong><p>{unknown.pending.message}</p>
              {mode === "edit" ? <button className="admin-button admin-button-secondary" type="button" onClick={() => void checkCurrent()} disabled={snapshotState === "loading"}>只读核对当前账号</button> : <p>创建操作没有可归因的服务器回执；不会按邮箱或列表数量判断是否创建成功。</p>}
              {snapshotState === "loading" ? <p role="status">正在读取当前账号…</p> : null}
              {snapshotState === "ready" && snapshot ? <dl className="admin-smtp-snapshot"><dt>当前邮箱</dt><dd>{snapshot.user}</dd><dt>主机</dt><dd>{snapshot.host}</dd><dt>端口</dt><dd>{snapshot.port}</dd><dt>启用状态</dt><dd>{snapshot.is_active ? "已启用" : "已停用"}</dd><dt>密码</dt><dd>不显示凭据（快照不代表原请求回执）</dd></dl> : null}
              {snapshotState === "missing" ? <p>当前读取返回账号不存在；这不能确认原保存请求的结果。</p> : null}
              {snapshotState === "error" ? <p role="alert">只读核对失败，原保存结果仍未知。</p> : null}
              <button className="admin-button admin-button-secondary" type="button" onClick={abandonUnknown} disabled={mode === "edit" && intentNeedsSnapshot(unknown) && snapshotState !== "ready"}>明确放弃原意图</button>
            </section>
          ) : null}
          <div className="admin-smtp-form-actions">
            <button className="admin-button admin-button-secondary" type="button" onClick={cancel}>返回列表</button>
            <button className="admin-button admin-button-primary" type="submit" disabled={submitDisabled}>{busy ? "保存中…" : "保存账号"}</button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

function intentNeedsSnapshot(intent: WriteIntent): boolean {
  return intent.operation === "update";
}
