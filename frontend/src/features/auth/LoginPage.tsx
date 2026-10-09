import { useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/errors";
import type { AppMode } from "../../shared/api/config";
import type { Credentials } from "../../shared/api/contracts";

interface LoginPageProps {
  mode: AppMode;
  error: ApiError | null;
  onSubmit(credentials: Credentials): Promise<void>;
}

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

export function LoginPage({ mode, error, onSubmit }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const message = loginMessage(error);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit({ username: username.trim(), password });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-screen">
      <section className="login-card" aria-labelledby="login-title">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">H</span>
          <span>Hao AI</span>
        </div>
        <div className="login-intro">
          <p className="eyebrow">创作工作台</p>
          <h1 id="login-title">欢迎回来</h1>
          <p>登录后查看你的剧集与协作项目。</p>
        </div>

        <div className={mode === "demo" ? "mode-note demo-note" : "mode-note api-note"} role="status">
          <span className="status-dot" aria-hidden="true" />
          {mode === "demo" ? "演示模式 · 数据仅保存在本机" : "隔离 API 模式 · 仅连接本地服务"}
        </div>

        {mode === "demo" && (
          <div className="demo-credentials">
            <span>演示账号</span>
            <code>demo</code>
            <span>密码</span>
            <code>demo123</code>
          </div>
        )}

        <form className="login-form" onSubmit={handleSubmit}>
          <label htmlFor="username">账号</label>
          <input
            autoComplete="username"
            id="username"
            name="username"
            onChange={(event) => setUsername(event.currentTarget.value)}
            required
            value={username}
          />
          <label htmlFor="password">密码</label>
          <input
            autoComplete="current-password"
            id="password"
            name="password"
            onChange={(event) => setPassword(event.currentTarget.value)}
            required
            type="password"
            value={password}
          />
          {message !== null && <p className="form-error" role="alert">{message}</p>}
          <button className="primary-button login-button" disabled={submitting} type="submit">
            {submitting ? "正在验证…" : "登录工作台"}
          </button>
        </form>
        <p className="login-footnote">现在可以安全查看剧集；制作功能待开放。</p>
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
