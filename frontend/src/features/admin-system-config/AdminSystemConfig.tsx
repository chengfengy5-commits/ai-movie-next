import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type {
  AdminSystemConfig,
  AdminSystemConfigApi,
  AdminSystemConfigExitSnapshot,
  AdminSystemConfigUpdate,
  PendingAdminSystemConfigExit,
} from "./adminSystemConfigTypes";
import "./adminSystemConfig.css";

type AdminSystemConfigMode = "list" | "edit";

export interface AdminSystemConfigProps {
  api: AdminSystemConfigApi;
  mode: AdminSystemConfigMode;
  configId?: string;
  ownerScope: string;
  viewKey: string;
  administratorId: string;
  listVersion: number;
  onAuthenticationError(): void;
  onEdit(id: string): void;
  onCancel(pending?: PendingAdminSystemConfigExit): void;
  onSaved(): void;
  onExitStateChange?(snapshot: AdminSystemConfigExitSnapshot): void;
}

interface Owner {
  api: AdminSystemConfigApi;
  scope: string;
  view: string;
  administratorId: string;
  configId: string;
  mode: AdminSystemConfigMode;
}

function sameOwner(left: Owner, right: Owner): boolean {
  return left.api === right.api && left.scope === right.scope && left.view === right.view
    && left.administratorId === right.administratorId && left.configId === right.configId
    && left.mode === right.mode;
}

function visibleReadError(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return "当前系统配置不存在。";
  if (error instanceof ApiError && error.kind === "network") return "无法连接管理后台服务，请检查服务后重试。";
  if (error instanceof ApiError && error.kind === "timeout") return "读取系统配置超时，请稍后重试。";
  return "系统配置读取失败，请重试。";
}

interface SystemConfigListProps {
  api: AdminSystemConfigApi;
  owner: Owner;
  listVersion: number;
  onAuthenticationError(): void;
  onEdit(id: string): void;
}

function SystemConfigList({
  api,
  owner,
  listVersion,
  onAuthenticationError,
  onEdit,
}: SystemConfigListProps) {
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const editCallback = useRef(onEdit);
  editCallback.current = onEdit;

  const [status, setStatus] = useState<"loading" | "opening" | "empty" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const requestSequence = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestSequence.current += 1;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++requestSequence.current;
    const capturedOwner = ownerRef.current;
    let current = true;
    setStatus("loading");
    setError(null);

    void api.list(controller.signal).then((page) => {
      if (!current || !mounted.current || controller.signal.aborted
        || sequence !== requestSequence.current || !sameOwner(capturedOwner, ownerRef.current)) return;
      const first = page.data[0];
      if (first) {
        setStatus("opening");
        editCallback.current(first.id);
      } else {
        setStatus("empty");
      }
    }).catch((cause: unknown) => {
      if (!current || !mounted.current || controller.signal.aborted
        || sequence !== requestSequence.current || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) {
        authCallback.current();
        return;
      }
      setError(visibleReadError(cause));
      setStatus("error");
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, owner.scope, owner.view, owner.administratorId, listVersion, retry]);

  return (
    <section className="admin-system-config-page">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">后台配置</p>
          <h1>系统配置</h1>
          <p>查看并编辑当前系统配置。</p>
        </div>
      </header>
      {status === "loading" || status === "opening" ? (
        <section className="admin-state-card" role="status">
          {status === "loading" ? "正在读取系统配置…" : "正在打开系统配置…"}
        </section>
      ) : status === "empty" ? (
        <section className="admin-state-card" role="status">
          <strong>尚未配置系统信息</strong>
          <p>当前服务没有返回系统配置记录。此页面不会自动新建配置。</p>
          <button className="admin-button admin-button-secondary" type="button"
            onClick={() => setRetry((value) => value + 1)}>重试读取</button>
        </section>
      ) : (
        <section className="admin-state-card" role="alert">
          <strong>系统配置列表加载失败</strong>
          <p>{error}</p>
          <button className="admin-button admin-button-secondary" type="button"
            onClick={() => setRetry((value) => value + 1)}>重试读取</button>
        </section>
      )}
    </section>
  );
}

