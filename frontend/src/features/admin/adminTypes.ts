import type { ApiError } from "../../shared/api/errors";

export interface AdminUser {
  id: string;
  username: string;
  is_superuser: true;
  [field: string]: unknown;
}

export interface AdminLoginUser {
  id: string;
  username: string;
  is_superuser: boolean;
  [field: string]: unknown;
}

export interface AdminLoginResponse {
  access_token: string;
  user: AdminLoginUser;
}

export interface AdminModel {
  id: string;
  category: string;
  name: string;
  provider: string;
  model: string | null;
  api_key: string | null;
  base_url: string | null;
  cost_per_call: number;
  description: string | null;
  is_active: boolean;
  sort_order: number;
  params: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminModelInput {
  category: string;
  name: string;
  provider: string;
  model: string;
  api_key: string;
  base_url: string;
  cost_per_call: number;
  description: string;
  is_active: boolean;
  sort_order: number;
  params: string | null;
}

export type AdminModelUpdate = Partial<AdminModelInput>;

export interface AdminModelPage {
  data: AdminModel[];
  total: number;
}

export interface AdminModelQuery {
  skip: number;
  limit: number;
  category?: string;
  provider?: string;
  is_active?: boolean;
}

export interface AdminStats {
  upstream: { balance: number | string | null };
  users: {
    total: number;
    premium: number;
    active_members: number;
    new_this_month: number;
  };
  credits: {
    total_balance: number;
    used_this_month: number;
    charged_this_month: number;
  };
  tasks: {
    total: number;
    this_month: number;
    completed: number;
    failed: number;
  };
  content: {
    series: number;
    chapters: number;
    characters: number;
    scenes: number;
    props: number;
  };
  revenue: {
    total: number;
    this_month: number;
    subscriptions: number;
  };
}

export interface AdminStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface AdminApi {
  login(username: string, password: string, signal?: AbortSignal): Promise<AdminLoginResponse>;
  getStats(signal?: AbortSignal): Promise<AdminStats>;
  listModels(query: AdminModelQuery, signal?: AbortSignal): Promise<AdminModelPage>;
  getModel(id: string, signal?: AbortSignal): Promise<AdminModel>;
  createModel(input: AdminModelInput, signal?: AbortSignal): Promise<AdminModel>;
  updateModel(id: string, input: AdminModelUpdate, signal?: AbortSignal): Promise<AdminModel>;
  deleteModel(id: string, signal?: AbortSignal): Promise<{ id: string; message: string }>;
}

export type AdminApiError = ApiError;
