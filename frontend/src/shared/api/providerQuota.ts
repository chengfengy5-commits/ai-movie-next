/** Safe, read-only projections used by the administrator quota monitor. */

export interface ProviderQuotaAccount {
  id: string;
  name: string;
  providerType: string;
  isActive: boolean;
  sortOrder: number;
  consoleUrl: string | null;
}

export interface ProviderQuotaExtraMeta {
  label: "现金" | "信控";
  unit: string;
  amount: number;
}

export interface ProviderQuotaBalance {
  id: string;
  name: string;
  providerType: string | null;
  isActive: boolean | null;
  disabled: boolean | null;
  configured: boolean | null;
  unit: string;
  unitSuffix: string;
  decimals: number;
  balanceLabel: string;
  warnBelow: number;
  balance: number | null;
  used: number | null;
  total: number | null;
  toppedUp: number | null;
  granted: number | null;
  credits: number | null;
  balanceCny: number | null;
  available: boolean | null;
  extraMeta: ProviderQuotaExtraMeta[];
  updatedAt: string;
  hasErrors: boolean;
  sessionReused: boolean | null;
  consoleUrl: string | null;
}

export interface ProviderQuotaServicePort {
  listAccounts(signal: AbortSignal): Promise<ProviderQuotaAccount[]>;
  getBalance(accountId: string, signal: AbortSignal): Promise<ProviderQuotaBalance>;
}

/** A semantic request that can be routed through the application's authenticated API seam. */
export type ProviderQuotaHttpCall =
  | { endpoint: "providerQuotaAccounts" }
  | { endpoint: "providerQuotaAccountBalance"; accountId: string };

export type ProviderQuotaJsonRequester = (
  call: ProviderQuotaHttpCall,
  signal: AbortSignal,
) => Promise<unknown>;
