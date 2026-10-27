export interface ZhangyugePatStatus {
  configured: boolean;
}

export interface ZhangyugeBalanceSnapshot {
  balanceUsd: number | null;
  balanceCny: number | null;
  usedUsd: number | null;
  usedCny: number | null;
  quotaPerUnit: number;
  usdExchangeRate: number;
  patConfigured: boolean;
  apiKeyConfigured: boolean;
  hasErrors: boolean;
  updatedAt: string | null;
}

export interface ZhangyugePatServicePort {
  getStatus(signal: AbortSignal): Promise<ZhangyugePatStatus>;
  getBalance(signal: AbortSignal): Promise<ZhangyugeBalanceSnapshot>;
  save(pat: string, signal: AbortSignal): Promise<void>;
}

export type ZhangyugePatHttpCall =
  | { endpoint: "zhangyugePatStatus" }
  | { endpoint: "zhangyugeBalance" }
  | { endpoint: "zhangyugePatSave"; pat: string };

export type ZhangyugePatJsonRequester = (
  call: ZhangyugePatHttpCall,
  signal: AbortSignal,
) => Promise<unknown>;
