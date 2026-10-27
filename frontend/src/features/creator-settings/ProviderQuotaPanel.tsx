import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type { ProviderQuotaAccount, ProviderQuotaBalance, ProviderQuotaServicePort } from "../../shared/api/providerQuota";
import "./providerQuota.css";

type CardState = "loading" | "disabled" | "error" | "ready";

interface ProviderQuotaCard {
  account: ProviderQuotaAccount;
  state: CardState;
  balance?: ProviderQuotaBalance;
  message?: string;
}

interface ProviderQuotaPanelProps {
  actorId: string;
  isSuperuser: boolean;
  service?: ProviderQuotaServicePort | undefined;
  onUnauthorized(): void;
  onClose(): void;
}

function requestMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  if (error.status === 401) return "登录状态已失效，请重新登录。";
  if (error.status === 403) return "当前账号没有读取供应商额度的权限。";
  if (error.status === 404) return "监控账号已不存在，请刷新列表。";
  if (error.status === 503) return "额度监控服务暂不可用，请稍后重试。";
  if (error.kind === "invalid-response") return "额度服务返回的数据无法识别。";
  return fallback;
}

function formatAmount(value: number | null, unit: string, suffix: string, decimals: number): string {
  if (value === null) return "—";
  return `${unit}${value.toFixed(decimals)}${suffix === "" ? "" : ` ${suffix}`}`;
}

function updatedTime(value: string): string {
  const match = /(\d{1,2}:\d{2})/.exec(value);
  return match ? `${match[1]} 更新` : "";
}

function consoleHost(value: string): string {
  try {
    return new URL(value).host;
  } catch {
    return "供应商后台";
  }
}