interface SystemConfigDraft {
  muse_token: string;
  site_title: string;
  site_tagline: string;
  storyboard_cost_multiplier: string;
  membership_benefits: string;
  payments_enabled: boolean;
  tutorial_url: string;
  purchase_qr_code_url: string;
  purchase_contact_info: string;
  team_created_limit: string;
  team_joined_limit: string;
  team_member_limit: string;
  invite_code_ttl_hours: string;
  chapter_lock_idle_minutes: string;
  aistarslab_credit_multiplier: string;
  frontend_url: string;
  allowed_origins: string;
  upload_dir: string;
  worker_max_concurrency: string;
}

type IntegerField =
  | "storyboard_cost_multiplier"
  | "team_created_limit"
  | "team_joined_limit"
  | "team_member_limit"
  | "invite_code_ttl_hours"
  | "chapter_lock_idle_minutes";

interface ChangeSet {
  patch: AdminSystemConfigUpdate;
  error: string | null;
}

interface WriteAttempt {
  id: number;
  owner: Owner;
  baseline: AdminSystemConfig;
  draft: Readonly<SystemConfigDraft>;
  patch: Readonly<AdminSystemConfigUpdate>;
  pending: PendingAdminSystemConfigExit;
}

interface UnknownWrite {
  id: number;
  draft: Readonly<SystemConfigDraft>;
  baseline: AdminSystemConfig;
  patch: Readonly<AdminSystemConfigUpdate>;
  pending: PendingAdminSystemConfigExit;
}

interface ReadbackSnapshot {
  owner: Owner;
  attemptId: number;
  fields: Array<{ label: string; submitted: string; current: string }>;
}

const READBACK_FIELD_LABELS: Record<keyof AdminSystemConfigUpdate, string> = {
  muse_token: "Muse 令牌",
  site_title: "网站标题",
  site_tagline: "网站标语",
  storyboard_cost_multiplier: "分镜费用倍率",
  membership_benefits: "会员权益说明",
  payments_enabled: "在线支付",
  tutorial_url: "教程链接",
  purchase_qr_code_url: "购买二维码地址",
  purchase_contact_info: "购买联系说明",
  team_created_limit: "可创建团队数",
  team_joined_limit: "可加入团队数",
  team_member_limit: "团队成员上限",
  invite_code_ttl_hours: "邀请码有效时长",
  chapter_lock_idle_minutes: "章节锁空闲时长",
  aistarslab_credit_multiplier: "AIStarsLab 积分倍率",
  frontend_url: "前端地址",
  allowed_origins: "允许的来源地址",
  upload_dir: "上传目录",
  worker_max_concurrency: "Worker 最大并发",
};

function snapshotValue(field: keyof AdminSystemConfigUpdate, value: unknown): string {
  if (field === "muse_token") {
    return typeof value === "string" && value.length > 0 ? "已配置" : "未配置";
  }
  if (field === "payments_enabled") return value === true ? "已启用" : "已关闭";
  if (value === null || value === undefined || value === "") return "空";
  return String(value);
}

function createReadbackSnapshot(
  owner: Owner,
  pending: UnknownWrite,
  record: AdminSystemConfig,
): ReadbackSnapshot {
  const fields = Object.entries(pending.patch).flatMap(([rawField, submitted]) => {
    if (submitted === undefined) return [];
    const field = rawField as keyof AdminSystemConfigUpdate;
    return [{
      label: READBACK_FIELD_LABELS[field],
      submitted: snapshotValue(field, submitted),
      current: snapshotValue(field, record[field as keyof AdminSystemConfig]),
    }];
  });
  return { owner, attemptId: pending.id, fields };
}

function draftFromRecord(record: AdminSystemConfig): SystemConfigDraft {
  return {
    muse_token: record.muse_token ?? "",
    site_title: record.site_title ?? "",
    site_tagline: record.site_tagline ?? "",
    storyboard_cost_multiplier: record.storyboard_cost_multiplier === null ? "" : String(record.storyboard_cost_multiplier),
    membership_benefits: record.membership_benefits,
    payments_enabled: record.payments_enabled,
    tutorial_url: record.tutorial_url,
    purchase_qr_code_url: record.purchase_qr_code_url,
    purchase_contact_info: record.purchase_contact_info,
    team_created_limit: String(record.team_created_limit),
    team_joined_limit: String(record.team_joined_limit),
    team_member_limit: String(record.team_member_limit),
    invite_code_ttl_hours: String(record.invite_code_ttl_hours),
    chapter_lock_idle_minutes: String(record.chapter_lock_idle_minutes),
    aistarslab_credit_multiplier: String(record.aistarslab_credit_multiplier),
    frontend_url: record.frontend_url,
    allowed_origins: record.allowed_origins,
    upload_dir: record.upload_dir,
    worker_max_concurrency: record.worker_max_concurrency,
  };
}

