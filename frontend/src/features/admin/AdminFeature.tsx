import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import { resolveApiBaseUrl } from "../../shared/api/config";
import { AdminDashboard } from "./AdminDashboard";
import { CreditLogs } from "./CreditLogs";
import { createAdminCreditsApi } from "./adminCreditsApi";
import { createAdminApi, type AdminFetcher } from "./adminApi";
import type { AdminApi, AdminStorage, AdminUser } from "./adminTypes";
import { SystemPrompts } from "./SystemPrompts";
import { createSystemPromptsApi } from "./systemPromptsApi";
import { Users } from "./Users";
import { UserCreditEditor, UserCreditsList } from "./UserCredits";
import { createAdminUsersApi } from "./usersApi";
import { SystemModels } from "./SystemModels";
import { RedemptionCodes } from "../admin-membership/RedemptionCodes";
import { PurchasePackages } from "../admin-membership/PurchasePackages";
import { AdminSystemConfig } from "../admin-system-config/AdminSystemConfig";
import { createAdminSystemConfigApi } from "../admin-system-config/adminSystemConfigApi";
import type { AdminSystemConfigApi, AdminSystemConfigExitSnapshot, PendingAdminSystemConfigExit } from "../admin-system-config/adminSystemConfigTypes";
import { createAdminMembershipApi } from "../admin-membership/adminMembershipApi";
import type { AdminMembershipApi, AdminMembershipExitSnapshot } from "../admin-membership/adminMembershipTypes";
import { AdminSeries } from "../admin-series/AdminSeries";
import { createAdminSeriesApi } from "../admin-series/adminSeriesApi";
import { DEFAULT_ADMIN_SERIES_LIST_STATE } from "../admin-series/adminSeriesTypes";
import type { AdminSeriesApi, AdminSeriesExitSnapshot, AdminSeriesListState, AdminSeriesRecord, PendingAdminSeriesExit } from "../admin-series/adminSeriesTypes";
import { AdminTasks } from "../admin-tasks/AdminTasks";
import { createAdminTasksApi } from "../admin-tasks/adminTasksApi";
import { DEFAULT_ADMIN_TASK_LIST_STATE, type AdminTaskListState, type AdminTasksApi } from "../admin-tasks/adminTasksTypes";
import { AdminSmtpAccounts } from "../admin-smtp-accounts/AdminSmtpAccounts";
import { createAdminSmtpAccountsApi } from "../admin-smtp-accounts/adminSmtpAccountsApi";
import type { AdminSmtpAccountExitSnapshot, AdminSmtpAccountMode, AdminSmtpAccountsApi, PendingAdminSmtpAccountExit } from "../admin-smtp-accounts/adminSmtpAccountsTypes";
import { createAistarslabApi } from "./aistarslabApi";
import type { AistarslabQuickConfigExitNotice } from "./AistarslabQuickConfig";
import type { AistarslabApi } from "./aistarslabApi";
import "./Admin.css";

const ADMIN_TOKEN_KEY = "admin_token";
const ADMIN_USER_KEY = "admin_user";

const defaultFetcher: AdminFetcher = (input, init) => fetch(input, init);

const browserStorage: AdminStorage = {
  getItem: (key) => window.localStorage.getItem(key),
  setItem: (key, value) => window.localStorage.setItem(key, value),
  removeItem: (key) => window.localStorage.removeItem(key),
};

type AdminView =
  | { kind: "dashboard" }
  | { kind: "system-models" }
  | { kind: "model-create" }
  | { kind: "model-edit"; id: string }
  | { kind: "system-prompts" }
  | { kind: "prompt-create" }
  | { kind: "prompt-edit"; id: string }
  | { kind: "users" }
  | { kind: "user-create" }
  | { kind: "user-edit"; id: string }
  | { kind: "user-credits" }
  | { kind: "credit-edit"; id: string }
  | { kind: "credit-logs" }
  | { kind: "redemption-codes" }
  | { kind: "redemption-create" }
  | { kind: "purchase-packages" }
  | { kind: "purchase-package-create" }
  | { kind: "purchase-package-edit"; id: string }
  | { kind: "system-config" }
  | { kind: "system-config-edit"; id: string }
  | { kind: "series" }
  | { kind: "series-create" }
  | { kind: "series-edit"; id: string }
  | { kind: "admin-tasks" }
  | { kind: "smtp-accounts" }
  | { kind: "smtp-account-create" }
  | { kind: "smtp-account-edit"; id: string };

interface AdminSession {
  token: string;
  user: AdminUser;
}

interface ActiveMembershipExit {
  snapshot: AdminMembershipExitSnapshot;
  pending: NonNullable<AdminMembershipExitSnapshot["pending"]>;
  viewIdentity: number;
  sessionGeneration: number;
  api: AdminMembershipApi;
}

interface ActiveSystemConfigExit {
  snapshot: AdminSystemConfigExitSnapshot;
  pending: NonNullable<AdminSystemConfigExitSnapshot["pending"]>;
  viewIdentity: number;
  sessionGeneration: number;
  api: AdminSystemConfigApi;
}

interface ActiveSeriesExit {
  snapshot: AdminSeriesExitSnapshot;
  pending: NonNullable<AdminSeriesExitSnapshot["pending"]>;
  viewIdentity: number;
  sessionGeneration: number;
  api: AdminSeriesApi;
}

interface ActiveSmtpAccountExit {
  snapshot: AdminSmtpAccountExitSnapshot;
  pending: NonNullable<AdminSmtpAccountExitSnapshot["pending"]>;
  viewIdentity: number;
  sessionGeneration: number;
  api: AdminSmtpAccountsApi;
}

interface ActiveAistarslabQuickConfigExit {
  owner: symbol;
  notice: AistarslabQuickConfigExitNotice;
  viewIdentity: number;
  sessionGeneration: number;
  api: AistarslabApi;
}

interface AdminFeatureProps {
  apiBaseUrl: string;
  fetcher?: AdminFetcher;
  storage?: AdminStorage;
}

function isAdminUser(value: unknown): value is AdminUser {
  if (typeof value !== "object" || value === null) return false;
  const user = value as Partial<AdminUser>;
  return typeof user.id === "string"
    && typeof user.username === "string"
    && user.is_superuser === true;
}

function clearAdminSession(storage: AdminStorage): void {
  storage.removeItem(ADMIN_TOKEN_KEY);
  storage.removeItem(ADMIN_USER_KEY);
}

