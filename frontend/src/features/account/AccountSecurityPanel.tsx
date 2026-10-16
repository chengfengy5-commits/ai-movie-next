import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import type { User } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import {
  parseAccountPasswordChangeReceipt,
  parseAccountSessionRevocationReceipt,
  type AccountSecurityServicePort,
  type AccountSession,
} from "../../shared/api/accountSecurity";
import "./accountSecurity.css";

export interface AccountSecurityPanelProps {
  userId: string;
  servicesIdentity: object;
  service: AccountSecurityServicePort | null;
  sessionGeneration: number | string;
  refreshCurrentUser(signal: AbortSignal): Promise<User>;
  onUnauthorized(): void;
  onLogout(): void;
  onBeforeSecurityMutation(): boolean | Promise<boolean>;
  /** Synchronous final admission immediately before establishing the password write owner. */
  onPasswordMutationAdmission?(): boolean;
  onDirtyChange?(dirty: boolean): void;
  onPasswordMutationOwnerChange?(owner: object, active: boolean): void;
}

type SessionReadStatus = "loading" | "ready" | "error";

interface SessionReadState {
  status: SessionReadStatus;
  sessions: AccountSession[];
  message: string | null;
}

interface ScopeTicket {
  controller: AbortController;
  generation: number;
  userId: string;
  servicesIdentity: object;
  service: AccountSecurityServicePort | null;
  sessionGeneration: number | string;
  refreshCurrentUser: AccountSecurityPanelProps["refreshCurrentUser"];
}

interface UnknownRevocation {
  sessionId: string;
  deviceName: string;
  message: string;
}

interface ReadOutcome {
  ok: boolean;
  sessions: AccountSession[];
}

function sameScope(ticket: ScopeTicket, props: AccountSecurityPanelProps, generation: number): boolean {
  return ticket.generation === generation
    && ticket.userId === props.userId
    && ticket.servicesIdentity === props.servicesIdentity
    && ticket.service === props.service
    && ticket.sessionGeneration === props.sessionGeneration
    && ticket.refreshCurrentUser === props.refreshCurrentUser
    && !ticket.controller.signal.aborted;
}

function errorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}

function isUncertainWriteError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  if (error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response") return true;
  if (error.kind === "aborted") return true;
  return error.kind === "http" && (error.status === null || error.status >= 500);
}

function sessionTime(value: string | null): string {
  if (value === null) return "未知";
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:?\d{2})?$/i.exec(value);
  if (match === null) return "未知";

  const [, yearText, monthText, dayText, hourText, minuteText, secondText = "0", , zone] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const calendarDay = new Date(Date.UTC(year, month - 1, day));
  if (calendarDay.getUTCFullYear() !== year || calendarDay.getUTCMonth() !== month - 1
    || calendarDay.getUTCDate() !== day || hour > 23 || minute > 59 || second > 59) return "未知";
  if (zone !== undefined && zone.toUpperCase() !== "Z") {
    const offset = /[+-](\d{2}):?(\d{2})$/.exec(zone);
    if (offset === null || Number(offset[1]) > 23 || Number(offset[2]) > 59) return "未知";
  }
  if (!Number.isFinite(Date.parse(zone === undefined ? `${value}Z` : value))) return "未知";

  const normalized = value.replace("T", " ").replace(/\.\d+(?=Z$|[+-]\d{2}:?\d{2}$|$)/i, "");
  if (/[zZ]$/.test(normalized)) return `${normalized.slice(0, -1)} UTC`;
  if (/[+-]\d{2}:?\d{2}$/.test(normalized)) return normalized;
  return `${normalized} UTC`;
}

function safeReadError(error: unknown): string {
  const status = errorStatus(error);
  if (status === 401) return "登录状态已失效，请重新登录后再查看登录设备。";
  if (status === 403) return "当前账号无权读取登录设备，登录状态保持不变。";
  return "暂时无法读取登录设备，请稍后重试。";
}

