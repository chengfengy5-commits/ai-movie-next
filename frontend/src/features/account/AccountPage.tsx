import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { AccountProfileServicePort } from "../../shared/api/accountProfile";
import type { WorkspaceServices } from "../../shared/api/services";
import "./account.css";

type LoadStatus = "loading" | "ready" | "error";

interface ReadState<T> {
  status: LoadStatus;
  value: T | null;
  message: string | null;
}

interface BioDraft {
  text: string;
  baseline: string | null;
}

interface PendingVerification {
  targetBio: string;
  status: "checking" | "unresolved";
}

interface RequestTicket {
  controller: AbortController;
  generation: number;
  editorGeneration: number | null;
  userId: string;
  services: WorkspaceServices;
  profile: AccountProfileServicePort | null;
  refreshCurrentUser: (signal: AbortSignal) => Promise<User>;
  sessionGeneration: number | string;
}

export interface AccountPageProps {
  user: User;
  services: WorkspaceServices;
  profile: AccountProfileServicePort | null;
  refreshCurrentUser(signal: AbortSignal): Promise<User>;
  sessionGeneration: number | string;
  onUnauthorized(): void;
  onBack(): void;
  onDirtyChange?(dirty: boolean): void;
}

function parseTimestamp(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:?\d{2})?$/i.exec(value);
  if (match === null) return null;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText = "0", , zone] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const calendarDay = new Date(Date.UTC(year, month - 1, day));
  if (calendarDay.getUTCFullYear() !== year || calendarDay.getUTCMonth() !== month - 1
    || calendarDay.getUTCDate() !== day || hour > 23 || minute > 59 || second > 59) return null;
  if (zone !== undefined && zone.toUpperCase() !== "Z") {
    const offset = /[+-](\d{2}):?(\d{2})$/.exec(zone);
    if (offset === null || Number(offset[1]) > 23 || Number(offset[2]) > 59) return null;
  }
  const timestamp = Date.parse(zone === undefined ? `${value}Z` : value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function formatTimestamp(value: string): string {
  if (parseTimestamp(value) === null) return "时间格式无效，暂不可确认";
  const normalized = value.replace("T", " ").replace(/\.\d+(?=Z$|[+-]\d{2}:?\d{2}$|$)/i, "");
  if (/[zZ]$/.test(normalized)) return `${normalized.slice(0, -1)} UTC`;
  if (/[+-]\d{2}:?\d{2}$/.test(normalized)) return normalized;
  return `${normalized} UTC`;
}

function membershipDescription(user: User, now = Date.now()): string {
  if (user.is_superuser) return "管理员 · 不受会员期限限制";
  if (user.membership_type !== "premium") {
    return user.membership_type === "free" ? "普通账号" : `会员类型：${user.membership_type}`;
  }
  if (user.membership_expires_at === null) return "永久会员";
  const expiresAt = parseTimestamp(user.membership_expires_at);
  if (expiresAt === null) return "会员到期时间无效，状态暂不可确认";
  if (expiresAt <= now) return "会员已过期";
  return `会员 · 有效至 ${formatTimestamp(user.membership_expires_at)}`;
}

function initials(username: string): string {
  return Array.from(username.trim())[0] ?? "用";
}

function errorMessage(error: unknown, action: string): string {
  if (error instanceof ApiError && error.status === 403) {
    return `${action}被服务端拒绝；当前登录状态和草稿仍保留。`;
  }
  if (error instanceof ApiError && error.status === 401) {
    return "当前登录状态已失效，请重新登录。";
  }
  if (error instanceof ApiError && error.kind === "network") {
    return `${action}暂时无法连接服务，请检查服务状态后重试。`;
  }
  if (error instanceof ApiError && error.kind === "timeout") {
    return `${action}请求超时，请稍后重试或核对服务器状态。`;
  }
  return `${action}暂时无法确认，请保留当前内容并重试读取。`;
}

function isUnknownWriteOutcome(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  if (error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response") return true;
  return error.kind === "http" && (error.status === null || error.status >= 500);
}

function sameScope(
  ticket: RequestTicket,
  props: AccountPageProps,
  generation: number,
  editorGeneration: number,
): boolean {
  return ticket.generation === generation
    && (ticket.editorGeneration === null || ticket.editorGeneration === editorGeneration)
    && ticket.userId === props.user.id
    && ticket.services === props.services
    && ticket.profile === props.profile
    && ticket.refreshCurrentUser === props.refreshCurrentUser
    && ticket.sessionGeneration === props.sessionGeneration
    && !ticket.controller.signal.aborted;
}

export function AccountPage({
  user,
  services,
  profile,
  refreshCurrentUser,
  sessionGeneration,
  onUnauthorized,
  onBack,
  onDirtyChange,
}: AccountPageProps) {
  const currentProps: AccountPageProps = {
    user,
    services,
    profile,
    refreshCurrentUser,
    sessionGeneration,
    onUnauthorized,
    onBack,
    ...(onDirtyChange === undefined ? {} : { onDirtyChange }),
  };
  const propsRef = useRef(currentProps);
  propsRef.current = currentProps;

  const [profileRead, setProfileRead] = useState<ReadState<User>>({ status: "loading", value: user, message: null });
  const [creditsRead, setCreditsRead] = useState<ReadState<number>>({ status: "loading", value: null, message: null });
  const [draft, setDraft] = useState<BioDraft | null>(null);
  const [pendingVerification, setPendingVerification] = useState<PendingVerification | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const requestGeneration = useRef(0);
  const editorGeneration = useRef(0);
  const profileReadGeneration = useRef(0);
  const profileReadController = useRef<AbortController | null>(null);
  const savingOwner = useRef<AbortController | null>(null);
  const controllers = useRef(new Set<AbortController>());
  const editorControllers = useRef(new Set<AbortController>());
  const unauthorizedNotifiedGeneration = useRef<number | null>(null);

  const beginRequest = useCallback((editorOwned = false): RequestTicket => {
    const current = propsRef.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    if (editorOwned) editorControllers.current.add(controller);
    return {
      controller,
      generation: requestGeneration.current,
      editorGeneration: editorOwned ? editorGeneration.current : null,
      userId: current.user.id,
      services: current.services,
      profile: current.profile,
      refreshCurrentUser: current.refreshCurrentUser,
      sessionGeneration: current.sessionGeneration,
    };
  }, []);

  const isCurrent = useCallback((ticket: RequestTicket): boolean => (
    sameScope(ticket, propsRef.current, requestGeneration.current, editorGeneration.current)
  ), []);

  const finishRequest = useCallback((ticket: RequestTicket) => {
    controllers.current.delete(ticket.controller);
    editorControllers.current.delete(ticket.controller);
    if (profileReadController.current === ticket.controller) profileReadController.current = null;
  }, []);

  const claimProfileRead = useCallback((ticket: RequestTicket): number => {
    const previous = profileReadController.current;
    if (previous !== null && previous !== ticket.controller) previous.abort();
    profileReadController.current = ticket.controller;
    profileReadGeneration.current += 1;
    return profileReadGeneration.current;
  }, []);

  const isCurrentProfileRead = useCallback((ticket: RequestTicket, readGeneration: number): boolean => (
    isCurrent(ticket)
    && profileReadGeneration.current === readGeneration
    && profileReadController.current === ticket.controller
  ), [isCurrent]);

  const notifyUnauthorized = useCallback((ticket: RequestTicket) => {
    if (!isCurrent(ticket) || unauthorizedNotifiedGeneration.current === ticket.generation) return;
    unauthorizedNotifiedGeneration.current = ticket.generation;
    propsRef.current.onUnauthorized();
  }, [isCurrent]);

  const refreshProfile = useCallback(async () => {
    const ticket = beginRequest();
    const readGeneration = claimProfileRead(ticket);
    setProfileRead((current) => ({ ...current, status: "loading", message: null }));
    try {
      const fresh = await ticket.refreshCurrentUser(ticket.controller.signal);
      if (!isCurrentProfileRead(ticket, readGeneration)) return;
      if (fresh.id !== ticket.userId) {
        throw new ApiError("invalid-response", "账户资料回读与当前用户不匹配。");
      }
      setProfileRead({ status: "ready", value: fresh, message: null });
    } catch (error) {
      if (!isCurrentProfileRead(ticket, readGeneration)) return;
      if (error instanceof ApiError && error.status === 401) notifyUnauthorized(ticket);
      setProfileRead((current) => ({
        ...current,
        status: "error",
        message: errorMessage(error, "账户资料读取"),
      }));
    } finally {
      finishRequest(ticket);
    }
  }, [beginRequest, claimProfileRead, finishRequest, isCurrentProfileRead, notifyUnauthorized]);

  const refreshCredits = useCallback(async () => {
    const ticket = beginRequest();
    if (ticket.profile === null) {
      setCreditsRead({ status: "error", value: null, message: "当前运行模式尚未接入真实积分读取。" });
      finishRequest(ticket);
      return;
    }
    setCreditsRead((current) => ({ ...current, status: "loading", message: null }));
    try {
      const snapshot = await ticket.profile.getCredits(ticket.controller.signal);
      if (!isCurrent(ticket)) return;
      if (!Number.isSafeInteger(snapshot.credits)) {
        throw new ApiError("invalid-response", "服务器返回的积分余额无效。");
      }
      setCreditsRead({ status: "ready", value: snapshot.credits, message: null });
    } catch (error) {
      if (!isCurrent(ticket)) return;
      if (error instanceof ApiError && error.status === 401) notifyUnauthorized(ticket);
      setCreditsRead((current) => ({
        ...current,
        status: "error",
        message: errorMessage(error, "积分读取"),
      }));
    } finally {
      finishRequest(ticket);
    }
  }, [beginRequest, finishRequest, isCurrent, notifyUnauthorized]);

  const verifyWriteTarget = useCallback(async (targetBio: string, ticket: RequestTicket) => {
    if (!isCurrent(ticket)) return;
    const readGeneration = claimProfileRead(ticket);
    setPendingVerification({ targetBio, status: "checking" });
    try {
      const fresh = await ticket.refreshCurrentUser(ticket.controller.signal);
      if (!isCurrentProfileRead(ticket, readGeneration)) return;
      if (fresh.id !== ticket.userId) {
        throw new ApiError("invalid-response", "账户资料回读与当前用户不匹配。");
      }
      setProfileRead({ status: "ready", value: fresh, message: null });
      if ((fresh.bio ?? "") === targetBio) {
        setDraft(null);
        setPendingVerification(null);
        setSaveError(null);
        setSaveMessage("简介已由独立账户读取确认。");
      } else {
        setPendingVerification({ targetBio, status: "unresolved" });
        setSaveError("当前简介与本次提交内容不同。草稿已保留；只能继续核对，不能自动重发。");
      }
    } catch (error) {
      if (!isCurrentProfileRead(ticket, readGeneration)) return;
      if (error instanceof ApiError && error.status === 401) notifyUnauthorized(ticket);
      setProfileRead((current) => ({
        ...current,
        status: "error",
        message: errorMessage(error, "账户资料核对"),
      }));
      setPendingVerification({ targetBio, status: "unresolved" });
      setSaveError("保存结果仍未确认。草稿已保留；请只读核对服务器状态，不要重复提交。");
    }
  }, [claimProfileRead, isCurrent, isCurrentProfileRead, notifyUnauthorized]);

  useLayoutEffect(() => {
    requestGeneration.current += 1;
    const generation = requestGeneration.current;
    profileReadGeneration.current += 1;
    profileReadController.current = null;
    for (const controller of controllers.current) controller.abort();
    controllers.current.clear();
    editorControllers.current.clear();
    unauthorizedNotifiedGeneration.current = null;
    propsRef.current.onDirtyChange?.(false);
    setProfileRead({ status: "loading", value: user, message: null });
    setCreditsRead({ status: "loading", value: null, message: null });
    setDraft(null);
    setPendingVerification(null);
    savingOwner.current = null;
    setSaving(false);
    setSaveError(null);
    setSaveMessage(null);
    void refreshProfile();
    if (profile === null) {
      setCreditsRead({ status: "error", value: null, message: "当前运行模式尚未接入真实积分读取。" });
    } else {
      void refreshCredits();
    }
    return () => {
      if (requestGeneration.current === generation) requestGeneration.current += 1;
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
    };
  }, [services, user.id, profile, refreshCurrentUser, sessionGeneration, refreshProfile, refreshCredits]);

  useEffect(() => {
    setProfileRead((current) => ({ ...current, value: user }));
  }, [user]);

  const isDirty = (draft !== null && draft.text !== (draft.baseline ?? "")) || pendingVerification !== null;
  useEffect(() => {
    onDirtyChange?.(isDirty);
  }, [isDirty, onDirtyChange]);

  function openBioEditor() {
    retireEditorRequests();
    const currentUser = profileRead.value ?? user;
    setDraft({ text: currentUser.bio ?? "", baseline: currentUser.bio });
    setPendingVerification(null);
    setSaveError(null);
    setSaveMessage(null);
  }

  function cancelBioEditor() {
    if (pendingVerification !== null) {
      const confirmed = window.confirm(
        "本次简介写入结果仍未确认，原请求可能稍后完成。放弃本次核对并退出编辑？",
      );
      if (!confirmed) return;
      setPendingVerification(null);
    } else if (draft !== null && draft.text !== (draft.baseline ?? "")) {
      if (!window.confirm("放弃尚未保存的简介修改？")) return;
    }
    retireEditorRequests();
    setDraft(null);
    setSaveError(null);
    setSaveMessage(null);
  }

  async function submitBio(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const activeDraft = draft;
    const ticket = beginRequest(true);
    const service = ticket.profile;
    if (activeDraft === null || service === null || saving || pendingVerification !== null) {
      finishRequest(ticket);
      return;
    }
    if (activeDraft.text === (activeDraft.baseline ?? "")) {
      setDraft(null);
      setSaveError(null);
      setSaveMessage(null);
      finishRequest(ticket);
      return;
    }

    savingOwner.current = ticket.controller;
    setSaving(true);
    setSaveError(null);
    setSaveMessage(null);
    try {
      const receipt = await service.updateProfile({ bio: activeDraft.text }, ticket.controller.signal);
      if (!isCurrent(ticket)) return;
      if (receipt.id !== ticket.userId) {
        throw new ApiError("invalid-response", "简介保存回执与当前用户不匹配。");
      }
      await verifyWriteTarget(activeDraft.text, ticket);
    } catch (error) {
      if (!isCurrent(ticket)) return;
      if (error instanceof ApiError && error.status === 401) notifyUnauthorized(ticket);
      if (isUnknownWriteOutcome(error)) {
        setSaveError("简介写入结果未知，已保留草稿并开始只读核对；不会自动重复提交。");
        await verifyWriteTarget(activeDraft.text, ticket);
      } else {
        setSaveError(errorMessage(error, "简介保存"));
      }
    } finally {
      if (savingOwner.current === ticket.controller) {
        savingOwner.current = null;
        setSaving(false);
      }
      finishRequest(ticket);
    }
  }

  async function retryPendingVerification() {
    if (pendingVerification === null) return;
    const ticket = beginRequest(true);
    await verifyWriteTarget(pendingVerification.targetBio, ticket);
    finishRequest(ticket);
  }

  function handleKeyboard(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && draft !== null) {
      event.preventDefault();
      cancelBioEditor();
    }
  }

  function retireEditorRequests() {
    editorGeneration.current += 1;
    const previousSavingOwner = savingOwner.current;
    if (previousSavingOwner !== null && editorControllers.current.has(previousSavingOwner)) {
      savingOwner.current = null;
      setSaving(false);
    }
    const retiredProfileRead = profileReadController.current !== null
      && editorControllers.current.has(profileReadController.current);
    if (retiredProfileRead) {
      setProfileRead((current) => current.status === "loading"
        ? { ...current, status: "error", message: "账户资料读取已中断，请重试读取。" }
        : current);
    }
    for (const controller of editorControllers.current) {
      controller.abort();
      controllers.current.delete(controller);
      if (profileReadController.current === controller) profileReadController.current = null;
    }
    editorControllers.current.clear();
  }

  const currentUser = profileRead.value ?? user;
  const bioEditing = draft !== null;

  return (
    <section className="account-page" aria-labelledby="account-page-title" onKeyDown={handleKeyboard}>
      <header className="account-page-header">
        <div>
          <p className="eyebrow">账户</p>
          <h1 id="account-page-title">我的账户</h1>
          <p>查看本人资料、会员状态与平台积分。</p>
        </div>
        <button className="secondary-button account-back-button" onClick={() => onBack()} type="button">
          返回工作区
        </button>
      </header>

      <div className="account-page-grid">
        <section className="account-card account-profile-card" aria-labelledby="account-profile-title">
          <div className="account-profile-heading">
            <div className="account-avatar" aria-label="头像首字" role="img">{initials(currentUser.username)}</div>
            <div>
              <p className="eyebrow">本人资料</p>
              <h2 id="account-profile-title">账户资料</h2>
            </div>
          </div>
          <dl className="account-facts">
            <div><dt>账号</dt><dd>{currentUser.username}</dd></div>
            <div><dt>邮箱</dt><dd>{currentUser.email}</dd></div>
            <div><dt>注册时间</dt><dd>{formatTimestamp(currentUser.created_at)}</dd></div>
            <div><dt>会员状态</dt><dd>{membershipDescription(currentUser)}</dd></div>
          </dl>
          <div className="account-bio-section">
            <div className="account-section-heading">
              <h3>个人简介</h3>
              {!bioEditing && profile !== null && (
                <button className="text-button" onClick={openBioEditor} type="button">编辑简介</button>
              )}
            </div>
            {bioEditing ? (
              <form className="account-bio-form" onSubmit={submitBio}>
                <label className="account-bio-label" htmlFor="account-bio-input">个人简介</label>
                <button
                  className="text-button account-editor-refresh"
                  disabled={profileRead.status === "loading" || saving || pendingVerification !== null}
                  onClick={() => void refreshProfile()}
                  type="button"
                >
                  {profileRead.status === "loading" ? "正在刷新资料…" : "刷新服务器资料"}
                </button>
                <textarea
                  id="account-bio-input"
                  value={draft.text}
                  readOnly={saving || pendingVerification !== null}
                  rows={5}
                  onChange={(event) => setDraft((current) => current === null
                    ? current
                    : { ...current, text: event.target.value })}
                />
                {(currentUser.bio ?? "") !== (draft.baseline ?? "") && (
                  <p className="account-verification-notice" role="status">
                    服务器资料已刷新，当前草稿仍保留。
                  </p>
                )}
                {pendingVerification !== null && (
                  <div className="account-verification-notice" role="status">
                    {pendingVerification.status === "checking"
                      ? "正在独立读取账户资料核对本次保存。"
                      : "保存结果仍不明确；下方操作只读取状态，不会重复写入。"}
                  </div>
                )}
                {saveError !== null && <p className="account-error" role="alert">{saveError}</p>}
                {saveMessage !== null && <p className="account-success" role="status">{saveMessage}</p>}
                <div className="account-form-actions">
                  {pendingVerification !== null ? (
                    <>
                      <button
                        className="primary-button"
                        disabled={pendingVerification.status === "checking"}
                        onClick={() => void retryPendingVerification()}
                        type="button"
                      >只核对服务器状态</button>
                      <button className="secondary-button" onClick={cancelBioEditor} type="button">
                        放弃本次核对并退出编辑
                      </button>
                    </>
                  ) : (
                    <>
                      <button className="primary-button" disabled={saving} type="submit">
                        {saving ? "正在保存…" : "保存简介"}
                      </button>
                      <button className="secondary-button" disabled={saving} onClick={cancelBioEditor} type="button">
                        取消
                      </button>
                    </>
                  )}
                </div>
              </form>
            ) : (
              <p className="account-bio-value">{currentUser.bio || "尚未填写简介。"}</p>
            )}
            {!bioEditing && saveMessage !== null && <p className="account-success" role="status">{saveMessage}</p>}
            {!bioEditing && profile === null && (
              <p className="account-muted" role="status">账户资料编辑服务尚未接入，目前仅显示已验证的账户信息。</p>
            )}
            {profileRead.message !== null && (
              <div className="account-load-message" role="alert">
                <p>{profileRead.message}</p>
                <button
                  className="text-button"
                  disabled={profileRead.status === "loading" || saving || pendingVerification !== null}
                  onClick={() => void refreshProfile()}
                  type="button"
                >
                  {profileRead.status === "loading" ? "正在刷新…" : "重试读取资料"}
                </button>
              </div>
            )}
            {bioEditing && saveError === null && profileRead.message === null && (
              <p className="account-help">只修改简介；账号、邮箱、头像与会员信息为只读。</p>
            )}
            {!bioEditing && profileRead.status === "loading" && (
              <p className="account-muted" role="status">正在核对最新账户资料…</p>
            )}
            {!bioEditing && profileRead.status === "ready" && (
              <button className="text-button account-refresh-profile" onClick={() => void refreshProfile()} type="button">
                刷新账户资料
              </button>
            )}
          </div>
        </section>

        <section className="account-card account-credits-card" aria-labelledby="account-credits-title">
          <div className="account-section-heading">
            <div>
              <p className="eyebrow">平台账户</p>
              <h2 id="account-credits-title">平台积分</h2>
            </div>
            {profile !== null && (
              <button className="text-button" disabled={creditsRead.status === "loading"} onClick={() => void refreshCredits()} type="button">
                {creditsRead.status === "loading" ? "读取中…" : "刷新"}
              </button>
            )}
          </div>
          {creditsRead.status === "ready" && creditsRead.value !== null ? (
            <p className="account-credit-balance"><strong>{creditsRead.value.toLocaleString("zh-CN")}</strong><span>积分</span></p>
          ) : creditsRead.status === "loading" ? (
            <p className="account-muted" role="status">正在读取积分余额…</p>
          ) : (
            <div className="account-load-message" role="alert">
              <p>{creditsRead.message ?? "积分余额暂不可用。"}</p>
              {profile !== null && (
                <button className="text-button" onClick={() => void refreshCredits()} type="button">
                  重试读取积分
                </button>
              )}
            </div>
          )}
          <p className="account-help">此处显示平台积分余额；不包含外部服务商余额或积分流水。</p>
        </section>
      </div>
    </section>
  );
}
