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

export interface AdminUsersApi {
  listUsers(query: AdminUserListQuery, signal?: AbortSignal): Promise<AdminUserPage>;
  getUser(userId: string, signal?: AbortSignal): Promise<AdminUserRecord>;
  createUser(input: AdminUserCreateInput, signal?: AbortSignal): Promise<AdminUserRecord>;
  updateUser(
    userId: string,
    input: AdminUserUpdateInput,
    signal?: AbortSignal,
  ): Promise<{ message: string }>;
}