export function AccountSecurityPanel({
  userId,
  servicesIdentity,
  service,
  sessionGeneration,
  refreshCurrentUser,
  onUnauthorized,
  onLogout,
  onBeforeSecurityMutation,
  onPasswordMutationAdmission,
  onDirtyChange,
  onPasswordMutationOwnerChange,
}: AccountSecurityPanelProps) {
  const currentProps: AccountSecurityPanelProps = {
    userId,
    servicesIdentity,
    service,
    sessionGeneration,
    refreshCurrentUser,
    onUnauthorized,
    onLogout,
    onBeforeSecurityMutation,
    ...(onPasswordMutationAdmission === undefined ? {} : { onPasswordMutationAdmission }),
    ...(onDirtyChange === undefined ? {} : { onDirtyChange }),
    ...(onPasswordMutationOwnerChange === undefined ? {} : { onPasswordMutationOwnerChange }),
  };
  const propsRef = useRef(currentProps);
  propsRef.current = currentProps;

  const [sessionRead, setSessionRead] = useState<SessionReadState>({
    status: "loading",
    sessions: [],
    message: null,
  });
  const [revokeMessage, setRevokeMessage] = useState<string | null>(null);
  const [pendingRevoke, setPendingRevoke] = useState<AccountSession | null>(null);
  const [unknownRevocation, setUnknownRevocation] = useState<UnknownRevocation | null>(null);
  const [writeBusy, setWriteBusy] = useState(false);
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmedPassword, setConfirmedPassword] = useState("");
  const [passwordPreparing, setPasswordPreparing] = useState(false);
  const [passwordConfirmationOpen, setPasswordConfirmationOpen] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordMessage, setPasswordMessage] = useState<string | null>(null);
  const [passwordOutcomeUnknown, setPasswordOutcomeUnknown] = useState(false);
  const [meCheckBusy, setMeCheckBusy] = useState(false);
  const [meCheckMessage, setMeCheckMessage] = useState<string | null>(null);

  const lifecycleGeneration = useRef(0);
  const readGeneration = useRef(0);
  const activeReadController = useRef<AbortController | null>(null);
  const controllers = useRef(new Set<AbortController>());
  const writeOwner = useRef<AbortController | null>(null);
  const passwordMutationOwner = useRef<object | null>(null);
  const unauthorizedScope = useRef<{
    userId: string;
    service: AccountSecurityServicePort | null;
    servicesIdentity: object;
    sessionGeneration: number | string;
  } | null>(null);
  const revokeConfirmRef = useRef<HTMLButtonElement>(null);
  const passwordConfirmRef = useRef<HTMLButtonElement>(null);
  const revokeTriggerRef = useRef<HTMLButtonElement | null>(null);
  const passwordSubmitRef = useRef<HTMLButtonElement>(null);
  const sessionsHeadingRef = useRef<HTMLHeadingElement>(null);
  const passwordHeadingRef = useRef<HTMLHeadingElement>(null);
  const revokeDialogWasOpen = useRef(false);
  const passwordDialogWasOpen = useRef(false);

  function releasePasswordMutationOwner(owner: object): void {
    if (passwordMutationOwner.current !== owner) return;
    passwordMutationOwner.current = null;
    propsRef.current.onPasswordMutationOwnerChange?.(owner, false);
  }

  const beginTicket = useCallback((): ScopeTicket => {
    const current = propsRef.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    return {
      controller,
      generation: lifecycleGeneration.current,
      userId: current.userId,
      servicesIdentity: current.servicesIdentity,
      service: current.service,
      sessionGeneration: current.sessionGeneration,
      refreshCurrentUser: current.refreshCurrentUser,
    };
  }, []);

  const isCurrent = useCallback((ticket: ScopeTicket): boolean => (
    sameScope(ticket, propsRef.current, lifecycleGeneration.current)
  ), []);

  const finishTicket = useCallback((ticket: ScopeTicket) => {
    controllers.current.delete(ticket.controller);
    if (activeReadController.current === ticket.controller) activeReadController.current = null;
  }, []);

  const abortRead = useCallback(() => {
    readGeneration.current += 1;
    activeReadController.current?.abort();
    activeReadController.current = null;
    setSessionRead((state) => state.status === "loading"
      ? { ...state, status: "error", message: "读取已中断，可重试读取设备列表。" }
      : state);
  }, []);

  const notifyUnauthorized = useCallback((ticket: ScopeTicket) => {
    if (!isCurrent(ticket)) return;
    const previous = unauthorizedScope.current;
    if (previous !== null
      && previous.userId === ticket.userId
      && previous.service === ticket.service
      && previous.servicesIdentity === ticket.servicesIdentity
      && previous.sessionGeneration === ticket.sessionGeneration) return;
    unauthorizedScope.current = {
      userId: ticket.userId,
      service: ticket.service,
      servicesIdentity: ticket.servicesIdentity,
      sessionGeneration: ticket.sessionGeneration,
    };
    propsRef.current.onUnauthorized();
  }, [isCurrent]);

  const loadSessions = useCallback(async (): Promise<ReadOutcome> => {
    const current = propsRef.current;
    if (current.service === null) {
      setSessionRead({ status: "ready", sessions: [], message: null });
      return { ok: false, sessions: [] };
    }

    activeReadController.current?.abort();
    const readId = ++readGeneration.current;
    const ticket = beginTicket();
    activeReadController.current = ticket.controller;
    setSessionRead((state) => ({ ...state, status: "loading", message: null }));

    try {
      const sessions = await current.service.listSessions(ticket.controller.signal);
      if (!isCurrent(ticket) || readGeneration.current !== readId) return { ok: false, sessions: [] };
      setSessionRead({ status: "ready", sessions, message: null });
      return { ok: true, sessions };
    } catch (error) {
      if (!isCurrent(ticket) || readGeneration.current !== readId) return { ok: false, sessions: [] };
      if (errorStatus(error) === 401) notifyUnauthorized(ticket);
      const message = safeReadError(error);
      setSessionRead((state) => ({ ...state, status: "error", message }));
      return { ok: false, sessions: [] };
    } finally {
      finishTicket(ticket);
    }
  }, [beginTicket, finishTicket, isCurrent, notifyUnauthorized]);

  async function reconcileUnknownRevocation(ticket: ScopeTicket, uncertain: UnknownRevocation): Promise<void> {
    const outcome = await loadSessions();
    if (!isCurrent(ticket)) return;

    let message = "撤销结果未确定；设备列表状态见上方。请勿重试撤销。";
    if (outcome.ok && outcome.sessions.some((session) => session.id === uncertain.sessionId)) {
      message = "撤销结果未确定；设备仍在列表中，这不能确认原请求已结束。请勿重试。";
    } else if (outcome.ok && outcome.sessions.length >= 20) {
      message = "撤销结果未确定；列表最多返回20条，缺少目标不能证明设备已下线。请勿重试。";
    } else if (outcome.ok) {
      message = "撤销结果未确定；设备当前不在有效列表中，但无法确认是否由本次请求撤销。请勿重试。";
    }
    setUnknownRevocation((current) => current === uncertain ? { ...uncertain, message } : current);
  }

  async function recheckUnknownRevocation(): Promise<void> {
    if (unknownRevocation === null) return;
    const uncertain = unknownRevocation;
    const ticket = beginTicket();
    try {
      await reconcileUnknownRevocation(ticket, uncertain);
    } finally {
      finishTicket(ticket);
    }
  }

  useLayoutEffect(() => {
    lifecycleGeneration.current += 1;
    const ownedGeneration = lifecycleGeneration.current;
    for (const controller of controllers.current) controller.abort();
    controllers.current.clear();
    activeReadController.current = null;
    readGeneration.current += 1;
    writeOwner.current = null;
    passwordMutationOwner.current = null;
    unauthorizedScope.current = null;
    setSessionRead({ status: "loading", sessions: [], message: null });
    setRevokeMessage(null);
    setPendingRevoke(null);
    setUnknownRevocation(null);
    setWriteBusy(false);
    setOldPassword("");
    setNewPassword("");
    setConfirmedPassword("");
    setPasswordPreparing(false);
    setPasswordConfirmationOpen(false);
    setPasswordError(null);
    setPasswordMessage(null);
    setPasswordOutcomeUnknown(false);
    setMeCheckBusy(false);
    setMeCheckMessage(null);

    return () => {
      if (lifecycleGeneration.current === ownedGeneration) lifecycleGeneration.current += 1;
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
      activeReadController.current = null;
      readGeneration.current += 1;
      writeOwner.current = null;
      const retiredPasswordMutationOwner = passwordMutationOwner.current;
      if (retiredPasswordMutationOwner !== null) {
        passwordMutationOwner.current = null;
        propsRef.current.onPasswordMutationOwnerChange?.(retiredPasswordMutationOwner, false);
      }
      propsRef.current.onDirtyChange?.(false);
    };
  }, [userId, servicesIdentity, service, sessionGeneration, refreshCurrentUser]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions, userId, servicesIdentity, service, sessionGeneration, refreshCurrentUser]);

  useEffect(() => {
    if (pendingRevoke !== null) {
      revokeDialogWasOpen.current = true;
      revokeConfirmRef.current?.focus();
    } else if (revokeDialogWasOpen.current) {
      revokeDialogWasOpen.current = false;
      const trigger = revokeTriggerRef.current;
      if (trigger?.isConnected && !trigger.disabled) trigger.focus();
      else sessionsHeadingRef.current?.focus();
    }
    if (passwordConfirmationOpen) {
      passwordDialogWasOpen.current = true;
      passwordConfirmRef.current?.focus();
    } else if (passwordDialogWasOpen.current) {
      passwordDialogWasOpen.current = false;
      const submit = passwordSubmitRef.current;
      if (submit?.isConnected && !submit.disabled) submit.focus();
      else passwordHeadingRef.current?.focus();
    }
  }, [pendingRevoke, passwordConfirmationOpen]);

  const hasUnresolvedOutcome = unknownRevocation !== null || passwordOutcomeUnknown;
  const securityDirty = oldPassword !== ""
    || newPassword !== ""
    || confirmedPassword !== ""
    || passwordPreparing
    || passwordConfirmationOpen
    || writeBusy
    || pendingRevoke !== null
    || unknownRevocation !== null
    || passwordOutcomeUnknown
    || meCheckBusy;

  useEffect(() => {
    onDirtyChange?.(securityDirty);
  }, [onDirtyChange, securityDirty]);

  useEffect(() => () => {
    propsRef.current.onDirtyChange?.(false);
  }, []);

  function beginWrite(): ScopeTicket | null {
    if (writeOwner.current !== null || hasUnresolvedOutcome) return null;
    const current = propsRef.current;
    if (current.service === null) return null;
    abortRead();
    const ticket = beginTicket();
    writeOwner.current = ticket.controller;
    setWriteBusy(true);
    return ticket;
  }

  function finishWrite(ticket: ScopeTicket): void {
    if (writeOwner.current === ticket.controller) {
      writeOwner.current = null;
      if (isCurrent(ticket)) setWriteBusy(false);
    }
    finishTicket(ticket);
  }

  async function confirmRevoke(): Promise<void> {
    const target = pendingRevoke;
    setPendingRevoke(null);
    setRevokeMessage(null);
    if (target === null) return;

    const ticket = beginWrite();
    if (ticket === null) return;
    const currentService = ticket.service;
    if (currentService === null) {
      finishWrite(ticket);
      return;
    }

    try {
      const response = await currentService.revokeSession(target.id, ticket.controller.signal);
      const receipt = parseAccountSessionRevocationReceipt(response, target.id);
      if (!isCurrent(ticket)) return;

      if (target.isCurrent) {
        setRevokeMessage(`${receipt.message}。正在退出当前登录。`);
        propsRef.current.onLogout();
        return;
      }

      const outcome = await loadSessions();
      if (!isCurrent(ticket)) return;
      setRevokeMessage(outcome.ok
        ? `${receipt.message}，设备列表已更新。`
        : `${receipt.message}，但设备列表暂时无法更新；可稍后只读刷新。`);
    } catch (error) {
      if (!isCurrent(ticket)) return;
      const status = errorStatus(error);
      if (status === 401) {
        setRevokeMessage("登录状态已失效，请重新登录。未将本次设备撤销记作成功。");
        notifyUnauthorized(ticket);
        return;
      }
      if (status === 403) {
        setRevokeMessage("当前账号无权撤销该设备，登录状态保持不变。");
        return;
      }
      if (status === 404) {
        const outcome = await loadSessions();
        if (!isCurrent(ticket)) return;
        setRevokeMessage(outcome.ok
          ? "设备当前不存在或不属于本人；已刷新列表，未将本次操作记作成功。"
          : "设备当前不存在或不属于本人；列表刷新失败，未将本次操作记作成功。可重试读取。");
        return;
      }
      if (!isUncertainWriteError(error)) {
        setRevokeMessage("服务器未确认撤销该设备；登录状态保持不变，可读取列表后再处理。");
        return;
      }

      const uncertain: UnknownRevocation = {
        sessionId: target.id,
        deviceName: target.deviceName,
        message: "撤销结果未确定。请勿重试；正在只读核对设备列表。",
      };
      setUnknownRevocation(uncertain);
      await reconcileUnknownRevocation(ticket, uncertain);
    } finally {
      finishWrite(ticket);
    }
  }

  function abandonUnknownRevocation(): void {
    setUnknownRevocation(null);
    setRevokeMessage("你已明确放弃此次撤销核对；先前结果仍未确定，列表可继续只读刷新。");
  }

  async function preparePasswordChange(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setPasswordError(null);
    setPasswordMessage(null);
    if (writeOwner.current !== null || hasUnresolvedOutcome || service === null) return;

    if (oldPassword === "" || newPassword === "" || confirmedPassword === "") {
      setPasswordError("请填写当前密码、新密码和确认密码。");
      return;
    }
    if (Array.from(newPassword).length < 6) {
      setPasswordError("新密码至少需要6个字符。");
      return;
    }
    if (oldPassword === newPassword) {
      setPasswordError("新密码不能与当前密码相同。");
      return;
    }
    if (newPassword !== confirmedPassword) {
      setPasswordError("两次输入的新密码不一致。");
      return;
    }

    const ticket = beginTicket();
    setPasswordPreparing(true);
    try {
      const allowed = await propsRef.current.onBeforeSecurityMutation();
      if (!isCurrent(ticket)) return;
      if (!allowed) {
        setPasswordError("请先保存或明确放弃未保存的账户简介，再修改密码。");
        return;
      }
      setPasswordConfirmationOpen(true);
    } catch {
      if (isCurrent(ticket)) setPasswordError("暂时无法确认账户简介状态，请先处理简介草稿后重试。");
    } finally {
      if (isCurrent(ticket)) setPasswordPreparing(false);
      finishTicket(ticket);
    }
  }

  async function changePassword(): Promise<void> {
    setPasswordConfirmationOpen(false);
    const ticket = beginWrite();
    if (ticket === null || ticket.service === null) return;
    setPasswordError(null);
    setPasswordMessage(null);

    const permissionCheck = propsRef.current.onBeforeSecurityMutation;
    const admissionCheck = propsRef.current.onPasswordMutationAdmission;
    let checkingPermission = true;
    try {
      const allowed = await permissionCheck();
      if (!isCurrent(ticket)) return;
      if (propsRef.current.onBeforeSecurityMutation !== permissionCheck
        || propsRef.current.onPasswordMutationAdmission !== admissionCheck
        || writeOwner.current !== ticket.controller) {
        setPasswordError("账户状态已变化，请重新确认简介状态后再试。");
        return;
      }
      if (!allowed) {
        setPasswordError("请先保存或明确放弃未保存的账户简介，再修改密码。");
        return;
      }

      if (passwordMutationOwner.current !== null) {
        setPasswordError("已有密码修改结果待处理，请先退出或重新登录后再继续。");
        return;
      }

      if (admissionCheck !== undefined && !admissionCheck()) {
        setPasswordError("会员兑换已进入最终授权或请求阶段；请等待结果明确后再修改密码。");
        return;
      }

      passwordMutationOwner.current = ticket.controller;
      propsRef.current.onPasswordMutationOwnerChange?.(ticket.controller, true);
      checkingPermission = false;

      const response = await ticket.service.changePassword(
        { old_password: oldPassword, new_password: newPassword },
        ticket.controller.signal,
      );
      const receipt = parseAccountPasswordChangeReceipt(response);
      if (!isCurrent(ticket)) return;
      setOldPassword("");
      setNewPassword("");
      setConfirmedPassword("");
      setPasswordMessage(`${receipt.message} 所有设备都需要重新登录，正在退出当前登录。`);
      propsRef.current.onLogout();
    } catch (error) {
      if (!isCurrent(ticket)) return;
      if (checkingPermission) {
        setPasswordError("暂时无法确认账户简介状态，请先处理简介草稿后重试。");
        return;
      }
      const status = errorStatus(error);
      if (status === 401) {
        setOldPassword("");
        setNewPassword("");
        setConfirmedPassword("");
        setPasswordError("登录状态已失效，请重新登录。密码修改结果未确认。");
        notifyUnauthorized(ticket);
      } else if (status === 403) {
        setPasswordError("当前账号无权修改密码，登录状态保持不变。");
      } else if (status === 400 || status === 422) {
        setPasswordError("密码未被修改，请检查当前密码和新密码后重试。");
      } else if (isUncertainWriteError(error)) {
        setOldPassword("");
        setNewPassword("");
        setConfirmedPassword("");
        setPasswordOutcomeUnknown(true);
        setPasswordError("改密结果未确定。为避免重复提交，密码输入已清除；请只读检查登录状态或退出后重新登录。");
      } else {
        setPasswordError("服务器未确认修改密码，登录状态保持不变。请检查后再决定下一步。");
      }
      if (status !== 401 && !isUncertainWriteError(error)) {
        releasePasswordMutationOwner(ticket.controller);
      }
    } finally {
      finishWrite(ticket);
    }
  }

  async function checkCurrentLogin(): Promise<void> {
    if (!passwordOutcomeUnknown || meCheckBusy) return;
    const ticket = beginTicket();
    setMeCheckBusy(true);
    setMeCheckMessage("正在只读检查当前登录状态；此检查不能确认密码是否已修改。");
    try {
      const current = await ticket.refreshCurrentUser(ticket.controller.signal);
      if (!isCurrent(ticket)) return;
      if (current.id !== ticket.userId) {
        setMeCheckMessage("读取到的账户与当前登录范围不一致，仍无法确认改密结果。");
        return;
      }
      setMeCheckMessage("当前登录检查通过，但改密结果仍未确定。请勿再次提交密码修改；可退出后由你手动重新登录核实。");
    } catch (error) {
      if (!isCurrent(ticket)) return;
      if (errorStatus(error) === 401) {
        setMeCheckMessage("当前登录已失效，但这不能单独证明改密成功。请手动重新登录核实。");
        notifyUnauthorized(ticket);
      } else if (errorStatus(error) === 403) {
        setMeCheckMessage("当前账号无权检查登录状态，登录状态保持不变；改密结果仍未确定。");
      } else {
        setMeCheckMessage("当前登录状态暂时无法读取，改密结果仍未确定。没有再次提交密码修改。");
      }
    } finally {
      if (isCurrent(ticket)) setMeCheckBusy(false);
      finishTicket(ticket);
    }
  }

  const revokeDisabled = writeBusy || service === null || hasUnresolvedOutcome;
  const passwordDisabled = writeBusy || service === null || hasUnresolvedOutcome
    || passwordPreparing || passwordConfirmationOpen;

  return (
    <section className="account-security-panel account-card" aria-labelledby="account-security-title">
      <header className="account-security-heading">
        <div>
          <p className="eyebrow">登录与凭据</p>
          <h2 id="account-security-title">账户安全</h2>
        </div>
        {service !== null && (
          <button
            className="account-security-secondary"
            type="button"
            onClick={() => void loadSessions()}
            disabled={sessionRead.status === "loading" || writeBusy}
          >
            {sessionRead.status === "loading" ? "正在读取…" : "刷新设备列表"}
          </button>
        )}
      </header>

      {service === null ? (
        <p className="account-security-muted" role="status">
          当前环境未接入账户安全服务，因此不会显示虚构设备或提交密码修改。
        </p>
      ) : (
        <>
          <section className="account-security-section" aria-labelledby="account-security-sessions-title">
            <div className="account-security-section-heading">
              <div>
                <p className="eyebrow">设备管理</p>
                <h3 id="account-security-sessions-title" ref={sessionsHeadingRef} tabIndex={-1}>登录设备</h3>
              </div>
            </div>

            {sessionRead.status === "loading" && (
              <p className="account-security-muted" role="status">正在读取登录设备…</p>
            )}
            {sessionRead.status === "error" && (
              <div className="account-security-load-error">
                <p role="alert">{sessionRead.message}</p>
                <button
                  className="account-security-secondary"
                  type="button"
                  disabled={writeBusy}
                  onClick={() => void loadSessions()}
                >
                  重试读取设备
                </button>
              </div>
            )}
            {sessionRead.status === "ready" && sessionRead.sessions.length === 0 && (
              <p className="account-security-muted" role="status">当前没有可显示的有效登录设备。</p>
            )}
            {sessionRead.status === "ready" && sessionRead.sessions.length > 0 && (
              <>
                {sessionRead.sessions.length >= 20 && (
                  <p className="account-security-muted">
                    服务端每次最多返回20条记录；列表未显示的设备不能据此判断为已下线。
                  </p>
                )}
                <ul className="account-security-session-list">
                  {sessionRead.sessions.map((session) => (
                    <li className="account-security-session" key={session.id}>
                      <div className="account-security-session-header">
                        <div className="account-security-session-title">
                          <h4>{session.deviceName}</h4>
                          {session.isCurrent && <span className="account-security-current">当前设备</span>}
                          {!session.isActive && <span className="account-security-muted">已下线</span>}
                        </div>
                        {session.isActive && !session.isCurrent && (
                          <button
                            className="account-security-danger"
                            type="button"
                            disabled={revokeDisabled}
                            onClick={(event) => {
                              revokeTriggerRef.current = event.currentTarget;
                              setPendingRevoke(session);
                            }}
                            aria-label={`撤销设备「${session.deviceName}」`}
                          >
                            撤销设备
                          </button>
                        )}
                        {session.isCurrent && (
                          <button className="account-security-secondary" type="button" disabled>
                            当前设备
                          </button>
                        )}
                      </div>
                      <dl className="account-security-session-facts">
                        <div><dt>IP 地址</dt><dd>{session.ipAddress}</dd></div>
                        <div><dt>浏览器</dt><dd>{session.userAgent || "未知"}</dd></div>
                        <div><dt>首次登录</dt><dd>{sessionTime(session.createdAt)}</dd></div>
                        <div><dt>最近活动</dt><dd>{sessionTime(session.lastSeenAt)}</dd></div>
                      </dl>
                    </li>
                  ))}
                </ul>
              </>
            )}

            {revokeMessage !== null && <p className="account-security-message" role="status">{revokeMessage}</p>}
            {unknownRevocation !== null && (
              <div className="account-security-uncertain" role="alert">
                <p>{unknownRevocation.message}</p>
                <p>目标设备：{unknownRevocation.deviceName}</p>
                <button className="account-security-secondary" type="button" onClick={() => void recheckUnknownRevocation()}>
                  只读核对设备列表
                </button>
                <button className="account-security-secondary" type="button" onClick={abandonUnknownRevocation}>
                  放弃本次撤销结果并允许其他操作
                </button>
              </div>
            )}
          </section>

          <section className="account-security-section" aria-labelledby="account-security-password-title">
            <div className="account-security-section-heading">
              <div>
                <p className="eyebrow">密码管理</p>
                <h3 id="account-security-password-title" ref={passwordHeadingRef} tabIndex={-1}>修改密码</h3>
              </div>
            </div>
            <p className="account-security-muted">
              修改后所有登录设备都需要重新登录。密码按原样发送，不会保存到浏览器存储。
            </p>

            <form className="account-security-password-form" onSubmit={(event) => void preparePasswordChange(event)}>
              <label>
                当前密码
                <input
                  type="password"
                  autoComplete="current-password"
                  required
                  value={oldPassword}
                  disabled={passwordDisabled}
                  onChange={(event) => {
                    setOldPassword(event.currentTarget.value);
                    setPasswordError(null);
                  }}
                />
              </label>
              <label>
                新密码
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  value={newPassword}
                  disabled={passwordDisabled}
                  onChange={(event) => {
                    setNewPassword(event.currentTarget.value);
                    setPasswordError(null);
                  }}
                />
              </label>
              <label>
                确认新密码
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  value={confirmedPassword}
                  disabled={passwordDisabled}
                  onChange={(event) => {
                    setConfirmedPassword(event.currentTarget.value);
                    setPasswordError(null);
                  }}
                />
              </label>
              {passwordError !== null && <p className="account-security-error" role="alert">{passwordError}</p>}
              {passwordMessage !== null && <p className="account-security-message" role="status">{passwordMessage}</p>}
              <button className="account-security-primary" ref={passwordSubmitRef} type="submit" disabled={passwordDisabled}>
                {passwordPreparing ? "正在确认简介草稿…" : writeBusy ? "正在处理…" : "修改密码"}
              </button>
            </form>

            {passwordOutcomeUnknown && (
              <div className="account-security-uncertain" role="region" aria-labelledby="account-security-unknown-title">
                <h4 id="account-security-unknown-title">改密结果未确定</h4>
                <p>只读检查不能证明密码已修改。系统不会重试改密，也不会用旧或新密码自动登录。</p>
                {meCheckMessage !== null && <p role="status">{meCheckMessage}</p>}
                <div className="account-security-actions">
                  <button
                    className="account-security-secondary"
                    type="button"
                    disabled={meCheckBusy}
                    onClick={() => void checkCurrentLogin()}
                  >
                    {meCheckBusy ? "正在检查…" : "只读检查当前登录"}
                  </button>
                  <button
                    className="account-security-primary"
                    type="button"
                    onClick={() => propsRef.current.onLogout()}
                  >
                    退出并手动重新登录
                  </button>
                </div>
              </div>
            )}
          </section>
        </>
      )}

      {pendingRevoke !== null && (
        <div className="account-security-dialog-backdrop">
          <section
            aria-labelledby="account-security-revoke-dialog-title"
            aria-modal="true"
            className="account-security-dialog"
            role="alertdialog"
          >
            <h3 id="account-security-revoke-dialog-title">确认撤销登录设备？</h3>
            <p>“{pendingRevoke.deviceName}” 将被要求重新登录。此操作不会退出当前设备。</p>
            <div className="account-security-actions">
              <button className="account-security-secondary" type="button" onClick={() => setPendingRevoke(null)}>
                取消
              </button>
              <button
                className="account-security-danger"
                ref={revokeConfirmRef}
                type="button"
                disabled={writeBusy || hasUnresolvedOutcome}
                onClick={() => void confirmRevoke()}
              >
                确认撤销设备
              </button>
            </div>
          </section>
        </div>
      )}

      {passwordConfirmationOpen && (
        <div className="account-security-dialog-backdrop">
          <section
            aria-labelledby="account-security-password-dialog-title"
            aria-modal="true"
            className="account-security-dialog"
            role="alertdialog"
          >
            <h3 id="account-security-password-dialog-title">确认修改密码？</h3>
            <p>确认后所有登录设备都需要重新登录，当前设备也会立即退出。系统不会自动重新登录。</p>
            <div className="account-security-actions">
              <button className="account-security-secondary" type="button" onClick={() => setPasswordConfirmationOpen(false)}>
                取消
              </button>
              <button
                className="account-security-primary"
                ref={passwordConfirmRef}
                type="button"
                disabled={writeBusy}
                onClick={() => void changePassword()}
              >
                确认修改密码
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
