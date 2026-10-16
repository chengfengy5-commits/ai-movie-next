import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import type { AccountProfileServicePort } from "../../shared/api/accountProfile";
import { ApiError } from "../../shared/api/errors";
import type { User } from "../../shared/api/contracts";
import {
  InvalidAccountMembershipResponseError,
  parseAccountRedemptionReceipt,
  parsePublicSiteConfig,
  parsePurchasePackages,
  type AccountMembershipServicePort,
  type AccountRedemptionReceipt,
  type PurchasePackage,
  type PurchasePackageCategory,
  type PublicSiteConfig,
} from "../../shared/api/accountMembership";
import "./accountMembership.css";

type ReadStatus = "idle" | "loading" | "ready" | "error";

interface ReadState<T> {
  status: ReadStatus;
  value: T | null;
  message: string | null;
}

export type AccountRedemptionPhase =
  | "idle"
  | "editing"
  | "authorizing"
  | "redeeming"
  | "outcome-unknown"
  | "confirmed-refreshing"
  | "confirmed"
  | "abandoned";

export interface AccountRedemptionState {
  phase: AccountRedemptionPhase;
  hasCodeDraft: boolean;
  writeInFlight: boolean;
  outcomeUnknown: boolean;
}

export interface AccountMembershipPanelProps {
  user: User;
  membership: AccountMembershipServicePort | null;
  profile: AccountProfileServicePort | null;
  refreshCurrentUser(signal: AbortSignal): Promise<User>;
  sessionGeneration: number | string;
  onUnauthorized(): void;
  onDirtyChange?(dirty: boolean): void;
  onRedemptionStateChange?(state: AccountRedemptionState): void;
  /** Parent-owned password or forced-exit critical section. */
  canRedeem: boolean;
  /** Final parent preflight immediately before the single POST. */
  authorizeRedeem(): Promise<boolean>;
}

interface RequestTicket {
  controller: AbortController;
  generation: number;
  userId: string;
  membership: AccountMembershipServicePort | null;
  profile: AccountProfileServicePort | null;
  refreshCurrentUser: AccountMembershipPanelProps["refreshCurrentUser"];
  sessionGeneration: number | string;
}

interface AuthorizationAttempt {
  controller: AbortController;
  authorizeRedeem: AccountMembershipPanelProps["authorizeRedeem"];
}

const categories: ReadonlyArray<{ category: PurchasePackageCategory; title: string }> = [
  { category: "membership", title: "会员套餐" },
  { category: "credits", title: "积分套餐" },
];

function safeQrSource(value: string): string | null {
  const source = value.trim();
  if (source === "" || /[\u0000-\u001f\\]/.test(source)) return null;

  if (/^[a-z][a-z\d+.-]*:/i.test(source)) {
    try {
      const parsed = new URL(source);
      return (parsed.protocol === "http:" || parsed.protocol === "https:")
        && parsed.username === ""
        && parsed.password === ""
        ? source
        : null;
    } catch {
      return null;
    }
  }

  if (source.startsWith("//")) return null;
  try {
    const base = typeof window === "undefined" ? "http://localhost/" : window.location.href;
    const parsed = new URL(source, base);
    const origin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
    return parsed.origin === origin && (parsed.protocol === "http:" || parsed.protocol === "https:")
      ? source
      : null;
  } catch {
    return null;
  }
}

