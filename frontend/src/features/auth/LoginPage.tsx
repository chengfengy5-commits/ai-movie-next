import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import type { AppMode } from "../../shared/api/config";
import type { Credentials, User } from "../../shared/api/contracts";
import type { AccountAccessService } from "../../shared/api/accountAccess";
import { ForgotPasswordForm, RegistrationForm, useUnsavedAccountForm } from "./AccountAccessForms";

interface LoginPageProps {
  mode: AppMode;
  error: ApiError | null;
  accountAccess?: AccountAccessService | undefined;
  onSubmit(credentials: Credentials): Promise<void>;
}

type EntryPage = "login" | "register" | "forgot-password";

function loginMessage(error: ApiError | null): string | null {
  if (error === null) {
    return null;
  }
  if (error.status === 401) {
    return "账号或密码不正确，请检查后重试。";
  }
  if (error.status === 422) {
    return error.detail ?? "请检查账号和密码格式。";
  }
  if (error.kind === "timeout") {
    return "验证请求超时，请检查本地服务后重试。";
  }
  if (error.kind === "network") {
    return "无法连接本地服务，请确认服务正在运行。";
  }
  return error.detail ?? "暂时无法登录，请稍后重试。";
}

export function LoginPage({ mode, error, accountAccess, onSubmit }: LoginPageProps) {
  const [entryPage, setEntryPage] = useState<EntryPage>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formDirty, setFormDirty] = useState(false);
  const [entryNotice, setEntryNotice] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousPage = useRef(entryPage);
  const message = loginMessage(error);
  useUnsavedAccountForm(formDirty && entryPage !== "login");

  useEffect(() => {
    if (previousPage.current !== entryPage) {
      headingRef.current?.focus();
      previousPage.current = entryPage;
    }
  }, [entryPage]);

  function changeEntryPage(nextPage: EntryPage) {
    if (nextPage === entryPage || submitting) {
      return;
    }
    if (formDirty && !window.confirm("离开后会清除当前输入，是否继续？")) {
      return;
    }
    setFormDirty(false);
    setEntryNotice(null);
    setEntryPage(nextPage);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setSubmitting(true);
    setEntryNotice(null);
    try {
      await onSubmit({ username: username.trim(), password });
    } finally {
      setSubmitting(false);
    }
  }

  function handleRegistered(user: User) {
    setUsername(user.username);
    setPassword("");
    setFormDirty(false);
    setEntryNotice("账号已创建，请使用新账号和密码登录。");
    setEntryPage("login");
  }

  const title = entryPage === "login"
    ? "欢迎回来"
    : entryPage === "register"
      ? "创建账号"
      : "找回密码";
  const description = entryPage === "login"
    ? "登录后查看你的剧集与协作项目。"
    : entryPage === "register"
      ? "填写邮箱验证码，创建你的创作账号。"
      : "填写账号和注册邮箱，申请密码重置链接。";

  return (
    <main className="login-screen">
      <section className="login-card" aria-labelledby="login-title">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">H</span>
          <span>Hao AI</span>
        </div>
        <div className="login-intro">
          <p className="eyebrow">创作工作台</p>
          <h1 id="login-title" ref={headingRef} tabIndex={-1}>{title}</h1>
          <p>{description}</p>
        </div>

        <div className={mode === "demo" ? "mode-note demo-note" : "mode-note api-note"} role="status">
          <span className="status-dot" aria-hidden="true" />
          {mode === "demo" ? "演示模式 · 数据仅保存在本机" : "隔离 API 模式 · 仅连接本地服务"}
        </div>

        {mode === "demo" && entryPage === "login" && (
          <div className="demo-credentials">
            <span>演示账号</span>
            <code>demo</code>
            <span>密码</span>
            <code>demo123</code>
          </div>
        )}

        {entryPage === "login" ? (
          <>
            <form className="login-form" onSubmit={handleSubmit}>
              <label htmlFor="username">账号</label>
              <input
                autoComplete="username"
                disabled={submitting}
                id="username"
                name="username"
                onChange={(event) => setUsername(event.currentTarget.value)}
                required
                value={username}
              />
              <label htmlFor="password">密码</label>
              <input
                autoComplete="current-password"
                disabled={submitting}
                id="password"
                name="password"
                onChange={(event) => setPassword(event.currentTarget.value)}
                required
                type="password"
                value={password}
              />
              {message !== null && <p className="form-error" role="alert">{message}</p>}
              {entryNotice !== null && <p className="form-success" role="status">{entryNotice}</p>}
              <button className="primary-button login-button" disabled={submitting} type="submit">
                {submitting ? "正在验证…" : "登录工作台"}
              </button>
            </form>
            <nav className="account-entry-links" aria-label="账户操作">
              <button disabled={submitting} onClick={() => changeEntryPage("register")} type="button">
                创建账号
              </button>
              <button disabled={submitting} onClick={() => changeEntryPage("forgot-password")} type="button">
                忘记密码？
              </button>
            </nav>
          </>
        ) : (
          <>
            <button
              className="account-back-link"
              onClick={() => changeEntryPage("login")}
              type="button"
            >
              返回登录
            </button>
            {entryPage === "register" ? (
              <RegistrationForm
                onDirtyChange={setFormDirty}
                onRegistered={handleRegistered}
                service={accountAccess}
              />
            ) : (
              <ForgotPasswordForm
                onDirtyChange={setFormDirty}
                service={accountAccess}
              />
            )}
          </>
        )}
        {entryPage === "login" && <p className="login-footnote">现在可以安全查看剧集；制作功能待开放。</p>}
      </section>
      <aside className="login-aside" aria-label="工作台说明">
        <div className="aside-orbit orbit-one" />
        <div className="aside-orbit orbit-two" />
        <p className="eyebrow">创作，从一个清晰的入口开始</p>
        <h2>把灵感、协作与故事<br />放在同一张桌面上。</h2>
        <p>这里是新版工作台的第一步。你可以安全地浏览剧集，也可以连接本机服务体验登录与列表。</p>
        <div className="aside-footer">
          <span className="aside-line" />
          <span>剧集 · 协作 · 灵感</span>
        </div>
      </aside>
    </main>
  );
}
