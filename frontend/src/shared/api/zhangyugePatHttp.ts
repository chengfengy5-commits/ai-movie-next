import { ApiError } from "./errors";
import type {
  ZhangyugeBalanceSnapshot,
  ZhangyugePatJsonRequester,
  ZhangyugePatServicePort,
  ZhangyugePatStatus,
} from "./zhangyugePat";

function invalidResponse(): ApiError {
  return new ApiError("invalid-response", "章鱼哥 PAT 服务返回的数据无法识别。", 200);
}

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw invalidResponse();
  return value as Record<string, unknown>;
}

export function parseZhangyugePatStatus(payload: unknown): ZhangyugePatStatus {
  const source = record(payload);
  if (typeof source.configured !== "boolean") throw invalidResponse();
  return { configured: source.configured };
}

function finiteNumberOrNull(source: Record<string, unknown>, key: string): number | null {
  const value = source[key];
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) throw invalidResponse();
  return value;
}

function finiteNumber(source: Record<string, unknown>, key: string): number {
  const value = source[key];
  if (typeof value !== "number" || !Number.isFinite(value)) throw invalidResponse();
  return value;
}

function isSafeTimestamp(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    && Number.isFinite(Date.parse(value));
}

export function parseZhangyugeBalanceSnapshot(payload: unknown): ZhangyugeBalanceSnapshot {
  const source = record(payload);
  if (typeof source.pat_configured !== "boolean"
    || typeof source.api_key_configured !== "boolean"
    || !Array.isArray(source.errors)
    || source.errors.some((error) => typeof error !== "string")
    || typeof source.updated_at !== "string") {
    throw invalidResponse();
  }

  return {
    balanceUsd: finiteNumberOrNull(source, "balance_usd"),
    balanceCny: finiteNumberOrNull(source, "balance_cny"),
    usedUsd: finiteNumberOrNull(source, "used_usd"),
    usedCny: finiteNumberOrNull(source, "used_cny"),
    quotaPerUnit: finiteNumber(source, "quota_per_unit"),
    usdExchangeRate: finiteNumber(source, "usd_exchange_rate"),
    patConfigured: source.pat_configured,
    apiKeyConfigured: source.api_key_configured,
    hasErrors: source.errors.length > 0,
    updatedAt: isSafeTimestamp(source.updated_at) ? source.updated_at : null,
  };
}

export function parseZhangyugePatSaveReceipt(payload: unknown): void {
  if (record(payload).ok !== true) throw invalidResponse();
}

export function createZhangyugePatHttp(requestJson: ZhangyugePatJsonRequester): ZhangyugePatServicePort {
  return {
    async getStatus(signal) {
      return parseZhangyugePatStatus(await requestJson({ endpoint: "zhangyugePatStatus" }, signal));
    },
    async getBalance(signal) {
      return parseZhangyugeBalanceSnapshot(await requestJson({ endpoint: "zhangyugeBalance" }, signal));
    },
    async save(pat, signal) {
      parseZhangyugePatSaveReceipt(await requestJson({ endpoint: "zhangyugePatSave", pat }, signal));
    },
  };
}