function parseMembershipTimestamp(value: string): number | null {
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

function formatMembershipTimestamp(value: string): string {
  if (parseMembershipTimestamp(value) === null) return "时间格式无效，暂不可确认";
  const normalized = value.replace("T", " ").replace(/\.\d+(?=Z$|[+-]\d{2}:?\d{2}$|$)/i, "");
  if (/[zZ]$/.test(normalized)) return `${normalized.slice(0, -1)} UTC`;
  if (/[+-]\d{2}:?\d{2}$/.test(normalized)) return normalized;
  return `${normalized} UTC`;
}

function membershipDescription(user: User): string {
  if (user.is_superuser) return "管理员账号";
  if (user.membership_type === "free") return "普通账号";
  if (user.membership_type === "premium" && user.membership_expires_at === null) return "永久会员";
  if (user.membership_type === "premium") {
    const expiration = user.membership_expires_at;
    if (expiration === null) return "永久会员";
    const timestamp = parseMembershipTimestamp(expiration);
    if (timestamp === null) return "会员到期时间格式无效，状态暂不可确认";
    if (timestamp <= Date.now()) return "会员已过期";
    return `会员 · 有效至 ${formatMembershipTimestamp(expiration)}`;
  }
  return `会员类型：${user.membership_type}`;
}

function membershipPolicyNotice(user: User, now = Date.now()): string | null {
  if (user.membership_type !== "premium") return null;
  if (user.membership_expires_at === null) {
    return "当前记录为永久会员。现有兑换规则可能把期限改为兑换天数对应的有限期限；请以兑换后重新读取的账户状态为准。";
  }
  const expiration = parseMembershipTimestamp(user.membership_expires_at);
  if (expiration !== null && expiration <= now) {
    return "当前 premium 记录已过期。现有兑换规则按旧到期时间累加天数，兑换后仍可能显示过期；请以兑换后重新读取的账户状态为准。";
  }
  return null;
}

function formatCredits(value: number): string {
  return value.toLocaleString("zh-CN");
}

function readErrorMessage(error: unknown, action: string): string {
  if (error instanceof ApiError && error.status === 401) return "当前登录状态已失效，请重新登录。";
  if (error instanceof ApiError && error.status === 403) return `${action}被服务端拒绝；当前登录状态仍保留。`;
  if (error instanceof ApiError && error.kind === "network") return `${action}暂时无法连接服务，请稍后重试。`;
  if (error instanceof ApiError && error.kind === "timeout") return `${action}请求超时，请稍后重试。`;
  if (error instanceof ApiError && error.status !== null && error.status >= 500) return `${action}暂时不可用，请稍后重试读取。`;
  if (error instanceof Error && error.message.trim() !== "") return `${action}失败：${error.message.slice(0, 240)}`;
  return `${action}暂时无法确认，请重试读取。`;
}

function definiteRedemptionRejection(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.kind !== "http") return false;
  return error.status === 400 || error.status === 401 || error.status === 403
    || error.status === 404 || error.status === 422;
}

function redemptionErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return "登录状态已失效，兑换未获服务端确认。";
  if (error instanceof ApiError && error.status === 403) return "服务端拒绝了本次兑换，当前登录状态仍保留。";
  if (definiteRedemptionRejection(error)) {
    const detail = error instanceof ApiError ? error.detail : null;
    return detail !== null && detail.trim() !== ""
      ? `兑换未被服务端接受：${detail.slice(0, 240)}`
      : "兑换未被服务端接受；可修改兑换码后重试。";
  }
  return "兑换结果未确定。请只读取账户状态或明确放弃核对，不要再次提交。";
}

function isUnknownRedemptionOutcome(error: unknown): boolean {
  if (error instanceof InvalidAccountMembershipResponseError) return true;
  if (!(error instanceof ApiError)) return true;
  if (error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response" || error.kind === "aborted") {
    return true;
  }
  if (error.kind === "http") {
    return error.status === null || error.status >= 500 || !definiteRedemptionRejection(error);
  }
  return true;
}

function initialReadState<T>(value: T | null = null): ReadState<T> {
  return { status: "loading", value, message: null };
}

function idleReadState<T>(): ReadState<T> {
  return { status: "idle", value: null, message: null };
}

