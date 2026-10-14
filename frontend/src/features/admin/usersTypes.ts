export type AdminUserMembershipType = "free" | "premium";

export type AdminUserSortField =
  | "username"
  | "id"
  | "email"
  | "membership_type"
  | "membership_expires_at"
  | "is_superuser"
  | "credits"
  | "created_at";

export interface AdminUserRecord {
  id: string;
  username: string;
  email: string;
  is_superuser: boolean;
  membership_type: string;
  membership_expires_at: string | null;
  credits: number;
  created_at: string;
}

export interface AdminUserPage {
  data: AdminUserRecord[];
  total: number;
}

export interface AdminUserListQuery {
  skip: number;
  limit: number;
  sort_field: AdminUserSortField;
  sort_order: "asc" | "desc";
  username?: string;
  email?: string;
  membership_type?: string;
  is_superuser?: boolean;
}

export interface AdminUserCreateInput {
  username: string;
  email: string;
  password: string;
  is_superuser: boolean;
  membership_type: string;
  membership_expires_at: string | null;
  credits: number;
}

export type AdminUserUpdateInput = Omit<AdminUserCreateInput, "password" | "credits"> & {
  credits: number | null;
};

export interface AdminUserDeleteResult {
  success: true;
  message: string;
}

export type AdminUserRelatedResource =
  | "credit-logs"
  | "model-configs"
  | "prompt-configs"
  | "tasks"
  | "series";

export interface AdminUserRelatedQuery {
  skip: number;
  limit: number;
  sort_field: string;
  sort_order: "asc" | "desc";
}

export interface AdminUserRelatedPage<TRecord = AdminUserRelatedRecord> {
  data: TRecord[];
  total: number;
}

export interface AdminUserCreditLogRecord {
  id: string;
  user_id: string;
  amount: number;
  balance_after: number;
  type: string;
  description: string | null;
  task_id: string | null;
  created_at: string;
}

export interface AdminUserModelConfigRecord {
  id: string;
  user_id: string;
  category: string;
  name: string;
  provider: string;
  model: string | null;
  api_key: string;
  base_url: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface AdminUserPromptConfigRecord {
  id: string;
  user_id: string;
  category: string;
  name: string;
  system_prompt: string | null;
  user_prompt: string | null;
  sort_order: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminUserTaskRecord {
  id: string;
  user_id: string;
  type: string;
  status: string;
  credit_cost: number;
  message_id: string | null;
  model_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminUserSeriesRecord {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  image_url: string | null;
  created_at: string;
  updated_at: string;
}

export type AdminUserRelatedRecord =
  | AdminUserCreditLogRecord
  | AdminUserModelConfigRecord
  | AdminUserPromptConfigRecord
  | AdminUserTaskRecord
  | AdminUserSeriesRecord;

export interface AdminUsersApi {
  listUsers(query: AdminUserListQuery, signal?: AbortSignal): Promise<AdminUserPage>;
  getUser(userId: string, signal?: AbortSignal): Promise<AdminUserRecord>;
  createUser(input: AdminUserCreateInput, signal?: AbortSignal): Promise<AdminUserRecord>;
  updateUser(
    userId: string,
    input: AdminUserUpdateInput,
    signal?: AbortSignal,
  ): Promise<{ message: string }>;
  deleteUser(userId: string, signal?: AbortSignal): Promise<AdminUserDeleteResult>;
  listRelatedRecords(
    resource: AdminUserRelatedResource,
    userId: string,
    query: AdminUserRelatedQuery,
    signal?: AbortSignal,
  ): Promise<AdminUserRelatedPage>;
}
