import { useState, type FormEvent } from "react";
import type { AccountAccessService } from "../../shared/api/accountAccess";
import { useAccountOperation, useUnsavedAccountForm } from "./AccountAccessForms";

interface ResetPasswordPageProps {
  token: string | null;
  accountAccess?: AccountAccessService | undefined;
  serviceUnavailableMessage: string;
  onTokenConsumed(): void;
}

export function captureResetTokenFromLocation(): string | null {
  const url = new URL(window.location.href);
  if (url.pathname !== "/reset-password.html") {
    return null;
  }
  const token = url.searchParams.get("token");
  if (url.searchParams.has("token")) {
    url.searchParams.delete("token");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }
  return token;
}

export function ResetPasswordPage({
  token: initialToken,
  accountAccess,
  serviceUnavailableMessage,
  onTokenConsumed,
}: ResetPasswordPageProps) {
  const [token, setToken] = useState(initialToken);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [dirty, setDirty] = useState(false);
  const [resetComplete, setResetComplete] = useState(false);
  const { pending, feedback, setFeedback, run } = useAccountOperation(accountAccess);
  useUnsavedAccountForm(dirty);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (token === null || token === "" || accountAccess === undefined) {
      return;
    }
    if (password !== confirmation) {
      setFeedback({ kind: "error", message: "两次输入的密码不一致。" });
      return;
    }
    void run((service, signal) => service.resetPassword({
      token,
      new_password: password,
    }, signal), (receipt) => {
      setToken(null);
      onTokenConsumed();
      setPassword("");
      setConfirmation("");
      setDirty(false);
      setResetComplete(true);
      setFeedback({ kind: "success", message: `${receipt.message}。当前登录会话没有改变，请使用新密码登录。` });
    });
  }

  return (
    <main className="login-screen reset-password-screen">
      <section className="login-card" aria-labelledby="reset-password-title">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">H</span>
          <span>Hao AI</span>
        </div>
        <div className="login-intro">
          <p className="eyebrow">账号安全</p>
          <h1 id="reset-password-title">设置新密码</h1>
          <p>设置完成后，请用新密码登录工作台。</p>
        </div>

        {accountAccess === undefined && (
          <p className="account-access-note" role="status">
            {serviceUnavailableMessage}
          </p>
        )}
        {!resetComplete && (token === null || token === "") ? (
          <p className="form-error" role="alert">
            重置链接无效或已离开当前页面。请从密码重置邮件中重新打开链接。
          </p>
        ) : !resetComplete && token !== null && accountAccess !== undefined ? (
          <form className="login-form" onSubmit={handleSubmit}>
            <label htmlFor="reset-new-password">新密码</label>
            <input
              autoComplete="new-password"
              disabled={pending}
              id="reset-new-password"
              name="new_password"
              onChange={(event) => {
                setPassword(event.currentTarget.value);
                setDirty(true);
                setFeedback(null);
              }}
              required
              type="password"
              value={password}
            />
            <p className="account-field-hint">密码至少 8 位，并包含字母和数字。</p>
            <label htmlFor="reset-password-confirm">确认新密码</label>
            <input
              autoComplete="new-password"
              disabled={pending}
              id="reset-password-confirm"
              name="new_password_confirmation"
              onChange={(event) => {
                setConfirmation(event.currentTarget.value);
                setDirty(true);
                setFeedback(null);
              }}
              required
              type="password"
              value={confirmation}
            />
            {feedback !== null && (
              <p className={feedback.kind === "error" ? "form-error" : "form-success"} role={feedback.kind === "error" ? "alert" : "status"}>
                {feedback.message}
              </p>
            )}
            <button className="primary-button login-button" disabled={pending} type="submit">
              {pending ? "正在更新密码…" : "保存新密码"}
            </button>
          </form>
        ) : null}
        {resetComplete && feedback !== null && (
          <p className="form-success" role="status">{feedback.message}</p>
        )}
        <a className="account-back-link" href="/" referrerPolicy="no-referrer">返回登录</a>
      </section>
      <aside className="login-aside" aria-label="密码重置说明">
        <div className="aside-orbit orbit-one" />
        <div className="aside-orbit orbit-two" />
        <p className="eyebrow">账号安全</p>
        <h2>更新密码，<br />继续你的创作。</h2>
        <p>重置链接由服务器验证。更新密码后，请使用新密码重新登录。</p>
        <div className="aside-footer">
          <span className="aside-line" />
          <span>剧集 · 协作 · 灵感</span>
        </div>
      </aside>
    </main>
  );
}