export function AccountMembershipPanel({
  user,
  membership,
  profile,
  refreshCurrentUser,
  sessionGeneration,
  onUnauthorized,
  onDirtyChange,
  onRedemptionStateChange,
  canRedeem,
  authorizeRedeem,
}: AccountMembershipPanelProps) {
  const currentProps = {
    user,
    membership,
    profile,
    refreshCurrentUser,
    sessionGeneration,
    onUnauthorized,
    authorizeRedeem,
    canRedeem,
  };
  const propsRef = useRef(currentProps);
  propsRef.current = currentProps;
  const parentAllowsRedeem = useCallback(() => propsRef.current.canRedeem, []);

  const [configRead, setConfigRead] = useState<ReadState<PublicSiteConfig>>(initialReadState);
  const [packageReads, setPackageReads] = useState<Record<PurchasePackageCategory, ReadState<PurchasePackage[]>>>(() => ({
    membership: initialReadState(),
    credits: initialReadState(),
  }));
  const [accountRead, setAccountRead] = useState<ReadState<User>>(() => ({ status: "ready", value: user, message: null }));
  const [creditsRead, setCreditsRead] = useState<ReadState<number>>(idleReadState);
  const [code, setCode] = useState("");
  const [phase, setPhase] = useState<AccountRedemptionPhase>("idle");
  const [formMessage, setFormMessage] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<AccountRedemptionReceipt | null>(null);
  const [accountRefreshFailed, setAccountRefreshFailed] = useState(false);
  const [selectedPackage, setSelectedPackage] = useState<PurchasePackage | null>(null);
  const [contactVisible, setContactVisible] = useState(false);
  const [qrFailed, setQrFailed] = useState(false);

  const generation = useRef(0);
  const configReadSequence = useRef(0);
  const packageReadSequence = useRef<Record<PurchasePackageCategory, number>>({ membership: 0, credits: 0 });
  const accountReadSequence = useRef(0);
  const creditsReadSequence = useRef(0);
  const unauthorizedGeneration = useRef<number | null>(null);
  const controllers = useRef(new Set<AbortController>());
  const activeAttempt = useRef<AbortController | null>(null);
  const writeInFlight = useRef(false);
  const authorizationAttempt = useRef<AuthorizationAttempt | null>(null);
  const redemptionAttemptSequence = useRef(0);
  const confirmedReceiptOwner = useRef<number | null>(null);

  const beginRequest = useCallback((): RequestTicket => {
    const current = propsRef.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    return {
      controller,
      generation: generation.current,
      userId: current.user.id,
      membership: current.membership,
      profile: current.profile,
      refreshCurrentUser: current.refreshCurrentUser,
      sessionGeneration: current.sessionGeneration,
    };
  }, []);

  const isCurrent = useCallback((ticket: RequestTicket): boolean => {
    const current = propsRef.current;
    return ticket.generation === generation.current
      && ticket.userId === current.user.id
      && ticket.membership === current.membership
      && ticket.profile === current.profile
      && ticket.refreshCurrentUser === current.refreshCurrentUser
      && ticket.sessionGeneration === current.sessionGeneration
      && !ticket.controller.signal.aborted;
  }, []);

  const isCurrentPreflight = useCallback((
    ticket: RequestTicket,
    authorize: AccountMembershipPanelProps["authorizeRedeem"],
  ): boolean => isCurrent(ticket)
    && authorizationAttempt.current?.controller === ticket.controller
    && authorizationAttempt.current.authorizeRedeem === authorize
    && propsRef.current.authorizeRedeem === authorize, [isCurrent]);

  const finishRequest = useCallback((ticket: RequestTicket) => {
    controllers.current.delete(ticket.controller);
  }, []);

  const retirePreflight = useCallback((ticket: RequestTicket, message: string) => {
    if (activeAttempt.current !== ticket.controller) return;
    authorizationAttempt.current = null;
    activeAttempt.current = null;
    writeInFlight.current = false;
    finishRequest(ticket);
    setPhase("editing");
    setFormError(message);
    setFormMessage(null);
  }, [finishRequest]);

  useLayoutEffect(() => {
    const pending = authorizationAttempt.current;
    if (pending === null || pending.authorizeRedeem === authorizeRedeem) return;
    authorizationAttempt.current = null;
    if (activeAttempt.current !== pending.controller) return;

    pending.controller.abort();
    controllers.current.delete(pending.controller);
    activeAttempt.current = null;
    writeInFlight.current = false;
    setPhase("editing");
    setFormError("账户安全检查已更新，本次未发送兑换请求；请重新提交以再次检查。");
    setFormMessage(null);
  }, [authorizeRedeem]);

  const notifyUnauthorized = useCallback((ticket: RequestTicket) => {
    if (!isCurrent(ticket) || unauthorizedGeneration.current === ticket.generation) return;
    unauthorizedGeneration.current = ticket.generation;
    propsRef.current.onUnauthorized();
  }, [isCurrent]);

  const readPublicConfig = useCallback(() => {
    const ticket = beginRequest();
    const readSequence = ++configReadSequence.current;
    setConfigRead((current) => ({ ...current, status: "loading", message: null }));
    if (ticket.membership === null) {
      setConfigRead({ status: "error", value: null, message: "会员与积分配置服务尚未接入。" });
      finishRequest(ticket);
      return;
    }
    void Promise.resolve().then(() => ticket.membership?.getPublicConfig(ticket.controller.signal)).then((value) => {
      if (value === undefined) throw new InvalidAccountMembershipResponseError("公开站点配置格式无效。");
      if (!isCurrent(ticket) || configReadSequence.current !== readSequence) return;
      setConfigRead({ status: "ready", value: parsePublicSiteConfig(value), message: null });
    }).catch((error: unknown) => {
      if (!isCurrent(ticket) || configReadSequence.current !== readSequence) return;
      setConfigRead((current) => ({
        ...current,
        status: "error",
        message: readErrorMessage(error, "公开配置读取"),
      }));
    }).finally(() => finishRequest(ticket));
  }, [beginRequest, finishRequest, isCurrent]);

  const readPackages = useCallback((category: PurchasePackageCategory) => {
    const ticket = beginRequest();
    const readSequence = ++packageReadSequence.current[category];
    setPackageReads((current) => ({
      ...current,
      [category]: { ...current[category], status: "loading", message: null },
    }));
    if (ticket.membership === null) {
      setPackageReads((current) => ({
        ...current,
        [category]: { status: "error", value: null, message: "套餐目录服务尚未接入。" },
      }));
      finishRequest(ticket);
      return;
    }
    void Promise.resolve().then(() => ticket.membership?.listPurchasePackages(category, ticket.controller.signal)).then((value) => {
      if (value === undefined) throw new InvalidAccountMembershipResponseError("套餐目录格式无效。");
      if (!isCurrent(ticket) || packageReadSequence.current[category] !== readSequence) return;
      setPackageReads((current) => ({
        ...current,
        [category]: { status: "ready", value: parsePurchasePackages(value, category), message: null },
      }));
    }).catch((error: unknown) => {
      if (!isCurrent(ticket) || packageReadSequence.current[category] !== readSequence) return;
      setPackageReads((current) => ({
        ...current,
        [category]: {
          ...current[category],
          status: "error",
          message: readErrorMessage(error, `${category === "membership" ? "会员" : "积分"}套餐读取`),
        },
      }));
    }).finally(() => finishRequest(ticket));
  }, [beginRequest, finishRequest, isCurrent]);

  const readAccountProfile = useCallback(async (ticket: RequestTicket): Promise<boolean> => {
    const readSequence = ++accountReadSequence.current;
    setAccountRead((current) => ({ ...current, status: "loading", message: null }));
    try {
      const fresh = await ticket.refreshCurrentUser(ticket.controller.signal);
      if (!isCurrent(ticket) || accountReadSequence.current !== readSequence) return false;
      if (fresh.id !== ticket.userId) throw new ApiError("invalid-response", "账户资料回读与当前用户不匹配。");
      setAccountRead({ status: "ready", value: fresh, message: null });
      return true;
    } catch (error) {
      if (!isCurrent(ticket) || accountReadSequence.current !== readSequence) return false;
      if (error instanceof ApiError && error.status === 401) notifyUnauthorized(ticket);
      setAccountRead((current) => ({ ...current, status: "error", message: readErrorMessage(error, "账户资料读取") }));
      return false;
    }
  }, [isCurrent, notifyUnauthorized]);

  const readAccountCredits = useCallback(async (ticket: RequestTicket): Promise<boolean> => {
    const readSequence = ++creditsReadSequence.current;
    setCreditsRead((current) => ({ ...current, status: "loading", message: null }));
    if (ticket.profile === null) {
      setCreditsRead({ status: "error", value: null, message: "积分读取服务尚未接入。" });
      return false;
    }
    try {
      const snapshot = await ticket.profile.getCredits(ticket.controller.signal);
      if (!isCurrent(ticket) || creditsReadSequence.current !== readSequence) return false;
      if (!Number.isSafeInteger(snapshot.credits)) throw new ApiError("invalid-response", "服务器返回的积分余额无效。");
      setCreditsRead({ status: "ready", value: snapshot.credits, message: null });
      return true;
    } catch (error) {
      if (!isCurrent(ticket) || creditsReadSequence.current !== readSequence) return false;
      if (error instanceof ApiError && error.status === 401) notifyUnauthorized(ticket);
      setCreditsRead((current) => ({ ...current, status: "error", message: readErrorMessage(error, "积分余额读取") }));
      return false;
    }
  }, [isCurrent, notifyUnauthorized]);

  const refreshAccountState = useCallback(async (ticket: RequestTicket): Promise<boolean> => {
    const [profileFresh, creditsFresh] = await Promise.all([
      readAccountProfile(ticket),
      readAccountCredits(ticket),
    ]);
    return isCurrent(ticket) && profileFresh && creditsFresh;
  }, [isCurrent, readAccountCredits, readAccountProfile]);

  useLayoutEffect(() => {
    generation.current += 1;
    const currentGeneration = generation.current;
    configReadSequence.current += 1;
    packageReadSequence.current = { membership: 0, credits: 0 };
    accountReadSequence.current += 1;
    creditsReadSequence.current += 1;
    unauthorizedGeneration.current = null;
    for (const controller of controllers.current) controller.abort();
    controllers.current.clear();
    activeAttempt.current = null;
    writeInFlight.current = false;
    authorizationAttempt.current = null;
    confirmedReceiptOwner.current = null;
    redemptionAttemptSequence.current += 1;
    setConfigRead(initialReadState());
    setPackageReads({ membership: initialReadState(), credits: initialReadState() });
    setAccountRead({ status: "ready", value: user, message: null });
    setCreditsRead(idleReadState());
    setCode("");
    setPhase("idle");
    setFormMessage(null);
    setFormError(null);
    setReceipt(null);
    setAccountRefreshFailed(false);
    setSelectedPackage(null);
    setContactVisible(false);
    setQrFailed(false);
    readPublicConfig();
    readPackages("membership");
    readPackages("credits");
    return () => {
      if (generation.current === currentGeneration) generation.current += 1;
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
      if (activeAttempt.current !== null) activeAttempt.current = null;
      writeInFlight.current = false;
      authorizationAttempt.current = null;
      confirmedReceiptOwner.current = null;
    };
  }, [user.id, membership, profile, refreshCurrentUser, sessionGeneration, readPublicConfig, readPackages]);

  useEffect(() => {
    setAccountRead((current) => ({ ...current, value: user }));
  }, [user]);

  const hasCodeDraft = code !== "";
  const writePending = phase === "redeeming";
  const outcomeUnknown = phase === "outcome-unknown";
  const isDirty = hasCodeDraft || writePending || outcomeUnknown;

  useEffect(() => {
    onDirtyChange?.(isDirty);
    onRedemptionStateChange?.({
      phase,
      hasCodeDraft,
      writeInFlight: writePending,
      outcomeUnknown,
    });
  }, [hasCodeDraft, isDirty, onDirtyChange, onRedemptionStateChange, outcomeUnknown, phase, writePending]);

  const qrSource = configRead.value === null ? null : safeQrSource(configRead.value.purchase_qr_code_url);
  useEffect(() => setQrFailed(false), [qrSource]);

  function selectPackage(item: PurchasePackage) {
    setSelectedPackage(item);
    setContactVisible(true);
  }

  function packageSection(category: PurchasePackageCategory, title: string) {
    const read = packageReads[category];
    return (
      <section key={category} className="account-membership-package-section" aria-labelledby={`account-membership-${category}-title`}>
        <div className="account-membership-section-heading">
          <h3 id={`account-membership-${category}-title`}>{title}</h3>
          <button
            className="text-button"
            disabled={read.status === "loading"}
            onClick={() => readPackages(category)}
            type="button"
          >
            {read.status === "loading" ? "读取中…" : "重读目录"}
          </button>
        </div>
        {read.status === "loading" ? (
          <p className="account-membership-muted" role="status">正在读取{title}…</p>
        ) : read.message !== null ? (
          <div className="account-membership-load-error" role="alert">
            <p>{read.message}</p>
            <button className="text-button" onClick={() => readPackages(category)} type="button">重试读取</button>
          </div>
        ) : read.value !== null && read.value.length === 0 ? (
          <p className="account-membership-muted">当前没有可展示的{title}。</p>
        ) : (
          <ul className="account-membership-packages">
            {(read.value ?? []).map((item) => (
              <li key={item.id}>
                <div className="account-membership-package-copy">
                  <strong>{item.name}</strong>
                  <span>价格：¥{item.price} 元</span>
                  <span>积分：{item.credits === null ? "未提供" : formatCredits(item.credits)}</span>
                  <span>天数：{item.days === null ? "未提供" : item.days}</span>
                </div>
                {configRead.status === "ready" && configRead.value?.payments_enabled === true && (
                  <button className="secondary-button" onClick={() => selectPackage(item)} type="button">
                    查看购买联系信息
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }

  async function submitRedemption(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (activeAttempt.current !== null || writeInFlight.current || outcomeUnknown || phase === "abandoned") return;
    if (!parentAllowsRedeem()) {
      setFormError("账户安全操作正在进行，暂不能开始新的兑换。");
      setFormMessage(null);
      return;
    }
    if (propsRef.current.membership === null) {
      setFormError("兑换服务尚未接入，当前不会发送兑换请求。");
      setFormMessage(null);
      return;
    }
    const normalizedCode = code.trim();
    if (normalizedCode.length === 0) {
      setFormError("请输入兑换码。");
      setFormMessage(null);
      return;
    }
    if (Array.from(normalizedCode).length > 32) {
      setFormError("兑换码最多 32 个字符，请检查后修改。");
      setFormMessage(null);
      return;
    }

    const ticket = beginRequest();
    if (ticket.membership === null) {
      finishRequest(ticket);
      setFormError("兑换服务尚未接入，当前不会发送兑换请求。");
      return;
    }
    const attemptId = ++redemptionAttemptSequence.current;
    const authorize = propsRef.current.authorizeRedeem;
    activeAttempt.current = ticket.controller;
    authorizationAttempt.current = { controller: ticket.controller, authorizeRedeem: authorize };
    confirmedReceiptOwner.current = null;
    setPhase("authorizing");
    setFormError(null);
    setFormMessage(null);
    setReceipt(null);
    setAccountRefreshFailed(false);

    let postStarted = false;
    try {
      let authorized: boolean;
      try {
        authorized = await authorize();
      } catch {
        if (!isCurrent(ticket)) return;
        if (!isCurrentPreflight(ticket, authorize)) {
          retirePreflight(ticket, "账户安全检查已更新，本次未发送兑换请求；请重新提交以再次检查。");
        } else {
          retirePreflight(ticket, "父级账户检查未完成，未发送兑换请求。");
        }
        return;
      }
      if (!isCurrent(ticket)) return;
      if (!isCurrentPreflight(ticket, authorize)) {
        retirePreflight(ticket, "账户安全检查已更新，本次未发送兑换请求；请重新提交以再次检查。");
        return;
      }
      if (!authorized) {
        retirePreflight(ticket, "父级账户检查尚未授权本次兑换，未发送请求。");
        return;
      }
      if (!parentAllowsRedeem()) {
        retirePreflight(ticket, "账户安全操作已进入临界阶段，未发送兑换请求。");
        return;
      }

      authorizationAttempt.current = null;
      writeInFlight.current = true;
      setPhase("redeeming");
      postStarted = true;
      const rawReceipt = await ticket.membership.redeem({ code: normalizedCode }, ticket.controller.signal);
      if (!isCurrent(ticket)) return;
      const verifiedReceipt = parseAccountRedemptionReceipt(rawReceipt);
      confirmedReceiptOwner.current = attemptId;
      setReceipt(verifiedReceipt);
      setCode("");
      setPhase("confirmed-refreshing");
      setFormError(null);
      setFormMessage(null);
      const readsSucceeded = await refreshAccountState(ticket);
      if (!isCurrent(ticket)) return;
      setAccountRefreshFailed(!readsSucceeded);
      setPhase("confirmed");
      setFormMessage(readsSucceeded
        ? "兑换已确认；账户资料与积分余额已分别重新读取。"
        : "兑换已确认，最新账户读取失败；可只读重试读取。");
    } catch (error) {
      if (!isCurrent(ticket)) return;
      if (!postStarted) {
        setPhase("editing");
        setFormError("兑换前检查未完成，未发送请求。");
        return;
      }
      if (error instanceof ApiError && error.status === 401) notifyUnauthorized(ticket);
      if (isUnknownRedemptionOutcome(error)) {
        setPhase("outcome-unknown");
        setFormError(redemptionErrorMessage(error));
        setFormMessage(null);
      } else {
        setPhase("editing");
        setFormError(redemptionErrorMessage(error));
      }
    } finally {
      finishRequest(ticket);
      if (activeAttempt.current === ticket.controller) {
        if (authorizationAttempt.current?.controller === ticket.controller) authorizationAttempt.current = null;
        activeAttempt.current = null;
        writeInFlight.current = false;
      }
    }
  }

  async function refreshUnknownAccountState() {
    if (!outcomeUnknown) return;
    const ticket = beginRequest();
    setAccountRefreshFailed(false);
    const readsSucceeded = await refreshAccountState(ticket);
    if (isCurrent(ticket)) setAccountRefreshFailed(!readsSucceeded);
    finishRequest(ticket);
  }

  async function retryConfirmedAccountReads() {
    const receiptOwner = confirmedReceiptOwner.current;
    if (receipt === null || receiptOwner === null) return;
    const ticket = beginRequest();
    setAccountRefreshFailed(false);
    const readsSucceeded = await refreshAccountState(ticket);
    if (isCurrent(ticket) && confirmedReceiptOwner.current === receiptOwner) {
      setAccountRefreshFailed(!readsSucceeded);
      setFormMessage(readsSucceeded
        ? "兑换已确认；账户资料与积分余额已分别重新读取。"
        : "兑换已确认，最新账户读取失败；可只读重试读取。");
    }
    finishRequest(ticket);
  }

  function abandonUnknownOutcome() {
    if (!outcomeUnknown) return;
    setCode("");
    setFormError(null);
    setFormMessage("已明确放弃本次结果核对；如需离开账户页，可使用页面顶部的返回按钮。不会自动重发兑换请求。");
    setPhase("abandoned");
  }

  const currentUser = accountRead.value?.id === user.id ? accountRead.value : user;
  const currentPolicyNotice = membershipPolicyNotice(currentUser);
  const paymentsAvailable = configRead.status === "ready" && configRead.value?.payments_enabled === true;
  const showContact = paymentsAvailable && contactVisible && selectedPackage !== null;
  const canEditCode = canRedeem && phase !== "authorizing" && phase !== "redeeming"
    && phase !== "outcome-unknown" && phase !== "confirmed-refreshing" && phase !== "abandoned";

  return (
    <section className="account-membership-panel" aria-labelledby="account-membership-panel-title">
      <header className="account-membership-header">
        <div>
          <p className="eyebrow">会员与积分</p>
          <h2 id="account-membership-panel-title">会员目录与兑换</h2>
        </div>
      </header>

      <section className="account-membership-account" aria-label="当前账户状态">
        <p>当前登录账号：<strong>{user.username}</strong></p>
        <p>会员状态：<strong>{membershipDescription(currentUser)}</strong></p>
        {accountRead.message !== null && <p className="account-membership-error" role="alert">{accountRead.message}</p>}
        {creditsRead.status === "ready" && creditsRead.value !== null && (
          <p>最新积分余额：<strong>{formatCredits(creditsRead.value)}</strong></p>
        )}
        {creditsRead.status === "error" && creditsRead.message !== null && (
          <p className="account-membership-error" role="alert">{creditsRead.message}</p>
        )}
        {accountRead.status === "loading" && <p className="account-membership-muted" role="status">正在重新读取账户资料…</p>}
        {creditsRead.status === "loading" && <p className="account-membership-muted" role="status">正在重新读取积分余额…</p>}
      </section>

      <section className="account-membership-benefits" aria-labelledby="account-membership-benefits-title">
        <div className="account-membership-section-heading">
          <h3 id="account-membership-benefits-title">会员权益</h3>
          <button
            className="text-button"
            disabled={configRead.status === "loading"}
            onClick={readPublicConfig}
            type="button"
          >
            {configRead.status === "loading" ? "读取中…" : "重读配置"}
          </button>
        </div>
        {configRead.status === "loading" ? (
          <p className="account-membership-muted" role="status">正在读取公开配置…</p>
        ) : configRead.message !== null ? (
          <div className="account-membership-load-error" role="alert">
            <p>{configRead.message}</p>
            <button className="text-button" onClick={readPublicConfig} type="button">重试读取</button>
          </div>
        ) : (
          <p className="account-membership-benefit-text">{configRead.value?.membership_benefits || "尚未配置权益说明。"}</p>
        )}
      </section>

      <div className="account-membership-package-grid">
        {categories.map(({ category, title }) => packageSection(category, title))}
      </div>

      {configRead.status === "ready" && configRead.value?.payments_enabled === false && (
        <p className="account-membership-muted" role="status">当前不提供套餐购买联系入口；登录用户仍可使用兑换码。</p>
      )}

      {showContact && selectedPackage !== null && configRead.value !== null && (
        <section className="account-membership-contact" aria-labelledby="account-membership-contact-title">
          <div className="account-membership-section-heading">
            <div>
              <p className="eyebrow">仅展示管理员提供的信息</p>
              <h3 id="account-membership-contact-title">{selectedPackage.name} · 联系信息</h3>
            </div>
            <button className="text-button" onClick={() => setContactVisible(false)} type="button">收起</button>
          </div>
          <p className="account-membership-help">所选套餐只用于查看联系说明，本页面不会创建订单或发起付款。</p>
          <p className="account-membership-contact-text">
            {configRead.value.purchase_contact_info || "尚未配置管理员联系信息。"}
          </p>
          {qrSource === null ? (
            <p className="account-membership-muted">{configRead.value.purchase_qr_code_url.trim() === ""
              ? "尚未配置联系二维码。"
              : "二维码地址不符合安全展示要求，已改为显示联系文本。"}</p>
          ) : qrFailed ? (
            <p className="account-membership-muted" role="status">二维码加载失败，请根据上方联系信息自行联系管理员。</p>
          ) : (
            <img
              className="account-membership-qr"
              alt="管理员提供的购买联系二维码"
              src={qrSource}
              onError={() => setQrFailed(true)}
            />
          )}
        </section>
      )}

      <section className="account-membership-redemption" aria-labelledby="account-membership-redemption-title">
        <div className="account-membership-section-heading">
          <div>
            <p className="eyebrow">登录用户可用</p>
            <h3 id="account-membership-redemption-title">使用兑换码</h3>
          </div>
        </div>
        {currentPolicyNotice !== null && <p className="account-membership-policy-notice">{currentPolicyNotice}</p>}
        {!canRedeem && <p className="account-membership-muted" role="status">账户安全操作正在进行，兑换输入与提交暂时关闭。</p>}
        <form className="account-membership-redemption-form" onSubmit={(event) => void submitRedemption(event)}>
          <label htmlFor="account-membership-redemption-code">兑换码</label>
          <input
            id="account-membership-redemption-code"
            autoComplete="off"
            readOnly={!canEditCode}
            value={code}
            onChange={(event) => {
              setCode(event.target.value);
              setFormError(null);
              setFormMessage(null);
              setPhase(event.target.value === "" ? "idle" : "editing");
            }}
          />
          <div className="account-membership-actions">
            <button
              className="primary-button"
              disabled={!canRedeem || membership === null || !canEditCode}
              type="submit"
            >
              {phase === "authorizing" ? "正在检查账户…" : phase === "redeeming" ? "正在提交…" : "兑换"}
            </button>
            {phase === "outcome-unknown" && (
              <>
                <button className="secondary-button" onClick={() => void refreshUnknownAccountState()} type="button">
                  只刷新账户状态
                </button>
                <button className="secondary-button" onClick={abandonUnknownOutcome} type="button">
                  放弃核对并清除本次输入
                </button>
              </>
            )}
          </div>
          {membership === null && <p className="account-membership-muted">兑换服务尚未接入；不会模拟成功。</p>}
          {formError !== null && <p className="account-membership-error" role="alert">{formError}</p>}
          {formMessage !== null && <p className="account-membership-status" role="status">{formMessage}</p>}
          {receipt !== null && (
            <div className="account-membership-receipt" role="status">
              <strong>{receipt.message}</strong>
              <p>服务端回执：{receipt.type === "membership" ? "会员" : "积分"}，数值 {receipt.value}</p>
              {receipt.detail !== null && <p>{receipt.detail}</p>}
              {accountRefreshFailed && <p>兑换已确认，最新账户读取失败；请使用上方账户读取操作重试。</p>}
            </div>
          )}
          {receipt !== null && accountRefreshFailed && (
            <button
              className="secondary-button"
              disabled={accountRead.status === "loading" || creditsRead.status === "loading"}
              onClick={() => void retryConfirmedAccountReads()}
              type="button"
            >
              {accountRead.status === "loading" || creditsRead.status === "loading" ? "正在只读重试…" : "只重试账户读取"}
            </button>
          )}
          {outcomeUnknown && (
            <p className="account-membership-unknown-note" role="status">
              当前页面无法查询该码是否由本次请求消耗。账户变化或“已使用”都不能证明本次兑换成功；请只读核对，或明确放弃本次核对。
            </p>
          )}
        </form>
      </section>
    </section>
  );
}
