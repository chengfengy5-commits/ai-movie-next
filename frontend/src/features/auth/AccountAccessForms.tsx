import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, isAbortError } from "../../shared/api/errors";
import type { AccountAccessService, ForgotPasswordInput } from "../../shared/api/accountAccess";
import { InvalidResponseError, type User } from "../../shared/api/contracts";

interface AccountAccessFormsProps {
  service: AccountAccessService | undefined;
  onDirtyChange(dirty: boolean): void;
  onRegistered(user: User): void;
}

type Feedback = { kind: "error" | "success"; message: string };

interface ActiveOperation {
  controller: AbortController;
  generation: number;
  service: AccountAccessService;
}

function normalizedEmail(value: string): string {
  return value.trim().toLowerCase();
}

function accountErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.kind === "timeout") {
    return "请求超时，请稍后重试。当前输入已保留。";
  }
  if (error instanceof ApiError && error.kind === "network") {
    return "无法连接本地服务，请检查服务状态后重试。当前输入已保留。";
  }
  if (error instanceof ApiError) {
    return error.detail ?? error.message;
  }
  if (error instanceof InvalidResponseError) {
    return error.message;
  }
  return "发生意外错误，请重试。";
}

function sendCodeErrorMessage(error: unknown): string {
  if (error instanceof ApiError && (error.kind === "timeout" || error.kind === "network")) {
    return "发送结果暂未确认。请先检查收件箱和垃圾邮件；如果已收到验证码，可直接填写并校验，或提交注册让服务器校验。系统不会自动重发。";
  }
  return accountErrorMessage(error);
}

function registrationErrorMessage(error: unknown): string {
  const uncertain = error instanceof InvalidResponseError
    || (error instanceof ApiError && (
      error.kind === "network"
      || error.kind === "timeout"
      || error.kind === "invalid-response"
      || (error.status !== null && error.status >= 500)
    ));
  if (uncertain) {
    return "暂时无法确认注册是否完成。请先尝试登录；确认账号未创建后再决定是否重新提交。系统不会自动重复注册。";
  }
  return accountErrorMessage(error);
}

export function useAccountOperation(service: AccountAccessService | undefined) {
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const activeOperation = useRef<ActiveOperation | null>(null);
  const generation = useRef(0);
  const serviceRef = useRef(service);
  serviceRef.current = service;

  useEffect(() => {
    generation.current += 1;
    activeOperation.current?.controller.abort();
    activeOperation.current = null;
    setPending(false);
    return () => {
      generation.current += 1;
      activeOperation.current?.controller.abort();
      activeOperation.current = null;
    };
  }, [service]);

  const run = useCallback(async <T,>(
    action: (currentService: AccountAccessService, signal: AbortSignal) => Promise<T>,
    onSuccess: (result: T) => void,
    errorMessage: (error: unknown) => string = accountErrorMessage,
  ) => {
    const currentService = service;
    if (currentService === undefined || activeOperation.current !== null) {
      return;
    }

    const operation: ActiveOperation = {
      controller: new AbortController(),
      generation: ++generation.current,
      service: currentService,
    };
    activeOperation.current = operation;
    setPending(true);
    setFeedback(null);

    const isCurrent = () => activeOperation.current === operation
      && operation.generation === generation.current
      && !operation.controller.signal.aborted
      && serviceRef.current === operation.service;

    try {
      const result = await action(currentService, operation.controller.signal);
      if (isCurrent()) {
        onSuccess(result);
      }
    } catch (error) {
      if (isCurrent() && !isAbortError(error)) {
        setFeedback({ kind: "error", message: errorMessage(error) });
      }
    } finally {
      if (isCurrent()) {
        activeOperation.current = null;
        setPending(false);
      }
    }
  }, [service]);

  return { pending, feedback, setFeedback, run };
}

