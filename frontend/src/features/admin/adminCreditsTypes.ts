export type AdminCreditsSortOrder = "asc" | "desc";

export type AdminUserCreditSortField = "user_id" | "credits" | "updated_at";

export type AdminCreditLogSortField =
  | "user_id"
  | "type"
  | "amount"
  | "balance_after"
  | "description"
  | "created_at";

export interface AdminUserCredit {
  id: string;
  user_id: string;
  credits: number;
  created_at: string;
  updated_at: string;
}

export interface AdminUserCreditPage {
  data: AdminUserCredit[];
  total: number;
}

export interface AdminUserCreditQuery {
  skip: number;
  limit: number;
  sort_field: AdminUserCreditSortField;
  sort_order: AdminCreditsSortOrder;
}

export interface AdminCreditLog {
  id: string;
  user_id: string;
  amount: number;
  balance_after: number;
  type: string;
  description: string | null;
  task_id: string | null;
  created_at: string;
}

export interface AdminCreditLogPage {
  data: AdminCreditLog[];
  total: number;
}

export interface AdminCreditLogQuery {
  skip: number;
  limit: number;
  sort_field: AdminCreditLogSortField;
  sort_order: AdminCreditsSortOrder;
  user_id?: string;
}

export interface AdminCreditsApi {
  listUserCredits(query: AdminUserCreditQuery, signal?: AbortSignal): Promise<AdminUserCreditPage>;
  getUserCredit(creditId: string, signal?: AbortSignal): Promise<AdminUserCredit>;
  updateUserCredit(creditId: string, credits: number, signal?: AbortSignal): Promise<AdminUserCredit>;
  listCreditLogs(query: AdminCreditLogQuery, signal?: AbortSignal): Promise<AdminCreditLogPage>;
}