function restoreAdminSession(storage: AdminStorage): AdminSession | null {
  const token = storage.getItem(ADMIN_TOKEN_KEY);
  const rawUser = storage.getItem(ADMIN_USER_KEY);
  if (!token || !rawUser) {
    if (token || rawUser) clearAdminSession(storage);
    return null;
  }

  try {
    const user: unknown = JSON.parse(rawUser);
    if (isAdminUser(user)) return { token, user };
  } catch {
    // A malformed administrator identity must not unlock the backend UI.
  }
  clearAdminSession(storage);
  return null;
}

function loginError(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.message;
  return "登录失败，请稍后重试。";
}

function AdminLogin({ api, storage, onLogin }: {
  api: AdminApi;
  storage: AdminStorage;
  onLogin(session: AdminSession): void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.login(username, password);
      const user = result.user as AdminUser;
      storage.setItem(ADMIN_TOKEN_KEY, result.access_token);
      storage.setItem(ADMIN_USER_KEY, JSON.stringify(result.user));
      onLogin({ token: result.access_token, user });
    } catch (cause) {
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) {
        clearAdminSession(storage);
      }
      setError(loginError(cause));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="admin-login-screen">
      <section className="admin-login-card">
        <div className="admin-brand-mark" aria-hidden="true">H</div>
        <p className="admin-eyebrow">Hao AI</p>
        <h1>管理后台</h1>
        <p className="admin-login-description">使用管理员账号登录平台管理后台。</p>
        <form className="admin-form admin-login-form" onSubmit={(event) => { void submit(event); }}>
          <label htmlFor="admin-login-username">用户名</label>
          <input
            id="admin-login-username"
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="请输入用户名"
          />
          <label htmlFor="admin-login-password">密码</label>
          <input
            id="admin-login-password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="请输入密码"
          />
          {error ? <p className="admin-form-error" role="alert">{error}</p> : null}
          <button className="admin-button admin-button-primary" type="submit" disabled={submitting || !username || !password}>
            {submitting ? "登录中…" : "登录"}
          </button>
        </form>
        <p className="admin-login-footnote">Hao AI · 剧本创作工具</p>
      </section>
      <aside className="admin-login-aside" aria-label="平台介绍">
        <span className="admin-aside-orbit admin-aside-orbit-one" aria-hidden="true" />
        <span className="admin-aside-orbit admin-aside-orbit-two" aria-hidden="true" />
        <p className="admin-eyebrow">运营与配置</p>
        <h2>管理模型与平台运行数据</h2>
        <p>在统一的管理空间中查看运营数据，并维护系统 AI 模型配置。</p>
      </aside>
    </main>
  );
}