export function RegistrationForm({ service, onDirtyChange, onRegistered }: AccountAccessFormsProps) {
  const [username, setUsername] = useState("");
  const [usernameError, setUsernameError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [verifiedEmail, setVerifiedEmail] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<"send" | "verify" | "register" | null>(null);
  const { pending, feedback, setFeedback, run } = useAccountOperation(service);
  const emailValue = normalizedEmail(email);
  const emailVerified = verifiedEmail === emailValue && emailValue !== "";

  function markDirty() {
    onDirtyChange(true);
    setFeedback(null);
  }

  function handleEmailChange(value: string) {
    setEmail(value);
    setEmailCode("");
    setVerifiedEmail(null);
    setFeedback(null);
    markDirty();
  }

  async function sendCode() {
    if (!service) {
      return;
    }
    setVerifiedEmail(null);
    setPendingAction("send");
    try {
      await run((currentService, signal) => currentService.sendEmailCode({
        email: emailValue,
        username: username.trim(),
      }, signal), (receipt) => {
        setVerifiedEmail(null);
        setFeedback({
          kind: "success",
          message: `接口返回“${receipt.message}”（约 ${Math.ceil(receipt.expireInSeconds / 60)} 分钟有效）。请检查收件箱和垃圾邮件；此响应不能确认邮件已送达。`,
        });
      }, sendCodeErrorMessage);
    } finally {
      setPendingAction(null);
    }
  }

  async function verifyCode() {
    if (!service || emailValue === "" || emailCode.trim() === "") {
      setFeedback({ kind: "error", message: "请填写邮箱和收到的验证码。" });
      return;
    }
    setPendingAction("verify");
    try {
      await run((currentService, signal) => currentService.verifyEmailCode({
        email: emailValue,
        code: emailCode.trim(),
      }, signal), (receipt) => {
        setVerifiedEmail(emailValue);
        setFeedback({ kind: "success", message: `${receipt.message}。` });
      });
    } finally {
      setPendingAction(null);
    }
  }

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!service) {
      return;
    }
    const normalizedUsername = username.trim();
    if (normalizedUsername === "") {
      setFeedback(null);
      setUsernameError("账号不能为空或只包含空白字符。");
      return;
    }
    setUsernameError(null);
    if (password !== confirmation) {
      setFeedback({ kind: "error", message: "两次输入的密码不一致。" });
      return;
    }
    setVerifiedEmail(null);
    setPendingAction("register");
    try {
      await run((currentService, signal) => currentService.register({
        username: normalizedUsername,
        email: emailValue,
        password,
        email_code: emailCode.trim(),
      }, signal), (user) => {
        setPassword("");
        setConfirmation("");
        onDirtyChange(false);
        onRegistered(user);
      }, registrationErrorMessage);
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <div className="account-access-panel">
      {service === undefined && (
        <p className="account-access-note" role="status">
          演示模式不连接注册或邮件服务。切换到 API 模式并连接本地服务后，才能创建账号。
        </p>
      )}
      <form className="login-form" onSubmit={register}>
        <label htmlFor="register-username">账号</label>
        <input
          autoComplete="username"
          aria-describedby={usernameError === null ? undefined : "register-username-error"}
          aria-invalid={usernameError !== null}
          disabled={pending}
          id="register-username"
          name="username"
          onChange={(event) => {
            setUsername(event.currentTarget.value);
            setUsernameError(null);
            markDirty();
          }}
          required
          value={username}
        />
        {usernameError !== null && (
          <p className="form-error" id="register-username-error" role="alert">{usernameError}</p>
        )}
        <label htmlFor="register-email">邮箱</label>
        <input
          autoComplete="email"
          disabled={pending}
          id="register-email"
          name="email"
          onChange={(event) => handleEmailChange(event.currentTarget.value)}
          required
          type="email"
          value={email}
        />
        <button
          className="secondary-button account-code-button"
          disabled={!service || pending || emailValue === ""}
          onClick={() => { void sendCode(); }}
          type="button"
        >
          {pendingAction === "send" ? "正在发送…" : "发送邮箱验证码"}
        </button>
        <label htmlFor="register-email-code">邮箱验证码</label>
        <div className="account-code-row">
          <input
            autoComplete="one-time-code"
            disabled={pending}
            id="register-email-code"
            inputMode="numeric"
            maxLength={6}
            name="email_code"
            onChange={(event) => {
              setEmailCode(event.currentTarget.value);
              setVerifiedEmail(null);
              markDirty();
            }}
            required
            value={emailCode}
          />
          <button
            className="secondary-button"
            disabled={!service || pending || emailValue === "" || emailCode.trim() === "" || emailVerified}
            onClick={() => { void verifyCode(); }}
            type="button"
          >
            {emailVerified ? "已验证" : pendingAction === "verify" ? "正在校验…" : "校验"}
          </button>
        </div>
        <label htmlFor="register-password">密码</label>
        <input
          autoComplete="new-password"
          disabled={pending}
          id="register-password"
          name="password"
          onChange={(event) => { setPassword(event.currentTarget.value); markDirty(); }}
          required
          type="password"
          value={password}
        />
        <p className="account-field-hint">密码至少 8 位，并包含字母和数字。</p>
        <label htmlFor="register-password-confirm">确认密码</label>
        <input
          autoComplete="new-password"
          disabled={pending}
          id="register-password-confirm"
          name="password_confirmation"
          onChange={(event) => { setConfirmation(event.currentTarget.value); markDirty(); }}
          required
          type="password"
          value={confirmation}
        />
        {feedback !== null && (
          <p className={feedback.kind === "error" ? "form-error" : "form-success"} role={feedback.kind === "error" ? "alert" : "status"}>
            {feedback.message}
          </p>
        )}
        <button className="primary-button login-button" disabled={!service || pending} type="submit">
          {pendingAction === "register" ? "正在创建账号…" : "创建账号"}
        </button>
      </form>
    </div>
  );
}