export function ProviderQuotaPanel({ actorId, isSuperuser, service, onUnauthorized, onClose }: ProviderQuotaPanelProps) {
  const [cards, setCards] = useState<ProviderQuotaCard[]>([]);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const generation = useRef(0);
  const scopeRef = useRef({ actorId, isSuperuser, service, onUnauthorized });
  scopeRef.current = { actorId, isSuperuser, service, onUnauthorized };

  useEffect(() => {
    if (!isSuperuser || service === undefined) {
      setCards([]);
      setListError(null);
      setLoading(false);
      return;
    }

    const requestGeneration = ++generation.current;
    const controller = new AbortController();
    let active = true;
    let authorizationHandled = false;
    const capturedActorId = actorId;
    const capturedService = service;
    const isCurrent = () => active
      && generation.current === requestGeneration
      && !controller.signal.aborted
      && scopeRef.current.actorId === capturedActorId
      && scopeRef.current.service === capturedService
      && scopeRef.current.isSuperuser;
    const handleAuthorizationFailure = (error: unknown) => {
      if (error instanceof ApiError && error.status === 401 && !authorizationHandled && isCurrent()) {
        authorizationHandled = true;
        scopeRef.current.onUnauthorized();
      }
    };

    setCards([]);
    setListError(null);
    setLoading(true);

    async function load(): Promise<void> {
      try {
        const accounts = await capturedService.listAccounts(controller.signal);
        if (!isCurrent()) return;
        const initialCards = accounts.map((account): ProviderQuotaCard => ({
          account,
          state: account.isActive ? "loading" : "disabled",
        }));
        setCards(initialCards);

        await Promise.all(accounts.filter((account) => account.isActive).map(async (account) => {
          try {
            const balance = await capturedService.getBalance(account.id, controller.signal);
            if (!isCurrent()) return;
            const nextCard: ProviderQuotaCard = balance.isActive === false || balance.disabled === true
              ? { account, state: "disabled", balance }
              : balance.configured === false
                ? { account, state: "error", balance, message: "该监控账号未配置可用凭据。" }
                : balance.hasErrors
                  ? { account, state: "error", balance, message: "额度查询异常，请检查该账号配置或稍后重试。" }
                  : balance.balance === null
                    ? { account, state: "error", balance, message: "供应商未返回余额数据。" }
                    : { account, state: "ready", balance };
            setCards((current) => current.map((card) => card.account.id === account.id ? nextCard : card));
          } catch (error) {
            if (!isCurrent()) return;
            handleAuthorizationFailure(error);
            const message = requestMessage(error, "额度查询失败，请稍后重试。");
            setCards((current) => current.map((card) => card.account.id === account.id
              ? { account, state: "error", message }
              : card));
          }
        }));
      } catch (error) {
        if (!isCurrent()) return;
        handleAuthorizationFailure(error);
        setCards([]);
        setListError(requestMessage(error, "暂时无法读取供应商监控账号，请稍后重试。"));
      } finally {
        if (isCurrent()) setLoading(false);
      }
    }

    void load();
    return () => {
      active = false;
      if (generation.current === requestGeneration) generation.current += 1;
      controller.abort();
    };
  }, [actorId, isSuperuser, refreshVersion, service]);

  const warningCount = cards.filter((card) => card.state === "ready"
    && card.balance !== undefined
    && card.balance.balance !== null
    && card.balance.balance < card.balance.warnBelow).length;
  const errorCount = cards.filter((card) => card.state === "error").length;

  if (!isSuperuser) return null;

  return (
    <section id="creator-provider-quota-panel" className="provider-quota-panel" aria-label="供应商额度监控">
      <header className="provider-quota-panel-header">
        <div>
          <h2>供应商额度监控</h2>
          <p>额度按供应商分别展示，不合并不同计价单位；仅在打开或手动刷新时查询。</p>
        </div>
        <div className="provider-quota-panel-actions">
          <button disabled={loading || service === undefined} onClick={() => setRefreshVersion((version) => version + 1)} type="button">
            {loading ? "正在查询…" : "刷新额度"}
          </button>
          <button onClick={onClose} type="button">关闭</button>
        </div>
      </header>

      {service === undefined && <p className="provider-quota-panel-message" role="status">当前运行配置尚未接入额度监控服务。</p>}
      {listError !== null && <p className="provider-quota-panel-error" role="alert">{listError}</p>}

      {service !== undefined && listError === null && (
        <>
          <div className="provider-quota-panel-summary" aria-label="额度状态概览">
            <span>监控账号 {cards.length} 家</span>
            <span className={warningCount > 0 ? "is-warn" : ""}>告警 {loading ? "—" : warningCount}</span>
            <span className={errorCount > 0 ? "is-error" : ""}>异常 {loading ? "—" : errorCount}</span>
          </div>

          {loading && cards.length === 0 && <p className="provider-quota-panel-message" role="status">正在读取监控账号…</p>}
          {!loading && cards.length === 0 && <p className="provider-quota-panel-message">还没有配置供应商监控账号。</p>}

          <div className="provider-quota-panel-grid">
            {cards.map((card) => {
              const balance = card.balance;
              const consoleUrl = balance?.consoleUrl ?? card.account.consoleUrl;
              const isWarning = card.state === "ready" && balance !== undefined
                && balance.balance !== null && balance.balance < balance.warnBelow;
              const stateLabel = card.state === "loading"
                ? "查询中"
                : card.state === "disabled"
                  ? "已停用"
                  : card.state === "error"
                    ? "异常"
                    : isWarning ? "告警" : "正常";
              const visibleName = balance?.name.trim() || card.account.name.trim() || "未命名供应商";

              return (
                <article className={`provider-quota-card is-${card.state}${isWarning ? " is-warn" : ""}`} key={card.account.id}>
                  <header className="provider-quota-card-header">
                    <div>
                      <strong>{visibleName}</strong>
                      <small>{card.account.providerType}</small>
                    </div>
                    {consoleUrl !== null && (
                      <a href={consoleUrl} rel="noopener noreferrer" target="_blank" title={`打开 ${consoleHost(consoleUrl)}（新窗口）`}>
                        打开后台
                      </a>
                    )}
                  </header>
                  <div className="provider-quota-card-status" role="status">{stateLabel}</div>
                  {card.state === "ready" && balance !== undefined ? (
                    <>
                      <div className="provider-quota-card-main">
                        <strong>{formatAmount(balance.balance, balance.unit, balance.unitSuffix, balance.decimals)}</strong>
                        <span>{balance.balanceLabel}</span>
                      </div>
                      <div className="provider-quota-card-details">
                        {balance.available === false && <span>余额不足，可能无法调用</span>}
                        {balance.used !== null && <span>已用 {formatAmount(balance.used, balance.unit, balance.unitSuffix, balance.decimals)}</span>}
                        {balance.total !== null && <span>总额 {formatAmount(balance.total, balance.unit, balance.unitSuffix, balance.decimals)}</span>}
                        {balance.toppedUp !== null && <span>充值 {formatAmount(balance.toppedUp, balance.unit, balance.unitSuffix, balance.decimals)}</span>}
                        {balance.granted !== null && <span>赠金 {formatAmount(balance.granted, balance.unit, balance.unitSuffix, balance.decimals)}</span>}
                        {balance.credits !== null && <span>{balance.credits.toFixed(0)} 积分</span>}
                        {balance.unit !== "¥" && balance.balanceCny !== null && <span>≈¥{balance.balanceCny.toFixed(2)}</span>}
                        {balance.extraMeta.map((item, index) => (
                          <span key={`${item.label}-${index}`}>{item.label} {item.unit}{item.amount.toFixed(2)}</span>
                        ))}
                      </div>
                    </>
                  ) : card.state === "loading" ? (
                    <div className="provider-quota-card-main"><strong>—</strong><span>正在查询额度…</span></div>
                  ) : (
                    <div className="provider-quota-card-main">
                      <strong>—</strong>
                      <span>{card.message ?? (card.state === "disabled" ? "该监控账号已停用。" : "余额暂不可用。")}</span>
                    </div>
                  )}
                  {balance !== undefined && updatedTime(balance.updatedAt) !== "" && (
                    <footer>{updatedTime(balance.updatedAt)}</footer>
                  )}
                </article>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
