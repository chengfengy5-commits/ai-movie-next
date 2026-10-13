import { useCallback, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import { resolveApiBaseUrl } from "../../shared/api/config";
import { AdminDashboard } from "./AdminDashboard";
import { createAdminApi, type AdminFetcher } from "./adminApi";
import type { AdminApi, AdminStorage, AdminUser } from "./adminTypes";
import { SystemPrompts } from "./SystemPrompts";
import { createSystemPromptsApi } from "./systemPromptsApi";
import { Users } from "./Users";
import { createAdminUsersApi } from "./usersApi";
import { SystemModels } from "./SystemModels";
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
  | { kind: "user-edit"; id: string };

interface AdminSession {
  token: string;
  user: AdminUser;
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
  const [modelListVersion, setModelListVersion] = useState(0);
  const [promptListVersion, setPromptListVersion] = useState(0);
  const [userListVersion, setUserListVersion] = useState(0);

  const navigate = useCallback((nextView: AdminView) => {
    viewIdentity.current += 1;
    setView(nextView);
  }, []);

  const api = useMemo(
    () => createAdminApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  const promptsApi = useMemo(
    () => createSystemPromptsApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );
  const usersApi = useMemo(
    () => createAdminUsersApi(fetcher, () => storage.getItem(ADMIN_TOKEN_KEY), safeApiBaseUrl),
    [fetcher, safeApiBaseUrl, storage],
  );

  const handleLogin = useCallback((nextSession: AdminSession) => {
    setSession(nextSession);
    navigate({ kind: "dashboard" });
  }, [navigate]);

  const handleUnauthorized = useCallback(() => {
    clearAdminSession(storage);
    setSession(null);
    navigate({ kind: "dashboard" });
  }, [navigate, storage]);

  const handleLogout = useCallback(() => {
    clearAdminSession(storage);
    setSession(null);
    navigate({ kind: "dashboard" });
  }, [navigate, storage]);

  const handleModelsSaved = useCallback(() => {
    navigate({ kind: "system-models" });
    setModelListVersion((value) => value + 1);
  }, [navigate]);

  const activeViewIdentity = viewIdentity.current;
  const handleResourceUnauthorized = useCallback(() => {
    if (viewIdentity.current === activeViewIdentity) handleUnauthorized();
  }, [activeViewIdentity, handleUnauthorized]);
  const handleResourceSaved = useCallback((resource: "system-prompts" | "users") => {
    if (viewIdentity.current !== activeViewIdentity) return;
    if (resource === "system-prompts") {
      setPromptListVersion((value) => value + 1);
    } else {
      setUserListVersion((value) => value + 1);
    }
    navigate({ kind: resource });
  }, [activeViewIdentity, navigate]);

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
          ) : view.kind === "system-models" ? (
            <SystemModels
              api={api}
              onUnauthorized={handleUnauthorized}
              mode="list"
              modelId={undefined}
              onCreate={() => navigate({ kind: "model-create" })}
              onEdit={(id) => navigate({ kind: "model-edit", id })}
              reloadVersion={modelListVersion}
            />
          ) : view.kind === "model-create" || view.kind === "model-edit" ? (
            <SystemModels
              api={api}
              onUnauthorized={handleUnauthorized}
              onCancel={() => navigate({ kind: "system-models" })}
              onSaved={handleModelsSaved}
              mode={view.kind === "model-create" ? "create" : "edit"}
              modelId={view.kind === "model-edit" ? view.id : undefined}
              reloadVersion={modelListVersion}
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
          ) : (
            <Users
              key={activeViewIdentity}
              api={usersApi}
              onAuthenticationError={handleResourceUnauthorized}
              mode={view.kind === "users" ? "list" : view.kind === "user-create" ? "create" : "edit"}
              userId={view.kind === "user-edit" ? view.id : undefined}
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
