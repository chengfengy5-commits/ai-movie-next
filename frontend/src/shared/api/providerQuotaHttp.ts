import { ApiError } from "./errors";
import type {
  ProviderQuotaAccount,
  ProviderQuotaAccountDirectory,
  ProviderQuotaAccountEditor,
  ProviderQuotaAccountInput,
  ProviderQuotaBalance,
  ProviderQuotaExtraMeta,
  ProviderQuotaProbeInput,
  ProviderQuotaProbeResult,
  ProviderQuotaJsonRequester,
  ProviderQuotaServicePort,
} from "./providerQuota";

function invalidResponse(): ApiError {
  return new ApiError("invalid-response", "供应商额度服务返回的数据无法识别。", 200);
}

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw invalidResponse();
  return value as Record<string, unknown>;
}

function requiredString(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  if (typeof value !== "string" || value.trim() === "") throw invalidResponse();
  return value;
}

function displayString(value: unknown, fallback = "", maxLength = 160): string {
  if (typeof value !== "string") return fallback;
  return value.replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, maxLength);
}

function optionalBoolean(source: Record<string, unknown>, key: string): boolean | null {
  const value = source[key];
  if (value === undefined || value === null) return null;
  if (typeof value !== "boolean") throw invalidResponse();
  return value;
}

function optionalAmount(source: Record<string, unknown>, key: string): number | null {
  const value = source[key];
  if (value === undefined || value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) throw invalidResponse();
  return value;
}

function safeConsoleUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.trim() === "" || /[\u0000-\u0020\u007f]/.test(value)) return null;
  const schemeEnd = value.indexOf("://");
  if (schemeEnd < 0 || value.includes("#")) return null;
  const authority = value.slice(schemeEnd + 3).split(/[/?#]/, 1)[0] ?? "";
  if (authority.includes("@")) return null;
  try {
    const url = new URL(value);
    if ((url.protocol !== "http:" && url.protocol !== "https:")
      || url.hostname === ""
      || url.username !== ""
      || url.password !== ""
      || url.hash !== "") return null;
    return url.toString();
  } catch {
    return null;
  }
}

function parseExtraMeta(value: unknown): ProviderQuotaExtraMeta[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw invalidResponse();

  const result: ProviderQuotaExtraMeta[] = [];
  for (const item of value) {
    if (typeof item !== "string") continue;
    const match = /^(现金|信控) (¥|\$|[A-Z]{3})(-?(?:\d+\.?\d*|\.\d+))$/.exec(item);
    if (!match) continue;
    const amount = Number(match[3]);
    if (!Number.isFinite(amount)) continue;
    result.push({ label: match[1] as ProviderQuotaExtraMeta["label"], unit: match[2]!, amount });
    if (result.length === 4) break;
  }
  return result;
}

function parseAccountRows(payload: unknown): ProviderQuotaAccountDirectory {
  if (!Array.isArray(payload)) throw invalidResponse();
  const ids = new Set<string>();
  const rows = payload.map((item): { account: ProviderQuotaAccount; editor: ProviderQuotaAccountEditor } => {
    const source = record(item);
    const id = requiredString(source, "id");
    if (ids.has(id)) throw invalidResponse();
    ids.add(id);

    const name = source.name;
    const providerType = source.provider_type;
    const baseUrl = source.base_url;
    const username = source.username;
    const isActive = source.is_active;
    const sortOrder = source.sort_order;
    if (typeof name !== "string" || typeof providerType !== "string"
      || typeof baseUrl !== "string" || typeof username !== "string"
      || typeof isActive !== "boolean" || typeof sortOrder !== "number"
      || !Number.isSafeInteger(sortOrder)) throw invalidResponse();

    const safeName = displayString(name);
    const safeProviderType = displayString(providerType, "new_api", 80);
    return {
      account: {
        id,
        name: safeName,
        providerType: safeProviderType,
        isActive,
        sortOrder,
        consoleUrl: safeConsoleUrl(source.console_url),
      },
      editor: {
        id,
        name: safeName,
        providerType: safeProviderType,
        baseUrl: displayString(baseUrl, "", 500),
        hasUsername: username.trim() !== "",
        isActive,
        sortOrder,
        consoleUrl: safeConsoleUrl(source.console_url),
      },
    };
  }).sort((left, right) => left.account.sortOrder - right.account.sortOrder);
  return {
    accounts: rows.map(({ account }) => account),
    editableAccounts: rows.map(({ editor }) => editor),
  };
}

export function parseProviderQuotaAccountDirectory(payload: unknown): ProviderQuotaAccountDirectory {
  return parseAccountRows(payload);
}

export function parseProviderQuotaAccountReceipt(payload: unknown, expectedId?: string): ProviderQuotaAccountEditor {
  const directory = parseAccountRows([payload]);
  const account = directory.editableAccounts[0];
  if (account === undefined || (expectedId !== undefined && account.id !== expectedId)) throw invalidResponse();
  return account;
}

export function parseProviderQuotaBalance(payload: unknown, expectedAccountId: string): ProviderQuotaBalance {
  const source = record(payload);
  const id = requiredString(source, "id");
  if (id !== expectedAccountId) throw invalidResponse();
  if (!Array.isArray(source.errors)) throw invalidResponse();

  const decimalsValue = source.decimals;
  const decimals = decimalsValue === undefined || decimalsValue === null ? 2 : decimalsValue;
  if (typeof decimals !== "number" || !Number.isSafeInteger(decimals) || decimals < 0 || decimals > 6) {
    throw invalidResponse();
  }
  const unit = source.unit;
  const unitSuffix = source.unit_suffix;
  const balanceLabel = source.balance_label;
  const updatedAt = source.updated_at;
  const providerType = source.provider_type;
  if ((unit !== undefined && unit !== null && typeof unit !== "string")
    || (unitSuffix !== undefined && unitSuffix !== null && typeof unitSuffix !== "string")
    || (balanceLabel !== undefined && balanceLabel !== null && typeof balanceLabel !== "string")
    || (updatedAt !== undefined && updatedAt !== null && typeof updatedAt !== "string")
    || (providerType !== undefined && providerType !== null && typeof providerType !== "string")) {
    throw invalidResponse();
  }
  const warnBelow = optionalAmount(source, "warn_below");
  const sessionReused = optionalBoolean(source, "session_reused");
  return {
    id,
    name: displayString(source.name, "未命名供应商", 160),
    providerType: providerType == null ? null : displayString(providerType, "", 80),
    isActive: optionalBoolean(source, "is_active"),
    disabled: optionalBoolean(source, "disabled"),
    configured: optionalBoolean(source, "configured"),
    unit: displayString(unit, "$", 12),
    unitSuffix: displayString(unitSuffix, "", 24),
    decimals,
    balanceLabel: displayString(balanceLabel, "剩余额度", 40),
    warnBelow: warnBelow ?? 10,
    balance: optionalAmount(source, "balance"),
    used: optionalAmount(source, "used"),
    total: optionalAmount(source, "total"),
    toppedUp: optionalAmount(source, "topped_up"),
    granted: optionalAmount(source, "granted"),
    credits: optionalAmount(source, "credits"),
    balanceCny: optionalAmount(source, "balance_cny"),
    available: optionalBoolean(source, "available"),
    extraMeta: parseExtraMeta(source.extra_meta),
    updatedAt: displayString(updatedAt, "", 64),
    hasErrors: source.errors.length > 0,
    sessionReused,
    consoleUrl: safeConsoleUrl(source.console_url),
  };
}

export function parseProviderQuotaDeleteReceipt(payload: unknown): void {
  const source = record(payload);
  if (source.ok !== true) throw invalidResponse();
}

export function parseProviderQuotaProbeResult(payload: unknown): ProviderQuotaProbeResult {
  const source = record(payload);
  if (source.id !== "" || typeof source.ok !== "boolean" || !Array.isArray(source.errors)) {
    throw invalidResponse();
  }
  const balance = optionalAmount(source, "balance");
  const unit = source.unit;
  if (unit !== undefined && unit !== null && typeof unit !== "string") throw invalidResponse();
  return {
    ok: source.ok && source.errors.length === 0 && balance !== null,
    balance,
    unit: displayString(unit, "$", 12),
  };
}

/**
 * Adapts the application's authenticated JSON requester into a strict quota
 * projection. The requester owns token, session-generation, and HTTP handling.
 */
export function createProviderQuotaHttp(requestJson: ProviderQuotaJsonRequester): ProviderQuotaServicePort {
  return {
    async listAccountDirectory(signal) {
      const payload = await requestJson({ endpoint: "providerQuotaAccounts" }, signal);
      return parseProviderQuotaAccountDirectory(payload);
    },
    async getBalance(accountId, signal) {
      if (accountId.trim() === "") throw invalidResponse();
      const payload = await requestJson({ endpoint: "providerQuotaAccountBalance", accountId }, signal);
      return parseProviderQuotaBalance(payload, accountId);
    },
    async createAccount(input: ProviderQuotaAccountInput, signal) {
      const payload = await requestJson({ endpoint: "providerQuotaCreateAccount", input }, signal);
      return parseProviderQuotaAccountReceipt(payload);
    },
    async updateAccount(accountId: string, input: ProviderQuotaAccountInput, signal) {
      if (accountId.trim() === "") throw invalidResponse();
      const payload = await requestJson({ endpoint: "providerQuotaUpdateAccount", accountId, input }, signal);
      return parseProviderQuotaAccountReceipt(payload, accountId);
    },
    async deleteAccount(accountId: string, signal) {
      if (accountId.trim() === "") throw invalidResponse();
      const payload = await requestJson({ endpoint: "providerQuotaDeleteAccount", accountId }, signal);
      parseProviderQuotaDeleteReceipt(payload);
    },
    async probeConnection(input: ProviderQuotaProbeInput, signal) {
      const payload = await requestJson({ endpoint: "providerQuotaProbe", input }, signal);
      return parseProviderQuotaProbeResult(payload);
    },
  };
}
