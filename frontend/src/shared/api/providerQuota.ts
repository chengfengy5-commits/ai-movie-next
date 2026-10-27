/** Safe, read-only projections used by the administrator quota monitor. */

export interface ProviderQuotaAccount {
  id: string;
  name: string;
  providerType: string;
  isActive: boolean;
  sortOrder: number;
  consoleUrl: string | null;
}

export const PROVIDER_QUOTA_TYPES = ["new_api", "deepseek", "aistarslab", "aliyun"] as const;
export type ProviderQuotaType = typeof PROVIDER_QUOTA_TYPES[number];

/** Non-secret values needed to edit an account; credentials are never included. */
export interface ProviderQuotaAccountEditor {
  id: string;
  name: string;
  providerType: string;
  baseUrl: string;
  hasUsername: boolean;
  isActive: boolean;
  sortOrder: number;
  consoleUrl: string | null;
}

export interface ProviderQuotaAccountDirectory {
  accounts: ProviderQuotaAccount[];
  editableAccounts: ProviderQuotaAccountEditor[];
}

export interface ProviderQuotaAccountInput {
  name: string;
  providerType: ProviderQuotaType;
  baseUrl: string;
  username: string;
  password: string;
  isActive: boolean;
}

export interface ProviderQuotaProbeInput {
  providerType: ProviderQuotaType;
  baseUrl: string;
  username: string;
  password: string;
}

export interface ProviderQuotaProbeResult {
  ok: boolean;
  balance: number | null;
  unit: string;
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
  listAccountDirectory(signal: AbortSignal): Promise<ProviderQuotaAccountDirectory>;
  getBalance(accountId: string, signal: AbortSignal): Promise<ProviderQuotaBalance>;
  createAccount(input: ProviderQuotaAccountInput, signal: AbortSignal): Promise<ProviderQuotaAccountEditor>;
  updateAccount(accountId: string, input: ProviderQuotaAccountInput, signal: AbortSignal): Promise<ProviderQuotaAccountEditor>;
  deleteAccount(accountId: string, signal: AbortSignal): Promise<void>;
  probeConnection(input: ProviderQuotaProbeInput, signal: AbortSignal): Promise<ProviderQuotaProbeResult>;
}

/** A semantic request that can be routed through the application's authenticated API seam. */
export type ProviderQuotaHttpCall =
  | { endpoint: "providerQuotaAccounts" }
  | { endpoint: "providerQuotaAccountBalance"; accountId: string }
  | { endpoint: "providerQuotaCreateAccount"; input: ProviderQuotaAccountInput }
  | { endpoint: "providerQuotaUpdateAccount"; accountId: string; input: ProviderQuotaAccountInput }
  | { endpoint: "providerQuotaDeleteAccount"; accountId: string }
  | { endpoint: "providerQuotaProbe"; input: ProviderQuotaProbeInput };

export type ProviderQuotaJsonRequester = (
  call: ProviderQuotaHttpCall,
  signal: AbortSignal,
) => Promise<unknown>;