function parseIntegerDraft(value: string): number | null {
  if (!/^-?\d+$/.test(value)) return null;
  const result = Number(value);
  return Number.isSafeInteger(result) ? result : null;
}

function buildChangeSet(baseline: AdminSystemConfig, draft: SystemConfigDraft): ChangeSet {
  const patch: AdminSystemConfigUpdate = {};
  const textFields = [
    ["muse_token", baseline.muse_token],
    ["site_title", baseline.site_title],
    ["site_tagline", baseline.site_tagline],
    ["membership_benefits", baseline.membership_benefits],
    ["tutorial_url", baseline.tutorial_url],
    ["purchase_qr_code_url", baseline.purchase_qr_code_url],
    ["purchase_contact_info", baseline.purchase_contact_info],
    ["frontend_url", baseline.frontend_url],
    ["allowed_origins", baseline.allowed_origins],
    ["upload_dir", baseline.upload_dir],
    ["worker_max_concurrency", baseline.worker_max_concurrency],
  ] as const;

  for (const [field, baselineValue] of textFields) {
    const displayedBaseline = baselineValue === null ? "" : baselineValue;
    if (draft[field] !== displayedBaseline) {
      patch[field] = draft[field];
    }
  }

  const integerFields: ReadonlyArray<readonly [IntegerField, number | null, string]> = [
    ["storyboard_cost_multiplier", baseline.storyboard_cost_multiplier, "分镜费用倍率"],
    ["team_created_limit", baseline.team_created_limit, "可创建团队数"],
    ["team_joined_limit", baseline.team_joined_limit, "可加入团队数"],
    ["team_member_limit", baseline.team_member_limit, "团队成员上限"],
    ["invite_code_ttl_hours", baseline.invite_code_ttl_hours, "邀请码有效时长"],
    ["chapter_lock_idle_minutes", baseline.chapter_lock_idle_minutes, "章节锁空闲时长"],
  ];
  for (const [field, baselineValue, label] of integerFields) {
    const displayedBaseline = baselineValue === null ? "" : String(baselineValue);
    if (draft[field] === displayedBaseline) continue;
    const value = parseIntegerDraft(draft[field]);
    if (value === null) {
      return { patch: {}, error: label + "需要填写安全范围内的整数。" };
    }
    patch[field] = value;
  }

  if (draft.payments_enabled !== baseline.payments_enabled) {
    patch.payments_enabled = draft.payments_enabled;
  }

  if (draft.aistarslab_credit_multiplier !== String(baseline.aistarslab_credit_multiplier)) {
    if (draft.aistarslab_credit_multiplier.trim() === "") {
      return { patch: {}, error: "AIStarsLab 积分倍率需要填写有限数字。" };
    }
    const value = Number(draft.aistarslab_credit_multiplier);
    if (!Number.isFinite(value)) {
      return { patch: {}, error: "AIStarsLab 积分倍率需要填写有限数字。" };
    }
    patch.aistarslab_credit_multiplier = value;
  }

  return { patch, error: null };
}

function hasChanges(changeSet: ChangeSet): boolean {
  return changeSet.error !== null || Object.keys(changeSet.patch).length > 0;
}

function exitMessage(kind: PendingAdminSystemConfigExit["kind"]): string {
  if (kind === "write-in-flight") return "系统配置保存请求已发出，离开不会撤销服务端操作。";
  if (kind === "unknown-write") return "系统配置保存结果待核对。";
  return "系统配置尚有未保存的修改。";
}

function isKnownRejection(error: unknown): error is ApiError {
  return error instanceof ApiError && error.kind === "http"
    && [401, 403, 404, 422].includes(error.status ?? -1);
}

interface SystemConfigFormProps extends Omit<AdminSystemConfigProps, "mode" | "onEdit"> {
  owner: Owner;
  configId: string;
}