export function AdminFeature({
  apiBaseUrl,
  fetcher = defaultFetcher,
  storage = browserStorage,
}: AdminFeatureProps) {
  const safeApiBaseUrl = useMemo(
    () => resolveApiBaseUrl(apiBaseUrl, window.location.href),
    [apiBaseUrl],
  );
  const [session, setSession] = useState<AdminSession | null>(() => restoreAdminSession(storage));
  const [view, setView] = useState<AdminView>({ kind: "dashboard" });
  const viewIdentity = useRef(0);
  const sessionGeneration = useRef(0);
  const membershipExit = useRef<ActiveMembershipExit | null>(null);
  const membershipApiRef = useRef<AdminMembershipApi | null>(null);
  const systemConfigExit = useRef<ActiveSystemConfigExit | null>(null);
  const systemConfigApiRef = useRef<AdminSystemConfigApi | null>(null);
  const seriesExit = useRef<ActiveSeriesExit | null>(null);
  const seriesApiRef = useRef<AdminSeriesApi | null>(null);
  const tasksApiRef = useRef<AdminTasksApi | null>(null);
  const smtpAccountsExit = useRef<ActiveSmtpAccountExit | null>(null);
  const smtpAccountsApiRef = useRef<AdminSmtpAccountsApi | null>(null);
  const aistarslabExit = useRef<ActiveAistarslabQuickConfigExit | null>(null);
  const aistarslabApiRef = useRef<AistarslabApi | null>(null);
  const [seriesListState, setSeriesListState] = useState<AdminSeriesListState>(DEFAULT_ADMIN_SERIES_LIST_STATE);
  const [taskListState, setTaskListState] = useState<AdminTaskListState>(DEFAULT_ADMIN_TASK_LIST_STATE);
  const [seriesListVersion, setSeriesListVersion] = useState(0);
  const [seriesNotice, setSeriesNotice] = useState<string | null>(null);
  const [redemptionListVersion, setRedemptionListVersion] = useState(0);
  const [packageListVersion, setPackageListVersion] = useState(0);
  const [modelListVersion, setModelListVersion] = useState(0);
  const [promptListVersion, setPromptListVersion] = useState(0);
  const [userListVersion, setUserListVersion] = useState(0);
  const [creditListVersion, setCreditListVersion] = useState(0);
  const [systemConfigListVersion, setSystemConfigListVersion] = useState(0);
  const [smtpAccountsListVersion, setSmtpAccountsListVersion] = useState(0);

  const navigate = useCallback((nextView: AdminView, options?: { force?: boolean }) => {
    const pending = membershipExit.current;
    if (!options?.force && pending && pending.viewIdentity === viewIdentity.current
      && pending.sessionGeneration === sessionGeneration.current && pending.api === membershipApiRef.current) {
      const { kind, operation, recordId, message } = pending.pending;
      const action = operation === "delete" ? "删除" : operation === "update" ? "更新" : "创建";
      const target = recordId ? "记录 " + recordId : "当前表单";
      const question = kind === "write-in-flight"
        ? "正在" + action + target + "，请求已发出。离开会停止本地跟踪，但不能撤销服务端操作。仍要离开吗？"
        : kind === "unknown-write"
          ? message + "\n\n离开将放弃此意图的本地核对；之后重新提交会成为一项新操作。仍要离开吗？"
          : message + "\n\n离开会放弃当前草稿。仍要离开吗？";
      if (!window.confirm(question)) return false;
    }
    const configPending = systemConfigExit.current;
    if (!options?.force && configPending && configPending.viewIdentity === viewIdentity.current
      && configPending.sessionGeneration === sessionGeneration.current
      && configPending.api === systemConfigApiRef.current) {
      const { kind, message } = configPending.pending;
      const question = kind === "write-in-flight"
        ? "系统配置保存请求已发出。离开会停止本地跟踪，但不能撤销服务端操作。仍要离开吗？"
        : kind === "unknown-write"
          ? message + "\n\n离开将放弃此意图的本地核对；之后重新提交会成为一项新操作。仍要离开吗？"
          : message + "\n\n离开会放弃当前草稿。仍要离开吗？";
      if (!window.confirm(question)) return false;
    }
    const seriesPending = seriesExit.current;
    if (!options?.force && seriesPending && seriesPending.viewIdentity === viewIdentity.current
      && seriesPending.sessionGeneration === sessionGeneration.current && seriesPending.api === seriesApiRef.current) {
      const { kind, operation, recordId, message } = seriesPending.pending;
      const action = operation === "update" ? "更新" : "创建";
      const target = recordId ? "记录 " + recordId : "当前表单";
      const question = kind === "write-in-flight"
        ? "正在" + action + target + "，请求已发出。离开会停止本地跟踪，但不能撤销服务端操作。仍要离开吗？"
        : kind === "unknown-write"
          ? message + "\n\n离开将放弃此意图的本地核对；之后重新提交会成为一项新操作。仍要离开吗？"
          : message + "\n\n离开会放弃当前草稿。仍要离开吗？";
      if (!window.confirm(question)) return false;
    }
    const smtpPending = smtpAccountsExit.current;
    if (!options?.force && smtpPending && smtpPending.viewIdentity === viewIdentity.current
      && smtpPending.sessionGeneration === sessionGeneration.current
      && smtpPending.api === smtpAccountsApiRef.current) {
      const { kind, operation, recordId, message } = smtpPending.pending;
      const action = operation === "delete" ? "删除" : operation === "update" ? "更新" : "创建";
      const target = recordId ? "账号 " + recordId : "当前表单";
      const question = kind === "write-in-flight"
        ? "正在" + action + target + "，请求已发出。离开会停止本地跟踪，但不能撤销服务端操作。仍要离开吗？"
        : kind === "unknown-write"
          ? message + "\n\n离开将放弃此意图的本地核对；之后重新提交会成为一项新操作。仍要离开吗？"
          : message + "\n\n离开会放弃当前草稿。仍要离开吗？";
      if (!window.confirm(question)) return false;
    }
    const aistarslabPending = aistarslabExit.current;
    if (!options?.force && aistarslabPending
      && aistarslabPending.viewIdentity === viewIdentity.current
      && aistarslabPending.sessionGeneration === sessionGeneration.current
      && aistarslabPending.api === aistarslabApiRef.current) {
      const question = aistarslabPending.notice.kind === "write-in-flight"
        ? "AIStarsLab 系统模型创建请求已发出。离开会停止本地跟踪，但不能撤销服务端操作。仍要离开吗？"
        : aistarslabPending.notice.kind === "created-needs-readback"
          ? "AIStarsLab 系统模型已创建，但详情尚未核对。离开会停止本地核对，不会撤销创建。仍要离开吗？"
          : "AIStarsLab 系统模型创建结果未知；服务端可能已经新增了共享模型。离开会停止本地跟踪，不会自动重试或按名称认领。仍要离开吗？";
      if (!window.confirm(question)) return false;
    }
    membershipExit.current = null;
    systemConfigExit.current = null;
    seriesExit.current = null;
    smtpAccountsExit.current = null;
    aistarslabExit.current = null;
    viewIdentity.current += 1;
    setView(nextView);
    return true;
  }, []);

  const api = useMemo(
    () => createAdminApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  const aistarslabApi = useMemo(
    () => createAistarslabApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  aistarslabApiRef.current = aistarslabApi;
  const promptsApi = useMemo(
    () => createSystemPromptsApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  const usersApi = useMemo(
    () => createAdminUsersApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  const creditsApi = useMemo(
    () => createAdminCreditsApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  const membershipApi = useMemo(
    () => createAdminMembershipApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  const systemConfigApi = useMemo(
    () => createAdminSystemConfigApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  const seriesApi = useMemo(
    () => createAdminSeriesApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), () => session?.user.id ?? "", safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, session?.user.id, storage],
  );
  const tasksApi = useMemo(
    () => createAdminTasksApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  const smtpAccountsApi = useMemo(
    () => createAdminSmtpAccountsApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), () => session?.user.id ?? "", safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, session?.user.id, storage],
  );
  membershipApiRef.current = membershipApi;
  systemConfigApiRef.current = systemConfigApi;
  seriesApiRef.current = seriesApi;
  tasksApiRef.current = tasksApi;
  smtpAccountsApiRef.current = smtpAccountsApi;

  const handleLogin = useCallback((nextSession: AdminSession) => {
    sessionGeneration.current += 1;
    membershipExit.current = null;
    systemConfigExit.current = null;
    seriesExit.current = null;
    smtpAccountsExit.current = null;
    aistarslabExit.current = null;
    setSeriesListState(DEFAULT_ADMIN_SERIES_LIST_STATE);
    setTaskListState(DEFAULT_ADMIN_TASK_LIST_STATE);
    setSeriesNotice(null);
    setSeriesListVersion(0);
    setSession(nextSession);
    navigate({ kind: "dashboard" }, { force: true });
  }, [navigate]);

  const handleUnauthorized = useCallback(() => {
    sessionGeneration.current += 1;
    membershipExit.current = null;
    systemConfigExit.current = null;
    seriesExit.current = null;
    smtpAccountsExit.current = null;
    aistarslabExit.current = null;
    setSeriesListState(DEFAULT_ADMIN_SERIES_LIST_STATE);
    setTaskListState(DEFAULT_ADMIN_TASK_LIST_STATE);
    setSeriesNotice(null);
    setSeriesListVersion(0);
    clearAdminSession(storage);
    setSession(null);
    navigate({ kind: "dashboard" }, { force: true });
  }, [navigate, storage]);

  const handleLogout = useCallback(() => {
    if (!navigate({ kind: "dashboard" })) return;
    sessionGeneration.current += 1;
    membershipExit.current = null;
    systemConfigExit.current = null;
    seriesExit.current = null;
    smtpAccountsExit.current = null;
    aistarslabExit.current = null;
    setSeriesListState(DEFAULT_ADMIN_SERIES_LIST_STATE);
    setTaskListState(DEFAULT_ADMIN_TASK_LIST_STATE);
    setSeriesNotice(null);
    setSeriesListVersion(0);
    clearAdminSession(storage);
    setSession(null);
  }, [navigate, storage]);

  const handleModelsSaved = useCallback(() => {
    navigate({ kind: "system-models" });
    setModelListVersion((value) => value + 1);
  }, [navigate]);

  const activeViewIdentity = viewIdentity.current;
  const activeSessionGeneration = sessionGeneration.current;
  const activeMembershipApi = membershipApi;
  const activeSystemConfigApi = systemConfigApi;
  const activeAistarslabApi = aistarslabApi;
  const activeAdministratorId = session?.user.id ?? "";
  const handleAistarslabExitStateChange = useCallback((owner: symbol, notice: AistarslabQuickConfigExitNotice | null) => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || aistarslabApiRef.current !== activeAistarslabApi
      || (view.kind !== "model-create" && view.kind !== "model-edit")) return;
    if (notice) {
      aistarslabExit.current = {
        owner,
        notice,
        viewIdentity: activeViewIdentity,
        sessionGeneration: activeSessionGeneration,
        api: activeAistarslabApi,
      };
    } else if (aistarslabExit.current?.owner === owner) {
      aistarslabExit.current = null;
    }
  }, [activeAistarslabApi, activeSessionGeneration, activeViewIdentity, view.kind]);
  const membershipOwnerScope = "session-" + activeSessionGeneration + ":admin-" + activeAdministratorId;
  const membershipViewKey = view.kind === "redemption-codes" ? "redemption/list"
    : view.kind === "redemption-create" ? "redemption/create"
      : view.kind === "purchase-packages" ? "packages/list"
        : view.kind === "purchase-package-create" ? "packages/create"
          : view.kind === "purchase-package-edit" ? "packages/edit/" + view.id : "";
  const systemConfigOwnerScope = "session-" + activeSessionGeneration + ":admin-" + activeAdministratorId;
  const systemConfigViewKey = view.kind === "system-config" ? "system-config/list"
    : view.kind === "system-config-edit" ? "system-config/edit/" + view.id : "";
  const seriesOwnerScope = "session-" + activeSessionGeneration + ":admin-" + activeAdministratorId;
  const seriesViewKey = view.kind === "series" ? "series/list"
    : view.kind === "series-create" ? "series/create"
      : view.kind === "series-edit" ? "series/edit/" + view.id : "";
  const tasksOwnerScope = "session-" + activeSessionGeneration + ":admin-" + activeAdministratorId;
  const tasksViewKey = view.kind === "admin-tasks" ? "admin-tasks/list" : "";
  const smtpAccountsOwnerScope = "session-" + activeSessionGeneration + ":admin-" + activeAdministratorId;
  const smtpAccountsMode: AdminSmtpAccountMode = view.kind === "smtp-account-create" ? "create"
    : view.kind === "smtp-account-edit" ? "edit" : "list";
  const smtpAccountsRecordId = view.kind === "smtp-account-edit" ? view.id : "";
  const smtpAccountsViewKey = view.kind === "smtp-accounts" ? "smtp-accounts/list"
    : view.kind === "smtp-account-create" ? "smtp-accounts/create"
      : view.kind === "smtp-account-edit" ? "smtp-accounts/edit/" + view.id : "";
  const handleMembershipExitState = useCallback((snapshot: AdminMembershipExitSnapshot) => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || membershipApiRef.current !== activeMembershipApi || snapshot.ownerScope !== membershipOwnerScope
      || snapshot.viewKey !== membershipViewKey || snapshot.administratorId !== activeAdministratorId) return;
    if (snapshot.pending) {
      membershipExit.current = {
        snapshot, pending: snapshot.pending, viewIdentity: activeViewIdentity,
        sessionGeneration: activeSessionGeneration, api: activeMembershipApi,
      };
    } else if (membershipExit.current?.viewIdentity === activeViewIdentity
      && membershipExit.current.sessionGeneration === activeSessionGeneration
      && membershipExit.current.api === activeMembershipApi) {
      membershipExit.current = null;
    }
  }, [activeAdministratorId, activeMembershipApi, activeSessionGeneration, activeViewIdentity, membershipOwnerScope, membershipViewKey]);
  const leaveMembership = useCallback((nextView: AdminView, pending?: AdminMembershipExitSnapshot["pending"]) => {
    if (pending && !membershipExit.current) {
      const snapshot = { ownerScope: membershipOwnerScope, viewKey: membershipViewKey, administratorId: activeAdministratorId, pending };
      membershipExit.current = {
        snapshot, pending, viewIdentity: activeViewIdentity,
        sessionGeneration: activeSessionGeneration, api: activeMembershipApi,
      };
    }
    navigate(nextView);
  }, [activeAdministratorId, activeMembershipApi, activeSessionGeneration, activeViewIdentity, membershipOwnerScope, membershipViewKey, navigate]);
  const handleSystemConfigExitState = useCallback((snapshot: AdminSystemConfigExitSnapshot) => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || systemConfigApiRef.current !== activeSystemConfigApi || snapshot.ownerScope !== systemConfigOwnerScope
      || snapshot.viewKey !== systemConfigViewKey || snapshot.administratorId !== activeAdministratorId) return;
    if (snapshot.pending) {
      systemConfigExit.current = {
        snapshot,
        pending: snapshot.pending,
        viewIdentity: activeViewIdentity,
        sessionGeneration: activeSessionGeneration,
        api: activeSystemConfigApi,
      };
    } else if (systemConfigExit.current?.viewIdentity === activeViewIdentity
      && systemConfigExit.current.sessionGeneration === activeSessionGeneration
      && systemConfigExit.current.api === activeSystemConfigApi) {
      systemConfigExit.current = null;
    }
  }, [activeAdministratorId, activeSessionGeneration, activeSystemConfigApi, activeViewIdentity, systemConfigOwnerScope, systemConfigViewKey]);
  const systemConfigRecordId = view.kind === "system-config-edit" ? view.id : "";
  const leaveSystemConfig = useCallback((nextView: AdminView, pending?: PendingAdminSystemConfigExit) => {
    if (pending && pending.configId === systemConfigRecordId) {
      const snapshot: AdminSystemConfigExitSnapshot = {
        ownerScope: systemConfigOwnerScope,
        viewKey: systemConfigViewKey,
        administratorId: activeAdministratorId,
        configId: pending.configId,
        pending,
      };
      systemConfigExit.current = {
        snapshot,
        pending,
        viewIdentity: activeViewIdentity,
        sessionGeneration: activeSessionGeneration,
        api: activeSystemConfigApi,
      };
    }
    navigate(nextView);
  }, [activeAdministratorId, activeSessionGeneration, activeSystemConfigApi, activeViewIdentity, systemConfigOwnerScope, systemConfigRecordId, systemConfigViewKey, navigate]);
  const handleSeriesExitState = useCallback((snapshot: AdminSeriesExitSnapshot) => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || seriesApiRef.current !== seriesApi || snapshot.ownerScope !== seriesOwnerScope
      || snapshot.viewKey !== seriesViewKey || snapshot.administratorId !== activeAdministratorId) return;
    if (snapshot.pending) {
      seriesExit.current = {
        snapshot, pending: snapshot.pending, viewIdentity: activeViewIdentity,
        sessionGeneration: activeSessionGeneration, api: seriesApi,
      };
    } else if (seriesExit.current?.viewIdentity === activeViewIdentity
      && seriesExit.current.sessionGeneration === activeSessionGeneration && seriesExit.current.api === seriesApi) {
      seriesExit.current = null;
    }
  }, [activeAdministratorId, activeSessionGeneration, activeViewIdentity, seriesApi, seriesOwnerScope, seriesViewKey]);
  const leaveSeries = useCallback((nextView: AdminView, pending?: PendingAdminSeriesExit) => {
    if (pending && !seriesExit.current) {
      const snapshot: AdminSeriesExitSnapshot = { ownerScope: seriesOwnerScope, viewKey: seriesViewKey,
        administratorId: activeAdministratorId, mode: view.kind === "series-create" ? "create" : "edit",
        recordId: view.kind === "series-edit" ? view.id : "", pending };
      seriesExit.current = { snapshot, pending, viewIdentity: activeViewIdentity,
        sessionGeneration: activeSessionGeneration, api: seriesApi };
    }
    navigate(nextView);
  }, [activeAdministratorId, activeSessionGeneration, activeViewIdentity, navigate, seriesApi, seriesOwnerScope, seriesViewKey, view]);
  const handleSeriesUnauthorized = useCallback(() => {
    if (viewIdentity.current === activeViewIdentity && sessionGeneration.current === activeSessionGeneration
      && seriesApiRef.current === seriesApi) handleUnauthorized();
  }, [activeSessionGeneration, activeViewIdentity, handleUnauthorized, seriesApi]);
  const handleSeriesSaved = useCallback((record: AdminSeriesRecord, operation: "create" | "update") => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || seriesApiRef.current !== seriesApi) return;
    setSeriesNotice(operation === "create"
      ? `服务端已确认创建剧集 ${record.id}。`
      : `服务端已确认更新剧集 ${record.id}。`);
    setSeriesListVersion((value) => value + 1);
    navigate({ kind: "series" });
  }, [activeSessionGeneration, activeViewIdentity, navigate, seriesApi]);
  const handleSeriesListStateChange = useCallback((state: AdminSeriesListState) => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || seriesApiRef.current !== seriesApi) return;
    setSeriesListState(state);
  }, [activeSessionGeneration, activeViewIdentity, seriesApi]);
  const handleTasksUnauthorized = useCallback(() => {
    if (viewIdentity.current === activeViewIdentity && sessionGeneration.current === activeSessionGeneration
      && tasksApiRef.current === tasksApi) handleUnauthorized();
  }, [activeSessionGeneration, activeViewIdentity, handleUnauthorized, tasksApi]);
  const handleTaskListStateChange = useCallback((state: AdminTaskListState) => {
    if (viewIdentity.current === activeViewIdentity && sessionGeneration.current === activeSessionGeneration
      && tasksApiRef.current === tasksApi) setTaskListState(state);
  }, [activeSessionGeneration, activeViewIdentity, tasksApi]);
  const handleSmtpAccountsExitState = useCallback((snapshot: AdminSmtpAccountExitSnapshot) => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || smtpAccountsApiRef.current !== smtpAccountsApi || snapshot.ownerScope !== smtpAccountsOwnerScope
      || snapshot.viewKey !== smtpAccountsViewKey || snapshot.administratorId !== activeAdministratorId
      || snapshot.mode !== smtpAccountsMode || snapshot.recordId !== smtpAccountsRecordId) return;
    if (snapshot.pending) {
      smtpAccountsExit.current = { snapshot, pending: snapshot.pending, viewIdentity: activeViewIdentity,
        sessionGeneration: activeSessionGeneration, api: smtpAccountsApi };
    } else if (smtpAccountsExit.current?.viewIdentity === activeViewIdentity
      && smtpAccountsExit.current.sessionGeneration === activeSessionGeneration
      && smtpAccountsExit.current.api === smtpAccountsApi) {
      smtpAccountsExit.current = null;
    }
  }, [activeAdministratorId, activeSessionGeneration, activeViewIdentity, smtpAccountsApi,
    smtpAccountsMode, smtpAccountsOwnerScope, smtpAccountsRecordId, smtpAccountsViewKey]);
  const leaveSmtpAccounts = useCallback((nextView: AdminView, pending?: PendingAdminSmtpAccountExit) => {
    if (pending) {
      const snapshot: AdminSmtpAccountExitSnapshot = {
        ownerScope: smtpAccountsOwnerScope,
        viewKey: smtpAccountsViewKey,
        administratorId: activeAdministratorId,
        mode: smtpAccountsMode,
        recordId: smtpAccountsRecordId,
        pending,
      };
      smtpAccountsExit.current = { snapshot, pending, viewIdentity: activeViewIdentity,
        sessionGeneration: activeSessionGeneration, api: smtpAccountsApi };
    }
    navigate(nextView);
  }, [activeAdministratorId, activeSessionGeneration, activeViewIdentity, navigate, smtpAccountsApi,
    smtpAccountsMode, smtpAccountsOwnerScope, smtpAccountsRecordId, smtpAccountsViewKey]);
  const handleSmtpAccountsUnauthorized = useCallback(() => {
    if (viewIdentity.current === activeViewIdentity && sessionGeneration.current === activeSessionGeneration
      && smtpAccountsApiRef.current === smtpAccountsApi) handleUnauthorized();
  }, [activeSessionGeneration, activeViewIdentity, handleUnauthorized, smtpAccountsApi]);
  const handleSmtpAccountsSaved = useCallback(() => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || smtpAccountsApiRef.current !== smtpAccountsApi) return;
    setSmtpAccountsListVersion((value) => value + 1);
  }, [activeSessionGeneration, activeViewIdentity, smtpAccountsApi]);
  const openSeriesCreate = useCallback(() => {
    setSeriesNotice(null);
    navigate({ kind: "series-create" });
  }, [navigate]);
  const handleSystemConfigUnauthorized = useCallback(() => {
    if (viewIdentity.current === activeViewIdentity && sessionGeneration.current === activeSessionGeneration
      && systemConfigApiRef.current === activeSystemConfigApi) handleUnauthorized();
  }, [activeSystemConfigApi, activeSessionGeneration, activeViewIdentity, handleUnauthorized]);
  const handleSystemConfigSaved = useCallback(() => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || systemConfigApiRef.current !== activeSystemConfigApi) return;
    setSystemConfigListVersion((value) => value + 1);
  }, [activeSystemConfigApi, activeSessionGeneration, activeViewIdentity]);
  const handleMembershipUnauthorized = useCallback(() => {
    if (viewIdentity.current === activeViewIdentity && sessionGeneration.current === activeSessionGeneration
      && membershipApiRef.current === activeMembershipApi) handleUnauthorized();
  }, [activeMembershipApi, activeSessionGeneration, activeViewIdentity, handleUnauthorized]);
  const handleMembershipSaved = useCallback((resource: "redemption-codes" | "purchase-packages") => {
    if (viewIdentity.current !== activeViewIdentity || sessionGeneration.current !== activeSessionGeneration
      || membershipApiRef.current !== activeMembershipApi) return;
    if (resource === "redemption-codes") setRedemptionListVersion((value) => value + 1);
    else setPackageListVersion((value) => value + 1);
  }, [activeMembershipApi, activeSessionGeneration, activeViewIdentity]);
  const handleResourceUnauthorized = useCallback(() => {
    if (viewIdentity.current === activeViewIdentity && sessionGeneration.current === activeSessionGeneration) handleUnauthorized();
  }, [activeSessionGeneration, activeViewIdentity, handleUnauthorized]);
  const handleResourceSaved = useCallback((resource: "system-prompts" | "users" | "user-credits") => {
    if (viewIdentity.current !== activeViewIdentity) return;
    if (resource === "system-prompts") {
      setPromptListVersion((value) => value + 1);
    } else if (resource === "users") {
      setUserListVersion((value) => value + 1);
    } else {
      setCreditListVersion((value) => value + 1);
    }
    navigate({ kind: resource });
  }, [activeViewIdentity, navigate]);

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      const membershipPending = membershipExit.current;
      const systemConfigPending = systemConfigExit.current;
      const seriesPending = seriesExit.current;
      const smtpAccountsPending = smtpAccountsExit.current;
      const protectsExit = (membershipPending && membershipPending.viewIdentity === viewIdentity.current
        && membershipPending.sessionGeneration === sessionGeneration.current
        && membershipPending.api === membershipApiRef.current)
        || (systemConfigPending && systemConfigPending.viewIdentity === viewIdentity.current
          && systemConfigPending.sessionGeneration === sessionGeneration.current
          && systemConfigPending.api === systemConfigApiRef.current)
        || (seriesPending && seriesPending.viewIdentity === viewIdentity.current
          && seriesPending.sessionGeneration === sessionGeneration.current
          && seriesPending.api === seriesApiRef.current)
        || (smtpAccountsPending && smtpAccountsPending.viewIdentity === viewIdentity.current
          && smtpAccountsPending.sessionGeneration === sessionGeneration.current
          && smtpAccountsPending.api === smtpAccountsApiRef.current);
      if (!protectsExit) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, []);

  if (!session) {
    return <AdminLogin api={api} storage={storage} onLogin={handleLogin} />;
  }

  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <a className="admin-workspace-brand" href="#" onClick={(event) => { event.preventDefault(); navigate({ kind: "dashboard" }); }}>
          <span className="admin-brand-mark" aria-hidden="true">H</span>
          <span><strong>Hao AI</strong><small>管理后台</small></span>
        </a>
        <nav className="admin-navigation" aria-label="后台导航">
          <p className="admin-nav-label">工作台</p>
          <button
            className={view.kind === "dashboard" ? "admin-nav-item is-active" : "admin-nav-item"}
            type="button"
            aria-label="运营概览"
            title="运营概览"
            aria-current={view.kind === "dashboard" ? "page" : undefined}
            onClick={() => navigate({ kind: "dashboard" })}
          >
            <span aria-hidden="true">◫</span>运营概览
          </button>
          <button
            className={view.kind === "system-config" || view.kind === "system-config-edit" ? "admin-nav-item is-active" : "admin-nav-item"}
            type="button" aria-label="系统配置" title="系统配置"
            aria-current={view.kind === "system-config" || view.kind === "system-config-edit" ? "page" : undefined}
            onClick={() => navigate({ kind: "system-config" })}
          >
            <span aria-hidden="true">⚙</span>系统配置
          </button>
          <button
            className={view.kind === "system-models" || view.kind === "model-create" || view.kind === "model-edit"
              ? "admin-nav-item is-active"
              : "admin-nav-item"}
            type="button"
            aria-label="系统模型"
            title="系统模型"
            aria-current={view.kind === "system-models" || view.kind === "model-create" || view.kind === "model-edit"
              ? "page"
              : undefined}
            onClick={() => navigate({ kind: "system-models" })}
          >
            <span aria-hidden="true">◈</span>系统模型
          </button>
          <button
            className={view.kind === "system-prompts" || view.kind === "prompt-create" || view.kind === "prompt-edit"
              ? "admin-nav-item is-active"
              : "admin-nav-item"}
            type="button"
            aria-label="系统提示词"
            title="系统提示词"
            aria-current={view.kind === "system-prompts" || view.kind === "prompt-create" || view.kind === "prompt-edit"
              ? "page"
              : undefined}
            onClick={() => navigate({ kind: "system-prompts" })}
          >
            <span aria-hidden="true">≋</span>系统提示词
          </button>
          <button
            className={view.kind === "users" || view.kind === "user-create" || view.kind === "user-edit"
              ? "admin-nav-item is-active"
              : "admin-nav-item"}
            type="button"
            aria-label="用户管理"
            title="用户管理"
            aria-current={view.kind === "users" || view.kind === "user-create" || view.kind === "user-edit"
              ? "page"
              : undefined}
            onClick={() => navigate({ kind: "users" })}
          >
            <span aria-hidden="true">♙</span>用户管理
          </button>
          <button
            className={view.kind === "user-credits" || view.kind === "credit-edit" ? "admin-nav-item is-active" : "admin-nav-item"}
            type="button"
            aria-label="用户积分"
            title="用户积分"
            aria-current={view.kind === "user-credits" || view.kind === "credit-edit" ? "page" : undefined}
            onClick={() => navigate({ kind: "user-credits" })}
          >
            <span aria-hidden="true">◉</span>用户积分
          </button>
          <button
            className={view.kind === "redemption-codes" || view.kind === "redemption-create" ? "admin-nav-item is-active" : "admin-nav-item"}
            type="button" aria-label="兑换码管理" title="兑换码管理"
            aria-current={view.kind === "redemption-codes" || view.kind === "redemption-create" ? "page" : undefined}
            onClick={() => navigate({ kind: "redemption-codes" })}
          >
            <span aria-hidden="true">⌁</span>兑换码管理
          </button>
          <button
            className={view.kind === "purchase-packages" || view.kind === "purchase-package-create" || view.kind === "purchase-package-edit" ? "admin-nav-item is-active" : "admin-nav-item"}
            type="button" aria-label="购买套餐" title="购买套餐"
            aria-current={view.kind === "purchase-packages" || view.kind === "purchase-package-create" || view.kind === "purchase-package-edit" ? "page" : undefined}
            onClick={() => navigate({ kind: "purchase-packages" })}
          >
            <span aria-hidden="true">◇</span>购买套餐
          </button>
          <button
            className={view.kind === "credit-logs" ? "admin-nav-item is-active" : "admin-nav-item"}
            type="button"
            aria-label="积分流水"
            title="积分流水"
            aria-current={view.kind === "credit-logs" ? "page" : undefined}
            onClick={() => navigate({ kind: "credit-logs" })}
          >
            <span aria-hidden="true">↕</span>积分流水
          </button>
          <button
            className={view.kind === "series" || view.kind === "series-create" || view.kind === "series-edit" ? "admin-nav-item is-active" : "admin-nav-item"}
            type="button"
            aria-label="剧集管理"
            title="剧集管理"
            aria-current={view.kind === "series" || view.kind === "series-create" || view.kind === "series-edit" ? "page" : undefined}
            onClick={() => navigate({ kind: "series" })}
          >
            <span aria-hidden="true">▣</span>剧集管理
          </button>
          <button
            className={view.kind === "admin-tasks" ? "admin-nav-item is-active" : "admin-nav-item"}
            type="button"
            aria-label="全局任务"
            title="全局任务"
            aria-current={view.kind === "admin-tasks" ? "page" : undefined}
            onClick={() => navigate({ kind: "admin-tasks" })}
          >
            <span aria-hidden="true">≡</span>全局任务
          </button>
          <button
            className={view.kind === "smtp-accounts" || view.kind === "smtp-account-create" || view.kind === "smtp-account-edit" ? "admin-nav-item is-active" : "admin-nav-item"}
            type="button"
            aria-label="SMTP 账号"
            title="SMTP 账号"
            aria-current={view.kind === "smtp-accounts" || view.kind === "smtp-account-create" || view.kind === "smtp-account-edit" ? "page" : undefined}
            onClick={() => navigate({ kind: "smtp-accounts" })}
          >
            <span aria-hidden="true">✉</span>SMTP 账号
          </button>
        </nav>
        <div className="admin-sidebar-account">
          <span className="admin-sidebar-avatar" aria-hidden="true">
            {session.user.username.slice(0, 1).toUpperCase()}
          </span>
          <span className="admin-sidebar-identity">
            <strong>{session.user.username}</strong>
            <small>超级管理员</small>
          </span>
          <button className="admin-logout-button" type="button" onClick={handleLogout} aria-label="退出登录" title="退出登录">
            ↗
          </button>
        </div>
      </aside>

      <main className="admin-main">
        <header className="admin-topbar">
          <span>Hao AI 管理后台</span>
          <span className="admin-identity-badge">{session.user.username}</span>
        </header>
        <div className="admin-content">
          {view.kind === "dashboard" ? (
            <AdminDashboard api={api} onUnauthorized={handleUnauthorized} />
          ) : view.kind === "system-config" || view.kind === "system-config-edit" ? (
            <AdminSystemConfig
              key={activeViewIdentity}
              api={systemConfigApi}
              mode={view.kind === "system-config" ? "list" : "edit"}
              {...(view.kind === "system-config-edit" ? { configId: view.id } : {})}
              ownerScope={systemConfigOwnerScope}
              viewKey={systemConfigViewKey}
              administratorId={activeAdministratorId}
              listVersion={systemConfigListVersion}
              onAuthenticationError={handleSystemConfigUnauthorized}
              onEdit={(id) => navigate({ kind: "system-config-edit", id })}
              onCancel={(pending) => leaveSystemConfig({ kind: "system-config" }, pending)}
              onSaved={handleSystemConfigSaved}
              onExitStateChange={handleSystemConfigExitState}
            />
          ) : view.kind === "system-models" ? (
            <SystemModels
              api={api}
              aistarslabApi={aistarslabApi}
              adminActorId={session?.user.id ?? ""}
              adminSessionGeneration={activeSessionGeneration}
              onUnauthorized={handleUnauthorized}
              mode="list"
              modelId={undefined}
              onCreate={() => navigate({ kind: "model-create" })}
              onEdit={(id) => navigate({ kind: "model-edit", id })}
              reloadVersion={modelListVersion}
              onAistarslabExitStateChange={handleAistarslabExitStateChange}
            />
          ) : view.kind === "model-create" || view.kind === "model-edit" ? (
            <SystemModels
              api={api}
              aistarslabApi={aistarslabApi}
              adminActorId={session?.user.id ?? ""}
              adminSessionGeneration={activeSessionGeneration}
              onUnauthorized={handleUnauthorized}
              onCancel={() => navigate({ kind: "system-models" })}
              onSaved={handleModelsSaved}
              mode={view.kind === "model-create" ? "create" : "edit"}
              modelId={view.kind === "model-edit" ? view.id : undefined}
              reloadVersion={modelListVersion}
              onAistarslabExitStateChange={handleAistarslabExitStateChange}
            />
          ) : view.kind === "system-prompts" || view.kind === "prompt-create" || view.kind === "prompt-edit" ? (
            <SystemPrompts
              key={activeViewIdentity}
              api={promptsApi}
              onAuthenticationError={handleResourceUnauthorized}
              mode={view.kind === "system-prompts" ? "list" : view.kind === "prompt-create" ? "create" : "edit"}
              promptId={view.kind === "prompt-edit" ? view.id : undefined}
              reloadVersion={promptListVersion}
              onCreate={() => navigate({ kind: "prompt-create" })}
              onEdit={(id) => navigate({ kind: "prompt-edit", id })}
              onCancel={() => navigate({ kind: "system-prompts" })}
              onSaved={() => handleResourceSaved("system-prompts")}
            />
          ) : view.kind === "user-credits" ? (
            <UserCreditsList
              key={activeViewIdentity}
              api={creditsApi}
              reloadVersion={creditListVersion}
              onEdit={(id) => navigate({ kind: "credit-edit", id })}
              onAuthenticationError={handleResourceUnauthorized}
            />
          ) : view.kind === "credit-edit" ? (
            <UserCreditEditor
              key={activeViewIdentity}
              api={creditsApi}
              creditId={view.id}
              onCancel={() => navigate({ kind: "user-credits" })}
              onSaved={() => handleResourceSaved("user-credits")}
              onAuthenticationError={handleResourceUnauthorized}
            />
          ) : view.kind === "credit-logs" ? (
            <CreditLogs
              key={activeViewIdentity}
              api={creditsApi}
              onAuthenticationError={handleResourceUnauthorized}
            />
          ) : view.kind === "series" || view.kind === "series-create" || view.kind === "series-edit" ? (
            <AdminSeries
              key={activeViewIdentity}
              api={seriesApi}
              mode={view.kind === "series" ? "list" : view.kind === "series-create" ? "create" : "edit"}
              {...(view.kind === "series-edit" ? { recordId: view.id } : {})}
              ownerScope={seriesOwnerScope}
              viewKey={seriesViewKey}
              administratorId={activeAdministratorId}
              listVersion={seriesListVersion}
              listState={seriesListState}
              notice={view.kind === "series" ? seriesNotice : null}
              onListStateChange={handleSeriesListStateChange}
              onAuthenticationError={handleSeriesUnauthorized}
              onCreate={openSeriesCreate}
              onEdit={(id) => navigate({ kind: "series-edit", id })}
              onCancel={(pending) => leaveSeries({ kind: "series" }, pending)}
              onSaved={handleSeriesSaved}
              onExitStateChange={handleSeriesExitState}
            />
          ) : view.kind === "admin-tasks" ? (
            <AdminTasks
              key={activeViewIdentity}
              api={tasksApi}
              ownerScope={tasksOwnerScope}
              viewKey={tasksViewKey}
              listState={taskListState}
              onListStateChange={handleTaskListStateChange}
              onAuthenticationError={handleTasksUnauthorized}
            />
          ) : view.kind === "smtp-accounts" || view.kind === "smtp-account-create" || view.kind === "smtp-account-edit" ? (
            <AdminSmtpAccounts
              key={activeViewIdentity}
              api={smtpAccountsApi}
              mode={smtpAccountsMode}
              {...(smtpAccountsMode === "edit" ? { recordId: smtpAccountsRecordId } : {})}
              ownerScope={smtpAccountsOwnerScope}
              viewKey={smtpAccountsViewKey}
              administratorId={activeAdministratorId}
              listVersion={smtpAccountsListVersion}
              onAuthenticationError={handleSmtpAccountsUnauthorized}
              onCreate={() => navigate({ kind: "smtp-account-create" })}
              onEdit={(id) => navigate({ kind: "smtp-account-edit", id })}
              onCancel={(pending) => leaveSmtpAccounts(
                smtpAccountsMode === "list" ? { kind: "dashboard" } : { kind: "smtp-accounts" }, pending,
              )}
              onSaved={handleSmtpAccountsSaved}
              onExitStateChange={handleSmtpAccountsExitState}
            />
          ) : view.kind === "redemption-codes" || view.kind === "redemption-create" ? (
            <RedemptionCodes
              key={activeViewIdentity}
              api={membershipApi}
              mode={view.kind === "redemption-codes" ? "list" : "create"}
              ownerScope={membershipOwnerScope}
              viewKey={membershipViewKey}
              currentAdministratorId={session.user.id}
              listVersion={redemptionListVersion}
              onAuthenticationError={handleMembershipUnauthorized}
              onCreate={() => navigate({ kind: "redemption-create" })}
              onCancel={(pending) => leaveMembership({ kind: "redemption-codes" }, pending)}
              onSaved={() => handleMembershipSaved("redemption-codes")}
              onExitStateChange={handleMembershipExitState}
            />
          ) : view.kind === "purchase-packages" || view.kind === "purchase-package-create" || view.kind === "purchase-package-edit" ? (
            <PurchasePackages
              key={activeViewIdentity}
              api={membershipApi}
              mode={view.kind === "purchase-packages" ? "list" : view.kind === "purchase-package-create" ? "create" : "edit"}
              {...(view.kind === "purchase-package-edit" ? { recordId: view.id } : {})}
              ownerScope={membershipOwnerScope}
              viewKey={membershipViewKey}
              currentAdministratorId={session.user.id}
              listVersion={packageListVersion}
              onAuthenticationError={handleMembershipUnauthorized}
              onCreate={() => navigate({ kind: "purchase-package-create" })}
              onEdit={(id) => navigate({ kind: "purchase-package-edit", id })}
              onCancel={(pending) => leaveMembership({ kind: "purchase-packages" }, pending)}
              onSaved={() => handleMembershipSaved("purchase-packages")}
              onExitStateChange={handleMembershipExitState}
            />
          ) : (
            <Users
              key={activeViewIdentity}
              api={usersApi}
              onAuthenticationError={handleResourceUnauthorized}
              mode={view.kind === "users" ? "list" : view.kind === "user-create" ? "create" : "edit"}
              userId={view.kind === "user-edit" ? view.id : undefined}
              currentAdministratorId={session.user.id}
              listVersion={userListVersion}
              onCreate={() => navigate({ kind: "user-create" })}
              onEdit={(id) => navigate({ kind: "user-edit", id })}
              onCancel={() => navigate({ kind: "users" })}
              onSaved={() => handleResourceSaved("users")}
            />
          )}
        </div>
      </main>
    </div>
  );
}
