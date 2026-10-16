export type AdminSmtpAccountMode = "list" | "create" | "edit";

export interface AdminSmtpAccountRecord {
  id: string;
  host: string;
  port: number;
  user: string;
  daily_sent: number;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminSmtpAccountPage {
  data: AdminSmtpAccountRecord[];
  total: number;
}

export interface AdminSmtpAccountCreateInput {
  user: string;
  password: string;
  host: string;
  port: number;
  is_active: boolean;
}

export interface AdminSmtpAccountUpdatePatch {
  user?: string;
  host?: string;
  port?: number;
  is_active?: boolean;
  password?: string;
}

export interface AdminSmtpAccountAck {
  message: "更新成功" | "删除成功";
}

export interface AdminSmtpAccountCreateAck {
  id: string;
  user: string;
  message: "添加成功";
}

export interface AdminSmtpAccountsApi {
  listAccounts(signal?: AbortSignal): Promise<AdminSmtpAccountPage>;
  getAccount(id: string, signal?: AbortSignal): Promise<AdminSmtpAccountRecord>;
  createAccount(input: AdminSmtpAccountCreateInput, signal?: AbortSignal): Promise<AdminSmtpAccountCreateAck>;
  updateAccount(id: string, patch: AdminSmtpAccountUpdatePatch, signal?: AbortSignal): Promise<AdminSmtpAccountAck>;
  deleteAccount(id: string, signal?: AbortSignal): Promise<AdminSmtpAccountAck>;
}

export type AdminSmtpAccountExitKind = "dirty-draft" | "write-in-flight" | "unknown-write";

export interface PendingAdminSmtpAccountExit {
  kind: AdminSmtpAccountExitKind;
  operation: "create" | "update" | "delete";
  mode: AdminSmtpAccountMode;
  recordId: string | null;
  intentId: number;
  secretChanged: boolean;
  message: string;
}

export interface AdminSmtpAccountExitSnapshot {
  ownerScope: string;
  viewKey: string;
  administratorId: string;
  mode: AdminSmtpAccountMode;
  recordId: string;
  pending: PendingAdminSmtpAccountExit | null;
}