function ConfigField({
  label,
  value,
  onChange,
  disabled,
  type = "text",
  inputMode,
  step,
}: {
  label: string;
  value: string;
  onChange(value: string): void;
  disabled: boolean;
  type?: string;
  inputMode?: "text" | "numeric" | "decimal" | "url";
  step?: string;
}) {
  return (
    <label className="admin-system-config-field">
      <span>{label}</span>
      <input
        type={type}
        inputMode={inputMode}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function ConfigTextArea({
  label,
  value,
  onChange,
  disabled,
  rows = 3,
}: {
  label: string;
  value: string;
  onChange(value: string): void;
  disabled: boolean;
  rows?: number;
}) {
  return (
    <label className="admin-system-config-field admin-system-config-field-wide">
      <span>{label}</span>
      <textarea rows={rows} value={value} disabled={disabled}
        onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function SystemConfigForm({
  api,
  owner,
  configId,
  ownerScope,
  viewKey,
  administratorId,
  onAuthenticationError,
  onCancel,
  onSaved,
  onExitStateChange,
}: SystemConfigFormProps) {
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const authCallback = useRef(onAuthenticationError);
  authCallback.current = onAuthenticationError;
  const cancelCallback = useRef(onCancel);
  cancelCallback.current = onCancel;
  const savedCallback = useRef(onSaved);
  savedCallback.current = onSaved;
  const exitCallback = useRef(onExitStateChange);
  exitCallback.current = onExitStateChange;

  const [baseline, setBaseline] = useState<AdminSystemConfig | null>(null);
  const baselineRef = useRef<AdminSystemConfig | null>(null);
  const [draft, setDraft] = useState<SystemConfigDraft | null>(null);
  const draftRef = useRef<SystemConfigDraft | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [unknown, setUnknown] = useState<UnknownWrite | null>(null);
  const unknownRef = useRef<UnknownWrite | null>(null);
  const [readbackSnapshot, setReadbackSnapshot] = useState<ReadbackSnapshot | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkNotice, setCheckNotice] = useState<string | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const mounted = useRef(true);
  const loadSequence = useRef(0);
  const attemptSequence = useRef(0);
  const checkSequence = useRef(0);
  const dirtyIntent = useRef(0);
  const writeAttempt = useRef<WriteAttempt | null>(null);
  const checkController = useRef<AbortController | null>(null);
  const writeController = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      loadSequence.current += 1;
      checkSequence.current += 1;
      checkController.current?.abort();
      writeController.current?.abort();
      checkController.current = null;
      writeController.current = null;
    };
  }, []);

  function publishExit(pending: PendingAdminSystemConfigExit | null): void {
    exitCallback.current?.({
      ownerScope,
      viewKey,
      administratorId,
      configId,
      pending,
    });
  }

  function pendingExit(): PendingAdminSystemConfigExit | undefined {
    const unresolved = unknownRef.current;
    if (unresolved) return unresolved.pending;
    const attempt = writeAttempt.current;
    if (attempt) return attempt.pending;
    const currentBaseline = baselineRef.current;
    const currentDraft = draftRef.current;
    if (!currentBaseline || !currentDraft) return undefined;
    const changes = buildChangeSet(currentBaseline, currentDraft);
    if (!hasChanges(changes)) {
      dirtyIntent.current = 0;
      return undefined;
    }
    if (dirtyIntent.current === 0) dirtyIntent.current = ++attemptSequence.current;
    return {
      kind: "dirty-draft",
      operation: "update",
      configId,
      intentId: dirtyIntent.current,
      message: exitMessage("dirty-draft"),
    };
  }

  useEffect(() => {
    publishExit(pendingExit() ?? null);
  }, [baseline, draft, busy, receipt, unknown, ownerScope, viewKey, administratorId, configId]);

  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++loadSequence.current;
    const capturedOwner = ownerRef.current;
    let current = true;
    setLoadState("loading");
    setLoadError(null);

    void api.get(configId, controller.signal).then((record) => {
      if (!current || !mounted.current || controller.signal.aborted
        || sequence !== loadSequence.current || !sameOwner(capturedOwner, ownerRef.current)) return;
      const nextDraft = draftFromRecord(record);
      baselineRef.current = record;
      draftRef.current = nextDraft;
      setBaseline(record);
      setDraft(nextDraft);
      setReceipt(null);
      setLoadState("ready");
    }).catch((cause: unknown) => {
      if (!current || !mounted.current || controller.signal.aborted
        || sequence !== loadSequence.current || !sameOwner(capturedOwner, ownerRef.current)) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) {
        authCallback.current();
        return;
      }
      setLoadError(visibleReadError(cause));
      setLoadState("error");
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [api, configId, ownerScope, viewKey, administratorId, retry]);

  function changeDraft(field: keyof SystemConfigDraft, value: string | boolean): void {
    if (busy || unknownRef.current || !draftRef.current) return;
    const next = { ...draftRef.current, [field]: value } as SystemConfigDraft;
    draftRef.current = next;
    setDraft(next);
    setWriteError(null);
    setReceipt(null);
    setCheckNotice(null);
    publishExit(pendingExit() ?? null);
  }

  function currentChanges(): ChangeSet {
    const currentBaseline = baselineRef.current;
    const currentDraft = draftRef.current;
    return currentBaseline && currentDraft
      ? buildChangeSet(currentBaseline, currentDraft)
      : { patch: {}, error: null };
  }

  async function save(): Promise<void> {
    if (busy || unknownRef.current || writeAttempt.current) return;
    const currentBaseline = baselineRef.current;
    const currentDraft = draftRef.current;
    if (!currentBaseline || !currentDraft) return;
    const changes = buildChangeSet(currentBaseline, currentDraft);
    if (changes.error || Object.keys(changes.patch).length === 0) return;

    const id = ++attemptSequence.current;
    const capturedDraft = Object.freeze({ ...currentDraft });
    const capturedPatch = Object.freeze({ ...changes.patch });
    const pending: PendingAdminSystemConfigExit = {
      kind: "write-in-flight",
      operation: "update",
      configId,
      intentId: id,
      message: exitMessage("write-in-flight"),
    };
    const attempt: WriteAttempt = {
      id,
      owner: ownerRef.current,
      baseline: currentBaseline,
      draft: capturedDraft,
      patch: capturedPatch,
      pending,
    };
    const controller = new AbortController();
    writeAttempt.current = attempt;
    writeController.current = controller;
    setBusy(true);
    setWriteError(null);
    setReceipt(null);
    setCheckNotice(null);
    setReadbackSnapshot(null);
    publishExit(pending);

    try {
      const saved = await api.update(configId, capturedPatch, controller.signal);
      if (!mounted.current || !sameOwner(attempt.owner, ownerRef.current)
        || writeAttempt.current !== attempt) return;
      const nextDraft = draftFromRecord(saved);
      baselineRef.current = saved;
      draftRef.current = nextDraft;
      unknownRef.current = null;
      setBaseline(saved);
      setDraft(nextDraft);
      setUnknown(null);
      setReadbackSnapshot(null);
      setWriteError(null);
      setReceipt("服务端已确认更新系统配置。");
      dirtyIntent.current = 0;
      publishExit(null);
      savedCallback.current();
    } catch (cause) {
      if (!mounted.current || !sameOwner(attempt.owner, ownerRef.current)
        || writeAttempt.current !== attempt) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) {
        authCallback.current();
        return;
      }
      if (isKnownRejection(cause)) {
        setWriteError("保存请求被服务器明确拒绝（HTTP " + cause.status + "）。原草稿已保留。");
      } else {
        const unresolved: UnknownWrite = {
          id,
          draft: capturedDraft,
          baseline: currentBaseline,
          patch: capturedPatch,
          pending: {
            ...pending,
            kind: "unknown-write",
            message: exitMessage("unknown-write"),
          },
        };
        unknownRef.current = unresolved;
        setUnknown(unresolved);
        setReadbackSnapshot(null);
        setWriteError("保存结果未确认。系统不会自动重发。");
      }
    } finally {
      if (writeAttempt.current === attempt) writeAttempt.current = null;
      if (writeController.current === controller) writeController.current = null;
      if (mounted.current && sameOwner(attempt.owner, ownerRef.current)
        && !controller.signal.aborted) {
        setBusy(false);
        const unresolved = unknownRef.current;
        publishExit(unresolved?.pending ?? pendingExit() ?? null);
      }
    }
  }

  async function checkUnknown(): Promise<void> {
    const pending = unknownRef.current;
    if (!pending || checking || discarding || checkController.current) return;
    const capturedOwner = ownerRef.current;
    const sequence = ++checkSequence.current;
    const controller = new AbortController();
    checkController.current = controller;
    setChecking(true);
    setCheckNotice(null);
    setReadbackSnapshot(null);
    try {
      const current = await api.get(configId, controller.signal);
      if (!mounted.current || controller.signal.aborted || sequence !== checkSequence.current
        || !sameOwner(capturedOwner, ownerRef.current) || unknownRef.current !== pending) return;
      setReadbackSnapshot(createReadbackSnapshot(capturedOwner, pending, current));
      setCheckNotice("只读快照已读取；当前值不能归因到刚才的保存请求。");
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted || sequence !== checkSequence.current
        || !sameOwner(capturedOwner, ownerRef.current) || unknownRef.current !== pending) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else if (cause instanceof ApiError && cause.status === 404) {
        setCheckNotice("只读快照：配置当前不存在；这不能说明刚才的请求未完成。");
      } else {
        setCheckNotice("只读核对失败；原保存意图仍待处理。");
      }
    } finally {
      if (checkController.current === controller) checkController.current = null;
      if (mounted.current && !controller.signal.aborted && sequence === checkSequence.current
        && sameOwner(capturedOwner, ownerRef.current) && unknownRef.current === pending) {
        setChecking(false);
      }
    }
  }

  async function discardAndReload(): Promise<void> {
    const pending = unknownRef.current;
    if (!pending || checking || discarding || checkController.current) return;
    if (!window.confirm("确认放弃原保存意图并重新读取当前配置吗？重新保存将成为一项新操作。")) return;

    const capturedOwner = ownerRef.current;
    const sequence = ++checkSequence.current;
    const controller = new AbortController();
    checkController.current = controller;
    setDiscarding(true);
    setCheckNotice(null);
    setReadbackSnapshot(null);
    try {
      const current = await api.get(configId, controller.signal);
      if (!mounted.current || controller.signal.aborted || sequence !== checkSequence.current
        || !sameOwner(capturedOwner, ownerRef.current) || unknownRef.current !== pending) return;
      const nextDraft = draftFromRecord(current);
      baselineRef.current = current;
      draftRef.current = nextDraft;
      unknownRef.current = null;
      dirtyIntent.current = 0;
      setBaseline(current);
      setDraft(nextDraft);
      setUnknown(null);
      setReadbackSnapshot(null);
      setWriteError(null);
      setReceipt(null);
      setDiscarding(false);
      setCheckNotice("原保存意图已放弃，表单已按当前配置重新读取。");
      publishExit(null);
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted || sequence !== checkSequence.current
        || !sameOwner(capturedOwner, ownerRef.current) || unknownRef.current !== pending) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status ?? -1)) authCallback.current();
      else setCheckNotice(cause instanceof ApiError && cause.status === 404
        ? "配置当前不存在；原保存结果仍未确认。"
        : "重新读取失败；原保存意图仍待处理。");
    } finally {
      if (checkController.current === controller) checkController.current = null;
      if (mounted.current && !controller.signal.aborted && sequence === checkSequence.current
        && sameOwner(capturedOwner, ownerRef.current) && unknownRef.current === pending) {
        setDiscarding(false);
      }
    }
  }

  const changes = currentChanges();
  const isFrozen = busy || unknown !== null;
  const formReady = loadState === "ready" && baseline !== null && draft !== null;
  const canSave = formReady && !isFrozen && changes.error === null && Object.keys(changes.patch).length > 0;
  const visibleReadbackSnapshot = readbackSnapshot && unknown
    && readbackSnapshot.attemptId === unknown.id
    && sameOwner(readbackSnapshot.owner, ownerRef.current)
    ? readbackSnapshot
    : null;

  if (loadState === "loading") {
    return <section className="admin-state-card" role="status">正在读取系统配置…</section>;
  }
  if (loadState === "error") {
    return (
      <section className="admin-state-card" role="alert">
        <strong>系统配置加载失败</strong>
        <p>{loadError}</p>
        <div className="admin-form-actions">
          <button className="admin-button admin-button-secondary" type="button"
            onClick={() => setRetry((value) => value + 1)}>重试读取</button>
          <button className="admin-button admin-button-secondary" type="button"
            onClick={() => cancelCallback.current()}>返回配置列表</button>
        </div>
      </section>
    );
  }
  if (!formReady) return <section className="admin-state-card" role="status">当前没有可编辑的系统配置。</section>;

  return (
    <section className="admin-system-config-page">
      <header className="admin-page-heading">
        <div>
          <p className="admin-eyebrow">后台配置</p>
          <h1>系统配置</h1>
          <p>维护当前系统配置；空文本、关闭状态和负数按服务现有规则保存。</p>
        </div>
        <button className="admin-button admin-button-secondary" type="button"
          onClick={() => cancelCallback.current(pendingExit())}>返回配置列表</button>
      </header>

      {receipt ? <p className="admin-system-config-notice" role="status">{receipt}</p> : null}
      {writeError ? <p className="admin-system-config-error" role="alert">{writeError}</p> : null}
      {changes.error ? <p className="admin-system-config-error" role="alert">{changes.error}</p> : null}

      {unknown ? (
        <section className="admin-system-config-unknown" aria-label="系统配置写入结果待核对">
          <strong>保存结果待核对</strong>
          <p>服务没有确认这次保存。请只读核对当前快照，或明确放弃原意图后重新读取；不会自动重发。</p>
          <div className="admin-form-actions">
            <button className="admin-button admin-button-secondary" type="button"
              onClick={() => { void checkUnknown(); }} disabled={checking || discarding}>
              {checking ? "核对中…" : "只读核对当前配置"}
            </button>
            <button className="admin-button admin-button-secondary" type="button"
              onClick={() => { void discardAndReload(); }} disabled={checking || discarding}>
              {discarding ? "重新读取中…" : "明确放弃并重新读取"}
            </button>
          </div>
          {checkNotice ? <p role="status">{checkNotice}</p> : null}
        </section>
      ) : null}

      {visibleReadbackSnapshot ? (
        <section className="admin-system-config-readback" aria-label="系统配置只读快照">
          <strong>当前只读快照</strong>
          <p>以下是本次核对读取到的值；它不代表原保存请求已完成，也不会替换冻结草稿。</p>
          <dl>
            {visibleReadbackSnapshot.fields.map((field) => (
              <div key={field.label}>
                <dt>{field.label}</dt>
                <dd><span>冻结提交稿：{field.submitted}</span><span>当前读取：{field.current}</span></dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      <form className="admin-system-config-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <section className="admin-system-config-section" aria-labelledby="config-general-heading">
          <div className="admin-system-config-section-heading">
            <h2 id="config-general-heading">网站与购买说明</h2>
            <p>仅编辑配置文本和开关；此页不会加载远端二维码或执行购买。</p>
          </div>
          <ConfigField label="Muse 令牌" type="password" value={draft.muse_token}
            disabled={isFrozen} onChange={(value) => changeDraft("muse_token", value)} />
          <ConfigField label="网站标题" value={draft.site_title}
            disabled={isFrozen} onChange={(value) => changeDraft("site_title", value)} />
          <ConfigField label="网站标语" value={draft.site_tagline}
            disabled={isFrozen} onChange={(value) => changeDraft("site_tagline", value)} />
          <ConfigField label="分镜费用倍率" type="number" step="1" inputMode="numeric" value={draft.storyboard_cost_multiplier}
            disabled={isFrozen} onChange={(value) => changeDraft("storyboard_cost_multiplier", value)} />
          <ConfigTextArea label="会员权益说明" value={draft.membership_benefits}
            disabled={isFrozen} onChange={(value) => changeDraft("membership_benefits", value)} rows={4} />
          <label className="admin-system-config-toggle">
            <input type="checkbox" checked={draft.payments_enabled} disabled={isFrozen}
              onChange={(event) => changeDraft("payments_enabled", event.target.checked)} />
            <span>启用在线支付</span>
          </label>
          <ConfigField label="教程链接" value={draft.tutorial_url}
            disabled={isFrozen} onChange={(value) => changeDraft("tutorial_url", value)} />
          <ConfigField label="购买二维码地址" value={draft.purchase_qr_code_url}
            disabled={isFrozen} onChange={(value) => changeDraft("purchase_qr_code_url", value)} />
          <ConfigTextArea label="购买联系说明" value={draft.purchase_contact_info}
            disabled={isFrozen} onChange={(value) => changeDraft("purchase_contact_info", value)} />
        </section>

        <section className="admin-system-config-section" aria-labelledby="config-limits-heading">
          <div className="admin-system-config-section-heading">
            <h2 id="config-limits-heading">团队与锁定限制</h2>
            <p>保留服务允许的 0 和负数，不自动套用正数下限。</p>
          </div>
          <ConfigField label="可创建团队数" type="number" step="1" inputMode="numeric"
            value={draft.team_created_limit} disabled={isFrozen}
            onChange={(value) => changeDraft("team_created_limit", value)} />
          <ConfigField label="可加入团队数" type="number" step="1" inputMode="numeric"
            value={draft.team_joined_limit} disabled={isFrozen}
            onChange={(value) => changeDraft("team_joined_limit", value)} />
          <ConfigField label="团队成员上限" type="number" step="1" inputMode="numeric"
            value={draft.team_member_limit} disabled={isFrozen}
            onChange={(value) => changeDraft("team_member_limit", value)} />
          <ConfigField label="邀请码有效时长（小时）" type="number" step="1" inputMode="numeric"
            value={draft.invite_code_ttl_hours} disabled={isFrozen}
            onChange={(value) => changeDraft("invite_code_ttl_hours", value)} />
          <ConfigField label="章节锁空闲时长（分钟）" type="number" step="1" inputMode="numeric"
            value={draft.chapter_lock_idle_minutes} disabled={isFrozen}
            onChange={(value) => changeDraft("chapter_lock_idle_minutes", value)} />
          <ConfigField label="AIStarsLab 积分倍率" type="number" step="any" inputMode="decimal"
            value={draft.aistarslab_credit_multiplier} disabled={isFrozen}
            onChange={(value) => changeDraft("aistarslab_credit_multiplier", value)} />
        </section>

        <section className="admin-system-config-section" aria-labelledby="config-deployment-heading">
          <div className="admin-system-config-section-heading">
            <h2 id="config-deployment-heading">部署与服务配置</h2>
            <p>保存只更新配置记录；各消费者的读取和生效方式仍按当前服务实现。</p>
          </div>
          <ConfigField label="前端地址" value={draft.frontend_url} disabled={isFrozen}
            onChange={(value) => changeDraft("frontend_url", value)} />
          <p className="admin-system-config-field-note">认证链接使用显式 runtime 配置；保存此项不代表现有链接立即变化。</p>
          <ConfigTextArea label="允许的来源地址" value={draft.allowed_origins} disabled={isFrozen}
            onChange={(value) => changeDraft("allowed_origins", value)} rows={2} />
          <p className="admin-system-config-field-note">当前 CORS 使用显式配置；保存此项不代表来源规则已热更新。</p>
          <ConfigField label="上传目录（文件读取时使用）" value={draft.upload_dir} disabled={isFrozen}
            onChange={(value) => changeDraft("upload_dir", value)} />
          <p className="admin-system-config-field-note">文件读取路径会动态查询此值；保存不代表所有上传流程切换目录。</p>
          <ConfigField label="Worker 最大并发" value={draft.worker_max_concurrency} disabled={isFrozen}
            onChange={(value) => changeDraft("worker_max_concurrency", value)} />
          <p className="admin-system-config-field-note">当前未发现此字段接入 Worker 启动并发；保存只更新配置值。</p>
        </section>

        <footer className="admin-form-actions admin-system-config-actions">
          <button className="admin-button admin-button-secondary" type="button"
            onClick={() => cancelCallback.current(pendingExit())}>返回配置列表</button>
          <button className="admin-button admin-button-primary" type="submit" disabled={!canSave}>
            {busy ? "保存中…" : "保存配置"}
          </button>
        </footer>
      </form>
    </section>
  );
}

const API_IDS = new WeakMap<object, number>();
let nextApiId = 1;

function apiIdentity(api: AdminSystemConfigApi): number {
  let id = API_IDS.get(api);
  if (id === undefined) {
    id = nextApiId;
    nextApiId += 1;
    API_IDS.set(api, id);
  }
  return id;
}

export function AdminSystemConfig(props: AdminSystemConfigProps) {
  const configId = props.mode === "edit" ? props.configId ?? "" : "";
  const owner: Owner = {
    api: props.api,
    scope: props.ownerScope,
    view: props.viewKey,
    administratorId: props.administratorId,
    configId,
    mode: props.mode,
  };
  const key = JSON.stringify([
    apiIdentity(props.api),
    props.ownerScope,
    props.viewKey,
    props.administratorId,
    props.mode,
    configId,
  ]);

  if (props.mode === "list") {
    return (
      <SystemConfigList
        key={key}
        api={props.api}
        owner={owner}
        listVersion={props.listVersion}
        onAuthenticationError={props.onAuthenticationError}
        onEdit={props.onEdit}
      />
    );
  }
  if (!configId) {
    return <section className="admin-state-card" role="alert">缺少系统配置编号，无法编辑。</section>;
  }
  return <SystemConfigForm key={key} {...props} owner={owner} configId={configId} />;
}