export function ForgotPasswordForm({ service, onDirtyChange }: Omit<AccountAccessFormsProps, "onRegistered">) {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const { pending, feedback, setFeedback, run } = useAccountOperation(service);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!service) {
      return;
    }
    const input: ForgotPasswordInput = { username: username.trim(), email: normalizedEmail(email) };
    void run((currentService, signal) => currentService.requestPasswordReset(input, signal), (receipt) => {
      setFeedback({
        kind: "success",
        message: `接口响应“${receipt.message}”。该接口不披露账号是否匹配，也不能确认邮件已送达；若用户名和邮箱匹配，请检查收件箱与垃圾邮件。`,
      });
      onDirtyChange(false);
    });
  }

  return (
    <div className="account-access-panel">
      {service === undefined && (
        <p className="account-access-note" role="status">
          演示模式不连接密码重置服务。切换到 API 模式并连接本地服务后，才能申请重置链接。
        </p>
      )}
      <form className="login-form" onSubmit={handleSubmit}>
        <label htmlFor="forgot-username">账号</label>
        <input
          autoComplete="username"
          disabled={pending}
          id="forgot-username"
          name="username"
          onChange={(event) => { setUsername(event.currentTarget.value); onDirtyChange(true); setFeedback(null); }}
          required
          value={username}
        />
        <label htmlFor="forgot-email">注册邮箱</label>
        <input
          autoComplete="email"
          disabled={pending}
          id="forgot-email"
          name="email"
          onChange={(event) => { setEmail(event.currentTarget.value); onDirtyChange(true); setFeedback(null); }}
          required
          type="email"
          value={email}
        />
        {feedback !== null && (
          <p className={feedback.kind === "error" ? "form-error" : "form-success"} role={feedback.kind === "error" ? "alert" : "status"}>
            {feedback.message}
          </p>
        )}
        <button className="primary-button login-button" disabled={!service || pending} type="submit">
          {pending ? "正在提交…" : "申请重置链接"}
        </button>
      </form>
    </div>
  );
}

export function useUnsavedAccountForm(dirty: boolean) {
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  useEffect(() => {
    function warnBeforeLeaving(event: BeforeUnloadEvent) {
      if (!dirtyRef.current) {
        return;
      }
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, []);
}
